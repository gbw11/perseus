"""YOLOP SDK 출력의 소유 bytes와 runtime tensor 계약을 검사한다.

DepthAI 2.29 NNData.cpp의 getTensorDataSize/getLayerFp16는 dims[0]*strides[0]
bytes를 offset부터 연속 읽는다. BlobReader의 compiler descriptor와 별개다.
SDK·NumPy를 import하지 않으며 빈/비연속 runtime stride는 추정하지 않는다.
고정 근거: depthai-core d6a37a5ba8ba2ee187fabc9208b813db5f75d4a7,
depthai-python 84daffedca6a9526017a5a8e5d5392347e0b022b.
"""

from dataclasses import dataclass, field
from datetime import timedelta
import json
import math
import operator
import time


MAX_NN_BYTES = 1_048_576
LAYER_SHAPES = {
    "det_out": (1, 6300, 6),
    "drive_area_seg": (1, 2, 320, 320),
    "lane_line_seg": (1, 2, 320, 320),
}
VALIDATION_ELEMENTS = sum(math.prod(shape) for shape in LAYER_SHAPES.values())
# FP16LE exponent의 5bit는 high byte의 bit2..6에 있다. 값별 Python
# 순회를 피하고 bytes의 C 연산으로 전체 활성값을 검사한다.
_FP16_NONFINITE_HIGH_BYTE = bytes(1 if value & 0x7C == 0x7C else 0 for value in range(256))
VALIDATION_WORKSPACE_BYTES = max(math.prod(shape) for shape in LAYER_SHAPES.values()) * 2


def _contains_nonfinite_fp16_le(payload: bytes, offset: int, byte_length: int) -> bool:
    """검증된 packed FP16LE 구간의 NaN/Inf. 최대 임시 bytes 두 개만 만든다."""
    high = payload[offset + 1:offset + byte_length:2]
    return high.translate(_FP16_NONFINITE_HIGH_BYTE).find(b"\x01") != -1


def _u64(value: int, name: str) -> None:
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError(f"invalid {name}")


def _context(value: str) -> None:
    if (type(value) is not str or not 1 <= len(value) <= 256
            or any(not 33 <= ord(c) <= 126 for c in value)):
        raise ValueError("invalid source context key")


def _stamps(item) -> None:
    _context(item.context_key)
    for name in ("sequence", "device_us", "sdk_us", "received_j_us"):
        _u64(getattr(item, name), name)


