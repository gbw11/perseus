"""A의 단일 OAK 세션과 M의 owned-byte 파서를 기동부터 종료까지 유지한다.

S가 등록한 원 BIND/SLOT만 사용한다. 준비 보고는 실제 관측/파서 완료이며
metric 지도·출력 허가·자동 재시작을 만들지 않는다. SDK는 A entry 안에서만 연다.
"""

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
import uuid

from ..yolop_profile import YolopBenchProfile
from .lifecycle_messages import LifecycleMessage, LifecyclePeer
from .lifecycle_policy import AbsoluteDeadline, CapturePairEvidence, CaptureScope, CaptureWarmup, SourceStamp
from .raw_binding import RawBindingProfile
from .raw_binding_messages import RawBindAck, RawBindContext, RawBindMessage
from .raw_leases import RawScope
from .raw_recovery import RawControlMessage, control_payload
from .raw_slot import ExpectedSlot, P0Context
from .resident_samples import CaptureAssembler, decode_sample_slot, encode_sample
from .resident_transport import ResidentLink, j_us
from .slot_cleanup import CleanupTimeLimits, ReleaseMessage
from .slot_messages import ReaderSlotContext, SlotMessage, SlotTimeLimits, WriterSlotContext


@dataclass(frozen=True)
class WorkerBoot:
    """spawn으로 전달할 등록 값. raw_tokens는 실제 생성된 SemLock handle만 담는다."""

    peer: LifecyclePeer
    profile: YolopBenchProfile
    raw_scope: RawScope
    raw_profile: RawBindingProfile
    raw_tokens: tuple
    startup_deadline_us: int
    diagnostic_directory: str
    writer_bind: RawBindMessage | None = None
    slot_limits: SlotTimeLimits = SlotTimeLimits(150_000, 500_000, 150_000)
    cleanup_limits: CleanupTimeLimits = CleanupTimeLimits(1_000_000)

    def __post_init__(self):
        for value, expected in ((self.peer, LifecyclePeer), (self.profile, YolopBenchProfile),
                                (self.raw_scope, RawScope), (self.raw_profile, RawBindingProfile),
                                (self.raw_tokens, tuple), (self.slot_limits, SlotTimeLimits),
                                (self.cleanup_limits, CleanupTimeLimits)):
            if type(value) is not expected:
                raise ValueError('worker boot requires exact registered values')
        if (self.peer.role not in ('A', 'M') or self.peer.service_instance != self.raw_scope.service_instance
                or type(self.startup_deadline_us) is not int or not 0 < self.startup_deadline_us < 2**64
                or type(self.diagnostic_directory) is not str or not self.raw_profile.nn_enabled
                or self.raw_profile.jpeg_enabled):
            raise ValueError('invalid resident worker scope/deadline')
        if self.peer.role == 'M' and type(self.writer_bind) is not RawBindMessage:
            raise ValueError('M needs the original A BIND, not a synthesized writer context')


class _StopWork(Exception):
    pass


def _exception_message(error, limit=1024):
    """원 args의 유한 텍스트만 남긴다. SDK payload repr/임의 __str__를 호출하지 않는다."""
    parts = []
    truncated = len(error.args) > 4
    for value in error.args[:4]:
        if type(value) is str:
            text = value[:limit]
            truncated = truncated or len(value) > limit
        elif type(value) in (bool, float) or value is None:
            text = str(value)
        elif type(value) is int and value.bit_length() <= 128:
            text = str(value)
        else:
            text = '<' + type(value).__name__[:64] + ' argument omitted>'
            truncated = True
        parts.append(text)
    message = ' | '.join(parts)
    return message[:limit], truncated or len(message) > limit


def _hold_device_lease(device_id):
    """승인 MXID의 배타 FD를 A process exit까지 유지한다. lock 파일은 지우지 않는다."""
    import fcntl
    import hashlib
    import stat

    root = '/tmp/xenova-device-locks-' + str(os.geteuid())
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptor = None
    try:
        parent = os.fstat(directory)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise RuntimeError('device lease directory is not owned and protected')
        name = 'oak-' + hashlib.sha256(device_id.encode('ascii')).hexdigest() + '.lock'
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1):
            raise RuntimeError('device lease file is unsafe')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError('device lease name changed')
        retained, descriptor = descriptor, None
        return retained
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


