"""RDF/FLU 투영과 바깥 반올림 interval. nominal 결과와 연속 bound를 분리한다.

rectified optical-Z를 range로 해석하지 않는다. 비선형 distortion의 nominal은
OpenCV를 쓰지만 미구현 연속 역왜곡 bound를 corner 표본으로 대신하지 않는다.
"""

from dataclasses import dataclass
import math
from numbers import Real

from .calibration import CameraModel, Plane, number as _native_number


def number(value):
    """계산 경계에서는 NumPy real scalar도 허용한다. 보정 JSON의 타입 검사는 별개다."""
    if type(value) in (int, float):
        return _native_number(value)
    if isinstance(value, Real) and not isinstance(value, bool):
        return _native_number(float(value))
    raise ValueError('expected finite real coordinate')


def _down(x):
    return math.nextafter(x, -math.inf)


def _up(x):
    return math.nextafter(x, math.inf)


@dataclass(frozen=True, slots=True)
class Interval:
    lo: float
    hi: float

    def __post_init__(self):
        lo, hi = number(self.lo), number(self.hi)
        if lo > hi:
            raise ValueError('reversed interval')
        object.__setattr__(self, 'lo', lo)
        object.__setattr__(self, 'hi', hi)

    @staticmethod
    def of(value):
        return value if type(value) is Interval else Interval(value, value)

    @classmethod
    def around(cls, center, error):
        center, error = number(center), number(error)
        if error < 0:
            raise ValueError('negative interval error')
        return cls(_down(center - error), _up(center + error))

    def __add__(self, other):
        other = self.of(other)
        return Interval(_down(self.lo + other.lo), _up(self.hi + other.hi))

    __radd__ = __add__

    def __neg__(self):
        return Interval(-self.hi, -self.lo)

    def __sub__(self, other):
        return self + -self.of(other)

    def __rsub__(self, other):
        return self.of(other) + -self

    def __mul__(self, other):
        other = self.of(other)
        products = (self.lo * other.lo, self.lo * other.hi, self.hi * other.lo, self.hi * other.hi)
        return Interval(_down(min(products)), _up(max(products)))

    __rmul__ = __mul__

    def __truediv__(self, other):
        other = self.of(other)
        if other.lo <= 0 <= other.hi:
            raise ValueError('interval denominator contains zero')
        return self * Interval(_down(1. / other.hi), _up(1. / other.lo))


@dataclass(frozen=True, slots=True)
class PointBounds:
    lower: tuple
    upper: tuple
    qualified: bool = False

    def __post_init__(self):
        if len(self.lower) != 3 or len(self.upper) != 3 or type(self.qualified) is not bool:
            raise ValueError('invalid point box')
        lower, upper = tuple(number(x) for x in self.lower), tuple(number(x) for x in self.upper)
        if any(a > b for a, b in zip(lower, upper)):
            raise ValueError('reversed point box')
        object.__setattr__(self, 'lower', lower)
        object.__setattr__(self, 'upper', upper)

    def error_from(self, point):
        errors = tuple(max(_up(abs(a - p)), _up(abs(b - p)))
                       for a, b, p in zip(self.lower, self.upper, point))
        return _norm_interval(tuple(Interval(0., x) for x in errors)).hi

    def xy_error_from(self, point):
        errors = tuple(max(_up(abs(a - p)), _up(abs(b - p)))
                       for a, b, p in zip(self.lower[:2], self.upper[:2], point[:2]))
        return _norm_interval(tuple(Interval(0., x) for x in errors)).hi

    @property
    def center(self):
        return tuple((a + b) / 2 for a, b in zip(self.lower, self.upper))


def _plane(plane):
    return plane if type(plane) is Plane else Plane(tuple(plane[:3]), plane[3])


def _matvec(matrix, value):
    return tuple(sum(row[i] * value[i] for i in range(3)) for row in matrix)


def _transpose(matrix):
    return tuple(zip(*matrix))


def _in_roi(u, v, camera, error=0.):
    x0, y0, x1, y1 = camera.roi
    return x0 <= u - error and u + error < x1 and y0 <= v - error and v + error < y1


