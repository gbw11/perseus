"""실물·재생·합성 관측의 공통 값. SDK 시계나 GT를 서로 대신하지 않는다.

stream별 원 key로 geometry와 늦은 NN을 따로 식별한다. captured_us=None은
수신 실패가 아니라 수락한 capture interval이 없다는 뜻이며 현재 시각으로 채우지 않는다.
배열 변환·보정·odom·지도·이동 허가는 이 모듈의 책임이 아니다.
"""

from dataclasses import dataclass
import hashlib


U64_MAX = 2**64 - 1


def _uint(value, name):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ValueError(name + ' must be a uint64')


def _text(value, name):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or any(not 33 <= ord(c) <= 126 for c in value)):
        raise ValueError(name + ' must be bounded printable ASCII without spaces')


def _sha(value):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('expected lowercase SHA256')


@dataclass(frozen=True)
class FrameStamp:
    """원 stream 순번과 clock domain의 capture 구간(us). available_us는 관측시각이 아니다."""

    source_id: str
    stream_id: str
    sequence: int
    clock_id: str
    captured_us: tuple[int, int] | None
    available_us: int

    def __post_init__(self):
        for name in ('source_id', 'stream_id', 'clock_id'):
            _text(getattr(self, name), name)
        _uint(self.sequence, 'sequence')
        _uint(self.available_us, 'available_us')
        if self.captured_us is not None:
            if type(self.captured_us) is not tuple or len(self.captured_us) != 2:
                raise ValueError('capture interval must be an exact pair or None')
            for value in self.captured_us:
                _uint(value, 'capture endpoint')
            if not self.captured_us[0] <= self.captured_us[1] <= self.available_us:
                raise ValueError('capture interval must precede availability')

    @property
    def key(self):
        return self.source_id, self.stream_id, self.clock_id, self.sequence

    def age_us(self, now_us, clock_id):
        """가장 오래된 capture 끝점으로 보수 age를 구한다. 다른 clock/미수락이면 None이다."""
        _uint(now_us, 'now_us')
        if clock_id != self.clock_id or self.captured_us is None or now_us < self.captured_us[1]:
            return None
        return now_us - self.captured_us[0]


@dataclass(frozen=True)
class ImageFrame:
    """소유한 packed bytes. NV12/BGR8/RIGHT axial-Z RAW16_LE를 명시한다."""

    stamp: FrameStamp
    width: int
    height: int
    encoding: str
    payload: bytes
    exposure_us: int | None = None

    def __post_init__(self):
        if type(self.stamp) is not FrameStamp:
            raise ValueError('frame requires a FrameStamp')
        if (type(self.width) is not int or type(self.height) is not int
                or not 1 <= self.width <= 1280 or not 1 <= self.height <= 720):
            raise ValueError('frame dimensions exceed the bounded input profile')
        if self.encoding == 'NV12':
            if self.width % 2 or self.height % 2:
                raise ValueError('NV12 requires even dimensions')
            size = self.width * self.height * 3 // 2
        elif self.encoding == 'BGR8':
            size = self.width * self.height * 3
        elif self.encoding == 'RAW16_LE':
            size = self.width * self.height * 2
        else:
            raise ValueError('unsupported image encoding')
        if type(self.payload) is not bytes or len(self.payload) != size:
            raise ValueError('packed image payload length mismatch')
        if self.exposure_us is not None:
            _uint(self.exposure_us, 'exposure_us')
            if self.exposure_us > 1_000_000:
                raise ValueError('exposure exceeds the input bound')

    @property
    def sha256(self):
        return hashlib.sha256(self.payload).hexdigest()

    def array(self):
        """원 bytes를 유지하는 readonly view. NV12만 새 BGR 배열을 반환한다."""
        import numpy as np
        if self.encoding == 'RAW16_LE':
            return np.frombuffer(self.payload, dtype='<u2').reshape(self.height, self.width)
        if self.encoding == 'BGR8':
            return np.frombuffer(self.payload, dtype=np.uint8).reshape(self.height, self.width, 3)
        import cv2
        packed = np.frombuffer(self.payload, dtype=np.uint8).reshape(self.height * 3 // 2, self.width)
        output = cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_NV12)
        output.setflags(write=False)
        return output


@dataclass(frozen=True)
class PerceptionInput:
    """한 capture의 관측. nn_key는 RGB/depth와 따로 중복 제거하며 GT pose는 담지 않는다."""

    kind: str
    calibration_id: str
    rgb: ImageFrame
    depth: ImageFrame | None = None
    nn: object = None
    nn_key: tuple | None = None

    def __post_init__(self):
        if self.kind not in ('LIVE', 'REPLAY', 'SYNTHETIC'):
            raise ValueError('input kind must be explicit')
        _sha(self.calibration_id)
        if type(self.rgb) is not ImageFrame or self.rgb.encoding not in ('NV12', 'BGR8'):
            raise ValueError('RGB requires a color ImageFrame')
        if self.depth is not None:
            if type(self.depth) is not ImageFrame or self.depth.encoding != 'RAW16_LE':
                raise ValueError('depth requires a native axial-Z ImageFrame')
            if (self.depth.stamp.source_id != self.rgb.stamp.source_id
                    or self.depth.stamp.clock_id != self.rgb.stamp.clock_id):
                raise ValueError('mixed acquisition or clock domains')
        if (self.nn is None) != (self.nn_key is None):
            raise ValueError('NN candidate and original key must be supplied together')
        if self.nn_key is not None and (type(self.nn_key) is not tuple or not 1 <= len(self.nn_key) <= 8):
            raise ValueError('NN key must be bounded')

    @property
    def geometry_key(self):
        return self.calibration_id, self.rgb.stamp.key, None if self.depth is None else self.depth.stamp.key

    @property
    def capture_interval(self):
        """관련 sensor의 실제 capture 구간 전체 hull. 하나라도 미수락이면 None이다."""
        stamps=(self.rgb.stamp,) if self.depth is None else (self.rgb.stamp,self.depth.stamp)
        if any(s.captured_us is None for s in stamps):
            return None
        return min(s.captured_us[0] for s in stamps),max(s.captured_us[1] for s in stamps)

    @property
    def live_authority(self):
        return False
