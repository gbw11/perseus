"""독립 RGB/RIGHT ray의 첫 표면을 합성 관측으로 만든다. GT를 core로 넘기지 않는다.

지면은 world z=0, cone/tires도 simulator와 같은 axis-aligned box 근사다.
holes/noise는 첫 표면 선택 뒤 적용한다. np/cv2는 렌더 호출에서만 import한다.
실물 stereo 품질이나 free 허가의 검증이 아니다.
"""

from dataclasses import asdict, dataclass
import hashlib
import math

from ..perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from .scene import SceneSnapshot, canonical


@dataclass(frozen=True)
class DepthEffects:
    """합성 axial-mm 오차. hole_rects는 RIGHT 픽셀 [u0,v0,u1,v1), seed는 재현용이다."""

    seed: int = 0
    noise_std_mm: float = 0.0
    noise_mm_per_m2: float = 0.0
    dropout_fraction: float = 0.0
    hole_rects: tuple[tuple[int, int, int, int], ...] = ()

    def __post_init__(self):
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            raise ValueError('synthetic seed must be uint64')
        if any(type(v) not in (float, int) or not math.isfinite(v) or v < 0
               for v in (self.noise_std_mm, self.noise_mm_per_m2, self.dropout_fraction)):
            raise ValueError('synthetic depth effects must be finite and nonnegative')
        if self.dropout_fraction > 1 or type(self.hole_rects) is not tuple or len(self.hole_rects) > 32:
            raise ValueError('synthetic dropout exceeds its bound')
        for rect in self.hole_rects:
            if (type(rect) is not tuple or len(rect) != 4 or any(type(v) is not int or v < 0 for v in rect)
                    or not rect[0] < rect[2] <= 1280 or not rect[1] < rect[3] <= 720):
                raise ValueError('synthetic hole rectangle is invalid')

    @property
    def fields(self):
        return dict(version=1, algorithm='first-surface-then-PCG64-axial-mm-noise-and-dropout', **asdict(self))

    @property
    def sha256(self):
        return hashlib.sha256(canonical(self.fields)).hexdigest()


def calibration_for_effects(calibration, effects):
    """유한 오차 보장이 없는 Gaussian 관측을 zero-error metric/free profile로 보내지 않는다."""
    if not effects.noise_std_mm and not effects.noise_mm_per_m2:
        return calibration
    from ..perception.calibration import CalibrationProfile
    fields = calibration.to_dict()
    fields['right']['qualified'] = False
    fields['first_surface_qualified'] = False
    provenance = fields['provenance']
    if type(provenance) is not dict:
        provenance = {'producer_provenance': provenance}
    fields['provenance'] = dict(provenance, synthetic_sensor_effects=effects.fields,
                               noise_qualification='UNBOUNDED_GAUSSIAN_METRIC_UNQUALIFIED')
    return CalibrationProfile.from_dict(fields)


def _rays(camera):
    import numpy as np
    v, u = np.mgrid[:camera.height, :camera.width]
    pixels = np.stack((u, v, np.ones_like(u)), axis=-1).astype(np.float64)
    rectified = pixels @ np.linalg.inv(np.asarray(camera.k).reshape(3, 3)).T
    rectified /= rectified[..., 2, None]
    raw = rectified @ np.asarray(camera.rectification).reshape(3, 3)
    return raw @ np.asarray(camera.rotation).reshape(3, 3).T


def _world(camera, pose, rays):
    import numpy as np
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    rotation = np.array(((c, -s, 0), (s, c, 0), (0, 0, 1)), dtype=np.float64)
    origin = np.asarray(camera.translation) @ rotation.T + (x, y, 0)
    return origin, rays @ rotation.T


