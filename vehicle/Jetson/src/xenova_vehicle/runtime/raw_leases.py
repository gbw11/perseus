"""한 raw arena의 두 slot을 순서화하는 순수 S 원장. 실제 메모리/token/exit는 소유하지 않는다.

등록·완료 fact는 caller가 검증한 범위가 있는 합성 정책 입력이다. constructor의 형식
검사가 실제 OS 관측을 만들지 않는다. 단일 owner는 외부 동기 token 작업이 돌아오기 전
다른 thread/signal callback에서 이 원장을 변이하지 않는다. METRIC_MAP과
전역 quarantine manager는 미지원이다. reader 교체는 실제 caller가 결박한
old reader 실종료·A pause·token 정규화 사실이 모두 있는 별도 경계에서만 허용한다.
"""

from dataclasses import dataclass, replace
from enum import Enum
import re
from uuid import UUID


U64_MAX = 2**64 - 1
SLOT_BYTES = 2 * 1024 * 1024
METADATA_BYTES = 4096
ARENA_BYTES = 2 * SLOT_BYTES


class LeaseLedgerError(ValueError):
    """잘못된 입력/현재성/원 기한/상태. 외부 token 완료 뒤 fault는 별도로 기록한다."""


class UnsupportedPurpose(LeaseLedgerError):
    """사전 capture metadata 계약이 없는 METRIC_MAP을 지원한 척하지 않는다."""


class GenerationExhausted(LeaseLedgerError):
    """현재 slot의 MAX를 소진했다. reset/새 arena를 자동 생성하지 않는다."""


def _fail(message):
    raise LeaseLedgerError(message)


def _u64(value, label='time', *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= U64_MAX:
        _fail(label + ' must be an exact u64 integer')
    return value


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        _fail('expected canonical UUID')
    try:
        canonical = str(UUID(value))
    except ValueError as error:
        raise LeaseLedgerError('invalid UUID') from error
    if canonical != value:
        _fail('expected canonical lowercase UUID')


def _sha(value):
    if type(value) is not str or len(value) != 64 or re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('expected lowercase SHA256')


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__ + ' record')


def _slot(value):
    if type(value) is not int or value not in (0, 1):
        _fail('raw slot must be zero or one')
    return value


def _event(event_us, now_us):
    _u64(event_us)
    _u64(now_us, 'now_us')
    if event_us > now_us:
        _fail('future event stamp')


def _next_generation(value):
    """순수 산술 경계이며 production seed/reset API가 아니다."""
    _u64(value, 'generation')
    if value == U64_MAX:
        raise GenerationExhausted('slot generation is exhausted')
    return value + 1


@dataclass(frozen=True)
class Endpoint:
    channel_epoch: str
    worker_instance: str

    def __post_init__(self):
        _uuid(self.channel_epoch)
        _uuid(self.worker_instance)


@dataclass(frozen=True)
class RawScope:
    """등록 name/size/role도 pin한다. 실제 SHM 존재·RawBinding 본문 승인을 확인하지 않는다."""

    service_instance: str
    arena_id: str
    writer: Endpoint
    raw_binding_sha256: str
    arena_name: str
    arena_size_bytes: int = ARENA_BYTES
    arena_role: str = 'RAW'

    def __post_init__(self):
        _uuid(self.service_instance)
        _uuid(self.arena_id)
        _exact(self.writer, Endpoint)
        _sha(self.raw_binding_sha256)
        name = self.arena_name
        if (type(name) is not str or not 1 <= len(name) <= 128
                or any(ord(c) < 32 or ord(c) > 126 for c in name)):
            _fail('expected bounded printable arena name; exact OS name profile is deferred')
        if type(self.arena_size_bytes) is not int or self.arena_size_bytes != ARENA_BYTES:
            _fail('initial raw arena is exactly two 2MiB slots')
        if type(self.arena_role) is not str or self.arena_role != 'RAW':
            _fail('initial ledger supports raw arena role only')


