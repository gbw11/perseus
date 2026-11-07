"""PARSER_WARMUP SLOT 6종의 정적 진술. 실제 예약·token·현재성·IO를 소유하지 않는다.

원 packet/context/limits/parent와 c1 값을 보유하며 G/P/Gr를 새 원 요청 시각으로
바꾸지 않는다. OFFER는 등록 S가 전달한 FI 진술이며 실제 S READY/과거 copy·post
증거가 아니다. 원장·수신 now/공통 순번·실송신·cleanup은 후속 actor의 책임이다.
"""

from dataclasses import dataclass
import re

from .local_envelope import StructuralPacket
from .raw_binding_messages import RawBindMessage
from .raw_leases import Endpoint, FilledInfo, LeaseRequest, Offer, RawScope, ReadKey, ReadLease, ReadRequest, WriteGrant, WriteKey


U64_MAX = 2**64 - 1
PAYLOAD_FIELDS = frozenset(('op', 'arena_id', 'slot', 'generation', 'write_token', 'read_token',
    'producer', 'consumer', 'raw_binding_sha256', 'publication_binding_sha256', 'purpose',
    'payload_length', 'descriptor_sha256'))
SUPPORTED_OPS = frozenset(('REQUEST', 'GRANT', 'FILLED', 'OFFER', 'ACQUIRE', 'ACQUIRED'))
_PARENTS = {'GRANT': 'REQUEST', 'FILLED': 'GRANT', 'ACQUIRE': 'OFFER', 'ACQUIRED': 'ACQUIRE'}


class SlotMessageError(ValueError):
    """정적 wire/원 parent/문맥/원 시각이 맞지 않는다."""


class UnsupportedSlotOperation(SlotMessageError):
    """초기6종·null publication·parser 이외의 기능은 명시 미지원이다."""


def _fail(message):
    raise SlotMessageError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _u64(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= U64_MAX:
        _fail('expected exact u64 integer')
    return value


def _decimal(value, *, positive=False):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or re.fullmatch('0|[1-9][0-9]*', value) is None):
        _fail('expected canonical u64 decimal string')
    return _u64(int(value), positive=positive)


def _same(value, expected):
    if type(value) is not type(expected) or value != expected:
        _fail('value differs from original registered field')


@dataclass(frozen=True)
class SlotTimeLimits:
    """독립 공급된 정적 상한. copy/OFFER의 실제 숫자는 runtime profile의 후속 gate다."""

    copy_budget_us: int
    offer_budget_us: int
    read_budget_us: int

    def __post_init__(self):
        for value in (self.copy_budget_us, self.offer_budget_us, self.read_budget_us):
            _u64(value, positive=True)
        if self.read_budget_us > 150000:
            _fail('read budget exceeds the initial M frame cap')


@dataclass(frozen=True)
class WriterSlotContext:
    """A 원 binding과 등록 arena. M BIND나 만료된 handshake의 재발급을 요구하지 않는다."""

    bind: RawBindMessage
    scope: RawScope

    def __post_init__(self):
        _exact(self.bind, RawBindMessage)
        _exact(self.scope, RawScope)
        context, scope = self.bind.context, self.scope
        if (context.peer.role != 'A' or context.peer.service_instance != scope.service_instance
                or self.bind.binding.sha256 != scope.raw_binding_sha256
                or context.acquisition != scope.writer
                or (context.peer.channel_epoch, context.peer.worker_instance)
                != (scope.writer.channel_epoch, scope.writer.worker_instance)):
            _fail('writer scope differs from the original A binding/registered endpoint')


@dataclass(frozen=True)
class ReaderSlotContext:
    """원 writer는 보존하고 등록 M만 별도로 결합한다. 현재 등록이나 takeover 허가가 아니다."""

    writer: WriterSlotContext
    bind: RawBindMessage

    def __post_init__(self):
        _exact(self.writer, WriterSlotContext)
        _exact(self.bind, RawBindMessage)
        writer, bind = self.writer, self.bind
        if (bind.context.peer.role != 'M' or bind.binding != writer.bind.binding
                or bind.binding_revision != writer.bind.binding_revision
                or bind.context.peer.service_instance != writer.scope.service_instance
                or bind.context.acquisition != writer.scope.writer):
            _fail('reader binding/revision/full A endpoint differs from original writer')

    @property
    def reader(self):
        peer = self.bind.context.peer
        return Endpoint(peer.channel_epoch, peer.worker_instance)


