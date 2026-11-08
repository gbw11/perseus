"""조건부 빈공간 certificate. 전체 target 배치 tile마다 연속 ray witness가 필요하다.

opaque vertical core와 그 중심→전체 충돌부 Rmax가 수락된 모델만 취급한다.
한 pixel/nominal ray의 통과, depth0, semantic road, 미검사 tile은 FREE가 아니다.
여기서 CLEAR_REGION은 주어진 모델의 수학적 결과이며 이동 권한을 만들지 않는다.
"""

from dataclasses import dataclass
import math

from .calibration import CalibrationProfile, number, polygon, vector
from .geometry import Interval, PointBounds, plane_height_bounds, project_point
from .ray_batch import RayBundleBatch


@dataclass(frozen=True, slots=True)
class RayBundle:
    origin_lower: tuple
    origin_upper: tuple
    direction_lower: tuple
    direction_upper: tuple
    range_lower_m: float
    range_upper_m: float
    near_m: float
    far_m: float
    qualified: bool = False
    source_key: tuple | None = None
    pixel: tuple | None = None

    def __post_init__(self):
        for low, high in (('origin_lower', 'origin_upper'), ('direction_lower', 'direction_upper')):
            a, b = vector(getattr(self, low), 3), vector(getattr(self, high), 3)
            if any(x > y for x, y in zip(a, b)):
                raise ValueError('reversed ray bundle interval')
            object.__setattr__(self, low, a)
            object.__setattr__(self, high, b)
        if (any(a < -1.000000001 or b > 1.000000001 for a, b in zip(self.direction_lower, self.direction_upper))
                or type(self.qualified) is not bool):
            raise ValueError('ray directions must enclose unit directions')
        minimum = sum((0. if a <= 0 <= b else min(abs(a), abs(b))) ** 2
                      for a, b in zip(self.direction_lower, self.direction_upper))
        maximum = sum(max(a * a, b * b) for a, b in zip(self.direction_lower, self.direction_upper))
        if minimum > 1 + 1e-12 or maximum < 1 - 1e-12:
            raise ValueError('direction box contains no unit direction')
        for name in ('range_lower_m', 'range_upper_m', 'near_m', 'far_m'):
            object.__setattr__(self, name, number(getattr(self, name)))
        if not 0 <= self.near_m < self.far_m or not 0 < self.range_lower_m <= self.range_upper_m:
            raise ValueError('invalid radial ray measurement/profile')
        if self.pixel is not None:
            object.__setattr__(self, 'pixel', vector(self.pixel, 2))
        if self.source_key is not None and (type(self.source_key) is not tuple or len(self.source_key) > 8
                or any(type(x) not in (int, str) for x in self.source_key)):
            raise ValueError('source key must be a bounded immutable tuple')


@dataclass(frozen=True)
class TargetClass:
    core_radius_m: float
    core_height_m: float
    max_extent_m: float
    base_height_m: tuple = (0., 0.)
    qualified: bool = False

    def __post_init__(self):
        for name in ('core_radius_m', 'core_height_m', 'max_extent_m'):
            value = number(getattr(self, name))
            if not 0 < value <= 10:
                raise ValueError('invalid target core or whole-body extent')
            object.__setattr__(self, name, value)
        base = vector(self.base_height_m, 2)
        if base[0] > base[1] or self.max_extent_m < self.core_radius_m or type(self.qualified) is not bool:
            raise ValueError('target placement/extent contract is invalid')
        object.__setattr__(self, 'base_height_m', base)


@dataclass(frozen=True)
class Certificate:
    status: str
    complete: bool
    work: int
    reason: str
    tested_tiles: int = 0
    required_tiles: int = 0
    source_keys: tuple = ()
    calibration_id: str | None = None
    rank_work: int = 0

    @property
    def free(self):
        return self.status == 'CLEAR_REGION' and self.complete


def _inside_box(bounds, support, guard=1e-10):
    x0, y0, x1, y1 = bounds
    for a, b in zip(support, support[1:] + support[:1]):
        for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):
            cross = ((Interval.of(b[0]) - a[0]) * (Interval.of(y) - a[1])
                     - (Interval.of(b[1]) - a[1]) * (Interval.of(x) - a[0]))
            if cross.lo < guard:
                return False
    return True


