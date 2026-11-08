"""S-jio 한 thread의 직접 J MQTT 입력/clock 응답/world 발행 경계다.

P broker relay·scene/이동 승인·capture/NN/DB 작업을 하지 않는다. callback의 원 J2와
publish 직전 J3를 구분하며 queue/reconnect가 원 source/관측/절대기한을 갱신하지 않는다.
Paho는 start 뒤 non-daemon owner 안에서만 생성하고 단일 loop로 구동한다.
"""

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import socket
import threading
import time
from uuid import UUID, uuid4

from .jio_profile import JetsonJioProfile


PREFIX = 'xenova/v1'
WORLD_TOPIC = PREFIX + '/world/snapshot.pb'
VEHICLE_TOPIC = PREFIX + '/vehicle/state.pb'
SOURCE_FIELDS = frozenset(('vehicle', 'device', 'mapper_boot', 'mapper_instance', 'clock_epoch',
    'map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive'))
COUNTERS = ('received', 'rejected', 'duplicate', 'replaced', 'sent', 'expired', 'source_replaced',
    'connect_failed', 'network_failed', 'oversize', 'reply_failed', 'world_rejected',
    'world_fenced', 'world_dispatch_started')


def j_us():
    return time.monotonic_ns() // 1000


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
        raise ValueError('canonical nonzero source UUID required')


@dataclass(frozen=True)
class JioInput:
    """원 callback bytes와 available 시각이다. lease 없음은 측정시각/현재 자격 없음이다."""
    kind: str
    raw: bytes
    received_j_us: int
    sensor_id: int | None = None
    lease: object = None


@dataclass(frozen=True)
class _Source:
    raw: bytes
    pi_host_boot: str
    gateway_instance: str
    device_session: str
    pi_session: str

    @property
    def fields(self):
        return json.loads(self.raw)


@dataclass(frozen=True)
class _Publication:
    raw: bytes
    sha256: str
    scene_ref: bytes | None


@dataclass(frozen=True)
class _World:
    raw: bytes
    expires_us: int
    token: object
    publication: _Publication
    scene_ref: bytes | None
    grant_sha256: str | None


@dataclass(frozen=True)
class _ClockWindow:
    """실제 restored lease에서 추출한 현재 수명이다. 원 lease bytes는 별도로 보존한다."""
    lower_j_us: int
    expires_j_us: int
    lower_offset_us: int
    upper_offset_us: int
    source_revision: int


@dataclass(frozen=True)
class _SceneWindow:
    scene_ref: bytes
    grant_sha256: str
    lifecycle_state: int
    source_revision: int
    issued_pi_us: int
    expires_pi_us: int


@dataclass(frozen=True)
class _Probe:
    item: JioInput
    token: object
    expires_us: int
    source: _Source


@dataclass(frozen=True)
class _Survey:
    raw: bytes
    grant_sha256: str
    expires_us: int
    token: object


def _new_paho(profile, oversize):
    """고정2.1 parser의 body 첫 recv 앞에서 remaining-length를 제한한다."""
    from importlib.metadata import version
    import paho.mqtt.client as mqtt
    if version('paho-mqtt') != '2.1.0':
        raise ValueError('J_PAHO_VERSION')
    class BoundedClient(mqtt.Client):
        def _sock_recv(self, size):
            command = self._in_packet['command']
            if command & 0xf0 == 0x30 and command & 0x06 > 0x02:
                # 구독 QoS는0/1뿐이다. Paho의 QoS2 pending map에 payload가 쌓이기
                # 전에 거절하며 callback 뒤의 검사로 메모리 상한을 대신하지 않는다.
                raise OSError('J_MQTT_UNSUPPORTED_QOS')
            if self._in_packet['have_remaining'] and self._in_packet['remaining_length'] > 1280:
                oversize()
                raise OSError('J_MQTT_OVERSIZE')
            return super()._sock_recv(size)
    client = BoundedClient(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id='jetson-j-' + str(uuid4()), protocol=mqtt.MQTTv311)
    client.max_queued_messages_set(1)
    client.max_inflight_messages_set(1)
    client.connect_timeout = .2
    if profile.username is not None:
        client.username_pw_set(profile.username, profile.password)
    return client


