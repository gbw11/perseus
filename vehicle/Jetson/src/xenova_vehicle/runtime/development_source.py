"""같은 Jetson A-dev source의 명시 descriptor와 RAW BIND/ACK 진술을 검사한다.

물리 RawBinding과 별개인 닫힌 local variant다. 내부 clock/I/O/SDK가 없으며 실제
peer 등록·same-host 배치·실송신·현재 revision/순번·ACK 채택은 actor가 확인한다.
이 타입이나 J endpoint pair는 geometry/Scene/World/추진 사용권을 만들지 않는다.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID

from .lifecycle_messages import LifecyclePeer
from .lifecycle_policy import validate_state
from .local_envelope import StructuralPacket
from .raw_binding_messages import NEGATIVE_REASONS, POSITIVE_STATES
from .raw_leases import Endpoint


PROFILE = 'development-owned-source-1'
DOMAIN = 'DEVELOPMENT_SYNTHETIC'
MAX_BINDING_BYTES = 2048
U64_MAX = 2**64 - 1
FIELDS = frozenset(('profile', 'service_instance', 'producer_instance', 'capture_epoch',
    'stream_instances', 'clock_epoch', 'calibration_model_sha256', 'motion_profile_sha256',
    'calibration_profile_sha256', 'nn_manifest_sha256'))
STREAMS = ('rgb_video', 'depth_right')
REFS = ('calibration_model_sha256', 'motion_profile_sha256', 'calibration_profile_sha256')


class DevelopmentSourceError(ValueError):
    """개발 source의 원 형식·독립 pin·peer·기한이 맞지 않는다."""


def _exact(value, kind):
    if type(value) is not kind:
        raise DevelopmentSourceError('expected exact ' + kind.__name__)


def _keys(value, names):
    if (type(value) is not dict or len(value) != len(names)
            or any(type(key) is not str for key in value) or set(value) != set(names)):
        raise DevelopmentSourceError('missing or unknown development source fields')


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        raise DevelopmentSourceError('canonical nonzero source UUID required')
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise DevelopmentSourceError('invalid source UUID') from error
    if str(parsed) != value or not parsed.int:
        raise DevelopmentSourceError('canonical nonzero source UUID required')


def _sha(value):
    if (type(value) is not str or len(value) != 64
            or any(c not in '0123456789abcdef' for c in value)):
        raise DevelopmentSourceError('lowercase source SHA256 required')


def _uint(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise DevelopmentSourceError('exact source uint64 required')
    return value


def _decimal(value, *, positive=False):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or any(c not in '0123456789' for c in value)
            or len(value) > 1 and value[0] == '0'):
        raise DevelopmentSourceError('canonical source uint64 string required')
    number = int(value)
    if number > U64_MAX or positive and number == 0:
        raise DevelopmentSourceError('source uint64 string outside range')
    return number


@dataclass(frozen=True, init=False)
class DevelopmentSourceBinding:
    """exact10 descriptor의 canonical bytes다. 파일 실존이나 실제 등록을 증명하지 않는다.

    nn_manifest_sha256=None은 명시 NN-absent 값이다. REQUIRED 소비자는 별도로 거절한다.
    model/motion/local calibration 참조는 서로 alias하지 않으며 SDK bridge 필드는 없다.
    """

    canonical_bytes: bytes
    sha256: str

    def __init__(self, fields):
        _keys(fields, FIELDS)
        if type(fields['profile']) is not str or fields['profile'] != PROFILE:
            raise DevelopmentSourceError('unsupported development source profile')
        for name in ('service_instance', 'producer_instance', 'capture_epoch', 'clock_epoch'):
            _uuid(fields[name])
        streams = fields['stream_instances']
        _keys(streams, STREAMS)
        for value in streams.values():
            _uuid(value)
        if len(set(streams.values())) != len(STREAMS):
            raise DevelopmentSourceError('development camera streams must have distinct UUIDs')
        for name in REFS:
            _sha(fields[name])
        if fields['nn_manifest_sha256'] is not None:
            _sha(fields['nn_manifest_sha256'])
        own = {name: fields[name] for name in FIELDS if name != 'stream_instances'}
        own['stream_instances'] = {name: streams[name] for name in STREAMS}
        raw = json.dumps(own, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                         allow_nan=False).encode('ascii')
        if len(raw) > MAX_BINDING_BYTES:
            raise DevelopmentSourceError('development source descriptor exceeds2048 bytes')
        object.__setattr__(self, 'canonical_bytes', raw)
        object.__setattr__(self, 'sha256', sha256(raw).hexdigest())

    @property
    def fields(self):
        """원 descriptor와 공유하지 않는 작은 JSON 사본이다."""
        return json.loads(self.canonical_bytes)

    @property
    def source_id(self):
        return 'sim-wall:' + self.fields['capture_epoch']

    @property
    def clock_id(self):
        return 'jetson-monotonic:' + self.fields['clock_epoch']

    @property
    def domain(self):
        return DOMAIN


@dataclass(frozen=True)
class DevelopmentBindContext:
    """caller의 실제 peer/A endpoint와 S가 독립 고정한 descriptor를 대조한다.

    same-host A-dev 배치가 선행조건이다. 이 구조 검사가 OS host/peer를 관측하지는 않는다.
    M 대상 BIND에서도 capture producer는 등록 A endpoint이며 M-owned source는 지원하지 않는다.
    """

    peer: LifecyclePeer
    acquisition: Endpoint
    expected_binding: DevelopmentSourceBinding

    def __post_init__(self):
        _exact(self.peer, LifecyclePeer)
        _exact(self.acquisition, Endpoint)
        _exact(self.expected_binding, DevelopmentSourceBinding)
        peer, acquisition = self.peer, self.acquisition
        for value in (peer.service_instance, peer.worker_instance, peer.channel_epoch,
                      acquisition.worker_instance, acquisition.channel_epoch):
            _uuid(value)
        if peer.role not in ('A', 'M'):
            raise DevelopmentSourceError('development RAW targets registered A or M only')
        if peer.role == 'A':
            if (peer.worker_instance, peer.channel_epoch) != (acquisition.worker_instance, acquisition.channel_epoch):
                raise DevelopmentSourceError('A-dev target differs from its registered endpoint')
        elif peer.worker_instance == acquisition.worker_instance or peer.channel_epoch == acquisition.channel_epoch:
            raise DevelopmentSourceError('M reader must differ from A-dev worker and channel')
        body = self.expected_binding.fields
        if (body['service_instance'], body['producer_instance']) != (peer.service_instance, acquisition.worker_instance):
            raise DevelopmentSourceError('independent descriptor differs from registered service or A-dev producer')


def _packet_fields(packet, context, *, ack):
    _exact(packet, StructuralPacket)
    _exact(context, DevelopmentBindContext)
    value = packet.fields
    peer = context.peer
    if (value['kind'] != ('BIND_ACK' if ack else 'BIND') or value['binding_domain'] != 'RAW'
            or value['service_instance'] != peer.service_instance or value['channel_epoch'] != peer.channel_epoch
            or value['sender_instance'] != (peer.worker_instance if ack else peer.service_instance)):
        raise DevelopmentSourceError('development RAW kind, direction or peer differs')
    revision = _decimal(value['binding_revision'], positive=True)
    issued, expires = (_decimal(value[name]) for name in ('issued_j_us', 'expires_j_us'))
    if issued >= expires:
        raise DevelopmentSourceError('development RAW statement requires an original nonempty lifetime')
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
class DevelopmentBindMessage(_PacketAccess):
    """개발 body만 허용하는 원 RAW BIND 진술. cap/5초는 정적 포함관계다."""

    packet: StructuralPacket
    context: DevelopmentBindContext
    binding: DevelopmentSourceBinding
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
            raise DevelopmentSourceError('development BIND payload revision differs')
        if expires - issued > 5_000_000:
            raise DevelopmentSourceError('development BIND exceeds original five-second lifetime')
        if cap_us is not None and expires > _uint(cap_us):
            raise DevelopmentSourceError('development BIND exceeds external original cap')
        binding = DevelopmentSourceBinding(payload['binding'])
        if binding.canonical_bytes != context.expected_binding.canonical_bytes:
            raise DevelopmentSourceError('development BIND differs from independently pinned descriptor')
        if type(payload['binding_sha256']) is not str or payload['binding_sha256'] != binding.sha256:
            raise DevelopmentSourceError('development BIND SHA does not cover its canonical body')
        for name, item in dict(packet=packet, context=context, binding=binding, binding_revision=revision,
                issued_us=issued, expires_us=expires, request_id=value['request_id'], cap_us=cap_us).items():
            object.__setattr__(self, name, item)


@dataclass(frozen=True, init=False)
class DevelopmentBindAck(_PacketAccess):
    """원 개발 BIND의 수락/거절 진술. 실제 ACK 송수신·현재 채택·준비 권위는 없다."""

    packet: StructuralPacket
    original_bind: DevelopmentBindMessage
    context: DevelopmentBindContext
    binding: DevelopmentSourceBinding
    binding_revision: int
    issued_us: int
    expires_us: int
    request_id: str
    accepted: bool
    state: str
    reason: str | None

    def __init__(self, packet, original_bind):
        _exact(original_bind, DevelopmentBindMessage)
        context = original_bind.context
        value, revision, issued, expires = _packet_fields(packet, context, ack=True)
        payload = value['payload']
        _keys(payload, ('binding_revision', 'binding_sha256', 'accepted', 'state', 'reason'))
        if (revision != original_bind.binding_revision or _decimal(payload['binding_revision'], positive=True) != revision
                or type(payload['binding_sha256']) is not str or payload['binding_sha256'] != original_bind.binding.sha256
                or value['request_id'] != original_bind.request_id):
            raise DevelopmentSourceError('development ACK changed original BIND identity, revision or SHA')
        if not original_bind.issued_us <= issued < expires <= original_bind.expires_us:
            raise DevelopmentSourceError('development ACK must fit original BIND lifetime')
        accepted, state, reason = (payload[name] for name in ('accepted', 'state', 'reason'))
        _exact(accepted, bool)
        _exact(state, str)
        try:
            validate_state(context.peer.role, state)
        except ValueError as error:
            raise DevelopmentSourceError('unsupported development role state') from error
        if accepted:
            if reason is not None or state not in POSITIVE_STATES[context.peer.role]:
                raise DevelopmentSourceError('positive development ACK requires active state and null reason')
        elif type(reason) is not str or reason not in NEGATIVE_REASONS:
            raise DevelopmentSourceError('negative development ACK requires explicit supported refusal')
        for name, item in dict(packet=packet, original_bind=original_bind, context=context, binding=original_bind.binding,
                binding_revision=revision, issued_us=issued, expires_us=expires, request_id=value['request_id'],
                accepted=accepted, state=state, reason=reason).items():
            object.__setattr__(self, name, item)


def j_capture_pairs(capture, binding):
    """원 WallCapture의 RGB/RIGHT J endpoint pair를 반환한다. 시각·provenance bytes는 만들지 않는다.

    반환은 ((rgb_lower_key,rgb_upper_key),(right_lower_key,right_upper_key))이며 각 key는
    (등록 stream UUID,원 sequence,원 J endpoint)다. source device time이 아니다. 현재 now/
    실제 ACK/원 profile 자산 검산은 actor가 소유하며 이 함수는 내부 clock을 읽지 않는다.
    """
    from ..perception.inputs import FrameStamp, ImageFrame, PerceptionInput
    from ..simulation.wall_sensor import MAX_CAPTURE_LIFETIME_US, WallCapture

    _exact(binding, DevelopmentSourceBinding)
    _exact(capture, WallCapture)
    _sha(capture.profile_sha256)
    observation = capture.observation
    _exact(observation, PerceptionInput)
    body = binding.fields
    if (observation.kind != 'SYNTHETIC' or observation.calibration_id != body['calibration_profile_sha256']
            or observation.nn is not None or observation.nn_key is not None):
        raise DevelopmentSourceError('exact original unprocessed bound SYNTHETIC observation required')
    interval = capture.captured_j_us
    if type(interval) is not tuple or len(interval) != 2:
        raise DevelopmentSourceError('original full J capture interval required')
    lower, upper = map(_uint, interval)
    completed, expires = _uint(capture.completed_j_us), _uint(capture.expires_j_us)
    if not lower < upper <= completed < expires <= lower + MAX_CAPTURE_LIFETIME_US:
        raise DevelopmentSourceError('original nonzero J capture/completion/500ms lifetime differs')
    pairs = []
    sequence = None
    for image, stream, shape, encoding, size in (
            (observation.rgb, STREAMS[0], (640, 360), 'NV12', 640*360*3//2),
            (observation.depth, STREAMS[1], (640, 480), 'RAW16_LE', 640*480*2)):
        _exact(image, ImageFrame)
        stamp = image.stamp
        _exact(stamp, FrameStamp)
        _uint(stamp.sequence)
        if (stamp.key[:3] != (binding.source_id, stream, binding.clock_id)
                or stamp.captured_us != interval or type(stamp.available_us) is not int or stamp.available_us != completed
                or type(image.width) is not int or type(image.height) is not int
                or (image.width, image.height) != shape or image.encoding != encoding
                or type(image.exposure_us) is not int or image.exposure_us != 0
                or type(image.payload) is not bytes or len(image.payload) != size
                or sequence is not None and stamp.sequence != sequence):
            raise DevelopmentSourceError('original development image/source/sequence/full capture changed')
        sequence = stamp.sequence
        instance = body['stream_instances'][stream]
        pairs.append(((instance, sequence, lower), (instance, sequence, upper)))
    return tuple(pairs)