def camera_ray(u, v, camera):
    """정규화하지 않은 base ray. distortion=0이면 rectified z=1 parameter를 유지한다."""
    u, v = number(u), number(v)
    if not _in_roi(u, v, camera):
        return None
    k = camera.k
    if any(camera.distortion):
        import numpy as np
        import cv2
        q = cv2.undistortPoints(np.array([[[u, v]]], dtype=np.float64),
                               np.asarray(k), np.asarray(camera.distortion)).reshape(2)
        ray = (float(q[0]), float(q[1]), 1.)
    else:
        y = (v - k[1][2]) / k[1][1]
        ray = ((u - k[0][2] - k[0][1] * y) / k[0][0], y, 1.)
    return _matvec(camera.rotation, _matvec(_transpose(camera.rectification), ray))


def project_point(point, camera):
    """base 3D 점을 실제 active image에 nominal 투영한다. ROI 밖/후면은 None이다."""
    raw = _matvec(_transpose(camera.rotation), tuple(number(x) - t for x, t in zip(point, camera.translation)))
    active = _matvec(camera.rectification, raw)
    if active[2] <= 0:
        return None
    if any(camera.distortion):
        import numpy as np
        import cv2
        image, _ = cv2.projectPoints(np.asarray(active, dtype=np.float64).reshape(1, 3),
            np.zeros(3), np.zeros(3), np.asarray(camera.k), np.asarray(camera.distortion))
        u, v = (float(x) for x in image.reshape(2))
    else:
        q = _matvec(camera.k, active)
        u, v = q[0] / q[2], q[1] / q[2]
    return (u, v) if _in_roi(u, v, camera) else None


project_base = project_point


def plane_height(x, y, plane):
    plane = _plane(plane)
    if abs(plane.normal[2]) < 1e-12:
        return None
    return -(plane.normal[0] * number(x) + plane.normal[1] * number(y) + plane.offset) / plane.normal[2]


def ground_to_pixel(x, y, camera, plane):
    z = plane_height(x, y, plane)
    return None if z is None else project_point((x, y, z), camera)


def ground_to_pixels(x_array, y_array, camera, plane):
    """birdview의 배열 좌표를 한 번에 투영한다. invalid map 좌표는 -1이다."""
    import numpy as np
    plane = _plane(plane)
    x, y = np.broadcast_arrays(np.asarray(x_array, dtype=np.float64), np.asarray(y_array, dtype=np.float64))
    if x.size > 2_000_000:
        raise ValueError('projection grid exceeds bounded work')
    if abs(plane.normal[2]) < 1e-12:
        z = np.full(x.shape, np.nan)
    else:
        z = -(plane.normal[0] * x + plane.normal[1] * y + plane.offset) / plane.normal[2]
    points = np.stack((x, y, z), axis=-1) - np.asarray(camera.translation)
    raw = points @ np.asarray(camera.rotation)
    active = raw @ np.asarray(camera.rectification).T
    valid = np.isfinite(active).all(axis=-1) & (active[..., 2] > 0)
    if any(camera.distortion):
        import cv2
        image, _ = cv2.projectPoints(active.reshape(-1, 3), np.zeros(3), np.zeros(3),
                                     np.asarray(camera.k), np.asarray(camera.distortion))
        image = image.reshape(x.shape + (2,))
        u, v = image[..., 0], image[..., 1]
    else:
        with np.errstate(divide='ignore', invalid='ignore'):
            v = camera.k[1][1] * active[..., 1] / active[..., 2] + camera.k[1][2]
            u = (camera.k[0][0] * active[..., 0] + camera.k[0][1] * active[..., 1]) / active[..., 2] + camera.k[0][2]
    a, b, c, d = camera.roi
    valid &= np.isfinite(u) & np.isfinite(v) & (u >= a) & (u < c) & (v >= b) & (v < d)
    u, v = np.where(valid, u, -1.).astype(np.float32), np.where(valid, v, -1.).astype(np.float32)
    for array in (u, v, valid):
        array.setflags(write=False)
    return u, v, valid


