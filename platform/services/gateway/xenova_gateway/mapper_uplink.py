"""Gateway 원 센서→독립 Pi J process. P broker relay/scene 승인/모터 출력은 하지 않는다.

Linux spawn child가 J MQTT를 단독 소유한다. Gateway actor는 nonblocking seqpacket과
clock owner만 호출한다. source별 최신 사본과 실제 MQTT in-flight 하나를 분리한다.
profile의 broker 계정/ACL은 배포 전제이며 MQTT payload가 publisher 인증을 증명하지 않는다.
"""

from collections import Counter
from dataclasses import dataclass, field
import ipaddress
import multiprocessing
import os
import socket
import stat
import struct
import sys
import threading
import time
from uuid import uuid4

from xenova_drive_world import schema, encode, decode
from xenova_drive_world.mapper_clock import (ClockPolicy, MapperClockOwner, MapperClockError,
    ProbePlan, build_probe, candidate_body, plan_json, parse_plan, strict_json,
    integer, canonical_uuid, ascii_id, TIMEOUT_US)


PREFIX = 'xenova/v1'
PACKET_BYTES = 4096
LENGTHS = struct.Struct('<II')
HEADER_KEYS = frozenset(('host_boot', 'gateway_instance', 'uplink_instance', 'channel_epoch',
    'message_sequence', 'kind', 'issued_pi_us', 'expires_pi_us', 'request_id'))
KINDS = frozenset(('SOURCE_VEHICLE', 'SOURCE_TOF', 'SCENE_GRANT', 'PROBE_PLAN', 'TRUST_CANDIDATE', 'STOP', 'STOPPED'))


def decimal(value):
    """local header는 원 u64 범위다. protobuf body의 별도 u63 semantic cap은 codec이 검사한다."""
    if (type(value) is not str or not value.isascii() or not value.isdigit()
            or len(value) > 20 or str(int(value)) != value or int(value) > 2**64 - 1):
        raise ValueError('J_LOCAL_U64')
    return int(value)


def p_us():
    return time.monotonic_ns() // 1000


@dataclass(frozen=True)
class UplinkProfile:
    policy: ClockPolicy
    host: str
    port: int
    username: str | None
    password: str | None = field(repr=False)
    tof_profiles: tuple = ()
    tof_max_age_us: int = 500000

    def __post_init__(self):
        if type(self.policy) is not ClockPolicy or type(self.host) is not str or str(ipaddress.IPv4Address(self.host)) != self.host:
            raise ValueError('J_PROFILE_ADDRESS')
        integer(self.port, 1, 65535)
        if self.username is not None:
            ascii_id(self.username, 128)
        if self.password is not None and (type(self.password) is not str or len(self.password) > 1024):
            raise ValueError('J_PROFILE_CREDENTIAL')
        integer(self.tof_max_age_us, 1, 1000000)
        if self.tof_profiles and self.policy.lookback_us < self.tof_max_age_us:
            raise ValueError('J_PROFILE_TOF_LOOKBACK')
        if type(self.tof_profiles) is not tuple or len(self.tof_profiles) > 2:
            raise ValueError('J_PROFILE_TOF')
        ids = set()
        for row in self.tof_profiles:
            if type(row) is not tuple or len(row) != 3 or type(row[0]) is not int or row[0] not in (0, 1) or row[0] in ids:
                raise ValueError('J_PROFILE_TOF')
            ids.add(row[0])
            if type(row[1]) is not str or len(row[1]) != 64 or any(c not in '0123456789abcdef' for c in row[1]):
                raise ValueError('J_PROFILE_TOF_LEAF')
            integer(row[2], 1, 2**32 - 1)

    @classmethod
    def load(cls, path, *, uid=0):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077
                    or info.st_nlink != 1 or info.st_size > 8192):
                raise ValueError('J_PROFILE_STORAGE')
            raw = stream.read(8193)
        from .ipc_contract import decode as private_json
        value = private_json(raw)
        keys = {'version', 'clock', 'host', 'port', 'username', 'password', 'tof_profiles', 'tof_max_age_us'}
        if set(value) != keys or type(value['version']) is not int or value['version'] != 1:
            raise ValueError('J_PROFILE_FIELDS')
        if type(value['clock']) is not dict or set(value['clock']) != set(ClockPolicy.__dataclass_fields__):
            raise ValueError('J_PROFILE_CLOCK')
        if type(value['tof_profiles']) is not list or any(type(row) is not list for row in value['tof_profiles']):
            raise ValueError('J_PROFILE_TOF')
        return cls(ClockPolicy(**value['clock']), value['host'], value['port'], value['username'], value['password'],
            tuple(tuple(row) for row in value['tof_profiles']), value['tof_max_age_us'])


