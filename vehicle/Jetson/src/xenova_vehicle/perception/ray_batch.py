"""동등한 ray interval을 한 번 검증해 bytes-backed 배열로 보존한다.

순서·pixel·source는 원 producer의 값이다. certificate가 고른 후보만 기존 scalar
RayBundle로 만든다. tuple(batch)는 명시 소비자의 선택이며 certificate는 사용하지 않는다.
"""

from dataclasses import dataclass
import math
import operator
from types import MappingProxyType


def _source(value):
    if value is not None and (type(value) is not tuple or len(value) > 8
            or any(type(v) not in (int, str) for v in value)):
        raise ValueError('source key must be a bounded immutable tuple')
    return value


@dataclass(frozen=True, slots=True, init=False)
class RayBundleBatch:
    """최대8192 ray. ndarray는 immutable bytes view여서 write flag도 되살릴 수 없다."""

    _storage: bytes
    _qualified_bytes: bytes
    _pixel_bytes: bytes
    _count: int
    _common_source: tuple | None
    _sources: tuple | None
    accepted_indices: tuple
    accepted_pixels: tuple
    bins: object

    def __init__(self, origin_lower, origin_upper, direction_lower, direction_upper,
                 range_lower_m, range_upper_m, near_m, far_m, qualified=False,
                 source_key=None, pixels=None, *, pixel_present=None, source_keys=None):
        import numpy as np
        arrays = (origin_lower, origin_upper, direction_lower, direction_upper,
                  range_lower_m, range_upper_m, near_m, far_m)
        if any(type(value) is not np.ndarray or value.dtype.kind not in 'uif' for value in arrays):
            raise ValueError('ray batch requires exact numeric ndarrays')
        if origin_lower.ndim != 2:
            raise ValueError('ray batch origins require Nx3 coordinates')
        count = len(origin_lower)
        if not 0 <= count <= 8192:
            raise ValueError('ray batch exceeds8192 rows')
        if any(value.shape != (count, 3) for value in arrays[:4]) or any(value.shape != (count,) for value in arrays[4:]):
            raise ValueError('ray batch column shape mismatch')
        values = [np.array(value, dtype=np.float64, copy=True) for value in arrays]
        if any(not np.isfinite(value).all() for value in values):
            raise ValueError('ray batch columns must be finite')
        olo, ohi, dlo, dhi, rlo, rhi, near, far = values
        if np.any(olo > ohi) or np.any(dlo > dhi):
            raise ValueError('reversed ray batch interval')
        if np.any(dlo < -1.000000001) or np.any(dhi > 1.000000001):
            raise ValueError('ray directions must enclose unit directions')
        closest = np.where((dlo <= 0) & (dhi >= 0), 0., np.minimum(np.abs(dlo), np.abs(dhi)))
        squares = closest * closest
        far_squares = np.maximum(dlo * dlo, dhi * dhi)
        minimum = squares[:, 0] + squares[:, 1] + squares[:, 2]
        maximum = far_squares[:, 0] + far_squares[:, 1] + far_squares[:, 2]
        if np.any(minimum > 1 + 1e-12) or np.any(maximum < 1 - 1e-12):
            raise ValueError('direction box contains no unit direction')
        if np.any(near < 0) or np.any(near >= far) or np.any(rlo <= 0) or np.any(rlo > rhi):
            raise ValueError('invalid radial ray measurement/profile')
        if type(qualified) is bool:
            flags = np.full(count, qualified, dtype=np.bool_)
        elif type(qualified) is np.ndarray and qualified.dtype == np.bool_ and qualified.shape == (count,):
            flags = qualified.copy()
        else:
            raise ValueError('batch qualification must be exact bool or a bool column')
        if pixels is None:
            pixel_values = np.zeros((count, 2), dtype=np.float64)
            present = np.zeros(count, dtype=np.bool_)
            if pixel_present is not None:
                raise ValueError('pixel flags require pixel coordinates')
        else:
            if (type(pixels) is not np.ndarray or pixels.dtype.kind not in 'uif'
                    or pixels.shape != (count, 2) or not np.isfinite(pixels).all()):
                raise ValueError('batch pixels require finite numeric Nx2 coordinates')
            pixel_values = np.array(pixels, dtype=np.float64, copy=True)
            if pixel_present is None:
                present = np.ones(count, dtype=np.bool_)
            elif type(pixel_present) is np.ndarray and pixel_present.dtype == np.bool_ and pixel_present.shape == (count,):
                present = pixel_present.copy()
            else:
                raise ValueError('pixel presence must be an exact bool column')
        common = _source(source_key)
        sources = None
        if source_keys is not None:
            if source_key is not None or type(source_keys) is not tuple or len(source_keys) != count:
                raise ValueError('batch source inventory differs from its rows')
            sources = tuple(_source(value) for value in source_keys)
        # 각 scalar의 native double 값과 순서를 그대로 packed 저장한다. 원 배열은 빌리지 않는다.
        matrix = np.column_stack((*values[:4], *values[4:], pixel_values)).astype('<f8', copy=False)
        object.__setattr__(self, '_storage', matrix.tobytes())
        object.__setattr__(self, '_qualified_bytes', flags.tobytes())
        object.__setattr__(self, '_pixel_bytes', present.tobytes())
        object.__setattr__(self, '_count', count)
        object.__setattr__(self, '_common_source', common)
        object.__setattr__(self, '_sources', sources)
        accepted = tuple(int(v) for v in np.flatnonzero(flags))
        accepted_pixels = tuple(tuple(float(v) for v in pixel_values[i]) if present[i] else None for i in accepted)
        bins = {}
        for index, pixel in enumerate(accepted_pixels):
            if pixel is not None:
                bins.setdefault((math.floor(pixel[0] / 8), math.floor(pixel[1] / 8)), []).append(index)
        object.__setattr__(self, 'accepted_indices', accepted)
        object.__setattr__(self, 'accepted_pixels', accepted_pixels)
        object.__setattr__(self, 'bins', MappingProxyType({key: tuple(value) for key, value in bins.items()}))

    @property
    def numbers(self):
        import numpy as np
        return np.frombuffer(self._storage, dtype='<f8').reshape(self._count, 18)

    @property
    def qualified(self):
        import numpy as np
        return np.frombuffer(self._qualified_bytes, dtype=np.bool_)

    @property
    def pixel_present(self):
        import numpy as np
        return np.frombuffer(self._pixel_bytes, dtype=np.bool_)

    @property
    def pixels(self):
        return self.numbers[:, 16:18]

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
            raise IndexError('ray batch index outside its original rows')
        from .free_space import RayBundle
        values = self.numbers[index].tolist()
        # 이 factory의 유일 입력은 위에서 전체 검증한 immutable 행이다. 재검증 DTO를8192개 만들지 않는다.
        result = object.__new__(RayBundle)
        for name, value in (
            ('origin_lower', tuple(values[0:3])), ('origin_upper', tuple(values[3:6])),
            ('direction_lower', tuple(values[6:9])), ('direction_upper', tuple(values[9:12])),
            ('range_lower_m', values[12]), ('range_upper_m', values[13]),
            ('near_m', values[14]), ('far_m', values[15]),
            ('qualified', bool(self.qualified[index])),
            ('source_key', self._common_source if self._sources is None else self._sources[index]),
            ('pixel', tuple(values[16:18]) if self.pixel_present[index] else None)):
            object.__setattr__(result, name, value)
        return result

    @classmethod
    def from_rays(cls, rays):
        """명시 scalar 입력의 호환 factory. certificate의 batch 경로에서는 호출하지 않는다."""
        import numpy as np
        from .free_space import RayBundle
        if type(rays) is not tuple or len(rays) > 8192 or any(type(ray) is not RayBundle for ray in rays):
            raise ValueError('expected a bounded original RayBundle tuple')
        vectors = [np.asarray([getattr(ray, name) for ray in rays], dtype=np.float64).reshape(-1, 3)
                   for name in ('origin_lower', 'origin_upper', 'direction_lower', 'direction_upper')]
        scalars = [np.asarray([getattr(ray, name) for ray in rays], dtype=np.float64)
                   for name in ('range_lower_m', 'range_upper_m', 'near_m', 'far_m')]
        return cls(*vectors, *scalars, np.asarray([ray.qualified for ray in rays], dtype=np.bool_),
            pixels=np.asarray([ray.pixel if ray.pixel is not None else (0., 0.) for ray in rays], dtype=np.float64).reshape(-1, 2),
            pixel_present=np.asarray([ray.pixel is not None for ray in rays], dtype=np.bool_),
            source_keys=tuple(ray.source_key for ray in rays))
