"""실제 S/A/M/D의 정상 기동·상주·종료를 연결한다. 지도·이동 권위는 발행하지 않는다.

S-main은 lifecycle/lease/원 기한과 spawn/sentinel만 소유한다. 파일·store·native
자원 준비는 S-log, Device는 A, owned-byte 해석은 M, SQLite는 D에 둔다.
실제 외부 clock/odom/metric 입력 부재는 WAIT로 남기고 정상 종료도
CLEANUP_CANDIDATE까지만 기록한다. 비정상 재기동을 새 자원 set으로 우회하지 않는다.
"""

from collections import deque
from dataclasses import dataclass
import multiprocessing
import os
from pathlib import Path
import selectors
import signal
import socket
import sys
import threading
import traceback
import uuid

from .async_resource_log import AsyncResourceLog
from .channel import control_pair
from .lifecycle_messages import LifecyclePeer, LifecycleReceiver
from .lifecycle_policy import AbsoluteDeadline, LocalSetupWatch, validate_state
from .native_resources import NativeResources, TOKEN_SLOTS, DB_COPY_BYTES
from .raw_binding import RawBinding, RawBindingProfile
from .raw_binding_messages import RawBindAck, RawBindContext, RawBindMessage
from .raw_leases import ARENA_BYTES, Endpoint, RawScope
from .resident_broker import ResidentRawBroker
from .resident_db import DbOutbound, ResidentDbBroker
from .resident_profile import canonical, publish_runtime_artifacts, runtime_artifacts
from .resident_storage import (StorageBoot, StorageProfile, binding_payload, discovery_payload,
                               parse_bootstrap_result, parse_storage_bind_ack, storage_binding, storage_main)
from .resident_transport import ResidentLink, j_us
from .resident_workers import WorkerBoot, acquisition_main, mapper_main
from .resident_recovery import ResidentRecovery
from .resource_manifest import ResourceManifest


def _identifier():
    return str(uuid.uuid4())


def _redirect_worker_output():
    """SDK의 native stdout도 stderr로 보낸다. S의 최종 JSON stdout은 독립이다."""
    os.dup2(2, 1)


def _worker_entry(endpoint, boot):
    # 터미널 Ctrl+C는 S만 처리하고 자식은 원 STOP과 실제 reap 순서를 따른다.
    # SIGTERM은 유지해 supervisor의 후속 강제 회수가 가능하게 한다.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _redirect_worker_output()
    target = {'A': acquisition_main, 'M': mapper_main, 'D': storage_main}[boot.peer.role]
    target(endpoint, boot)


def _decimal(value):
    if type(value) is not str or not value.isascii() or not value.isdigit():
        raise ValueError('expected canonical u64 string')
    parsed = int(value)
    if not 0 <= parsed <= 2**64 - 1 or str(parsed) != value:
        raise ValueError('expected canonical u64 string')
    return parsed


@dataclass
class _Worker:
    peer: LifecyclePeer
    process: object
    link: ResidentLink
    receiver: LifecycleReceiver
    spawned_us: int
    local_watch: object = None
    admitted: bool = False
    hello: bool = False
    bind: object = None
    bind_draft: object = None
    bind_sent: object = None
    bind_ack: object = None
    discovery: object = None
    bootstrap: object = None
    stop: object = None
    stopped: object = None
    eof: bool = False
    exited_us: int | None = None
    reaped: bool = False
    last_heartbeat_us: int | None = None
    held: object = None
    pid: int | None = None
    exitcode: int | None = None
    failed_pending: object = None
    startup_deadline_us: int = 0
    stop_draft: object = None
    term_sent_us: int | None = None
    kill_sent_us: int | None = None
    parse_started_us: int | None = None
    parse_sequence: int | None = None
    compute_watch: object = None
    source: object = None