@dataclass(frozen=True)
class RawRecoveryBarrier:
    """caller의 원 PAUSED/old M 실종료 사실. 형식 검사가 OS 증명을 만들지는 않는다."""

    scope: RawScope
    old_reader: Endpoint
    pause_request_id: str
    pause_ack_sha256: str
    paused_us: int
    old_reader_exited_us: int

    def __post_init__(self):
        _exact(self.scope, RawScope)
        _exact(self.old_reader, Endpoint)
        _uuid(self.pause_request_id)
        _sha(self.pause_ack_sha256)
        _u64(self.paused_us)
        _u64(self.old_reader_exited_us)


@dataclass(frozen=True)
class RawOwnerExitBarrier:
    """A/M이 모두 실제 종료된 arena의 정규화 전제. 새 arena grant 자체는 아니다."""

    scope: RawScope
    writer_exited_us: int
    reader_exited_us: int

    def __post_init__(self):
        _exact(self.scope, RawScope)
        _u64(self.writer_exited_us)
        _u64(self.reader_exited_us)


@dataclass(frozen=True)
class RawRecoveryEvidence:
    """동일 원 barrier에서 실제 token 정규화가 끝난 한 operation의 완료 사실."""

    barrier: RawRecoveryBarrier
    operation_id: str
    normalized_us: int

    def __post_init__(self):
        _exact(self.barrier, RawRecoveryBarrier)
        _uuid(self.operation_id)
        _u64(self.normalized_us)
        if max(self.barrier.paused_us, self.barrier.old_reader_exited_us) > self.normalized_us:
            _fail('normalization precedes actual quiescence facts')


@dataclass(frozen=True)
class RawOwnerExitEvidence:
    """A/M 실제 종료 뒤 token 정규화 사실. M-only pause 사실로 바꾸어 쓰지 않는다."""

    barrier: RawOwnerExitBarrier
    operation_id: str
    normalized_us: int

    def __post_init__(self):
        _exact(self.barrier, RawOwnerExitBarrier)
        _uuid(self.operation_id)
        _u64(self.normalized_us)
        if max(self.barrier.writer_exited_us, self.barrier.reader_exited_us) > self.normalized_us:
            _fail('normalization precedes actual owner exits')


@dataclass(frozen=True)
class WriteKey:
    scope: RawScope
    slot: int
    generation: int
    write_token: str

    def __post_init__(self):
        _exact(self.scope, RawScope)
        _slot(self.slot)
        _u64(self.generation, 'generation', positive=True)
        _uuid(self.write_token)


@dataclass(frozen=True)
class ReadKey:
    write_key: WriteKey
    reader: Endpoint
    read_token: str

    def __post_init__(self):
        _exact(self.write_key, WriteKey)
        _exact(self.reader, Endpoint)
        _uuid(self.read_token)


@dataclass(frozen=True)
class LeaseRequest:
    request_id: str
    issued_us: int
    expires_us: int
    cap_us: int | None = None

    def __post_init__(self):
        _uuid(self.request_id)
        _u64(self.issued_us)
        _u64(self.expires_us)
        if self.cap_us is not None:
            _u64(self.cap_us, 'cap_us')
        if self.issued_us >= self.expires_us or self.cap_us is not None and self.expires_us > self.cap_us:
            _fail('request needs a nonempty original lifetime within its cap')

    def check(self, now_us):
        _u64(now_us, 'now_us')
        if not self.issued_us <= now_us < self.expires_us:
            _fail('request is outside its original half-open lifetime')


@dataclass(frozen=True)
class WriteGrant:
    key: WriteKey
    request: LeaseRequest
    granted_us: int

    def __post_init__(self):
        _exact(self.key, WriteKey)
        _exact(self.request, LeaseRequest)
        self.request.check(self.granted_us)


@dataclass(frozen=True)
class FilledInfo:
    """producer 완료 진술의 작은 요약. descriptor/CRC/원 capture freshness 검증은 c2/lower다."""

    key: WriteKey
    payload_length: int
    descriptor_sha256: str
    completed_us: int

    def __post_init__(self):
        _exact(self.key, WriteKey)
        if type(self.payload_length) is not int or not 1 <= self.payload_length <= SLOT_BYTES - METADATA_BYTES:
            _fail('payload length must fit the slot body extent')
        _sha(self.descriptor_sha256)
        _u64(self.completed_us)


@dataclass(frozen=True)
class Offer:
    """READY content의 작은 안내. read token/메모리 읽기 허가는 없다."""

    content: FilledInfo

    def __post_init__(self):
        _exact(self.content, FilledInfo)


