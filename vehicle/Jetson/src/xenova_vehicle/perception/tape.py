"""유한 birdview white-band에서 frame-local tape graph를 만든다.

색/폭/작업 cap은 조정할 실험 profile이다. NN mask는 보조 후보로만 보유하며,
calibration·통행권·FREE·stable ID를 만들지 않는다. NumPy/OpenCV는 호출 시 불러온다.
"""

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import math
import struct
from typing import Any

from .inputs import FrameStamp
from .geometry import timing_start, timing_finish


ENDPOINT_KINDS = frozenset(('physical_junction', 'observed_paint_end', 'FOV_clip', 'occluded', 'predicted'))


@dataclass(frozen=True)
class TapeProfile:
    """미실측 초기 값. 좌표는 rear-axle base의 x전방/y왼쪽이며 길이는 m다."""

    x_min_m: float = 0.15
    x_max_m: float = 2.0
    y_min_m: float = -1.2
    y_max_m: float = 1.2
    cell_m: float = 0.005
    saturation_max: int = 85
    value_min: int = 150
    contrast_min: int = 25
    tape_width_min_m: float = 0.010
    tape_width_max_m: float = 0.045
    min_strip_length_m: float = 0.08
    simplify_error_m: float = 0.004
    extraction_error_m: float = 0.005
    max_projection_error_m: float = 0.10
    max_thin_iterations: int = 64
    max_components: int = 512
    max_component_work: int = 1_048_576
    max_graph_pixels: int = 20_000
    edge_work_cap: int = 500_000
    max_strips: int = 32
    max_points_per_strip: int = 64

    def __post_init__(self):
        for name in ('x_min_m', 'x_max_m', 'y_min_m', 'y_max_m', 'cell_m', 'tape_width_min_m',
                     'tape_width_max_m', 'min_strip_length_m', 'simplify_error_m', 'extraction_error_m',
                     'max_projection_error_m'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('tape profile requires finite scalars')
        if (self.x_min_m >= self.x_max_m or self.y_min_m >= self.y_max_m
                or not .001 <= self.cell_m <= .05 or not 0 < self.tape_width_min_m <= self.tape_width_max_m <= .20
                or min(self.min_strip_length_m, self.max_projection_error_m) <= 0
                or min(self.simplify_error_m, self.extraction_error_m) < 0):
            raise ValueError('invalid tape metric profile')
        for name, cap in (('saturation_max', 255), ('value_min', 255), ('contrast_min', 255),
                          ('max_thin_iterations', 256), ('max_components', 2048),
                          ('max_component_work', 4_194_304), ('max_graph_pixels', 40_000),
                          ('edge_work_cap', 2_000_000),
                          ('max_strips', 32), ('max_points_per_strip', 64)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError('invalid tape work bound: ' + name)
        if self.rows * self.columns > 262_144 or max(self.rows, self.columns) > 1024:
            raise ValueError('birdview exceeds bounded raster')

    @property
    def rows(self):
        return int(math.floor((self.x_max_m - self.x_min_m) / self.cell_m)) + 1

    @property
    def columns(self):
        return int(math.floor((self.y_max_m - self.y_min_m) / self.cell_m)) + 1

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class TapeStrip:
    """width_complete=False의 폭 구간은 읽힌 cross-section에만 적용한다. 가림 밖을 채우지 않는다."""

    local_id: int
    points_xy: tuple
    width_interval_m: tuple[float, float]
    error_m: float
    start_kind: str
    end_kind: str
    observed: bool = True
    closed: bool = False
    endpoint_error_m: tuple = (None, None)
    point_error_xy: tuple = ()
    segment_error_xy: tuple = ()
    width_complete: bool = True
    censored_cross_sections: int = 0

    def __post_init__(self):
        if (type(self.local_id) is not int or self.local_id < 0 or type(self.points_xy) is not tuple
                or not 2 <= len(self.points_xy) <= 64 or self.start_kind not in ENDPOINT_KINDS
                or self.end_kind not in ENDPOINT_KINDS):
            raise ValueError('invalid frame-local tape strip')
        if any(type(p) is not tuple or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v)
                                                       for v in p) for p in self.points_xy):
            raise ValueError('invalid tape point')
        if (type(self.width_interval_m) is not tuple or len(self.width_interval_m) != 2
                or not 0 <= self.width_interval_m[0] <= self.width_interval_m[1]
                or not math.isfinite(self.error_m) or self.error_m < 0
                or type(self.observed) is not bool or type(self.closed) is not bool
                or type(self.width_complete) is not bool or type(self.censored_cross_sections) is not int
                or self.censored_cross_sections < 0):
            raise ValueError('invalid tape width/error/observation')
        if (type(self.endpoint_error_m) is not tuple or len(self.endpoint_error_m) != 2
                or any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0)
                       for v in self.endpoint_error_m)):
            raise ValueError('invalid physical endpoint error')
        for values, size in ((self.point_error_xy, len(self.points_xy)),
                             (self.segment_error_xy, len(self.points_xy) - 1)):
            if (type(values) is not tuple or values and len(values) != size
                    or any(type(p) is not tuple or len(p) != 2 or any(type(v) not in (int, float)
                        or not math.isfinite(v) or v < 0 for v in p) for p in values)):
                raise ValueError('invalid axis-wise tape geometry error')

    @property
    def length_m(self):
        return sum(math.dist(a, b) for a, b in zip(self.points_xy, self.points_xy[1:]))


