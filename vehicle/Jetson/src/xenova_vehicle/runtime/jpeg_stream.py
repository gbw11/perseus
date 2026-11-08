"""원 JPEG 사본과 A→S의 유한 stream 전송. raw view나 센서 수명을 소유하지 않는다.

길이를 먼저 검사하고 원 시각/CRC를 보존한다. 불명 partial은 영상 채널만 닫는다.
JPEG bitstream을 decode하거나 capture/NN/metric/source 권한을 발급하지 않는다.
"""

from dataclasses import dataclass
import json
import socket
import struct
import threading
import time
from uuid import UUID
import zlib


HEADER_CAP = 1024
BODY_CAP = 256 * 1024
AGE_US = 500_000
TRANSFER_US = 250_000
U64_MAX = 2**64 - 1
FIELDS = ('version', 'service_instance', 'acquisition_worker', 'pipeline_instance',
    'capture_epoch', 'frame_sequence', 'capture_j_lower_us', 'capture_j_upper_us',
    'completed_j_us', 'expires_j_us', 'width', 'height', 'encoding', 'payload_length', 'crc32')
SCOPE_FIELDS = FIELDS[1:5]


class JpegError(ValueError):
    """영상 사본의 원 형식/문맥/기한이 맞지 않는다."""


class JpegStreamError(RuntimeError):
    """이 영상 stream을 더는 재동기화하지 않고 닫았다."""


def monotonic_us():
    return time.monotonic_ns() // 1000


