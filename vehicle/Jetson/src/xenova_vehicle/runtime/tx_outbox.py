"""순번 없는 초기 lifecycle 진술을 유한하게 보관하고 실제 packet 하나를 준비한다.

단일 owner의 순수 정책이다. 실제 send/clock/attempt/채널 은퇴·원 작업 ledger는 소유하지
않는다. 송신 결과 반환 전 다른 thread/signal callback이 변이하면 안 된다. 준비된 packet
포기는 local 참조 정리이며 실제 미송신/peer 적용/lease FREE를 증명하지 않는다.
"""

from dataclasses import dataclass, field
from enum import Enum

from .lifecycle_messages import (LifecycleMessageError, LifecyclePeer, U64_MAX,
                                 _context, _payload_profile, _u64, _uuid)
from .local_envelope import encode_structure
from .mailbox import PendingTransmission, PendingTx


class TxOutboxError(ValueError):
    """draft/현재 문맥/원 수명 또는 준비 실패. 실패한 prepare는 전체 상태를 보존한다."""


class SequenceExhausted(TxOutboxError):
    """현재 sender/channel의 MAX를 이미 발급했다. 새 channel 등록은 외부 책임이다."""


def _time(value, label):
    try:
        return _u64(value, label)
    except LifecycleMessageError as error:
        raise TxOutboxError(str(error)) from error


def _flat_copy(payload):
    """작은 builtin scalar를 검사하면서 소유 사본을 만든다. 사용자 복사/비교 hook은 없다."""
    if type(payload) is not dict or len(payload) > 5:
        raise TxOutboxError('initial draft payload must be an exact flat dict of at most five keys')
    owned = {}
    for key, value in payload.items():
        if type(key) is not str or len(key) > 64:
            raise TxOutboxError('draft key must be an exact bounded string')
        value_type = type(value)
        if value_type is str:
            if len(value) > 64:
                raise TxOutboxError('draft string exceeds the native work bound')
        elif value_type is not bool and value is not None:
            raise TxOutboxError('draft payload supports only exact string/bool/null scalars')
        owned[key] = value
    return owned


@dataclass(frozen=True, init=False, eq=False)
class LifecycleDraft:
    """원 request/시각/cap의 소유 사본. 순번·wire bytes는 아직 없고 constructor 우회는 없다.

    fields는 실제 sequence가 없는11field 사본이다. 입력 dict의 호출 중 동시변경과
    private state 직접 수정은 지원하지 않는다. 형식 성공은 producer 출력 권한이 아니다.
    """

    peer: LifecyclePeer
    receiver_role: str
    kind: str
    request_id: str
    issued_us: int
    expires_us: int
    cap_us: int | None
    _payload: dict = field(repr=False)

    def __init__(self, peer, receiver_role, kind, request_id, issued_us, expires_us, payload,
                 *, cap_us=None):
        if type(peer) is not LifecyclePeer:
            raise TxOutboxError('expected immutable registered peer')
        if type(receiver_role) is not str or len(receiver_role) != 1:
            raise TxOutboxError('expected bounded receiver role')
        if type(kind) is not str or len(kind) > 64:
            raise TxOutboxError('expected bounded exact kind string')
        if type(request_id) is not str or len(request_id) != 36:
            raise TxOutboxError('expected canonical request UUID')
        _time(issued_us, 'issued_us')
        _time(expires_us, 'expires_us')
        if cap_us is not None:
            _time(cap_us, 'cap_us')
        if issued_us >= expires_us or cap_us is not None and expires_us > cap_us:
            raise TxOutboxError('outgoing draft requires a nonempty original lifetime within its cap')
        owned = _flat_copy(payload)
        try:
            _uuid(request_id)
            _context(peer, receiver_role, kind)
            _payload_profile(peer, kind, owned, issued_us, expires_us)
        except LifecycleMessageError as error:
            raise TxOutboxError(str(error)) from error
        for name, value in (('peer', peer), ('receiver_role', receiver_role), ('kind', kind),
                            ('request_id', request_id), ('issued_us', issued_us),
                            ('expires_us', expires_us), ('cap_us', cap_us), ('_payload', owned)):
            object.__setattr__(self, name, value)

    @property
    def payload(self):
        return self._payload.copy()

    @property
    def fields(self):
        return dict(version=1, service_instance=self.peer.service_instance,
                    channel_epoch=self.peer.channel_epoch,
                    sender_instance=_context(self.peer, self.receiver_role, self.kind),
                    kind=self.kind, binding_domain='LIFECYCLE', binding_revision='0',
                    request_id=self.request_id, issued_j_us=str(self.issued_us),
                    expires_j_us=str(self.expires_us), payload=self.payload)

    @property
    def fingerprint(self):
        return (self.peer, self.receiver_role, self.kind, self.request_id,
                self.issued_us, self.expires_us, self.cap_us, tuple(sorted(self._payload.items())))