def project_ground(u, v, camera, plane):
    plane = _plane(plane)
    ray = camera_ray(u, v, camera)
    if ray is None:
        return None
    denominator = sum(n * r for n, r in zip(plane.normal, ray))
    if abs(denominator) < 1e-12:
        return None
    lam = -(sum(n * t for n, t in zip(plane.normal, camera.translation)) + plane.offset) / denominator
    point = tuple(t + lam * r for t, r in zip(camera.translation, ray))
    return point if lam > 0 and camera.near_m <= lam <= camera.far_m else None


project_plane = project_ground


def depth_point(u, v, z_mm, camera):
    z = number(z_mm) * .001
    ray = camera_ray(u, v, camera)
    if ray is None or z <= 0:
        return None
    if not camera.near_m <= z <= camera.far_m:
        return None
    return tuple(t + z * r for t, r in zip(camera.translation, ray))


def _norm_interval(values):
    squares = []
    for value in values:
        nearest = 0. if value.lo <= 0 <= value.hi else min(abs(value.lo), abs(value.hi))
        squares.append(Interval(max(0., _down(nearest * nearest)), _up(max(value.lo * value.lo, value.hi * value.hi))))
    total = sum(squares, Interval(0., 0.))
    return Interval(max(0., _down(math.sqrt(max(0., total.lo)))), _up(math.sqrt(total.hi)))


def _ray_intervals(u, v, camera, pixel_error=None):
    error = camera.pixel_error if pixel_error is None else number(pixel_error)
    if error < 0 or any(camera.distortion) or not _in_roi(u, v, camera, error):
        return None
    k = camera.k
    y = (Interval.around(v, error) - k[1][2]) / k[1][1]
    x = (Interval.around(u, error) - k[0][2] - y * k[0][1]) / k[0][0]
    q = (x, y, Interval(1., 1.))
    if camera.ray_error_rad:
        norm = _norm_interval(q)
        delta = _up(2 * math.sin(camera.ray_error_rad / 2))
        unit = tuple(value / norm + Interval(-delta, delta) for value in q)
        if unit[2].lo <= 0:
            return None
        q = (unit[0] / unit[2], unit[1] / unit[2], Interval(1., 1.))
    q = _matvec(camera.rotation, _matvec(_transpose(camera.rectification), q))
    norm = _norm_interval(q)
    pad = _up(2 * norm.hi * math.sin(camera.rotation_error_rad / 2))
    return tuple(value + Interval(-pad, pad) for value in q)


def ray_bounds(u, v, camera, *, pixel_error=None):
    q = _ray_intervals(u, v, camera, pixel_error)
    if q is None:
        return None
    norm = _norm_interval(q)
    if norm.lo <= 0:
        return None
    direction = _unit_directions(q, norm)
    if direction is None:
        return None
    origin = tuple(Interval.around(x, camera.translation_error_m) for x in camera.translation)
    return origin, direction


def _unit_directions(q, norm):
    # interval 나눗셈은 상관을 풀어 ±1을 넘을 수 있다. 실제 unit sphere와의 교집합은 보수적이다.
    divided = tuple(value / norm for value in q)
    bounds = tuple((max(-1., value.lo), min(1., value.hi)) for value in divided)
    if any(lo > hi for lo, hi in bounds):
        return None
    return tuple(Interval(lo, hi) for lo, hi in bounds)


def _plane_intervals(plane):
    plane = _plane(plane)
    delta = _up(2 * math.sin(plane.normal_error_rad / 2))
    return tuple(Interval.around(x, delta) for x in plane.normal), Interval.around(plane.offset, plane.offset_error_m)


def plane_height_bounds(x_interval, y_interval, plane):
    n, d = _plane_intervals(plane)
    try:
        return -(n[0] * Interval.of(x_interval) + n[1] * Interval.of(y_interval) + d) / n[2]
    except ValueError:
        return None


def project_ground_bounds(u, v, camera, plane, *, pixel_error=None):
    plane = _plane(plane)
    q = _ray_intervals(number(u), number(v), camera, pixel_error)
    if q is None:
        return None
    origin = tuple(Interval.around(x, camera.translation_error_m) for x in camera.translation)
    n, d = _plane_intervals(plane)
    try:
        numerator = -(sum((a * b for a, b in zip(n, origin)), Interval(0., 0.)) + d)
        denominator = sum((a * b for a, b in zip(n, q)), Interval(0., 0.))
        lam = numerator / denominator
        if lam.lo <= 0:
            return None
        if lam.lo < camera.near_m or lam.hi > camera.far_m:
            return None
        values = tuple(t + lam * r for t, r in zip(origin, q))
        return PointBounds(tuple(v.lo for v in values), tuple(v.hi for v in values), camera.qualified and plane.qualified)
    except (ValueError, OverflowError):
        return None


