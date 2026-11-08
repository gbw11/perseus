"""명시 FD의 반대 끝에서 B08 feedback3를 수행하는 개발 물리 peer다.

caller가 준 같은 패키지 codec/Framer/정책만 사용한다. clock install은 Gateway가
검증할 원 ACK를 만들 뿐 READY/authority를 발급하지 않는다. target 적용과 실제
wheel/servo 측정을 구분한다. 하나의 Pi/device session만 지원하며 재연결은 새 owner다.
"""

from collections import OrderedDict, deque
from dataclasses import dataclass
import errno
import os
import select
import stat
import threading
from uuid import UUID

from .plant import VirtualPlant


MAX_READ_BYTES = 4096
MAX_FRAMES_PER_POLL = 16
MAX_QUEUED_FRAMES = 32
MAX_QUEUED_BYTES = 8192
U63_MAX = 2**63-1


@dataclass(frozen=True, slots=True)
class PreparedWireEvidence:
    """원 표본의 직렬화 결과다. 실제 FD 송신/수신·현재성 수락 증거는 별도다."""

    kind: int
    wire: bytes
    source_sample: int
    source_us: int
    steering_provenance: bytes | None


@dataclass(frozen=True, slots=True)
class AcceptedClockEvidence:
    """peer가 검산한 원 INSTALL payload와 그때 생성한 ACK wire다. consumer 수락은 별도다."""

    install_payload: bytes
    peer_produced_ack_wire: bytes

    def __post_init__(self):
        if type(self.install_payload) is not bytes or type(self.peer_produced_ack_wire) is not bytes:
            raise ValueError('original immutable INSTALL payload and produced ACK bytes required')


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
        raise ValueError('explicit original nonzero session UUID required')
    return UUID(value).bytes


def _sha(value):
    if type(value) is not str or len(value) != 64 or any(v not in '0123456789abcdef' for v in value):
        raise ValueError('explicit original configuration/firmware SHA256 required')
    return bytes.fromhex(value)


def _positive(*values):
    if any(type(value) is not int or not 0 < value <= U63_MAX for value in values):
        raise ValueError('original protocol counter/time must be positive u63')


def _duplicate_fd(fd):
    if os.name != 'posix' or type(fd) is not int or fd < 0:
        raise ValueError('explicit POSIX nonblocking development FD required')
    import fcntl
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    mode = os.fstat(fd).st_mode
    if not flags & os.O_NONBLOCK or flags & os.O_ACCMODE != os.O_RDWR:
        raise ValueError('caller FD must already be nonblocking and full duplex')
    if stat.S_ISSOCK(mode):
        import socket
        view = socket.socket(fileno=fd)
        try:
            if view.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
                raise ValueError('development socket must be a stream')
        finally:view.detach()
    elif not stat.S_ISCHR(mode) or not os.isatty(fd):
        raise ValueError('development FD must be a stream socket or PTY')
    # dup만 소유한다. caller FD의 blocking/TTY 설정이나 다른 끝은 변경하지 않는다.
    return os.dup(fd)


