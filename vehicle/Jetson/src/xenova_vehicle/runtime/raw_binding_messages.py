"""RAW BIND/ACK의 정적 진술 검사. 실제 send·등록·순번·watch·READY를 소유하지 않는다.

원 packet bytes와 canonical body의 hash는 별개다. public constructor는 현재 caller
등록 문맥과 독립 profile, ACK의 원 BIND·원 시각을 요구한다. 내부 clock/IO가 없으며
실제 HELLO/release·fullsend·현재 revision·중복·staging 결합은 후속 actor의 책임이다.
"""

from dataclasses import dataclass
import re

from .lifecycle_messages import LifecyclePeer
from .lifecycle_policy import validate_state
from .local_envelope import StructuralPacket
from .raw_binding import RawBinding, RawBindingError, RawBindingProfile
from .raw_leases import Endpoint


U64_MAX = 2**64 - 1
POSITIVE_STATES = {
    'A': frozenset('STARTING ASSET_CHECK DEVICE_BOOT PIPELINE_START WARMUP STREAMING DEGRADED'.split()),
    'M': frozenset('STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED RECOVERING'.split()),
}
NEGATIVE_REASONS = frozenset(('UNSUPPORTED_PROFILE', 'ASSET_UNAVAILABLE', 'LOCAL_SETUP_FAILED'))


class RawBindMessageError(RawBindingError):
    """RAW typed 진술/원 문맥·수명이 맞지 않는다. 실제 수신 수락 결과는 아니다."""


def _fail(message):
    raise RawBindMessageError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _u64(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        _fail('expected exact u64 integer')


def _decimal(value, *, positive=False):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or re.fullmatch('0|[1-9][0-9]*', value) is None):
        _fail('expected canonical u64 decimal string')
    number = int(value)
    if number > U64_MAX or positive and number == 0:
        _fail('u64 string outside its range')
    return number


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        _fail('missing/unknown RAW payload fields')


@dataclass(frozen=True)
class RawBindContext:
    """등록 대상 peer와 A endpoint를 분리한다. release SHA는 BIND wire의 검사항목이 아니다."""

    peer: LifecyclePeer
    acquisition: Endpoint
    profile: RawBindingProfile

    def __post_init__(self):
        _exact(self.peer, LifecyclePeer)
        _exact(self.acquisition, Endpoint)
        _exact(self.profile, RawBindingProfile)
        peer, acquisition = self.peer, self.acquisition
        if peer.role not in ('A', 'M'):
            _fail('RAW BIND targets registered A/M only')
        if peer.role == 'A':
            if (peer.worker_instance, peer.channel_epoch) != (acquisition.worker_instance, acquisition.channel_epoch):
                _fail('A target differs from its registered endpoint')
        elif peer.worker_instance == acquisition.worker_instance or peer.channel_epoch == acquisition.channel_epoch:
            _fail('M worker and channel must each differ from registered A')


def _packet_fields(packet, context, *, ack):
    _exact(packet, StructuralPacket)
    _exact(context, RawBindContext)
    value = packet.fields
    peer = context.peer
    if (value['kind'] != ('BIND_ACK' if ack else 'BIND') or value['binding_domain'] != 'RAW'
            or value['service_instance'] != peer.service_instance
            or value['channel_epoch'] != peer.channel_epoch
            or value['sender_instance'] != (peer.worker_instance if ack else peer.service_instance)):
        _fail('RAW kind/direction/current registered peer differs')
    revision = _decimal(value['binding_revision'], positive=True)
    issued, expires = (_decimal(value[k]) for k in ('issued_j_us', 'expires_j_us'))
    if issued >= expires:
        _fail('RAW statement needs a nonempty original lifetime')
    return value, revision, issued, expires


class _PacketAccess:
    @property
    def raw(self):
        return self.packet.raw_bytes

    @property
    def fields(self):
        return self.packet.fields

    @property
    def payload(self):
        return self.packet.payload


