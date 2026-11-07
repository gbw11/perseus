"""고정 Linux 4.9 procfs profile의 순수 bounded bytes parser. 실제 OS 관측은 하지 않는다.

PID는 positive u32, mount/mnt_id는 positive u64, parent는 nonnegative u64다.
device major/minor는 u32다. 실제 Nano mountinfo의 root 행은 parent 0을 사용한다.
stat의 무관 numeric은 signed64 음수 또는 u64 비음수를 허용하고 start_ticks는 u64다.
숫자 길이를 먼저 제한하며 모든 raw는 exact bytes·최종 LF·명시 cap을 요구한다.
comm은 0..15 raw bytes·NUL 없음이며 comm/경로를 UTF8로 추측하지 않는다.
공개 record의 값만으로 실제 source를 증명하지 않는다.
"""

from dataclasses import dataclass
import re


_U32 = 2**32 - 1
_U64 = 2**64 - 1
_I64_MIN = -(2**63)
_STATES = ('R', 'S', 'D', 'T', 't', 'X', 'Z')
_BOOT_ID = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_ESCAPES = {b'\\040': 32, b'\\011': 9, b'\\012': 10, b'\\134': 92}


class ProcFormatError(ValueError):
    """타입·bounded profile·완결된 raw 문법이 맞지 않는다. PID 종료 판정은 아니다."""


def _fail(message):
    raise ProcFormatError(message)


def _integer(value, maximum, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0) or value > maximum:
        _fail('invalid bounded native integer')


def _body(raw, maximum):
    if type(raw) is not bytes:
        _fail('raw must be exact bytes')
    if not raw or len(raw) > maximum or not raw.endswith(b'\n') or raw.endswith(b'\r\n'):
        _fail('raw is empty, oversized or lacks final LF')
    return raw[:-1]


def _decimal(token, maximum, *, positive=False, signed=False, digits=20):
    # int() 이전에 길이·ASCII 문법을 검사해 큰 정수 변환 작업을 제한한다.
    if type(token) is not bytes or not token or len(token) > digits + (1 if signed else 0):
        _fail('numeric token exceeds profile')
    negative = signed and token.startswith(b'-')
    magnitude = token[1:] if negative else token
    if not magnitude or len(magnitude) > digits or not magnitude.isdigit():
        _fail('numeric token is not bounded decimal')
    value = int(token)
    if negative:
        if value < _I64_MIN:
            _fail('signed numeric token exceeds profile')
    elif value > maximum:
        _fail('unsigned numeric token exceeds profile')
    if positive and value <= 0:
        _fail('numeric token must be positive')
    return value


@dataclass(frozen=True)
class ProcStat:
    """원 PID/state/start clock ticks. state Z/X도 종료·회수 완료를 뜻하지 않는다."""

    pid: int
    state: str
    start_ticks: int

    def __post_init__(self):
        _integer(self.pid, _U32, positive=True)
        if type(self.state) is not str or self.state not in _STATES:
            _fail('unsupported Linux 4.9 task state')
        _integer(self.start_ticks, _U64)


@dataclass(frozen=True)
class MountRecord:
    """mountinfo 한 행의 값. 실제 FD 선택·hidepid/backing 검증은 native adapter의 책임이다."""

    mount_id: int
    parent_id: int
    major: int
    minor: int
    root: bytes
    mountpoint: bytes
    fs_type: bytes
    source: bytes
    mount_options: tuple[bytes, ...]
    super_options: tuple[bytes, ...]
    optional_fields: tuple[bytes, ...]

    def __post_init__(self):
        _integer(self.mount_id, _U64, positive=True)
        _integer(self.parent_id, _U64)
        _integer(self.major, _U32)
        _integer(self.minor, _U32)
        total = 0
        for value in (self.root, self.mountpoint, self.fs_type, self.source):
            if type(value) is not bytes or not value or len(value) > 65536:
                _fail('mount field must be bounded nonempty bytes')
            total += len(value)
        if not self.root.startswith(b'/') or not self.mountpoint.startswith(b'/'):
            _fail('mount root and mountpoint must be absolute')
        for values, required in ((self.mount_options, True), (self.super_options, True),
                                 (self.optional_fields, False)):
            if type(values) is not tuple or len(values) > 65536 or (required and not values):
                _fail('mount field collection must be bounded native tuple')
            for value in values:
                if type(value) is not bytes or not value:
                    _fail('mount token must be nonempty exact bytes')
                total += len(value)
                if total > 65536:
                    _fail('mount record value bytes exceed row profile')
        if total > 65536:
            _fail('mount record value bytes exceed row profile')