class VirtualFeedbackPeer:
    """bounded non-daemon owner다. start 또는 단일 owner poll_once 중 하나만 사용한다.

    최소 수신4096B/회, decode16frames/회, TX32frames/8192B, command8+STOPpin1,
    probe2/install2를 유지한다. partial write는 원 bytes/offset을 보존한다. EOF·fault·
    통신 손실 뒤에도 close 전까지 같은 owner가 실제 제동 물리를 진행한다. close는
    시뮬레이션 종료이며 실제 속도0을 뜻하지 않는다. clock gap은 별도 sticky invalid다.
    """

    def __init__(self, fd, plant, *, feedback, feedback3, scene_authority, protocol_profile,
                 configuration_sha256, firmware_sha256, device_session, capabilities, clock=None):
        if type(plant) is not VirtualPlant:
            raise ValueError('original VirtualPlant required')
        if (feedback3.packet is not feedback.packet or feedback3.COMMAND3 is not scene_authority.COMMAND3
                or feedback3.FEEDBACK3 is not scene_authority.FEEDBACK3
                or feedback3.FeedbackSerialSink is not feedback.FeedbackSerialSink
                or type(protocol_profile) is not feedback3.Feedback3Profile):
            raise ValueError('one coherent caller-provided B08 package/profile required')
        self._configuration, self._firmware = _sha(configuration_sha256), _sha(firmware_sha256)
        if (protocol_profile.vehicle_config_sha256 != configuration_sha256
                or protocol_profile.firmware_sha256 != firmware_sha256):
            raise ValueError('protocol profile differs from explicit source hashes')
        self._steering_profile = plant.profile.steering_sensor_profile
        measured = self._steering_profile is not None
        if (type(capabilities) is not int or capabilities & feedback3.REQUIRED_CAPS != feedback3.REQUIRED_CAPS
                or capabilities & ~0x05ff or bool(capabilities & feedback.CAP_STEERING) != measured):
            raise ValueError('estimated-servo peer cannot declare measured steering/ToF capabilities')
        if capabilities & 0x0200:
            raise ValueError('no virtual ToF source is installed')
        policy = plant.profile.fields['clock']
        if any(getattr(protocol_profile, key) != policy[key] for key in
                ('timestamp_error_us', 'drift_ppm', 'max_offset_width_us', 'lease_us')):
            raise ValueError('plant/Gateway clock assumptions differ')
        if clock is not None and clock is not plant.clock:
            raise ValueError('peer and plant must use the same original device clock callable')
        self.plant, self.clock, self.policy = plant, plant.clock, protocol_profile
        self.fb, self.f3, self.sa = feedback, feedback3, scene_authority
        self.device_session = device_session
        self._device = _uuid(device_session)
        self.capabilities = capabilities
        self._pi = None
        self._hello_raw = self._hello_ack = None
        self._steering_sensor = None
        self._combined = OrderedDict()
        self._evidence = deque(maxlen=32)
        self._evidence_lock = threading.Lock()
        self._omitted_evidence = 0
        self._clock_evidence_lock = threading.Lock()
        self._accepted_clocks = ()
        self._framer = feedback.Framer()
        self._out = deque(); self._out_bytes = self._offset = 0
        self._commands = OrderedDict(); self._probes = OrderedDict(); self._leases = OrderedDict()
        self._command_hwm = self._probe_hwm = self._probe_p1 = self._clock_hwm = self._generation = 0
        self._report_seq = self._last_sensor_us = 0
        self._clock_epoch = None
        self._sample_period_us = plant.profile.fields['odometry']['sample_period_ms']*1000
        self._physics_period_us = round(1_000_000/plant.profile.development_profile.fields['rates']['physics_hz'])
        self._active = self._stop_pin = None
        self._direction = 0
        self._last_report_sample = 0
        self._halt = threading.Event(); self._thread = None; self._owner = None
        self._closed = False; self._failure = None
        self._link_lost = self._simulation_stopped = False
        self._stats = dict(received=0, rejected=0, duplicate_commands=0, duplicate_probes=0,
            duplicate_installs=0, applied=0, sensor_samples=0, cutoff_lateness_us=0)
        self._fd = _duplicate_fd(fd)

    def _now(self):
        try:
            value = self.clock()
            _positive(value)
        except BaseException:
            self.plant.invalidate('DEVICE_CLOCK_INVALID')
            raise
        return value

    def _owned(self):
        ident = threading.get_ident()
        if self._owner is None:self._owner = ident
        if self._owner != ident:raise RuntimeError('feedback peer belongs to original byte owner')

    def _queue(self, wire):
        if type(wire) is not bytes or not 1 <= len(wire) <= self.fb.MAX_FRAME:
            raise ValueError('paired codec did not return one bounded immutable frame')
        if len(self._out) >= MAX_QUEUED_FRAMES or self._out_bytes+len(wire) > MAX_QUEUED_BYTES:
            raise ValueError('peer original TX queue exhausted')
        self._out.append(wire); self._out_bytes += len(wire)

    def _flush(self):
        if not self._out:return
        raw = self._out[0]
        try:sent = os.write(self._fd, memoryview(raw)[self._offset:])
        except BlockingIOError:return
        if sent <= 0:raise EOFError('peer write made no progress')
        self._offset += sent
        if self._offset == len(raw):
            self._out.popleft(); self._out_bytes -= len(raw); self._offset = 0

    def _sample(self, now=None):
        wheel, state = self.plant.latch(self._now() if now is None else now)
        if self._steering_sensor is not None:
            if wheel.source_us != state.observed_us:
                raise ValueError('actual servo snapshot does not match original wheel time')
            old = self._combined.get(wheel.sample_seq)
            if old is not None:
                if old[0].wheel_sample is not wheel or old[1] != state.steering_rad:
                    raise ValueError('same actual wheel latch changed measurement source')
            else:
                measurement = self._steering_sensor.sample(state.steering_rad,
                    source_sample=wheel.sample_seq, source_us=state.observed_us)
                combined = self._steering_sensor.combine(wheel, measurement)
                self._combined[wheel.sample_seq] = (combined, state.steering_rad)
                while len(self._combined) > 8:self._combined.popitem(last=False)
        return wheel, state

    def _measurement(self, sample):
        return None if self._steering_sensor is None else self._combined[sample.sample_seq][0]

    def _record(self, kind, wire, sample):
        combined = self._measurement(sample)
        provenance = None if combined is None else combined.provenance_bytes
        if provenance is not None and (type(provenance) is not bytes or len(provenance) > 8192):
            raise ValueError('bounded original steering provenance required')
        record = PreparedWireEvidence(kind, wire, sample.sample_seq, sample.source_us, provenance)
        with self._evidence_lock:
            if len(self._evidence) == self._evidence.maxlen:self._omitted_evidence += 1
            self._evidence.append(record)

    def _report(self, row, status, sample, snapshot):
        _positive(self._report_seq+1)
        self._report_seq += 1
        c = row['fields']
        combined = self._measurement(sample)
        # 명시 가상 sensor의 같은 원 latch만 measured flag를 낸다. 추정 기본값은0이다.
        payload = self.sa.FEEDBACK3.pack(c[0], c[1], c[2], c[8], c[9], c[10], status,
            self._report_seq, sample.sample_seq, sample.source_us, 5 if combined is None else 7,
            round(snapshot.velocity_mps*1000), 0 if combined is None else combined.steering_mrad,
            int(snapshot.velocity_mps == 0.),
            c[3], c[4], c[7], c[11], 0)
        wire = self.fb.packet(self.f3.REPORT, payload)
        self._record(self.f3.REPORT, wire, sample)
        return wire

    def _sensor(self, sample, now):
        lease = next((v for v in reversed(tuple(self._leases.values())) if now < v['fields'][9]), None)
        if lease is None:return
        fields = lease['fields']
        combined = self._measurement(sample)
        if combined is None:
            wire = self.plant.feedback.sensor_wire(self.f3, sample,
                clock_epoch=str(UUID(bytes=fields[2])), clock_sequence=fields[3])
        else:
            wire = combined.sensor_wire(self.f3, pi_session=str(UUID(bytes=self._pi)),
                device_session=self.device_session, clock_epoch=str(UUID(bytes=fields[2])), clock_sequence=fields[3])
        self._record(self.f3.SENSOR, wire, sample)
        self._queue(wire); self._last_sensor_us = sample.source_us
        self._stats['sensor_samples'] += 1

    def _hello(self, raw):
        version, pi, configuration = self.f3.HELLO3.unpack(raw)
        if version != 3 or configuration != self._configuration or not UUID(bytes=pi).int:
            raise ValueError('HELLO3 original version/session/configuration differs')
        if self._pi is not None and self._pi != pi:
            raise ValueError('new Pi session requires explicit new peer; no encoder reset')
        if self._hello_raw is not None:
            if raw != self._hello_raw:raise ValueError('same HELLO session changed original payload')
            self._queue(self._hello_ack)
            return
        self.plant.stop()
        self.plant.bind_sessions(str(UUID(bytes=pi)), self.device_session)
        self._pi = pi
        if self._steering_profile is not None:
            from .steering_sensor import VirtualSteeringSensor
            self._steering_sensor = VirtualSteeringSensor(self._steering_profile,
                pi_session=str(UUID(bytes=pi)), device_session=self.device_session)
        self._hello_raw = raw
        self._hello_ack = self.fb.packet(self.fb.HELLO_ACK, self.f3.ACK3.pack(3, pi, self._device,
            self.capabilities, self._configuration, self._firmware, self.plant.feedback.encoder_bits))
        self._queue(self._hello_ack)

    def _probe(self, raw, received):
        pi, device, seq, p1 = self.f3.PROBE3.unpack(raw)
        self._session(pi, device); _positive(seq, p1)
        old = self._probes.get(seq)
        if old is not None:
            if old['raw'] != raw:raise ValueError('same probe sequence changed original payload')
            self._stats['duplicate_probes'] += 1; self._queue(old['wire']); return
        if seq <= self._probe_hwm or p1 <= self._probe_p1:
            raise ValueError('original probe sequence/time regressed')
        sample, _ = self._sample()
        if old is None and self._probes and sample.sample_seq <= next(reversed(self._probes.values()))['sample']:
            raise ValueError('new probe requires a new actual latch')
        sent = self._now()
        combined = self._measurement(sample)
        if combined is None:
            wire = self.plant.feedback.clock_reply(self.f3, raw, sample,
                received_device_us=received, sent_device_us=sent)
        else:
            wire = combined.clock_reply(self.f3, raw, received_device_us=received, sent_device_us=sent)
        self._record(self.f3.CLOCK_REPLY, wire, sample)
        self._queue(wire)
        self._probes[seq] = dict(raw=raw, wire=wire, p1=p1, d2=received, d3=sent, sample=sample.sample_seq)
        self._probe_hwm, self._probe_p1 = seq, p1
        while len(self._probes) > 2:self._probes.popitem(last=False)

    def _session(self, pi, device):
        if self._pi is None or (pi, device) != (self._pi, self._device):
            raise ValueError('original HELLO Pi/device session required')

    def _install(self, raw, now):
        fields = self.f3.INSTALL3.unpack(raw)
        pi, device, epoch, sequence, probe, reference, lower, upper, expires, cutoff = fields
        self._session(pi, device); _positive(sequence, probe, reference, expires, cutoff)
        old = self._leases.get(sequence)
        if old is not None:
            if old['raw'] != raw:raise ValueError('same clock sequence changed original install')
            self._stats['duplicate_installs'] += 1; self._queue(old['wire']); return
        original = self._probes.get(probe)
        if (original is None or probe != self._probe_hwm or sequence <= self._clock_hwm
                or self._clock_hwm and probe <= self._leases[self._clock_hwm]['fields'][4]
                or not UUID(bytes=epoch).int or self._clock_epoch is not None and epoch != self._clock_epoch
                or not original['p1'] <= reference < original['p1']+self.policy.probe_timeout_us
                or not reference < expires <= original['p1']+self.policy.lease_us
                or lower > upper or upper-lower > self.policy.max_offset_width_us):
            raise ValueError('CLOCK_INSTALL3 source/probe/sequence/interval/lease differs')
        pad = 2*self.policy.timestamp_error_us+self.f3.drift_pad(self.policy.drift_ppm, reference-original['p1'])
        if (lower, upper) != (original['p1']-original['d2']-pad, reference-original['d3']+pad):
            raise ValueError('install does not preserve original four-timestamp interval')
        end_pad = self.f3.drift_pad(self.policy.drift_ppm, expires-reference)
        if not now < cutoff <= expires-(upper+end_pad)-self.policy.j_cutoff_us:
            raise ValueError('install original cutoff expired or exceeds conservative upper bound')
        if len(self._leases) >= 2:
            oldest = next(iter(self._leases))
            if self._active is not None and self._active['fields'][4] == oldest and now < self._active['fields'][7]:
                raise ValueError('previous original lease is still pinned by live command')
            self._leases.popitem(last=False)
        wire = self.fb.packet(self.f3.CLOCK_ACK, self.f3.CLOCK_ACK3.pack(pi, device, epoch,
            sequence, probe, 1, 0, now, cutoff))
        self._leases[sequence] = dict(fields=fields, raw=raw, wire=wire)
        self._clock_hwm, self._clock_epoch = sequence, epoch
        self._queue(wire)
        record = AcceptedClockEvidence(raw, wire)
        with self._clock_evidence_lock:
            self._accepted_clocks = (self._accepted_clocks+(record,))[-2:]

    def _command(self, raw, now):
        c = self.sa.COMMAND3.unpack(raw)
        pi, device, sequence, epoch, clock_seq, issued, expires, cutoff, generation, counter, kind, stop_seq, velocity, steering, reason, reserved = c
        self._session(pi, device); _positive(sequence)
        old = self._commands.get(sequence)
        if old is None and self._stop_pin is not None and self._stop_pin['fields'][2] == sequence:
            old = self._stop_pin
        if old is not None:
            if old['raw'] != raw:raise ValueError('same command sequence changed original payload')
            self._stats['duplicate_commands'] += 1
            for wire in old['wires']:self._queue(wire)
            return
        if sequence <= self._command_hwm or not 1 <= generation < 2**32 or kind not in (1, 2, 3) or reserved or not 0 <= reason <= 10:
            raise ValueError('COMMAND3 original sequence/generation/kind/reserved differs')
        if kind == 2:
            if epoch != bytes(16) or any((clock_seq, issued, expires, cutoff, stop_seq, velocity, steering)):
                raise ValueError('clockless STOP must preserve exact zero sentinel')
        else:
            _positive(clock_seq, issued, expires, cutoff, stop_seq)
            lease = self._leases.get(clock_seq)
            if (lease is None or epoch != lease['fields'][2] or generation < self._generation
                    or not issued < expires <= issued+100_000 or expires > lease['fields'][8]
                    or cutoff > lease['fields'][9] or now+self.policy.j_apply_us >= cutoff
                    or self._stop_pin is None or stop_seq != self._stop_pin['fields'][2]):
                raise ValueError('original command clock/STOP/expiry/apply budget differs')
            reference, upper = lease['fields'][5], lease['fields'][7]
            upper += self.f3.drift_pad(self.policy.drift_ppm, abs(expires-reference))
            if cutoff > expires-upper-self.policy.j_cutoff_us:
                raise ValueError('command cutoff exceeds original conservative expiry')
            state = self.plant.snapshot()
            direction = 1 if velocity > 0 else -1 if velocity < 0 else 0
            if kind == 3:
                if (velocity or not self.capabilities & 0x400 or generation != self._generation
                        or state.velocity_mps != 0.):
                    raise ValueError('HOLD_STEER requires actual stationary STOP/current generation')
            elif ((self._direction and direction and direction != self._direction)
                    or not self._direction and direction and state.velocity_mps != 0.):
                raise ValueError('direction change requires a new STOP and actual braking')
            if kind == 1 and velocity == 0 and steering != 0:
                raise ValueError('ordinary zero DRIVE is not stationary HOLD_STEER')
        row = dict(raw=raw, fields=c, wires=())
        sample, state = self._sample()
        accepted = self._report(row, 1, sample, state)
        actual_apply = self._now()
        if kind != 2 and actual_apply+self.policy.j_apply_us >= cutoff:
            self.plant.stop()
            raise ValueError('original command expired before actual setpoint application')
        if kind == 2:self.plant.stop()
        else:self.plant.apply_target(velocity/1000, steering/1000)
        applied = self._report(row, 2, sample, self.plant.snapshot())
        row['wires'] = (accepted, applied)
        self._commands[sequence] = row
        self._command_hwm = sequence; self._generation = max(generation, self._generation)
        self._active = row
        if kind == 2:self._stop_pin = row; self._direction = 0
        elif kind == 1 and velocity:self._direction = 1 if velocity > 0 else -1
        while len(self._commands) > 8:self._commands.popitem(last=False)
        self._queue(accepted)
        # 원 measurement를 먼저 보내 Gateway가 같은 sample/time의 REPORT와 대응시킨다.
        self._sensor(sample, actual_apply)
        self._queue(applied)
        self._stats['applied'] += 1

    def _receive(self, kind, raw):
        now = self._now()
        self._stats['received'] += 1
        if kind == self.fb.HELLO:self._hello(raw)
        elif kind == self.f3.CLOCK_PROBE:self._probe(raw, now)
        elif kind == self.f3.CLOCK_INSTALL:self._install(raw, now)
        elif kind == self.f3.COMMAND:self._command(raw, now)
        else:raise ValueError('unsupported peer frame; no legacy command fallback')

    def poll_once(self):
        try:
            self._owned()
            if self._link_lost and not self._simulation_stopped:
                self._physics(self._now())
                return
            self._poll_once()
        except BaseException as error:
            self._failure = error; self._stats['rejected'] += 1
            # 통신 사건 전까지의 원 setpoint 운동을 먼저 진행한다. 제동을 과거에 적용하지 않는다.
            if self.plant.snapshot().valid:
                try:
                    event_now = self._now()
                    if event_now > self.plant.snapshot().observed_us:self.plant.step(event_now)
                except BaseException:
                    self.plant.invalidate('FAULT_TRANSITION_CLOCK_INVALID')
            self.plant.stop(); self._link_lost = True
            if not self._closed:os.close(self._fd); self._closed = True
            raise

    def _physics(self, now):
        # nominal100Hz 진행과 실제 probe/command latch의 추가 substep을 구분한다.
        # 지연된 tick을 여러 과거 시각으로 catchup하지 않고 실제 elapsed 한 번만 쓴다.
        observed = self.plant.snapshot().observed_us
        if now < observed or now-observed >= self._physics_period_us:
            self.plant.step(now)

    def _poll_once(self):
        self._owned()
        if self._closed:raise RuntimeError('peer already closed')
        now = self._now()
        self._physics(now)
        if self._active is not None and self._active['fields'][10] != 2 and now >= self._active['fields'][7]:
            if self.plant.snapshot().observed_us < now:self.plant.step(now)
            lateness = now-self._active['fields'][7]
            self._stats['cutoff_lateness_us'] = max(self._stats['cutoff_lateness_us'], lateness)
            self.plant.stop(); self._active = self._stop_pin = None; self._direction = 0
            if lateness > self.policy.j_cutoff_us:
                raise ValueError('actual software cutoff overran explicit original J_cutoff budget')
        readable, writable, _ = select.select([self._fd], [self._fd] if self._out else [], [], 0)
        if readable:
            try:data = os.read(self._fd, MAX_READ_BYTES)
            except BlockingIOError:data = None
            except OSError as error:
                if error.errno == errno.EIO:raise EOFError('PTY peer disconnected') from error
                raise
            if data == b'':raise EOFError('development peer EOF')
            if data is not None:
                frames = 0
                for kind, raw in self._framer.frames(data):
                    frames += 1
                    if frames > MAX_FRAMES_PER_POLL:raise ValueError('peer RX work budget exhausted')
                    self._receive(kind, raw)
        current = self._now()
        period = self._sample_period_us
        if (self._pi is not None and any(current < row['fields'][9] for row in self._leases.values())
                and current-self._last_sensor_us >= period):
            sample, state = self._sample(current)
            if sample.source_us > self._last_sensor_us:
                self._sensor(sample, current)
                row = self._active
                if row is not None and sample.sample_seq > self._last_report_sample:
                    if row['fields'][10] == 2 or current < row['fields'][7]:
                        self._queue(self._report(row, 2, sample, state)); self._last_report_sample = sample.sample_seq
        if self._out or writable:self._flush()

    def start(self):
        if self._thread is not None or self._owner is not None or self._closed:
            raise RuntimeError('peer owner already started/used')
        self._thread = threading.Thread(target=self._run, name='development-physics-feedback3-peer', daemon=False)
        self._thread.start()
        return self

    def _run(self):
        try:
            while not self._halt.is_set():
                try:self.poll_once()
                except BaseException:
                    # 통신 실패는 물리 정지가 아니다. 실제 dt가 유효한 동안 제동을 계속한다.
                    if not self.plant.snapshot().valid:break
                self._halt.wait(.002)
        except BaseException as error:
            self._failure = error
        finally:
            self.plant.stop()
            self.plant.invalidate('SIMULATION_OWNER_CLOSED')
            self._simulation_stopped = True
            if not self._closed:os.close(self._fd); self._closed = True

    def close(self, *, timeout_s=1.):
        if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 5:
            raise ValueError('bounded peer close timeout required')
        self._halt.set(); self.plant.stop()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_s)
            if thread.is_alive():raise RuntimeError('peer thread/native FD termination unconfirmed')
        elif not self._closed:
            os.close(self._fd); self._closed = True
        if thread is None:
            self.plant.invalidate('SIMULATION_OWNER_CLOSED'); self._simulation_stopped = True

    def snapshot(self):
        """작은 진단 사본이다. 세션/READY·출력 권한이나 새 sensor 시각을 발급하지 않는다."""
        return dict(self._stats, closed=self._closed, link_lost=self._link_lost,
            simulation_stopped=self._simulation_stopped, thread_alive=self._thread is not None and self._thread.is_alive(),
            failure=None if self._failure is None else type(self._failure).__name__+': '+str(self._failure)[:160],
            pi_session=None if self._pi is None else str(UUID(bytes=self._pi)), device_session=self.device_session,
            command_hwm=self._command_hwm, control_generation=self._generation,
            clock_hwm=self._clock_hwm, queued_frames=len(self._out),
            queued_bytes=self._out_bytes, parser_invalid=self._framer.invalid,
            parser_oversize=self._framer.oversize, physical_qualified=False)

    @property
    def failure(self):
        return self._failure

    def evidence_snapshot(self):
        """최대32개 원 wire/provenance 사본참조와 생략 수. 무제한 전체 로그라고 주장하지 않는다."""
        with self._evidence_lock:
            return tuple(self._evidence), self._omitted_evidence

    def clock_evidence_snapshot(self):
        """잠긴 원 immutable tuple만 반환한다. 중복 INSTALL은 원 record/ACK를 갱신하지 않는다."""
        with self._clock_evidence_lock:
            return self._accepted_clocks