def _us(value: timedelta) -> int:
    if not isinstance(value, timedelta):
        raise ValueError("SDK timestamp must be timedelta")
    return (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds


def _enum_code(value, name: str) -> int:
    if type(value) is bool:
        raise ValueError(f"invalid {name}")
    try:
        code = operator.index(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"invalid {name}") from error
    if not 0 <= code < 2**32:
        raise ValueError(f"invalid {name}")
    return code


def _golden_copy(value):
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("golden diagnostic must be a dict or None")
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(encoded) > 8192:
        raise ValueError("golden diagnostic exceeds 8192 bytes")
    return json.loads(encoded)


@dataclass(frozen=True)
class RuntimeTensor:
    """원 getRaw().tensors의 metadata. compiler tensor를 대신 넣지 않는다."""

    name: str
    data_type_code: int
    order_code: int
    num_dimensions: int
    dims: tuple[int, ...]
    strides: tuple[int, ...]
    offset: int
    data_type_text: str = "DataType.FP16"
    order_text: str = ""

    def __post_init__(self) -> None:
        if type(self.name) is not str or not 1 <= len(self.name) <= 64:
            raise ValueError("invalid runtime tensor name")
        if type(self.data_type_code) is not int or self.data_type_code != 0:
            raise ValueError("YOLOP runtime tensor must be FP16")
        if type(self.order_code) is not int or self.order_code not in (0x3, 0x321, 0x4321):
            raise ValueError("unsupported runtime tensor order")
        if type(self.num_dimensions) is not int or not 1 <= self.num_dimensions <= 4:
            raise ValueError("invalid runtime tensor rank")
        if (type(self.dims) is not tuple or type(self.strides) is not tuple
                or len(self.dims) != self.num_dimensions):
            raise ValueError("runtime tensor dimensions/rank mismatch")
        if not self.strides:
            raise ValueError("empty runtime tensor strides; compiler fallback is forbidden")
        if len(self.strides) != self.num_dimensions:
            raise ValueError("runtime tensor strides/rank mismatch")
        if (any(type(v) is not int or not 0 < v <= MAX_NN_BYTES for v in self.dims)
                or any(type(v) is not int or not 0 < v <= MAX_NN_BYTES for v in self.strides)):
            raise ValueError("invalid runtime tensor dimensions/strides")
        if math.prod(self.dims) * 2 > MAX_NN_BYTES:
            raise ValueError("runtime tensor exceeds payload bound")
        _u64(self.offset, "tensor offset")
        if self.offset % 2:
            raise ValueError("unaligned FP16 tensor offset")
        if (type(self.data_type_text) is not str or type(self.order_text) is not str
                or len(self.data_type_text) > 64 or len(self.order_text) > 64):
            raise ValueError("invalid enum metadata text")

    @property
    def byte_length(self) -> int:
        return math.prod(self.dims) * 2

    def descriptor(self) -> dict:
        return {"name": self.name, "dataType": self.data_type_text,
                "data_type_code": self.data_type_code, "order": self.order_text,
                "order_code": self.order_code, "numDimensions": self.num_dimensions,
                "dims": list(self.dims), "strides": list(self.strides),
                "offset": self.offset, "byte_length": self.byte_length,
                "layout_origin": "runtime_NNData_getRaw_tensors"}


def _layouts(packet) -> None:
    if type(packet.payload) is not bytes or not 0 < len(packet.payload) <= MAX_NN_BYTES:
        raise ValueError("NN payload must be owned bytes within 1MiB")
    if (type(packet.tensors) is not tuple or len(packet.tensors) != 3
            or any(not isinstance(t, RuntimeTensor) for t in packet.tensors)):
        raise ValueError("YOLOP requires exactly three runtime tensors")
    if {tensor.name for tensor in packet.tensors} != set(LAYER_SHAPES):
        raise ValueError("YOLOP runtime layer names are missing/duplicate/unknown")
    regions = []
    for tensor in packet.tensors:
        shape = LAYER_SHAPES[tensor.name]
        # setLayer의 고정 SDK serializer는 rank1/stride2와 기본 order를 사용한다.
        # 다차원형은 원 NNData outer-first dims/packed stride가 있어야 한다.
        if tensor.dims not in (shape, (math.prod(shape),)):
            raise ValueError(f"unexpected runtime dimensions for {tensor.name}: {tensor.dims}")
        if tensor.num_dimensions > 1:
            expected_order = 0x321 if len(shape) == 3 else 0x4321
            if tensor.order_code != expected_order:
                raise ValueError(f"runtime storage order differs for {tensor.name}")
        expected = []
        stride = 2
        for dimension in reversed(tensor.dims):
            expected.append(stride)
            stride *= dimension
        expected = tuple(reversed(expected))
        if tensor.strides != expected:
            raise ValueError(f"non-packed runtime strides for {tensor.name}: {tensor.strides}")
        if tensor.dims[0] * tensor.strides[0] != tensor.byte_length:
            raise ValueError("runtime layout disagrees with the fixed SDK accessor")
        end = tensor.offset + tensor.byte_length
        if end > len(packet.payload):
            raise ValueError(f"truncated runtime tensor: {tensor.name}")
        regions.append((tensor.offset, end, tensor.name))
    regions.sort()
    if any(left[1] > right[0] for left, right in zip(regions, regions[1:])):
        raise ValueError("overlapping runtime tensor regions")


@dataclass(frozen=True)
class NNPacket:
    """SDK view를 보유하지 않는 한 NN output. 생성 시 활성 FP16 전체를 검사한다."""

    stream: str
    sequence: int
    device_us: int
    sdk_us: int
    received_j_us: int
    payload: bytes
    tensors: tuple[RuntimeTensor, ...]
    context_key: str
    golden: dict | None = None
    validation_elapsed_us: int = field(init=False)
    validation_elements: int = field(init=False)

    def __post_init__(self) -> None:
        started = time.perf_counter_ns()
        if self.stream != "nn_output":
            raise ValueError("invalid NN output stream")
        _stamps(self)
        _layouts(self)
        count = 0
        for tensor in self.tensors:
            if _contains_nonfinite_fp16_le(self.payload, tensor.offset, tensor.byte_length):
                raise ValueError(f"non-finite FP16 value in {tensor.name}")
            count += tensor.byte_length // 2
        object.__setattr__(self, "validation_elapsed_us", (time.perf_counter_ns() - started) // 1000)
        object.__setattr__(self, "validation_elements", count)
        object.__setattr__(self, "golden", _golden_copy(self.golden))

    def validate(self) -> None:
        """불변 packet의 구조만 재확인한다. FP16은 생성 시 한 번 검사한 근거를 쓴다."""
        _stamps(self)
        _layouts(self)
        if self.validation_elements != VALIDATION_ELEMENTS:
            raise ValueError("missing complete FP16 validation")

    def layer_bytes(self, name: str) -> bytes:
        """검증한 runtime offset에서 해당 layer의 소유 bytes 사본을 반환한다."""
        for tensor in self.tensors:
            if tensor.name == name:
                return self.payload[tensor.offset:tensor.offset + tensor.byte_length]
        raise KeyError(name)

    def descriptor(self) -> dict:
        self.validate()
        return {"stream": self.stream, "context_key": self.context_key,
                "sequence": str(self.sequence), "device_timestamp_us": str(self.device_us),
                "sdk_timestamp_us": str(self.sdk_us), "received_j_us": str(self.received_j_us),
                "payload_length": len(self.payload), "tensors": [t.descriptor() for t in self.tensors],
                "validation_elapsed_us": self.validation_elapsed_us,
                "validation_elements": self.validation_elements,
                "finite_validation": "all_active_FP16LE_high_byte_translate",
                "validation_workspace_bound_bytes": VALIDATION_WORKSPACE_BYTES,
                "compiler_layout_used": False, "golden": _golden_copy(self.golden),
                "metric_ready": False}


@dataclass(frozen=True)
class NNInputStamp:
    """NN passthrough의 원 입력 metadata. 실제 image payload를 보관하지 않는다."""

    stream: str
    sequence: int
    device_us: int
    sdk_us: int
    received_j_us: int
    width: int
    height: int
    encoding: str
    context_key: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.stream != "nn_input":
            raise ValueError("invalid NN input stream")
        _stamps(self)
        if (type(self.width) is not int or type(self.height) is not int
                or (self.width, self.height, self.encoding) != (320, 320, "BGR888p")):
            raise ValueError("NN input must be 320x320 BGR888p")

    def descriptor(self) -> dict:
        self.validate()
        return {"stream": self.stream, "context_key": self.context_key,
                "sequence": str(self.sequence), "device_timestamp_us": str(self.device_us),
                "sdk_timestamp_us": str(self.sdk_us), "received_j_us": str(self.received_j_us),
                "width": self.width, "height": self.height, "encoding": self.encoding,
                "payload_length": 0, "metadata_only": True,
                "timestamp_basis": "SDK_Buffer_raw_equals_ImgFrame_END_in_2.29"}


def _byte_view(data, limit: int) -> memoryview:
    try:
        view = memoryview(data)
    except TypeError as error:
        raise ValueError("SDK data must expose a bounded uint8 buffer") from error
    if (view.ndim != 1 or view.itemsize != 1 or view.format != "B"
            or not view.c_contiguous or view.nbytes > limit):
        raise ValueError("invalid SDK uint8 buffer shape/type/length")
    return view


def sdk_nn_packet(message, received_j_us: int, context_key: str) -> NNPacket:
    """실제 RawNNData tensor와 buffer를 읽어 SDK 수명과 분리한다."""
    raw = message.getRaw()
    infos = raw.tensors
    if len(infos) != 3:
        raise ValueError("YOLOP requires exactly three runtime tensors")
    if any(len(info.dims) > 4 or len(info.strides) > 4 for info in infos):
        raise ValueError("runtime metadata exceeds the fixed tensor rank bound")
    tensors = tuple(RuntimeTensor(
        name=info.name, data_type_code=_enum_code(info.dataType, "tensor data type"),
        order_code=_enum_code(info.order, "tensor order"), num_dimensions=info.numDimensions,
        dims=tuple(info.dims), strides=tuple(info.strides), offset=info.offset,
        data_type_text=str(info.dataType), order_text=str(info.order)) for info in infos)
    payload = bytes(_byte_view(message.getData(), MAX_NN_BYTES))
    return NNPacket("nn_output", message.getSequenceNum(), _us(message.getTimestampDevice()),
                    _us(message.getTimestamp()), received_j_us, payload, tensors, context_key)


def sdk_nn_input(message, received_j_us: int, context_key: str) -> NNInputStamp:
    """Metadata-only passthrough를 검사한다. no-arg Buffer stamp는 END와 동일하다."""
    if _enum_code(message.getType(), "NN input frame type") != 8:  # fixed BGR888p
        raise ValueError("NN input frame type differs from BGR888p")
    if _byte_view(message.getData(), MAX_NN_BYTES).nbytes != 0:
        raise ValueError("NN passthrough must be metadata-only")
    return NNInputStamp("nn_input", message.getSequenceNum(), _us(message.getTimestampDevice()),
                        _us(message.getTimestamp()), received_j_us, message.getWidth(),
                        message.getHeight(), "BGR888p", context_key)


def validate_nn_packet(packet: NNPacket) -> None:
    packet.validate()