def _first_surface(origin, rays, boxes):
    """rectified optical Z=1인 ray의 첫 양수 계수, 즉 axial metre를 반환한다."""
    import numpy as np
    with np.errstate(divide='ignore', invalid='ignore'):
        ground = -origin[2] / rays[..., 2]
    depth = np.where((rays[..., 2] < -1e-12) & (ground > 0), ground, np.inf)
    label = np.where(np.isfinite(depth), 0, -1).astype(np.int16)
    for index, box in enumerate(boxes, 1):
        low = (box['x'] - box['w'] / 2, box['y'] - box['w'] / 2, 0.0)
        high = (box['x'] + box['w'] / 2, box['y'] + box['w'] / 2, box['h'])
        if all(low[axis] <= origin[axis] <= high[axis] for axis in range(3)):
            raise ValueError('synthetic camera is on or inside an opaque box; no free ray origin is available')
        enter = np.full(depth.shape, -np.inf)
        leave = np.full(depth.shape, np.inf)
        intersects = np.ones(depth.shape, dtype=bool)
        for axis in range(3):
            direction = rays[..., axis]
            parallel = np.abs(direction) <= 1e-12
            safe = np.where(parallel, 1.0, direction)
            a, b = (low[axis] - origin[axis]) / safe, (high[axis] - origin[axis]) / safe
            enter = np.maximum(enter, np.where(parallel, -np.inf, np.minimum(a, b)))
            leave = np.minimum(leave, np.where(parallel, np.inf, np.maximum(a, b)))
            intersects &= ~parallel | ((low[axis] <= origin[axis]) & (origin[axis] <= high[axis]))
        first = np.where(enter > 0, enter, leave)
        visible = intersects & (leave >= enter) & (first > 0) & (first < depth)
        depth[visible], label[visible] = first[visible], index
    return depth, label


def _ground_raster(scene):
    import cv2
    import numpy as np
    track = scene.fields['track']
    layout = track['layout']
    x0, y0, x1, y1 = layout['bounds']
    x0, y0, x1, y1 = x0 - 1, y0 - 1, x1 + 1, y1 + 1
    resolution = 0.01
    width, height = math.ceil((x1 - x0) / resolution) + 1, math.ceil((y1 - y0) / resolution) + 1
    if width * height > 2_000_000:
        raise ValueError('synthetic ground raster exceeds two million pixels')
    raster = np.full((height, width, 3), 110, np.uint8)
    center = np.asarray(track['centerline'], dtype=np.float64)
    tangent = np.roll(center, -1, axis=0) - np.roll(center, 1, axis=0)
    lengths = np.linalg.norm(tangent, axis=1)
    if np.any(lengths <= 1e-9):
        raise ValueError('synthetic centerline has degenerate tangent')
    normal = np.column_stack((-tangent[:, 1], tangent[:, 0])) / lengths[:, None]
    half = track['lane_width_m'] / 2
    left, right = center + half * normal, center - half * normal
    def pixels(points):
        return np.rint((np.asarray(points) - (x0, y0)) / resolution).astype(np.int32)
    for i in range(len(center)):
        j = (i + 1) % len(center)
        cv2.fillPoly(raster, [pixels((left[i], left[j], right[j], right[i]))], (35, 35, 35))
    thickness = max(1, round(.025 / resolution))
    for edge in (left, right):
        cv2.polylines(raster, [pixels(edge)], True, (245, 245, 245), thickness)
    # 원 코스의 divider 주기 .5m/on .25m와 입구 edge 지우기 순서를 재사용한다.
    station = 0.0
    for a, b in zip(center, np.roll(center, -1, axis=0)):
        distance = float(np.linalg.norm(b - a))
        if station % .5 < .25:
            cv2.line(raster, tuple(pixels(a)), tuple(pixels(b)), (245, 245, 245), thickness)
        station += distance
    paved = np.zeros((height, width), np.uint8)
    for polygon in layout['paved_areas']:
        cv2.fillPoly(paved, [pixels(polygon)], 1)
    raster[cv2.dilate(paved, np.ones((3, 3), np.uint8)) > 0] = (35, 35, 35)
    for line in layout['paint_lines']:
        cv2.polylines(raster, [pixels(line)], False, (245, 245, 245), thickness)
    cv2.line(raster, tuple(pixels(left[0])), tuple(pixels(right[0])), (245, 245, 245), thickness)
    return raster, x0, y0, resolution