def depth_point_bounds(u, v, z_mm, camera, *, depth_error_m=0., pixel_error=None):
    q = _ray_intervals(number(u), number(v), camera, pixel_error)
    z, error = number(z_mm) * .001, number(depth_error_m)
    if q is None or error < 0 or z - error <= 0:
        return None
    z_range = Interval.around(z, error)
    if z_range.lo < camera.near_m or z_range.hi > camera.far_m:
        return None
    points = tuple(Interval.around(t, camera.translation_error_m) + z_range * r
                   for t, r in zip(camera.translation, q))
    return PointBounds(tuple(v.lo for v in points), tuple(v.hi for v in points), camera.qualified)


def axial_ray_bounds(u, v, z_mm, camera, *, depth_error_m=0., pixel_error=None):
    """axial-Z를 radial 측정 구간으로 한 번 변환한다. near/far도 같은 ray norm을 사용한다."""
    q = _ray_intervals(number(u), number(v), camera, pixel_error)
    z, error = number(z_mm) * .001, number(depth_error_m)
    if q is None or error < 0 or z - error <= 0:
        return None
    axial = Interval.around(z, error)
    if axial.lo < camera.near_m or axial.hi > camera.far_m:
        return None
    norm = _norm_interval(q)
    if norm.lo <= 0:
        return None
    origin = tuple(Interval.around(t, camera.translation_error_m) for t in camera.translation)
    direction = _unit_directions(q, norm)
    if direction is None:
        return None
    return origin, direction, axial * norm, _up(camera.near_m * norm.hi), _down(camera.far_m * norm.lo)


# batch interval은 scalar와 같은 연산 순서/바깥 반올림을 사용한다.
# 각 helper의 배열은 한 block(8192개) 이하이며 전체 float image를 만들지 않는다.
def _ba(value):
    return value if type(value) is tuple else (value, value)


def _badd(a, b):
    import numpy as np
    a, b = _ba(a), _ba(b)
    return np.nextafter(a[0] + b[0], -np.inf), np.nextafter(a[1] + b[1], np.inf)


def _bneg(a):
    a = _ba(a)
    return -a[1], -a[0]


def _bsub(a, b):
    return _badd(a, _bneg(b))


def _bmul(a, b):
    import numpy as np
    a, b = _ba(a), _ba(b)
    p0, p1, p2, p3 = a[0] * b[0], a[0] * b[1], a[1] * b[0], a[1] * b[1]
    return (np.nextafter(np.minimum(np.minimum(p0, p1), np.minimum(p2, p3)), -np.inf),
            np.nextafter(np.maximum(np.maximum(p0, p1), np.maximum(p2, p3)), np.inf))


def _bdiv(a, b):
    import numpy as np
    b = _ba(b)
    bad = (b[0] <= 0) & (b[1] >= 0)
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        reciprocal = np.nextafter(1. / b[1], -np.inf), np.nextafter(1. / b[0], np.inf)
        result = _bmul(a, reciprocal)
    return np.where(bad, np.nan, result[0]), np.where(bad, np.nan, result[1])


def _baround(center, error):
    import numpy as np
    return np.nextafter(center - error, -np.inf), np.nextafter(center + error, np.inf)


def _bnorm(values):
    import numpy as np
    total = (0., 0.)
    for lo, hi in values:
        nearest = np.where((lo <= 0) & (hi >= 0), 0., np.minimum(np.abs(lo), np.abs(hi)))
        square = (np.maximum(0., np.nextafter(nearest * nearest, -np.inf)),
                  np.nextafter(np.maximum(lo * lo, hi * hi), np.inf))
        total = _badd(total, square)
    return (np.maximum(0., np.nextafter(np.sqrt(np.maximum(0., total[0])), -np.inf)),
            np.nextafter(np.sqrt(total[1]), np.inf))


