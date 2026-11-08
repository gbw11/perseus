"""S 단일 owner의 raw 예약·token 교체·원 wire 중계.

OFFER는 읽기 권한이 아니다. ACQUIRE를 원장에 먼저 예약하고 같은 ACQUIRED draft를
전송하며, 원격 RELEASE에서는 writable을 다시 post하지 않는다. 장치/SDK는 열지 않는다.
"""

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import uuid

from .raw_binding_messages import RawBindMessage
from .local_envelope import StructuralPacket
from .raw_leases import (Endpoint, LeaseStatus, RawLeaseLedger, RawRecoveryBarrier,
                         RawRecoveryEvidence, RawScope, ReplacementSyncFact, SlotState)
from .raw_recovery import RawControlMessage
from .resident_transport import j_us
from .slot_cleanup import CleanupTimeLimits, ReleaseMessage
from .slot_messages import PAYLOAD_FIELDS, ReaderSlotContext, SlotMessage, SlotTimeLimits, WriterSlotContext


class BrokerError(ValueError):
    """원 parent/현재 예약/원 기한 또는 실제 token 전환이 맞지 않는다."""


@dataclass(frozen=True)
class _Draft:
    op: str
    payload: dict
    request_id: str
    issued_us: int
    expires_us: int
    parent: SlotMessage | None


