"""원 순서의 hit box를 한 번 검증한 immutable bytes batch로 보존한다.

scalar PointBounds는 명시 색인/iteration 때만 만든다. 가림·hazard·grid 소비자는
readonly 열을 직접 읽으며 qualification/source를 새로 만들지 않는다.
"""

from dataclasses import dataclass
import math
import operator

from .calibration import number, vector


@dataclass(frozen=True, slots=True, init=False)
class PointBoundsBatch:
    """최대8192개 원 lower/upper/자격. 원 ndarray를 빌리지 않고 write flag도 복원할 수 없다."""

    _storage: bytes
    _flags: bytes
    _count: int
    source_key: tuple | None

    def __init__(self, lower, upper, qualified=False, source_key=None):
        import numpy as np
        if (type(lower) is not np.ndarray or type(upper) is not np.ndarray
                or lower.dtype.kind not in 'uif' or upper.dtype.kind not in 'uif'
                or lower.ndim != 2 or lower.shape[1:] != (3,) or upper.shape != lower.shape
                or not 0 <= len(lower) <= 8192):
            raise ValueError('point batch requires at most8192 numeric Nx3 bounds')
        values = np.column_stack((lower, upper)).astype('<f8', copy=False)
        if not np.isfinite(values).all() or np.any(values[:, :3] > values[:, 3:]):
            raise ValueError('point batch bounds must be finite and ordered')
        if type(qualified) is bool:
            flags = np.full(len(lower), qualified, dtype=np.bool_)
        elif type(qualified) is np.ndarray and qualified.dtype == np.bool_ and qualified.shape == (len(lower),):
            flags = qualified
        else:
            raise ValueError('point qualification requires bool or same-row bool column')
        if source_key is not None and (type(source_key) is not tuple or len(source_key) > 8
                or any(type(value) not in (int, str) for value in source_key)):
            raise ValueError('point source key must be a bounded immutable tuple')
        object.__setattr__(self, '_storage', values.tobytes())
        object.__setattr__(self, '_flags', flags.tobytes())
        object.__setattr__(self, '_count', len(lower))
        object.__setattr__(self, 'source_key', source_key)

    @property
    def numbers(self):
        import numpy as np
        return np.frombuffer(self._storage, dtype='<f8').reshape(self._count, 6)

    @property
    def lower(self):
        return self.numbers[:, :3]

    @property
    def upper(self):
        return self.numbers[:, 3:]

    @property
    def qualified(self):
        import numpy as np
        return np.frombuffer(self._flags, dtype=np.bool_)

    def __len__(self):
        return self._count

    def __iter__(self):
        for index in range(self._count):
            yield self[index]

    def __getitem__(self, index):
        if type(index) is slice:
            return tuple(self[i] for i in range(*index.indices(self._count)))
        index = operator.index(index)
        if index < 0:
            index += self._count
        if not 0 <= index < self._count:
            raise IndexError('point batch index outside original rows')
        from .geometry import PointBounds
        values = self.numbers[index].tolist()
        result = object.__new__(PointBounds)
        object.__setattr__(result, 'lower', tuple(values[:3]))
        object.__setattr__(result, 'upper', tuple(values[3:]))
        object.__setattr__(result, 'qualified', bool(self.qualified[index]))
        return result

    @classmethod
    def from_points(cls, points):
        """명시 scalar tuple의 호환 factory. 정상 depth/가림 경로에서 확장하지 않는다."""
        import numpy as np
        from .geometry import PointBounds
        if type(points) is not tuple or len(points) > 8192 or any(type(point) is not PointBounds for point in points):
            raise ValueError('expected a bounded original PointBounds tuple')
        return cls(np.asarray([point.lower for point in points], dtype=np.float64).reshape(-1, 3),
            np.asarray([point.upper for point in points], dtype=np.float64).reshape(-1, 3),
            np.asarray([point.qualified for point in points], dtype=np.bool_))

    def first_intersection(self, bounds):
        """원 행에서 최초 XY 닫힌 교차의(index,자격)만 반환한다. scalar DTO를 만들지 않는다."""
        import numpy as np
        x0, y0, x1, y1 = vector(bounds, 4)
        if x0 > x1 or y0 > y1:
            raise ValueError('invalid XY query bounds')
        lower, upper = self.lower, self.upper
        hit = (lower[:, 0] <= x1) & (upper[:, 0] >= x0) & (lower[:, 1] <= y1) & (upper[:, 1] >= y0)
        if not np.any(hit):
            return None
        index = int(np.argmax(hit))
        return index, bool(self.qualified[index])

    def transformed_xy(self, pose, *, position_error_m=0., yaw_error_rad=0.):
        """기존 scalar 네 모서리 SE2/오차식을 유지한다. Z와 원 행/자격/source는 보존한다."""
        import numpy as np
        px, py, yaw = vector(pose, 3)
        position, angle = number(position_error_m), number(yaw_error_rad)
        if position < 0 or angle < 0:
            raise ValueError('negative pose uncertainty')
        lo, hi = self.lower, self.upper
        x = np.column_stack((lo[:, 0], lo[:, 0], hi[:, 0], hi[:, 0]))
        y = np.column_stack((lo[:, 1], hi[:, 1], lo[:, 1], hi[:, 1]))
        # math.hypot의 scalar rounding을 유지하면서 모서리 norm 네 개를 monotonic 최대 한 개로 줄인다.
        radii = np.fromiter((math.hypot(float(a), float(b)) for a, b in
            zip(np.maximum(np.abs(lo[:, 0]), np.abs(hi[:, 0])), np.maximum(np.abs(lo[:, 1]), np.abs(hi[:, 1])))),
            dtype=np.float64, count=len(self))
        error = position + 2 * radii * math.sin(min(math.pi, angle) / 2)
        c, s = math.cos(yaw), math.sin(yaw)
        transformed_x, transformed_y = px + c * x - s * y, py + s * x + c * y
        lower = np.column_stack((transformed_x.min(axis=1) - error, transformed_y.min(axis=1) - error, lo[:, 2]))
        upper = np.column_stack((transformed_x.max(axis=1) + error, transformed_y.max(axis=1) + error, hi[:, 2]))
        return type(self)(lower, upper, self.qualified, self.source_key)