@dataclass(frozen=True)
class TapeJunction:
    local_id: int
    xy: tuple[float, float]
    degree: int
    kind: str


@dataclass(frozen=True)
class TapeGraph:
    strips: tuple[TapeStrip, ...]
    junctions: tuple[TapeJunction, ...] = ()
    status: str = 'VALID'
    complete: bool = True
    reasons: tuple[str, ...] = ()
    source_key: tuple | None = None
    calibration_qualified: bool = False
    work: dict = field(default_factory=dict)
    profile: TapeProfile = TapeProfile()
    mask: Any = field(default=None, repr=False, compare=False)
    skeleton: Any = field(default=None, repr=False, compare=False)
    valid_mask: Any = field(default=None, repr=False, compare=False)
    nn_candidates: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if (type(self.strips) is not tuple or len(self.strips) > 32
                or any(type(s) is not TapeStrip for s in self.strips)
                or type(self.junctions) is not tuple or len(self.junctions) > 64
                or len({s.local_id for s in self.strips}) != len(self.strips)
                or self.status not in ('VALID', 'EMPTY', 'UNKNOWN') or type(self.complete) is not bool
                or type(self.calibration_qualified) is not bool):
            raise ValueError('invalid bounded tape graph')

    def descriptor(self):
        return dict(status=self.status, complete=self.complete, reasons=list(self.reasons),
            source_key=self.source_key, calibration_qualified=self.calibration_qualified,
            strips=[asdict(s) for s in self.strips], junctions=[asdict(j) for j in self.junctions], work=dict(self.work))


def _thin_tables():
    tables=[]
    for step in (0,1):
        flags=[]
        for code in range(256):
            p=tuple(bool(code & (1<<i)) for i in range(8))
            transitions=sum(not p[i] and p[(i+1)%8] for i in range(8))
            corner=(not (p[0] and p[2] and p[4]) and not (p[2] and p[4] and p[6]) if step==0 else
                    not (p[0] and p[2] and p[6]) and not (p[0] and p[4] and p[6]))
            flags.append(2<=sum(p)<=6 and transitions==1 and corner)
        tables.append(bytes(flags))
    return tuple(tables)


# 원 N,NE,E,SE,S,SW,W,NW 순서. 캐시는 LUT512B+kernel36B이며 frame 배열은 보유하지 않는다.
_THIN_REMOVE=_thin_tables()
_THIN_KERNEL=struct.pack('<9f',128,1,2,64,0,4,32,16,8)


