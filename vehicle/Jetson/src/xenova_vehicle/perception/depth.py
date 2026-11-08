"""native RIGHT axial depth의 보수 hit/unknown과 유한 ray patch를 만든다.

전체 pixel의 nominal 분류와 선택 patch의 연속 bound를 구분한다. 원 source/time을
보존하고 생략된 hit/patch 수를 보고한다. depth0/plane/도로색에서 FREE를 만들지 않는다.
"""

from dataclasses import dataclass
from functools import lru_cache
import math

from .calibration import CalibrationProfile, CameraModel, Plane, number
from .ray_batch import RayBundleBatch
from .point_batch import PointBoundsBatch
from .geometry import axial_ray_bounds_many
from .inputs import FrameStamp


BLOCK_PIXELS = 8192
# 최대2개 모델의 width1280+height720+matrix9+translation3+normal3 float64 bytes만 캐시한다.
COEFFICIENT_CACHE_BYTES = 2 * (1280 + 720 + 9 + 3 + 3) * 8


@lru_cache(maxsize=2)
def _coefficients(camera, plane):
    """immutable 1D 계수와 작은 행렬만 재사용한다. 전체 image/ray/depth 값은 캐시하지 않는다."""
    import numpy as np
    k = camera.k
    values = ((np.arange(camera.width, dtype=np.float64) - k[0][2]).tobytes(),
        ((np.arange(camera.height, dtype=np.float64) - k[1][2]) / k[1][1]).tobytes(),
        (np.asarray(camera.rectification) @ np.asarray(camera.rotation).T).tobytes(),
        np.asarray(camera.translation, dtype=np.float64).tobytes(), np.asarray(plane.normal, dtype=np.float64).tobytes())
    return (*values, math.hypot(camera.pixel_error / k[1][1],
        camera.pixel_error / k[0][0] + abs(k[0][1]) * camera.pixel_error / (k[0][0] * k[1][1])),
        2 * math.sin(camera.ray_error_rad / 2), 2 * math.sin(camera.rotation_error_rad / 2),
        2 * math.sin(plane.normal_error_rad / 2), sum(abs(x) for x in plane.normal))


@dataclass(frozen=True)
class DepthConfig:
    max_patches: int = 8192
    max_hit_boxes: int = 8192
    stride: int = 1
    obstacle_min_height_m: float = .015
    obstacle_max_height_m: float = .50
    floor_tolerance_m: float = .01
    extra_depth_error_m: float = 0.
    extra_relative_error: float = 0.

    def __post_init__(self):
        for name in ('max_patches', 'max_hit_boxes'):
            if type(getattr(self, name)) is not int or not 0 <= getattr(self, name) <= 8192:
                raise ValueError('depth output cap exceeds profile')
        if type(self.stride) is not int or not 1 <= self.stride <= 64:
            raise ValueError('invalid explicit depth stride')
        for name in ('obstacle_min_height_m', 'obstacle_max_height_m', 'floor_tolerance_m',
                     'extra_depth_error_m', 'extra_relative_error'):
            value = number(getattr(self, name))
            if value < 0 or value > 100:
                raise ValueError('invalid depth classification/error profile')
            object.__setattr__(self, name, value)
        if self.obstacle_min_height_m >= self.obstacle_max_height_m:
            raise ValueError('invalid collision-height band')
        if self.floor_tolerance_m >= self.obstacle_min_height_m:
            raise ValueError('floor and collision-height bands must not overlap')


@dataclass(frozen=True)
class DepthEvidence:
    stamp: FrameStamp | None
    calibration_id: str | None
    input_kind: str
    hit_boxes: tuple | PointBoundsBatch
    patches: tuple | RayBundleBatch
    valid_count: int
    floor_count: int
    unknown_count: int
    invalid_count: int
    omitted_hit_count: int
    omitted_patch_count: int
    metric_qualified: bool
    mode: str

    @property
    def rays(self):
        return self.patches


