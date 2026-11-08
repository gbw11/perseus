"""A의 원 RGB metadata와 별도 JPEG 사본을 잇는다. raw arena나 인식 결과를 빌리지 않는다.

동일 sequence/device/SDK END 시각의 RGB에만 연결하며 metadata8개만 보관한다.
JPEG 오류·역압은 이 통로에서 버린다. Device/NN/지도 세대나 원 관측 시각을 바꾸지
않는다. SDK/J bracket 기반 영상 age는 물리 동기·metric 보정의 수락 증거가 아니다.
"""

from collections import OrderedDict
from zlib import crc32

from ..oak.frames import RawFrame
from ..oak.jpeg import JpegPacket
from .jpeg_stream import EncodedJpeg, JpegSender, JpegStreamError
from .raw_binding import RawBinding
from .resident_transport import j_us


_AGE_US = 500_000
_U64_MAX = 2**64 - 1


def _decimal(value):
    if (type(value) is not str or not 1 <= len(value) <= 20 or not value.isascii()
            or not value.isdecimal() or str(int(value)) != value or int(value) > _U64_MAX):
        raise ValueError('original bounded decimal timestamp required')
    return int(value)


class AcquisitionVideo:
    """A-main 한 소유자의 metadata ring8과 원 전송 한 개. 실패해도 인지는 계속한다."""

    def __init__(self, binding, endpoint, *, clock=j_us):
        if type(binding) is not RawBinding or 'jpeg' not in binding.fields['stream_instances']:
            raise ValueError('video requires the approved JPEG RawBinding')
        fields = binding.fields
        self.scope = tuple(fields[name] for name in (
            'service_instance', 'acquisition_worker', 'pipeline_instance', 'capture_epoch'))
        self.clock = clock
        self.sender = JpegSender(endpoint, clock=clock)
        self._rgbs = OrderedDict()
        self._last_rgb = self._last_jpeg = None
        self.counts = dict(rgb=0, offered=0, duplicate=0, missing_rgb=0,
            expired=0, invalid=0, busy=0, transport_failed=0)
        self.failure = None

    def _prune(self, now):
        for key, row in tuple(self._rgbs.items()):
            if now >= row[0] + _AGE_US or now - row[2] > _AGE_US:
                del self._rgbs[key]

    def observe_rgb(self, frame, clock_fields):
        """검산된 원 ClockEvidence 사본만 읽는다. payload/SDK 객체는 보관하지 않는다."""
        try:
            if type(frame) is not RawFrame or frame.stream != 'rgb_video':
                raise ValueError('original RGB frame required')
            frame.validate()
            key = frame.sequence, frame.device_us, frame.sdk_us
            if self._last_rgb is not None and any(a <= b for a, b in zip(key, self._last_rgb)):
                self.counts['duplicate'] += 1
                return False
            if (frame.width, frame.height, frame.encoding) != (640, 360, 'NV12'):
                raise ValueError('video RGB profile differs')
            if type(frame.exposure_us) is not int or not 0 < frame.exposure_us <= _AGE_US:
                raise ValueError('original RGB exposure required')
            if type(clock_fields) is not dict or type(clock_fields.get('bridge_sample')) is not dict:
                raise ValueError('original SDK/J bridge required')
            bridge = clock_fields['bridge_sample']
            if set(bridge) != {'j_before_us', 'sdk_us', 'j_after_us'}:
                raise ValueError('original SDK/J bridge fields differ')
            before, sdk, after = (_decimal(bridge[name]) for name in ('j_before_us', 'sdk_us', 'j_after_us'))
            end = clock_fields.get('sdk_host_timestamp_j_bracket_us')
            if type(end) is not list or len(end) != 2:
                raise ValueError('original END bracket required')
            lower, upper = map(_decimal, end)
            if (before > after or not 0 <= frame.received_j_us - after <= 1_000_000
                    or (lower, upper) != (frame.sdk_us + before - sdk - 1, frame.sdk_us + after - sdk + 1)):
                raise ValueError('original END bracket differs from its bridge')
            lower -= frame.exposure_us
            now = self.clock()
            if (lower < 0 or lower + _AGE_US > _U64_MAX or upper > now
                    or frame.received_j_us > now or now >= lower + _AGE_US):
                self.counts['expired'] += 1
                return False
            self._prune(now)
            self._rgbs[key] = lower, upper, frame.received_j_us
            while len(self._rgbs) > 8:
                self._rgbs.popitem(last=False)
            self._last_rgb = key
            self.counts['rgb'] += 1
            return True
        except (ValueError, TypeError, KeyError):
            self.counts['invalid'] += 1
            return False

    def offer_jpeg(self, packet):
        """원 packet은 한 번만 시도한다. busy/drop 뒤 새 completed/E로 재포장하지 않는다."""
        if type(packet) is not JpegPacket:
            self.counts['invalid'] += 1
            return False
        key = packet.sequence, packet.device_us, packet.sdk_us
        if self._last_jpeg is not None and any(a <= b for a, b in zip(key, self._last_jpeg)):
            self.counts['duplicate'] += 1
            return False
        self._last_jpeg = key
        now = self.clock()
        self._prune(now)
        row = self._rgbs.get(key)
        if row is None:
            self.counts['missing_rgb'] += 1
            return False
        lower, upper, _ = row
        if packet.received_j_us > now or not upper <= now < lower + _AGE_US:
            self.counts['expired'] += 1
            return False
        fields = dict(zip(('service_instance', 'acquisition_worker', 'pipeline_instance', 'capture_epoch'), self.scope))
        fields.update(version=1, frame_sequence=str(packet.sequence), capture_j_lower_us=str(lower),
            capture_j_upper_us=str(upper), completed_j_us=str(now), expires_j_us=str(lower + _AGE_US),
            width=packet.width, height=packet.height, encoding='MJPEG', payload_length=len(packet.payload),
            crc32=crc32(packet.payload))
        try:
            frame = EncodedJpeg(fields, packet.payload)
            accepted = self.sender.offer(frame)
        except (OSError, ValueError, JpegStreamError) as error:
            self._fail(error)
            return False
        self.counts['offered' if accepted else 'busy'] += 1
        return accepted

    def _fail(self, error):
        self.failure = type(error).__name__[:32]
        self.counts['transport_failed'] += 1
        self.sender.close()

    def step(self):
        """원 전송을 유한하게 진행한다. 카메라/NN owner의 재시작을 요청하지 않는다."""
        try:
            self.sender.step()
        except (OSError, ValueError, JpegStreamError) as error:
            self._fail(error)

    def close(self):
        self.sender.close()
        self._rgbs.clear()

    def snapshot(self):
        return dict(counts=dict(self.counts), metadata_count=len(self._rgbs),
            channel_closed=self.sender.closed, failure=self.failure)