def _thin(np, mask, limit):
    """원 bool mask의 Zhang-Suen predicate/반복 한도를 정수 이웃 코드로 평가한다."""
    import cv2
    image = mask.copy()
    if not image.size and limit>0:return image,True,1
    kernel=np.frombuffer(_THIN_KERNEL,dtype='<f4').reshape(3,3)
    lookup=tuple(np.frombuffer(table,dtype=np.bool_) for table in _THIN_REMOVE)
    for iteration in range(limit):
        changed = False
        for step in (0, 1):
            # 0/1×정수 kernel의 합은0..255라 float32 합과 CV_16S 변환도 정수로 exact하다.
            codes=cv2.filter2D(image.astype(np.uint8),cv2.CV_16S,kernel,borderType=cv2.BORDER_CONSTANT)
            remove = image & lookup[step][codes]
            if np.any(remove):
                image[remove] = False
                changed = True
        if not changed:
            return image, True, iteration + 1
    return image, False, limit


def _readonly(np, value):
    return np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)


@lru_cache(maxsize=2)
def _birdview_map(camera, plane, profile):
    """불변 보정/profile의 투영만 보유한다. 영상·가림·분류 결과는 캐시하지 않는다."""
    import numpy as np
    from .geometry import ground_to_pixels

    x = profile.x_min_m + np.arange(profile.rows, dtype=np.float64)[:, None] * profile.cell_m
    y = profile.y_min_m + np.arange(profile.columns, dtype=np.float64)[None, :] * profile.cell_m
    u, v, valid = ground_to_pixels(x, y, camera, plane)
    x0, y0, x1, y1 = camera.roi
    margin = camera.pixel_error + 1.0
    valid = valid & (u - margin >= x0) & (u + margin < x1) & (v - margin >= y0) & (v + margin < y1)
    return _readonly(np, u), _readonly(np, v), _readonly(np, valid)


