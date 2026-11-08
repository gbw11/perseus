"""원 노출 구간 전체의 SE2 pose bound를 각 native ray에 전파한다.

출력은 PoseEstimate.sample의 nominal 후륜축 pose를 기준으로 한 body frame이다.
T_nominal^-1*T(t)의 translation과 yaw를 원점·방향 모두에 적용한다. radial range와
first-surface 자격은 원 producer의 값이며 새 측정/시각/odom/권한을 만들지 않는다.
호출자는 실제 motion/odom 오차 상한을 수락한 경우에만 motion_qualified=True를 준다.
"""

import math
from dataclasses import replace

from .calibration import CalibrationProfile, number
from .inputs import FrameStamp
from .ray_batch import RayBundleBatch
from ..mapping.odometry import PoseBuffer, PoseEstimate, PoseSample


def sin_cos_bounds(lower, upper):
    """연속 각도 구간의 endpoint와 내부 ±1 extrema를 모두 포함한다. corner sampling이 아니다."""
    lower, upper = number(lower), number(upper)
    if lower > upper:
        raise ValueError('reversed angle interval')
    if lower == upper == 0.:
        return (0., 0.), (1., 1.)
    # libm/상수 및 extrema 위치 계산의 roundoff를 별도로 바깥에 둔다.
    guard = 32 * math.ulp(1.) * (1 + max(abs(lower), abs(upper)))
    lo, hi = math.nextafter(lower - guard, -math.inf), math.nextafter(upper + guard, math.inf)
    if hi - lo >= math.tau:
        return (-1., 1.), (-1., 1.)

    def bounds(function, maximum_phase, minimum_phase):
        values = (function(lo), function(hi))
        a, b = min(values), max(values)
        for phase, is_maximum in ((maximum_phase, True), (minimum_phase, False)):
            if math.ceil((lo - phase) / math.tau) <= math.floor((hi - phase) / math.tau):
                if is_maximum:
                    b = 1.
                else:
                    a = -1.
        return max(-1., math.nextafter(a - guard, -math.inf)), min(1., math.nextafter(b + guard, math.inf))

    return bounds(math.sin, math.pi / 2, -math.pi / 2), bounds(math.cos, 0., math.pi)


def _copy(rays, qualified, origins=None, directions=None):
    values = rays.numbers
    olo, ohi = (values[:, :3], values[:, 3:6]) if origins is None else origins
    dlo, dhi = (values[:, 6:9], values[:, 9:12]) if directions is None else directions
    return RayBundleBatch(olo, ohi, dlo, dhi, values[:, 12], values[:, 13], values[:, 14], values[:, 15],
        qualified, rays._common_source, rays.pixels, pixel_present=rays.pixel_present, source_keys=rays._sources)


def _interval(value):
    if type(value) is not tuple or len(value) != 2 or any(type(v) is not int or not 0 <= v < 2**64 for v in value):
        return False
    return value[0] <= value[1]


def _bind(stamp, calibration, pose, input_kind, motion_qualified, capture_interval):
    if type(stamp) is not FrameStamp or type(calibration) is not CalibrationProfile:
        raise ValueError('motion requires original FrameStamp and CalibrationProfile')
    if input_kind not in ('LIVE', 'REPLAY', 'SYNTHETIC') or type(motion_qualified) is not bool:
        raise ValueError('explicit input kind and exact motion qualification required')
    if not motion_qualified or not calibration.free_qualified or stamp.captured_us is None:
        return None
    full = stamp.captured_us if capture_interval is None else capture_interval
    if not _interval(full) or not full[0] <= stamp.captured_us[0] <= stamp.captured_us[1] <= full[1]:
        return None
    if (input_kind == 'SYNTHETIC') != (calibration.kind == 'SYNTHETIC'):
        return None
    if type(pose) is PoseBuffer:
        if full[1] > stamp.available_us:
            return None
        pose = pose.at(replace(stamp, captured_us=full), calibration.sha256)
    if type(pose) is not PoseEstimate or type(pose.sample) is not PoseSample:
        return None
    sample = pose.sample
    if (sample.clock_id != stamp.clock_id or sample.calibration_id != calibration.sha256
            or pose.captured_us != full or not _interval(pose.bracket_us)
            or not pose.bracket_us[0] <= full[0] <= sample.at_us <= full[1] <= pose.bracket_us[1]
            or sample.origin == 'ORACLE_SIMULATION'
            or input_kind != 'SYNTHETIC' and sample.origin != 'ODOMETRY'):
        return None
    return pose


