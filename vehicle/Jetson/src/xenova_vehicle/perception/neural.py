"""원 YOLOP source mask를 유한 평면 후보와 도로 가설의 보조 support로 소비한다.

argmax support는 모델 후보의 일치 비율이며 확률·통행권·FREE가 아니다. 모든 기하
가설과 그 모호함을 유지한다. 늦은 NN은 자기 RGB key에만 붙고 투표를 누적하지 않는다.
NumPy와 투영 연산은 호출 시에만 사용하며 카메라·모델 파일·통신을 열지 않는다.
"""

from dataclasses import asdict, dataclass, field, replace
import hashlib
import math
from typing import Any

from .calibration import CalibrationProfile
from .geometry import PointBounds
from .inputs import FrameStamp, PerceptionInput
from .road import LaneHypothesis, RoadResult
from .yolop import PREPROCESSING_ID, DetectionCandidate, YolopCandidates


MAX_PLANE_POINTS = 128
MAX_SCORED_HYPOTHESES = 64
MAX_DETECTIONS = 32


@dataclass(frozen=True, slots=True)
class NeuralPlanePoint:
    """하나의 원 NN cell이 덮는 source 2×2 영역의 plane box. stable ID가 아니다."""

    kind: str
    cell_index: int
    source_pixel_xy: tuple[float, float]
    bounds: PointBounds