@dataclass(frozen=True)
class ReadRequest:
    offer: Offer
    reader: Endpoint
    request: LeaseRequest
    purpose: str = 'PARSER_WARMUP'
    publication_sha256: str | None = None

    def __post_init__(self):
        _exact(self.offer, Offer)
        _exact(self.reader, Endpoint)
        _exact(self.request, LeaseRequest)
        if (type(self.purpose) is not str or len(self.purpose) > 32
                or self.purpose not in ('PARSER_WARMUP', 'METRIC_MAP')):
            _fail('unknown read purpose')
        if self.publication_sha256 is not None:
            _sha(self.publication_sha256)


@dataclass(frozen=True)
class ReadLease:
    key: ReadKey
    read_request: ReadRequest
    reserved_us: int

    def __post_init__(self):
        _exact(self.key, ReadKey)
        _exact(self.read_request, ReadRequest)
        r = self.read_request
        if self.key.write_key != r.offer.content.key or self.key.reader != r.reader:
            _fail('read lease differs from its original content/reader')
        r.request.check(self.reserved_us)
        _event(r.offer.content.completed_us, self.reserved_us)


@dataclass(frozen=True)
class ViewReturnFact:
    """lower가 모든 view 종료와 writable post1을 확인한 뒤만 만든다. 여기서는 형식만 검사한다."""

    lease: ReadLease
    views_ended_us: int
    writable_posted_us: int

    def __post_init__(self):
        _exact(self.lease, ReadLease)
        _u64(self.views_ended_us)
        _u64(self.writable_posted_us)
        if not self.lease.reserved_us <= self.views_ended_us <= self.writable_posted_us:
            _fail('view return must follow the original reservation and precede writable post')


@dataclass(frozen=True)
class RemoteReturnStatement:
    """등록 peer의 post 뒤 완료보고 진술. local 두 시각이나 실제 token 관측을 만들지 않는다."""

    lease: ReadLease
    completed_us: int

    def __post_init__(self):
        _exact(self.lease, ReadLease)
        _u64(self.completed_us)
        if self.completed_us < self.lease.reserved_us:
            _fail('remote completion must follow the original reservation')


@dataclass(frozen=True)
class ReplacementTicket:
    """원장이 보유한 접수 identity다. planned key는 아직 WriteGrant가 아니다."""

    old_content: FilledInfo
    request: LeaseRequest
    planned_key: WriteKey
    begun_us: int

    def __post_init__(self):
        _exact(self.old_content, FilledInfo)
        _exact(self.request, LeaseRequest)
        _exact(self.planned_key, WriteKey)
        old = self.old_content.key
        if (self.planned_key.scope != old.scope or self.planned_key.slot != old.slot
                or self.planned_key.generation != _next_generation(old.generation)):
            _fail('replacement must keep the slot/context and advance generation once')
        self.request.check(self.begun_us)
        _event(self.old_content.completed_us, self.begun_us)


@dataclass(frozen=True)
class ReplacementSyncFact:
    """한 외부 동기시도의 최종 진술. None/None 또는 consume만 완료는 partial failure다."""

    ticket: ReplacementTicket
    ready_consumed_us: int | None
    writable_posted_us: int | None
    finished_us: int

    def __post_init__(self):
        _exact(self.ticket, ReplacementTicket)
        _u64(self.finished_us)
        if self.finished_us < self.ticket.begun_us:
            _fail('replacement completion precedes begin')
        if self.ready_consumed_us is None:
            if self.writable_posted_us is not None:
                _fail('writable post cannot precede ready consumption')
        else:
            _u64(self.ready_consumed_us)
            if not self.ticket.begun_us <= self.ready_consumed_us <= self.finished_us:
                _fail('ready consumption is outside the original operation')
            if self.writable_posted_us is not None:
                _u64(self.writable_posted_us)
                if not self.ready_consumed_us <= self.writable_posted_us <= self.finished_us:
                    _fail('writable post must follow ready consumption')


class SlotState(Enum):
    FREE = 'FREE'
    WRITING = 'WRITING'
    READY = 'READY'
    IN_USE = 'IN_USE'