class ResidentRawBroker:
    """같은 A와2slot의 예약. 실제 pause/exit/정규화 뒤 M incarnation만 교체할 수 있다."""

    def __init__(self, writer_bind, scope, reader, raw_tokens, *,
                 limits=SlotTimeLimits(150_000, 500_000, 150_000),
                 cleanup_limits=CleanupTimeLimits(1_000_000)):
        if (type(writer_bind) is not RawBindMessage or type(scope) is not RawScope
                or type(reader) is not Endpoint or type(limits) is not SlotTimeLimits
                or type(cleanup_limits) is not CleanupTimeLimits
                or type(raw_tokens) is not tuple or len(raw_tokens) != 2
                or any(type(pair) is not tuple or len(pair) != 2 for pair in raw_tokens)):
            raise BrokerError('broker requires the registered two-slot scope and token pairs')
        self.writer = WriterSlotContext(writer_bind, scope)
        self.reader = reader
        self.reader_context = None
        self.limits, self.cleanup_limits = limits, cleanup_limits
        self._tokens = raw_tokens
        self._ledger = RawLeaseLedger(scope, reader)
        self._request = self._writer_draft = self._reader_draft = None
        self._grants = [None, None]
        self._offers = [None, None]
        self._previous_offers = [None, None]
        self._active_offer = None
        self._reads = [deque(maxlen=2), deque(maxlen=2)]
        self._counts = Counter()
        self._quiescing = False
        self._paused = None
        self._last_reader_recovery = None

    def begin_pause(self):
        """미승인 REQUEST는 취소하고 기존 예약 GRANT만 배출한다. Device pause는 아직 아니다."""
        if self._ledger.snapshot.fenced:
            raise BrokerError('a fenced arena cannot enter reader-only recovery')
        if self._quiescing:
            return False
        self._quiescing = True
        self._paused = None
        if self._writer_draft is None:
            self._request = None
        if self._reader_draft is not None and self._reader_draft.op == 'OFFER':
            self._reader_draft = None
        self._active_offer = None
        return True

    @property
    def pause_ready(self):
        return self._quiescing and self._writer_draft is None

    def accept_paused(self, packet, original_pause, *, first_sent_us, now_us):
        if not self.pause_ready or type(original_pause) is not RawControlMessage or original_pause.op != 'PAUSE':
            raise BrokerError('PAUSED requires an original selected PAUSE after prior GRANT drafts')
        message = RawControlMessage(packet, self.writer.bind, original_pause)
        message.check_live(now_us)
        self._now(first_sent_us)
        if not original_pause.issued_us <= first_sent_us <= message.issued_us:
            raise BrokerError('PAUSED predates actual original PAUSE send')
        if any(s.state is SlotState.WRITING or s.pending is not None for s in self._ledger.snapshot.slots):
            raise BrokerError('producer PAUSED before its preceding writes were completed')
        if self._paused is not None and self._paused.raw != message.raw:
            raise BrokerError('original producer PAUSED statement cannot change')
        self._paused = message
        return message

    def recovery_barrier(self, old_reader_exited_us):
        if self._paused is None:
            raise BrokerError('actual producer PAUSED statement is absent')
        return RawRecoveryBarrier(self.writer.scope, self.reader, self._paused.pause_request_id,
            hashlib.sha256(self._paused.raw).hexdigest(), self._paused.issued_us, old_reader_exited_us)

    def rebind_reader(self, reader, evidence, *, now_us):
        self._now(now_us)
        if type(reader) is not Endpoint or type(evidence) is not RawRecoveryEvidence:
            raise BrokerError('expected actual reader incarnation and raw normalization evidence')
        if self._last_reader_recovery == (reader, evidence):
            return False
        if not self._quiescing or self._paused is None or type(evidence) is not RawRecoveryEvidence:
            raise BrokerError('reader recovery requires actual pause/exit/normalization evidence')
        if evidence.barrier != self.recovery_barrier(evidence.barrier.old_reader_exited_us):
            raise BrokerError('normalization differs from original producer pause and reader')
        changed = self._ledger.rebind_reader(reader, evidence, now_us=now_us)
        if changed:
            self.reader = reader
            self.reader_context = None
            self._request = self._writer_draft = self._reader_draft = None
            self._offers, self._previous_offers = [None, None], [None, None]
            self._active_offer = None
            self._counts['reader_recoveries'] += 1
            self._last_reader_recovery = reader, evidence
        return changed

    def resume(self):
        """S가 원 PAUSED에 대한 RESUME를 선택한 뒤 호출한다. 자체 전송/시간 갱신은 없다."""
        if not self._quiescing or self._paused is None or self.reader_context is None:
            raise BrokerError('resume requires recovered and rebound reader plus original pause')
        self._quiescing = False
        self._paused = None

    def bind_reader(self, reader_bind):
        """S가 원 M ACK의 actual send/current 검사를 마친 뒤 stable 원 BIND를 결합한다."""
        context = ReaderSlotContext(self.writer, reader_bind)
        if context.reader != self.reader:
            raise BrokerError('reader BIND differs from the pre-registered M endpoint')
        if self.reader_context is not None:
            if self.reader_context.bind.raw != reader_bind.raw:
                raise BrokerError('reader BIND cannot be replaced in this broker')
            return False
        self.reader_context = context
        return True

    @staticmethod
    def _now(now_us):
        if type(now_us) is not int or not 0 <= now_us < 2**64:
            raise BrokerError('expected current monotonic u64 microseconds')

    @staticmethod
    def _live(message, now_us):
        ResidentRawBroker._now(now_us)
        if not message.issued_us <= now_us < message.expires_us:
            raise BrokerError('statement is outside its original half-open lifetime')

    def receive(self, role, packet, *, now_us):
        """등록된 원 packet만 dispatch한다. stale notice는 새 grant로 고쳐 주지 않는다."""
        if type(packet) is not StructuralPacket or packet.kind != 'SLOT' or type(role) is not str or role not in ('A', 'M'):
            raise BrokerError('expected registered A/M structural SLOT packet')
        self._now(now_us)
        payload = packet.payload
        if type(payload) is not dict or set(payload) != PAYLOAD_FIELDS:
            raise BrokerError('expected exact thirteen-field SLOT payload')
        op = payload.get('op')
        if role == 'A' and op == 'REQUEST':
            message = SlotMessage(packet, self.writer, limits=self.limits)
            if self._quiescing:
                self._counts['requests_paused'] += 1
                return None
            self._live(message, now_us)
            if self._ledger.snapshot.fenced:
                raise BrokerError('new write request after fence')
            retained = [self._request] + [grant.parent for grant in self._grants if grant is not None]
            for previous in retained:
                if previous is not None and previous.request_id == message.request_id:
                    if previous.raw != message.raw:
                        raise BrokerError('write request ID changed its original bytes')
                    self._counts['request_duplicate'] += 1
                    return None
            if self._request is not None or any(s.state is SlotState.WRITING for s in self._ledger.snapshot.slots):
                raise BrokerError('producer already has an outstanding write')
            self._request = message
            self._counts['requests'] += 1
            return None
        if role == 'A' and op == 'FILLED':
            slot = payload.get('slot')
            if type(slot) is not int or slot not in (0, 1) or self._grants[slot] is None:
                raise BrokerError('FILLED has no original selected GRANT')
            message = SlotMessage(packet, self.writer, self._grants[slot], limits=self.limits)
            self._live(message, now_us)
            result = self._ledger.filled(message.record, now_us=now_us)
            self._counts['filled'] += result.status is LeaseStatus.APPLIED
            return result
        if role != 'M' or self.reader_context is None:
            raise BrokerError('unsupported role/op or M RAW BIND is absent')
        if op == 'ACQUIRE':
            if self._quiescing:
                self._counts['acquires_paused'] += 1
                return None
            slot = payload.get('slot')
            if type(slot) is not int or slot not in (0, 1):
                raise BrokerError('ACQUIRE has no registered slot')
            # 직전 parent 하나만 추가 보유한다. 옛 ACQUIRE를 새 generation의 OFFER로
            # 파싱하지 않으며, 더 오래된 unknown content도 새 예약으로 승격하지 않는다.
            names = ('arena_id', 'slot', 'generation', 'write_token', 'producer', 'consumer',
                     'raw_binding_sha256', 'publication_binding_sha256', 'payload_length', 'descriptor_sha256')
            offer = None
            for candidate in (self._offers[slot], self._previous_offers[slot]):
                if candidate is None:
                    continue
                expected = candidate.payload
                if all(type(payload[name]) is type(expected[name]) and payload[name] == expected[name] for name in names):
                    offer = candidate
                    break
            if offer is None:
                self._counts['stale_acquire'] += 1
                return None
            message = SlotMessage(packet, self.reader_context, offer, limits=self.limits)
            self._live(message, now_us)
            for queue in self._reads:
                for previous in queue:
                    if previous.request_id == message.request_id:
                        if previous.parent.raw != message.raw:
                            raise BrokerError('read request ID changed its original bytes')
                        self._counts['acquire_duplicate'] += 1
                        return None
            current = self._ledger.snapshot
            if (current.fenced or now_us >= offer.expires_us
                    or current.slots[slot].state is not SlotState.READY
                    or current.slots[slot].filled != message.record.offer.content):
                self._counts['stale_acquire'] += 1
                return None
            if self._reader_draft is not None or any(s.state is SlotState.IN_USE for s in current.slots):
                raise BrokerError('reader already holds an outstanding read')
            result = self._ledger.acquire(message.record, str(uuid.uuid4()), now_us=now_us)
            lease = result.value
            values = message.payload
            values.update(op='ACQUIRED', read_token=lease.key.read_token)
            self._reader_draft = _Draft('ACQUIRED', values, message.request_id,
                                        lease.reserved_us, message.expires_us, message)
            self._active_offer = None
            self._counts['reads_reserved'] += 1
            return result
        if op == 'RELEASE':
            # 원 Er/fence가 지나도 exact cleanup은 허용한다. 실제 post는 M이 이미 한 번 수행했다.
            for queue in self._reads:
                for parent in queue:
                    expected = parent.payload
                    expected['op'] = 'RELEASE'
                    if (parent.request_id == packet.fields['request_id']
                            and all(payload.get(name) == expected[name] for name in
                                    ('slot', 'generation', 'write_token', 'read_token'))):
                        message = ReleaseMessage(packet, parent, limits=self.cleanup_limits)
                        result = self._ledger.release_remote(message.record, now_us=now_us)
                        self._counts['released'] += result.status is LeaseStatus.APPLIED
                        self._counts['release_duplicate'] += result.status is LeaseStatus.DUPLICATE_NO_EFFECT
                        return result
            self._counts['release_unknown'] += 1
            return None
        raise BrokerError('unsupported raw broker operation')

    def _replace(self, slot, request, now_us):
        content = self._ledger.snapshot.slots[slot].filled
        ticket = self._ledger.begin_replace(slot, content.key, request.record, str(uuid.uuid4()),
                                            now_us=now_us).value
        consumed = posted = None
        error = None
        try:
            writable, ready = self._tokens[slot]
            if ready.acquire(False):
                consumed = j_us()
                writable.release()
                posted = j_us()
        except BaseException as failure:
            error = failure
            self._ledger.fence(now_us=j_us())
        finished = j_us()
        fact = ReplacementSyncFact(ticket, consumed, posted, finished)
        result = self._ledger.complete_replace(ticket, fact, now_us=finished)
        if result.status is not LeaseStatus.APPLIED:
            raise BrokerError('actual READY replacement failed; original ticket/fact is fenced') from error
        self._counts['replaced'] += 1
        return result.value

    def _reserve_writer(self, now_us):
        request = self._request
        if request is None or self._writer_draft is not None:
            return
        self._live(request, now_us)
        snapshot = self._ledger.snapshot
        if any(s.state is SlotState.WRITING for s in snapshot.slots):
            return
        free = next((i for i, slot in enumerate(snapshot.slots) if slot.state is SlotState.FREE), None)
        if free is not None:
            grant = self._ledger.reserve_write(free, request.record, str(uuid.uuid4()), now_us=now_us).value
        else:
            # 한 개의 안내 READY를 보호하면, M이 아직 받지 않은 OFFER와 교체가 경주하지 않는다.
            protected = None if self._active_offer is None else self._active_offer.record.content.key.slot
            if self._reader_draft is not None and self._reader_draft.op == 'OFFER':
                protected = self._reader_draft.payload['slot']
            candidates = [(slot.filled.completed_us, i) for i, slot in enumerate(snapshot.slots)
                          if slot.state is SlotState.READY and i != protected]
            if not candidates:
                return
            grant = self._replace(min(candidates)[1], request, now_us)
        values = request.payload
        values.update(op='GRANT', slot=grant.key.slot, generation=str(grant.key.generation),
                      write_token=grant.key.write_token)
        self._writer_draft = _Draft('GRANT', values, request.request_id,
                                    grant.granted_us, request.expires_us, request)

    def _send(self, draft, link, context, now_us):
        if now_us >= draft.expires_us:
            if draft.op == 'OFFER':
                self._counts['offer_expired_before_selection'] += 1
                return None, True
            self.fence(now_us)
            raise BrokerError('reserved outgoing statement expired before selection')
        try:
            packet = link.send('SLOT', draft.payload, domain='RAW', revision=self.writer.bind.binding_revision,
                               request_id=draft.request_id, issued_us=draft.issued_us, expires_us=draft.expires_us)
        except BaseException:
            self.fence(now_us)
            raise
        if packet is None:
            self._counts['send_busy'] += 1
            return None, False
        message = SlotMessage(packet, context, draft.parent, limits=self.limits)
        if draft.op == 'GRANT':
            self._grants[message.record.key.slot] = message
            self._request = None
            self._counts['grants_selected'] += 1
        elif draft.op == 'OFFER':
            slot = message.record.content.key.slot
            self._previous_offers[slot] = self._offers[slot]
            self._offers[slot] = message
            self._active_offer = message
            self._counts['offers_selected'] += 1
        else:
            self._reads[message.record.key.write_key.slot].append(message)
            self._counts['acquired_selected'] += 1
        return packet, True

    def pump(self, writer_link, reader_link=None, *, now_us):
        """수락되지 않은 draft는 그대로 보존한다. 반환 packet도 actual full-send 증거와 별개다."""
        self._now(now_us)
        selected = []
        if self._ledger.snapshot.fenced:
            return selected
        if self._active_offer is not None and now_us >= self._active_offer.expires_us:
            self._active_offer = None
        if not self._quiescing:
            self._reserve_writer(now_us)
        if self._writer_draft is not None:
            packet, done = self._send(self._writer_draft, writer_link, self.writer, now_us)
            if packet is not None:
                selected.append(packet)
            if done:
                self._writer_draft = None
        if self._quiescing or self.reader_context is None or reader_link is None:
            return selected
        if self._reader_draft is None and self._active_offer is None:
            slots = self._ledger.snapshot.slots
            if not any(slot.state is SlotState.IN_USE for slot in slots):
                candidates = [(slot.filled.completed_us, i) for i, slot in enumerate(slots)
                    if slot.state is SlotState.READY and now_us < slot.filled.completed_us + self.limits.offer_budget_us]
                if candidates:
                    _, index = max(candidates)
                    offer = self._ledger.offer(index, now_us=now_us)
                    info = offer.content
                    values = dict(op='OFFER', arena_id=info.key.scope.arena_id, slot=index,
                        generation=str(info.key.generation), write_token=info.key.write_token, read_token=None,
                        producer=info.key.scope.writer.worker_instance, consumer=self.reader.worker_instance,
                        raw_binding_sha256=info.key.scope.raw_binding_sha256, publication_binding_sha256=None,
                        purpose=None, payload_length=info.payload_length, descriptor_sha256=info.descriptor_sha256)
                    self._reader_draft = _Draft('OFFER', values, str(uuid.uuid4()), info.completed_us,
                                                info.completed_us + self.limits.offer_budget_us, None)
        if self._reader_draft is not None:
            packet, done = self._send(self._reader_draft, reader_link, self.reader_context, now_us)
            if packet is not None:
                selected.append(packet)
            if done:
                self._reader_draft = None
        return selected

    def fence(self, now_us):
        """새 사용만 중지한다. 원 예약·pending·read는 지우지 않고 exact RELEASE를 계속 받는다."""
        return self._ledger.fence(now_us=now_us)

    def snapshot(self):
        state = self._ledger.snapshot
        return dict(fenced=state.fenced, fault=None if state.fault is None else state.fault.value,
            write_paused=self._quiescing, producer_paused=self._paused is not None,
            reader_bound=self.reader_context is not None,
            slots=[dict(state=s.state.value, generation=s.generation, replacement=s.pending is not None)
                   for s in state.slots], write_request_pending=self._request is not None,
            writer_draft=None if self._writer_draft is None else self._writer_draft.op,
            reader_draft=None if self._reader_draft is None else self._reader_draft.op,
            counts=dict(self._counts))

    def summary(self):
        return self.snapshot()