def _selected(indices, maximum):
    import numpy as np
    if maximum == 0 or len(indices) == 0:
        return indices[:0].copy()
    if len(indices) <= maximum:
        return indices.copy()
    return indices[(np.arange(maximum, dtype=np.int64) * len(indices)) // maximum]


def _stratified(indices, floors, maximum, width):
    """유효 pixel 범위의 2D bin별 실제 표본을 선택한다. 빈 bin은 증거를 만들지 않는다."""
    import numpy as np
    if maximum == 0 or len(indices) <= maximum:
        return _selected(indices, maximum)
    xmin, xmax = width, -1
    for start in range(0, len(indices), BLOCK_PIXELS):
        x = indices[start:start + BLOCK_PIXELS] % width
        xmin, xmax = min(xmin, int(x.min())), max(xmax, int(x.max()))
    ymin, ymax = int(indices[0]) // width, int(indices[-1]) // width
    span_x, span_y = xmax - xmin + 1, ymax - ymin + 1
    columns = min(span_x, max(1, int(math.sqrt(maximum * span_x / span_y))))
    rows = min(span_y, max(1, maximum // columns))
    scores = np.full(rows * columns, np.inf)
    sentinel = np.iinfo(np.int32).max
    representatives = np.full(rows * columns, sentinel, dtype=np.int32)
    for start in range(0, len(indices), BLOCK_PIXELS):
        current = indices[start:start + BLOCK_PIXELS]
        x, y = current % width, current // width
        bx, by = (x - xmin) * columns // span_x, (y - ymin) * rows // span_y
        bins = by * columns + bx
        dx = (x - xmin + .5) * columns / span_x - (bx + .5)
        dy = (y - ymin + .5) * rows / span_y - (by + .5)
        # bin 안의 floor를 우선하되 선택된 ray의 원 pixel/측정만 보존한다.
        score = dx * dx + dy * dy + (~floors[start:start + BLOCK_PIXELS]).astype(np.float64)
        improved = score < scores[bins]
        representatives[np.unique(bins[improved])] = sentinel
        np.minimum.at(scores, bins, score)
        matching = score == scores[bins]
        np.minimum.at(representatives, bins[matching], current[matching])
    chosen = np.sort(representatives[representatives != sentinel])
    # 실제 유효 영역이 bin을 채우지 못한 경우만 남은 명시 cap 안에서 보충한다.
    remainder = maximum - len(chosen)
    if remainder:
        extra = np.setdiff1d(_selected(indices, maximum), chosen, assume_unique=True)
        chosen = np.sort(np.concatenate((chosen, _selected(extra, remainder))))
    return chosen


def analyze_depth(depth_mm, camera, plane, *, stamp=None, calibration_id=None,
                  input_kind='LIVE', profile=None, config=DepthConfig()):
    """전체 nominal 분류는 8192픽셀 block, hit/patch는 명시 cap의 immutable 기록이다.

    전체 float grid를 보관하지 않는다. 출력 선택용 int32 index 두 개는 각 1/pixel
    이하이고 선택 뒤 해제한다. 입력/출력 소유 메모리와 실제 RSS 수락은 별도다.
    """
    import numpy as np
    if type(camera) is not CameraModel or type(plane) is not Plane or type(config) is not DepthConfig:
        raise ValueError('depth requires exact camera/plane/config')
    if input_kind not in ('LIVE', 'REPLAY', 'SYNTHETIC') or stamp is not None and type(stamp) is not FrameStamp:
        raise ValueError('invalid original input kind/stamp')
    if profile is not None:
        if type(profile) is not CalibrationProfile or profile.right != camera or profile.plane != plane:
            raise ValueError('depth profile differs from the registered RIGHT model')
        if input_kind == 'LIVE' and profile.kind == 'SYNTHETIC':
            raise ValueError('synthetic camera cannot be relabeled LIVE')
        if calibration_id is not None and calibration_id != profile.sha256:
            raise ValueError('depth calibration ID mismatch')
        calibration_id = profile.sha256
    depth = np.asarray(depth_mm)
    if depth.shape != (camera.height, camera.width) or depth.dtype.kind not in 'uif':
        raise ValueError('native axial depth grid/type mismatch')
    if any(camera.distortion):
        raise ValueError('native RECTIFIED_RIGHT depth must use an undistorted active plane')
    total = depth.size
    flat_depth = depth.reshape(-1)
    a, b, c, d = camera.roi
    k = camera.k
    stored = _coefficients(camera, plane)
    centered_x, row_qy, transform, translation, normal = (np.frombuffer(value, dtype=np.float64) for value in stored[:5])
    transform = transform.reshape(3, 3)
    pixel_q_error, delta, rotation_delta, plane_delta, normal_l1 = stored[5:]
    absolute = config.extra_depth_error_m + (0. if profile is None else profile.depth_error_m)
    relative = config.extra_relative_error + (0. if profile is None else profile.depth_relative_error)

    def classify(indices):
        # 최대 BLOCK_PIXELS개의 작업 배열만 유지한다. bbox radius는 축별 pad다.
        with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
            yy, xx = indices // camera.width, indices % camera.width
            values = flat_depth[indices]
            z = values.astype(np.float64) * .001
            valid = (np.isfinite(z) & (z > 0) & (values <= 65535)
                     & (z >= camera.near_m) & (z <= camera.far_m)
                     & (xx >= a) & (xx < c) & (yy >= b) & (yy < d))
            # invalid 원 행의 분모는 보존하되 기하 계산은 실제 valid index에만 한다.
            locations = np.flatnonzero(valid)
            z = z[locations]
            qy = row_qy[yy[locations]]
            qx = (centered_x[xx[locations]] - k[0][1] * qy) / k[0][0]
            q = np.stack((qx, qy, np.ones_like(qx)), axis=-1)
            points = z[:, None] * (q @ transform) + translation
            norm = np.sqrt((q * q).sum(axis=-1))
            min_hz = 1 / (norm + pixel_q_error) - delta
            q_error = pixel_q_error + np.where(min_hz > 0,
                delta * (1 + norm + pixel_q_error) / min_hz, np.inf)
            z_error = absolute + relative * z
            radius = ((z + z_error) * q_error + z_error * norm
                + rotation_delta * (z + z_error) * (norm + q_error)
                + camera.translation_error_m + 1e-9)
            heights = points @ normal + plane.offset
            h_error = radius * normal_l1 + plane.offset_error_m + 1e-9
            # 축별 translation/error box의 L2 반경은 sqrt(3)*radius 이하다.
            h_error += plane_delta * (np.sqrt((points * points).sum(axis=-1)) + math.sqrt(3) * radius)
            finite = np.isfinite(radius) & np.isfinite(heights) & np.isfinite(h_error)
            floor_values = (finite & (heights - h_error >= -config.floor_tolerance_m)
                     & (heights + h_error <= config.floor_tolerance_m))
            hit_values = (finite & (heights + h_error >= config.obstacle_min_height_m)
                    & (heights - h_error <= config.obstacle_max_height_m))
            floor, hits = np.zeros(len(indices), dtype=bool), np.zeros(len(indices), dtype=bool)
            floor[locations], hits[locations] = floor_values, hit_values
        return points, radius, valid, floor, hits

    metric = (profile is not None and profile.metric_qualified and camera.qualified and plane.qualified
              and stamp is not None and stamp.captured_us is not None and profile.time_qualified)
    free = metric and profile.free_qualified
    hit_indices = np.empty(total if config.max_hit_boxes else 0, dtype=np.int32)
    patch_indices = np.empty(total if config.max_patches else 0, dtype=np.int32)
    patch_floors = np.empty(total if config.max_patches else 0, dtype=bool)
    valid_count = floor_count = hit_count = patch_count = 0
    for start in range(0, total, BLOCK_PIXELS):
        indices = np.arange(start, min(start + BLOCK_PIXELS, total), dtype=np.int32)
        points, radii, valid, floor, hits = classify(indices)
        valid_count += int(np.count_nonzero(valid))
        floor_count += int(np.count_nonzero(floor))
        current_hits = int(np.count_nonzero(hits))
        if config.max_hit_boxes:
            hit_indices[hit_count:hit_count + current_hits] = indices[hits]
        hit_count += current_hits
        if config.max_patches:
            chosen = valid & (indices % camera.width % config.stride == 0)
            chosen &= indices // camera.width % config.stride == 0
            current_patches = int(np.count_nonzero(chosen))
            patch_indices[patch_count:patch_count + current_patches] = indices[chosen]
            patch_floors[patch_count:patch_count + current_patches] = floor[chosen]
            patch_count += current_patches
    chosen_hits = _selected(hit_indices[:hit_count], config.max_hit_boxes)
    selected = _stratified(patch_indices[:patch_count], patch_floors[:patch_count], config.max_patches, camera.width)
    del hit_indices, patch_indices, patch_floors, points, radii, valid, floor, hits, indices

    hit_boxes = ()
    for start in range(0, len(chosen_hits), BLOCK_PIXELS):
        points, radii, valid, floor, hits = classify(chosen_hits[start:start + BLOCK_PIXELS])
        lower, upper = np.nextafter(points - radii[:, None], -np.inf), np.nextafter(points + radii[:, None], np.inf)
        hit_boxes = PointBoundsBatch(lower, upper, bool(metric), None if stamp is None else stamp.key)
        del points, radii, valid, floor, hits
    patches = ()
    source_key = None if stamp is None else stamp.key
    for start in range(0, len(selected), BLOCK_PIXELS):
        indices = selected[start:start + BLOCK_PIXELS]
        values = flat_depth[indices].astype(np.float64)
        bounds = axial_ray_bounds_many(indices % camera.width, indices // camera.width, values, camera,
            depth_error_m=absolute + relative * values * .001)
        origin_lo, origin_hi, direction_lo, direction_hi, range_lo, range_hi, near, far, valid = bounds
        if np.any(valid):
            # 원 행 순서와 bounds를 유지하며 전체 scalar DTO 생성을 피한다.
            pixels = np.column_stack((indices[valid] % camera.width, indices[valid] // camera.width))
            patches = RayBundleBatch(origin_lo[valid], origin_hi[valid], direction_lo[valid], direction_hi[valid],
                range_lo[valid], range_hi[valid], near[valid], far[valid], bool(free), source_key, pixels)
    return DepthEvidence(stamp, calibration_id, input_kind, hit_boxes, patches,
        valid_count, floor_count, total - floor_count - len(hit_boxes), total - valid_count,
        hit_count - len(hit_boxes), valid_count - len(patches), bool(metric),
        'QUALIFIED_RAYS' if free else 'HIT_ONLY' if metric else 'UNKNOWN')
