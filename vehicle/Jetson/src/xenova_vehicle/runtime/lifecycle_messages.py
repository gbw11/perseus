"""초기4종 lifecycle 진술과 현재 수신 순서를 검증한다. 실제 전송/준비/exit 권한은 없다.

LIFECYCLE revision0·capability0·null checkpoint는 이 배포 profile의 명시 선택이다.
peer는 lower adapter가 등록한 현재 child 문맥으로 양방향에 쓴다. receiver_role은
S 또는 해당 child role이며 S sender는 service UUID다. 별도 S-worker UUID는 없다.
OS peer 자체를 이 모듈이 확인하지 않는다.
시각은 호출자가 주는 J monotonic u64 us이고 내부 clock/I/O/서비스 동작은 없다.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
import re
from uuid import UUID

from .local_envelope import StructuralPacket
from .lifecycle_policy import validate_state


SUPPORTED_KINDS = frozenset(('HELLO', 'HEARTBEAT', 'STOP', 'STOPPED'))
U64_MAX = 2**64 - 1


class LifecycleMessageError(ValueError):
    """초기 profile 또는 현재 RX 수락 실패. 실패 호출은 accepted state를 바꾸지 않는다."""


def _fail(message):
    raise LifecycleMessageError(message)


def _u64(value, label='time'):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        _fail(label + ' must be a u64 integer')
    return value


def _decimal(value, *, positive=False):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or any(c < '0' or c > '9' for c in value)
            or len(value) > 1 and value[0] == '0'):
        _fail('expected canonical u64 decimal string')
    number = int(value)
    if number > U64_MAX or positive and number == 0:
        _fail('u64 string is outside its range')
    return number


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        _fail('expected canonical UUID')
    try:
        parsed = str(UUID(value))
    except ValueError as error:
        raise LifecycleMessageError('invalid UUID') from error
    if parsed != value:
        _fail('expected canonical lowercase UUID')


def _sha(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('expected lowercase release SHA256')


@dataclass(frozen=True)
class LifecyclePeer:
    """caller가 고정한 현재 child/channel/release. wire에서 자동 등록하지 않는다."""

    service_instance: str
    channel_epoch: str
    worker_instance: str
    role: str
    release_sha256: str

    def __post_init__(self):
        for value in (self.service_instance, self.channel_epoch, self.worker_instance):
            _uuid(value)
        if type(self.role) is not str or self.role not in ('A', 'M', 'D'):
            _fail('expected registered child role')
        _sha(self.release_sha256)


def _keys(payload, expected):
    if set(payload) != set(expected.split()):
        _fail('unsupported lifecycle payload keys')


def _context(peer, receiver_role, kind):
    """검사된 작은 scalar의 고정 profile 방향만 공유한다. RX 사건 수락은 하지 않는다."""
    if type(peer) is not LifecyclePeer:
        _fail('expected immutable registered peer')
    if type(receiver_role) is not str or receiver_role not in ('S', peer.role):
        _fail('receiver must be S or the registered child role')
    if kind not in SUPPORTED_KINDS:
        _fail('kind is unsupported by the initial lifecycle profile')
    if receiver_role == 'S':
        if kind not in ('HELLO', 'HEARTBEAT', 'STOPPED'):
            _fail('unsupported child-to-S direction')
        return peer.worker_instance
    if kind != 'STOP':
        _fail('unsupported S-to-child direction')
    return peer.service_instance


def _payload_profile(peer, kind, p, issued_us, expires_us):
    """bounded builtin payload의 네 kind 의미만 공유한다. native 입력 work guard는 caller 책임이다."""
    if kind == 'HELLO':
        _keys(p, 'role worker_instance release_sha256 capability_mask')
        _uuid(p['worker_instance'])
        _sha(p['release_sha256'])
        _decimal(p['capability_mask'])
        if (p['role'], p['worker_instance'], p['release_sha256'], p['capability_mask']) != (
                peer.role, peer.worker_instance, peer.release_sha256, '0'):
            _fail('HELLO differs from the pinned role/worker/release/capability profile')
    elif kind == 'HEARTBEAT':
        _keys(p, 'worker_state last_progress_sequence last_progress_j_us')
        try:
            validate_state(peer.role, p['worker_state'])
        except ValueError as error:
            raise LifecycleMessageError('HEARTBEAT state is not valid for this role') from error
        seq, time = p['last_progress_sequence'], p['last_progress_j_us']
        if (seq is None) != (time is None):
            _fail('HEARTBEAT progress fields must both be null or both present')
        if seq is not None:
            _decimal(seq)
            if _decimal(time) > issued_us:
                _fail('reported progress is after the original issue time')
    elif kind == 'STOP':
        _keys(p, 'stop_revision reason drain_deadline_j_us')
        _decimal(p['stop_revision'], positive=True)
        reason = p['reason']
        if (type(reason) is not str or not 1 <= len(reason) <= 32
                or any(ord(c) < 32 or ord(c) > 126 for c in reason)):
            _fail('STOP reason must be bounded printable ASCII')
        drain = _decimal(p['drain_deadline_j_us'])
        if not issued_us <= drain <= expires_us:
            _fail('STOP drain must remain inside the original lifetime')
    else:
        _keys(p, 'stop_revision leases_released device_closed checkpoint_job status')
        _decimal(p['stop_revision'], positive=True)
        if type(p['leases_released']) is not bool:
            _fail('leases_released must be an exact bool report')
        if peer.role == 'A':
            if type(p['device_closed']) is not bool:
                _fail('A device_closed must be an exact bool report')
        elif p['device_closed'] is not None:
            _fail('M/D device_closed must be null')
        if p['checkpoint_job'] is not None:
            _fail('checkpoint job references are unsupported by the initial profile')
        if type(p['status']) is not str or p['status'] not in ('STOPPED', 'FAILED'):
            _fail('unknown STOPPED report status')
        if p['status'] == 'STOPPED' and (not p['leases_released']
                                       or peer.role == 'A' and not p['device_closed']):
            _fail('successful cleanup report requires its role-specific statements')


def _profile(packet, peer, receiver_role):
    if type(packet) is not StructuralPacket or type(peer) is not LifecyclePeer:
        _fail('expected structural packet and immutable registered peer')
    f = packet.fields
    sender = _context(peer, receiver_role, f['kind'])
    if f['binding_domain'] != 'LIFECYCLE' or f['binding_revision'] != '0':
        _fail('initial lifecycle requires LIFECYCLE revision zero')
    if (f['service_instance'], f['channel_epoch'], f['sender_instance']) != (
            peer.service_instance, peer.channel_epoch, sender):
        _fail('packet does not match the current registered peer/channel')
    _payload_profile(peer, f['kind'], f['payload'], int(f['issued_j_us']), int(f['expires_j_us']))
    return f


@dataclass(frozen=True)
class LifecycleMessage:
    """형식·현재 peer 문맥을 검사한 producer 진술. 수신 기한·준비/exit/회수 성공과 별개다.

    constructor도 항상 profile을 검사한다. A device_closed=true는 미시도가 명시 확인된
    경우를 포함한 자원수명 정리 진술이며 변수None/constructor불명으로 생성하면 안 된다.
    이 parser가 producer 내부 사실이나 실제 전송·process exit를 검증하지는 않는다.
    """

    packet: StructuralPacket
    peer: LifecyclePeer
    receiver_role: str
    _fields: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, '_fields', _profile(self.packet, self.peer, self.receiver_role))

    @property
    def fields(self):
        return deepcopy(self._fields)

    @property
    def payload(self):
        return deepcopy(self._fields['payload'])

    @property
    def kind(self):
        return self._fields['kind']

    @property
    def sequence(self):
        return int(self._fields['message_sequence'])

    @property
    def issued_us(self):
        return int(self._fields['issued_j_us'])

    @property
    def expires_us(self):
        return int(self._fields['expires_j_us'])


def parse_lifecycle(packet, peer, receiver_role):
    """지원4kind의 정확 형식/current 문맥만 검사한다. now나 RX state를 바꾸지 않는다."""
    return LifecycleMessage(packet, peer, receiver_role)


@dataclass(frozen=True)
class StopSnapshot:
    """한 worker/channel의 원 STOP. first_sent는 외부 full-send 성공 사실의 lower bound다."""

    message: LifecycleMessage
    first_sent_us: int | None = None


@dataclass(frozen=True)
class ReceiverSnapshot:
    """유한 수신 상태. 객체의 private parsed state를 수정하지 않는 공유 계약이다."""

    peer: LifecyclePeer
    receiver_role: str
    highwater: int
    hello_seen: bool
    last_packet: StructuralPacket | None
    pending_stop: StopSnapshot | None
    stopped_report: LifecycleMessage | None


class RxDisposition(Enum):
    ACCEPTED_NEW = 'ACCEPTED_NEW'
    DUPLICATE_NO_EFFECT = 'DUPLICATE_NO_EFFECT'


@dataclass(frozen=True)
class ReceiveResult:
    """새 진술만 dispatch한다. 중복은 message=None이며 재실행할 입력이 없다."""

    disposition: RxDisposition
    message: LifecycleMessage | None


def _lifetime(message, now_us, cap_us=None):
    _u64(now_us, 'now_us')
    if cap_us is not None:
        _u64(cap_us, 'cap_us')
        if message.expires_us > cap_us:
            _fail('packet exceeds the registered original cap')
    if not message.issued_us <= now_us < message.expires_us:
        _fail('packet is future or outside its original lifetime')


class LifecycleReceiver:
    """단일 owner의 순수 admission. typed/current/lifetime 뒤 순번과 event gate를 검사한다.

    새 채널은 새 객체로 등록한다. 내부 상태는 실패 때 바뀌지 않으며 마지막 packet,
    HELLO flag와 원 STOP/보고 하나만 보관한다. 실제 전송/exit/lease 권위는 만들지 않는다.
    """

    def __init__(self, peer, receiver_role):
        if type(peer) is not LifecyclePeer:
            _fail('expected immutable registered peer')
        if type(receiver_role) is not str or receiver_role not in ('S', peer.role):
            _fail('receiver must be S or the registered child role')
        self._peer = peer
        self._receiver_role = receiver_role
        self._highwater = 0
        self._hello_seen = False
        self._last_packet = None
        self._pending_stop = None
        self._stopped_report = None

    @property
    def snapshot(self):
        return ReceiverSnapshot(self._peer, self._receiver_role, self._highwater,
                                self._hello_seen, self._last_packet,
                                self._pending_stop, self._stopped_report)

    def _outbound_stop(self, packet, now_us, cap_us=None):
        if self._receiver_role != 'S':
            _fail('only S registers outbound STOP facts')
        message = parse_lifecycle(packet, self._peer, self._peer.role)
        _lifetime(message, now_us, cap_us)
        return message

    def register_stop_intent(self, packet, *, now_us, cap_us=None):
        """S의 원 outbound STOP을 고정한다. True도 실제 송신 사실은 아니며 RX를 전진시키지 않는다."""
        message = self._outbound_stop(packet, now_us, cap_us)
        if self._pending_stop is not None:
            if packet.raw_bytes != self._pending_stop.message.packet.raw_bytes:
                _fail('active STOP cannot be replaced or renewed')
            return False
        self._pending_stop = StopSnapshot(message)
        return True

    def record_first_sent(self, packet, event_us, *, now_us):
        """외부 full-send 성공 후 그 성공 시도 직전 J lower bound를 등록한다.

        event_us는 post-return/등록 now가 아니다. EAGAIN/short send 때 호출하면 안 된다.
        이 API는 실제 send를 호출하거나 호출자의 성공 사실을 독립 검증하지 않는다.
        """
        message = self._outbound_stop(packet, now_us)
        _u64(event_us, 'event_us')
        pending = self._pending_stop
        if pending is None or packet.raw_bytes != pending.message.packet.raw_bytes:
            _fail('first send must match the original registered STOP')
        if not message.issued_us <= event_us <= now_us:
            _fail('first-send lower bound is future or before the original STOP')
        if pending.first_sent_us is not None:
            if pending.first_sent_us != event_us:
                _fail('first-send fact cannot be replaced')
            return False
        self._pending_stop = replace(pending, first_sent_us=event_us)
        return True

    def _stopped_origin(self, message):
        pending = self._pending_stop
        if pending is None or pending.first_sent_us is None:
            _fail('STOPPED requires the original STOP and actual first-send fact')
        original = pending.message
        if (message.fields['request_id'], message.payload['stop_revision']) != (
                original.fields['request_id'], original.payload['stop_revision']):
            _fail('STOPPED does not match the original STOP request/revision')
        if (message.issued_us < pending.first_sent_us
                or message.expires_us > original.expires_us):
            _fail('STOPPED exceeds its original send/lifetime bounds')

    def receive(self, packet, *, now_us, cap_us=None):
        """원 수명을 통과한 새 사건만 원자적으로 수락한다. 중복에는 dispatch할 message가 없다."""
        message = parse_lifecycle(packet, self._peer, self._receiver_role)
        _lifetime(message, now_us, cap_us)
        if message.kind == 'STOPPED':
            self._stopped_origin(message)

        if message.sequence < self._highwater:
            _fail('message sequence is older than the accepted highwater')
        if message.sequence == self._highwater:
            if self._last_packet is None or packet.raw_bytes != self._last_packet.raw_bytes:
                _fail('same sequence has different original bytes')
            return ReceiveResult(RxDisposition.DUPLICATE_NO_EFFECT, None)

        hello_seen, pending_stop = self._hello_seen, self._pending_stop
        stopped_report = self._stopped_report
        if message.kind == 'HELLO':
            if hello_seen or pending_stop is not None:
                _fail('HELLO is once per channel and cannot follow an active STOP')
            hello_seen = True
        elif message.kind == 'HEARTBEAT':
            if not hello_seen:
                _fail('HEARTBEAT requires an accepted HELLO')
        elif message.kind == 'STOP':
            if pending_stop is not None:
                _fail('active STOP cannot be replaced or renewed')
            pending_stop = StopSnapshot(message)
        else:
            if stopped_report is not None:
                _fail('STOPPED is one statement per original STOP')
            stopped_report = message

        # 모든 검사가 끝난 뒤에만 accepted state를 함께 전진시킨다.
        self._highwater, self._last_packet = message.sequence, packet
        self._hello_seen, self._pending_stop = hello_seen, pending_stop
        self._stopped_report = stopped_report
        return ReceiveResult(RxDisposition.ACCEPTED_NEW, message)