def _u64(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise JpegError('exact u64 clock required')
    return value


def _decimal(value):
    if (type(value) is not str or not 1 <= len(value) <= 20 or not value.isascii()
            or not value.isdecimal() or str(int(value)) != value):
        raise JpegError('canonical u64 decimal string required')
    return _u64(int(value))


def validate_scope(value):
    if type(value) is not tuple or len(value) != 4:
        raise JpegError('registered JPEG scope4 required')
    for item in value:
        if type(item) is not str or len(item) != 36:
            raise JpegError('canonical nonzero JPEG UUID required')
        try:
            parsed = UUID(item)
        except ValueError as error:
            raise JpegError('canonical nonzero JPEG UUID required') from error
        if str(parsed) != item or not parsed.int:
            raise JpegError('canonical nonzero JPEG UUID required')
    return value


def _fields(fields):
    if (type(fields) is not dict or any(type(k) is not str for k in fields)
            or set(fields) != set(FIELDS)):
        raise JpegError('exact EncodedJpeg fields required')
    validate_scope(tuple(fields[k] for k in SCOPE_FIELDS))
    for key, expected in (('version', 1), ('width', 640), ('height', 360)):
        if type(fields[key]) is not int or fields[key] != expected:
            raise JpegError('unsupported JPEG version or P0 shape')
    if type(fields['encoding']) is not str or fields['encoding'] != 'MJPEG':
        raise JpegError('MJPEG encoding required')
    for key, maximum in (('payload_length', BODY_CAP), ('crc32', 2**32 - 1)):
        if type(fields[key]) is not int or not 0 <= fields[key] <= maximum:
            raise JpegError('bounded JPEG length/CRC required')
    if not fields['payload_length']:
        raise JpegError('nonempty JPEG body required')
    for key in FIELDS[5:10]:
        _decimal(fields[key])
    lower, upper, completed, expires = (int(fields[k]) for k in FIELDS[6:10])
    if not lower <= upper or not lower <= completed < expires <= lower + AGE_US:
        raise JpegError('original capture age/deadline differs')
    return dict(fields)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JpegError('duplicate JPEG header key')
        result[key] = value
    return result


def _decode_header(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(JpegError('nonfinite JPEG header')))
        return _fields(value)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise JpegError('invalid bounded JPEG header') from error


@dataclass(frozen=True, slots=True, init=False)
class EncodedJpeg:
    """실제 encoder가 소유한 bytes. header/CRC 수락은 영상 decode 성공이 아니다."""

    _values: tuple
    payload: bytes
    header_bytes: bytes

    def __init__(self, fields, payload):
        values = _fields(fields)
        raw = json.dumps(values, sort_keys=True, ensure_ascii=True,
            separators=(',', ':'), allow_nan=False).encode('ascii')
        self._install(values, payload, raw)

    def _install(self, values, payload, header):
        if (type(payload) is not bytes or len(payload) != values['payload_length']
                or zlib.crc32(payload) != values['crc32'] or len(header) > HEADER_CAP):
            raise JpegError('JPEG body length/CRC/header cap differs')
        object.__setattr__(self, '_values', tuple(values[k] for k in FIELDS))
        object.__setattr__(self, 'payload', payload)
        object.__setattr__(self, 'header_bytes', header)

    @classmethod
    def _received(cls, fields, payload, header):
        value = object.__new__(cls)
        value._install(fields, payload, header)
        return value

    @property
    def fields(self):
        return dict(zip(FIELDS, self._values))

    @property
    def scope(self):
        return self._values[1:5]

    @property
    def sequence(self):
        return int(self._values[5])

    @property
    def completed_j_us(self):
        return int(self._values[8])

    @property
    def expires_j_us(self):
        return int(self._values[9])


class _Endpoint:
    def __init__(self, endpoint, clock):
        if (not isinstance(endpoint, socket.socket) or endpoint.type & 15 != socket.SOCK_STREAM
                or not callable(clock)):
            raise ValueError('owned stream socket and clock required')
        endpoint.setblocking(False)
        self.endpoint, self.clock = endpoint, clock
        self._owner = threading.current_thread()
        self.closed = False
        self.failure = None

    def _check(self):
        if threading.current_thread() is not self._owner:
            raise RuntimeError('JPEG endpoint belongs to original thread')

    def _fail(self, reason):
        self.failure = reason
        self.close()
        raise JpegStreamError(reason)

    def close(self):
        self._check()
        if not self.closed:
            self.closed = True
            self.endpoint.close()


class JpegSender(_Endpoint):
    """단일 원 frame만 보유한다. step 수락을 full-send 완료로 세지 않는다."""

    def __init__(self, endpoint, clock=monotonic_us):
        super().__init__(endpoint, clock)
        self._frame = None
        self._parts = ()
        self._index = self._offset = 0
        self._started = None

    @property
    def pending(self):
        return self._frame

    def offer(self, frame):
        self._check()
        if type(frame) is not EncodedJpeg:
            raise JpegError('original EncodedJpeg required')
        if self.closed or self._frame is not None:
            return False
        now = _u64(self.clock())
        if not frame.completed_j_us <= now < min(frame.expires_j_us, frame.completed_j_us + TRANSFER_US):
            return False
        self._frame = frame
        self._parts = (struct.pack('<II', len(frame.header_bytes), len(frame.payload)), frame.header_bytes, frame.payload)
        self._index = self._offset = 0
        self._started = None
        return True

    def step(self):
        self._check()
        if self.closed or self._frame is None:
            return None
        if self._started is None:
            self._started = _u64(self.clock())
        frame = self._frame
        deadline = min(frame.expires_j_us, frame.completed_j_us + TRANSFER_US, self._started + TRANSFER_US)
        for _ in range(6):
            if _u64(self.clock()) >= deadline:
                self._fail('original JPEG send deadline expired')
            part = self._parts[self._index]
            try:
                count = self.endpoint.send(memoryview(part)[self._offset:self._offset + 65536])
            except BlockingIOError:
                return None
            except OSError as error:
                self._fail('JPEG send failed: ' + type(error).__name__)
            if count <= 0:
                self._fail('JPEG stream send EOF')
            self._offset += count
            if self._offset == len(part):
                self._index += 1
                self._offset = 0
                if self._index == len(self._parts):
                    if _u64(self.clock()) >= deadline:
                        self._fail('JPEG completed after original send deadline')
                    self._frame, self._parts = None, ()
                    return frame
        return None

    def close(self):
        super().close()
        self._frame, self._parts = None, ()


class JpegReceiver(_Endpoint):
    """길이 선검사/한 partial. freeze 직전 callback은 S 최신 사본 ref만 비운다."""

    def __init__(self, endpoint, expected_scope, clock=monotonic_us):
        self.expected_scope = validate_scope(expected_scope)
        super().__init__(endpoint, clock)
        self._prefix = bytearray(8)
        self._header = self._body = None
        self._fields = None
        self._stage = self._offset = 0
        self._started = None
        self._highwater = -1

    @property
    def buffered_bytes(self):
        return 0 if self._body is None else len(self._body)

    def step(self, *, before_complete=None):
        self._check()
        if self.closed:
            return None
        for _ in range(6):
            now = _u64(self.clock())
            deadline = None if self._started is None else self._started + TRANSFER_US
            if self._fields is not None:
                deadline = min(deadline, int(self._fields['expires_j_us']), int(self._fields['completed_j_us']) + TRANSFER_US)
            if deadline is not None and now >= deadline:
                self._fail('original JPEG receive deadline expired')
            buffer = (self._prefix, self._header, self._body)[self._stage]
            try:
                count = self.endpoint.recv_into(memoryview(buffer)[self._offset:self._offset + 65536])
            except BlockingIOError:
                return None
            except OSError as error:
                self._fail('JPEG receive failed: ' + type(error).__name__)
            if count == 0:
                self._fail('JPEG stream EOF')
            if self._started is None:
                self._started = now
            self._offset += count
            if self._offset != len(buffer):
                continue
            self._offset = 0
            if self._stage == 0:
                header_size, body_size = struct.unpack('<II', self._prefix)
                if not 0 < header_size <= HEADER_CAP or not 0 < body_size <= BODY_CAP:
                    self._fail('JPEG prefix exceeds header/body cap')
                self._header = bytearray(header_size)
                self._body_size = body_size
                self._stage = 1
            elif self._stage == 1:
                try:
                    self._fields = _decode_header(bytes(self._header))
                    if (self._fields['payload_length'] != self._body_size
                            or tuple(self._fields[k] for k in SCOPE_FIELDS) != self.expected_scope
                            or int(self._fields['completed_j_us']) > _u64(self.clock())):
                        raise JpegError('JPEG original scope/length/completion differs')
                except JpegError as error:
                    self._fail(str(error))
                self._body = bytearray(self._body_size)
                self._stage = 2
            else:
                deadline = min(self._started + TRANSFER_US, int(self._fields['expires_j_us']),
                    int(self._fields['completed_j_us']) + TRANSFER_US)
                if _u64(self.clock()) >= deadline:
                    self._fail('JPEG completed after original receive deadline')
                if int(self._fields['frame_sequence']) <= self._highwater:
                    if zlib.crc32(self._body) != self._fields['crc32']:
                        self._fail('duplicate JPEG body CRC differs')
                    self._header = self._body = self._fields = None
                    self._stage = self._offset = 0
                    self._started = None
                    return None
                if before_complete is not None:
                    before_complete()
                payload = bytes(self._body)
                try:
                    frame = EncodedJpeg._received(self._fields, payload, bytes(self._header))
                except JpegError as error:
                    self._fail(str(error))
                self._header = self._body = self._fields = None
                self._stage = self._offset = 0
                self._started = None
                self._highwater = frame.sequence
                return frame
        return None

    def close(self):
        super().close()
        self._header = self._body = self._fields = None