class _Actor:
    def __init__(self, endpoint, boot):
        from .resident_profile import runtime_artifacts
        actual = runtime_artifacts(boot.profile)
        if (actual['release_sha256'] != boot.peer.release_sha256
                or any(actual[name] != value for name, value in boot.raw_profile.refs.items())
                or boot.profile.device_mxid != boot.raw_profile.device_id):
            raise ValueError('actual local release/profile manifests differ from registered boot')
        self.boot = boot
        self.link = ResidentLink(endpoint, boot.peer, boot.peer.role, track_sent=True)
        self.state = 'STARTING'
        self.stop = None
        self.last_progress = None
        self.last_heartbeat = 0
        self.progress_count = 0
        self.leases_released = True
        self.device_closed = True if boot.peer.role == 'A' else None
        self.failure = None
        self.binding = None
        self.bind = None
        self.ack = None
        self.ack_sent = None
        self.last_filled_sent = None
        self.deferred = deque()
        self._hello()

    def _hello(self):
        p = self.boot.peer
        self.link.send('HELLO', dict(role=p.role, worker_instance=p.worker_instance,
                                   release_sha256=p.release_sha256, capability_mask='0'))
        self.drain_sent()

    def drain_sent(self):
        # 송신 완료 queue는32개 상한이다. 보유할 사실은 ACK 한 개뿐이다.
        for sent in self.link.take_sent():
            if self.ack is not None and sent.packet is self.ack.packet:
                self.ack_sent = sent
            if sent.packet.kind == 'SLOT' and sent.packet.payload.get('op') == 'FILLED':
                self.last_filled_sent = sent.packet

    def heartbeat(self, force=False):
        now = j_us()
        self.drain_sent()
        if force or now - self.last_heartbeat >= 100_000:
            seq, completed = (None, None) if self.last_progress is None else self.last_progress
            self.link.send('HEARTBEAT', dict(worker_state=self.state, last_progress_sequence=seq,
                                          last_progress_j_us=completed), issued_us=now)
            self.last_heartbeat = now
        self.link.flush()
        self.drain_sent()

    def progress(self, stage, sequence=None, *, completed=None, reason=None):
        completed = j_us() if completed is None else completed
        packet = self.send_required('PROGRESS', dict(stage=stage,
            source_sequence=None if sequence is None else str(sequence), completed_j_us=str(completed),
            state=self.state, reason=reason), issued_us=j_us())
        self.last_progress = packet.fields['message_sequence'], str(completed)
        self.progress_count += 1
        self.drain_sent()

    def _receive(self):
        packets = self.link.poll()
        result = []
        for packet in packets:
            if packet.kind == 'STOP':
                stop = LifecycleMessage(packet, self.boot.peer, self.boot.peer.role)
                if not stop.issued_us <= j_us() < stop.expires_us:
                    raise ValueError('STOP is outside its original lifetime')
                if self.stop is not None:
                    raise ValueError('original STOP cannot be replaced')
                self.stop = stop
            else:
                result.append(packet)
        return result

    def send_required(self, kind, payload, *, allow_stop=False, **fields):
        """원 draft 하나만 보유한다. busy 재시도는 시각/ID를 갱신하거나 post를 반복하지 않는다."""
        fields.setdefault('issued_us', j_us())
        fields.setdefault('expires_us', fields['issued_us'] + 5_000_000)
        fields.setdefault('request_id', str(uuid.uuid4()))
        while True:
            if not allow_stop and self.stop is not None:
                raise _StopWork()
            if j_us() >= fields['expires_us']:
                raise TimeoutError('original required control draft expired while busy')
            packet = self.link.send(kind, payload, **fields)
            if packet is not None:
                # ACK는 caller가 원 typed 객체를 만든 다음 실제 send 사실과 연결한다.
                return packet
            self.drain_sent()
            packets = self._receive()
            if len(self.deferred) + len(packets) > 8:
                raise RuntimeError('bounded worker control backlog is full')
            self.deferred.extend(packets)
            if not allow_stop and self.stop is not None:
                raise _StopWork()
            time.sleep(0.005)

    def poll(self):
        self.heartbeat()
        packets = list(self.deferred)
        self.deferred.clear()
        packets.extend(self._receive())
        if self.stop is not None:
            raise _StopWork()
        return packets

    def accept_bind(self, packet):
        if self.bind is not None:
            raise ValueError('resident source accepts one original RAW BIND')
        context = RawBindContext(self.boot.peer, self.boot.raw_scope.writer, self.boot.raw_profile)
        bind = RawBindMessage(packet, context, cap_us=self.boot.startup_deadline_us)
        if not bind.issued_us <= j_us() < bind.expires_us:
            raise ValueError('BIND is outside its original lifetime')
        if bind.binding.sha256 != self.boot.raw_scope.raw_binding_sha256:
            raise ValueError('BIND differs from the registered arena scope')
        now = j_us()
        ack = self.send_required('BIND_ACK', dict(binding_revision=str(bind.binding_revision),
            binding_sha256=bind.binding.sha256, accepted=True, state=self.state, reason=None),
            domain='RAW', revision=bind.binding_revision, request_id=bind.request_id,
            issued_us=now, expires_us=bind.expires_us)
        self.bind, self.binding = bind, bind.binding
        self.ack = RawBindAck(ack, bind)
        self.drain_sent()

    def await_bind(self):
        self.state = 'STARTING' if self.boot.peer.role == 'A' else 'WAIT_INPUT'
        while self.bind is None or self.ack_sent is None:
            for packet in self.poll():
                if packet.kind != 'BIND':
                    raise ValueError('expected initial RAW BIND')
                self.accept_bind(packet)
            now = j_us()
            end = self.boot.startup_deadline_us if self.bind is None else self.bind.expires_us
            if now >= end:
                raise TimeoutError('original RAW BIND/ACK deadline elapsed')
            time.sleep(0.002)
        return self.bind

    def slot(self, payload, *, request_id=None, issued=None, expires=None):
        return self.link.send('SLOT', payload, domain='RAW', revision=self.bind.binding_revision,
                              request_id=request_id, issued_us=issued, expires_us=expires)

    def required_slot(self, payload, *, request_id, issued, expires):
        return self.send_required('SLOT', payload, domain='RAW', revision=self.bind.binding_revision,
                                  request_id=request_id, issued_us=issued, expires_us=expires)

    def save(self, filename, value):
        path = Path(self.boot.diagnostic_directory)
        path.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('resident diagnostic exceeds its bound')
        with (path / filename).open('xb') as target:
            target.write(raw)

    def fault(self, error):
        configuration = (isinstance(error, ValueError) and self.state in
                         ('STARTING', 'WAIT_INPUT', 'ASSET_CHECK', 'DEVICE_BOOT', 'PIPELINE_START'))
        self.failure = type(error).__name__
        message, truncated = _exception_message(error)
        try:
            self.save('failure.json', dict(schema_version=1, role=self.boot.peer.role,
                worker_instance=self.boot.peer.worker_instance, service_instance=self.boot.peer.service_instance,
                raw_binding_sha256=self.boot.raw_scope.raw_binding_sha256, phase=self.state,
                observed_j_us=str(j_us()), exception_type=self.failure[:96], message=message,
                message_truncated=truncated, message_basis='bounded_exception_args_without_payload_repr'))
        except Exception as diagnostic_error:
            # 진단 I/O 실패가 원 FAULT의 분류·전송을 대체하지 않게 한다.
            self.failure_diagnostic_error = type(diagnostic_error).__name__[:96]
        self.state = 'FAILED'
        self.drain_sent()
        self.link.send('FAULT', dict(domain='RAW', code='CONFIG_INVALID' if configuration else 'RESIDENT_FAILED',
            last_source_sequence=None, retry_class='NO_RETRY' if configuration else
                'RESTART_CAMERA' if self.boot.peer.role == 'A' else 'RESTART_MAPPER',
            origin_context_sha256=self.boot.raw_scope.raw_binding_sha256))
        self.drain_sent()

    def finish(self):
        """진술 전송까지만 담당한다. 실제 process exit/reap은 S의 별도 사실이다."""
        try:
            self._stopped()
        finally:
            self.link.close()

    def _stopped(self):
        if self.stop is not None:
            self.state = 'CLOSING' if self.boot.peer.role == 'A' else 'QUIESCING'
            now = j_us()
            if now < self.stop.expires_us:
                success = self.failure is None and self.leases_released and self.device_closed is not False
                packet = self.send_required('STOPPED', dict(stop_revision=self.stop.payload['stop_revision'],
                    leases_released=self.leases_released, device_closed=self.device_closed,
                    checkpoint_job=None, status='STOPPED' if success else 'FAILED'),
                    request_id=self.stop.fields['request_id'], issued_us=now, expires_us=self.stop.expires_us,
                    allow_stop=True)
                while packet is not None and j_us() < self.stop.expires_us:
                    self.link.flush()
                    sent = self.link.take_sent()
                    if any(item.packet is packet for item in sent):
                        break
                    time.sleep(0.002)


