"""원 센서 바이트부터 Processor 채택까지 잇는 명시 software 시험 장치다.

설치 보정·Pi 시계·Vehicle의 수락은 외부 software fixture다. 영상 렌더러의
좌표를 인지 결과에 넘기지 않으며, 실제 core/등록/catalog/Scene/채택을 호출한다.
D는 임시 SQLite와 실제 copy codec을 쓰지만 SHM/token/native 완료 시험은 아니다.
제어한 J 시각은 기능 계약용이며 계산 성능이나 물리 시계 검증을 뜻하지 않는다.
"""

from base64 import b64encode
from collections import deque
from copy import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
from uuid import UUID, uuid4

from processor_observation_fixture import software_live_calibration, observation_payloads
from test_mapper_profile import profile_fields
from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import ClockPolicy
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.runtime.data_channel import DataLink, DataPeer, data_pair
from xenova_vehicle.runtime.mapper_bootstrap import MapperBootstrap
from xenova_vehicle.runtime.mapper_compute import MapperCompute, MapperInput
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.mapper_mapping import MappingContextPort
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_profile import MapperProfile
from xenova_vehicle.runtime.mapper_publication import MapperPublicationPort
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.resident_db import DbCompletion
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.resident_storage import _Database, StorageProfile
from xenova_vehicle.runtime.resident_workers import relay_publications, mapper_sensor_owner, attach_sensor_snapshot
from xenova_vehicle.runtime.source_identity import SourcePolicy
from xenova_vehicle.runtime.source_runtime import MapperSource, SupervisorSource
from xenova_vehicle.runtime.storage_copy import CopyLease, OwnedCopy, encode_load
from xenova_vehicle.runtime.storage_messages import DbMessage, db_payload, prepare_payload
from xenova_vehicle.storage.contracts import LoadRequest, ProvenanceRequest, canonical, json_bytes
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.repository import PersistenceStore
from xenova_vehicle.transport.jio_profile import JetsonJioProfile
from xenova_vehicle.transport.jetson_jio import JetsonJio,PREFIX,VEHICLE_TOPIC,WORLD_TOPIC


def uid():
    return str(uuid4())


# 명시 software bench 간격이다. 실물 회복 시간이나 카메라 FPS 수락값이 아니다.
_SAMPLE_CADENCE_US = 20_000
_M_RESTART_GAP_US = 200_000


class _SoftwarePublishInfo:
    """네트워크 성공만 명시한 leaf다. 실제 broker/Pi 수락을 뜻하지 않는다."""
    rc = 0
    def is_published(self): return True


class _SoftwareMqttClient:
    """IO만 대체한다. callback/최종 outbox/clock/Scene 검산은 실제 JIO가 수행한다."""
    def __init__(self, ingress): self.ingress = ingress
    def _io(self):
        with self.ingress._lock: self.ingress._io_threads.add(threading.get_ident())
    def connect(self, *args, **kwargs):
        self._io(); self.on_connect(self, None, None, 0, None)
    def socket(self): self._io(); return None
    def subscribe(self, *args, **kwargs): self._io()
    def loop(self, timeout):
        self._io()
        with self.ingress._lock:
            incoming = self.ingress._incoming.popleft() if self.ingress._incoming else None
        if incoming is not None:
            message, completed = incoming
            try: self.on_message(self, None, message)
            finally: completed.set()
        return 0
    def publish(self, topic, raw, *, qos, retain):
        self._io()
        if qos != 0 or retain: raise AssertionError('software JIO emitted unsupported publish flags')
        if topic == WORLD_TOPIC: self.ingress._published_world(raw)
        return _SoftwarePublishInfo()
    def disconnect(self): self._io()


class SoftwareJioIngress:
    """외부 software 입력을 실제 JIO owner에 전달하고 실제 publish에서만 World를 기록한다.

    입력 queue8/원 offer metadata128/실제 발행 기록128의 유한 시험 장치다. metadata는
    원 body SHA와 E/publication SHA만 저장하며 body 사본을 누적하지 않는다.
    """
    def __init__(self, policy, device, clock_policy, clock):
        self._lock = threading.Lock(); self._incoming = deque(); self._offers = {}
        self._io_threads = set(); self.worlds = []; self.summaries = []
        self.source = self.origin = self.publication = None
        self.client = _SoftwareMqttClient(self)
        self.profile = JetsonJioProfile(policy, clock_policy, device, '127.0.0.1', 1883)
        self.transport = JetsonJio(self.profile, clock=clock, client_factory=lambda *_: self.client)
        self.transport.start()

    @property
    def current_scene(self): return self.transport.current_scene
    @property
    def current_lease(self): return self.transport.current_lease
    @property
    def io_thread_ids(self):
        with self._lock: return tuple(self._io_threads)
    def take_inputs(self): return self.transport.take_inputs()
    def snapshot(self): return self.transport.snapshot()

    def _deliver(self, topic, raw, *, qos=0):
        if type(raw) is not bytes: raise AssertionError('original immutable software ingress bytes required')
        before = self.transport.snapshot()['counts']
        completed = threading.Event()
        message = SimpleNamespace(topic=topic, payload=raw, qos=qos, retain=False)
        with self._lock:
            if len(self._incoming) >= 8: raise AssertionError('bounded software ingress is full')
            self._incoming.append((message, completed))
        if not completed.wait(2.): raise AssertionError('actual JIO callback did not finish: '+repr(self.snapshot()))
        after = self.transport.snapshot()
        counts = after['counts']
        if counts['received'] == before['received'] and counts['duplicate'] == before['duplicate']:
            raise AssertionError('actual JIO rejected original software input: '+repr(after))
    def deliver_vehicle(self, raw): self._deliver(VEHICLE_TOPIC, raw)
    def deliver_probe(self, raw): self._deliver(f'{PREFIX}/mapper/{self.profile.device}/clock/request.pb', raw)
    def deliver_scene(self, raw): self._deliver(f'{PREFIX}/mapper/{self.profile.device}/scene.pb', raw, qos=1)

    def replace_source(self, source, **origin):
        changed = self.transport.replace_source(source, **origin)
        self.source, self.origin = dict(source), dict(origin)
        if changed: self.publication = None
        return changed
    def replace_publication(self, binding):
        changed = self.transport.replace_publication(binding)
        self.publication = json.loads(canonical(binding))
        return changed
    def fence_source(self):
        self.transport.fence_source()
        self.source = self.origin = self.publication = None

    def offer_world(self, raw, *, expires_j_us, publication_sha256):
        key = sha256(raw).digest(); record = (len(raw), expires_j_us, publication_sha256)
        # actual offer가 반환하기 전에 IO thread가 발행할 수도 있으므로 원 metadata가 먼저다.
        with self._lock:
            previous = self._offers.get(key); inserted = previous is None
            if inserted:
                if len(self._offers) >= 128: raise AssertionError('bounded original offer metadata is full')
                self._offers[key] = record
        try:
            accepted = self.transport.offer_world(raw, expires_j_us=expires_j_us, publication_sha256=publication_sha256)
        except BaseException:
            with self._lock:
                if inserted and self._offers.get(key) is record: self._offers.pop(key)
            raise
        if not accepted:
            with self._lock:
                if inserted and self._offers.get(key) is record: self._offers.pop(key)
        elif not inserted and previous != record:
            raise AssertionError('accepted World changed the original offer metadata')
        return accepted

    def _published_world(self, raw):
        key = sha256(raw).digest()
        with self._lock:
            original = self._offers.pop(key, None)
            if original is None or original[0] != len(raw):
                raise AssertionError('actual publish has no original pre-offer metadata')
            if len(self.worlds) >= 128: raise AssertionError('bounded actual publish receipts are full')
            self.worlds.append((raw, original[1], original[2]))
    def offer_survey_summary(self, raw, *, grant_sha256, expires_j_us):
        accepted = self.transport.offer_survey_summary(raw, grant_sha256=grant_sha256, expires_j_us=expires_j_us)
        if accepted:
            if len(self.summaries) >= 128: raise AssertionError('bounded survey receipts are full')
            self.summaries.append((raw, grant_sha256, expires_j_us))
        return accepted
    def close(self):
        self.transport.stop()
        if not self.transport.join(1.): raise AssertionError('actual non-daemon JIO owner is still alive')
        if self.transport.snapshot()['failure'] is not None:
            raise AssertionError('actual JIO owner failed: '+repr(self.snapshot()))