def frame(header, body):
    if type(header) is not dict or set(header) != HEADER_KEYS or type(body) is not bytes:
        raise ValueError('J_LOCAL_FIELDS')
    for key in ('host_boot', 'gateway_instance', 'uplink_instance', 'channel_epoch', 'request_id'):
        canonical_uuid(header[key])
    for key in ('message_sequence', 'issued_pi_us', 'expires_pi_us'):
        decimal(header[key])
    if (type(header['kind']) is not str or header['kind'] not in KINDS or decimal(header['message_sequence']) == 0
            or decimal(header['issued_pi_us']) >= decimal(header['expires_pi_us'])):
        raise ValueError('J_LOCAL_KIND_TIME')
    import json
    raw = json.dumps(header, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    body_cap = 2048 if header['kind'] in ('PROBE_PLAN', 'TRUST_CANDIDATE') else 1024
    if len(raw) > 2048 or len(body) > body_cap or len(raw) + len(body) + 8 > PACKET_BYTES:
        raise ValueError('J_LOCAL_SIZE')
    return LENGTHS.pack(len(raw), len(body)) + raw + body


def unframe(raw):
    if type(raw) is not bytes or not 8 <= len(raw) <= PACKET_BYTES:
        raise ValueError('J_LOCAL_SIZE')
    a, b = LENGTHS.unpack_from(raw)
    if a > 2048 or b > 2048 or a + b + 8 != len(raw):
        raise ValueError('J_LOCAL_LENGTH')
    header = strict_json(raw[8:8 + a], 2048)
    body = raw[8 + a:]
    frame(header, body)
    return header, body


class _Link:
    def __init__(self, endpoint, identities):
        if endpoint.family != socket.AF_UNIX or endpoint.type != socket.SOCK_SEQPACKET:
            raise ValueError('J_LOCAL_SEQPACKET')
        endpoint.setblocking(False)
        self.socket, self.identities = endpoint, identities
        self.next_sequence, self.highwater, self.last_raw = 1, 0, None
        self.pending = None

    def offer(self, kind, body, issued, expires, request):
        if self.pending is not None:
            return False
        if self.next_sequence > 2**64 - 1:
            raise ValueError('J_LOCAL_SEQUENCE_EXHAUSTED')
        header = dict(zip(('host_boot', 'gateway_instance', 'uplink_instance', 'channel_epoch'), self.identities))
        header.update(message_sequence=str(self.next_sequence), kind=kind, issued_pi_us=str(issued),
            expires_pi_us=str(expires), request_id=request)
        self.pending = frame(header, body), kind, expires
        self.next_sequence += 1
        return True

    def flush(self, now):
        if self.pending is None:
            return
        raw, kind, expires = self.pending
        if now >= expires and kind not in ('STOP', 'STOPPED'):
            self.pending = None
            return
        try:
            size = self.socket.send(raw)
        except BlockingIOError:
            return
        if size != len(raw):
            raise ValueError('J_LOCAL_PARTIAL_SEND')
        self.pending = None

    def receive(self, now):
        result = []
        for _ in range(8):
            try:
                raw, _, flags, _ = self.socket.recvmsg(PACKET_BYTES + 1)
            except BlockingIOError:
                break
            if not raw:
                raise EOFError('J_LOCAL_EOF')
            if flags & socket.MSG_TRUNC or len(raw) > PACKET_BYTES:
                raise ValueError('J_LOCAL_TRUNCATED')
            header, body = unframe(raw)
            actual = tuple(header[key] for key in ('host_boot', 'gateway_instance', 'uplink_instance', 'channel_epoch'))
            if actual != self.identities:
                raise ValueError('J_LOCAL_CHANNEL')
            sequence = decimal(header['message_sequence'])
            if sequence <= self.highwater:
                if sequence == self.highwater and raw == self.last_raw:
                    continue
                raise ValueError('J_LOCAL_SEQUENCE')
            self.highwater, self.last_raw = sequence, raw
            if not decimal(header['issued_pi_us']) <= now:
                raise ValueError('J_LOCAL_FUTURE')
            if now >= decimal(header['expires_pi_us']) and header['kind'] not in ('STOP', 'STOPPED'):
                continue
            result.append((header, body))
        return result


def _client(profile):
    import importlib.metadata
    import paho.mqtt.client as mqtt
    if importlib.metadata.version('paho-mqtt') != '2.1.0':
        raise ValueError('J_PAHO_VERSION')
    class BoundedClient(mqtt.Client):
        def _sock_recv(self, size):
            if self._in_packet['have_remaining'] and self._in_packet['remaining_length'] > 1280:
                raise OSError('J_MQTT_OVERSIZE')
            return super()._sock_recv(size)
    client = BoundedClient(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id='pi-j-' + str(uuid4()), protocol=mqtt.MQTTv311)
    client.max_queued_messages_set(1)
    client.max_inflight_messages_set(1)
    client.connect_timeout = .2
    if profile.username is not None:
        client.username_pw_set(profile.username, profile.password)
    return client


def uplink_main(endpoint, profile, identities):
    """실제 별도 Linux process. MQTTMessageInfo 실제 완료까지 네트워크 in-flight1개다."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    link = _Link(endpoint, identities)
    pending = {}
    candidate = None
    outstanding = None
    inflight = None
    client = None
    connected = False
    retry_after = 0
    stopping = None
    turn = 0
    reply_topic = f'{PREFIX}/mapper/{profile.policy.device}/clock/reply.pb'
    try:
        while stopping is None:
            now = p_us()
            link.flush(now)
            for header, body in link.receive(now):
                kind = header['kind']
                if kind == 'STOP':
                    value = strict_json(body)
                    if set(value) != {'revision'} or decimal(value['revision']) != 1:
                        raise ValueError('J_STOP_FIELDS')
                    stopping = header
                    break
                if kind not in ('SOURCE_VEHICLE', 'SOURCE_TOF', 'SCENE_GRANT', 'PROBE_PLAN'):
                    raise ValueError('J_LOCAL_DIRECTION')
                key = kind
                if kind == 'SOURCE_TOF':
                    key = kind, decode(body, schema.TofObservation).sensor_id
                pending[key] = header, body
            if stopping is not None:
                break
            if candidate is not None and link.pending is None:
                header, body = candidate
                if link.offer('TRUST_CANDIDATE', body, decimal(header['issued_pi_us']), decimal(header['expires_pi_us']), header['request_id']):
                    candidate = None
            if client is None and now >= retry_after:
                client = _client(profile)
                def on_connect(c, userdata, flags, reason, properties):
                    nonlocal connected
                    connected = getattr(reason, 'value', reason) == 0
                    if connected:
                        c.subscribe(reply_topic, qos=0)
                def on_message(c, userdata, message):
                    nonlocal candidate, outstanding
                    p4 = p_us()
                    if (message.topic != reply_topic or message.retain or message.qos != 0
                            or len(message.payload) > 1024 or outstanding is None or candidate is not None):
                        return
                    header, raw_probe, deadline = outstanding
                    if p4 >= deadline:
                        outstanding = None
                        return
                    try:
                        probe = decode(raw_probe, schema.MapperClockProbe)
                        reply = decode(bytes(message.payload), schema.MapperClockReply)
                        if any(getattr(probe, name) != getattr(reply, name) for name in
                                ('pi_host_boot', 'pi_client_instance', 'probe_seq', 'nonce', 'pi_send_us', 'device')):
                            return
                        body = candidate_body(raw_probe, bytes(message.payload), p4)
                    except ValueError:
                        return
                    response_header = dict(header, issued_pi_us=str(p4))
                    candidate, outstanding = (response_header, body), None
                client.on_connect, client.on_message = on_connect, on_message
                try:
                    client.connect(profile.host, profile.port, keepalive=10)
                    client.socket().setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    if client.socket() is not None:
                        client.socket().close()
                    client, connected, retry_after = None, False, now + 100000
            if client is not None:
                try:
                    rc = client.loop(timeout=.002)
                    now = p_us()
                    if rc not in (0, -1):
                        raise OSError('J_MQTT_LOOP')
                    if inflight is not None:
                        info, deadline = inflight
                        if info.is_published():
                            inflight = None
                        elif now >= deadline:
                            raise OSError('J_MQTT_WRITE_EXPIRED')
                    if outstanding is not None and now >= outstanding[2]:
                        outstanding = None
                    if connected and inflight is None:
                        for key in tuple(pending):
                            if now >= decimal(pending[key][0]['expires_pi_us']):
                                del pending[key]
                        order = ('PROBE_PLAN', 'SOURCE_VEHICLE', ('SOURCE_TOF', 0), ('SOURCE_TOF', 1), 'SCENE_GRANT')
                        key = 'PROBE_PLAN' if 'PROBE_PLAN' in pending and outstanding is None else next(
                            (order[(turn + i) % len(order)] for i in range(len(order))
                                if order[(turn + i) % len(order)] in pending and order[(turn + i) % len(order)] != 'PROBE_PLAN'), None)
                        if key is not None:
                            header, body = pending.pop(key)
                            issued, expires = decimal(header['issued_pi_us']), decimal(header['expires_pi_us'])
                            qos = 0
                            if key == 'PROBE_PLAN':
                                plan = parse_plan(body, request_id=header['request_id'], issued_us=issued, expires_us=expires)
                                if (plan.pi_host_boot, plan.pi_client_instance, plan.vehicle, plan.device) != (
                                        identities[0], identities[2], profile.policy.vehicle, profile.policy.device):
                                    raise ValueError('J_PLAN_CONTEXT')
                                p1 = p_us()
                                try:
                                    body = build_probe(plan, p1)
                                except MapperClockError:
                                    # connect/송신 대기로 원 plan/prior lease가 끝났으면 새 P1로 살리지 않는다.
                                    outstanding = None
                                    continue
                                topic = f'{PREFIX}/mapper/{profile.policy.device}/clock/request.pb'
                                outstanding = header, body, expires
                            elif key == 'SOURCE_VEHICLE':
                                topic = PREFIX + '/vehicle/state.pb'
                            elif type(key) is tuple:
                                topic = f'{PREFIX}/sensors/tof/{key[1]}/observation.pb'
                            else:
                                topic, qos = f'{PREFIX}/mapper/{profile.policy.device}/scene.pb', 1
                            selected_us = p_us()
                            if selected_us >= expires:
                                continue
                            info = client.publish(topic, body, qos=qos, retain=False)
                            if info.rc != 0:
                                raise OSError('J_MQTT_PUBLISH')
                            inflight = info, min(expires, selected_us + TIMEOUT_US)
                            turn = (order.index(key) + 1) % len(order)
                except (OSError, RuntimeError):
                    # 이미 kernel에 들어간 사본의 원 identity/기한은 바뀌지 않는다.
                    try:
                        client.disconnect()
                    finally:
                        if client.socket() is not None:
                            client.socket().close()
                    client, connected, inflight, outstanding = None, False, None, None
                    retry_after = now + 100000
            time.sleep(.002)
    finally:
        if client is not None:
            client.disconnect()
            if client.socket() is not None:
                client.socket().close()
        if stopping is not None:
            import json
            now = p_us()
            link.pending = None
            link.offer('STOPPED', json.dumps(dict(revision='1', status='STOPPED')).encode('ascii'),
                decimal(stopping['issued_pi_us']), decimal(stopping['expires_pi_us']), stopping['request_id'])
            link.flush(now)
        endpoint.close()


class GatewayMapperUplink:
    """Gateway actor 전용. 신뢰/원본 mailbox와 child 생명을 소유하며 serial/Authority는 만지지 않는다."""

    def __init__(self, profile, host_boot, gateway_instance, *, clock=p_us):
        if type(profile) is not UplinkProfile:
            raise ValueError('J_PROFILE')
        self.profile, self.clock = profile, clock
        self.host_boot, self.gateway_instance = canonical_uuid(host_boot), canonical_uuid(gateway_instance)
        self.owner = MapperClockOwner(profile.policy, self.host_boot)
        self.process = self.link = None
        self.pending = {}
        self.counts = Counter()
        self.last_vehicle = None
        self.last_tof = {}
        self.last_scene = None
        self.failure = None
        self.closed = False
        self._tof_lock = threading.Lock()
        self._tof_inputs = {}

    def start(self):
        if not sys.platform.startswith('linux') or self.process is not None or self.closed:
            raise ValueError('J_UPLINK_REQUIRES_FRESH_LINUX_OWNER')
        self.uplink_instance, self.channel_epoch = str(uuid4()), str(uuid4())
        identities = self.host_boot, self.gateway_instance, self.uplink_instance, self.channel_epoch
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        for endpoint in (parent, child):
            endpoint.setblocking(False)
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
        process = multiprocessing.get_context('spawn').Process(target=uplink_main,
            args=(child, self.profile, identities), name='Pi-J-uplink', daemon=False)
        try:
            link=_Link(parent,identities)
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self.process, self.link = process, link
        return self

    def _offer(self, key, kind, raw, expires, request=None):
        now = self.clock()
        if self.closed or now >= expires:
            return False
        self.pending[key] = kind, raw, now, expires, str(uuid4()) if request is None else request
        return True

    def offer_vehicle(self, raw):
        """DriveLinkProvider가 P에 준 바로 그 protobuf bytes. P 구독/재발급은 없다."""
        try:
            if type(raw) is not bytes or len(raw) > 1024:
                raise ValueError('J_VEHICLE_SIZE')
            now = self.clock()
            original = decode(raw, schema.VehicleSample)
            if not original.HasField('gateway_instance') or original.gateway_instance != self.gateway_instance:
                raise ValueError('J_VEHICLE_GATEWAY')
            self.owner.set_vehicle(raw, now)
            sample = self.owner.vehicle
            identity = sample.device_session, sample.odom_epoch, sample.sequence
            if identity == self.last_vehicle:
                return False
            self.last_vehicle = identity
            return self._offer('vehicle', 'SOURCE_VEHICLE', raw,
                sample.pi_sample_us + self.profile.policy.vehicle_max_age_us - sample.clock_error_us)
        except ValueError as error:
            self.counts['vehicle_rejected'] += 1
            return False

    def offer_tof(self, raw):
        """호출자는 원 serial/센서 owner다. 없는 센서·승인되지 않은 leaf를 만들지 않는다."""
        if type(raw) is not bytes or len(raw) > 1024:
            raise ValueError('J_TOF_SIZE')
        value = decode(raw, schema.TofObservation)
        expected = next((row for row in self.profile.tof_profiles if row[0] == value.sensor_id), None)
        vehicle = self.owner.vehicle
        if (expected is None or (value.calibration, value.sensor_config_revision) != expected[1:]
                or (value.vehicle, value.host_boot, value.gateway_instance) != (
                    self.profile.policy.vehicle, self.host_boot, self.gateway_instance)
                or vehicle is None or value.device_session != vehicle.device_session or value.pi_session != vehicle.pi_session
                or value.pi_sample_us > self.clock()):
            raise ValueError('J_TOF_ORIGIN')
        identity = value.sensor_instance, value.sensor_generation, value.device_session, value.pi_session, value.clock_epoch
        previous = self.last_tof.get(value.sensor_id)
        if previous is not None and previous[0] == identity:
            if value.source_sample < previous[1] or value.source_sample == previous[1] and raw != previous[2]:
                raise ValueError('J_TOF_REGRESSION')
            if value.source_sample == previous[1]:
                return False
        self.last_tof[value.sensor_id] = identity, value.source_sample, raw
        return self._offer(('tof', value.sensor_id), 'SOURCE_TOF', raw,
            value.pi_sample_us + self.profile.tof_max_age_us - value.clock_error_us - value.sensor_time_error_us)

    def submit_tof(self, raw):
        """원 센서 생산 thread용 latest2 사본. 검증/발행은 Gateway actor가 한다."""
        if type(raw) is not bytes or len(raw) > 1024 or self.closed:
            return False
        value = decode(raw, schema.TofObservation)
        with self._tof_lock:
            self._tof_inputs[value.sensor_id] = raw
        return True

    def publish_scene_after_commit(self, raw):
        """현재 durable scene owner의 원 SceneGrant만 전달한다. 이 함수는 내구 승인 producer가 아니다."""
        if type(raw) is not bytes or len(raw) > 1024:
            raise ValueError('J_SCENE_SIZE')
        value = decode(raw, schema.SceneGrant)
        p = self.profile.policy
        if ((value.vehicle, value.pi_host_boot, value.gateway_instance, value.mapper_device) !=
                (p.vehicle, self.host_boot, self.gateway_instance, p.device)
                or (value.configuration, value.calibration, value.primitive) != (p.configuration, p.calibration, p.primitive)
                or value.source_admission_revision != self.owner.source_revision
                or not value.issued_pi_us <= self.clock() < value.valid_until_pi_us):
            raise ValueError('J_SCENE_ORIGIN')
        trust = self.owner.trust(self.clock())
        if value.lifecycle_state in (0, 1, 2) and (trust is None or (value.map_epoch, value.frame_epoch, value.odom_epoch)
                != (trust.map_epoch, trust.frame_epoch, trust.odom_epoch)):
            raise ValueError('J_SCENE_SOURCE')
        identity = value.scene.admission_id, value.scene.admission_revision
        if self.last_scene is not None and self.last_scene[0] == identity:
            if value.lifecycle_revision < self.last_scene[1] or value.lifecycle_revision == self.last_scene[1] and raw != self.last_scene[2]:
                raise ValueError('J_SCENE_REVISION')
            if value.lifecycle_revision == self.last_scene[1]:
                return False
        self.last_scene = identity, value.lifecycle_revision, raw
        return self._offer('scene', 'SCENE_GRANT', raw, value.valid_until_pi_us)

    def tick(self, provider):
        """nonblocking actor 회차. 실패는 J/자동 입력만 제한하며 MANUAL/ESP STOP을 호출하지 않는다."""
        now = self.clock()
        try:
            if self.closed or self.link is None or self.process.exitcode is not None:
                raise EOFError('J_PROCESS_UNAVAILABLE')
            with self._tof_lock:
                tof_inputs, self._tof_inputs = tuple(self._tof_inputs.values()), {}
            for raw in tof_inputs:
                try:
                    self.offer_tof(raw)
                except ValueError:
                    self.counts['tof_rejected'] += 1
            self.link.flush(now)
            for header, body in self.link.receive(now):
                if header['kind'] == 'STOPPED':
                    continue
                if header['kind'] != 'TRUST_CANDIDATE':
                    raise ValueError('J_LOCAL_DIRECTION')
                plan = self.owner.pending
                if plan is None or header['request_id'] != plan.request_id:
                    self.counts['old_candidate'] += 1
                    continue
                try:
                    from xenova_drive_world.mapper_clock import parse_candidate
                    _, _, p4 = parse_candidate(body)
                    if (decimal(header['issued_pi_us']) != p4 or decimal(header['expires_pi_us']) != plan.expires_us):
                        raise ValueError('J_CANDIDATE_ORIGINAL_TIME')
                    lease = self.owner.accept(plan, body, now)
                    self.counts['clock_accepted' if lease is not None else 'clock_warmup'] += 1
                    if lease is not None:
                        self.failure = None
                        provider.world_failure = None
                except ValueError:
                    self.counts['clock_rejected'] += 1
            plan = self.owner.plan(now, self.uplink_instance)
            if plan is not None:
                self.pending['probe'] = 'PROBE_PLAN', plan_json(plan), plan.issued_us, plan.expires_us, plan.request_id
            if self.link.pending is None:
                key = 'probe' if 'probe' in self.pending else next(iter(self.pending), None)
                if key is not None:
                    kind, raw, issued, expires, request = self.pending.pop(key)
                    if now < expires:
                        self.link.offer(kind, raw, issued, expires, request)
                        self.link.flush(now)
        except (OSError, EOFError, ValueError) as error:
            self.failure = type(error).__name__
            self.counts['unavailable'] += 1
            provider.world_failure = error
        provider.trust = self.owner.trust(now)

    def close(self):
        """출력 STOP/닫기 뒤 호출한다. STOPPED 진술과 실제 child exit/reap는 다르다."""
        if self.closed:
            if self.process is not None and self.process.is_alive():
                raise RuntimeError('J_UPLINK_EXIT_UNCONFIRMED')
            return
        self.closed = True
        self.pending.clear()
        if self.process is None:
            return
        now = self.clock()
        try:
            if self.process.is_alive():
                import json
                try:
                    if self.link.pending is None:
                        self.link.offer('STOP', json.dumps(dict(revision='1')).encode('ascii'), now, now + TIMEOUT_US, str(uuid4()))
                        self.link.flush(now)
                except (OSError, ValueError):
                    self.counts['stop_transport_failed'] += 1
                self.process.join(.2)
                if self.process.is_alive():
                    self.counts['forced_termination'] += 1
                    self.process.terminate()
                    self.process.join(.5)
                if self.process.is_alive():
                    self.counts['forced_kill'] += 1
                    self.process.kill()
                    self.process.join(.5)
            else:
                self.process.join(0)
        finally:
            self.link.socket.close()
        if self.process.is_alive() or self.process.exitcode is None:
            raise RuntimeError('J_UPLINK_EXIT_UNCONFIRMED')

    def restart(self):
        """critical actor 밖에서 old close/reap를 마친 명시 supervisor 호출. 자동 retry 정책은 아니다."""
        if (not self.closed or self.process is None or self.process.is_alive() or self.process.exitcode is None):
            raise RuntimeError('J_RESTART_REQUIRES_CONFIRMED_EXIT')
        self.process = self.link = None
        self.closed = False
        self.owner.pending = None
        self.owner._streak = 0
        self.last_vehicle = None
        return self.start()