def _trace(np, cv2, skeleton, valid, occluded, band, projection_error, projection_xy, profile, work):
    pixels = set(map(tuple,np.argwhere(skeleton).tolist()))
    adjacency = {p: tuple((p[0] + dy, p[1] + dx) for dy, dx in
        ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
        if (p[0] + dy, p[1] + dx) in pixels) for p in pixels}
    special = np.zeros(skeleton.shape, dtype=np.uint8)
    for p, neighbors in adjacency.items():
        if len(neighbors) != 2:
            special[p] = 1
    count, labels = cv2.connectedComponents(special, connectivity=8)
    groups = {}
    for p in pixels:
        if labels[p]:
            groups.setdefault(int(labels[p]), []).append(p)
    # 독립 cycle도 유한 graph로 남기되 도로의 열린 strip으로 바꾸지 않는다.
    _, components = cv2.connectedComponents(skeleton.astype(np.uint8), connectivity=8)
    first, represented = {}, set()
    for p in sorted(pixels):
        component = int(components[p])
        first.setdefault(component, p)
        if labels[p]:
            represented.add(component)
    cycle_nodes = set()
    for component, p in sorted(first.items()):
        if component not in represented:
            groups[count] = [p]
            labels[p] = count
            cycle_nodes.add(count)
            count += 1
    visited = set()
    paths = []
    trace_complete = True
    steps = 0
    for node, group in sorted(groups.items()):
        for first in sorted(group):
            for second in adjacency[first]:
                edge = tuple(sorted((first, second)))
                if labels[second] == node or edge in visited:
                    continue
                path = [first]
                previous, current = first, second
                for _ in range(profile.max_graph_pixels + 1):
                    steps += 1
                    if steps > profile.max_graph_pixels * 4 or len(paths) >= profile.max_strips * 8:
                        trace_complete = False
                        break
                    visited.add(tuple(sorted((previous, current))))
                    path.append(current)
                    if labels[current] != 0:
                        paths.append((path, node, int(labels[current])))
                        break
                    choices = [p for p in adjacency[current] if p != previous]
                    if len(choices) != 1:
                        break
                    previous, current = current, choices[0]
                    if current == first:
                        visited.add(tuple(sorted((previous, current))))
                        path.append(first)
                        paths.append((path, node, node))
                        break
                if not trace_complete:
                    break
            if not trace_complete:
                break
        if not trace_complete:
            break
    radius = int(math.ceil(profile.tape_width_max_m / profile.cell_m)) + 2
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    near_occ = cv2.dilate(occluded.astype(np.uint8), kernel) != 0
    invalid = (~valid).astype(np.uint8)
    invalid[[0, -1], :] = 1
    invalid[:, [0, -1]] = 1
    near_invalid = cv2.dilate(invalid, kernel) != 0
    degrees = dict.fromkeys(groups, 0)
    for _, start, end in paths:
        degrees[start] += 1
        degrees[end] += 1

    def kind(node):
        group = groups[node]
        if node in cycle_nodes:
            return 'predicted'
        if any(near_occ[p] for p in group):
            return 'occluded'
        if any(near_invalid[p] or p[0] in (0, valid.shape[0] - 1) or p[1] in (0, valid.shape[1] - 1) for p in group):
            return 'FOV_clip'
        return 'physical_junction' if degrees[node] >= 3 else 'observed_paint_end'

    strips = []
    complete = trace_complete
    kinds = {node: kind(node) for node in groups}
    work.update(trace_steps=steps, strip_paths=len(paths), short_paths=0, edge_pair_missing=0,
                edge_pair_censored=0, edge_pair_internal=0, no_width_paths=0,
                width_rejected=0, point_cap_rejected=0, edge_budget_exhausted=0,
                trace_cap_exhausted=int(not trace_complete), strip_cap_exhausted=0, junction_cap_exhausted=0)
    for path, start, end in paths:
        points = [(profile.x_min_m + p[0] * profile.cell_m, profile.y_min_m + p[1] * profile.cell_m) for p in path]
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        if length < profile.min_strip_length_m:
            work['short_paths'] += 1
            continue
        widths = []
        center_pad = 0.
        width_projection_pad = 0.
        edge_budget = False
        censored_sections = 0
        internal_sections = 0
        arc = [0.]
        for a, b in zip(points, points[1:]):
            arc.append(arc[-1] + math.dist(a, b))
        for index in range(2, len(path) - 2):
            if ((kinds[start] == 'physical_junction' and arc[index] < 2 * profile.tape_width_max_m)
                    or (kinds[end] == 'physical_junction' and length - arc[index] < 2 * profile.tape_width_max_m)):
                continue
            first, last = path[index - 2], path[index + 2]
            dy, dx = last[0] - first[0], last[1] - first[1]
            norm = math.hypot(dx, dy)
            if norm <= 1e-9:
                continue
            normal = -dx / norm, dy / norm
            edges = []
            causes = []
            limit = int(math.ceil(2 * profile.tape_width_max_m / profile.cell_m)) + 2
            for sign in (-1, 1):
                for offset in range(1, limit + 1):
                    if work['edge_tests'] >= profile.edge_work_cap:
                        edge_budget = True
                        break
                    work['edge_tests'] += 1
                    row = int(round(path[index][0] + sign * offset * normal[0]))
                    column = int(round(path[index][1] + sign * offset * normal[1]))
                    if not (0 <= row < band.shape[0] and 0 <= column < band.shape[1]) or not valid[row, column]:
                        causes.append('censored')
                        break
                    if occluded[row, column]:
                        causes.append('censored')
                        break
                    if not band[row, column]:
                        edges.append(offset - .5)
                        causes.append('measured')
                        break
                else:
                    causes.append('internal')
                if edge_budget:
                    break
            if edge_budget:
                break
            if len(edges) == 2:
                widths.append(sum(edges) * profile.cell_m)
                center_pad = max(center_pad, abs(edges[0] - edges[1]) * profile.cell_m / 2)
                ex, ey = projection_xy[path[index]]
                width_projection_pad = max(width_projection_pad, float(abs(normal[0]) * ex + abs(normal[1]) * ey))
            else:
                work['edge_pair_missing'] += 1
                # ROI/가림 바깥은 폭의 반증이 아니다. 읽지 못한 cross-section을
                # 측정값에 넣지 않고 표시한다. 내부에서 끝까지 edge를 못 찾은 경우만
                # 미해결 topology로 남겨 다른 정상 strip까지 VALID로 승격하지 않는다.
                if 'internal' in causes:
                    work['edge_pair_internal'] += 1
                    internal_sections += 1
                    complete = False
                else:
                    work['edge_pair_censored'] += 1
                    censored_sections += 1
        if edge_budget:
            work['edge_budget_exhausted'] = 1
            complete = False
            break
        if not widths:
            work['no_width_paths'] += 1
            complete = False
            continue
        width = float(np.median(widths))
        pad = 2 * profile.cell_m
        if width + pad < profile.tape_width_min_m or width - pad > profile.tape_width_max_m:
            work['width_rejected'] += 1
            continue
        simplified = cv2.approxPolyDP(np.asarray(points, dtype=np.float32).reshape(-1, 1, 2),
                                     profile.simplify_error_m, start == end).reshape(-1, 2)
        if len(simplified) < 2:
            continue
        if len(simplified) > profile.max_points_per_strip:
            work['point_cap_rejected'] += 1
            complete = False
            continue
        if len(strips) == profile.max_strips:
            work['strip_cap_exhausted'] = 1
            complete = False
            break
        projection_pad = max(float(projection_error[p]) for p in path)
        local_pad = center_pad + profile.extraction_error_m + profile.cell_m / math.sqrt(2)
        point_error = (projection_pad + center_pad + profile.extraction_error_m + profile.simplify_error_m
                       + profile.cell_m / math.sqrt(2))
        physical_tip_error = point_error + width / 2 + pad
        endpoint_error = tuple(physical_tip_error if kinds[node] in ('physical_junction', 'observed_paint_end')
                               else None for node in (start, end))
        original = np.asarray(points, dtype=np.float64)
        vertex_indices = [int(np.argmin(np.sum((original - vertex) ** 2, axis=1))) for vertex in simplified]
        point_errors = tuple(tuple(float(projection_xy[path[index]][axis] + local_pad
            + abs(original[index, axis] - vertex[axis])) for axis in (0, 1))
            for index, vertex in zip(vertex_indices, simplified))
        segment_errors = []
        for i, (a, b) in enumerate(zip(simplified, simplified[1:])):
            lo, hi = sorted(vertex_indices[i:i + 2])
            source = original[lo:hi + 1]
            delta = b.astype(np.float64) - a
            norm2 = float(delta @ delta)
            fraction = np.clip(((source - a) @ delta) / max(norm2, 1e-20), 0., 1.)
            displacement = np.max(np.abs(source - (a + fraction[:, None] * delta)), axis=0)
            source_error = np.asarray([projection_xy[p] for p in path[lo:hi + 1]])
            segment_errors.append(tuple(float(v) for v in np.max(source_error, axis=0) + local_pad + displacement))
        strips.append(TapeStrip(len(strips), tuple(tuple(float(v) for v in p) for p in simplified),
            (max(0., width - pad - 2 * width_projection_pad), width + pad + 2 * width_projection_pad),
            point_error, kinds[start], kinds[end], True, start == end, endpoint_error, point_errors, tuple(segment_errors),
            not (censored_sections or internal_sections), censored_sections))
    all_junctions = [(node, group) for node, group in sorted(groups.items()) if degrees[node] >= 3]
    if len(all_junctions) > 64:
        work['junction_cap_exhausted'] = 1
        complete = False
    junctions = tuple(TapeJunction(node, (profile.x_min_m + sum(p[0] for p in group) / len(group) * profile.cell_m,
        profile.y_min_m + sum(p[1] for p in group) / len(group) * profile.cell_m), degrees[node], kinds[node])
        for node, group in all_junctions[:64])
    return strips, junctions, complete


