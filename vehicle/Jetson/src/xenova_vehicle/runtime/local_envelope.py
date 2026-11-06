"""LocalEnvelope 바깥 구조만 검사한다. peer·typed payload·기한·작업 허가는 검사하지 않는다.

원 bytes를 보유하고 JSON 사본만 노출한다. decoder의 성공은 운영 admission이 아니며
kind별 unknown payload key/type/role/domain 규칙은 후속 의미 검사 책임이다.
로컬 encoder 한 packet profile로 leading/trailing whitespace와 BOM을 거절한다.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from uuid import UUID


MAX_PACKET_BYTES = 4096
MAX_DEPTH = 6
MAX_NODES = MAX_PACKET_BYTES
# 4096자리 십진수가 필요로 하는 bit 수보다 크며 str() work는 약4111자리 이하다.
MAX_INTEGER_BITS = MAX_PACKET_BYTES * 10 // 3
KINDS = frozenset("HELLO BIND BIND_ACK PROGRESS HEARTBEAT SLOT DB_JOB FAULT STOP STOPPED".split())
DOMAINS = frozenset("RAW PUBLICATION STORAGE LIFECYCLE".split())
OUTER_KEYS = frozenset("version service_instance channel_epoch sender_instance message_sequence kind binding_domain binding_revision request_id issued_j_us expires_j_us payload".split())


class EnvelopeStructureError(ValueError):
    """구조/유한 work 제한 실패. 권한 거절이나 mailbox 포화와는 다른 오류다."""


def _fail(message):
    raise EnvelopeStructureError(message)


def _bounded_tree(root, *, enforce_encoded_cost=True):
    """serialize 전에 encoded JSON cost/node/depth/cycle를 유한하게 검사한다."""
    remaining = MAX_PACKET_BYTES
    nodes = 0
    active = set()

    def charge(cost):
        nonlocal remaining
        if not enforce_encoded_cost:
            return
        remaining -= cost
        if remaining < 0:
            _fail("JSON exceeds packet byte budget")

    def string(value):
        if len(value) > MAX_PACKET_BYTES:
            _fail("string exceeds bounded character work")
        charge(2)
        for char in value:
            code = ord(char)
            if 0xD800 <= code <= 0xDFFF:
                _fail("lone surrogate is not a Unicode scalar")
            if char in ('"', '\\', '\b', '\f', '\n', '\r', '\t'):
                charge(2)
            elif code < 32:
                charge(6)
            else:
                charge(1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)

    def visit(value, depth):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES:
            _fail("JSON node work exceeds packet-derived limit")
        kind = type(value)
        if kind is dict or kind is list:
            if depth >= MAX_DEPTH or len(value) > MAX_NODES:
                _fail("JSON container depth/length exceeds limit")
            identity = id(value)
            if identity in active:
                _fail("cyclic JSON tree")
            active.add(identity)
            charge(2 + max(0, len(value) - 1))
            if kind is dict:
                charge(len(value))
                for key, child in value.items():
                    if type(key) is not str:
                        _fail("JSON object key must be a string")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in value:
                    visit(child, depth + 1)
            active.remove(identity)
        elif kind is str:
            string(value)
        elif value is None:
            charge(4)
        elif kind is bool:
            charge(4 if value else 5)
        elif kind is int:
            if value.bit_length() > MAX_INTEGER_BITS:
                _fail("integer exceeds bounded conversion work")
            try:
                charge(len(str(value)))
            except ValueError as error:
                raise EnvelopeStructureError("integer conversion exceeds interpreter limit") from error
        elif kind is float:
            if not math.isfinite(value):
                _fail("non-finite JSON number")
            charge(len(repr(value)))
        else:
            _fail("only exact builtin JSON tree types are supported")

    visit(root, 0)


def _u64_string(value, *, positive=False):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or any(char < '0' or char > '9' for char in value)
            or len(value) > 1 and value[0] == '0'):
        _fail("u64 field must be a canonical ASCII decimal string")
    number = int(value)
    if number > 2**64 - 1 or positive and number == 0:
        _fail("u64 field is outside its range")


def _outer(fields):
    if type(fields) is not dict or set(fields) != OUTER_KEYS:
        _fail("outer envelope must have exactly twelve keys")
    if type(fields['version']) is not int or fields['version'] != 1:
        _fail("unsupported structural version")
    for name in ('service_instance', 'channel_epoch', 'sender_instance', 'request_id'):
        value = fields[name]
        if type(value) is not str or len(value) != 36:
            _fail("identity must be a canonical lowercase UUID")
        try:
            canonical = str(UUID(value))
        except ValueError as error:
            raise EnvelopeStructureError("invalid UUID") from error
        if canonical != value:
            _fail("identity must be a canonical lowercase UUID")
    _u64_string(fields['message_sequence'], positive=True)
    for name in ('binding_revision', 'issued_j_us', 'expires_j_us'):
        _u64_string(fields[name])
    if type(fields['kind']) is not str or fields['kind'] not in KINDS:
        _fail("unknown structural kind")
    if type(fields['binding_domain']) is not str or fields['binding_domain'] not in DOMAINS:
        _fail("unknown binding domain")
    if type(fields['payload']) is not dict:
        _fail("payload must be an opaque JSON object")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate decoded JSON key")
        result[key] = value
    return result


def _constant(value):
    _fail("non-finite JSON constant")


def _decode(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_PACKET_BYTES:
        _fail("packet must be immutable bytes within 4096B")
    if raw[:1] in (b' ', b'\t', b'\r', b'\n') or raw[-1:] in (b' ', b'\t', b'\r', b'\n'):
        _fail("packet edge whitespace is outside the local encoder profile")
    try:
        text = raw.decode('utf-8', errors='strict')
        if text.startswith('\ufeff'):
            _fail("UTF-8 BOM is outside the local encoder profile")
        fields = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, EnvelopeStructureError):
            raise
        raise EnvelopeStructureError("invalid UTF-8 JSON packet") from error
    # Decoder는 실제 받은 bytes cap을 이미 확인했다. compact 지수 표기의 유한 float를
    # re-encode했을 때 길어진다는 이유로 원 유효 packet을 거절하지 않는다.
    _bounded_tree(fields, enforce_encoded_cost=False)
    _outer(fields)
    return fields


@dataclass(frozen=True)
class StructuralPacket:
    """검사한 원 bytes와 바깥 구조. fields/payload는 항상 새 사본이며 허가 증거가 아니다.

    private parsed state를 직접 바꾸지 않는 단일-owner 공유 계약이다. 원 packet은
    canonical re-encode로 교체하지 않는다. typed payload/peer/sequence/기한은 미검증이다.
    """

    raw_bytes: bytes
    _parsed: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, '_parsed', _decode(self.raw_bytes))

    @property
    def fields(self):
        return deepcopy(self._parsed)

    @property
    def payload(self):
        return deepcopy(self._parsed['payload'])

    @property
    def kind(self):
        return self._parsed['kind']

    @property
    def binding_domain(self):
        return self._parsed['binding_domain']


def decode_structure(raw: bytes) -> StructuralPacket:
    """strict UTF-8·exact outer·bounded opaque JSON을 검사하고 원 bytes를 보존한다."""
    return StructuralPacket(raw)


def encode_structure(fields: dict) -> StructuralPacket:
    """유한 tree를 먼저 검사해 deterministic UTF-8 packet을 만든다. 의미 허가는 하지 않는다."""
    _bounded_tree(fields)
    _outer(fields)
    try:
        raw = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise EnvelopeStructureError("cannot encode bounded JSON") from error
    return StructuralPacket(raw)