def _writer(context):
    if type(context) is WriterSlotContext:
        return context
    if type(context) is ReaderSlotContext:
        return context.writer
    _fail('expected exact writer/reader SLOT context')


def _header(packet, context):
    _exact(packet, StructuralPacket)
    writer = _writer(context)
    fields = packet.fields
    payload = fields['payload']
    if type(payload) is not dict or set(payload) != PAYLOAD_FIELDS:
        _fail('SLOT requires the exact thirteen payload fields')
    op = payload['op']
    if type(op) is not str:
        _fail('SLOT op must be an exact string')
    if op not in SUPPORTED_OPS:
        raise UnsupportedSlotOperation('only the initial six static SLOT operations are supported')
    _exact(context, WriterSlotContext if op in ('REQUEST', 'GRANT', 'FILLED') else ReaderSlotContext)
    scope = writer.scope
    peer = writer.bind.context.peer if type(context) is WriterSlotContext else context.bind.context.peer
    sender = peer.worker_instance if op in ('REQUEST', 'FILLED', 'ACQUIRE') else scope.service_instance
    expected = dict(kind='SLOT', binding_domain='RAW', service_instance=scope.service_instance,
                    channel_epoch=peer.channel_epoch, sender_instance=sender)
    for name, value in expected.items():
        _same(fields[name], value)
    if _decimal(fields['binding_revision'], positive=True) != writer.bind.binding_revision:
        _fail('SLOT RAW revision differs from its stable binding')
    for name, value in (('arena_id', scope.arena_id), ('producer', scope.writer.worker_instance),
                        ('raw_binding_sha256', scope.raw_binding_sha256)):
        _same(payload[name], value)
    if payload['publication_binding_sha256'] is not None:
        raise UnsupportedSlotOperation('initial SLOT publication is null-only')
    if payload['purpose'] not in (None, 'PARSER_WARMUP'):
        raise UnsupportedSlotOperation('metric/other read purpose is unsupported')
    _same(payload['purpose'], 'PARSER_WARMUP' if op in ('ACQUIRE', 'ACQUIRED') else None)
    _same(payload['consumer'], context.reader.worker_instance if type(context) is ReaderSlotContext else None)
    if op != 'ACQUIRED':
        _same(payload['read_token'], None)
    issued, expires = (_decimal(fields[k]) for k in ('issued_j_us', 'expires_j_us'))
    if issued >= expires:
        _fail('SLOT statement needs a nonempty original lifetime')
    return fields, payload, op, issued, expires


def _key(payload, scope):
    slot = payload['slot']
    if type(slot) is not int or slot not in (0, 1):
        _fail('slot must be an exact integer zero or one')
    return WriteKey(scope, slot, _decimal(payload['generation'], positive=True), payload['write_token'])


