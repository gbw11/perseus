"""고정 video plane의 JPEG bytes를 SDK 수명에서 분리한다.

SOF/SOS/marker 길이와 dimensions를 검사하며 픽셀 디코드는 하지 않는다. 원
sequence/device/SDK/수신 J를 보존하고 capture interval·노출·주행 권위는 만들지 않는다.
"""

from dataclasses import dataclass
from datetime import timedelta


MAX_JPEG_BYTES = 256 * 1024
JPEG_WIDTH, JPEG_HEIGHT = 640, 360


def _uint(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('JPEG metadata requires original unsigned integer')
    return value


def _timestamp(value):
    if not isinstance(value, timedelta):
        raise ValueError('JPEG SDK timestamp must be timedelta')
    return _uint((value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds)


def _dimensions(payload):
    """최대256KiB marker scan이다. Huffman/pixel decode 성공을 대신하지 않는다."""
    if not payload.startswith(b'\xff\xd8') or not payload.endswith(b'\xff\xd9'):
        raise ValueError('JPEG SOI/EOI framing required')
    position, dimensions, components, scans = 2, None, (), 0
    entropy = has_entropy = False
    while position < len(payload):
        if entropy:
            marker_at = payload.find(b'\xff', position)
            if marker_at < 0:
                raise ValueError('truncated JPEG entropy')
            has_entropy |= marker_at > position
            position = marker_at
        if payload[position] != 0xff:
            raise ValueError('JPEG marker required')
        position += 1
        while position < len(payload) and payload[position] == 0xff:
            position += 1
        if position >= len(payload):
            raise ValueError('truncated JPEG marker')
        marker = payload[position]
        position += 1
        if entropy and marker == 0:
            has_entropy = True
            continue
        if entropy and 0xd0 <= marker <= 0xd7:
            continue
        if entropy and not has_entropy:
            raise ValueError('empty JPEG scan')
        entropy = False
        if marker == 0xd9:
            if position != len(payload) or dimensions is None or not scans:
                raise ValueError('incomplete JPEG image')
            return dimensions
        if marker in (0, 1, 0xd8) or 0xd0 <= marker <= 0xd7:
            raise ValueError('invalid standalone JPEG marker')
        if position + 2 > len(payload):
            raise ValueError('truncated JPEG segment length')
        length = int.from_bytes(payload[position:position+2], 'big')
        end = position + length
        if length < 2 or end > len(payload):
            raise ValueError('invalid JPEG segment length')
        if marker in (0xc0, 0xc1, 0xc2):
            if dimensions is not None or length < 8:
                raise ValueError('invalid JPEG frame header')
            count = payload[position+7]
            if payload[position+2] != 8 or count not in (1, 3) or length != 8 + 3 * count:
                raise ValueError('unsupported JPEG frame layout')
            height = int.from_bytes(payload[position+3:position+5], 'big')
            width = int.from_bytes(payload[position+5:position+7], 'big')
            components = tuple(payload[position+8+3*i] for i in range(count))
            if not width or not height or len(set(components)) != count:
                raise ValueError('invalid JPEG frame dimensions/components')
            dimensions = width, height
        elif 0xc0 <= marker <= 0xcf and marker not in (0xc4, 0xc8, 0xcc):
            raise ValueError('unsupported JPEG frame coding')
        elif marker == 0xda:
            if dimensions is None or length < 6:
                raise ValueError('JPEG scan requires an original frame header')
            count = payload[position+2]
            selected = tuple(payload[position+3+2*i] for i in range(count)) if length == 6 + 2 * count else ()
            if not 1 <= count <= len(components) or len(selected) != count or len(set(selected)) != count or any(c not in components for c in selected):
                raise ValueError('invalid JPEG scan components/length')
            scans += 1
            entropy, has_entropy = True, False
        position = end
    raise ValueError('JPEG final marker missing')


@dataclass(frozen=True)
class JpegPacket:
    """SDK 객체/view 없는 불변 압축 packet이다. 이 시각들은 capture 추정값이 아니다."""
    stream: str
    sequence: int
    device_us: int
    sdk_us: int
    received_j_us: int
    width: int
    height: int
    payload: bytes

    def __post_init__(self):
        self.validate()

    def validate(self):
        if type(self.stream) is not str or self.stream != 'jpeg':
            raise ValueError('original JPEG stream required')
        for value in (self.sequence, self.device_us, self.sdk_us, self.received_j_us):
            _uint(value)
        if type(self.width) is not int or type(self.height) is not int or (self.width, self.height) != (JPEG_WIDTH, JPEG_HEIGHT):
            raise ValueError('JPEG requires the fixed 640x360 video plane')
        if type(self.payload) is not bytes or not 4 <= len(self.payload) <= MAX_JPEG_BYTES:
            raise ValueError('owned JPEG bytes exceed the 256KiB bound')
        if _dimensions(self.payload) != (self.width, self.height):
            raise ValueError('original JPEG SOF dimensions differ from the video plane')


def sdk_jpeg(message, received_j_us, *, bitstream_type, exposure_offset, copy_budget_bytes=MAX_JPEG_BYTES):
    """고정 v2 ImgFrame BITSTREAM을 한 번 복사한다. 호출자가 JPEG만 drop한다.

    dimensions는 BITSTREAM의 미확인 width metadata 대신 원 JPEG SOF에서 검산한다.
    timestamp offset은 raw RGB와 같은 명시 END다. 기본 offset과 같다고 가정하지 않는다.
    copy budget은 원 collector 잔여량이며 초과 payload는 복사하기 전에 거절한다.
    """
    _uint(received_j_us)
    if type(copy_budget_bytes) is not int or not 0 <= copy_budget_bytes <= MAX_JPEG_BYTES:
        raise ValueError('bounded original JPEG copy budget required')
    if message.getType() != bitstream_type:
        raise ValueError('JPEG SDK packet must be BITSTREAM')
    if exposure_offset is None:
        raise ValueError('explicit original JPEG timestamp offset required')
    sequence = _uint(message.getSequenceNum())
    device_us = _timestamp(message.getTimestampDevice(exposure_offset))
    sdk_us = _timestamp(message.getTimestamp(exposure_offset))
    view = memoryview(message.getData())
    try:
        if view.ndim != 1 or view.itemsize != 1 or view.format != 'B' or not view.c_contiguous:
            raise ValueError('JPEG SDK payload requires contiguous uint8 bytes')
        if not 4 <= view.nbytes <= min(MAX_JPEG_BYTES, copy_budget_bytes):
            raise ValueError('JPEG SDK payload exceeds original copy budget')
        payload = view.tobytes()
    finally:
        view.release()
    return JpegPacket('jpeg', sequence, device_us, sdk_us, received_j_us, JPEG_WIDTH, JPEG_HEIGHT, payload)