class JetsonJio:
    """S-main은 불변 참조만 교환한다. paho client/socket은 S-jio만 소유한다."""

    def __init__(self, profile, *, clock=j_us, client_factory=None):
        if type(profile) is not JetsonJioProfile:
            raise ValueError('exact optional JIO profile required')
        self.profile, self.clock = profile, clock
        self._factory = _new_paho if client_factory is None else client_factory
        self._lock = threading.Lock(); self._stop = threading.Event(); self._thread = None
        self._source = None; self._token = object(); self._world = self._probe = None
        self._vehicle = self._scene = self._probe_input = None; self._tof = {0: (), 1: ()}
        self._lease = None; self._current_scene = self._summary = None
        self._publication = self._clock_window = self._scene_window = None
        self._heads = {}; self._counts = Counter({key: 0 for key in COUNTERS})
        self._connected = False; self._failure = self._last_rejection = None
        self._inflight = False; self._world_head = None; self._pi_revision = 0

    def _count(self, key):
        with self._lock:
            self._counts[key] += 1

    def _reject(self, code):
        with self._lock:
            self._counts['rejected'] += 1; self._last_rejection = code

    def replace_source(self, expected_source, *, pi_host_boot, gateway_instance, device_session, pi_session):
        """원 S identity와 실제 Pi origin만 수락한다. clock renewal은 이 API를 다시 발급하지 않는다."""
        if type(expected_source) is not dict or set(expected_source) != SOURCE_FIELDS:
            raise ValueError('exact bound S clock/publication source required')
        p = self.profile.source_policy
        if tuple(expected_source[k] for k in ('vehicle', 'device', 'configuration', 'calibration', 'primitive')) != (
                p.vehicle, self.profile.device, p.configuration, p.calibration, p.primitive):
            raise ValueError('bound source differs from approved installation policy')
        for key in ('mapper_boot', 'mapper_instance', 'clock_epoch', 'map_epoch', 'frame_epoch', 'odom_epoch'):
            _uuid(expected_source[key])
        for value in (pi_host_boot, gateway_instance, device_session, pi_session):
            _uuid(value)
        source = _Source(_canonical(expected_source), pi_host_boot, gateway_instance, device_session, pi_session)
        with self._lock:
            if source == self._source:
                return False
            self._source = source; self._token = object(); self._world = self._probe = self._lease = None
            self._publication = self._clock_window = self._scene_window = None
            self._current_scene = self._summary = None
            self._vehicle = self._scene = self._probe_input = None; self._tof = {0: (), 1: ()}; self._heads = {}
            self._world_head = None; self._pi_revision = 0
            self._counts['source_replaced'] += 1
        return True

    def replace_publication(self, binding):
        """원 S binding만 교체한다. source/clock/HWM과 이미 시작한 dispatch는 되돌리지 않는다."""
        from google.protobuf.json_format import ParseDict
        from xenova_drive_world import encode, schema
        from ..runtime.source_identity import scene_ref_fields
        from ..runtime.storage_messages import publication_binding
        checked=publication_binding(binding)
        raw=_canonical(checked)
        scene=checked['scene_admission_ref']
        scene_raw=None if scene is None else encode(ParseDict(scene_ref_fields(scene),schema.SceneAdmissionRef()))
        publication=_Publication(raw,sha256(raw).hexdigest(),scene_raw)
        with self._lock:source,token=self._source,self._token
        if source is None:raise ValueError('publication has no current original source')
        fields=source.fields
        if any(checked[key]!=fields[key] for key in (
                'mapper_instance','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')):
            raise ValueError('publication changed current source geometry')
        with self._lock:
            if self._token is not token:return False
            if self._publication is not None and self._publication.raw==raw:return False
            self._publication=publication
            if self._world is not None:self._counts['world_fenced']+=1
            self._world=None
        return True

    def _world_current_locked(self, item, now):
        clock=self._clock_window
        if (self._stop.is_set() or item.token is not self._token or self._source is None
                or item.publication is not self._publication or clock is None
                or not clock.lower_j_us<=now<clock.expires_j_us):return False
        if item.scene_ref is not None:
            scene=self._scene_window
            if (item.publication.scene_ref!=item.scene_ref or scene is None
                    or scene.scene_ref!=item.scene_ref or scene.grant_sha256!=item.grant_sha256
                    or scene.lifecycle_state!=2 or scene.source_revision!=clock.source_revision
                    or scene.issued_pi_us>now+clock.lower_offset_us
                    or now+clock.upper_offset_us>=scene.expires_pi_us):return False
        return True

    def _claim_world(self, item):
        """이 짧은 lock이 새 dispatch의 시작점이다. 이후 fence는 이미 시작한 송신을 회수하지 못한다."""
        with self._lock:
            now=self.clock()
            if now>=item.expires_us:
                self._counts['expired']+=1;return False
            if not self._world_current_locked(item,now):
                self._counts['world_fenced']+=1;return False
            self._counts['world_dispatch_started']+=1
            return True

    def offer_world(self, raw, *, expires_j_us, publication_sha256):
        """원 world와 절대기한을 최신1에 둔다. encode/접수는 발행 완료나 사용 승인이 아니다."""
        from xenova_drive_world import decode, schema
        if type(raw) is not bytes or not 0 < len(raw) <= 32768 or type(expires_j_us) is not int or not 0 < expires_j_us < 1 << 63:
            raise ValueError('bounded original world bytes/absolute expiry required')
        with self._lock:
            source, token, publication, scene = self._source, self._token, self._publication, self._scene_window
        if source is None or publication is None or publication.sha256!=publication_sha256:
            self._count('world_fenced')
            return False
        value = decode(raw, schema.WorldSnapshot); fields = source.fields; now = self.clock()
        if now>=expires_j_us:
            self._count('expired');return False
        if (any(getattr(value, key) != fields[key] for key in SOURCE_FIELDS if key != 'device')
                or not value.completed_us <= value.published_us <= now):
            self._count('world_rejected'); return False
        from xenova_drive_world import encode
        scene_ref=encode(value.scene) if value.HasField('scene') else None
        item = _World(raw, expires_j_us, token, publication, scene_ref,
            None if scene_ref is None or scene is None else scene.grant_sha256)
        with self._lock:
            if not self._world_current_locked(item,now):
                self._counts['world_fenced']+=1
                return False
            if self._world_head is not None:
                sequence, previous = self._world_head
                if value.sequence < sequence or value.sequence == sequence and raw != previous:
                    self._counts['world_rejected'] += 1; return False
                if value.sequence == sequence:
                    self._counts['duplicate'] += 1; return False
            self._world_head = value.sequence, raw
            if self._world is not None:
                self._counts['replaced'] += 1
            self._world = item
        return True

    @property
    def current_scene(self):
        """take_inputs 뒤에도 남는 마지막 실제 SceneGrant 참조다. 현재 자격은 별도 검산한다."""
        with self._lock: return self._current_scene

    @property
    def current_lease(self):
        with self._lock: return self._lease

    def offer_survey_summary(self, raw, *, grant_sha256, expires_j_us):
        """현재 원 grant의 작은 보고를 보존한다. clock reply마다 시각을 새로 만들지 않는다."""
        from hashlib import sha256
        from xenova_drive_world import decode, schema
        from ..runtime.source_identity import validate_survey_summary
        with self._lock:
            source, token, grant, lease = self._source, self._token, self._current_scene, self._lease
        if source is None or grant is None: return False
        try:
            if type(grant_sha256) is not str or grant_sha256 != sha256(grant.raw).hexdigest():
                raise ValueError('summary has no current original grant')
            summary = decode(raw, schema.SurveySummary)
            origin = dict(pi_host_boot=source.pi_host_boot,gateway_instance=source.gateway_instance,
                device_session=source.device_session,pi_session=source.pi_session)
            validate_survey_summary(raw,grant.raw,lease,source.fields,origin,
                issued_us=summary.completed_peer_us,expires_us=expires_j_us,now_us=self.clock())
        except (ValueError, TypeError, OverflowError):
            self._reject('INVALID_SURVEY'); return False
        item = _Survey(raw,grant_sha256,expires_j_us,token)
        with self._lock:
            if self._token is not token or self._current_scene is not grant or self._stop.is_set(): return False
            if self._summary == item: return False
            if self._summary is not None and self._summary.raw == raw:
                # 같은 원문에 더 긴 전달 수명을 붙이는 retry를 받지 않는다.
                if self._summary.expires_us != expires_j_us: return False
            self._summary = item
        return True

    def fence_source(self):
        """실제 source 철회 사건. 원 pending publish를 새 source의 결과로 재사용하지 않는다."""
        with self._lock:
            self._source=None;self._token=object();self._world=self._probe=self._lease=None
            self._publication=self._clock_window=self._scene_window=None
            self._current_scene=self._summary=None
            self._vehicle=self._scene=self._probe_input=None;self._tof={0:(),1:()};self._heads={}
            self._world_head=None;self._pi_revision=0

    def take_inputs(self):
        """Vehicle1+ToF각2+scene1+probe1 이하의 원 사본이다. 시각을 재발급하지 않는다."""
        with self._lock:
            result = tuple(x for x in (self._vehicle, *self._tof[0], *self._tof[1], self._scene, self._probe_input) if x is not None)
            self._vehicle = self._scene = self._probe_input = None; self._tof = {0: (), 1: ()}
        return tuple(sorted(result, key=lambda item: item.received_j_us))

    def snapshot(self):
        with self._lock:
            thread = self._thread
            return dict(connected=self._connected, alive=thread is not None and thread.is_alive(),
                stopped=self._stop.is_set(), source_bound=self._source is not None, world_pending=self._world is not None,
                publication_sha256=None if self._publication is None else self._publication.sha256,
                probe_pending=self._probe is not None, publish_inflight=self._inflight,
                inputs=(self._vehicle is not None) + len(self._tof[0]) + len(self._tof[1]) + (self._scene is not None) + (self._probe_input is not None),
                counts=dict(self._counts), failure=self._failure, last_rejection=self._last_rejection)

    def start(self):
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                raise ValueError('JIO owner is single-start')
            self._thread = threading.Thread(target=self._run, name='S-jio', daemon=False)
            thread = self._thread
        thread.start()
        return self

    def stop(self):
        self._stop.set()

    def join(self, timeout=1.):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 5:
            raise ValueError('bounded JIO join timeout required')
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def _origin(self, value, source, *, vehicle=False):
        if source is None:
            return False
        if (value.vehicle, value.host_boot, value.gateway_instance, value.device_session, value.pi_session) != (
                self.profile.source_policy.vehicle, source.pi_host_boot, source.gateway_instance, source.device_session, source.pi_session):
            return False
        return not vehicle or (value.odom_epoch, value.calibration) == (source.fields['odom_epoch'], source.fields['calibration'])

    def _receive(self, message):
        """Paho callback에서만 호출한다. 원 J2는 payload 검사보다 먼저 취득한다."""
        from xenova_drive_world import decode, schema
        from xenova_drive_world.mapper_clock import restore_returned_probe, TIMEOUT_US
        received = self.clock()
        with self._lock:
            source, token, lease = self._source, self._token, self._lease
            known_scene, known_scene_head = self._current_scene, self._heads.get('scene')
        known_negative = False;clock_window=scene_window=None
        if message.retain or len(message.payload) > 1024:
            self._reject('RETAINED_OR_SIZE'); return
        raw = bytes(message.payload); p = self.profile.source_policy
        try:
            topic = message.topic
            if topic == VEHICLE_TOPIC and message.qos == 0:
                value = decode(raw, schema.VehicleSample)
                if (value.vehicle, value.calibration) != (p.vehicle, p.calibration) or not value.HasField('gateway_instance') or not value.HasField('pi_session'):
                    raise ValueError('VEHICLE_POLICY')
                for key in ('host_boot', 'gateway_instance', 'device_session', 'pi_session', 'odom_epoch'):
                    _uuid(getattr(value, key))
                matches = self._origin(value, source, vehicle=True)
                # 최초 승인 Vehicle과 새 Pi source의 후보는 S identity actor가 받아야 한다.
                item = JioInput('VEHICLE', raw, received, lease=lease if matches else None)
                head = ('VEHICLE', value.host_boot, value.gateway_instance, value.device_session, value.pi_session, value.odom_epoch)
                sequence = value.sequence
                destination = 'vehicle'
            elif topic == f'{PREFIX}/mapper/{self.profile.device}/clock/request.pb' and message.qos == 0:
                if source is None:
                    self._reject('SOURCE_NOT_BOUND'); return
                value = decode(raw, schema.MapperClockProbe)
                if (value.vehicle, value.device, value.pi_host_boot) != (p.vehicle, self.profile.device, source.pi_host_boot):
                    raise ValueError('PROBE_ORIGIN')
                _uuid(value.pi_client_instance)
                for key in ('mapper_boot', 'mapper_instance', 'clock_epoch'):
                    name = 'expected_' + key
                    if value.HasField(name) and getattr(value, name) != source.fields[key]:
                        raise ValueError('PROBE_EXPECTED_SOURCE')
                returned = restore_returned_probe(raw, self.profile.clock_policy, source.fields, source.pi_host_boot, received)
                item = JioInput('PROBE', raw, received, lease=returned)
                deadline = received + TIMEOUT_US
                if returned is not None:
                    from xenova_drive_world import decode as decode_trust
                    trust = decode_trust(returned.trust_bytes, schema.WorldTrust)
                    returned.pi_interval_to_j((returned.probe_pi_send_us,returned.probe_pi_send_us),received)
                    clock_window=_ClockWindow(returned.probe_pi_send_us-trust.upper_offset_us,
                        returned.expires_pi_us-trust.upper_offset_us,trust.lower_offset_us,
                        trust.upper_offset_us,returned.source_revision)
                    deadline = min(deadline, value.pi_send_us + TIMEOUT_US - trust.upper_offset_us)
                probe = _Probe(item, token, deadline, source)
                head = ('PROBE', value.pi_client_instance); sequence = value.probe_seq; destination = 'probe'
            elif topic in (f'{PREFIX}/sensors/tof/0/observation.pb', f'{PREFIX}/sensors/tof/1/observation.pb') and message.qos == 0:
                value = decode(raw, schema.TofObservation)
                sensor = int(topic.split('/')[-2]); installed = next((x for x in self.profile.tof_profiles if x[0] == sensor), None)
                if (sensor != value.sensor_id or installed is None or (value.calibration, value.sensor_config_revision) != installed[1:]
                        or not self._origin(value, source)):
                    raise ValueError('TOF_ORIGIN_OR_PROFILE')
                from .sensor_samples import tof_crc32
                if tof_crc32(value) != value.source_crc32:
                    raise ValueError('TOF_SOURCE_CRC')
                item = JioInput('TOF', raw, received, sensor, lease)
                head = ('TOF', sensor, value.sensor_generation, value.sensor_instance)
                sequence = value.source_sample; destination = sensor
            elif topic == f'{PREFIX}/mapper/{self.profile.device}/scene.pb' and message.qos == 1:
                value = decode(raw, schema.SceneGrant)
                if value.lifecycle_state in (3, 4):
                    from ..runtime.source_identity import known_scene_negative
                    if (source is None or known_scene is None or not known_scene_negative(raw, known_scene.raw,
                            source=source.fields, pi_host_boot=source.pi_host_boot, gateway_instance=source.gateway_instance)):
                        raise ValueError('SCENE_UNKNOWN_NEGATIVE')
                    known_negative = True
                elif (source is None or (value.vehicle, value.pi_host_boot, value.gateway_instance, value.mapper_device) != (
                        p.vehicle, source.pi_host_boot, source.gateway_instance, self.profile.device)
                        or any(getattr(value, key) != source.fields[key] for key in ('map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive'))
                        or lease is None or value.source_admission_revision != lease.source_revision):
                    raise ValueError('SCENE_ORIGIN')
                if not known_negative:
                    lease.pi_interval_to_j((lease.probe_pi_send_us, lease.probe_pi_send_us), received)
                    from xenova_drive_world import decode as decode_trust
                    trust = decode_trust(lease.trust_bytes, schema.WorldTrust)
                    if (value.issued_pi_us > received + trust.lower_offset_us
                            or received + trust.upper_offset_us >= value.valid_until_pi_us):
                        raise ValueError('SCENE_EXPIRED')
                item = JioInput('SCENE', raw, received, lease=None if known_negative else lease)
                from xenova_drive_world import encode
                scene_window=_SceneWindow(encode(value.scene),sha256(raw).hexdigest(),value.lifecycle_state,
                    value.source_admission_revision,value.issued_pi_us,value.valid_until_pi_us)
                head = ('SCENE', value.scene.admission_id, value.scene.admission_revision)
                sequence = value.lifecycle_revision; destination = 'scene'
            else:
                self._reject('TOPIC_OR_QOS'); return
        except (ValueError, OverflowError, TypeError):
            self._reject('INVALID_SOURCE_OR_WIRE'); return
        with self._lock:
            if token is not self._token:
                self._counts['rejected'] += 1; return
            if known_negative and (self._current_scene is not known_scene or self._heads.get('scene') is not known_scene_head):
                self._counts['rejected'] += 1; return
            # 한 kind/sensor당 마지막 source만 보유한다. 무한 publisher registry는 만들지 않는다.
            key = destination
            previous = self._heads.get(key)
            if (destination == 'scene' and previous is not None and head[1] == previous[0][1]
                    and (head[2] < previous[0][2] or sequence < previous[1]
                         or sequence == previous[1] and raw != previous[2])):
                self._counts['rejected'] += 1; return
            if destination == 'probe' and value.admission_revision < self._pi_revision:
                self._counts['rejected'] += 1; return
            if (type(destination) is int and previous is not None
                    and (head[2] < previous[0][2] or head[2] == previous[0][2] and head[3] != previous[0][3]
                         or head[2] > previous[0][2] and head[3] == previous[0][3])):
                self._counts['rejected'] += 1; return
            if previous is not None and previous[0] == head:
                if sequence < previous[1] or sequence == previous[1] and raw != previous[2]:
                    self._counts['rejected'] += 1; return
                if sequence == previous[1]:
                    self._counts['duplicate'] += 1; return
            self._heads[key] = head, sequence, raw
            if destination == 'vehicle':
                self._vehicle = item
            elif destination == 'probe':
                self._probe_input = item; self._probe = probe
                # 새 원 Probe가 prior trust를 돌려주지 않으면 옛 lease로 센서 입력을
                # 계속 현재화하지 않는다. Pi의 revision과 S binding 숫자는 비교하지 않는다.
                self._pi_revision = value.admission_revision; self._lease = item.lease
                self._clock_window=clock_window
            elif destination == 'scene':
                self._scene = self._current_scene = item
                self._scene_window=scene_window
                self._summary = None
            else:
                old = self._tof[destination]
                self._tof[destination] = (old + (item,))[-2:]
            self._counts['received'] += 1

    def _reply(self, pending):
        from xenova_drive_world import decode, encode, schema
        from hashlib import sha256
        from ..runtime.source_identity import validate_survey_summary
        source = pending.source
        value = decode(pending.item.raw, schema.MapperClockProbe)
        reply = schema.MapperClockReply(major=1, pi_host_boot=value.pi_host_boot,
            pi_client_instance=value.pi_client_instance, probe_seq=value.probe_seq, nonce=value.nonce,
            pi_send_us=value.pi_send_us, j2=pending.item.received_j_us, j3=0,
            **{key: data for key, data in source.fields.items() if key != 'vehicle'})
        with self._lock:
            summary, grant, lease, token = self._summary,self._current_scene,self._lease,self._token
        if (summary is not None and grant is not None and summary.token is token is pending.token
                and summary.grant_sha256 == sha256(grant.raw).hexdigest()):
            try:
                value = decode(summary.raw,schema.SurveySummary)
                origin = dict(pi_host_boot=source.pi_host_boot,gateway_instance=source.gateway_instance,
                    device_session=source.device_session,pi_session=source.pi_session)
                validate_survey_summary(summary.raw,grant.raw,lease,source.fields,origin,
                    issued_us=value.completed_peer_us,expires_us=summary.expires_us,now_us=self.clock())
                reply.survey_summary.CopyFrom(value)
            except (ValueError, TypeError, OverflowError):
                pass  # 보고가 오래되어도 독립 clock 응답은 계속한다.
        reply.j3 = self.clock()
        if reply.HasField('survey_summary') and reply.j3 >= summary.expires_us:
            reply.ClearField('survey_summary')
        return encode(reply)

    @staticmethod
    def _close_client(client):
        if client is not None:
            try:
                client.disconnect()
            finally:
                connection = client.socket()
                if connection is not None:
                    connection.close()

    def _run(self):
        from xenova_drive_world.mapper_clock import TIMEOUT_US
        client = None; inflight = None; connected = False; retry_after = 0; observed_token = None
        try:
            while not self._stop.is_set():
                now = self.clock()
                with self._lock:
                    token = self._token
                if observed_token is not token:
                    self._close_client(client); client = None; inflight = None; connected = False; observed_token = token
                if client is None and now >= retry_after:
                    client = self._factory(self.profile, lambda: self._count('oversize'))
                    def on_connect(c, user, flags, reason, properties):
                        nonlocal connected
                        connected = getattr(reason, 'value', reason) == 0
                        if connected:
                            for topic, qos in ((VEHICLE_TOPIC, 0),
                                    (f'{PREFIX}/mapper/{self.profile.device}/clock/request.pb', 0),
                                    (f'{PREFIX}/mapper/{self.profile.device}/scene.pb', 1),
                                    (f'{PREFIX}/sensors/tof/0/observation.pb', 0),
                                    (f'{PREFIX}/sensors/tof/1/observation.pb', 0)):
                                c.subscribe(topic, qos=qos)
                    client.on_connect = on_connect
                    client.on_message = lambda c, u, message: self._receive(message)
                    try:
                        client.connect(self.profile.host, self.profile.port, keepalive=10)
                        connection = client.socket()
                        if connection is not None:
                            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except OSError:
                        self._count('connect_failed'); self._close_client(client); client = None
                        connected = False; retry_after = now + 100_000
                if client is not None:
                    try:
                        rc = client.loop(timeout=.002)
                        if rc not in (0, -1):
                            raise OSError('J_MQTT_LOOP')
                        now = self.clock()
                        if inflight is not None:
                            info, expires, active_token = inflight
                            if info.is_published():
                                self._count('sent'); inflight = None
                            elif now >= expires or active_token is not self._token:
                                raise OSError('J_MQTT_WRITE_EXPIRED_OR_FENCED')
                        if connected and inflight is None:
                            with self._lock:
                                if self._probe is not None:
                                    selected = self._probe; self._probe = None; kind = 'reply'
                                else:
                                    selected = self._world; self._world = None; kind = 'world'
                            if selected is not None:
                                expires = selected.expires_us
                                if kind=='world':
                                    raw=selected.raw;topic=WORLD_TOPIC
                                    dispatch=self._claim_world(selected)
                                else:
                                    dispatch=selected.token is self._token and now<expires
                                    if dispatch:
                                        raw=self._reply(selected)
                                        topic=f'{PREFIX}/mapper/{self.profile.device}/clock/reply.pb'
                                        dispatch=selected.token is self._token and self.clock()<expires
                                    if not dispatch:
                                        self._count('expired')
                                if dispatch:
                                    # 원 dispatch claim 뒤 IO를 한다. 송신 시작 뒤의 철회는 recall이 아니다.
                                    info = client.publish(topic, raw, qos=0, retain=False)
                                    if info.rc != 0:
                                        raise OSError('J_MQTT_PUBLISH')
                                    inflight = info, min(expires, self.clock() + TIMEOUT_US), selected.token
                    except ValueError:
                        self._count('reply_failed')
                    except (OSError, RuntimeError):
                        self._count('network_failed'); self._close_client(client)
                        client = None; connected = False; inflight = None; retry_after = now + 100_000
                with self._lock:
                    self._connected = connected; self._inflight = inflight is not None
                self._stop.wait(.002)
        except Exception as error:
            with self._lock:
                self._failure = type(error).__name__
        finally:
            try:
                self._close_client(client)
            except Exception as error:
                with self._lock:
                    self._failure = type(error).__name__
            with self._lock:
                self._connected = self._inflight = False