@dataclass(frozen=True, init=False)
class SlotMessage:
    """원 진술의 유한 parent와 c1 투영. 실제 S 원장의 소유/예약 상태를 만들지 않는다."""

    packet: StructuralPacket
    context: WriterSlotContext | ReaderSlotContext
    limits: SlotTimeLimits
    parent: object
    op: str
    request_id: str
    issued_us: int
    expires_us: int
    cap_us: int | None
    record: LeaseRequest | WriteGrant | FilledInfo | Offer | ReadRequest | ReadLease

    def __init__(self, packet, context, parent=None, *, limits, cap_us=None):
        _exact(limits, SlotTimeLimits)
        fields, payload, op, issued, expires = _header(packet, context)
        if op in _PARENTS:
            _exact(parent, SlotMessage)
            if parent.op != _PARENTS[op] or parent.context != context or parent.limits != limits:
                _fail('parent op/context/limits differ from the original statement')
            context, limits = parent.context, parent.limits
        elif parent is not None:
            _fail('REQUEST/OFFER have no parent in this static profile')
        if op not in ('REQUEST', 'ACQUIRE') and cap_us is not None:
            _fail('a child/independent OFFER cannot replace the original local cap')
        if op in ('REQUEST', 'ACQUIRE') and cap_us is not None:
            _u64(cap_us)
        local_cap = cap_us if op in ('REQUEST', 'ACQUIRE') else None if parent is None else parent.cap_us
        try:
            record = self._record(fields, payload, op, context, parent, limits, issued, expires, cap_us)
        except SlotMessageError:
            raise
        except ValueError as error:
            raise SlotMessageError('invalid c1 static record/key/time') from error
        for name, value in (('packet', packet), ('context', context), ('limits', limits), ('parent', parent),
                            ('op', op), ('request_id', fields['request_id']), ('issued_us', issued),
                            ('expires_us', expires), ('cap_us', local_cap), ('record', record)):
            object.__setattr__(self, name, value)

    @staticmethod
    def _record(fields, payload, op, context, parent, limits, issued, expires, cap_us):
        request_id = fields['request_id']
        scope = _writer(context).scope
        if op == 'REQUEST':
            for name in ('slot', 'generation', 'write_token', 'payload_length', 'descriptor_sha256'):
                _same(payload[name], None)
            if expires - issued > limits.copy_budget_us:
                _fail('copy request exceeds its original budget')
            return LeaseRequest(request_id, issued, expires, cap_us)
        key = _key(payload, scope)
        if op == 'GRANT':
            _same(payload['payload_length'], None)
            _same(payload['descriptor_sha256'], None)
            if request_id != parent.request_id or expires != parent.expires_us:
                _fail('GRANT must preserve original copy request ID/expiry')
            return WriteGrant(key, parent.record, issued)
        if op == 'FILLED':
            if (key != parent.record.key or request_id != parent.request_id or expires != parent.expires_us
                    or not parent.issued_us <= issued < expires):
                _fail('FILLED must preserve original grant/key/ID/expiry and completion order')
            return FilledInfo(key, payload['payload_length'], payload['descriptor_sha256'], issued)
        if op == 'OFFER':
            end = issued + limits.offer_budget_us
            _u64(end)
            if expires != end:
                _fail('OFFER expiry must equal original completion plus fixed offer budget')
            return Offer(FilledInfo(key, payload['payload_length'], payload['descriptor_sha256'], issued))
        original = parent.record.content if op == 'ACQUIRE' else parent.record.offer.content
        if (key != original.key or type(payload['payload_length']) is not int
                or payload['payload_length'] != original.payload_length
                or type(payload['descriptor_sha256']) is not str
                or payload['descriptor_sha256'] != original.descriptor_sha256):
            _fail('read statement differs from original complete content')
        if op == 'ACQUIRE':
            if (request_id == parent.request_id or not parent.issued_us <= issued < parent.expires_us
                    or expires - issued > limits.read_budget_us):
                _fail('read request must start inside original OFFER with a distinct ID and bounded budget')
            return ReadRequest(parent.record, context.reader, LeaseRequest(request_id, issued, expires, cap_us),
                               purpose='PARSER_WARMUP', publication_sha256=None)
        if (request_id != parent.request_id or expires != parent.expires_us
                or not parent.issued_us <= issued < min(parent.expires_us, parent.parent.expires_us)):
            _fail('ACQUIRED must preserve original read ID/expiry and reservation before notice expiry')
        return ReadLease(ReadKey(key, context.reader, payload['read_token']), parent.record, issued)

    @property
    def raw(self):
        return self.packet.raw_bytes

    @property
    def fields(self):
        return self.packet.fields

    @property
    def payload(self):
        return self.packet.payload


def parse_slot_message(packet, context, parent=None, *, limits, cap_us=None):
    return SlotMessage(packet, context, parent, limits=limits, cap_us=cap_us)
