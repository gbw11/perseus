"""고정 YOLOP raw tensor를 차량·road/lane 후보로 해석한다. 제어/metric은 만들지 않는다.

근거 utils SHA256 7e15e612b8f32d0140346d23f6c3a726dbbec775e3cbeb63053245e483265bd4:
FP16→FP32, strict objectness/product, xywh 변환, 두 channel argmax를 따른다.
NMS의 FP32 연산·binary64 threshold 비교·row tie, 무효 box 제외, [0,1] score
정의역 거절은 프로젝트 정책이다. Torchvision kernel parity를 주장하지 않는다.
원 예제의 10초 wall-time cutoff 대신 유한 비교 상한을 쓰며 반환 기한/15Hz를 보장하지 않는다.
NumPy는 호출 시에만 import하며 SDK·OpenCV·모델 파일·운영 권한을 열지 않는다.
"""

from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any

from ..oak.yolop_frames import LAYER_SHAPES, NNPacket


PARSER_ID = "yolop-320-rvc2-postprocess-v1"
REFERENCE_UTILS_SHA256 = "7e15e612b8f32d0140346d23f6c3a726dbbec775e3cbeb63053245e483265bd4"
PREPROCESSING_ID = "yolop-letterbox-640x360-to320-v1"


@dataclass(frozen=True)
class YolopPostprocessConfig:
    """임시 시험 threshold. binary64 원값을 보존하며 정확도 수락값은 아니다."""

    confidence: float = 0.5
    iou_threshold: float = 0.3
    max_det: int = 500

    def __post_init__(self):
        for name in ("confidence", "iou_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite Python float in [0,1]")
            object.__setattr__(self, name, float(value))
        if type(self.max_det) is not int or not 1 <= self.max_det <= 500:
            raise ValueError("max_det must be an integer in 1..500")

    def to_dict(self) -> dict:
        return {"confidence": self.confidence, "iou_threshold": self.iou_threshold, "max_det": self.max_det}

    @property
    def sha256(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class DetectionCandidate:
    """원 row와 FP32 score, 연속 image-edge xyxy. class0는 차량 후보에 한정한다."""

    row: int
    class_id: int
    score: float
    xyxy: tuple[float, float, float, float]

    def descriptor(self) -> dict:
        return {"row": self.row, "class_id": self.class_id, "score": self.score, "xyxy": list(self.xyxy)}


@dataclass(frozen=True)
class MaskCandidates:
    """원 argmax와 관측 ROI. padding의 0/1 label은 관측·FREE 판정이 아니다.

    source[y,x]=argmax_nn[70+y//2,x//2]로 정확히 2×2 복제한다. content는
    원 bytes의 연속 view이며 owner_bytes가 모든 출력의 읽기 전용 수명을 보존한다.
    """

    argmax_nn: Any
    observed_nn: Any
    content: Any
    source: Any
    owner_bytes: tuple[bytes, bytes, bytes]


@dataclass(frozen=True)
class YolopCandidates:
    """SDK 수명과 독립된 원 packet 및 후보. parser 준비·성능·주행 허가가 아니다."""

    packet: NNPacket
    layers: Any
    detections_nn: tuple[DetectionCandidate, ...]
    detections_source: tuple[DetectionCandidate, ...]
    road: MaskCandidates
    lane: MaskCandidates
    counts: dict
    config: YolopPostprocessConfig
    parser_id: str = field(default=PARSER_ID, init=False)
    reference_utils_sha256: str = field(default=REFERENCE_UTILS_SHA256, init=False)
    preprocessing_id: str = field(default=PREPROCESSING_ID, init=False)
    live_authority: bool = field(default=False, init=False)
    nn_ready: bool = field(default=False, init=False)
    metric_ready: bool = field(default=False, init=False)

    @property
    def config_sha256(self) -> str:
        return self.config.sha256


def _layers(np, packet):
    result = {}
    for tensor in packet.tensors:
        view = np.ndarray(tensor.dims, dtype="<f2", buffer=packet.payload,
                          offset=tensor.offset, strides=tensor.strides)
        # 고정 NNPacket은 실제 logical shape 또는 고정 serializer의 flat stride2만
        # 허용한다. compiler dims/빈 stride에서 logical layout을 추측하지 않는다.
        logical = view.reshape(LAYER_SHAPES[tensor.name])
        if not logical.flags.c_contiguous or logical.flags.writeable:
            raise ValueError("YOLOP layer is not a readonly packed runtime view")
        result[tensor.name] = logical
    return result


def _score_domain(np, layers):
    heads = {"det_out obj/class": layers["det_out"][..., 4:6],
             "drive_area_seg": layers["drive_area_seg"], "lane_line_seg": layers["lane_line_seg"]}
    for name, values in heads.items():
        if np.any(values < np.float16(0)) or np.any(values > np.float16(1)):
            # 고정 모델의 sigmoid score 정의역 밖은 원 reference parity의 범위 밖이다.
            # 낮은 objectness 행도 예외 없이 거절하며 clamp/sigmoid로 수리하지 않는다.
            raise ValueError(f"YOLOP score-domain violation outside [0,1]: {name}")


def _iou(np, best, others, area_best, areas):
    left_top = np.maximum(best[:2], others[:, :2])
    right_bottom = np.minimum(best[2:], others[:, 2:])
    extent = np.maximum(np.subtract(right_bottom, left_top, dtype=np.float32), np.float32(0))
    intersection = np.multiply(extent[:, 0], extent[:, 1], dtype=np.float32)
    union = np.subtract(np.add(area_best, areas, dtype=np.float32), intersection, dtype=np.float32)
    return np.divide(intersection, union, out=np.zeros_like(intersection), where=union > np.float32(0),
                     dtype=np.float32)


def _detections(np, layer, config):
    values = layer[0].astype(np.float32)
    rows = np.flatnonzero(values[:, 4].astype(np.float64) > config.confidence)
    objectness_pass = len(rows)
    scores = np.multiply(values[rows, 4], values[rows, 5], dtype=np.float32)
    passed = scores.astype(np.float64) > config.confidence
    rows, scores = rows[passed], scores[passed]
    confidence_pass = len(rows)
    selected = values[rows, :4]
    positive = (selected[:, 2] > np.float32(0)) & (selected[:, 3] > np.float32(0))
    invalid_xywh = int(len(rows) - np.count_nonzero(positive))
    rows, scores, selected = rows[positive], scores[positive], selected[positive]
    half = np.divide(selected[:, 2:4], np.float32(2), dtype=np.float32)
    boxes = np.concatenate((np.subtract(selected[:, :2], half, dtype=np.float32),
                            np.add(selected[:, :2], half, dtype=np.float32)), axis=1)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    invalid_xyxy = int(len(rows) - np.count_nonzero(valid))
    rows, scores, boxes = rows[valid], scores[valid], boxes[valid]
    size = np.subtract(boxes[:, 2:], boxes[:, :2], dtype=np.float32)
    areas = np.multiply(size[:, 0], size[:, 1], dtype=np.float32)
    pending = np.lexsort((rows, -scores))
    kept, suppressed, pairwise, unclassified = [], 0, 0, 0
    while len(pending):
        best = int(pending[0])
        kept.append(best)
        remaining = pending[1:]
        if len(kept) >= config.max_det:
            unclassified = len(remaining)
            break
        if not len(remaining):
            break
        overlaps = _iou(np, boxes[best], boxes[remaining], areas[best], areas[remaining])
        pairwise += len(remaining)
        # FP32 내부 계산 이후 binary64로 비교해 NumPy 1/2의 scalar 승격 차이를 없앤다.
        remove = overlaps.astype(np.float64) > config.iou_threshold
        suppressed += int(np.count_nonzero(remove))
        pending = remaining[~remove]
    detections = tuple(DetectionCandidate(int(rows[index]), 0, float(scores[index]),
                                          tuple(float(value) for value in boxes[index])) for index in kept)
    counts = {"input_rows": 6300, "objectness_pass": objectness_pass, "confidence_pass": confidence_pass,
              "invalid_nonpositive_xywh": invalid_xywh, "invalid_fp32_xyxy": invalid_xyxy,
              "nms_candidates": len(rows), "nms_kept": len(kept), "nms_suppressed": suppressed,
              "nms_unclassified_at_cap": unclassified, "nms_pairwise_tests": pairwise,
              "nms_pairwise_bound": config.max_det * 6300, "nms_input_pre_topk": False}
    return detections, counts


def _source_detection(detection):
    left, top, right, bottom = detection.xyxy
    left, top, right, bottom = max(0.0, left), max(70.0, top), min(320.0, right), min(250.0, bottom)
    if left >= right or top >= bottom:
        return None
    return DetectionCandidate(detection.row, detection.class_id, detection.score,
                              (left * 2, (top - 70) * 2, right * 2, (bottom - 70) * 2))


def _mask(np, layer):
    nn_bytes = np.argmax(layer, axis=1)[0].astype(np.uint8).tobytes(order="C")
    raw = np.frombuffer(nn_bytes, dtype=np.uint8).reshape(320, 320)
    observed = np.zeros((320, 320), dtype=np.bool_)
    observed[70:250] = True
    observed_bytes = observed.tobytes(order="C")
    observed = np.frombuffer(observed_bytes, dtype=np.bool_).reshape(320, 320)
    content = raw[70:250]
    source_bytes = np.repeat(np.repeat(content, 2, axis=0), 2, axis=1).tobytes(order="C")
    source = np.frombuffer(source_bytes, dtype=np.uint8).reshape(360, 640)
    return MaskCandidates(raw, observed, content, source, (nn_bytes, observed_bytes, source_bytes))


def decode_yolop(packet: NNPacket, config: YolopPostprocessConfig = YolopPostprocessConfig()) -> YolopCandidates:
    """실제 runtime tensor만 읽는다. score-domain 위반은 전체 실패, 무효 box는 계수해 제외한다.

    모든 6300행을 비교하며 pre-topK하지 않는다. NMS prefix 최대500개 뒤 중단해
    pairwise≤max_det×6300을 지키고 미분류 잔여를 보고한다. full NN NMS 후 ROI를
    clip하므로 padding에만 남은 검출은 source 후보가 없다. score/mask는 후보다.
    """
    if not isinstance(packet, NNPacket) or not isinstance(config, YolopPostprocessConfig):
        raise TypeError("decode_yolop requires NNPacket and YolopPostprocessConfig")
    packet.validate()
    import numpy as np

    layers = _layers(np, packet)
    _score_domain(np, layers)
    detections, counts = _detections(np, layers["det_out"], config)
    source = tuple(candidate for detection in detections
                   if (candidate := _source_detection(detection)) is not None)
    counts.update(source_detections=len(source), source_unobserved_after_nms=len(detections) - len(source))
    return YolopCandidates(packet, MappingProxyType(layers), detections, source,
                            _mask(np, layers["drive_area_seg"]), _mask(np, layers["lane_line_seg"]),
                            counts, config)