def _request_payload(scope):
    return dict(op='REQUEST', arena_id=scope.arena_id, slot=None, generation=None, write_token=None,
                read_token=None, producer=scope.writer.worker_instance, consumer=None,
                raw_binding_sha256=scope.raw_binding_sha256, publication_binding_sha256=None,
                purpose=None, payload_length=None, descriptor_sha256=None)


def _source_stamp(item, stream_id):
    return SourceStamp(stream_id, item.sequence, item.device_us, item.sdk_us, item.received_j_us)


def acquisition_main(endpoint, boot):
    """하나의 Device/graph를 유지한다. M의 대기·교체는 이 세션의 재적재 원인이 아니다."""
    # 첫 SDK 배열의 import 지연을 이미 열린 queue의 수집 정체로 오판하지 않는다.
    import numpy

    actor = _Actor(endpoint, boot)
    source = writer = None
    failed = False
    try:
        bind = actor.await_bind()
        context = WriterSlotContext(bind, boot.raw_scope)
        from .native_resources import RawArenaWriter
        from ..oak.yolop_device import YolopOakSource

        writer = RawArenaWriter(boot.raw_scope, boot.raw_tokens)
        # plain FD는 함수 반환/객체 GC로 풀지 않는다. 실제 A exit 때 kernel이 회수한다.
        device_lease_fd = _hold_device_lease(boot.profile.device_mxid)
        actor.save('device-owner.json', dict(device_mxid=boot.profile.device_mxid,
                                            owner_pid=os.getpid(), lease_fd=device_lease_fd))
        source = YolopOakSource(boot.profile, context_key=bind.binding.sha256)
        actor.device_closed = False

        def stage(name):
            actor.state = name
            if name == 'DEVICE_BOOT':
                actor.progress('ASSET_VALID')
            elif name == 'PIPELINE_START':
                actor.progress('DEVICE_BOOTED')
            actor.heartbeat(force=True)

        setup = source.start(stage)
        if setup['model_asset']['blob_sha256'] != boot.raw_profile.model_id:
            raise ValueError('actual validated model differs from the accepted binding')
        actor.save('oak-setup.json', setup)
        queue_j = int(setup['queues_configured_j_us'])
        bridge = source.sample_clock()
        assembler = CaptureAssembler(bind.binding, boot.profile.sync_tolerance_us, bridge['sdk_us'])
        assembler.bridge.update(bridge)
        streams = bind.binding.fields['stream_instances']
        scope = CaptureScope(bind.binding.sha256, boot.peer.worker_instance, streams['rgb_video'],
                             streams['depth_right'], streams.get('nn_input0'), streams.get('nn_output0'))
        deadline = AbsoluteDeadline.after(queue_j, 15_000_000, now_us=j_us(), cap_us=boot.startup_deadline_us)
        warmup = CaptureWarmup(scope, queue_j, bridge['sdk_us'], deadline, now_us=j_us())
        actor.state = 'WARMUP'
        actor.progress('GRAPH_STARTED', completed=queue_j)
        next_clock = j_us() + 100_000
        last_raw = dict(rgb_video=j_us(), depth_right=j_us())
        last_nn = None
        request = pending_sample = None
        last_published = None
        capture_reported = False
        published = 0
        pause = paused_ack = last_filled = None
        write_paused = False
        while True:
            incoming = actor.poll()
            now = j_us()
            if now >= next_clock:
                assembler.bridge.update(source.sample_clock())
                next_clock = j_us() + 100_000
            for item in source.poll():
                if item.stream in last_raw:
                    last_raw[item.stream] = item.received_j_us
                if item.stream == 'nn_output':
                    if item.golden is not None:
                        if not item.golden.get('performed') or not item.golden.get('matched'):
                            raise ValueError('first actual NN SDK accessor comparison failed')
                        actor.save('nn-layout-probe.json', item.descriptor())
                    last_nn = item.received_j_us
                for sample in assembler.observe(item):
                    complete = j_us()
                    proof = CapturePairEvidence(scope, _source_stamp(sample.rgb, scope.rgb_stream),
                        _source_stamp(sample.depth, scope.depth_stream), complete, sample.fresh(complete))
                    warmup.observe(proof, now_us=complete)
                    if proof.checks_passed:
                        actor.progress('CAPTURE_PAIR', sample.rgb.sequence, completed=complete)
            now = j_us()
            if not warmup.ready and warmup.poll(now):
                raise TimeoutError('capture30 original warmup deadline elapsed')
            if warmup.ready and not capture_reported:
                actor.state = 'STREAMING'
                actor.progress('CAPTURE_READY', assembler.latest_capture.rgb.sequence, completed=warmup.ready_us)
                capture_reported = True
            if any(now - completed >= 500_000 for completed in last_raw.values()):
                raise TimeoutError('active RGB/depth stream stalled')
            if last_nn is not None and now - last_nn >= int(boot.profile.nn_stall_timeout_s * 1_000_000):
                raise TimeoutError('actual NN output stream stalled')
            for packet in incoming:
                if packet.kind == 'RAW_CONTROL':
                    op = packet.payload.get('op')
                    if op == 'PAUSE':
                        message = RawControlMessage(packet, bind)
                        message.check_live(j_us())
                        if pause is not None:
                            raise ValueError('active producer PAUSE cannot be replaced')
                        pause, write_paused = message, True
                        # 앞선 GRANT는 같은 channel에서 먼저 처리됐다. 남은 미승인 요청만 취소한다.
                        request = pending_sample = None
                    elif op == 'RESUME':
                        if paused_ack is None:
                            raise ValueError('RESUME requires original actual PAUSED statement')
                        message = RawControlMessage(packet, bind, paused_ack)
                        message.check_live(j_us())
                        pause = paused_ack = None
                        write_paused = False
                    else:
                        raise ValueError('unsupported producer raw control')
                    continue
                if packet.kind != 'SLOT' or packet.payload.get('op') != 'GRANT' or request is None:
                    raise ValueError('unexpected A control statement')
                grant = SlotMessage(packet, context, request, limits=boot.slot_limits)
                request.record.check(j_us())
                if pending_sample is None or not pending_sample.fresh(j_us()):
                    raise TimeoutError('granted sample is no longer fresh')
                encoding = encode_sample(pending_sample, grant.record.key, bind.binding)
                actor.leases_released = False
                info = writer.publish(grant.record, encoding)
                actor.leases_released = True
                payload = grant.payload
                payload.update(op='FILLED', payload_length=info.payload_length,
                               descriptor_sha256=info.descriptor_sha256)
                out = actor.required_slot(payload, request_id=grant.request_id, issued=info.completed_us,
                                 expires=grant.expires_us)
                SlotMessage(out, context, grant, limits=boot.slot_limits)
                last_filled = out
                published += 1
                actor.progress('RAW_PUBLISHED', pending_sample.rgb.sequence, completed=info.completed_us)
                last_published = pending_sample.key
                request = pending_sample = None
            actor.drain_sent()
            if write_paused and paused_ack is None and (last_filled is None or actor.last_filled_sent is last_filled):
                complete = j_us()
                reply = actor.send_required('RAW_CONTROL', control_payload('PAUSED', bind, pause.request_id),
                    domain='RAW', revision=bind.binding_revision, request_id=pause.request_id,
                    issued_us=complete, expires_us=pause.expires_us)
                paused_ack = RawControlMessage(reply, bind, pause)
            if not write_paused and request is not None and j_us() >= request.expires_us:
                raise TimeoutError('original copy request expired without GRANT')
            sample = assembler.latest(j_us())
            if (not write_paused and capture_reported and request is None
                    and sample is not None and sample.key != last_published):
                issued = j_us()
                packet = actor.slot(_request_payload(boot.raw_scope), request_id=str(uuid.uuid4()),
                    issued=issued, expires=issued + boot.slot_limits.copy_budget_us)
                if packet is not None:
                    request = SlotMessage(packet, context, limits=boot.slot_limits)
                    pending_sample = sample
            time.sleep(0.002)
    except _StopWork:
        pass
    except EOFError:
        failed = True
    except Exception as error:
        failed = True
        try:
            actor.fault(error)
        except (EOFError, OSError):
            pass
    finally:
        actor.state = 'DRAINING'
        if source is not None:
            actor.state = 'CLOSING'
            try:
                source.close()
                actor.device_closed = True
            except Exception as error:
                actor.device_closed = False
                actor.failure = type(error).__name__
                failed = True
            else:
                try:
                    actor.progress('SESSION_CLOSED')
                except Exception:
                    # 보고 통로 실패로 이미 끝난 Device close 사실을 뒤집지 않는다.
                    pass
        if writer is not None:
            try:
                writer.close()
            except Exception as error:
                actor.failure = type(error).__name__
                failed = True
        try:
            actor.finish()
        except Exception:
            failed = True
    if failed:
        raise SystemExit(1)