class _Runtime:
    def __init__(self, profile, state_directory, storage_profile, output, duration_s, service_scope=None, output_root=False,
                 perception_profile_bytes=None,jio_profile=None,mapper_profile_bytes=None):
        self.profile, self.state_directory = profile, str(Path(state_directory).expanduser().absolute())
        self.storage_profile, self.output, self.duration_s = storage_profile, Path(output).expanduser().absolute(), duration_s
        self.context = multiprocessing.get_context('spawn')
        self.service, self.epoch, self.bootstrap_request = _identifier(), _identifier(), _identifier()
        self.clock_epoch = _identifier()
        if output_root:
            self.output = self.output / self.service
        self.service_scope = service_scope
        self.perception_profile_bytes=perception_profile_bytes
        self.jio_profile=jio_profile
        self.mapper_profile_bytes=mapper_profile_bytes
        if mapper_profile_bytes is not None and jio_profile is None:
            raise ValueError('operational mapper configuration requires the explicit JIO/source policy')
        if jio_profile is not None:
            from ..perception.calibration import CalibrationProfile
            if perception_profile_bytes is None:raise ValueError('JIO needs the explicit pinned camera calibration profile')
            import json
            jio_profile.source_policy.check_profile(CalibrationProfile.from_dict(json.loads(perception_profile_bytes)))
        self.jio=None
        self.source_revision=0
        self.storage_recovery = None
        self.identities = {r: (_identifier(), _identifier()) for r in ('A', 'M', 'D')}
        self.began = j_us()
        self.startup_deadline = self.began + 90_000_000
        self.selector = selectors.DefaultSelector()
        self.workers, self.birth_pending, self.birth_admitting = {}, set(), set()
        self.manifest = self.latest_ack = self.artifacts = self.handles = None
        self.binding = self.raw_profile = self.scope = self.broker = None
        self.db_broker = self.db_copy_cleanup = None
        self.db_outbox = deque()
        self.db_selected = {}
        self.db_last_progress_us = None
        self.db_recovery_operation = None
        self.publication_context = None
        self.native = NativeResources(self.context)
        self.log = AsyncResourceLog(self.state_directory, self.service, native=self.native,
            prepare=self._prepare, bootstrap=self._admit_runtime, publication=self._record_publication,
            bootstrap_with_request=True)
        self.admission = None
        self.log_pending = None
        self.allocations = deque(('RAW_CURRENT', 'DB_COPY_0') + TOKEN_SLOTS)
        self.allocating = self.allocation_deadline = None
        self.phase = 'PREPARE'
        self.capture_ready_us = self.nn_ready_us = self.storage_ready_us = None
        self.last_capture_us = self.parser_deadline = self.resident_started_us = None
        self.last_fresh_parser_us = None
        self.progress = {}
        self.stopping = self.stop_us = self.stop_reason = None
        self.failure = None
        self.failure_trace = None
        self.native_closed = self.candidate_written = False
        self.native_started = False
        self.cleanup_attempted = self.cleanup_failed = False
        self.a_phase = None
        self.a_phase_deadlines = {}
        self.log_stop_requested = False
        self.output_owned = False
        self.events = deque(maxlen=64)
        self.recovery = ResidentRecovery(self)

    def _admit_runtime(self, store, batch, request):
        from .runtime_admission import consume_runtime_state
        return consume_runtime_state(store, batch, resource_epoch=self.epoch,
            request_id=request.request_id, issued_us=request.issued_us, expires_us=request.expires_us,
            service_scope=self.service_scope, native_owner=self.native)

    def _record_publication(self, store, ack, receipt):
        from .runtime_admission import record_runtime_publication
        record_runtime_publication(store, ack, receipt, service_scope=self.service_scope)

    def _prepare(self):
        self.output.mkdir(mode=0o700)
        self.output_owned = True
        artifacts = runtime_artifacts(self.profile,self.perception_profile_bytes,self.mapper_profile_bytes)
        if self.mapper_profile_bytes is not None:
            from .mapper_profile import MapperProfile
            from ..perception.calibration import CalibrationProfile
            import json
            calibration=CalibrationProfile.from_dict(json.loads(artifacts['manifests']['perception-calibration.json']))
            self.jio_profile.source_policy.check_profile(calibration)
            MapperProfile.from_bytes(artifacts['manifests']['mapper-profile.json']).check(calibration,
                vehicle_configuration=self.jio_profile.source_policy.configuration,
                vehicle_calibration=self.jio_profile.source_policy.calibration)
        artifacts['published'] = publish_runtime_artifacts(self.output / 'artifacts', artifacts)
        return artifacts

    def event(self, kind, **fields):
        self.events.append(dict(event=kind, observed_j_us=str(j_us()), **fields))

    def _request(self, operation, purpose, *, cap_us=None, request_id=None, **kwargs):
        if self.log_pending is not None:
            return False
        now = j_us()
        end = min(now + (15_000_000 if operation in ('PREPARE', 'BOOTSTRAP') else 5_000_000),
                  self.stop_us + 25_000_000 if self.stopping else self.recovery.operation_cap,
                  cap_us if cap_us is not None else 2**64 - 1)
        if now >= end:
            raise TimeoutError('original resource operation budget elapsed')
        request = request_id or _identifier()
        if not self.log.submit(request, operation, issued_us=now, expires_us=end, **kwargs):
            raise RuntimeError('resource request was not accepted by the idle owner')
        if operation == 'CREATE':
            self.native_started = True
        elif operation == 'CLEANUP':
            self.cleanup_attempted = True
        self.log_pending = (request, operation, purpose, end)
        return True

    def _write(self, fields, purpose, *, cap_us=None):
        fields['revision'] = str(self.manifest.revision + 1)
        return self._request('WRITE', purpose, manifest=ResourceManifest(canonical(fields)),
                             expected_ref=self.manifest.ref, cap_us=cap_us)

    @staticmethod
    def _owners(fields, records):
        for record in records:
            old = next((item for item in fields['owners'] if (item['role'], item['phase']) == (record.role, record.phase)), None)
            value = dict(role=record.role, phase=record.phase, pid=record.pid,
                         start_ticks=None if record.start_ticks is None else str(record.start_ticks))
            if old is None:
                raise RuntimeError('owner was not durably declared before construction')
            if old['pid'] is not None and old != value:
                raise RuntimeError('actual owner birth changed during the initial group')
            old.update(value)

    def _complete_log(self):
        completion = self.log.poll()
        if completion is None:
            return
        if self.log_pending is None or (completion.request_id, completion.operation) != self.log_pending[:2]:
            raise RuntimeError('resource completion differs from the original outstanding request')
        _, operation, purpose, _ = self.log_pending
        self.log_pending = None
        if not completion.timely(j_us()):
            if purpose == 'NATIVE_CLOSE':
                self.cleanup_failed = True
            raise RuntimeError(completion.error or 'late resource completion is historical only')
        if operation in ('BOOTSTRAP', 'WRITE'):
            if (completion.ack is None or completion.receipt is None
                    or completion.receipt.outcome.kind != 'VALID'
                    or completion.receipt.outcome.manifest.ref != completion.ack.target_ref):
                raise RuntimeError('actual canonical durable target was not confirmed')
            self.manifest, self.latest_ack = completion.receipt.outcome.manifest, completion.ack
        if self.stopping and purpose not in ('NATIVE_CLOSE', 'CANDIDATE'):
            return
        if purpose.startswith('RECOVERY_'):
            self.recovery.completed(purpose, completion)
            return
        if purpose == 'PREPARE':
            self.artifacts = completion.value
            self._request('BOOTSTRAP', 'BOOTSTRAP', request_id=self.bootstrap_request)
        elif purpose == 'BOOTSTRAP':
            self.admission = completion.value
            self.phase = 'ALLOCATING'
        elif purpose == 'INTENT':
            self._request('CREATE', 'CREATE', intent_ack=self.latest_ack, cap_us=self.allocation_deadline)
        elif purpose == 'CREATE':
            record = completion.value
            fields = self.manifest.fields
            fields['resources'][self.allocating] = dict(state=record.state, name=record.name,
                object_id=record.object_id, size_bytes=None if record.size_bytes is None else str(record.size_bytes))
            fields['intents'] = []
            self._owners(fields, completion.owner_births)
            self._write(fields, 'KNOWN', cap_us=self.allocation_deadline)
        elif purpose == 'KNOWN':
            self.allocating = None
        elif purpose == 'HANDLES':
            self.handles = completion.value
            self._configure_binding()
            self._spawn('A')
            self._spawn('D')
            self.phase = 'WORKERS'
        elif purpose == 'BIRTHS':
            fields = self.manifest.fields
            self._owners(fields, completion.owner_births)
            self._write(fields, 'OWNERS')
        elif purpose == 'OWNERS':
            for role in self.birth_admitting:
                self.workers[role].admitted = True
            self.birth_pending.difference_update(self.birth_admitting)
            self.birth_admitting.clear()
        elif purpose == 'NATIVE_CLOSE':
            self.native_closed = True
        elif purpose == 'CANDIDATE':
            self.candidate_written = True

    def _configure_binding(self):
        streams = {name: _identifier() for name in ('rgb_video', 'depth_right', 'nn_input0', 'nn_output0')}
        value = dict(service_instance=self.service, acquisition_worker=self.identities['A'][0],
            pipeline_instance=_identifier(), capture_epoch=_identifier(), stream_instances=streams,
            sdk_bridge_epoch=_identifier(), clock_epoch=self.clock_epoch, device_id=self.profile.device_mxid,
            **{k: self.artifacts[k] for k in ('model_id', 'parser_id', 'graph_profile_sha256', 'calibration_bundle', 'format_table_sha256')})
        self.binding = RawBinding(value)
        self.raw_profile = RawBindingProfile(value['device_id'], value['clock_epoch'], value['model_id'],
            value['parser_id'], value['graph_profile_sha256'], value['format_table_sha256'], value['calibration_bundle'], True, False)
        self.scope = RawScope(self.service, self.handles.raw.arena_id,
            Endpoint(self.identities['A'][1], self.identities['A'][0]), self.binding.sha256,
            self.handles.raw.name, self.handles.raw.size_bytes)

    def _spawn(self, role, *, deadline_us=None):
        deadline_us = self.recovery.operation_cap if deadline_us is None else deadline_us
        worker_id, epoch = self.identities[role]
        peer = LifecyclePeer(self.service, epoch, worker_id, role, self.artifacts['release_sha256'])
        parent=child=data_parent=data_child=data_peer=None
        mapper_instance=process=host=None
        tracked=False
        try:
            parent,child=control_pair()
            if role=='M' and self.jio_profile is not None:
                from .data_channel import DataPeer,data_pair
                data_parent,data_child=data_pair()
                data_peer=DataPeer(self.service,worker_id,_identifier())
                mapper_instance=_identifier()
            for endpoint in (parent,child):
                endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,65_536)
                endpoint.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,65_536)
            if role=='D':
                boot=StorageBoot(peer,deadline_us,self.storage_profile,
                    copy_handles=(self.handles.db_copy,self.handles.db_tokens),
                    diagnostic_directory=str(self.output/role/worker_id),recovery=self.storage_recovery)
            else:
                boot=WorkerBoot(peer,self.profile,self.scope,self.raw_profile,self.handles.raw_tokens,
                    deadline_us,str(self.output/role/worker_id),
                    writer_bind=self.workers['A'].bind if role=='M' else None,
                    perception_profile_bytes=self.artifacts['manifests'].get('perception-calibration.json'),
                    copy_handles=(self.handles.db_copy,self.handles.db_tokens) if role=='M' else None,
                    initial_storage_material=self.storage_material() if role=='M' else None,
                    data_endpoint=data_child,data_peer=data_peer,mapper_instance=mapper_instance,
                    source_policy=None if data_child is None else self.jio_profile.source_policy,
                    mapper_device=None if data_child is None else self.jio_profile.device,
                    clock_policy=None if data_child is None else self.jio_profile.clock_policy,
                    mapper_profile_bytes=self.artifacts['manifests'].get('mapper-profile.json') if role=='M' else None,
                    mapper_profile_sha256=self.artifacts.get('mapper_profile_sha256') if role=='M' else None)
            source_owner=None
            if data_parent is not None:
                from .data_channel import DataLink
                from .source_runtime import SupervisorSource
                source_owner=SupervisorSource(DataLink(data_parent,data_peer,'S'),self.binding,self.jio,
                    mapper_instance=mapper_instance,mapper_boot=self.manifest.runtime.boot_id,
                    clock_epoch=self.clock_epoch,revision_floor=self.source_revision,clock=j_us)
            process=self.context.Process(name='xenova-'+role,target=_worker_entry,args=(child,boot),daemon=False)
            link=ResidentLink(parent,peer,'S',track_sent=True)
            spawned=j_us()
            host=_Worker(peer,process,link,LifecycleReceiver(peer,'S'),spawned,
                LocalSetupWatch(role,worker_id,spawned,now_us=j_us()) if role!='A' else None,
                held=deque(maxlen=8),startup_deadline_us=deadline_us,source=source_owner)
            # 사용자 공간 구성 실패는 실제 child를 만들기 전에 모두 드러낸다.
            process.start()
            self.workers[role]=host;tracked=True
            host.pid=process.pid
            self.birth_pending.add(role)
            if data_parent is not None:
                self.selector.register(data_parent,selectors.EVENT_READ,('source',role))
            self.selector.register(parent,selectors.EVENT_READ,('control',role))
            self.selector.register(process.sentinel,selectors.EVENT_READ,('exit',role))
            self.event('SPAWN',role=role,pid=host.pid,
                send_buffer=parent.getsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF),
                receive_buffer=parent.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF))
        except BaseException:
            # start 예외라도 실제 Popen/PID가 있으면 원 owner를 버리거나 종료를 발명하지 않는다.
            if not tracked and host is not None and process.pid is not None:
                host.pid=process.pid;self.workers[role]=host;tracked=True
                self.birth_pending.add(role)
            if not tracked:
                for endpoint in (parent,data_parent):
                    if endpoint is not None:
                        try:endpoint.close()
                        except OSError:self.failure=self.failure or 'pre-spawn endpoint close failed'
                if process is not None:
                    try:process.close()
                    except (OSError,ValueError):pass
            raise
        finally:
            # 실제 spawn 뒤에도 부모의 child-side 사본만 닫는다. parent는 tracked host가 소유한다.
            for endpoint in (child,data_child):
                if endpoint is not None:
                    try:endpoint.close()
                    except OSError:self.failure=self.failure or 'child-side endpoint close failed'

    def storage_material(self):
        """실제 D discovery/BIND가 끝난 작은 원본만 반환한다. EMPTY에서 catalog head를 만들지 않는다."""
        host=self.workers.get('D')
        if (host is None or self.recovery.suppressed('D',host) or host.bind_ack is None or host.bootstrap is None
                or host.bootstrap['status'] not in ('EMPTY','COMPLETE')):
            return None
        snapshot=dict(host.bootstrap)
        if snapshot['selected_checkpoint'] is not None:snapshot['selected_checkpoint']=dict(snapshot['selected_checkpoint'])
        return (storage_binding(host.peer,self.storage_profile,host.bootstrap),snapshot)

    def set_publication(self,binding,storage_context):
        """source actor가 수락한 원 binding/context를 결합한다. Pi clock/odom/scene 사용권 생성은 아니다."""
        from ..storage.contracts import StorageContext,canonical as context_canonical
        self._ensure_db_broker()
        if self.db_broker is None or type(storage_context) is not StorageContext:
            raise RuntimeError('publication needs the actual current storage context')
        if (storage_context.database_uuid!=self.db_broker.binding['database_uuid']
                or storage_context.source_binding!=context_canonical(binding)
                or storage_context.mapper_worker!=self.workers['M'].peer.worker_instance
                or storage_context.origin_mapper_instance!=binding['mapper_instance']
                or any(getattr(storage_context,name)!=binding[name] for name in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration'))
                or storage_context.primitive_sha256!=binding['primitive'] or binding['raw_binding_sha256']!=self.binding.sha256):
            raise RuntimeError('publication context differs from admitted original source')
        self.publication_context=storage_context
        self._queue_db(self.db_broker.set_publication(binding,storage_context.sha256,
            geometry_origin=storage_context.geometry_origin_fields))

    def request_storage_bootstrap_load(self,request):
        """COMPLETE의 원 selected ref에 한정해 pre-publication LOAD를 시작한다."""
        from ..storage.contracts import LoadRequest,json_bytes
        self._ensure_db_broker();material=self.storage_material()
        if (self.db_broker is None or material is None or material[1]['status']!='COMPLETE' or type(request) is not LoadRequest
                or json_bytes(request.checkpoint_ref,2048)!=material[1]['selected_checkpoint']):
            raise RuntimeError('bootstrap restore needs the actual selected complete checkpoint')
        if self.db_broker.pending is not None or self.db_broker.aborted is not None:return False
        self._queue_db(self.db_broker.request_bootstrap_load(request))
        return True

    def _queue_db(self,drafts):
        for draft in drafts:
            if type(draft) is not DbOutbound:raise RuntimeError('unsupported DB draft')
            if draft in self.db_outbox:continue
            if len(self.db_outbox)>=8:raise RuntimeError('bounded DB control staging is full')
            self.db_outbox.append(draft)

    def _ensure_db_broker(self):
        material=self.storage_material();mapper=self.workers.get('M');storage=self.workers.get('D')
        if material is None or mapper is None or not mapper.admitted or storage.eof:return
        binding,_=material
        if self.db_broker is None:
            self.db_broker=ResidentDbBroker(storage.peer,mapper.peer,binding,self.handles.db_copy)
            return
        if self.db_broker.mapper!=Endpoint(mapper.peer.channel_epoch,mapper.peer.worker_instance):
            attempt=self.recovery.active;old=None if attempt is None else attempt.old.get('M')
            if old is None or not old.reaped:raise RuntimeError('DB mapper replacement requires actual old-M reap')
            self.db_broker.replace_mapper(mapper.peer);self.publication_context=None
        if self.db_broker.storage!=Endpoint(storage.peer.channel_epoch,storage.peer.worker_instance):
            attempt=self.recovery.active;old=None if attempt is None else attempt.old.get('D')
            if old is None or not old.reaped:raise RuntimeError('DB storage replacement requires actual old-D reap')
            self._queue_db(self.db_broker.rebind_storage(storage.peer,binding))

    def _drive_db(self):
        self._ensure_db_broker()
        if self.db_broker is None:return
        attempt=self.recovery.active
        if attempt is not None and self.db_recovery_operation!=attempt.operation_id:
            self.db_recovery_operation=attempt.operation_id
            self.db_broker.fence('RECOVERY_'+attempt.kind)
        self._queue_db(self.db_broker.tick())
        for _ in range(len(self.db_outbox)):
            draft=self.db_outbox.popleft()
            if not self.db_broker.dispatchable(draft):
                self._queue_db(self.db_broker.abandon_unselected(draft))
                continue
            host=self.workers.get(draft.role)
            if host is None or host.eof:
                self.db_outbox.append(draft);continue
            packet=host.link.send(**draft.kwargs)
            if packet is None:
                self.db_outbox.append(draft);continue
            self.db_broker.note_selected(draft,packet)
            key=(host.peer.channel_epoch,packet.fields['message_sequence'])
            if len(self.db_selected)>=8:raise RuntimeError('bounded selected DB send records are full')
            self.db_selected[key]=draft
            self._sent(host)
        lease=self.db_broker.cleanup_ready
        if lease is not None:
            if self.db_copy_cleanup is None:
                from .storage_copy import CopyArena
                self.db_copy_cleanup=CopyArena(self.handles.db_copy,self.handles.db_tokens,None,self.service)
            returned=self.db_copy_cleanup.discard_ready(lease)
            self._queue_db(self.db_broker.note_discarded(returned))
        notice=self.db_broker.take_aborted()
        if notice is not None:
            mapper=self.workers.get('M')
            if mapper is not None and mapper.source is not None and notice.mapper.worker_instance==mapper.peer.worker_instance:
                mapper.source.offer_abort(notice)
            else:
                self.event('DB_ABORT_HISTORY',request_id=notice.request_id,origin_context_sha256=notice.origin_context_sha256)

    def _dispatch_db(self,role,packet):
        if self.db_broker is None:raise RuntimeError('post-bootstrap DB statement has no actual current broker')
        drafts=self.db_broker.receive_mapper(packet) if role=='M' else self.db_broker.receive_storage(packet)
        self._queue_db(drafts)
        if role=='D':
            self.db_last_progress_us=j_us()
            if (not self.stopping and packet.kind=='DB_JOB' and packet.payload.get('op')=='RESULT'
                    and packet.payload['body']['status']=='UNKNOWN'):
                self.recovery.fail('D','D persistence transaction outcome is unknown; original history must be queried')

    def _dispatch(self, role, packet):
        host, now = self.workers[role], j_us()
        if self.stopping or self.recovery.suppressed(role, host):
            if packet.kind == 'STOPPED':
                received = host.receiver.receive(packet, now_us=now)
                if received.message is not None:
                    host.stopped = received.message
                    self.event('STOPPED', role=role, payload=received.message.payload)
            elif (packet.kind == 'SLOT' and packet.fields['binding_domain']=='STORAGE'
                  and packet.payload.get('op')=='RELEASE' and self.db_broker is not None):
                self._dispatch_db(role,packet)
            elif (packet.kind=='DB_JOB' and packet.fields['binding_domain']=='STORAGE' and packet.fields['binding_revision']=='1'
                  and packet.payload.get('op') in ('READY','RESULT','RANGE_RESULT') and self.db_broker is not None):
                self._dispatch_db(role,packet)
            elif (packet.kind == 'SLOT' and packet.fields['binding_domain']=='RAW' and self.broker is not None
                  and (packet.payload.get('op') == 'RELEASE'
                       or not self.stopping and packet.payload.get('op') == 'FILLED')):
                self.broker.receive(role, packet, now_us=now)
            elif packet.kind == 'PROGRESS' and packet.payload.get('stage') == 'SESSION_CLOSED':
                self._progress(role, packet, now)
            return
        if packet.kind == 'RAW_CONTROL':
            self.recovery.receive_control(role, packet)
            return
        if not host.admitted:
            if len(host.held) == 8:
                raise RuntimeError('pre-admission worker mailbox is full')
            host.held.append(packet)
            return
        if packet.kind in ('HELLO', 'HEARTBEAT', 'STOPPED'):
            received = host.receiver.receive(packet, now_us=now)
            if received.message is None:
                return
            message = received.message
            if message.kind == 'HELLO':
                if host.exited_us is not None:
                    raise RuntimeError('HELLO cannot admit an already exited owner')
                host.hello = True
                if host.local_watch is not None:
                    host.local_watch.hello(host.peer.worker_instance, message.issued_us, now_us=now)
                self.event('HELLO', role=role)
            elif message.kind == 'HEARTBEAT':
                host.last_heartbeat_us = message.issued_us
                if role == 'A':
                    self._a_stage(message.payload['worker_state'], message.issued_us)
                elif role=='D' and message.payload['worker_state']=='FAILED':
                    self.recovery.fail('D','D reported a failed persistence owner')
            else:
                host.stopped = message
                self.event('STOPPED', role=role, payload=message.payload)
            return
        if not host.hello:
            raise RuntimeError('worker data arrived before accepted HELLO')
        if ((packet.kind=='DB_JOB' and packet.fields['binding_revision']!='0')
                or packet.kind=='SLOT' and packet.fields['binding_domain']=='STORAGE'
                or packet.kind=='BIND_ACK' and packet.fields['binding_domain']=='PUBLICATION'):
            self._dispatch_db(role,packet)
            return
        if packet.kind == 'BIND_ACK':
            if host.bind is None or host.bind_sent is None:
                raise RuntimeError('BIND_ACK has no original successful send')
            if _decimal(packet.fields['issued_j_us']) < host.bind_sent.started_us:
                raise RuntimeError('BIND_ACK predates the original successful send lower bound')
            if role == 'D':
                payload = parse_storage_bind_ack(packet, host.peer, host.bind, now_us=now)
                host.bind_ack = packet
                self.storage_ready_us = _decimal(packet.fields['issued_j_us'])
            else:
                ack = RawBindAck(packet, host.bind)
                if not ack.issued_us <= now < ack.expires_us or not ack.accepted:
                    raise RuntimeError('RAW BIND_ACK is rejected or outside its original lifetime')
                host.bind_ack = ack
                if role == 'M':
                    self.broker.bind_reader(host.bind)
                    self.parser_deadline = min(host.startup_deadline_us,
                        max(self.capture_ready_us, ack.issued_us) + 15_000_000)
            self.event('BIND_ACK', role=role)
        elif packet.kind == 'DB_JOB' and role == 'D':
            host.bootstrap = parse_bootstrap_result(packet, host.peer, self.storage_profile, host.discovery,
                                                   now_us=now, recovery=self.storage_recovery)
            if host.bootstrap['status'] not in ('EMPTY','COMPLETE'):
                self.recovery.fail('D', 'storage bootstrap is unavailable: ' + host.bootstrap['status'], retryable=False)
                return
            self.event('STORAGE_BOOTSTRAP', database_uuid=host.bootstrap['database_uuid'], status=host.bootstrap['status'])
        elif packet.kind == 'PROGRESS':
            self._progress(role, packet, now)
        elif packet.kind == 'SLOT' and self.broker is not None:
            self.broker.receive(role, packet, now_us=now)
        elif packet.kind == 'FAULT':
            fields = packet.payload
            if set(fields) != {'domain', 'code', 'last_source_sequence', 'retry_class', 'origin_context_sha256'}:
                raise ValueError('FAULT has unsupported fields')
            if (fields['origin_context_sha256'] != self.binding.sha256 or fields['domain'] != 'RAW'
                    or fields['retry_class'] not in ('NO_RETRY', 'RESTART_CAMERA', 'RESTART_MAPPER')):
                raise ValueError('FAULT differs from current RAW context or supported retry policy')
            self.recovery.fail(role, 'worker ' + role + ' fault: ' + str(fields['code']),
                               retryable=fields['retry_class'] != 'NO_RETRY')
        else:
            raise ValueError('unsupported resident worker statement')

    def _progress(self, role, packet, now):
        fields, payload = packet.fields, packet.payload
        if (fields['binding_domain'] != 'LIFECYCLE' or fields['binding_revision'] != '0'
                or set(payload) != {'stage', 'source_sequence', 'completed_j_us', 'state', 'reason'}):
            raise ValueError('invalid progress envelope or exact payload')
        completed, issued, expires = (_decimal(payload['completed_j_us']), _decimal(fields['issued_j_us']), _decimal(fields['expires_j_us']))
        if not completed <= issued <= now or not self.stopping and now >= expires:
            raise ValueError('progress changed its original completion or lifetime')
        validate_state(role, payload['state'])
        from .mapper_progress import STAGES,MapperJobWatch
        if role=='M' and payload['stage'] in STAGES:
            host=self.workers['M']
            if host.bind_ack is None:
                raise RuntimeError('mapper job has no current admitted raw binding')
            if host.compute_watch is None:
                host.compute_watch=MapperJobWatch()
            source=None if payload['source_sequence'] is None else _decimal(payload['source_sequence'])
            host.compute_watch.observe(fields['request_id'],payload['stage'],source,completed,
                                       payload['reason'],now_us=now)
            return
        allowed = {'A': {'ASSET_VALID', 'DEVICE_BOOTED', 'GRAPH_STARTED', 'CAPTURE_PAIR', 'CAPTURE_READY', 'RAW_PUBLISHED', 'SESSION_CLOSED'},
                   'M': {'PARSER_STARTED', 'NN_READY', 'PARSER_COMPLETED'}, 'D': set()}
        if payload['stage'] not in allowed[role]:
            raise ValueError('unsupported producer stage')
        sequence = None if payload['source_sequence'] is None else _decimal(payload['source_sequence'])
        key = role, payload['stage']
        previous = self.progress.get(key)
        if previous is not None and (completed <= previous['completed_us'] or sequence is not None and sequence <= previous['sequence']):
            return
        if payload['stage'] not in ('ASSET_VALID', 'DEVICE_BOOTED', 'GRAPH_STARTED', 'SESSION_CLOSED') and self.workers[role].bind_ack is None:
            raise RuntimeError('current data readiness has no accepted binding')
        self.progress[key] = dict(sequence=sequence, completed_us=completed, state=payload['state'])
        if role == 'M':
            host = self.workers['M']
            if payload['stage'] == 'PARSER_STARTED':
                if sequence is None or host.parse_started_us is not None:
                    raise RuntimeError('parser start has no new distinct active job')
                host.parse_started_us, host.parse_sequence = completed, sequence
            else:
                if (host.parse_started_us is None or host.parse_sequence != sequence
                        or completed < host.parse_started_us):
                    raise RuntimeError('parser completion has no original active start')
                host.parse_started_us = host.parse_sequence = None
                if payload['reason'] != 'STALE_INPUT':
                    self.last_fresh_parser_us = completed
        if key == ('A', 'CAPTURE_PAIR'):
            self.last_capture_us = completed
        elif key == ('A', 'CAPTURE_READY'):
            self.capture_ready_us = completed
            self.event('CAPTURE_READY', completed_j_us=str(completed))
        elif key == ('M', 'NN_READY'):
            if self.parser_deadline is None or now >= self.parser_deadline or completed >= self.parser_deadline:
                raise TimeoutError('actual parser readiness missed the original deadline')
            self.nn_ready_us = completed
            self.event('NN_READY', completed_j_us=str(completed))
        if role == 'A':
            self._a_stage(payload['state'], completed)

    def _a_stage(self, state, event_us):
        durations = {'ASSET_CHECK': 15_000_000, 'DEVICE_BOOT': 30_000_000,
                     'PIPELINE_START': 30_000_000, 'WARMUP': 15_000_000}
        if state in durations:
            deadline = min(self.workers['A'].startup_deadline_us, event_us + durations[state])
            self.a_phase_deadlines[state] = min(self.a_phase_deadlines.get(state, deadline), deadline)
            self.a_phase = state
        elif state == 'STREAMING':
            self.a_phase = state

    def _sent(self, host):
        for sent in host.link.take_sent():
            self.recovery.sent(host, sent)
            draft=self.db_selected.pop((host.peer.channel_epoch,sent.packet.fields['message_sequence']),None)
            if draft is not None:
                self.db_broker.note_sent(draft,sent.packet,sent.completed_us)
            if sent.packet.kind == 'BIND' and sent.packet.fields['binding_domain']!='PUBLICATION':
                host.bind_sent = sent
            elif sent.packet.kind == 'STOP':
                host.receiver.record_first_sent(sent.packet, sent.started_us, now_us=j_us())

    def _bind(self, role):
        host = self.workers[role]
        if not host.hello or host.bind is not None or not host.admitted or self.recovery.suppressed(role, host):
            return
        if role == 'M' and self.capture_ready_us is None or role == 'D' and host.bootstrap is None:
            return
        if host.bind_draft is None:
            now = j_us()
            payload = (binding_payload(storage_binding(host.peer, self.storage_profile, host.bootstrap)) if role == 'D'
                else dict(binding_revision='1', binding_sha256=self.binding.sha256, binding=self.binding.fields))
            host.bind_draft = dict(kind='BIND', payload=payload, domain='STORAGE' if role == 'D' else 'RAW',
                revision=1, request_id=_identifier(), issued_us=now, expires_us=min(now + 5_000_000, host.startup_deadline_us))
        packet = host.link.send(**host.bind_draft)
        if packet is None:
            return
        host.bind = (packet if role == 'D' else RawBindMessage(packet,
            RawBindContext(host.peer, self.scope.writer, self.raw_profile), cap_us=host.startup_deadline_us))
        if role == 'A':
            self.broker = ResidentRawBroker(host.bind, self.scope,
                Endpoint(self.identities['M'][1], self.identities['M'][0]), self.handles.raw_tokens)

    def _drive_workers(self):
        for role, host in tuple(self.workers.items()):
            try:
                self._sent(host)
                if host.admitted:
                    while host.held:
                        self._dispatch(role, host.held.popleft())
                if not host.eof:
                    for packet in host.link.poll():
                        self._dispatch(role, packet)
                host.link.flush()
                self._sent(host)
            except Exception as error:
                self._close_link(role, error)
                continue
            if self.stopping or self.recovery.suppressed(role, host):
                continue
            if role == 'D' and host.hello and host.discovery is None:
                now = j_us()
                packet = host.link.send('DB_JOB', discovery_payload(self.storage_profile, _identifier()), domain='STORAGE',
                    revision=0, issued_us=now, expires_us=min(now + 5_000_000, host.startup_deadline_us))
                if packet is not None:
                    host.discovery = packet
            self._bind(role)
        if (not self.stopping and 'A' in self.workers and self.workers['A'].bind is not None and 'M' not in self.workers
                and (self.recovery.active is None or self.recovery.active.kind == 'AM'
                     and self.recovery.active.phase in ('WAIT_READY', 'RESUME', 'WAIT_PARSER'))):
            self._spawn('M')
        if self.broker is not None and 'A' in self.workers and not self.stopping:
            self.broker.pump(self.workers['A'].link,
                self.workers['M'].link if 'M' in self.workers and self.workers['M'].bind_ack is not None else None,
                now_us=j_us())
        self._drive_db()
        self._drive_source()

    def _drive_source(self):
        host=self.workers.get('M')
        if (host is None or host.source is None or host.eof or self.stopping
                or self.recovery.suppressed('M',host)):
            return
        try:
            host.source.pump(self.storage_material(),bind_ready=host.bind_ack is not None,
                             on_publication=self.set_publication,on_bootstrap_load=self.request_storage_bootstrap_load,
                             bootstrap_completion=lambda:None if self.db_broker is None else self.db_broker.last_completion)
            self.source_revision=max(self.source_revision,host.source.owner.revision)
        except Exception as error:
            self._close_source(host)
            self.recovery.fail('M','M source channel: '+str(error))

    def _close_source(self,host):
        if host.source is None:return
        if self.workers.get('M') is host and self.jio is not None:
            self.jio.fence_source()
        self.source_revision=max(self.source_revision,host.source.owner.revision)
        try:self.selector.unregister(host.source.link.endpoint)
        except (KeyError,OSError,ValueError):pass
        host.source.close();host.source=None

    def _close_link(self, role, error):
        host = self.workers[role]
        if role=='M':self._close_source(host)
        expected = (self.stopping or self.recovery.suppressed(role, host)) and isinstance(error, EOFError)
        if not expected:
            if self.db_broker is not None:self.db_broker.fence('CONTROL_CLOSED_'+role)
            self.recovery.fail(role, str(error), retryable=host.hello)
        if not host.eof:
            try:
                self.selector.unregister(host.link.endpoint)
            except (KeyError, OSError, ValueError):
                pass
            host.eof = True
            host.failed_pending = host.link.abort()
            self.event('CONTROL_CLOSED', role=role, expected=bool(expected))

    def _advance(self):
        if self.log_pending is not None:
            return
        if self.phase == 'ALLOCATING' and self.allocating is None:
            if not self.allocations:
                self.phase = 'HANDLES'
                self._request('HANDLES', 'HANDLES', intent_ack=self.latest_ack)
                return
            self.allocating = self.allocations.popleft()
            now = j_us()
            self.allocation_deadline = min(now + 5_000_000, self.startup_deadline)
            fields = self.manifest.fields
            slot = self.allocating
            object_id = _identifier() if slot in ('RAW_CURRENT', 'DB_COPY_0') else None
            size = ARENA_BYTES if slot == 'RAW_CURRENT' else DB_COPY_BYTES if slot == 'DB_COPY_0' else None
            name = (('/xnv_raw_' if slot == 'RAW_CURRENT' else '/xnv_db_') + object_id) if object_id is not None else None
            fields['resources'][slot] = dict(state='INTENT', name=None, object_id=None, size_bytes=None)
            fields['intents'] = [dict(intent_id=_identifier(), slot=slot, operation='CREATE', planned_name=name,
                planned_object_id=object_id, planned_size_bytes=None if size is None else str(size))]
            self._write(fields, 'INTENT', cap_us=self.allocation_deadline)
        elif self.phase == 'WORKERS' and self.birth_pending:
            self.birth_admitting = set(self.birth_pending)
            self._request('BIRTHS', 'BIRTHS', birth_pids=tuple((role, self.workers[role].pid) for role in sorted(self.birth_admitting)))

    def stop(self, reason, error=None):
        if self.stopping:
            if error is not None and self.failure is None:
                self.failure = str(error)[:512]
            return
        self.stopping, self.stop_us, self.stop_reason = True, j_us(), reason
        if error is not None:
            self.failure = str(error)[:512]
            self.failure_trace = traceback.format_exc(limit=8)
        if self.broker is not None:
            self.broker.fence(self.stop_us)
        if self.db_broker is not None:self.db_broker.fence('STOP')
        self.event('QUIESCING', reason=reason)

    def _stop_one(self, host, began_us, reason):
        """동일 원 cleanup 안에서 STOP→TERM1초→KILL/reap1초를 진행한다."""
        if host.reaped:
            return
        role, now = host.peer.role, j_us()
        if not host.eof:
            try:
                if host.stop_draft is None:
                    host.stop_draft = dict(kind='STOP', payload=dict(stop_revision='1', reason=reason,
                        drain_deadline_j_us=str(began_us + 20_000_000)), request_id=_identifier(),
                        issued_us=now, expires_us=began_us + 20_000_000)
                if host.stop is None and now < host.stop_draft['expires_us']:
                    packet = host.link.send(**host.stop_draft)
                    if packet is not None:
                        host.stop = packet
                        host.receiver.register_stop_intent(packet, now_us=j_us())
                host.link.flush()
                self._sent(host)
            except Exception as error:
                self._close_link(role, error)
        grace = {'A': 15_000_000, 'M': 2_000_000, 'D': 5_000_000}[role]
        if host.term_sent_us is None and now >= began_us + grace:
            try:
                host.process.terminate()
            except ProcessLookupError:
                pass
            host.term_sent_us = now
            self.event('TERM_SENT', role=role, pid=host.pid)
        if host.term_sent_us is not None and host.kill_sent_us is None and now >= host.term_sent_us + 1_000_000:
            try:
                host.process.kill()
            except ProcessLookupError:
                pass
            host.kill_sent_us = now
            self.event('KILL_SENT', role=role, pid=host.pid)

    def _stop_workers(self):
        for host in tuple(self.workers.values()):
            self._stop_one(host, self.stop_us, self.stop_reason)

    def _observe_exit(self, role):
        host = self.workers[role]
        if host.exited_us is None:
            host.exited_us = j_us()
            if not self.stopping and not self.recovery.suppressed(role, host):
                self.recovery.fail(role, 'worker ' + role + ' exited before requested shutdown', retryable=host.hello)
        # owner 등록 전 reap으로 PID가 재사용되지 않게 birth 조회 동안 sentinel만 기록한다.
        if self.log_pending is not None and self.log_pending[2] == 'BIRTHS':
            return
        if not host.reaped:
            if not host.eof:
                try:
                    self._sent(host)
                    for packet in host.link.poll():
                        self._dispatch(role, packet)
                except Exception as error:
                    self._close_link(role, error)
            host.process.join(timeout=0)
            if host.process.exitcode is not None:
                self._close_source(host)
                host.exitcode = host.process.exitcode
                host.reaped = True
                if self.stopping and host.exitcode != 0 and self.failure is None:
                    self.failure = role + ' exited with code ' + str(host.exitcode) + ' during shutdown'
                self.selector.unregister(host.process.sentinel)
                self.event('PROCESS_EXIT', role=role, exitcode=host.exitcode)
                host.process.close()

    def _shutdown(self):
        self._stop_workers()
        if any(not host.reaped or not host.eof for host in self.workers.values()) or self.log_pending is not None:
            return False
        if not self.native_closed:
            if self.db_copy_cleanup is not None:
                self.db_copy_cleanup.close();self.db_copy_cleanup=None
            if self.cleanup_failed:
                if not self.log_stop_requested:
                    self.log.request_stop()
                    self.log_stop_requested = True
                return not self.log.is_alive
            if self.native_started and not self.cleanup_attempted:
                self._request('CLEANUP', 'NATIVE_CLOSE')
            else:
                self.native_closed = True
            return False
        if not self.candidate_written and self.manifest is not None and self.failure is None:
            fields = self.manifest.fields
            fields['state'] = 'CLEANUP_CANDIDATE'
            self._write(fields, 'CANDIDATE')
            return False
        if not self.log_stop_requested:
            self.log.request_stop()
            self.log_stop_requested = True
        return not self.log.is_alive

    def _timers(self):
        now = j_us()
        if self.log_pending is not None and now >= self.log_pending[3]:
            self.stop('resource_timeout', TimeoutError('original S-log operation expired'))
        if self.stopping:
            if now - self.stop_us >= 25_000_000:
                raise TimeoutError('normal shutdown budget elapsed; owners are not confirmed reclaimed')
            return
        if self.resident_started_us is None and self.recovery.active is None and now >= self.startup_deadline:
            raise TimeoutError('original total startup budget elapsed')
        db_deadline=None if self.db_broker is None else self.db_broker.pending_deadline_us
        if db_deadline is not None and now>=db_deadline and self.recovery.active is None:
            self.db_broker.fence('DB_JOB_TIMEOUT')
            self.recovery.fail('D','original five-second D job expired; historical outcome requires lookup')
        if (self.db_broker is not None and self.db_broker.bind_draft is not None
                and now>=self.db_broker.bind_draft.expires_us and self.recovery.active is None):
            self.recovery.fail('D','original D publication binding budget elapsed')
        for role, host in tuple(self.workers.items()):
            if self.recovery.suppressed(role, host):
                continue
            if host.local_watch is not None and host.local_watch.poll(now):
                self.recovery.fail(role, role + ' original local setup budget elapsed', retryable=False)
                continue
            if host.bind_draft is not None and host.bind_ack is None and now >= host.bind_draft['expires_us']:
                self.recovery.fail(role, role + ' original binding budget elapsed', retryable=False)
                continue
            if role == 'D' and host.discovery is not None and host.bootstrap is None and now >= _decimal(host.discovery.fields['expires_j_us']):
                self.recovery.fail('D', 'original D discovery budget elapsed')
                continue
            if host.parse_started_us is not None and now - host.parse_started_us >= 500_000:
                self.recovery.fail('M', 'actual active parser job stalled')
                continue
            if role=='M' and host.compute_watch is not None and host.compute_watch.stalled(now):
                self.recovery.fail('M', 'original mapper job expired without actual stage progress')
                continue
            idle_watch = (role == 'A' and self.capture_ready_us is not None
                          or role == 'M' and host.bind_ack is not None
                          or role == 'D' and self.storage_ready_us is not None)
            heartbeat_lower=host.last_heartbeat_us
            if role=='D' and self.db_last_progress_us is not None:
                heartbeat_lower=max(heartbeat_lower or 0,self.db_last_progress_us)
            active_d=role=='D' and db_deadline is not None and now<db_deadline
            if idle_watch and not active_d and heartbeat_lower is not None and now-heartbeat_lower>=1_000_000:
                self.recovery.fail(role, role + ' current control progress stalled')
        if self.a_phase in self.a_phase_deadlines and now >= self.a_phase_deadlines[self.a_phase]:
            self.recovery.fail('A', 'original A ' + self.a_phase + ' budget elapsed')
        if self.capture_ready_us is not None and self.last_capture_us is not None and now - self.last_capture_us >= 500_000:
            self.recovery.fail('A', 'actual capture pair progress stalled')
        if self.parser_deadline is not None and self.nn_ready_us is None and now >= self.parser_deadline:
            self.recovery.fail('M', 'original M parser readiness budget elapsed')
        if (self.nn_ready_us is not None and self.last_fresh_parser_us is not None
                and now - self.last_fresh_parser_us >= 5_000_000):
            # A는 자신의 실제 NN output 정체를 별도로 감시한다. M 계산/해석 정체만으로
            # 정상인 OAK 세션과 적재 모델을 다시 열지 않는다.
            self.recovery.fail('M', 'required fresh NN parser completion stalled')
        if self.capture_ready_us is not None and self.nn_ready_us is not None and self.storage_ready_us is not None:
            if self.resident_started_us is None:
                self.resident_started_us = now
                self.event('RESIDENT_LOCAL_READY', metric_ready=False, world_valid=False)
            if (self.recovery.active is None and self.duration_s is not None
                    and now - self.resident_started_us >= int(self.duration_s * 1_000_000)):
                self.stop('duration_elapsed')

    def run(self, signal_value):
        try:
            if self.jio_profile is not None:
                from ..transport.jetson_jio import JetsonJio
                self.jio=JetsonJio(self.jio_profile)
                self.jio.start()
            self.log.start()
            self._request('PREPARE', 'PREPARE')
            while True:
                try:
                    self._complete_log()
                    if signal_value():
                        self.stop('operator_stop')
                    self._drive_workers()
                    self._timers()
                    if self.stopping:
                        if self._shutdown():
                            break
                    else:
                        self._advance()
                        self.recovery.step()
                        self.recovery.healthy()
                    for key, _ in self.selector.select(0.01):
                        kind, role = key.data
                        if kind == 'exit':
                            self._observe_exit(role)
                except EOFError:
                    # socket EOF를 실제 process exit로 승격하지 않는다.
                    for host in self.workers.values():
                        if not host.reaped:
                            try:
                                for packet in host.link.poll():
                                    self._dispatch(host.peer.role, packet)
                            except EOFError:
                                host.eof = True
                    if not self.stopping:
                        self.stop('control_eof', RuntimeError('worker control closed before requested shutdown'))
                except Exception as error:
                    if self.stopping and j_us() - self.stop_us >= 25_000_000:
                        self.failure = self.failure or str(error)[:512]
                        break
                    self.stop('runtime_failed', error)
        finally:
            for host in self.workers.values():
                try:self._close_source(host)
                except Exception as error:self.failure=self.failure or 'source close: '+type(error).__name__
                try:host.link.close()
                except Exception as error:self.failure=self.failure or 'control close: '+type(error).__name__
            if self.jio is not None:
                try:
                    self.jio.stop()
                    if not self.jio.join(1.):
                        self.failure=self.failure or 'S-jio owner is still alive after bounded join'
                except Exception as error:self.failure=self.failure or 'S-jio close: '+type(error).__name__
            try:self.selector.close()
            finally:
                self.log.request_stop()
                if self.log.is_alive:
                    try:self.log.close(timeout=1.)
                    except Exception as error:self.failure=self.failure or 'S-log close: '+type(error).__name__
        stopped = all(host.reaped and host.exitcode == 0 and host.stopped is not None
                      and host.stopped.payload['status'] == 'STOPPED' for host in self.workers.values())
        complete = (self.resident_started_us is not None and self.recovery.active is None and stopped and self.native_closed
                    and self.candidate_written and not self.log.is_alive and self.log.terminal_error is None and self.failure is None)
        result = dict(schema_version=1, scope='NORMAL_RESIDENT_CAPTURE_PARSER_STORAGE_NOT_DRIVING', complete=complete,
            exit_code=0 if complete else 1, service_instance=self.service, resource_epoch=self.epoch,
            release_sha256=None if self.artifacts is None else self.artifacts['release_sha256'],
            admission_kind=None if self.admission is None else self.admission.decision.kind,
            raw_binding=None if self.binding is None else dict(sha256=self.binding.sha256, fields=self.binding.fields),
            parent_native_modules={name: name in sys.modules for name in ('depthai', 'numpy', 'cv2', 'sqlite3')},
            reason=self.stop_reason, error=self.failure, error_trace=self.failure_trace, metric_ready=False, world_valid=False,
            external_waits=['PI_J_CLOCK', 'ODOM', 'METRIC_CALIBRATION', 'MAP_AND_PROVENANCE'],
            capture_ready=self.capture_ready_us is not None, nn_ready=self.nn_ready_us is not None,
            storage_ready=self.storage_ready_us is not None, fixed_token_count=6 if self.handles is not None else 0,
            resource_state=None if self.manifest is None else self.manifest.state,
            log_exited=not self.log.is_alive, log_error=self.log.terminal_error,
            resource_cleanup_confirmed=self.native_closed, all_worker_exits_confirmed=stopped,
            requires_service_reap=(self.log.is_alive or any(not host.reaped for host in self.workers.values())
                or self.jio is not None and self.jio.snapshot()['alive']),
            recoveries=list(self.recovery.receipts),
            supervisor_exit_confirmed=False, resource_tracker_exit_confirmed=False,
            resident_started_j_us=None if self.resident_started_us is None else str(self.resident_started_us),
            ended_j_us=str(j_us()), events=list(self.events),
            raw_broker=None if self.broker is None else self.broker.summary(),
            db_broker=None if self.db_broker is None else self.db_broker.summary(),
            jio=None if self.jio is None else self.jio.snapshot(),
            workers={role: dict(pid=host.pid, worker_instance=host.peer.worker_instance,
                channel_epoch=host.peer.channel_epoch, reaped=host.reaped, exitcode=host.exitcode,
                stopped=None if host.stopped is None else host.stopped.payload,
                unsent_on_link_failure=None if host.failed_pending is None else host.failed_pending.raw_bytes.decode('ascii'),
                transport=dict(host.link.counts)) for role, host in self.workers.items()})
        return result


def run_runtime(profile, state_directory, storage_profile, output, *, duration_s=None, service_scope=None, output_root=False,
                perception_profile_bytes=None,jio_profile=None,mapper_profile_bytes=None):
    """명시 profile/state/DB의 상주 실행. duration=None은 operator stop까지 유지한다."""
    if type(storage_profile) is not StorageProfile:
        raise ValueError('runtime requires an explicit storage profile')
    if duration_s is not None and (type(duration_s) not in (int, float) or not 0.1 <= duration_s <= 300):
        raise ValueError('optional runtime duration must be in 0.1..300 seconds')
    runtime = _Runtime(profile, state_directory, storage_profile, output, duration_s, service_scope, output_root,
                       perception_profile_bytes,jio_profile,mapper_profile_bytes)
    thread_environment = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
    old_environment = {name: os.environ.get(name) for name in thread_environment}
    for name in thread_environment:
        os.environ[name] = '1'
    observed_signal = []
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda number, _frame: observed_signal.append(number) if not observed_signal else None)
    try:
        result = runtime.run(lambda: bool(observed_signal))
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        for name, value in old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if runtime.output_owned:
        with (runtime.output / 'runtime-result.json').open('xb') as destination:
            destination.write(canonical(result))
    return result