def extract_tape(bgr, camera, plane, profile=TapeProfile(), *, stamp=None, occlusion_mask=None, nn_candidates=None, diagnostics=None):
    """원 RGB만으로 white-band 후보를 만들고 가림/경계 tip을 보존한다. NN은 mask gate가 아니다."""
    timing=timing_start(diagnostics,'tape.birdview_remap')
    if type(profile) is not TapeProfile or stamp is not None and type(stamp) is not FrameStamp:
        raise ValueError('expected tape profile and optional original FrameStamp')
    import numpy as np
    import cv2
    from .geometry import project_ground_bounds_many

    if (not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8
            or bgr.shape != (camera.height, camera.width, 3)):
        raise ValueError('tape input differs from camera BGR shape')
    if occlusion_mask is not None and (not isinstance(occlusion_mask, np.ndarray)
            or occlusion_mask.shape != bgr.shape[:2] or occlusion_mask.dtype not in (np.bool_, np.uint8)):
        raise ValueError('occlusion mask must be bool/uint8 in original RGB coordinates; nonzero is blocked')
    u, v, valid = _birdview_map(camera, plane, profile)
    warped = cv2.remap(bgr, u, v, cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    valid = np.asarray(valid, dtype=np.bool_).copy()
    occ = np.zeros(valid.shape, dtype=np.bool_)
    if occlusion_mask is not None:
        footprint = cv2.dilate((occlusion_mask != 0).astype(np.uint8), np.ones((3, 3), np.uint8))
        occ = cv2.remap(footprint, u, v,
                        cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0) != 0
    timing_finish(diagnostics,timing)
    timing=timing_start(diagnostics,'tape.mask')
    hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
    white = valid & ~occ & (hsv[..., 1] <= profile.saturation_max) & (hsv[..., 2] >= profile.value_min)
    work = dict(grid_cells=int(white.size), components=0, component_cells=0, thin_iterations=0,
                graph_pixels=0, projection_checks=0, projection_batches=0,
                projection_unbounded=0, projection_limited=0, edge_tests=0)
    reasons = []
    complete = True
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), connectivity=8)
    if count - 1 > profile.max_components:
        complete = False
        reasons.append('COMPONENT_CAP')
        white[:] = False
    else:
        for index in range(1, count):
            bx, by, width, height, area = (int(v) for v in stats[index])
            work['components'] += 1
            if area < 4:
                white[labels == index] = False
                continue
            x0, x1 = max(0, bx - 4), min(white.shape[1], bx + width + 4)
            y0, y1 = max(0, by - 4), min(white.shape[0], by + height + 4)
            work['component_cells'] += (x1 - x0) * (y1 - y0)
            if work['component_cells'] > profile.max_component_work:
                complete = False
                reasons.append('COMPONENT_WORK_CAP')
                break
            part = labels[y0:y1, x0:x1] == index
            ring = (cv2.dilate(part.astype(np.uint8), np.ones((7, 7), np.uint8)) != 0) & ~part
            ring &= valid[y0:y1, x0:x1] & ~white[y0:y1, x0:x1]
            values = hsv[y0:y1, x0:x1, 2]
            if not np.any(ring) or float(np.median(values[part])) - float(np.median(values[ring])) < profile.contrast_min:
                white[labels == index] = False
    distance = cv2.distanceTransform(white.astype(np.uint8), cv2.DIST_L2, 5)
    # T/X 교차부의 내접 원은 단일 band 반폭보다 크다. 그 영역을 넓은 물체로
    # 지우면 실제 분기가 네 개의 가림 tip으로 바뀌므로 더 깊은 white core만 격리한다.
    # 그보다 얇지만 폭 profile을 벗어난 물체는 아래 edge-pair 검사에서 제외한다.
    wide = distance * profile.cell_m > profile.tape_width_max_m
    if np.any(wide):
        radius = max(1, int(math.ceil(profile.tape_width_max_m / profile.cell_m)))
        censored = cv2.dilate(wide.astype(np.uint8), np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)) != 0
        white[censored] = False
        occ |= censored
        reasons.append('WIDE_WHITE_SURFACE_CENSORED')
    timing_finish(diagnostics,timing)
    timing=timing_start(diagnostics,'tape.skeleton')
    skeleton, converged, work['thin_iterations'] = _thin(np, white, profile.max_thin_iterations)
    timing_finish(diagnostics,timing)
    if not converged:
        complete = False
        reasons.append('THINNING_CAP')
    work['graph_pixels'] = int(np.count_nonzero(skeleton))
    if work['graph_pixels'] > profile.max_graph_pixels:
        complete = False
        reasons.append('GRAPH_PIXEL_CAP')
        strips, junctions = [], ()
    else:
        timing=timing_start(diagnostics,'tape.projection')
        projection_error = np.zeros(valid.shape, dtype=np.float64)
        projection_xy = np.zeros(valid.shape + (2,), dtype=np.float64)
        near_invalid = cv2.dilate((~valid).astype(np.uint8), np.ones((3, 3), np.uint8)) != 0
        rows, columns = np.nonzero(skeleton)
        work['projection_checks'] = len(rows)
        if len(rows):
            work['projection_batches'] = 1
            lower, upper, bounded = project_ground_bounds_many(u[rows, columns], v[rows, columns],
                camera, plane, pixel_error=camera.pixel_error + 1.0,diagnostics=diagnostics)
            nominal = np.column_stack((profile.x_min_m + rows * profile.cell_m,
                                       profile.y_min_m + columns * profile.cell_m))
            pad = np.maximum(np.abs(lower[:, :2] - nominal), np.abs(upper[:, :2] - nominal))
            errors = np.nextafter(np.hypot(pad[:, 0], pad[:, 1]), np.inf)
            projection_error[rows, columns] = errors
            projection_xy[rows, columns] = np.nextafter(pad, np.inf)
            limited = bounded & (errors > profile.max_projection_error_m)
            work['projection_unbounded'] = int(np.count_nonzero(~bounded))
            work['projection_limited'] = int(np.count_nonzero(limited))
            if np.any(~bounded & ~near_invalid[rows, columns]):
                complete = False
                reasons.append('UNBOUNDED_PROJECTION')
            if np.any(limited):
                reasons.append('PROJECTION_LIMIT')
            rejected_rows, rejected_columns = rows[~bounded | limited], columns[~bounded | limited]
            skeleton[rejected_rows, rejected_columns] = False
            valid[rejected_rows, rejected_columns] = False
        timing_finish(diagnostics,timing)
        timing=timing_start(diagnostics,'tape.graph')
        strips, junctions, trace_complete = _trace(np, cv2, skeleton, valid, occ, white, projection_error,
                                                  projection_xy, profile, work)
        timing_finish(diagnostics,timing)
        if not trace_complete:
            complete = False
            for key, reason in (('trace_cap_exhausted', 'TRACE_WORK_CAP'),
                    ('edge_budget_exhausted', 'EDGE_WORK_CAP'), ('edge_pair_internal', 'UNRESOLVED_INTERIOR_EDGE_PAIR'),
                    ('no_width_paths', 'NO_MEASURED_TAPE_WIDTH'), ('point_cap_rejected', 'STRIP_POINT_CAP'),
                    ('strip_cap_exhausted', 'STRIP_COUNT_CAP'), ('junction_cap_exhausted', 'JUNCTION_COUNT_CAP')):
                if work[key]:
                    reasons.append(reason)
    return TapeGraph(tuple(strips), junctions, 'UNKNOWN' if not complete else 'VALID' if strips else 'EMPTY',
        complete, tuple(reasons), None if stamp is None else stamp.key,
        bool(camera.qualified and plane.qualified), work, profile,
        _readonly(np, white), _readonly(np, skeleton), _readonly(np, valid), nn_candidates)