def _bmatvec(matrix, values):
    result = []
    for row in matrix:
        total = (0., 0.)
        for coefficient, value in zip(row, values):
            total = _badd(total, _bmul(value, coefficient))
        result.append(total)
    return tuple(result)


def _batch_arrays(*values):
    import numpy as np
    arrays = []
    for value in values:
        raw = np.asarray(value)
        if raw.dtype.kind not in 'uif':
            raise ValueError('batch coordinates require real numeric arrays')
        arrays.append(raw.astype(np.float64, copy=False))
    arrays = np.broadcast_arrays(*arrays)
    if arrays[0].size > 65536:
        raise ValueError('interval batch exceeds 65536 output points')
    return arrays


def _ray_intervals_batch(u, v, camera, error):
    import numpy as np
    a, b, c, d = camera.roi
    valid = (np.isfinite(u) & np.isfinite(v) & (u - error >= a) & (u + error < c)
             & (v - error >= b) & (v + error < d))
    if any(camera.distortion):
        valid &= False
    k = camera.k
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        y = _bdiv(_bsub(_baround(v, error), k[1][2]), k[1][1])
        x = _bdiv(_bsub(_bsub(_baround(u, error), k[0][2]), _bmul(y, k[0][1])), k[0][0])
        q = (x, y, (1., 1.))
        if camera.ray_error_rad:
            norm = _bnorm(q)
            delta = _up(2 * math.sin(camera.ray_error_rad / 2))
            unit = tuple(_badd(_bdiv(value, norm), (-delta, delta)) for value in q)
            valid &= unit[2][0] > 0
            q = (_bdiv(unit[0], unit[2]), _bdiv(unit[1], unit[2]), (1., 1.))
        q = _bmatvec(camera.rotation, _bmatvec(_transpose(camera.rectification), q))
        norm = _bnorm(q)
        pad = np.nextafter(2 * norm[1] * math.sin(camera.rotation_error_rad / 2), np.inf)
        q = tuple(_badd(value, (-pad, pad)) for value in q)
    return q, valid


def _ground_homography_coefficients(camera, plane):
    """고정 보정의 각 좌표를 affine/affine으로 쓴다. 계수도 interval로 반올림한다."""
    k = camera.k
    fy, fx, skew = Interval.of(k[1][1]), Interval.of(k[0][0]), Interval.of(k[0][1])
    one = Interval(1., 1.)
    y = (Interval(0., 0.), one / fy, -Interval.of(k[1][2]) / fy)
    x = (one / fx, -(skew * y[1]) / fx, (-Interval.of(k[0][2]) - skew * y[2]) / fx)
    zero = Interval(0., 0.)
    active = (x, y, (zero, zero, Interval(1., 1.)))
    rays = tuple(_matvec(camera.rotation, _matvec(_transpose(camera.rectification),
                 tuple(row[column] for row in active))) for column in range(3))
    coefficients = tuple(tuple(rays[column][axis] for column in range(3)) for axis in range(3))
    denominator = tuple(sum((n * row[column] for n, row in zip(plane.normal, coefficients)), zero)
                        for column in range(3))
    offset = sum((Interval.of(n) * t for n, t in zip(plane.normal, camera.translation)), zero) + plane.offset
    numerators = tuple(tuple(Interval.of(t) * d - offset * q for d, q in zip(denominator, row))
                       for t, row in zip(camera.translation, coefficients))
    native = lambda row: tuple((value.lo, value.hi) for value in row)
    return tuple(native(row) for row in numerators), native(denominator)


def _ground_homography_boxes(u, v, error, coefficients):
    """분모 부호가 일정한 rectangle의 linear-fractional extrema는 vertex에 있다.

    affine 분자/분모는 rectangle vertex의 동일 bilinear 가중합이다. 분모의 부호가
    일정하면 그 비율은 vertex 비율의 양의 가중평균이다. 이는 표본 근사가 아니다.
    호출자는 전체 rectangle의 분모가0을 지나지 않음을 먼저 검사한다.
    """
    import numpy as np
    numerators, denominator = coefficients
    u_bounds, v_bounds = _baround(u, error), _baround(v, error)
    lows, highs = [], []
    def evaluate(row, x, y):
        return _badd(_badd(_bmul(row[0], x), _bmul(row[1], y)), row[2])
    for x in u_bounds:
        for y in v_bounds:
            divisor = evaluate(denominator, x, y)
            points = tuple(_bdiv(evaluate(row, x, y), divisor) for row in numerators)
            lows.append(np.stack([point[0] for point in points], axis=-1))
            highs.append(np.stack([point[1] for point in points], axis=-1))
    return np.minimum.reduce(lows), np.maximum.reduce(highs)