def mapper_main(endpoint, boot):
    """한 owned 표본만 보유해 decode한다. raw view 반환은 파서/후보 수명과 분리한다."""
    import numpy
    import cv2

    cv2.setNumThreads(1)
    actor = _Actor(endpoint, boot)
    reader = None
    failed = False
    try:
        bind = actor.await_bind()
        context = ReaderSlotContext(WriterSlotContext(boot.writer_bind, boot.raw_scope), bind)
        from .native_resources import RawArenaReader
        from ..perception.decoders import decode_depth, decode_rgb
        from ..perception import yolop

        from .resident_profile import parser_manifest_sha256
        if parser_manifest_sha256() != boot.raw_profile.parser_id:
            raise ValueError('actual local parser manifest differs from the accepted binding')

        reader = RawArenaReader(boot.raw_scope, boot.raw_tokens)
        # S가 CAPTURE_READY 후 발급한 원 ACK 시각이다. 첫 NN 수신/heartbeat로 갱신하지 않는다.
        parser_deadline = AbsoluteDeadline.after(actor.ack.issued_us, 15_000_000, now_us=j_us(),
                                                cap_us=boot.startup_deadline_us)
        actor.state = 'WARMUP'
        acquire = None
        streak = 0
        previous = None
        ready = False
        last_completion = None
        while True:
            incoming = actor.poll()
            if not ready and parser_deadline.expired(j_us()):
                raise TimeoutError('parser5 original first-output deadline elapsed')
            for packet in incoming:
                if packet.kind != 'SLOT':
                    raise ValueError('unexpected M control statement')
                if packet.payload.get('op') == 'OFFER':
                    offer = SlotMessage(packet, context, limits=boot.slot_limits)
                    now = j_us()
                    if not offer.issued_us <= now < offer.expires_us or acquire is not None:
                        continue
                    payload = offer.payload
                    payload.update(op='ACQUIRE', purpose='PARSER_WARMUP')
                    out = actor.slot(payload, request_id=str(uuid.uuid4()), issued=now,
                                     expires=now + boot.slot_limits.read_budget_us)
                    if out is not None:
                        acquire = SlotMessage(out, context, offer, limits=boot.slot_limits)
                elif packet.payload.get('op') == 'ACQUIRED':
                    if acquire is None:
                        raise ValueError('ACQUIRED has no original read request')
                    acquired = SlotMessage(packet, context, acquire, limits=boot.slot_limits)
                    acquired.record.read_request.request.check(j_us())
                    expected = ExpectedSlot(P0Context(acquired.record.key.write_key,
                        bind.binding.fields['stream_instances'], boot.raw_profile.nn_enabled),
                        acquired.record.read_request.offer.content)
                    actor.leases_released = False
                    owned, returned = reader.copy_owned(acquired.record, expected)
                    actor.leases_released = True
                    payload = acquired.payload
                    payload['op'] = 'RELEASE'
                    completed = returned.writable_posted_us
                    release = actor.required_slot(payload, request_id=acquired.request_id, issued=completed,
                        expires=completed + boot.cleanup_limits.transport_budget_us)
                    ReleaseMessage(release, acquired, limits=boot.cleanup_limits)
                    acquire = None
                    sample = decode_sample_slot(owned, bind.binding)
                    owned = None
                    # RGB/depth 전용 또는 이미 stale인 입력은 NN parser 시도가 아니다.
                    # 느린 NN 사이의 capture가 기존 성공 횟수나 원15초 기한을 바꾸지 않는다.
                    if sample.nn_output is None or not sample.fresh(j_us()):
                        continue
                    stamp = _source_stamp(sample.nn_input, bind.binding.fields['stream_instances']['nn_input0'])
                    if not stamp.advances(previous):
                        continue
                    started = j_us()
                    actor.progress('PARSER_STARTED', stamp.sequence, completed=started)
                    candidates = yolop.decode_yolop(sample.nn_output)
                    rgb = decode_rgb(sample.rgb)
                    depth = decode_depth(sample.depth)
                    completed = j_us()
                    if completed - started >= 500_000:
                        raise TimeoutError('active parser exceeded 500ms progress deadline')
                    if not sample.fresh(completed):
                        streak = 0 if not ready else streak
                        actor.progress('PARSER_COMPLETED', stamp.sequence, completed=completed,
                                       reason='STALE_INPUT')
                        continue
                    previous = stamp
                    streak += 1
                    last_completion = completed
                    if not ready and streak >= 5:
                        if parser_deadline.expired(completed):
                            raise TimeoutError('parser completion missed original readiness deadline')
                        ready = True
                        actor.state = 'WAIT_CLOCK'
                        actor.progress('NN_READY', stamp.sequence, completed=completed,
                                       reason='METRIC_NOT_READY')
                    else:
                        actor.progress('PARSER_COMPLETED', stamp.sequence, completed=completed,
                                       reason='METRIC_NOT_READY')
                    # 후보는 이번 owned 원본에만 결박한다. 아직 metric/world 소비자는 없다.
                    candidates = rgb = depth = sample = None
                else:
                    raise ValueError('unsupported M SLOT operation')
            if acquire is not None and j_us() >= acquire.expires_us:
                raise TimeoutError('original read request expired without ACQUIRED')
            if (ready and last_completion is not None
                    and j_us() - last_completion >= int(boot.profile.nn_stall_timeout_s * 1_000_000)):
                # WAIT_CLOCK 자체는 hang이 아니다. 원 NN의 새 유효 해석 부재를 별도로 감시한다.
                raise TimeoutError('fresh NN parser association stalled')
            time.sleep(0.002)
    except _StopWork:
        pass
    except EOFError:
        failed = True
    except Exception as error:
        failed = True
        try:
            actor.fault(error)
        except (EOFError, OSError):
            pass
    finally:
        actor.state = 'QUIESCING'
        if reader is not None:
            try:
                reader.close()
            except Exception as error:
                actor.failure = type(error).__name__
                failed = True
        try:
            actor.finish()
        except Exception:
            failed = True
    if failed:
        raise SystemExit(1)