def parse_proc_stat(raw, expected_pid):
    """52-field stat을 해석한다. opaque comm의 마지막 ')' 뒤에서만 numeric을 분리한다."""
    _integer(expected_pid, _U32, positive=True)
    body = _body(raw, 4096)
    opening = body.find(b' (')
    closing = body.rfind(b') ')
    if opening < 1 or closing < opening + 2:
        _fail('invalid PID/comm envelope')
    pid = _decimal(body[:opening], _U32, positive=True, digits=10)
    if pid != expected_pid:
        _fail('stat PID differs from original target')
    comm = body[opening + 2:closing]
    if len(comm) > 15 or b'\x00' in comm:
        _fail('stat comm exceeds Linux 4.9 raw bytes profile')
    suffix = body[closing + 2:].split(b' ')
    if len(suffix) != 50 or suffix[0] not in (b'R', b'S', b'D', b'T', b't', b'X', b'Z'):
        _fail('expected state and 49 numeric fields')
    for token in suffix[1:]:
        _decimal(token, _U64, signed=True)
    start_ticks = _decimal(suffix[19], _U64)
    return ProcStat(pid, suffix[0].decode('ascii'), start_ticks)


def parse_boot_id(raw):
    """kernel boot UUID의 canonical lowercase 표현과 최종 LF를 검사한다."""
    body = _body(raw, 64)
    if _BOOT_ID.fullmatch(body) is None:
        _fail('boot ID must be canonical lowercase UUID')
    return body.decode('ascii')


def parse_fdinfo(raw):
    """단일 mnt_id만 해석한다. pos/flags/unknown 행과 신형 ino 필드는 필수값이 아니다."""
    body = _body(raw, 8192)
    result = None
    for line in body.split(b'\n'):
        key, colon, value = line.partition(b':')
        if key != b'mnt_id':
            continue
        if not colon or result is not None:
            _fail('fdinfo must contain exactly one mnt_id')
        tokens = value.split()
        if len(tokens) != 1:
            _fail('fdinfo mnt_id must have one integer')
        result = _decimal(tokens[0], _U64, positive=True)
    if result is None:
        _fail('fdinfo mnt_id is missing')
    return result


def validate_self_status(raw, expected_pid):
    """Pid/Tgid와 두 namespace 목록의 정확 한 원소를 검사한다. 실제 self source는 외부 책임이다."""
    _integer(expected_pid, _U32, positive=True)
    body = _body(raw, 65536)
    required = (b'Pid', b'Tgid', b'NSpid', b'NStgid')
    found = {}
    for line in body.split(b'\n'):
        key, colon, value = line.partition(b':')
        if key not in required:
            continue
        if not colon or key in found:
            _fail('self identity key must appear exactly once')
        tokens = value.split()
        if len(tokens) != 1:
            _fail('self identity must have one namespace element')
        parsed = _decimal(tokens[0], _U32, positive=True, digits=10)
        if parsed != expected_pid:
            _fail('self identity does not match original PID')
        found[key] = parsed
    if len(found) != 4:
        _fail('self identity fields are missing')


def _path(value):
    # 입력만 순회하므로 \134040의 결과인 literal \040을 다시 space로 바꾸지 않는다.
    result = bytearray()
    position = 0
    while position < len(value):
        if value[position] == 92 and position + 4 <= len(value):
            decoded = _ESCAPES.get(value[position:position + 4])
            if decoded is not None:
                result.append(decoded)
                position += 4
                continue
        result.append(value[position])
        position += 1
    return bytes(result)


def _options(value):
    result = tuple(value.split(b','))
    if any(not token for token in result):
        _fail('mount option tokens must be nonempty')
    return result


def parse_mountinfo(raw):
    """모든 행을 검사한다. stacked mountpoint는 허용하고 전역 mount ID 중복은 거절한다."""
    body = _body(raw, 1048576)
    if raw.count(b'\n') > 4096:
        _fail('mountinfo exceeds row count profile')
    records = []
    identifiers = set()
    for row in body.split(b'\n'):
        if not row or len(row) + 1 > 65536:
            _fail('mountinfo row is empty or oversized')
        tokens = row.split(b' ')
        separator = len(tokens) - 4
        if (len(tokens) < 10 or any(not token for token in tokens) or tokens[separator] != b'-'
                or b'-' in tokens[6:separator]):
            _fail('invalid mountinfo fields or separator')
        mount_id = _decimal(tokens[0], _U64, positive=True)
        parent_id = _decimal(tokens[1], _U64)
        device = tokens[2].split(b':')
        if len(device) != 2:
            _fail('mount device must have major:minor')
        major = _decimal(device[0], _U32, digits=10)
        minor = _decimal(device[1], _U32, digits=10)
        if mount_id in identifiers:
            _fail('duplicate mount ID')
        identifiers.add(mount_id)
        record = MountRecord(
            mount_id, parent_id, major, minor, _path(tokens[3]), _path(tokens[4]),
            tokens[separator + 1], _path(tokens[separator + 2]), _options(tokens[5]),
            _options(tokens[separator + 3]), tuple(tokens[6:separator]),
        )
        records.append(record)
    return tuple(records)