def project_ground_bounds_many(u, v, camera, plane, *, pixel_error=None):
    """연속 ground box의 batch `(lower, upper, valid)`. 마지막 축은 base XYZ다.

    최대65536 출력이며 내부 작업은8192개씩 한다. 미지원 distortion/수평선/ROI와
    비유한 좌표는 valid=False, 숫자 placeholder는0이다. 수락 여부는 caller가 보존한다.
    """
    import numpy as np
    plane = _plane(plane)
    error = camera.pixel_error if pixel_error is None else number(pixel_error)
    if error < 0:
        raise ValueError('negative pixel error')
    u, v = _batch_arrays(u, v)
    shape = u.shape
    flat_u, flat_v = u.reshape(-1), v.reshape(-1)
    lower, upper = np.zeros((u.size, 3)), np.zeros((u.size, 3))
    accepted = np.zeros(u.size, dtype=bool)
    origin = tuple(_baround(t, camera.translation_error_m) for t in camera.translation)
    n0, d0 = _plane_intervals(plane)
    normal, offset = tuple((x.lo, x.hi) for x in n0), (d0.lo, d0.hi)
    numerator = (0., 0.)
    for n, t in zip(normal, origin):
        numerator = _badd(numerator, _bmul(n, t))
    numerator = _bneg(_badd(numerator, offset))
    fixed = (not any(camera.distortion) and not camera.ray_error_rad and not camera.rotation_error_rad
             and not camera.translation_error_m and not plane.normal_error_rad and not plane.offset_error_m)
    coefficients = _ground_homography_coefficients(camera, plane) if fixed else None
    for start in range(0, u.size, 8192):
        end = min(start + 8192, u.size)
        q, valid = _ray_intervals_batch(flat_u[start:end], flat_v[start:end], camera, error)
        denominator = (0., 0.)
        for n, ray in zip(normal, q):
            denominator = _badd(denominator, _bmul(n, ray))
        lam = _bdiv(numerator, denominator)
        valid &= (lam[0] > 0) & (lam[0] >= camera.near_m) & (lam[1] <= camera.far_m)
        with np.errstate(invalid='ignore', over='ignore'):
            if coefficients is not None:
                lo, hi = _ground_homography_boxes(flat_u[start:end], flat_v[start:end], error, coefficients)
            else:
                points = tuple(_badd(t, _bmul(lam, ray)) for t, ray in zip(origin, q))
                lo, hi = np.stack([x[0] for x in points], axis=-1), np.stack([x[1] for x in points], axis=-1)
        valid &= np.isfinite(lo).all(axis=-1) & np.isfinite(hi).all(axis=-1) & (lo <= hi).all(axis=-1)
        lower[start:end], upper[start:end] = np.where(valid[:, None], lo, 0.), np.where(valid[:, None], hi, 0.)
        accepted[start:end] = valid
    outputs = lower.reshape(shape + (3,)), upper.reshape(shape + (3,)), accepted.reshape(shape)
    for output in outputs:
        output.setflags(write=False)
    return outputs