class LeaseStatus(Enum):
    APPLIED = 'APPLIED'
    CLEANUP_CORROBORATED = 'CLEANUP_CORROBORATED'
    DUPLICATE_NO_EFFECT = 'DUPLICATE_NO_EFFECT'
    UNKNOWN_NO_EFFECT = 'UNKNOWN_NO_EFFECT'
    REPLACEMENT_FAILED = 'REPLACEMENT_FAILED'


class ReplacementFault(Enum):
    PARTIAL = 'PARTIAL'
    EXPIRED = 'EXPIRED'
    FENCED = 'FENCED'
    LOCAL_COMMIT_FAILED = 'LOCAL_COMMIT_FAILED'


@dataclass(frozen=True)
class PendingReplacement:
    ticket: ReplacementTicket
    fact: ReplacementSyncFact | None = None

    def __post_init__(self):
        _exact(self.ticket, ReplacementTicket)
        if self.fact is not None:
            _exact(self.fact, ReplacementSyncFact)
            if self.fact.ticket is not self.ticket:
                _fail('pending completion is not for the exact ticket')


@dataclass(frozen=True)
class CleanupSummary:
    """마지막 정리1개. 반환은 먼저 받은 사실과 반대 종류1개만 보유한다."""

    fact: ViewReturnFact | RemoteReturnStatement | ReplacementSyncFact
    opposite: ViewReturnFact | RemoteReturnStatement | None = None

    def __post_init__(self):
        kind = type(self.fact)
        if kind is not ViewReturnFact and kind is not RemoteReturnStatement and kind is not ReplacementSyncFact:
            _fail('unsupported bounded cleanup summary')
        if self.opposite is None:
            return
        if kind is ReplacementSyncFact:
            _fail('replacement cleanup cannot have an opposite return statement')
        _exact(self.opposite, RemoteReturnStatement if kind is ViewReturnFact else ViewReturnFact)
        if self.fact.lease != self.opposite.lease:
            _fail('local and remote returns differ from the full original lease')
        local = self.fact if kind is ViewReturnFact else self.opposite
        remote = self.opposite if kind is ViewReturnFact else self.fact
        if local.writable_posted_us > remote.completed_us:
            _fail('remote report precedes the observed local writable post')


def _cleanup_return_lease(summary):
    # 반대 사실은 같은 원 lease이므로 history에 두 번 넣지 않는다.
    if summary is not None and type(summary.fact) is not ReplacementSyncFact:
        return summary.fact.lease
    return None


@dataclass(frozen=True)
class SlotSnapshot:
    """마지막 content 기록은 FREE에서도 남을 수 있다. 상태가 새 grant/read 허가를 결정한다."""

    generation: int = 0
    state: SlotState = SlotState.FREE
    write: WriteGrant | None = None
    filled: FilledInfo | None = None
    read: ReadLease | None = None
    pending: PendingReplacement | None = None
    cleanup: CleanupSummary | None = None

    def __post_init__(self):
        _u64(self.generation, 'generation')
        _exact(self.state, SlotState)
        for value, expected in ((self.write, WriteGrant), (self.filled, FilledInfo),
                                (self.read, ReadLease), (self.pending, PendingReplacement),
                                (self.cleanup, CleanupSummary)):
            if value is not None:
                _exact(value, expected)
        if self.write is not None and self.write.key.generation != self.generation:
            _fail('slot/write generation mismatch')
        if self.filled is not None and (self.write is None or self.filled.key != self.write.key):
            _fail('FILLED is not bound to the slot write record')
        if self.read is not None and (self.filled is None or self.read.read_request.offer.content != self.filled):
            _fail('read lease is not bound to the slot content')
        if self.state is SlotState.WRITING and (self.write is None or self.filled is not None
                                                or self.read is not None or self.pending is not None):
            _fail('invalid WRITING records')
        if self.state is SlotState.READY and (self.filled is None or self.read is not None):
            _fail('invalid READY records')
        if self.state is SlotState.IN_USE and (self.read is None or self.pending is not None):
            _fail('invalid IN_USE records')
        if self.state is SlotState.FREE and (self.read is not None or self.pending is not None):
            _fail('FREE cannot retain an active read/replacement')
        if self.pending is not None and (self.state is not SlotState.READY
                                         or self.pending.ticket.old_content != self.filled):
            _fail('replacement is not bound to unclaimed READY content')