class TxPutResult(Enum):
    ACCEPTED = 'ACCEPTED'
    REPLACED_HEARTBEAT = 'REPLACED_HEARTBEAT'
    DUPLICATE_NO_EFFECT = 'DUPLICATE_NO_EFFECT'
    BUSY_GENERAL = 'BUSY_GENERAL'
    BUSY_TERMINAL = 'BUSY_TERMINAL'

    @property
    def accepted(self):
        return self in (TxPutResult.ACCEPTED, TxPutResult.REPLACED_HEARTBEAT)

    def __bool__(self):
        return self.accepted


class TxStatus(Enum):
    EMPTY = 'EMPTY'
    PREPARED_NEW = 'PREPARED_NEW'
    PENDING_CURRENT = 'PENDING_CURRENT'
    PENDING_EXPIRED = 'PENDING_EXPIRED'
    PENDING_CAP_EXCEEDED = 'PENDING_CAP_EXCEEDED'


class DiscardCause(Enum):
    EXPIRED = 'EXPIRED'
    CAP_EXCEEDED = 'CAP_EXCEEDED'
    FENCED = 'FENCED'
    CANCELLED = 'CANCELLED'


@dataclass(frozen=True)
class DraftDrop:
    draft: LifecycleDraft
    cause: DiscardCause


@dataclass(frozen=True)
class TxPreparation:
    """pending status는 송신 검토 입력이며 실제 권한/송신 성공/원 job 완료가 아니다."""

    status: TxStatus
    handle: PendingTransmission | None
    drops: tuple[DraftDrop, ...] = ()


@dataclass(frozen=True)
class PendingDrop:
    handle: PendingTransmission
    draft: LifecycleDraft
    cause: DiscardCause


@dataclass(frozen=True)
class TxSnapshot:
    peer: LifecyclePeer
    receiver_role: str
    next_sequence: int | None
    general: tuple[LifecycleDraft, ...]
    terminal: tuple[LifecycleDraft, ...]
    heartbeat: LifecycleDraft | None
    pending: PendingTransmission | None
    pending_draft: LifecycleDraft | None


def _next_after(sequence):
    """발급 성공 뒤의 순수 산술. production seed/reset API가 아니다."""
    _time(sequence, 'sequence')
    if sequence == 0:
        raise TxOutboxError('message sequence starts at one')
    return None if sequence == U64_MAX else sequence + 1


def _drop_cause(draft, now_us, cap_us):
    if now_us < draft.issued_us:
        raise TxOutboxError('draft issue time is in the future')
    if now_us >= draft.expires_us:
        return DiscardCause.EXPIRED
    if cap_us is not None and draft.expires_us > cap_us:
        return DiscardCause.CAP_EXCEEDED
    return None


