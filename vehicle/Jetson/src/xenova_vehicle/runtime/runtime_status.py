"""mapper-runtime-1 진단 payload의 작은 불변 사본과 exact codec.

준비 상태·원 관측 시각은 실제 owner가 공급한다. 이 모듈은 clock을 읽거나
freshness, 모델 상주, metric/World/주행 권한을 승인하지 않는다. reason의 등록
여부 역시 producer 책임이며 여기서는 원 ASCII 코드의 문법만 검사한다.
HTTP/MQTT envelope·전송·로그·장치·doctor와 독립인 표준 라이브러리 코드다.
"""

from dataclasses import dataclass, field
import json
import re
from uuid import UUID

from .lifecycle_policy import validate_state


PROFILE = 'mapper-runtime-1'
MAX_PAYLOAD_BYTES = 2048
U64_MAX = (1 << 64) - 1
STATUS_FIELDS = frozenset(('profile', 'vehicle', 'device', 'service_instance',
    'mapper_boot', 'mapper_instance', 'pipeline_instance', 'clock_epoch',
    'release_sha256', 'runtime_state', 'source_binding_revision', 'snapshot_j_us',
    'capture_ready', 'metric_ready', 'model', 'workers', 'reason'))
MODEL_FIELDS = frozenset(('requirement', 'blob_sha256', 'residency_evidence',
    'inference_state', 'parser_state', 'last_input_j_us', 'last_result_j_us'))
WORKER_FIELDS = frozenset(('role', 'instance', 'state', 'last_progress_sequence',
    'last_progress_j_us'))
STAGE_STATES = frozenset(('UNAVAILABLE', 'WARMUP', 'VALID', 'STALE', 'FAILED'))
REQUIREMENTS = frozenset(('REQUIRED', 'OPTIONAL', 'ABSENT'))
RESIDENCY_EVIDENCE = frozenset(('NONE', 'GRAPH_STARTED', 'PAIRED_OUTPUT'))
ROLES = ('A', 'M', 'D')


class RuntimeStatusError(ValueError):
    """지원하지 않는 exact 형식·원 시각·크기다. runtime 상태를 변경하지 않는다."""


def _fail(message):
    raise RuntimeStatusError(message)


def _projection(value):
    """작은 builtin tree만 소유한다. 임의 copy/변환 hook은 호출하지 않는다."""
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 128:
            _fail('status exceeds its bounded tree')
        kind = type(item)
        if item is None or kind is bool:
            return item
        if kind is str:
            if len(item) > 64 or not item.isascii():
                _fail('status string/key exceeds ASCII64')
            return item
        if depth >= 3:
            _fail('status exceeds container depth3')
        if kind is dict:
            if len(item) > 17:
                _fail('status object exceeds17 fields')
            own = {}
            for key, child in item.items():
                if type(key) is not str:
                    _fail('status key must be an exact string')
                own[visit(key, depth + 1)] = visit(child, depth + 1)
            return own
        if kind is list:
            if len(item) > 3:
                _fail('status worker list exceeds3')
            return [visit(child, depth + 1) for child in item]
        _fail('status accepts exact dict/list/str/bool/null only')

    return visit(value, 0)


def _keys(value, expected):
    if type(value) is not dict or set(value) != expected:
        _fail('status object has unsupported exact fields')


def _ascii(value, maximum):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(char) < 32 or ord(char) > 126 for char in value)):
        _fail('expected bounded printable ASCII')


def _uuid(value):
    if value is None:
        return
    if type(value) is not str or len(value) != 36:
        _fail('expected canonical nonzero UUID or null')
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise RuntimeStatusError('invalid status UUID') from error
    if not parsed.int or str(parsed) != value:
        _fail('expected canonical nonzero UUID or null')


def _sha(value):
    if value is not None and (type(value) is not str
            or re.fullmatch('[0-9a-f]{64}', value) is None):
        _fail('expected lowercase SHA256 or null')


def _decimal(value, *, nullable=False):
    if value is None and nullable:
        return None
    if (type(value) is not str or not 1 <= len(value) <= 20
            or any(char < '0' or char > '9' for char in value)
            or len(value) > 1 and value[0] == '0'):
        _fail('expected canonical u64 decimal string')
    number = int(value)
    if number > U64_MAX:
        _fail('status u64 is outside its range')
    return number