@dataclass(frozen=True, init=False)
class RawBindMessage(_PacketAccess):
    """BIND 선언 하나. cap/5초는 정적 포함관계이며 현재 now나 실송신을 증명하지 않는다."""

    packet: StructuralPacket
    context: RawBindContext
    binding: RawBinding
    binding_revision: int
    issued_us: int
    expires_us: int
    request_id: str
    cap_us: int | None

    def __init__(self, packet, context, cap_us=None):
        value, revision, issued, expires = _packet_fields(packet, context, ack=False)
        payload = value['payload']
        _keys(payload, ('binding_revision', 'binding_sha256', 'binding'))
        if _decimal(payload['binding_revision'], positive=True) != revision:
            _fail('payload/outer RAW revision differs')
        if expires - issued > 5000000:
            _fail('BIND exceeds original issued plus five seconds')
        if cap_us is not None:
            _u64(cap_us)
            if expires > cap_us:
                _fail('BIND exceeds its external original cap')
        try:
            binding = RawBinding(payload['binding'])
            context.profile.check(binding)
        except RawBindingError as error:
            raise RawBindMessageError('invalid binding or independent profile mismatch') from error
        body = binding.fields
        if (body['service_instance'] != context.peer.service_instance
                or body['acquisition_worker'] != context.acquisition.worker_instance):
            _fail('binding service/acquisition differs from registered source')
        if type(payload['binding_sha256']) is not str or payload['binding_sha256'] != binding.sha256:
            _fail('BIND SHA must cover exactly canonical RawBinding')
        for name, item in (('packet', packet), ('context', context), ('binding', binding),
                           ('binding_revision', revision), ('issued_us', issued), ('expires_us', expires),
                           ('request_id', value['request_id']), ('cap_us', cap_us)):
            object.__setattr__(self, name, item)


@dataclass(frozen=True, init=False)
class RawBindAck(_PacketAccess):
    """원 BIND에 대한 수락/거절 진술. negative도 현재 source/registry/watch를 바꾸지 않는다."""

    packet: StructuralPacket
    original_bind: RawBindMessage
    context: RawBindContext
    binding: RawBinding
    binding_revision: int
    issued_us: int
    expires_us: int
    request_id: str
    accepted: bool
    state: str
    reason: str | None

    def __init__(self, packet, original_bind):
        _exact(original_bind, RawBindMessage)
        context = original_bind.context
        value, revision, issued, expires = _packet_fields(packet, context, ack=True)
        payload = value['payload']
        _keys(payload, ('binding_revision', 'binding_sha256', 'accepted', 'state', 'reason'))
        if (revision != original_bind.binding_revision
                or _decimal(payload['binding_revision'], positive=True) != revision
                or type(payload['binding_sha256']) is not str
                or payload['binding_sha256'] != original_bind.binding.sha256
                or value['request_id'] != original_bind.request_id):
            _fail('ACK differs from original BIND identity/revision/hash')
        if not original_bind.issued_us <= issued < expires <= original_bind.expires_us:
            _fail('ACK must remain inside the original BIND lifetime')
        accepted, state, reason = (payload[k] for k in ('accepted', 'state', 'reason'))
        _exact(accepted, bool)
        _exact(state, str)
        try:
            validate_state(context.peer.role, state)
        except ValueError as error:
            raise RawBindMessageError('unsupported reported role state') from error
        if accepted:
            if reason is not None or state not in POSITIVE_STATES[context.peer.role]:
                _fail('positive ACK needs active role state and null reason')
        elif type(reason) is not str or reason not in NEGATIVE_REASONS:
            _fail('negative ACK needs an explicit supported refusal reason')
        for name, item in (('packet', packet), ('original_bind', original_bind), ('context', context),
                           ('binding', original_bind.binding), ('binding_revision', revision),
                           ('issued_us', issued), ('expires_us', expires), ('request_id', value['request_id']),
                           ('accepted', accepted), ('state', state), ('reason', reason)):
            object.__setattr__(self, name, item)


def parse_raw_bind(packet, context, cap_us=None):
    return RawBindMessage(packet, context, cap_us)


def parse_raw_bind_ack(packet, original_bind):
    return RawBindAck(packet, original_bind)