class TxOutbox:
    """고정 sender/channel/방향당 general8/HB1/terminal2+pending1. 전역 은퇴 검사는 외부 책임이다.

    held request_id만 중복 검사한다. 새 HB snapshot에는 caller의 실제 새 request ID가
    필요하다. 완료/포기 뒤 원 bytes 재답·과거 작업 중복 ledger는 제공하지 않는다.
    """

    def __init__(self, peer, receiver_role):
        if (type(peer) is not LifecyclePeer or type(receiver_role) is not str
                or len(receiver_role) != 1):
            raise TxOutboxError('expected fixed registered peer and direction')
        if receiver_role not in ('S', peer.role):
            raise TxOutboxError('invalid fixed direction')
        self._peer, self._receiver_role = peer, receiver_role
        self._general, self._terminal, self._heartbeat = (), (), None
        self._pending, self._pending_draft = PendingTx(), None
        self._next_sequence = 1

    @property
    def snapshot(self):
        return TxSnapshot(self._peer, self._receiver_role, self._next_sequence,
                          self._general, self._terminal, self._heartbeat,
                          self._pending.peek(), self._pending_draft)

    @property
    def counts(self):
        return dict(general=len(self._general), terminal=len(self._terminal),
                    heartbeat=int(self._heartbeat is not None), pending=int(self._pending.peek() is not None))

    @property
    def encoded_held_bytes(self):
        """queued draft에는 encoded bytes가 없다. native 객체/외부 사본 overhead와 구분한다."""
        return self._pending.held_bytes

    def _queued(self):
        return self._terminal + self._general + (() if self._heartbeat is None else (self._heartbeat,))

    def _current(self, draft):
        if type(draft) is not LifecycleDraft:
            raise TxOutboxError('outbox requires a validated unsequenced draft')
        if draft.peer != self._peer or draft.receiver_role != self._receiver_role:
            raise TxOutboxError('draft differs from the fixed peer/channel/direction')

    @staticmethod
    def _predicate_time(now_us, cap_us):
        _time(now_us, 'now_us')
        if cap_us is not None:
            _time(cap_us, 'cap_us')

    def put(self, draft, *, now_us, cap_us=None):
        """원 수명/현재 held-ID 판정 뒤에만 새 요청 접수·HB 교체를 한다."""
        self._predicate_time(now_us, cap_us)
        self._current(draft)
        if _drop_cause(draft, now_us, cap_us) is not None:
            raise TxOutboxError('put is outside the original lifetime/current cap')
        held = self._queued() + (() if self._pending_draft is None else (self._pending_draft,))
        for previous in held:
            if previous.request_id == draft.request_id:
                if previous.fingerprint != draft.fingerprint:
                    raise TxOutboxError('held request ID has a different original fingerprint')
                return TxPutResult.DUPLICATE_NO_EFFECT
        if draft.kind == 'HEARTBEAT':
            outcome = TxPutResult.ACCEPTED if self._heartbeat is None else TxPutResult.REPLACED_HEARTBEAT
            self._heartbeat = draft
            return outcome
        terminal = draft.kind in ('STOP', 'STOPPED')
        target, limit = (self._terminal, 2) if terminal else (self._general, 8)
        if len(target) == limit:
            return TxPutResult.BUSY_TERMINAL if terminal else TxPutResult.BUSY_GENERAL
        if terminal:
            self._terminal = target + (draft,)
        else:
            self._general = target + (draft,)
        return TxPutResult.ACCEPTED

    def prepare(self, *, now_us, cap_us=None):
        """현재 pending 관찰 또는 queue plan→실 encode→한 commit. 실패면 purge도 수행하지 않는다."""
        self._predicate_time(now_us, cap_us)
        pending = self._pending.peek()
        if pending is not None:
            cause = _drop_cause(self._pending_draft, now_us, cap_us)
            status = (TxStatus.PENDING_EXPIRED if cause is DiscardCause.EXPIRED else
                      TxStatus.PENDING_CAP_EXCEEDED if cause is DiscardCause.CAP_EXCEEDED else
                      TxStatus.PENDING_CURRENT)
            return TxPreparation(status, pending)

        drops, remaining = [], []
        for draft in self._queued():
            cause = _drop_cause(draft, now_us, cap_us)
            if cause is None:
                remaining.append(draft)
            else:
                drops.append(DraftDrop(draft, cause))
        if not remaining:
            result = TxPreparation(TxStatus.EMPTY, None, tuple(drops))
            self._general, self._terminal, self._heartbeat = (), (), None
            return result
        if self._next_sequence is None:
            raise SequenceExhausted('current sender/channel has no sequence left')

        candidate = remaining[0]
        fields = candidate.fields
        fields['message_sequence'] = str(self._next_sequence)
        packet = encode_structure(fields)
        holder = PendingTx()
        if not holder.accept(packet) or holder.peek() is None:
            raise TxOutboxError('local pending acceptance failed')
        # 새 holder는 commit까지 현재 state에 넣지 않아 실패 때 purge만 남기지 않는다.
        survivors = remaining[1:]
        general = tuple(d for d in survivors if d.kind == 'HELLO')
        terminal = tuple(d for d in survivors if d.kind in ('STOP', 'STOPPED'))
        heartbeat = next((d for d in survivors if d.kind == 'HEARTBEAT'), None)
        next_sequence = _next_after(self._next_sequence)
        result = TxPreparation(TxStatus.PREPARED_NEW, holder.peek(), tuple(drops))
        self._general, self._terminal, self._heartbeat = general, terminal, heartbeat
        self._pending, self._pending_draft = holder, candidate
        self._next_sequence = next_sequence
        return result

    def finish(self, expected_handle):
        """외부 full-send 성공 뒤의 bookkeeping. 늦은 실제 결과도 새 dispatch 허가와 구분한다."""
        if not self._pending.finish(expected_handle):
            return False
        self._pending_draft = None
        return True

    def abandon(self, expected_handle, *, cause):
        """exact pending 참조만 포기한다. 원 STOP latch/cleanup budget·번호를 되돌리지 않는다.

        cause는 caller의 local 포기 진술이며 실제 미송신 증명이 아니다. in-flight 변이 금지,
        short-send/불명 결과와 원STOP 수습은 future 동기 adapter/actor 책임이다.
        """
        if type(cause) is not DiscardCause:
            raise TxOutboxError('expected explicit local discard cause')
        current = self._pending.peek()
        if current is None or expected_handle is not current:
            return None
        result = PendingDrop(current, self._pending_draft, cause)
        self._pending, self._pending_draft = PendingTx(), None
        return result