@dataclass(frozen=True)
class LedgerSnapshot:
    scope: RawScope
    reader: Endpoint
    publication_sha256: str | None
    slots: tuple[SlotSnapshot, SlotSnapshot]
    fenced: bool
    retired: bool
    fault: ReplacementFault | None
    fenced_us: int | None

    def __post_init__(self):
        _exact(self.scope, RawScope)
        _exact(self.reader, Endpoint)
        if self.publication_sha256 is not None:
            _sha(self.publication_sha256)
        if type(self.slots) is not tuple or len(self.slots) != 2:
            _fail('snapshot must contain exactly two slots')
        for index, slot in enumerate(self.slots):
            _exact(slot, SlotSnapshot)
            if slot.write is not None and (slot.write.key.scope != self.scope or slot.write.key.slot != index):
                _fail('snapshot write differs from the registered slot/context')
            if slot.read is not None and slot.read.key.reader != self.reader:
                _fail('snapshot reader differs from the registered reader')
        if type(self.fenced) is not bool or type(self.retired) is not bool:
            _fail('snapshot flags must be exact bool')
        if self.retired and not self.fenced:
            _fail('retirement must retain a fence')
        if self.fault is not None:
            _exact(self.fault, ReplacementFault)
            if not self.fenced:
                _fail('protocol fault must retain a fence')
        if self.fenced_us is not None:
            _u64(self.fenced_us)
        if self.fenced != (self.fenced_us is not None):
            _fail('fence must keep its first event stamp')


@dataclass(frozen=True)
class LeaseResult:
    """원장 처리 결과. value/상태는 실제 메모리·token·출력 권위가 아니다."""

    status: LeaseStatus
    value: WriteGrant | ReadLease | ReplacementTicket | PendingReplacement | None = None

    def __post_init__(self):
        _exact(self.status, LeaseStatus)
        if self.status is LeaseStatus.CLEANUP_CORROBORATED and self.value is not None:
            _fail('cleanup corroboration carries no new lease or operation value')
        if self.value is not None and not any(type(self.value) is t for t in (
                WriteGrant, ReadLease, ReplacementTicket, PendingReplacement)):
            _fail('unsupported result record')