def axial_ray_bounds_many(u, v, z_mm, camera, *, depth_error_m=0., pixel_error=None):
    """batch ray의 `(originLo,originHi,dirLo,dirHi,rangeLo,rangeHi,near,far,valid)`.

    XYZ 배열의 마지막 축은3, 나머지는 broadcast shape다. 모든 range/near/far는
    radial m이며 입력만 axial mm다. 실제 unit sphere와의 [-1,1] 교집합을 유지한다.
    미지원/비유한/0포함/clip 실패는 valid=False이며 placeholder0을 증거로 쓰지 않는다.
    """
    import numpy as np
    error = camera.pixel_error if pixel_error is None else number(pixel_error)
    if error < 0:
        raise ValueError('negative pixel error')
    u, v, z_mm, errors = _batch_arrays(u, v, z_mm, depth_error_m)
    shape = u.shape
    u, v, z_mm, errors = (x.reshape(-1) for x in (u, v, z_mm, errors))
    lower, upper, direction_lo, direction_hi = (np.zeros((u.size, 3)) for _ in range(4))
    range_lo, range_hi, near, far = (np.zeros(u.size) for _ in range(4))
    accepted = np.zeros(u.size, dtype=bool)
    origin = tuple(_baround(t, camera.translation_error_m) for t in camera.translation)
    for start in range(0, u.size, 8192):
        end = min(start + 8192, u.size)
        q, valid = _ray_intervals_batch(u[start:end], v[start:end], camera, error)
        z, dz = z_mm[start:end] * .001, errors[start:end]
        axial = _baround(z, dz)
        norm = _bnorm(q)
        valid &= (np.isfinite(dz) & (dz >= 0) & (z - dz > 0) & (axial[0] >= camera.near_m)
                  & (axial[1] <= camera.far_m) & (norm[0] > 0))
        directions = tuple(_bdiv(ray, norm) for ray in q)
        lo = np.stack([np.maximum(-1., x[0]) for x in directions], axis=-1)
        hi = np.stack([np.minimum(1., x[1]) for x in directions], axis=-1)
        radial = _bmul(axial, norm)
        close = np.nextafter(camera.near_m * norm[1], np.inf)
        distant = np.nextafter(camera.far_m * norm[0], -np.inf)
        valid &= (np.isfinite(lo).all(axis=-1) & np.isfinite(hi).all(axis=-1)
                  & (lo <= hi).all(axis=-1) & np.isfinite(radial[0]) & np.isfinite(radial[1])
                  & (radial[0] > 0) & (close < distant))
        lower[start:end] = np.where(valid[:, None], [x[0] for x in origin], 0.)
        upper[start:end] = np.where(valid[:, None], [x[1] for x in origin], 0.)
        direction_lo[start:end], direction_hi[start:end] = np.where(valid[:, None], lo, 0.), np.where(valid[:, None], hi, 0.)
        range_lo[start:end], range_hi[start:end] = np.where(valid, radial[0], 0.), np.where(valid, radial[1], 0.)
        near[start:end], far[start:end], accepted[start:end] = np.where(valid, close, 0.), np.where(valid, distant, 0.), valid
    outputs = tuple(x.reshape(shape + ((3,) if x.ndim == 2 else ())) for x in
        (lower, upper, direction_lo, direction_hi, range_lo, range_hi, near, far, accepted))
    for output in outputs:
        output.setflags(write=False)
    return outputs


def capture_interval(end_interval_us, exposure_us, *, clock_error_us=None, timestamp_error_us=None,
                     readout_error_us=None):
    """END 구간에서 integration과 각 오차를 합한다. None인 미수락 오차를 0으로 만들지 않는다."""
    if end_interval_us is None or exposure_us is None or any(x is None for x in
            (clock_error_us, timestamp_error_us, readout_error_us)):
        return None
    if type(end_interval_us) not in (tuple, list) or len(end_interval_us) != 2:
        raise ValueError('END interval requires two endpoints')
    values = tuple(end_interval_us) + (exposure_us, clock_error_us, timestamp_error_us, readout_error_us)
    if any(type(x) is not int or not 0 <= x < 2**64 for x in values) or values[0] > values[1]:
        raise ValueError('invalid bounded capture timing')
    error = clock_error_us + timestamp_error_us + readout_error_us
    lo, hi = end_interval_us[0] - exposure_us - error, end_interval_us[1] + error
    return None if lo < 0 or hi >= 2**64 else (lo, hi)


def capture_hull(intervals):
    values = tuple(intervals)
    if not values or any(x is None for x in values):
        return None
    if any(type(x) not in (tuple, list) or len(x) != 2 or any(type(v) is not int for v in x)
           or not 0 <= x[0] <= x[1] < 2**64 for x in values):
        raise ValueError('invalid integration intervals')
    return min(x[0] for x in values), max(x[1] for x in values)