def _witness(patch, lower, upper, guard):
    origin = tuple(Interval(a, b) for a, b in zip(patch.origin_lower, patch.origin_upper))
    direction = tuple(Interval(a, b) for a, b in zip(patch.direction_lower, patch.direction_upper))
    axes = sorted(range(3), key=lambda i: -min(abs(direction[i].lo), abs(direction[i].hi)))
    for axis in axes:
        if direction[axis].lo <= 0 <= direction[axis].hi:
            continue
        cut = (lower[axis] + upper[axis]) / 2
        try:
            rho = (Interval(cut, cut) - origin[axis]) / direction[axis]
        except ValueError:
            continue
        if rho.lo <= max(0., patch.near_m) + guard or rho.hi >= min(patch.range_lower_m, patch.far_m) - guard:
            continue
        intersects = True
        for i in range(3):
            value = Interval(cut, cut) if i == axis else origin[i] + rho * direction[i]
            if value.lo <= lower[i] + guard or value.hi >= upper[i] - guard:
                intersects = False
                break
        if intersects:
            return True
    return False


def certify_cell(cell_xy_bounds, patches, targets, profile, *, permission_polygon=None,
                 max_tiles=4096, max_ray_tests=200000, tile_size_m=None, hazards=()):
    """미검사 placement가 하나라도 남으면 UNKNOWN이다. 후보 ray 순서는 증명 범위를 줄이지 않는다.

    image-space 근접32개는 witness 탐색 힌트다. 그 안에서 증명되지 않은 tile을
    불가능/빈 공간으로 바꾸지 않으며 전체 target placement-domain cover는 유지한다.
    """
    bounds = vector(cell_xy_bounds, 4)
    x0, y0, x1, y1 = bounds
    if not x0 < x1 or not y0 < y1 or type(profile) is not CalibrationProfile:
        raise ValueError('cell/profile mismatch')
    if (type(max_tiles) is not int or not 1 <= max_tiles <= 65536
            or type(max_ray_tests) is not int or not 0 <= max_ray_tests <= 2_000_000):
        raise ValueError('certificate work cap exceeds profile')
    batch = type(patches) is RayBundleBatch
    patches = patches if batch else tuple(patches)
    targets, hazards = tuple(targets), tuple(hazards)
    if len(patches) > 8192 or not 1 <= len(targets) <= 4 or len(hazards) > 8192:
        raise ValueError('certificate input cap exceeded')
    if (not batch and any(type(p) is not RayBundle for p in patches)) or any(type(t) is not TargetClass for t in targets):
        raise ValueError('certificate requires exact ray/core records')
    def result(status, reason, tested=0, required=0, work=0, keys=(), rank=0):
        return Certificate(status, status == 'CLEAR_REGION', work, reason, tested, required,
                           tuple(keys), profile.sha256, rank)
    for hit in hazards:
        if type(hit) is not PointBounds:
            raise ValueError('hazard must be a bounded 3D hit')
        if hit.lower[0] <= x1 and hit.upper[0] >= x0 and hit.lower[1] <= y1 and hit.upper[1] >= y0:
            return result('OCCUPIED' if hit.qualified else 'UNKNOWN', 'CURRENT_HIT')
    if not profile.free_qualified or any(not t.qualified for t in targets):
        return result('UNKNOWN', 'UNQUALIFIED_FIRST_SURFACE_OR_TARGET')
    if not _inside_box(bounds, profile.support_polygon):
        return result('UNKNOWN', 'UNSUPPORTED_CELL')
    if permission_polygon is not None and not _inside_box(bounds, polygon(permission_polygon)):
        return result('UNKNOWN', 'NO_WHOLE_CELL_PERMISSION')
    accepted = patches.accepted_indices if batch else tuple(p for p in patches if p.qualified)
    if not accepted:
        return result('UNKNOWN', 'NO_ACCEPTED_RAY_BUNDLE')
    # 8px bin의 근방 후보만 순서화한다. 범위 밖/누락 witness는 UNKNOWN으로 남긴다.
    pixels = patches.accepted_pixels if batch else tuple(p.pixel for p in accepted)
    if batch:
        bins = patches.bins
    else:
        bins = {}
        for i, pixel in enumerate(pixels):
            if pixel is not None:
                bins.setdefault((math.floor(pixel[0] / 8), math.floor(pixel[1] / 8)), []).append(i)
    consumed = {}
    def candidate(index):
        if not batch:
            return accepted[index]
        if index not in consumed:
            consumed[index] = patches[accepted[index]]
        return consumed[index]
    required = 0
    plans = []
    for target in targets:
        domain = (math.nextafter(x0 - target.max_extent_m, -math.inf),
                  math.nextafter(y0 - target.max_extent_m, -math.inf),
                  math.nextafter(x1 + target.max_extent_m, math.inf),
                  math.nextafter(y1 + target.max_extent_m, math.inf))
        if not _inside_box(domain, profile.support_polygon):
            return result('UNKNOWN', 'TARGET_PLACEMENT_DOMAIN_OUTSIDE_SUPPORT')
        step = target.core_radius_m / 2 if tile_size_m is None else number(tile_size_m)
        if not 0 < step <= target.core_radius_m / 2:
            raise ValueError('placement tile exceeds core coverage profile')
        nx, ny = max(1, math.ceil((domain[2] - domain[0]) / step)), max(1, math.ceil((domain[3] - domain[1]) / step))
        nz = max(1, math.ceil((target.base_height_m[1] - target.base_height_m[0]) / (target.core_height_m / 4)))
        required += nx * ny * nz
        plans.append((target, domain, nx, ny, nz))
    tested = work = 0
    rank_work = len(accepted)
    keys = set()
    for target, domain, nx, ny, nz in plans:
        r = math.nextafter(target.core_radius_m / math.sqrt(2.), -math.inf)
        for ix in range(nx):
            xa, xb = (domain[0] + (domain[2] - domain[0]) * i / nx for i in (ix, ix + 1))
            for iy in range(ny):
                ya, yb = (domain[1] + (domain[3] - domain[1]) * i / ny for i in (iy, iy + 1))
                floor = plane_height_bounds(Interval(xa, xb), Interval(ya, yb), profile.plane)
                if floor is None:
                    return result('UNKNOWN', 'PLANE_INTERVAL_UNBOUNDED', tested, required, work, sorted(keys, key=repr), rank_work)
                for iz in range(nz):
                    if tested >= max_tiles:
                        return result('UNKNOWN', 'UNTESTED_PLACEMENT_TILES', tested, required, work, sorted(keys, key=repr), rank_work)
                    a, b = (target.base_height_m[0] + (target.base_height_m[1] - target.base_height_m[0]) * i / nz
                            for i in (iz, iz + 1))
                    lower = tuple(math.nextafter(x, math.inf) for x in (xb - r, yb - r, floor.hi + b))
                    upper = tuple(math.nextafter(x, -math.inf) for x in (xa + r, ya + r, floor.lo + a + target.core_height_m))
                    if any(lo >= hi for lo, hi in zip(lower, upper)):
                        return result('UNKNOWN', 'NO_COMMON_CORE_INTERIOR', tested, required, work, sorted(keys, key=repr), rank_work)
                    center = tuple((lo + hi) / 2 for lo, hi in zip(lower, upper))
                    uv = project_point(center, profile.right)
                    if uv is not None and bins:
                        bx, by = math.floor(uv[0] / 8), math.floor(uv[1] / 8)
                        pool = [i for dx in range(-2, 3) for dy in range(-2, 3)
                                for i in bins.get((bx + dx, by + dy), ())]
                        order = sorted(pool, key=lambda i: ((pixels[i][0] - uv[0]) ** 2
                            + (pixels[i][1] - uv[1]) ** 2, i))[:32]
                        rank_work += len(pool)
                    else:
                        order = list(range(min(32, len(accepted))))
                    found = False
                    for index in order:
                        if work >= max_ray_tests:
                            return result('UNKNOWN', 'RAY_TEST_CAP', tested, required, work, sorted(keys, key=repr), rank_work)
                        work += 1
                        patch = candidate(index)
                        if _witness(patch, lower, upper, 1e-9):
                            if patch.source_key is not None:
                                keys.add(patch.source_key)
                            found = True
                            break
                    tested += 1
                    if not found:
                        return result('UNKNOWN', 'NO_COMMON_RAY_WITNESS', tested, required, work, sorted(keys, key=repr), rank_work)
    return result('CLEAR_REGION', 'ALL_TARGET_PLACEMENTS_EXCLUDED', tested, required, work,
                  sorted(keys, key=repr), rank_work)