@dataclass(frozen=True, slots=True)
class SemanticSupport:
    """기존 가설 station의 nominal source-mask 일치. 물리 차선의 확률이 아니다."""

    road_fraction: float | None
    lane_fraction: float | None
    combined_fraction: float | None
    road_tested: int
    lane_tested: int
    requested: int
    score_kind: str = 'binary_argmax_station_support_not_probability'

    def descriptor(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NeuralLayer:
    rgb_stamp: FrameStamp
    nn_source_key: tuple
    calibration_id: str
    input_kind: str
    nn_sequence: int
    nn_device_us: int
    nn_sdk_us: int
    nn_received_j_us: int
    nn_context_key: str
    nn_payload_sha256: str
    parser_id: str
    parser_config_sha256: str
    preprocessing_id: str
    road_points: tuple[NeuralPlanePoint, ...]
    lane_points: tuple[NeuralPlanePoint, ...]
    unprojected: tuple
    detections_source: tuple[DetectionCandidate, ...]
    counts: dict
    geometry_qualified: bool
    sampling_complete: bool
    projection_complete: bool
    road_mask: Any = field(repr=False, compare=False)
    lane_mask: Any = field(repr=False, compare=False)
    live_authority: bool = field(default=False, init=False)
    metric_ready: bool = field(default=False, init=False)
    free_authority: bool = field(default=False, init=False)

    @property
    def rgb_source_key(self):
        return self.rgb_stamp.key

    @property
    def source(self):
        return (self.rgb_source_key, self.nn_source_key, self.calibration_id,
                self.nn_payload_sha256, self.parser_config_sha256)

    @property
    def captured_us(self):
        return self.rgb_stamp.captured_us

    @property
    def available_us(self):
        return max(self.rgb_stamp.available_us, self.nn_received_j_us)

    def descriptor(self):
        def point(p):
            return dict(kind=p.kind, cell_index=p.cell_index, source_pixel_xy=p.source_pixel_xy,
                        lower=p.bounds.lower, upper=p.bounds.upper, qualified=p.bounds.qualified)
        return dict(rgb_source_key=self.rgb_source_key, nn_source_key=self.nn_source_key,
            calibration_id=self.calibration_id, input_kind=self.input_kind, captured_us=self.captured_us,
            rgb_available_us=self.rgb_stamp.available_us, nn_received_j_us=self.nn_received_j_us,
            nn_sequence=self.nn_sequence, nn_device_us=self.nn_device_us, nn_sdk_us=self.nn_sdk_us,
            nn_context_key=self.nn_context_key, nn_payload_sha256=self.nn_payload_sha256,
            parser_id=self.parser_id, parser_config_sha256=self.parser_config_sha256,
            preprocessing_id=self.preprocessing_id, geometry_qualified=self.geometry_qualified,
            qualification='MODEL_CANDIDATE_ONLY',
            sampling_complete=self.sampling_complete, projection_complete=self.projection_complete,
            projection_scope='selected_cells_only',
            counts=dict(self.counts), road_points=[point(p) for p in self.road_points],
            lane_points=[point(p) for p in self.lane_points], unprojected=list(self.unprojected),
            detections_source=[d.descriptor() for d in self.detections_source],
            live_authority=False, metric_ready=False, free_authority=False)


def _mask(np, candidate):
    value = candidate.source
    if (not isinstance(value, np.ndarray) or value.shape != (360, 640) or value.dtype != np.uint8
            or np.any(value > 1)):
        raise ValueError('YOLOP source mask must be a binary uint8 640x360 grid')
    base = value[::2, ::2]
    if any(not np.array_equal(base, value[y::2, x::2]) for y in (0, 1) for x in (0, 1)):
        raise ValueError('YOLOP source mask differs from fixed 2x2 preprocessing')
    # 외부 candidate가 수동 생성됐더라도 결과 layer가 mutable scratch를 붙잡지 않는다.
    return np.frombuffer(value.tobytes(order='C'), dtype=np.uint8).reshape(360, 640)


def build_neural_layer(observation, calibration, max_points=MAX_PLANE_POINTS):
    """원 RGB parent에 결박된 NN만 받는다. source/SDK parent 대조는 adapter가 먼저 수행한다.

    원 source mask의 2×2 복제를 한 NN cell로 표본화한다. 표본 수와 bound 실패를
    별도로 보고하며 미표본 mask 영역을 비도로·빈 공간으로 바꾸지 않는다.
    """
    if (type(observation) is not PerceptionInput or type(calibration) is not CalibrationProfile
            or observation.calibration_id != calibration.sha256):
        raise ValueError('neural input differs from its calibration')
    if type(max_points) is not int or not 1 <= max_points <= MAX_PLANE_POINTS:
        raise ValueError('neural plane point cap must be in 1..128')
    candidates = observation.nn
    if (type(candidates) is not YolopCandidates or observation.nn_key is None
            or candidates.preprocessing_id != PREPROCESSING_ID
            or candidates.packet.sequence != observation.rgb.stamp.sequence):
        raise ValueError('neural candidates have no matching original RGB parent')
    if any(not (type(v) is int and 0 <= v < 2**64 or type(v) is str and 1 <= len(v) <= 128
                and all(33 <= ord(c) <= 126 for c in v)) for v in observation.nn_key):
        raise ValueError('NN source key must preserve bounded immutable scalars')
    if ((observation.rgb.width, observation.rgb.height) != (640, 360)
            or (calibration.rgb.width, calibration.rgb.height) != (640, 360)):
        raise ValueError('fixed YOLOP source plane requires RGB640x360')
    import numpy as np
    from .geometry import project_ground_bounds_many

    road, lane = _mask(np, candidates.road), _mask(np, candidates.lane)
    positive = [np.flatnonzero(mask[::2, ::2]) for mask in (road, lane)]
    budgets = [min(len(indices), max_points // 2) for indices in positive]
    left = max_points - sum(budgets)
    for i in (0, 1):
        extra = min(left, len(positive[i]) - budgets[i])
        budgets[i] += extra
        left -= extra
    selected = []
    for kind, indices, budget in zip(('road', 'lane'), positive, budgets):
        if budget:
            chosen = indices[(np.arange(budget, dtype=np.int64) * len(indices)) // budget]
            selected.extend((kind, int(index), (2 * int(index % 320) + .5, 2 * int(index // 320) + .5))
                            for index in chosen)
    # 수치 plane box와 모델의 물리 의미 수락은 다르다. NN key의 clock도 opaque 원값이다.
    # 현재 모델 plane 후보에는 실물 측정/시각 자격을 발급하지 않는다.
    qualified = False
    projected, unprojected = [], []
    if selected:
        pixels = np.asarray([point[2] for point in selected], dtype=np.float64)
        lower, upper, valid = project_ground_bounds_many(pixels[:, 0], pixels[:, 1],
            calibration.rgb, calibration.plane, pixel_error=calibration.rgb.pixel_error + 1.0)
        for i, (kind, index, pixel) in enumerate(selected):
            if valid[i]:
                projected.append(NeuralPlanePoint(kind, index, pixel,
                    PointBounds(tuple(float(v) for v in lower[i]), tuple(float(v) for v in upper[i]), qualified)))
            else:
                unprojected.append((kind, index, pixel))
    counts = dict(road_positive_cells=len(positive[0]), lane_positive_cells=len(positive[1]),
        road_source_positive_pixels=4 * len(positive[0]), lane_source_positive_pixels=4 * len(positive[1]),
        selected_cells=len(selected), projected_cells=len(projected), unprojected_cells=len(unprojected),
        omitted_at_cap=sum(map(len, positive)) - len(selected), max_points=max_points,
        source_detections=len(candidates.detections_source),
        retained_detections=min(MAX_DETECTIONS, len(candidates.detections_source)),
        omitted_detections=max(0, len(candidates.detections_source) - MAX_DETECTIONS))
    packet = candidates.packet
    return NeuralLayer(observation.rgb.stamp, observation.nn_key, calibration.sha256, observation.kind,
        packet.sequence, packet.device_us, packet.sdk_us, packet.received_j_us, packet.context_key,
        hashlib.sha256(packet.payload).hexdigest(), candidates.parser_id, candidates.config_sha256,
        candidates.preprocessing_id, tuple(p for p in projected if p.kind == 'road'),
        tuple(p for p in projected if p.kind == 'lane'), tuple(unprojected), candidates.detections_source[:MAX_DETECTIONS],
        counts, qualified, counts['omitted_at_cap'] == 0, len(unprojected) == 0, road, lane)


def assist_road(road, layer, calibration):
    """최대64개 기하 가설에 모델 support만 붙인다. 삭제·유일 판정·기하 상태 변경은 없다."""
    if type(road) is not RoadResult or type(layer) is not NeuralLayer or type(calibration) is not CalibrationProfile:
        raise ValueError('road assistance requires exact road/neural/calibration values')
    if (road.source_key is None or road.source_key != layer.rgb_source_key
            or layer.calibration_id != calibration.sha256):
        return road
    if (type(road.hypotheses) is not tuple or len(road.hypotheses) > 4096
            or any(type(h) is not LaneHypothesis or type(h.local_id) is not int or h.local_id < 0
                   for h in road.hypotheses)
            or len({h.local_id for h in road.hypotheses}) != len(road.hypotheses)):
        raise ValueError('road assistance requires a bounded frame-local hypothesis set')
    import numpy as np
    from .geometry import ground_to_pixels

    ordered = sorted(road.hypotheses, key=lambda h: h.local_id)
    selected = ordered[:MAX_SCORED_HYPOTHESES]
    requests, ranges = [], []
    for hypothesis in selected:
        count = len(hypothesis.station_s)
        paths = (hypothesis.left_center, hypothesis.right_center, hypothesis.left_outer,
                 hypothesis.divider, hypothesis.right_outer)
        if (not 2 <= count <= 32 or any(type(path) is not tuple or len(path) != count
                or any(type(p) is not tuple or len(p) != 2 or any(type(v) not in (int, float)
                       or not math.isfinite(v) for v in p) for p in path) for path in paths)):
            raise ValueError('model scoring exceeds common-station geometry bounds')
        start = len(requests)
        requests.extend(hypothesis.left_center + hypothesis.right_center)
        middle = len(requests)
        requests.extend(hypothesis.left_outer + hypothesis.divider + hypothesis.right_outer)
        ranges.append((start, middle, len(requests)))
    labels = np.zeros((len(requests),), dtype=np.uint8)
    observed = np.zeros((len(requests),), dtype=np.bool_)
    if requests:
        coordinates = np.asarray(requests, dtype=np.float64)
        u, v, valid = ground_to_pixels(coordinates[:, 0], coordinates[:, 1], calibration.rgb, calibration.plane)
        ix, iy = np.rint(u).astype(np.int64), np.rint(v).astype(np.int64)
        observed = valid & (ix >= 0) & (ix < 640) & (iy >= 0) & (iy < 360)
        for start, middle, end in ranges:
            for a, b, mask in ((start, middle, layer.road_mask), (middle, end, layer.lane_mask)):
                indices = np.flatnonzero(observed[a:b]) + a
                labels[indices] = mask[iy[indices], ix[indices]]
    supported = []
    for hypothesis, (start, middle, end) in zip(selected, ranges):
        road_count, lane_count = int(np.count_nonzero(observed[start:middle])), int(np.count_nonzero(observed[middle:end]))
        road_fraction = None if road_count == 0 else float(np.sum(labels[start:middle])) / road_count
        lane_fraction = None if lane_count == 0 else float(np.sum(labels[middle:end])) / lane_count
        fractions = tuple(v for v in (road_fraction, lane_fraction) if v is not None)
        support = SemanticSupport(road_fraction, lane_fraction,
            None if not fractions else sum(fractions) / len(fractions), road_count, lane_count, end - start)
        supported.append(replace(hypothesis, semantic_support=support, semantic_source=layer.source))
    supported.sort(key=lambda h: (-(h.semantic_support.combined_fraction
        if h.semantic_support.combined_fraction is not None else -1.), h.local_id))
    unscored = tuple(replace(h, semantic_support=None, semantic_source=layer.source)
                     for h in ordered[MAX_SCORED_HYPOTHESES:])
    return replace(road, hypotheses=tuple(supported) + unscored, semantic_scored=len(selected),
                   semantic_total=len(ordered), semantic_complete=len(selected) == len(ordered),
                   semantic_source=layer.source)