class ProcessorFlowRig:
    """실제 M-compute 한 개와 public 원 D mailbox를 순차 구동한다.

    main의 SQL/copy dispatcher는 software D다. COMPLETE의 원 LOAD request/ready를
    S bootstrap에도 전달하지만 실제 semaphore 또는 프로세스 종료 증명은 만들지 않는다.
    """

    def __init__(self, *, database=None, predecessor=None):
        self._bench_started_wall = time.monotonic() if predecessor is None else predecessor._bench_started_wall
        self._restore_deadline_wall = self._restore_validation_progress = None
        self.now = 2_000_000 if predecessor is None else predecessor.now + _M_RESTART_GAP_US
        self.sequence = (0 if predecessor is None else
            predecessor.sequence + _M_RESTART_GAP_US // _SAMPLE_CADENCE_US)
        self.vehicle_sequence = 0 if predecessor is None else predecessor.vehicle_sequence
        self.temporary = tempfile.TemporaryDirectory() if database is None else None
        self.db = database or _Database(StorageProfile(str(Path(self.temporary.name) / 'processor.sqlite3'),
            'CREATE_IF_ABSENT'), time.monotonic_ns() // 1000 + 5_000_000)
        if predecessor is not None and self.db is not predecessor.db:
            raise ValueError('M-only restart fixture must keep the actual original D database owner')
        self.store = PersistenceStore(self.db.connection, self.db.metadata['database_uuid'],
            expires_us=self.now + 60_000_000, clock_us=lambda: self.now)
        self.calibration = (software_live_calibration('a' * 64) if predecessor is None else predecessor.calibration)
        self.raw = (self._raw_binding() if predecessor is None else predecessor.raw)
        self.mapper_worker, self.mapper_instance, self.mapper_boot = uid(), uid(), uid()
        self.peer = DataPeer(self.raw.fields['service_instance'], self.mapper_worker, uid())
        self.origin = (dict(pi_host_boot=uid(), gateway_instance=uid(), device_session=uid(), pi_session=uid())
            if predecessor is None else dict(predecessor.origin))
        self.odom_epoch = uid() if predecessor is None else predecessor.odom_epoch
        self.policy = SourcePolicy('software-car', 'software-installation', 'a' * 64, 'b' * 64,
            self.calibration.sha256)
        self.clock_policy = ClockPolicy(self.policy.vehicle, 'oak-bench', self.policy.configuration,
            self.policy.calibration, self.policy.primitive, 0, 0, 100, 1_000_000, 500_000, 500_000)
        self.config = self._profile()
        _,self.sensor_owner=mapper_sensor_owner(SimpleNamespace(
            mapper_profile_bytes=self.config.canonical_bytes,perception_profile_bytes=canonical(self.calibration.to_dict()),
            source_policy=self.policy,clock_policy=self.clock_policy,
            peer=SimpleNamespace(worker_instance=self.mapper_worker),mapper_instance=self.mapper_instance),self.raw)
        self._sensor_fence=0;self._station_snapshot=None;self._mapping_scene=None
        self.binding = (dict(service_instance=self.peer.service_instance, storage_worker=uid(),
            storage_profile_sha256=self.db.profile.sha256, database_uuid=self.store.database_uuid, db_schema_version=1)
            if predecessor is None else dict(predecessor.binding))
        selected = None if predecessor is None else self.store.latest_complete(expires_us=self.now + 5_000_000)
        # D는 계속 살아 있다. 생성 시 metadata 사본 대신 실제 SQL 예약 이후의 HWM을 읽는다.
        highwater = self.db.connection.execute(
            'SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone()[0]
        self.bootstrap_fields = dict(status='EMPTY' if selected is None else 'COMPLETE', allocator_ready=True,
            schema_version=1, database_uuid=self.store.database_uuid,
            allocator_highwater=highwater, selected_checkpoint=selected)
        self.material = self.binding, self.bootstrap_fields
        self.port = MappingContextPort()
        self.mailbox = MapperStorageMailbox(self.raw.sha256)
        self.publications = MapperPublicationPort(self.port)
        self.jio = SoftwareJioIngress(self.policy, 'oak-bench', self.clock_policy, lambda: self.now)
        left, right = data_pair()
        self.mapper = MapperSource(DataLink(left, self.peer, 'M', clock=lambda: self.now), self.raw,
            self.policy, mapper_instance=self.mapper_instance, device='oak-bench', clock=lambda: self.now)
        self.supervisor = SupervisorSource(DataLink(right, self.peer, 'S', clock=lambda: self.now), self.raw,
            self.jio, mapper_instance=self.mapper_instance, mapper_boot=self.mapper_boot,
            clock_epoch=self.raw.fields['clock_epoch'], clock=lambda: self.now)
        self.bootstrap = MapperBootstrap(self.mailbox, mapper_worker=self.mapper_worker,
            mapper_instance=self.mapper_instance, cap_us=self.now + 15_000_000, clock_us=lambda: self.now)
        self.processor = None
        self.compute = MapperCompute(self._processor_factory, source_generation=self.raw.sha256,
            clock=lambda: self.now, maintenance=self._maintenance, adoption_lock=self.port.adoption_lock)
        self.sql_requests = []
        self.commits = []
        self.loads = []
        self.releases = []
        self.events = []
        self.last_result = self.last_summary = self.last_item = None
        self.last_probe_raw = self.lease = None
        self._dispatch = self._writer = self._encode_request = None
        self._bootstrap_allowed = {}
        self._bootstrap_request = None
        self._physical_completion = None
        self._copy_generation = 0
        self._db_sequence = 0
        self._station_diagnostics = []
        self.compute.start()
        # M만 바꾸는 경우 첫 source_us는 old.now+10_000이다. 원 last는 old.now-1000 이하다.
        # 같은 Pi 세션의 번호를 잇고 새 표본만 만든다. 옛 wire/시각을 새 번호로 재발급하지 않는다.
        # 신원 bootstrap은 시계 수락 전 실제 Vehicle wire 하나로 시작한다.
        self._offer_vehicle(self.now - 190_000, lease=None)
        self.wait(lambda: self.mapper.publication is not None, 'actual source identity/bootstrap ACK')
        self.renew_clock()
        for at in range(self.now - 180_000, self.now, 10_000):
            self._offer_vehicle(at)
            self.tick()
        self.tick()

    def _raw_binding(self):
        return RawBinding(dict(service_instance=uid(), acquisition_worker=uid(), pipeline_instance=uid(),
            capture_epoch=uid(), stream_instances=dict(rgb_video=uid(), depth_right=uid()), sdk_bridge_epoch=uid(),
            device_id='1844301051456A1200', model_id=None, parser_id=None, graph_profile_sha256='c' * 64,
            calibration_bundle=self.calibration.sha256, format_table_sha256='d' * 64, clock_epoch=uid()))

    def _profile(self):
        value = profile_fields(self.calibration)
        value.update(vehicle_configuration=self.policy.configuration, motion_qualified=True, parking_aisles=[],
            localization=dict(registration_valid_for_us=500_000, position_drift_mps=.001, yaw_drift_rps=.001),
            catalog_policy=dict(crossing_allowed=False, aisle_lane_indices=[]),
            rolling_roi=dict(margin_cells=1, lookahead_cells=2))
        value['sensors'].update(vehicle_max_age_us=500_000, tof_max_age_us=500_000,
            max_speed_mps=.05, max_yaw_rate_rps=.05, max_gap_us=20_000, capacity=128, tof_sources=[])
        value['stopping'].update(max_speed_mps=.05)
        pipeline = value['pipeline']
        # 기본 body의 all-yaw 반경만 약.273m다. 원 오차/정지거리까지 뒤쪽으로 담는다.
        pipeline['grid'].update(width=20, height=20, resolution_m=.1, origin_xy=(-.4, -1.))
        pipeline['tape'].update(x_min_m=.2, x_max_m=1.4, y_min_m=-.7, y_max_m=.7, cell_m=.005)
        pipeline.update(permission_polygon=self.calibration.support_polygon,
            permission_source='EXPLICIT_SOFTWARE_BENCH_POLICY')
        for target in value['targets']:
            target['qualified'] = True
        value['survey'] = dict(coverage_profile_sha256='e' * 64, required_sensor_mask=3,
            atlas_cell_capacity=128 * 4096, station_window_us=100_000, station_distance_m=.1,
            station_motion_m=.01, station_yaw_rad=.01, max_pose_gap_us=20_000,
            max_anchor_position_m=.03, max_anchor_heading_rad=.03,
            observation_max_age_us=500_000, input_kind='LIVE')
        return MapperProfile(value)

    def _processor_factory(self, generation):
        """운영 진입점처럼 원 M-compute에서 CV Processor만 생성한다. 센서는 main 소유다."""
        if generation != self.raw.sha256 or self.processor is not None:
            raise AssertionError('one original processor factory generation required')
        processor = ResidentMapperProcessor(canonical(self.calibration.to_dict()), self.raw,
            clock=lambda: self.now, mapper_profile_bytes=self.config.canonical_bytes, source_policy=self.policy,
            clock_policy=self.clock_policy, mapper_worker=self.mapper_worker, mapper_instance=self.mapper_instance,
            mapping_context_port=self.port, mapping_mailbox=self.mailbox, publication_port=self.publications,
            checkpoint_interval_us=1)
        self.bootstrap.attach_processor(processor)
        # main은 이 참조를 조회만 한다. 소유 thread를 바꾸거나 검사를 덮어쓰지 않는다.
        self.processor = processor
        return processor

    def _maintenance(self):
        self.bootstrap.compute_step()
        if self.bootstrap.failure is not None:
            raise RuntimeError(self.bootstrap.failure)
        if self.processor is not None:
            self.processor.maintenance()
            self._diagnose_station()

    def _diagnose_station(self):
        """거절된 원 관측만 재검산한다. 실제 Scene/입력/채택 결과는 바꾸지 않는다."""
        processor = self.processor
        if processor._scene is None:
            return
        scene = processor._scene.scene
        observation = scene._observation
        if observation is None or any(row['key'] == observation.stamp.key for row in self._station_diagnostics):
            return
        if len(self._station_diagnostics) >= 16:
            return
        from xenova_vehicle.runtime.mapper_scene import SceneStationWitness
        row = dict(key=observation.stamp.key, now=self.now, scene_status=processor._scene_status,
            capture=observation.raw_pose.captured_us, bracket=observation.raw_pose.bracket_us,
            validation_start=scene._validation_start_j, stage='FROM_FRAME')
        try:
            sensors=self._station_snapshot
            if sensors is None or sensors.capture_stamp.key!=observation.stamp.key:return
            witness = SceneStationWitness.from_frame(sensors, observation,
                scene.context, scene.profile, now_j_us=self.now)
            row.update(window=witness.window_fields, stage='STATION_ON_COPY')
            # station은 tuple/summary 참조만 교체한다. 사본에서 거절 사유를 읽고 버린다.
            row['accepted_on_copy'] = copy(scene).station(witness, now_j_us=self.now)
        except ValueError as exc:
            row['error'] = str(exc)
        self._station_diagnostics.append(row)

    def _published(self, binding, context):
        self.port.publish(context, material=canonical(dict(binding=self.binding, bootstrap=self.bootstrap_fields)))

    def _allow_bootstrap(self, request):
        self._bootstrap_allowed[request.job.job_id] = request
        return True

    def tick(self):
        # 기능 시험의 원 J clock도 진행한다. 관측/job/lease의 원 기한은 그대로라 더 오래될 뿐이다.
        self.now += 1
        self.supervisor.pump(self.material, bind_ready=True, on_publication=self._published,
            on_bootstrap_load=self._allow_bootstrap, bootstrap_completion=lambda: self._physical_completion)
        self.mapper.pump(before_outputs=self._ingest)
        if self.mapper.material is not None:
            self.bootstrap.offer_material(self.mapper.material)
        if self._bootstrap_request is None:
            self._bootstrap_request = self.bootstrap.take_request()
        if self._bootstrap_request is not None:
            self.mapper.offer_bootstrap_load(self._bootstrap_request)
            self._bootstrap_request = None
        complete = self.bootstrap.take_complete()
        if complete is not None:
            self.mapper.accept_bootstrap(complete)
        if self.mapper.storage_context is not None:
            self.port.publish(self.mapper.storage_context)
        self._drive_sql()
        relay_publications(self.publications, self.mapper, clock=lambda: self.now)
        self.events.extend(self.compute.poll())
        if self.compute.terminal_error is not None:
            raise AssertionError('actual compute failed: ' + str(self.compute.terminal_error))

    def _ingest(self,context=None):
        """실제 main 회차의 수락이다. frame 취소·계산 완료와 독립적으로 원 HWM을 유지한다."""
        if self.mapper.scene is not self._mapping_scene:
            self.port.publish_scene(None if self.mapper.scene is None else canonical(self.mapper.scene))
            self.publications.discard_pending();self.mapper.discard_outputs()
            self._mapping_scene=self.mapper.scene
        self.sensor_owner.ingest(self.mapper.context_bytes() if context is None else context,now_us=self.now)
        revision=self.sensor_owner.fence_revision
        if revision!=self._sensor_fence:
            self.compute.fence_admission(revision)
            self.publications.fence_admission(revision)
            self.mapper.discard_outputs()
            self._sensor_fence=revision

    def wait(self, predicate, label, *, timeout=45.):
        deadline = time.monotonic() + timeout
        if self._restore_deadline_wall is not None:
            deadline = min(deadline, self._restore_deadline_wall)
        while not predicate():
            self.tick()
            if time.monotonic() >= deadline:
                # 정리 STOP 전에 실제 대기 위치를 읽는다. 원 요청/시계/반환을 바꾸지 않는다.
                frames = sys._current_frames()
                stacks = {str(ident): ''.join(traceback.format_stack(frame, limit=32))
                    for ident, frame in sorted(frames.items())[:8]}
                raise AssertionError(label + ': ' + self.diagnostics() + '; wait_thread_stacks=' + repr(stacks))
            time.sleep(.005)
        self.tick()

    def diagnostics(self):
        """현재 World gate를 먼저 보존하고 큰 이력은 제한된 진단 꼬리로 둔다."""
        processor = self.processor
        mapping = None if processor is None else processor._mapping
        with self.mailbox._lock:
            held, completion = self.mailbox._dispatch, self.mailbox._completion
            result = None if completion is None else completion.result
            encode_request, encoded = self.mailbox._encode_request, self.mailbox._encode_result
            mailbox = dict(request=None if held is None else type(held.request).__name__,
                job=None if held is None else held.request.job.job_id,
                issued=None if held is None else held.request.job.issued_us,
                expires=None if held is None else held.request.job.expires_us,
                taken=self.mailbox._taken, seen=self.mailbox._seen, released=self.mailbox._released,
                result=None if result is None else type(result).__name__, status=getattr(result, 'status', None),
                encode_request=None if encode_request is None else encode_request.dispatch.request.job.job_id,
                encoding=self.mailbox._encoding, encode_result=encoded is not None,
                encode_error=None if encoded is None else encoded.error,
                encode_completed=None if encoded is None else encoded.completed_us,
                encode_delivered=self.mailbox._encode_delivered)
        catalog = None if mapping is None else mapping.catalogs
        catalog_view = None if catalog is None else catalog.view
        basis = None if catalog_view is None else catalog_view.road_basis
        scene = None if processor is None or processor._scene is None else processor._scene.scene
        catalog_pending = None if catalog is None else catalog.pending
        mapping_pending = None if mapping is None else mapping.pending
        association = None if catalog is None else catalog._accepted.lane.current_association
        lane_observation = None if association is None else dict(status=association.status,
            reasons=association.reasons, matches=association.matches, checked_lanes=association.checked_lanes)
        mailbox['catalog_request_is_held'] = bool(held is not None and catalog_pending is not None
            and catalog_pending.request is held.request)
        pending = {name: None if entry is None else dict(role=entry.role, job=entry.request.job.job_id,
            issued=entry.request.job.issued_us, expires=entry.request.job.expires_us)
            for name, entry in (('mapping', mapping_pending), ('catalog', catalog_pending))}
        def select(value, names):
            return None if type(value) is not dict else {name: value[name] for name in names if name in value}
        last = self.last_summary
        current = dict(summary_available=last is not None,
            world=select(last, ('world_candidate_ready', 'world_candidate_reason')),
            world_attempts=getattr(self, '_world_attempts', ()),
            perception=select(last, ('road_status', 'road_reasons', 'tape_status', 'tape_reasons', 'scene_status')),
            scene=None if scene is None else dict(status=processor._scene_status, reason=scene.blocked_reason,
                eligible=scene.eligible),
            catalog=None if catalog_view is None else dict(lane_status=catalog_view.lane_status, reasons=catalog_view.reasons,
                road_basis=None if basis is None else dict(status=basis.status, reason=basis.reason,
                    reference_id=basis.reference_id, reference_revision=basis.reference_revision,
                    latest_model_revision=basis.latest_model_revision, lane_ids=basis.lane_ids,
                    point_count=len(basis.points_f32)//8, point_error_max=max(basis.point_error_m, default=None),
                    segment_error_max=max(basis.segment_error_m, default=None))),
            metric=select(last, ('metric_input_reasons', 'preserved_metric_map', 'bootstrap_source_complete')),
            mapping=select(last, ('mapping_status', 'mapping_ready', 'mapping_reason', 'map_restore_issue',
                'map_frame', 'map_revision', 'map_counts')),
            localization=select(last, ('localization_status', 'localization_qualified', 'localization_configured',
                'quarantined', 'registration_status', 'reference_revision')),
            alignment=select(None if last is None else last.get('alignment_metadata'), ('pose', 'position_error_m',
                'yaw_error_rad', 'observed_j_interval', 'registration_revision', 'valid_for_us',
                'position_drift_mps', 'yaw_drift_rps', 'context')),
            time=dict(rig_now_us=self.now, summary=select(last, ('captured_us', 'rgb_captured_us', 'now_us')),
                item_expires_us=None if self.last_item is None else self.last_item.expires_us),
            source=select(last, ('source_binding_revision', 'source_vehicle_sequence', 'rgb_source_key',
                'depth_source_key', 'odom_origin', 'input_odom_origin')),
            free=select(last, ('free_status', 'free_temporal_status', 'free_cells_applied', 'free_ray_tests')),
            free_schedule=select(None if last is None else last.get('free_schedule'), ('status', 'selection_status',
                'needed_count', 'due_needed_count', 'selected_needed_count', 'unavailable_needed_count',
                'occupied_needed_count', 'due_not_scheduled_count', 'all_needed_live_fresh',
                'after_all_needed_live_fresh', 'necessary_capacity_ok', 'minimum_refresh_cycles', 'renewal_proven')),
            costs_us=None if last is None else last.get('costs_us'))
        # 원 summary 값을 재계산하지 않는다. station 수집/수락과 무관하게 출력만 마지막 두 행으로 제한한다.
        text = repr(dict(current=current, compute=self.compute.snapshot(),
            mapping='WAIT_FACTORY' if processor is None else processor._mapping_status,
            schedule=None if processor is None or processor._storage_schedule is None else processor._storage_schedule.status,
            scene='WAIT_FACTORY' if processor is None else processor._scene_status, bootstrap=self.bootstrap.failure,
            mailbox=mailbox, pending=pending, lane_observation=lane_observation, release_returns=self.releases[-4:],
            jio=self.jio.snapshot(), restore_validation=self._restore_validation_progress,
            station_diagnostics_count=len(self._station_diagnostics), station_diagnostics=self._station_diagnostics[-2:]))
        return text.encode('utf-8')[:8000].decode('utf-8', errors='ignore')

    def _lease(self, *, load=False):
        self._copy_generation += 1
        mapper = Endpoint(self.peer.channel_epoch, self.peer.mapper_worker)
        storage = Endpoint(uid(), self.binding['storage_worker'])
        producer, consumer = (storage, mapper) if load else (mapper, storage)
        return CopyLease(self.peer.service_instance, uid(), self._copy_generation, producer, consumer, uid(),
            raw_binding_sha256=self.raw.sha256,
            publication_binding_sha256=None if self.mapper.publication is None else sha256(canonical(self.mapper.publication)).hexdigest())

    @staticmethod
    def _owned(encoding):
        reader = replace(encoding.lease, read_token=uid())
        ready = dict(encoding.ready, copy_lease=reader.fields)
        return OwnedCopy(reader, canonical(ready), encoding.evidence, encoding.descriptors,
            encoding.manifest, encoding.tiles)

    def _drive_sql(self):
        head = self.mailbox.main_peek()
        if head is None:
            return
        request = head.request
        if self._dispatch is None:
            if type(request) is LoadRequest and request.purpose == 'BOOTSTRAP_RESTORE':
                if request.job.job_id not in self._bootstrap_allowed:
                    return
                current = request.job.context.sha256
            else:
                context = self.port.read()
                if context is None:
                    return
                current = context.sha256
            self._dispatch = self.mailbox.main_take(current_context_sha256=current, now_us=self.now)
            if self._dispatch is None:
                return
            self.sql_requests.append(request)
        dispatch = self._dispatch
        if dispatch.request is not request:
            raise AssertionError('one immutable D dispatch was replaced')
        if type(request) is ProvenanceRequest:
            committed = self.store.reserve_provenance(request.job, request.reservation_nonce,
                block_count=request.block_count, current_context_sha256=request.job.context.sha256)
            body = dict(database_uuid=request.job.context.database_uuid,
                mapper_worker=request.job.context.mapper_worker, reservation_nonce=request.reservation_nonce,
                start_inclusive=str(committed.start_inclusive), end_exclusive=str(committed.end_exclusive),
                highwater_after=str(committed.highwater_after), result='GRANTED')
            self._db_sequence += 1
            packet = encode_structure(dict(version=1, service_instance=self.peer.service_instance,
                channel_epoch=self.peer.channel_epoch, sender_instance=self.peer.service_instance,
                message_sequence=str(self._db_sequence), kind='DB_JOB', binding_domain='STORAGE', binding_revision='1',
                request_id=request.job.job_id, issued_j_us=str(self.now),
                expires_j_us=str(request.job.expires_us), payload=db_payload('RANGE_RESULT', body)))
            result = DbMessage(packet, 'RANGE_RESULT', canonical(body), self.now, request.job.expires_us)
        elif type(request) is CheckpointRequest:
            if self._writer is None:
                self._writer = self._lease()
                self._encode_request = self.mailbox.main_request_encode(dispatch, self._writer,
                    issued_us=self.now, expires_us=request.job.expires_us)
            encoded = self.mailbox.main_take_encoding(dispatch)
            if encoded is None:
                return
            if encoded.error is not None:
                raise AssertionError('actual compute checkpoint encoder: ' + encoded.error)
            copy = self._owned(encoded.encoding)
            decoded = copy.decode()
            if decoded.digest != request.digest or decoded.job != request.job:
                raise AssertionError('D copy changed the original checkpoint')
            result = self.store.commit(decoded.job, decoded.payload,
                current_context_sha256=request.job.context.sha256)
            self.commits.append((request, result))
        elif type(request) is LoadRequest:
            loaded = self.store.load(request, current_context_sha256=request.job.context.sha256)
            result = self._owned(encode_load(loaded, self._lease(load=True)))
            self.loads.append((request, loaded))
            if request.purpose == 'BOOTSTRAP_RESTORE':
                self._physical_completion = DbCompletion(canonical(prepare_payload(request)['body']),
                    canonical(result.ready), request.job.job_id, request.job.issued_us,
                    request.job.expires_us, self.now, False)
        else:
            raise AssertionError('unsupported actual D request ' + type(request).__name__)
        if not self.mailbox.main_complete(dispatch, result, completed_us=self.now):
            raise AssertionError('original D result was not admitted')
        # 이 dispatcher가 소유한 bytes의 SQL/codec 작업 종료다. native token/post 진술은 아니다.
        released = self.mailbox.main_release(dispatch)
        if len(self.releases) >= 128:
            raise AssertionError('bounded software copy cleanup receipts are full')
        self.releases.append((request.job.job_id, released))
        self._dispatch = self._writer = self._encode_request = None

    def _offer_vehicle(self, at_j_us, lease='CURRENT'):
        self.vehicle_sequence += 1
        message = schema.VehicleSample(major=1, vehicle=self.policy.vehicle,
            host_boot=self.origin['pi_host_boot'], gateway_instance=self.origin['gateway_instance'],
            device_session=self.origin['device_session'], pi_session=self.origin['pi_session'],
            odom_epoch=self.odom_epoch, sequence=self.vehicle_sequence, source_sample=self.vehicle_sequence,
            source_us=at_j_us, pi_sample_us=at_j_us + 100_000, clock_error_us=20,
            left_ticks=0, right_ticks=0, velocity_mps=0., position_error_m=.0001,
            heading_error_rad=.0001, quality=3, calibration=self.policy.calibration)
        message.odom_pose.CopyFrom(schema.Pose(x=0., y=0., yaw=0.))
        if lease != 'CURRENT' and lease is not self.jio.current_lease:
            raise AssertionError('software input cannot replace the actual current JIO lease')
        self.jio.deliver_vehicle(encode(message))

    def renew_clock(self):
        source = self.mapper.source
        trust = schema.WorldTrust(**{key: value for key, value in source.items() if key != 'device'},
            lower_offset_us=99_990, upper_offset_us=100_010, expires_pi_us=self.now + 1_000_000,
            max_error_us=100)
        probe = schema.MapperClockProbe(major=1, vehicle=source['vehicle'], device=source['device'],
            pi_host_boot=self.origin['pi_host_boot'], pi_client_instance=uid(),
            probe_seq=self.sequence + 1, nonce=UUID(uid()).bytes, pi_send_us=self.now + 100_000,
            admission_revision=1, expected_mapper_boot=source['mapper_boot'],
            expected_mapper_instance=source['mapper_instance'], expected_clock_epoch=source['clock_epoch'])
        probe.mapper_trust.CopyFrom(trust)
        self.last_probe_raw = encode(probe)
        self.jio.deliver_probe(self.last_probe_raw)
        self.lease = self.jio.current_lease
        if self.lease is None:
            raise AssertionError('explicit software prior trust failed its actual codec/helper')
        self.tick()

    def input(self, *, blank_rgb=False, missing_depth=False, duplicate=False, context=None, lifetime=150_000):
        if not duplicate:
            self.sequence += 1
        rgb_bytes, depth_bytes = observation_payloads(self.calibration,
            blank_rgb=blank_rgb, missing_depth=missing_depth)
        end = self.now - 5000
        rgb = RawFrame('rgb_video', self.sequence, 100_000 + self.sequence * _SAMPLE_CADENCE_US,
            end - 100_000, self.now, 640, 360, 'NV12', rgb_bytes, 100)
        depth = RawFrame('depth_right', self.sequence + 100,
            100_050 + self.sequence * _SAMPLE_CADENCE_US, end - 99_950, self.now,
            640, 480, 'RAW16_LE', depth_bytes, 100)
        clocks = {name: dict(bridge_sample=dict(j_before_us=str(self.now), sdk_us=str(self.now - 100_000),
            j_after_us=str(self.now)), sdk_host_timestamp_j_bracket_us=[str(at), str(at)])
            for name, at in (('rgb_video', end), ('depth_right', end + 50))}
        sample = CaptureSample(rgb, depth, None, None, clocks)
        item=MapperInput(uid(), self.raw.sha256, sample, self.now + lifetime,
            context_bytes=self.mapper.context_bytes() if context is None else context)
        self._ingest(item.context_bytes)
        return attach_sensor_snapshot(item,self.sensor_owner,self.config.capture_timing,now_us=self.now)

    def frame(self, **kwargs):
        self.now += _SAMPLE_CADENCE_US
        self.renew_clock()
        for at in (self.now - 11_000, self.now - 1000):
            self._offer_vehicle(at)
            self.tick()
        item = self.input(**kwargs)
        return self.submit(item)

    def submit(self, item):
        """원 CaptureSample을 actual compute queue로 넘기고 실제 final 채택 결과를 받는다."""
        self._station_snapshot=item.sensor_snapshot
        if not self.compute.submit(item):
            raise AssertionError('actual M-compute did not accept original frame')
        captured = []
        def done():
            result = self.compute.take_result()
            if result is not None:
                captured.append(result)
            cancelled = next((event for event in reversed(self.events)
                if event.job_id == item.job_id and event.kind == 'CANCELLED'), None)
            if cancelled is not None:
                raise AssertionError('actual compute cancelled: ' + str(cancelled.reason) + '; ' + self.diagnostics())
            return bool(captured)
        self.wait(done, 'actual Processor geometry/adoption')
        result = captured[0]
        self.last_result, self.last_item = result, item
        self.last_summary = None if result.payload is None else json.loads(result.payload)['latest']
        return result

    def world(self, *, frames=8, **variants):
        initial = len(self.jio.worlds)
        self._world_attempts = []
        for _ in range(frames):
            self.frame(**variants)
            self._world_attempts.append({key: self.last_summary.get(key) for key in (
                'rgb_source_key', 'world_candidate_ready', 'world_candidate_reason', 'mapping_reason',
                'quarantined', 'registration_status', 'localization_qualified', 'scene_status')})
            self.wait(lambda: self.compute.snapshot()['active'] is None, 'frame retirement')
            # 원 CP/head/range의 실제 완료가 다음 fresh pixels보다 먼저 필요하다.
            # 정해진 tick 수는 encode/SQL 완료의 증거가 아니므로 같은 원 요청을 끝까지 구동한다.
            self.settle_storage()
            if self.last_summary.get('world_candidate_ready') is True:
                self.wait(lambda: len(self.jio.worlds) > initial, 'actual final JIO publish')
            if len(self.jio.worlds) > initial:
                return decode(self.jio.worlds[-1][0], schema.WorldSnapshot)
        raise AssertionError('normal bytes produced no World: ' + self.diagnostics())

    def settle_storage(self):
        """실제 최신 accepted revision의 정상 CP 완료까지 원 요청들을 구동한다."""
        def complete():
            mapping, schedule = self.processor._mapping, self.processor._storage_schedule
            return (mapping is not None and schedule is not None
                and mapping.catalogs is not None
                and schedule._saved_revision >= mapping._revision
                and mapping.pending is None and mapping.catalogs.pending is None
                and mapping.catalogs._queued is None and self.mailbox.main_peek() is None)
        self.wait(complete, 'actual accepted map/catalog checkpoint completion')

    def grant(self, *, purpose=0, original=None):
        source = self.mapper.source
        issued = self.lease.j_interval_to_pi((self.now, self.now), self.now)[0] - 100
        if original is None:
            value = schema.SceneGrant(major=1, vehicle=source['vehicle'], pi_host_boot=self.origin['pi_host_boot'],
                gateway_instance=self.origin['gateway_instance'], source_admission_revision=self.lease.source_revision,
                mapper_device=source['device'], issued_pi_us=issued, valid_until_pi_us=issued + 14_400_000_000,
                lifecycle_state=0 if purpose == 0 else 1, request_nonce=UUID(uid()).bytes,
                survey_deadline_pi_us=issued + (180_000_000 if purpose == 0 else 10_000_000),
                max_observation_stations=24, purpose=purpose, lifecycle_revision=1,
                frozen_survey_revision=0, frozen_hazard_revision=0,
                **{key: source[key] for key in ('map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive')})
            value.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(uid()).bytes, admission_revision=1,
                survey_id=UUID(uid()).bytes, survey_revision=1, policy=1,
                coverage_profile_sha256=bytes.fromhex(self.config.survey.coverage_profile_sha256),
                allow_restore_after_mapper_restart=True))
        else:
            value = decode(encode(original), schema.SceneGrant)
            value.lifecycle_revision += 1
            value.request_nonce = UUID(uid()).bytes
        self.deliver_grant(value)
        return value

    def deliver_grant(self, grant):
        self.jio.deliver_scene(encode(grant))
        self.tick()

    def freeze_and_admit(self, grant=None):
        """실제 station/증거와 SQL 완료 뒤 외부 operator의 다음 원 grant를 전달한다."""
        grant = self.grant() if grant is None else grant
        for _ in range(6):
            self.frame()
            current = self.processor._scene
            if current is not None and current.scene._stations:
                break
        else:
            raise AssertionError('actual stationary observation is not ready: ' + self.diagnostics())
        pending = decode(encode(grant), schema.SceneGrant)
        pending.lifecycle_state = 1
        pending.lifecycle_revision += 1
        pending.request_nonce = UUID(uid()).bytes
        self.deliver_grant(pending)
        self.wait(lambda: self.processor._scene.scene._frozen is not None
            and self.processor._scene.scene._frozen.receipt is not None,
            'actual Scene FREE evidence/freeze/SQL commit')
        frozen = self.processor._scene.scene._frozen
        summary = decode(frozen.summary, schema.SurveySummary)
        admitted = decode(encode(pending), schema.SceneGrant)
        admitted.lifecycle_state = 2
        admitted.lifecycle_revision += 1
        admitted.frozen_evidence_sha256 = summary.evidence_sha256
        admitted.frozen_survey_revision = summary.survey_revision
        admitted.frozen_hazard_revision = summary.hazard_revision
        self.deliver_grant(admitted)
        self.wait(lambda: self.processor._scene.scene.eligible
            and self.mapper.publication['scene_admission_ref'] is not None,
            'original ADMITTED grant and actual SourcePublication')
        return admitted, frozen

    def restore_and_admit(self, predecessor_grant):
        """원 RESTORE_CHECK 수락 뒤의 실제 Vehicle 창을 공급한다. 제품 gate는 그대로다."""
        complete = self.bootstrap._complete
        if complete is None or complete.original_commit is None:
            raise AssertionError('actual original all-page SourceComplete is missing')
        selected_sha = complete.original_commit.validate(complete.original_job, complete.manifest,
            complete.checkpoint_ref)
        grant = decode(encode(predecessor_grant), schema.SceneGrant)
        grant.lifecycle_state = 1
        grant.lifecycle_revision += 1
        grant.purpose = 1
        grant.request_nonce = UUID(uid()).bytes
        grant.restore_checkpoint_sha256 = bytes.fromhex(selected_sha)
        grant.survey_deadline_pi_us = self.now + 100_000 + 10_000_000
        original_raw = encode(grant)
        profile = self.config.survey
        progress = dict(grant_sha256=sha256(original_raw).hexdigest(), request_nonce=grant.request_nonce.hex(),
            stage='WAIT_ORIGINAL_RESTORE_GRANT', frames_sent=0, validation_start_j=None,
            station_window_us=profile.station_window_us, max_pose_gap_us=profile.max_pose_gap_us,
            target_capture_lower_j=None, capture_j=None, window_started_j=None, window_completed_j=None,
            scene_status=None, summary_status=None, absolute_expires_j=None, frame_cap=None,
            registration_status=None, mapping_reason=None, localization_qualified=None)
        self._restore_validation_progress = progress
        # 원 supervisor360초 상한을 늘리지 않는다. M-only 전임 rig부터 같은 벽시계를 쓴다.
        self._restore_deadline_wall = self._bench_started_wall + 360.
        fixed_grant_expires_j = None

        def fail(reason):
            progress['stage'] = reason
            raise AssertionError('actual fresh restore validation ' + reason + ': ' + self.diagnostics())

        def snapshot():
            # 큰 검산/serialize 없이 actual accepted Scene의 원 참조만 읽는다.
            with self.port.adoption_lock:
                coordinator = None if self.processor is None else self.processor._scene
                scene = None if coordinator is None else coordinator.scene
                return (scene, None if scene is None else scene.grant_bytes,
                    None if scene is None else scene._validation_start_j,
                    None if scene is None else scene._lease,
                    None if scene is None else scene._observation,
                    () if scene is None else scene._station_windows,
                    None if scene is None else scene._frozen,
                    None if self.processor is None else self.processor._scene_status)

        def limits(scene_lease):
            nonlocal fixed_grant_expires_j
            if time.monotonic() >= self._restore_deadline_wall:
                fail('SUPERVISOR_WALL_BUDGET_EXPIRED')
            lease = self.jio.current_lease if scene_lease is None else scene_lease
            if lease is None or lease.source != self.mapper.source:
                fail('ORIGINAL_SCENE_CLOCK_UNAVAILABLE')
            trust = decode(lease.trust_bytes, schema.WorldTrust)
            grant_expires = min(grant.valid_until_pi_us, grant.survey_deadline_pi_us) - trust.upper_offset_us
            fixed_grant_expires_j = (grant_expires if fixed_grant_expires_j is None
                else min(fixed_grant_expires_j, grant_expires))
            expires = min(fixed_grant_expires_j, lease.expires_pi_us - trust.upper_offset_us)
            progress['absolute_expires_j'] = expires
            if self.now >= expires:
                fail('ORIGINAL_GRANT_SURVEY_OR_SCENE_LEASE_EXPIRED')
            return fixed_grant_expires_j

        try:
            self.deliver_grant(grant)
            frame_budget_end = limits(None)
            progress['frame_cap'] = max(0, (frame_budget_end - self.now - 1) // _SAMPLE_CADENCE_US)
            while True:
                scene, raw, started, lease, observation, windows, frozen, status = snapshot()
                limits(lease)
                progress['scene_status'] = status
                if self.last_summary is not None:
                    for key in ('registration_status', 'mapping_reason', 'localization_qualified'):
                        progress[key] = self.last_summary.get(key)
                if raw is not None and raw != original_raw:
                    fail('ORIGINAL_RESTORE_GRANT_CHANGED')
                if raw == original_raw:
                    progress['stage'] = 'WAIT_ACTUAL_CURRENT_SCENE_OBSERVATION'
                    if type(started) is not int:
                        fail('ACTUAL_VALIDATION_START_MISSING')
                    if progress['validation_start_j'] is None:
                        progress['validation_start_j'] = started
                        # 연속 표본의 마지막 left가 max gap만큼 앞에 있을 수 있다.
                        # 충분조건일 뿐이며 실제 witness 검산·원 station 수락은 제품 책임이다.
                        progress['target_capture_lower_j'] = started + profile.station_window_us + profile.max_pose_gap_us
                    elif progress['validation_start_j'] != started:
                        fail('ACTUAL_VALIDATION_START_CHANGED')
                    if (scene.profile.station_window_us, scene.profile.max_pose_gap_us) != (
                            profile.station_window_us, profile.max_pose_gap_us):
                        fail('ORIGINAL_STATION_PROFILE_CHANGED')
                    current_observation = (observation is not None and self.last_item is not None
                        and observation.stamp.sequence == self.last_item.sample.rgb.sequence
                        and self.last_summary is not None
                        and observation.stamp.key == tuple(self.last_summary.get('rgb_source_key', ())))
                    if current_observation:
                        capture = observation.raw_pose.captured_us
                        progress['capture_j'] = capture
                        # _station_windows는 실제 candidate.station이 원 typed witness에서 저장한 값이다.
                        # _station_diagnostics의 사후 재검산/accepted_on_copy는 성공 근거가 아니다.
                        window = next((row for row in reversed(windows)
                            if tuple(row.get('capture_us', ())) == capture), None)
                        if window is not None:
                            progress['window_started_j'] = window['started_us']
                            progress['window_completed_j'] = window['completed_us']
                        if frozen is not None:
                            summary = decode(frozen.summary, schema.SurveySummary)
                            progress['summary_status'] = summary.status
                            if (summary.status == 4 and self.last_summary.get('scene_status') == 'RESTORE_READY'
                                    and summary.request_nonce == grant.request_nonce
                                    and summary.evidence_sha256.hex() == selected_sha
                                    and window is not None and window['started_us'] >= started
                                    and window['started_us'] <= capture[0] - profile.station_window_us
                                    and window['completed_us'] >= capture[1]):
                                progress['stage'] = 'ACTUAL_RESTORE_READY_WITH_POST_START_WINDOW'
                                break
                        progress['stage'] = ('WAIT_POST_START_VEHICLE_WINDOW'
                            if capture[0] < progress['target_capture_lower_j'] else 'WAIT_PRODUCT_RESTORE_READY')
                if progress['frames_sent'] >= progress['frame_cap']:
                    fail('ORIGINAL_SOURCE_FRAME_BUDGET_EXHAUSTED')
                progress['frames_sent'] += 1
                self.frame()
            admitted = decode(original_raw, schema.SceneGrant)
            admitted.lifecycle_state = 2
            admitted.lifecycle_revision += 1
            self.deliver_grant(admitted)
            admitted_raw = encode(admitted)

            def admitted_current():
                scene, raw, _, lease, _, _, _, _ = snapshot()
                limits(lease)
                return (scene is not None and raw == admitted_raw and scene.eligible
                    and self.mapper.publication['scene_admission_ref'] is not None)

            self.wait(admitted_current, 'actual restored Scene ADMITTED publication')
            progress['stage'] = 'ACTUAL_RESTORED_SCENE_ADMITTED'
            return admitted, complete
        finally:
            self._restore_deadline_wall = None

    def stop(self):
        """실제 compute/data endpoint/JIO owner를 끝낸다. 재시작 시험의 D/DB 소유자는 유지한다."""
        compute = getattr(self, 'compute', None)
        if compute is not None:
            compute.request_stop()
            compute.join(2.)
            self.compute = None
        mapper, supervisor = getattr(self, 'mapper', None), getattr(self, 'supervisor', None)
        if mapper is not None:
            mapper.close()
            self.mapper = None
        if supervisor is not None:
            supervisor.close()
            self.supervisor = None
        jio = getattr(self, 'jio', None)
        if jio is not None: jio.close()

    def close(self):
        self.stop()
        temporary = getattr(self, 'temporary', None)
        if temporary is not None:
            db = getattr(self, 'db', None)
            if db is not None:
                db.close()
            temporary.cleanup()
            self.temporary = None
