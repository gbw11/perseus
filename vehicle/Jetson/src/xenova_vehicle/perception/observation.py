"""검증된 선택 재생 표본을 공유 BGR/depth/YOLOP 후보로 변환한다.

S03 파일 검사 이후의 소비 경계다. 원 metadata와 typed 객체의 결박을 다시 검사하며
파일·카메라·모델·통신은 열지 않는다. 운영 LIVE 입력은 별도 계약이 필요하다.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter_ns

from .decoders import DecodedDepth, DecodedRgb, decode_depth, decode_rgb
from .yolop import YolopCandidates, YolopPostprocessConfig, decode_yolop
from ..oak.yolop_frames import NNPacket
from ..recording.yolop_contract import YolopDiagnosticSample, decode_sample, validate_manifest


@dataclass(frozen=True)
class DecodedObservation:
    """한 표본의 공유 결과. 원 객체/bytes를 유지하며 각 결과는 scratch를 공유하지 않는다.

    source_metadata/provenance는 원본과 독립된 사본이다. readonly 배열은 공유 계약이며
    임의 호출자의 메모리 변경을 막는 보안 경계는 아니다. costs_us는 새 decode 비용이다.
    """

    sample: YolopDiagnosticSample
    rgb: DecodedRgb
    depth: DecodedDepth
    yolop: YolopCandidates
    source_metadata: dict
    provenance: dict
    costs_us: dict
    input_kind: str = field(default="REPLAY", init=False)
    live_authority: bool = field(default=False, init=False)
    nn_ready: bool = field(default=False, init=False)
    metric_ready: bool = field(default=False, init=False)

    @property
    def availability_j_us(self) -> int:
        return self.sample.availability_j_us

    @property
    def depth_time_qualification(self) -> str:
        return self.sample.depth_time_qualification


class YolopDecoder:
    """고정 manifest에 결박한 camera-free decoder. 상태·공유 scratch·자동 캐시는 없다.

    manifest는 S02 원 JSON object이며 내부 사본으로 검증한다. decode는 S03 typed
    sample만 받는다. 원 context/payload/source 결박 오류나 score 정의역 오류면 예외를
    내고 결과를 반환하지 않는다. threshold는 임시 시험 설정이며 주행 수락값이 아니다.
    """

    def __init__(self, manifest: dict, config: YolopPostprocessConfig | None = None):
        self._manifest = validate_manifest(manifest)
        if config is None:
            config = YolopPostprocessConfig()
        if type(config) is not YolopPostprocessConfig:
            raise ValueError("config must be YolopPostprocessConfig")
        self._config = config

    def _bound(self, sample):
        if type(sample) is not YolopDiagnosticSample:
            raise ValueError("decoder requires a YolopDiagnosticSample")
        # metadata/payloads는 dict이므로 frozen dataclass만 신뢰하지 않는다. S03 순수
        # 계약을 재사용해 현재 결박을 검사한다. 파일 hash 검사는 reader 책임이다.
        checked = decode_sample(sample.metadata, sample.payloads, manifest=self._manifest,
                                ordinal=sample.ordinal)
        if (sample.rgb != checked.rgb or sample.depth != checked.depth
                or sample.nn_input != checked.nn_input
                or sample.availability_j_us != checked.availability_j_us):
            raise ValueError("typed source differs from the recorded sample")
        original, expected = sample.nn_output, checked.nn_output
        if type(original) is not NNPacket:
            raise ValueError("typed sample requires an NNPacket")
        # 새 NN 생성 시의 검사 비용은 원 packet과 같을 필요가 없다. 나머지는 모두
        # 같은 원 packet이어야 한다. 검사 비용은 source metadata를 덮어쓰지 않는다.
        names = ("stream", "sequence", "device_us", "sdk_us", "received_j_us", "payload",
                 "tensors", "context_key", "golden", "validation_elements")
        if any(getattr(original, name) != getattr(expected, name) for name in names):
            raise ValueError("typed NN packet differs from the recorded sample")
        return checked.metadata

    def decode(self, sample: YolopDiagnosticSample) -> DecodedObservation:
        """BGR 변환 1회와 native depth/NN 해석을 수행해 원 source/time을 그대로 보존한다."""
        started = perf_counter_ns()
        metadata = self._bound(sample)
        bound_done = perf_counter_ns()
        candidates = decode_yolop(sample.nn_output, self._config)
        nn_done = perf_counter_ns()
        rgb = decode_rgb(sample.rgb)
        rgb_done = perf_counter_ns()
        depth = decode_depth(sample.depth)
        done = perf_counter_ns()
        provenance = {
            "context_key": self._manifest["context_key"],
            "model": deepcopy(self._manifest["model_provenance"]),
            "preprocessing": deepcopy(self._manifest["preprocessing"]),
            "parser_id": candidates.parser_id,
            "reference_utils_sha256": candidates.reference_utils_sha256,
            "config": self._config.to_dict(), "config_sha256": self._config.sha256,
            "pixel_equivalence_verified": False,
        }
        costs = {"binding": (bound_done - started) // 1000, "nn": (nn_done - bound_done) // 1000,
                 "rgb": (rgb_done - nn_done) // 1000, "depth": (done - rgb_done) // 1000,
                 "total": (done - started) // 1000}
        return DecodedObservation(sample, rgb, depth, candidates, metadata, provenance, costs)