def widen_motion_rays(rays, stamp, calibration, pose, *, input_kind, motion_qualified=False, capture_interval=None):
    """최대8192 원 행을 반환한다. 시간/pose/프로필 미수락이면 모든 FREE 자격이 False다.

    PoseBuffer.at의 전체 구간 bound 또는 동등하게 수락한 PoseEstimate만 사용한다.
    scalar tuple도 호환하지만 정상 batch 경로는 RayBundle8192개를 펼치지 않는다.
    원점과 방향의 상관을 풀어 넓어진 interval은 충분조건을 약하게 할 뿐 증거를 늘리지 않는다.
    """
    import numpy as np
    if type(rays) is tuple:
        rays = RayBundleBatch.from_rays(rays)
    if type(rays) is not RayBundleBatch:
        raise ValueError('expected bounded original ray batch or tuple')
    bound = _bind(stamp, calibration, pose, input_kind, motion_qualified, capture_interval)
    qualified = np.zeros(len(rays), dtype=bool)
    if bound is None:
        return _copy(rays, qualified)
    sources = rays._sources
    matching = np.full(len(rays), rays._common_source == stamp.key, dtype=bool) if sources is None else np.asarray(
        [source == stamp.key for source in sources], dtype=bool)
    qualified = rays.qualified & matching
    sample = bound.sample
    position, angle = sample.position_error_m, sample.yaw_error_rad
    if position == angle == 0.:
        values = rays.numbers
        directions = np.maximum(-1., values[:, 6:9]), np.minimum(1., values[:, 9:12])
        if np.any(directions[0] > directions[1]):
            return _copy(rays, np.zeros(len(rays), dtype=bool))
        return _copy(rays, qualified, directions=directions)
    sine, cosine = sin_cos_bounds(-angle, angle)
    nominal_sine, nominal_cosine = sin_cos_bounds(sample.yaw, sample.yaw)

    def multiply(lo, hi, coefficients):
        c, d = coefficients
        products = np.stack((lo * c, lo * d, hi * c, hi * d))
        return np.nextafter(products.min(axis=0), -np.inf), np.nextafter(products.max(axis=0), np.inf)

    def add(a, b):
        return np.nextafter(a[0] + b[0], -np.inf), np.nextafter(a[1] + b[1], np.inf)

    def negative(value):
        return -value[1], -value[0]

    with np.errstate(over='ignore', invalid='ignore'):
        # x/y absolute pose box를 nominal yaw의 body frame으로 옮긴다. cell을 대신 팽창하지 않는다.
        delta = np.asarray([-position], dtype=np.float64), np.asarray([position], dtype=np.float64)
        tx = add(multiply(*delta, nominal_cosine), multiply(*delta, nominal_sine))
        ty = add(negative(multiply(*delta, nominal_sine)), multiply(*delta, nominal_cosine))

        def rotate(lower, upper, translation=False):
            x = add(multiply(lower[:, 0], upper[:, 0], cosine),
                    negative(multiply(lower[:, 1], upper[:, 1], sine)))
            y = add(multiply(lower[:, 0], upper[:, 0], sine),
                    multiply(lower[:, 1], upper[:, 1], cosine))
            if translation:
                x, y = add(x, tx), add(y, ty)
            lo = np.column_stack((x[0], y[0], lower[:, 2]))
            hi = np.column_stack((x[1], y[1], upper[:, 2]))
            return lo, hi

        values = rays.numbers
        origins = rotate(values[:, :3], values[:, 3:6], True)
        directions = rotate(values[:, 6:9], values[:, 9:12])
        directions = np.maximum(-1., directions[0]), np.minimum(1., directions[1])
    if (any(not np.isfinite(value).all() for value in (*origins, *directions))
            or np.any(directions[0] > directions[1])):
        return _copy(rays, np.zeros(len(rays), dtype=bool))
    return _copy(rays, qualified, origins, directions)