def _enum(value, allowed):
    if type(value) is not str or value not in allowed:
        _fail('unsupported status enum')


def _state(role, value):
    try:
        validate_state(role, value)
    except ValueError as error:
        raise RuntimeStatusError('unsupported role/state combination') from error


def _validate(value):
    _keys(value, STATUS_FIELDS)
    if value['profile'] != PROFILE:
        _fail('unsupported runtime status profile')
    _ascii(value['vehicle'], 64)
    if (type(value['device']) is not str
            or re.fullmatch('[a-z0-9][a-z0-9_-]{0,31}', value['device']) is None):
        _fail('unsupported device topic component')
    for key in ('service_instance', 'mapper_boot', 'mapper_instance', 'pipeline_instance', 'clock_epoch'):
        _uuid(value[key])
    _sha(value['release_sha256'])
    _state('S', value['runtime_state'])
    _decimal(value['source_binding_revision'], nullable=True)
    snapshot = _decimal(value['snapshot_j_us'])
    for key in ('capture_ready', 'metric_ready'):
        if type(value[key]) is not bool:
            _fail('status readiness must be an exact bool')
    if value['reason'] is not None:
        _ascii(value['reason'], 32)

    model = value['model']
    _keys(model, MODEL_FIELDS)
    _enum(model['requirement'], REQUIREMENTS)
    _sha(model['blob_sha256'])
    _enum(model['residency_evidence'], RESIDENCY_EVIDENCE)
    for key in ('inference_state', 'parser_state'):
        _enum(model[key], STAGE_STATES)
    for key in ('last_input_j_us', 'last_result_j_us'):
        original = _decimal(model[key], nullable=True)
        if original is not None and original > snapshot:
            _fail('model original time is after snapshot')

    workers = value['workers']
    if type(workers) is not list or len(workers) > 3:
        _fail('expected at most3 ordered workers')
    previous = -1
    for worker in workers:
        _keys(worker, WORKER_FIELDS)
        role = worker['role']
        _enum(role, ROLES)
        index = ROLES.index(role)
        if index <= previous:
            _fail('workers must be a unique A/M/D subsequence')
        previous = index
        _uuid(worker['instance'])
        _state(role, worker['state'])
        sequence = _decimal(worker['last_progress_sequence'], nullable=True)
        original = _decimal(worker['last_progress_j_us'], nullable=True)
        if (sequence is None) != (original is None):
            _fail('worker progress sequence/time must both be null or present')
        if original is not None and original > snapshot:
            _fail('worker original progress is after snapshot')


def _pairs(pairs):
    own = {}
    for key, value in pairs:
        if key in own:
            _fail('duplicate status JSON key')
        own[key] = value
    return own


def _reject_number(value):
    _fail('status JSON numbers/nonfinite constants are unsupported')


@dataclass(frozen=True, slots=True, init=False)
class RuntimeStatus:
    """정확한17필드의 canonical ASCII bytes≤2048B를 소유한다.

    fields는 매번 독립 사본이다. 입력 순서/공백은 정규화하되 원 ID·순번·시각·bool은
    보충하거나 변경하지 않는다. 모델 stage/residency는 독립 진술이며 의미 승인은
    producer 책임이다. 아직 없는 UUID/hash/revision/원 시각에는 null을 사용한다.
    """

    _canonical_bytes: bytes = field(repr=False)

    def __init__(self, fields):
        value = _projection(fields)
        _validate(value)
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(',', ':'), allow_nan=False).encode('ascii')
        if len(raw) > MAX_PAYLOAD_BYTES:
            _fail('canonical runtime status exceeds2048 bytes')
        object.__setattr__(self, '_canonical_bytes', raw)

    @property
    def canonical_bytes(self):
        return self._canonical_bytes

    @property
    def fields(self):
        return json.loads(self._canonical_bytes)

    @classmethod
    def from_bytes(cls, raw):
        """원 payload cap을 먼저 검사한다. MQTT envelope/HTTP body를 대신 파싱하지 않는다."""
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PAYLOAD_BYTES:
            _fail('runtime status requires bytes1..2048')
        try:
            value = json.loads(raw.decode('ascii'), object_pairs_hook=_pairs,
                parse_int=_reject_number, parse_float=_reject_number, parse_constant=_reject_number)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise RuntimeStatusError('invalid runtime status JSON') from error
        return cls(value)