def _nv12(bgr):
    import cv2
    import numpy as np
    height, width = bgr.shape[:2]
    planar = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420).reshape(-1)
    area = width * height
    packed = np.empty(area * 3 // 2, dtype=np.uint8)
    packed[:area] = planar[:area]
    packed[area::2] = planar[area:area * 5 // 4]
    packed[area + 1::2] = planar[area * 5 // 4:]
    return packed.tobytes()


class SyntheticCamera:
    """불변 camera profile의 offline producer. 한 epoch의 seq/time을 되감거나 바꾸지 않는다."""

    def __init__(self, calibration, effects=DepthEffects()):
        if calibration.kind != 'SYNTHETIC' or type(effects) is not DepthEffects:
            raise ValueError('synthetic producer requires an explicit synthetic calibration/effects profile')
        if (calibration.rgb.width, calibration.rgb.height, calibration.right.width, calibration.right.height) != (640, 360, 640, 480):
            raise ValueError('synthetic capture requires RGB640x360 and RIGHT640x480')
        if any(any(camera.distortion) for camera in (calibration.rgb, calibration.right)):
            raise ValueError('synthetic ray renderer currently requires explicit ideal pinhole cameras')
        self._calibration, self._effects = calibration_for_effects(calibration, effects), effects
        self._ray_cache = self._raster_cache = None
        self._last = self._last_input = None

    @property
    def calibration(self):
        return self._calibration

    @property
    def effects(self):
        return self._effects

    def capture(self, snapshot):
        import cv2
        import numpy as np
        if type(snapshot) is not SceneSnapshot:
            raise ValueError('capture requires one frozen scene snapshot')
        if self._last is not None and snapshot.epoch == self._last.epoch:
            if snapshot.sequence == self._last.sequence:
                if snapshot != self._last:
                    raise ValueError('same synthetic capture ID changed its snapshot')
                return self._last_input
            if snapshot.sequence < self._last.sequence or snapshot.sim_time_us <= self._last.sim_time_us:
                raise ValueError('synthetic sequence/time regressed or repeated without the original capture ID')
        if self._ray_cache is None:
            self._ray_cache = tuple(_rays(camera) for camera in (self.calibration.rgb, self.calibration.right))
        if self._raster_cache is None or self._raster_cache[0] != snapshot.scene.sha256:
            self._raster_cache = (snapshot.scene.sha256, _ground_raster(snapshot.scene))
        boxes = snapshot.scene.fields['track']['layout']['obstacle_seeds']
        color_origin, color_rays = _world(self.calibration.rgb, snapshot.pose, self._ray_cache[0])
        color_z, color_label = _first_surface(color_origin, color_rays, boxes)
        right_origin, right_rays = _world(self.calibration.right, snapshot.pose, self._ray_cache[1])
        right_z, _ = _first_surface(right_origin, right_rays, boxes)
        def roi(camera):
            v, u = np.mgrid[:camera.height, :camera.width]
            x0, y0, x1, y1 = camera.roi
            return (u >= x0) & (u < x1) & (v >= y0) & (v < y1)
        color_label[~roi(self.calibration.rgb)] = -1
        image = np.full((360, 640, 3), 20, np.uint8)
        ground = color_label == 0
        points = color_origin + np.where(ground, color_z, 0)[..., None] * color_rays
        raster, x0, y0, resolution = self._raster_cache[1]
        sampled = cv2.remap(raster, ((points[..., 0] - x0) / resolution).astype(np.float32),
            ((points[..., 1] - y0) / resolution).astype(np.float32), cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(110, 110, 110))
        image[ground] = sampled[ground]
        palette = {'cone': (25, 120, 230), 'box': (35, 35, 190), 'tires': (35, 35, 80)}
        for i, box in enumerate(boxes, 1):
            image[color_label == i] = palette.get(box['kind'], palette['box'])
        valid = np.isfinite(right_z) & (right_z >= self.calibration.right.near_m) & (right_z <= self.calibration.right.far_m)
        valid &= roi(self.calibration.right)
        safe_z = np.where(valid, right_z, 0)
        seed = hashlib.sha256(canonical([snapshot.epoch, snapshot.sequence, self.effects.seed])).digest()
        rng = np.random.Generator(np.random.PCG64(int.from_bytes(seed[:16], 'little')))
        sigma = self.effects.noise_std_mm + self.effects.noise_mm_per_m2 * safe_z**2
        measured = np.rint(safe_z * 1000 + rng.normal(0, sigma, size=right_z.shape))
        valid &= (measured >= max(1, self.calibration.right.near_m * 1000))
        valid &= (measured <= min(65535, self.calibration.right.far_m * 1000))
        if self.effects.dropout_fraction:
            valid &= rng.random(right_z.shape) >= self.effects.dropout_fraction
        for u0, v0, u1, v1 in self.effects.hole_rects:
            valid[v0:v1, u0:u1] = False
        depth = np.where(valid, measured, 0).astype('<u2').tobytes()
        source = 'sim:' + snapshot.epoch + ':' + self.effects.sha256[:16]
        clock = 'simulation:' + snapshot.epoch
        def stamp(stream):
            return FrameStamp(source, stream, snapshot.sequence, clock,
                              (snapshot.sim_time_us, snapshot.sim_time_us), snapshot.sim_time_us)
        result = PerceptionInput('SYNTHETIC', self.calibration.sha256,
            ImageFrame(stamp('rgb_video'), 640, 360, 'NV12', _nv12(image), 0),
            ImageFrame(stamp('depth_right'), 640, 480, 'RAW16_LE', depth, 0))
        self._last, self._last_input = snapshot, result
        return result