class RawLeaseLedger:
    """고정 arena2slot/A/M 문맥. owner exit·reader 교체·복원/정규화 API는 제공하지 않는다."""

    def __init__(self, scope, reader, *, publication_sha256=None):
        _exact(scope, RawScope)
        _exact(reader, Endpoint)
        if publication_sha256 is not None:
            _sha(publication_sha256)
        self._scope, self._reader, self._publication = scope, reader, publication_sha256
        self._slots = (SlotSnapshot(), SlotSnapshot())
        self._fenced, self._retired, self._fault, self._fenced_us = False, False, None, None
        self._last_recovery = None

    @property
    def snapshot(self):
        return LedgerSnapshot(self._scope, self._reader, self._publication, self._slots,
                              self._fenced, self._retired, self._fault, self._fenced_us)

    def _open(self):
        if self._fenced:
            _fail('arena use has been fenced; recovery is unsupported here')

    def _write_key(self, key):
        _exact(key, WriteKey)
        if key.scope != self._scope:
            _fail('write key differs from the registered raw context')
        return self._slots[key.slot]

    def _commit_slot(self, index, value):
        updated = tuple(value if i == index else item for i, item in enumerate(self._slots))
        self._slots = updated

    def _busy_writer(self):
        return any(s.state is SlotState.WRITING or s.pending is not None for s in self._slots)

    def _write_request_match(self, index, request, token, *, replacement=False):
        for slot in self._slots:
            if (slot.cleanup is not None and type(slot.cleanup.fact) is ReplacementSyncFact
                    and slot.cleanup.fact.ticket.request.request_id == request.request_id):
                ticket = slot.cleanup.fact.ticket
                if (replacement and ticket.planned_key.slot == index and ticket.request == request
                        and ticket.planned_key.write_token == token):
                    return ticket
                # 현재 write record가 같은 완료 replacement의 grant인 경우 아래에서 대조한다.
                if replacement or slot.write is None or slot.write.request.request_id != request.request_id:
                    _fail('write request ID conflicts with a retained replacement summary')
            if slot.pending is not None and slot.pending.ticket.request.request_id == request.request_id:
                ticket = slot.pending.ticket
                if (replacement and ticket.planned_key.slot == index and ticket.request == request
                        and ticket.planned_key.write_token == token):
                    return ticket
                _fail('write request ID conflicts with a retained replacement')
            if slot.write is not None and slot.write.request.request_id == request.request_id:
                grant = slot.write
                if not replacement and (grant.key.slot, grant.request, grant.key.write_token) == (index, request, token):
                    return grant
                _fail('write request ID conflicts with a retained write')
        return None

    def reserve_write(self, slot, request, write_token, *, now_us):
        _slot(slot)
        _exact(request, LeaseRequest)
        _uuid(write_token)
        request.check(now_us)
        self._open()
        duplicate = self._write_request_match(slot, request, write_token)
        if duplicate is not None:
            return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT, duplicate)
        if self._busy_writer() or self._slots[slot].state is not SlotState.FREE:
            _fail('producer already has an outstanding write or slot is not FREE')
        old = self._slots[slot]
        key = WriteKey(self._scope, slot, _next_generation(old.generation), write_token)
        grant = WriteGrant(key, request, now_us)
        updated = SlotSnapshot(key.generation, SlotState.WRITING, grant, cleanup=old.cleanup)
        result = LeaseResult(LeaseStatus.APPLIED, grant)
        self._commit_slot(slot, updated)
        return result

    def filled(self, info, *, now_us):
        _exact(info, FilledInfo)
        slot = self._write_key(info.key)
        _event(info.completed_us, now_us)
        if slot.filled is not None and slot.filled.key == info.key:
            if slot.filled != info:
                _fail('same write key has a different original FILLED summary')
            return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT)
        self._open()
        if slot.state is not SlotState.WRITING or slot.write.key != info.key:
            _fail('FILLED does not match an outstanding write')
        slot.write.request.check(now_us)
        if not slot.write.granted_us <= info.completed_us < slot.write.request.expires_us:
            _fail('FILLED completion is outside the original write operation')
        updated = replace(slot, state=SlotState.READY, filled=info)
        result = LeaseResult(LeaseStatus.APPLIED)
        self._commit_slot(info.key.slot, updated)
        return result

    def offer(self, slot, *, now_us):
        _slot(slot)
        _u64(now_us, 'now_us')
        self._open()
        value = self._slots[slot]
        if value.state is not SlotState.READY or value.pending is not None:
            _fail('only unclaimed READY can be offered')
        _event(value.filled.completed_us, now_us)
        return Offer(value.filled)

    def acquire(self, request, read_token, *, now_us):
        _exact(request, ReadRequest)
        _uuid(read_token)
        request.request.check(now_us)
        if request.purpose == 'METRIC_MAP':
            raise UnsupportedPurpose('METRIC_MAP requires a future exact pre-read metadata/full-job contract')
        slot = self._write_key(request.offer.content.key)
        if request.reader != self._reader:
            _fail('read request is not for the registered current M')
        if request.publication_sha256 is not None and request.publication_sha256 != self._publication:
            _fail('parser publication reference differs from the registered context')
        self._open()
        for existing in self._slots:
            retained = (() if existing.read is None else (existing.read,))
            returned = _cleanup_return_lease(existing.cleanup)
            if returned is not None:
                retained += (returned,)
            for lease in retained:
                if lease.read_request.request.request_id == request.request.request_id:
                    if lease.read_request != request or lease.key.read_token != read_token:
                        _fail('read request ID has a different original fingerprint')
                    return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT, lease)
        if (any(s.read is not None for s in self._slots) or slot.state is not SlotState.READY
                or slot.pending is not None or slot.filled != request.offer.content):
            _fail('reader already holds a lease or content is not unclaimed READY')
        key = ReadKey(request.offer.content.key, self._reader, read_token)
        lease = ReadLease(key, request, now_us)
        updated = replace(slot, state=SlotState.IN_USE, read=lease)
        result = LeaseResult(LeaseStatus.APPLIED, lease)
        self._commit_slot(key.write_key.slot, updated)
        return result

    def read_is_current(self, lease, *, now_us):
        """local key/사용권/원 read 기한만 판정한다. capture freshness/실 메모리 접근 허가가 아니다."""
        _exact(lease, ReadLease)
        _u64(now_us, 'now_us')
        slot = self._write_key(lease.key.write_key)
        return (not self._fenced and slot.state is SlotState.IN_USE and slot.read == lease
                and lease.reserved_us <= now_us < lease.read_request.request.expires_us)

    def write_is_current(self, grant, *, now_us):
        """local WRITING 예약과 실제 grant 하한만 확인한다. writable token/OS 사용 허가가 아니다."""
        _exact(grant, WriteGrant)
        _u64(now_us, 'now_us')
        slot = self._write_key(grant.key)
        return (not self._fenced and slot.state is SlotState.WRITING and slot.write == grant
                and grant.granted_us <= now_us < grant.request.expires_us)

    def release(self, fact, *, now_us):
        """실제 lower의 local 두 시각을 받는다. 만료/fence도 반환 정리를 막지 않는다."""
        _exact(fact, ViewReturnFact)
        return self._release_return(fact, fact.writable_posted_us, now_us)

    def release_remote(self, statement, *, now_us):
        """원격 완료 진술의 bookkeeping. peer·전송·실제 post 승인은 외부 actor 책임이다."""
        _exact(statement, RemoteReturnStatement)
        return self._release_return(statement, statement.completed_us, now_us)

    def _release_return(self, fact, event_us, now_us):
        slot = self._write_key(fact.lease.key.write_key)
        _event(event_us, now_us)
        previous_lease = _cleanup_return_lease(slot.cleanup)
        if previous_lease is not None and previous_lease.key == fact.lease.key:
            if previous_lease != fact.lease:
                _fail('same returned key has a different full original lease')
            for previous in (slot.cleanup.fact, slot.cleanup.opposite):
                if type(previous) is type(fact):
                    if previous != fact:
                        _fail('same returned lease has a different original completion fact')
                    return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT)
            # 첫 반대 종류 보충만 허용한다. 현재 generation/소유/교체 상태는 그대로다.
            summary = CleanupSummary(slot.cleanup.fact, fact)
            updated = replace(slot, cleanup=summary)
            result = LeaseResult(LeaseStatus.CLEANUP_CORROBORATED)
            self._commit_slot(fact.lease.key.write_key.slot, updated)
            return result
        if slot.read is not None and slot.read.key == fact.lease.key and slot.read != fact.lease:
            _fail('same read key has a different original request/context')
        if slot.state is not SlotState.IN_USE or slot.read != fact.lease:
            return LeaseResult(LeaseStatus.UNKNOWN_NO_EFFECT)
        updated = replace(slot, state=SlotState.FREE, read=None, cleanup=CleanupSummary(fact))
        result = LeaseResult(LeaseStatus.APPLIED)
        self._commit_slot(fact.lease.key.write_key.slot, updated)
        return result

    def begin_replace(self, slot, expected_key, request, write_token, *, now_us):
        _slot(slot)
        value = self._write_key(expected_key)
        if expected_key.slot != slot:
            _fail('replacement slot differs from the expected content')
        _exact(request, LeaseRequest)
        _uuid(write_token)
        request.check(now_us)
        self._open()
        duplicate = self._write_request_match(slot, request, write_token, replacement=True)
        if duplicate is not None:
            if duplicate.old_content.key != expected_key:
                _fail('same replacement request has different old content')
            return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT, duplicate)
        if (self._busy_writer() or value.state is not SlotState.READY or value.read is not None
                or value.filled.key != expected_key):
            _fail('replacement requires unclaimed READY and no outstanding producer operation')
        planned = WriteKey(self._scope, slot, _next_generation(value.generation), write_token)
        ticket = ReplacementTicket(value.filled, request, planned, now_us)
        updated = replace(value, pending=PendingReplacement(ticket))
        result = LeaseResult(LeaseStatus.APPLIED, ticket)
        self._commit_slot(slot, updated)
        return result

    def _replacement_fault(self, slot, fact, cause, now_us, *, original_slot=None):
        # 외부 token 작업 뒤 정상 상태로 rollback하지 않는다. 먼저 fence를 보존한다.
        self._fenced = True
        self._fault = self._fault or cause
        if self._fenced_us is None:
            self._fenced_us = now_us
        value = self._slots[slot] if original_slot is None else original_slot
        pending = PendingReplacement(value.pending.ticket, fact)
        self._commit_slot(slot, replace(value, pending=pending, cleanup=CleanupSummary(fact)))

    def complete_replace(self, ticket, fact, *, now_us):
        _exact(ticket, ReplacementTicket)
        _exact(fact, ReplacementSyncFact)
        if fact.ticket is not ticket:
            _fail('completion is not for the exact caller ticket')
        slot = self._write_key(ticket.old_content.key)
        _event(fact.finished_us, now_us)
        if slot.cleanup is not None and type(slot.cleanup.fact) is ReplacementSyncFact:
            previous = slot.cleanup.fact
            if previous.ticket is ticket:
                if previous != fact:
                    _fail('replacement has a different terminal synchronization fact')
                return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT)
        if slot.pending is None or slot.pending.ticket is not ticket:
            return LeaseResult(LeaseStatus.UNKNOWN_NO_EFFECT)
        if slot.pending.fact is not None:
            if slot.pending.fact != fact:
                _fail('replacement terminal fact cannot be replaced')
            return LeaseResult(LeaseStatus.DUPLICATE_NO_EFFECT)
        failure = (ReplacementFault.FENCED if self._fenced else
                   ReplacementFault.EXPIRED if now_us >= ticket.request.expires_us else
                   ReplacementFault.PARTIAL if fact.writable_posted_us is None else None)
        if failure is not None:
            self._replacement_fault(ticket.planned_key.slot, fact, failure, now_us)
            return LeaseResult(LeaseStatus.REPLACEMENT_FAILED, self._slots[ticket.planned_key.slot].pending)
        try:
            ticket.request.check(now_us)
            grant = WriteGrant(ticket.planned_key, ticket.request, now_us)
            updated = SlotSnapshot(grant.key.generation, SlotState.WRITING, grant, cleanup=CleanupSummary(fact))
            result = LeaseResult(LeaseStatus.APPLIED, grant)
            self._commit_slot(grant.key.slot, updated)
        except BaseException:
            self._replacement_fault(ticket.planned_key.slot, fact, ReplacementFault.LOCAL_COMMIT_FAILED,
                                    now_us, original_slot=slot)
            raise
        return result

    def rebind_reader(self, reader, evidence, *, now_us):
        """정규화된 quiescent slot을 같은 generation의 FREE로 바꾼다. token post는 하지 않는다."""
        _exact(reader, Endpoint)
        _exact(evidence, RawRecoveryEvidence)
        _exact(evidence.barrier, RawRecoveryBarrier)
        _event(evidence.normalized_us, now_us)
        self._open()
        if self._last_recovery == (reader, evidence):
            return False
        barrier = evidence.barrier
        if (barrier.scope != self._scope or barrier.old_reader != self._reader
                or reader.worker_instance == self._reader.worker_instance
                or reader.channel_epoch == self._reader.channel_epoch):
            _fail('reader recovery differs from original scope/old reader/new incarnation')
        if any(s.state is SlotState.WRITING or s.pending is not None for s in self._slots):
            _fail('producer is not quiescent for reader recovery')
        if self._last_recovery is not None and self._last_recovery[1].barrier.pause_request_id == barrier.pause_request_id:
            _fail('a pause recovery cannot be reinterpreted or renewed')
        if any(s.filled is not None and s.filled.completed_us > barrier.paused_us for s in self._slots):
            _fail('content completion follows the original producer pause')
        self._slots = tuple(replace(s, state=SlotState.FREE, read=None, pending=None) for s in self._slots)
        self._reader = reader
        self._last_recovery = reader, evidence
        return True

    def fence(self, *, now_us, retired=False):
        """사용권만 sticky 철회한다. WRITING/IN_USE/실 token 회수나 새 baseline 생성은 없다."""
        _u64(now_us, 'now_us')
        if type(retired) is not bool:
            _fail('retired must be an exact bool')
        changed = not self._fenced or retired and not self._retired
        self._fenced, self._retired = True, self._retired or retired
        if self._fenced_us is None:
            self._fenced_us = now_us
        return changed
