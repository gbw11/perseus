"""M-compute의 원 frame 증거와 catalog/checkpoint를 한 D mailbox에 연결한다.

관측 후보는 외부 요청을 내보내지 않는다. 최종 채택은 참조 교환이며 maintenance만
원 request를 offer/소비한다. SQLite·native token·thread·scene/추진 권한은 소유하지 않는다.
ToF는 source scope에만 보존한다. 실제 ToF 시각의 보간/융합은 별도 연결 전까지 수행하지 않는다.
"""

from copy import copy
from dataclasses import dataclass,replace
from hashlib import sha256
from threading import RLock,get_ident
from uuid import uuid4

from .map_checkpoint import MapCheckpointCollector,MapCheckpointNotReady,FrameEvidence,SourceComplete
from .mapper_catalogs import MapperCatalogs,CatalogNotReady
from .mapper_compute import MapperInput
from .mapper_profile import MapperProfile
from .mapper_sensor_state import MapperSensorState,MetricInputs
from .mapper_storage import MapperStorageMailbox,StorageAbort
from .parking_bridge import WireProvenanceGrant
from .raw_binding import RawBinding
from .scene import SceneEvidenceScope
from ..mapping.odometry import PoseEstimate
from ..perception.calibration import CalibrationProfile
from ..perception.pipeline import PerceptionPipeline,PerceptionResult
from ..storage.contracts import (StorageContext,StorageJob,ProvenanceRequest,LoadRequest,CommitReceipt,
    canonical,json_bytes,uint,selected_checkpoint_reference)
from ..storage.geometry_scope import same_catalog_geometry
from ..storage.port import CheckpointRequest
from ..storage.records import EvidenceTable,LoadedCheckpoint


class MappingNotReady(RuntimeError):
    """원 승인 문맥/센서 bracket/내구 range 또는 이전 D terminal이 준비되지 않았다."""


class MappingContextPort:
    """M-main이 실제 IdentityACK의 불변 context 참조만 게시한다. ACK/승인을 생성하지 않는다."""
    def __init__(self):self._lock=RLock();self._context=None;self._material=None;self._scene=None

    @property
    def adoption_lock(self):
        """최종 compute 채택과 실제 publication 갱신이 공유하는 짧은 재진입 lock이다."""
        return self._lock

    def publish(self,context,*,material=None):
        """material=None은 원 material을 유지한다. JSON/출처 검산은 compute가 수행한다."""
        if context is not None and type(context) is not StorageContext:raise ValueError('original immutable StorageContext required')
        if material is not None and (type(material) is not bytes or not 0<len(material)<=2048):
            raise ValueError('original bounded immutable D material bytes required')
        with self._lock:
            if context!=self._context:self._context=context
            if material is not None:self._material=material

    def read(self):
        with self._lock:return self._context

    def read_material(self):
        with self._lock:return self._material

    def publish_scene(self,original_row):
        """main이 받은 원 SceneGrant delivery의 유한 bytes다. decode/수락은 compute 책임이다."""
        if original_row is not None and (type(original_row) is not bytes or not 0<len(original_row)<=2048):
            raise ValueError('bounded immutable original scene delivery required')
        with self._lock:self._scene=original_row

    def read_scene(self):
        with self._lock:return self._scene


@dataclass(frozen=True)
class MappingFrameDraft:
    original_job: MapperInput
    context: StorageContext | None
    pipeline: PerceptionPipeline
    collector: object
    inputs: MetricInputs
    frame: FrameEvidence | None
    observer: object
    accepted_revision: int
    ready: bool
    reason: str
    catalog_draft: object=None
    result: object=None


@dataclass(frozen=True)
class MappingPending:
    role: str
    request: object
    via_catalog: bool=False


@dataclass(frozen=True)
class MappingTerminal:
    request: object
    result: object
    current: bool
    observed_us: int


def _fork_collector(owner,pipeline):
    # frozen request/snapshot/proof/bytes는 공유한다. grid/lock/mailbox를 deepcopy하지 않는다.
    result=copy(owner)
    for name,value in vars(owner).items():
        if type(value) is dict:setattr(result,name,dict(value))
        elif type(value) is set:setattr(result,name,set(value))
    result._grid=pipeline.grid
    return result


def checkpoint_reference(request,receipt,*,now_us):
    """현재 원 COMMITTED CP의 ref13이다. 재조회/새 기한/현재 catalog head는 생성하지 않는다."""
    if (type(request) is not CheckpointRequest or type(receipt) is not CommitReceipt
            or request.payload.intent!='CHECKPOINT'
            or not receipt.current(request.job,request.payload,request.job.context.sha256,now_us)):
        raise MappingNotReady('actual current original checkpoint COMMITTED receipt required')
    job,payload=request.job,request.payload;c=job.context
    value=dict(checkpoint_job_id=job.job_id,durable_revision=str(receipt.durable_revision),
        manifest_sha256=sha256(payload.manifest).hexdigest(),manifest_length=len(payload.manifest),
        tile_count=len(payload.tile_references),map_epoch=c.map_epoch,frame_epoch=c.frame_epoch,odom_epoch=c.odom_epoch,
        pi_host_boot=c.pi_host_boot,configuration=c.configuration,calibration=c.calibration,
        primitive_sha256=c.primitive_sha256,origin_mapper_instance=c.origin_mapper_instance)
    raw=canonical(value);selected_checkpoint_reference(raw);return raw


class MapperMapping:
    """한 compute owner의 collector/catalog와 한 원 storage 요청을 보존한다.

    physical profile이 준비되면 최초 원 PROVENANCE 요청을 한 번만 생성한다. 실패/만료/
    UNKNOWN을 새 UUID/nonce/기한으로 자동 재발행하지 않는다. scene-only 전환은 이미
    수락한 range cursor를 유지하지만 실제 geometry source 교체는 새 owner를 요구한다.
    """
    def __init__(self,context_port,mailbox,raw_binding,calibration,profile,*,initial_storage_material,
                 complete=None,provenance_block_count=1<<20,job_lifetime_us=5_000_000,
                 crossing=None,aisle_lane_indices=None):
        if (type(context_port) is not MappingContextPort or type(mailbox) is not MapperStorageMailbox
                or type(raw_binding) is not RawBinding or type(calibration) is not CalibrationProfile or type(profile) is not MapperProfile):
            raise ValueError('actual context port/mailbox/raw/calibration/mapper profile required')
        if (type(initial_storage_material) is not tuple or len(initial_storage_material)!=2
                or type(initial_storage_material[0]) is not dict or type(initial_storage_material[1]) is not dict):
            raise ValueError('original admitted D binding/bootstrap required')
        raw=canonical(dict(binding=initial_storage_material[0],bootstrap=initial_storage_material[1]))
        material=json_bytes(raw,2048);binding,bootstrap=material['binding'],material['bootstrap']
        if (bootstrap.get('status') not in ('EMPTY','COMPLETE') or bootstrap.get('allocator_ready') is not True
                or bootstrap.get('schema_version')!=1 or binding.get('db_schema_version')!=1
                or binding.get('database_uuid')!=bootstrap.get('database_uuid')):
            raise ValueError('actual allocator-ready EMPTY/COMPLETE storage material required')
        if complete is not None and type(complete) is not SourceComplete:raise ValueError('actual decoded SourceComplete required')
        if complete is not None and (bootstrap['status']!='COMPLETE' or bootstrap.get('selected_checkpoint')!=json_bytes(complete.checkpoint_ref,2048)
                or complete.original_job.context.database_uuid!=bootstrap['database_uuid']):
            raise ValueError('decoded complete differs from original D bootstrap selection')
        uint(provenance_block_count,1<<20,positive=True);uint(job_lifetime_us,5_000_000,positive=True)
        self.context_port,self.mailbox,self.raw,self.calibration,self.profile=context_port,mailbox,raw_binding,calibration,profile
        self._material=raw;self._complete=complete;self._thread=get_ident()
        self._count,self._job_lifetime=provenance_block_count,job_lifetime_us
        policy=profile.catalog_policy
        self._crossing=crossing if crossing is not None or policy is None else policy.crossing
        self._roles=aisle_lane_indices if aisle_lane_indices is not None or policy is None else policy.aisle_lane_indices
        self.collector=self.catalogs=None;self._context=self._scope=self._pipeline=None
        self._pending=self._range_request=self._terminal=self._staged=None
        self._revision=0;self._cursor_floor=0;self._fenced=False
        self._restore_done=False;self._head_ready=False;self._head_load=None;self._head_offered=False
        self._checkpoint_ref=None if complete is None else complete.checkpoint_ref

    def _owner(self):
        if get_ident()!=self._thread:raise RuntimeError('mapping belongs to the original compute owner')

    @property
    def pending(self):return self._pending

    @property
    def ready(self):return self.collector is not None and not self._fenced

    @property
    def view(self):return None if self.catalogs is None else self.catalogs.view

    @property
    def evidence_scope(self):
        """수집한 원 scope 참조다. metric/scene 준비 또는 현재 lease 승인이 아니다."""
        return self._scope

    def adopt_source_complete(self,complete):
        """compute가 실제 full LOAD를 끝낸 원 선택만 연결한다. 실행 중 map을 덮지 않는다."""
        self._owner()
        bootstrap=json_bytes(self._material,2048)['bootstrap']
        if (type(complete) is not SourceComplete or bootstrap['status']!='COMPLETE'
                or json_bytes(complete.checkpoint_ref,2048)!=bootstrap.get('selected_checkpoint')
                or complete.original_job.context.database_uuid!=bootstrap['database_uuid']):
            raise ValueError('actual original selected COMPLETE source required')
        if self._complete is not None:
            if self._complete is not complete:raise ValueError('original decoded bootstrap source reference changed')
            return False
        if self.collector is not None or self._staged is not None:raise MappingNotReady('complete source arrived after map collection began')
        self._complete=complete;self._checkpoint_ref=complete.checkpoint_ref;return True

    def queue_head_load(self,job):
        """caller의 새 원 read job만 받는다. 실패/만료된 LOAD를 자동 갱신하지 않는다."""
        self._owner()
        if (type(job) is not StorageJob or job.context is not self._context or self._checkpoint_ref is None
                or self._pending is not None or self._staged is not None or self.mailbox.main_peek() is not None
                or self.catalogs is not None and self.catalogs.pending is not None):
            raise MappingNotReady('current source/original checkpoint reference and idle read job required')
        self._head_load=LoadRequest(job,self._checkpoint_ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        self._head_offered=False;return self._head_load

    def queue_provenance(self,request,*,now_us):
        """명시 새 원 작업만 수락한다. UNKNOWN/실패한 옛 nonce/range를 새 기한으로 포장하지 않는다."""
        self._owner();uint(now_us)
        previous=self._range_request
        if (type(request) is not ProvenanceRequest or request.job.context is not self._context
                or not request.job.current(self._context.sha256,now_us) or self.collector is not None or self._fenced
                or self._pending is not None or self._staged is not None or self.mailbox.main_peek() is not None
                or previous is not None and (request.job.job_id==previous.job.job_id or request.reservation_nonce==previous.reservation_nonce)):
            raise MappingNotReady('explicit new original provenance job/nonce and idle source required')
        self._range_request=request;self._pending=MappingPending('PROVENANCE',request)
        return request

    def _qualified(self):
        return bool(self.calibration.kind=='LIVE' and self.calibration.free_qualified
            and self.profile.calibration_profile_sha256==self.calibration.sha256
            and self.raw.fields['calibration_bundle']==self.calibration.sha256
            and self.profile.timing_configured and self.profile.localization_configured and self.profile.motion_qualified
            and self.profile.targets and all(target.qualified for target in self.profile.targets)
            and self.profile.pipeline.permission_polygon)

    def _make_scope(self,context,*,now_us,inputs=None,sensors=None):
        sources=[]
        if inputs is not None:
            for evidence in inputs.tof:
                item=self.profile.sensors.tof_leaves.get(evidence.sensor_id)
                if (item is not None and sensors.sensors.current_tof(evidence,now_us)
                        and all((item[1].extrinsics_qualified,item[1].first_surface_qualified,
                                 item[1].coverage_qualified,item[1].time_qualified))):
                    sources.append((item[0],item[1],evidence.sensor_instance,evidence.sensor_generation))
        old=() if self._scope is None else self._scope._historical
        if self._scope is not None and self._scope._tof!=tuple(sources):old=(*old,self._scope)
        scope=SceneEvidenceScope(context,self.raw,self.calibration,self.profile.sha256,
            tof_sources=tuple(sources),historical_scopes=old)
        if self._complete is not None and not self._restore_done:
            scope=scope.with_complete_history(self._complete,now_us=now_us)
        return scope

    def _bind_context(self,now_us):
        context=self.context_port.read()
        if context is None:return 'WAIT_CONTEXT'
        self.profile.check(self.calibration,vehicle_configuration=context.configuration,vehicle_calibration=context.calibration)
        bootstrap=json_bytes(self._material,2048)['bootstrap']
        if context.database_uuid!=bootstrap['database_uuid']:raise ValueError('source context differs from actual D database')
        if self._context is not None and context.bytes!=self._context.bytes:
            if not same_catalog_geometry(self._context,context,'SLOT'):
                self._fenced=True;self.mailbox.fence(context.sha256,now_us=now_us);return 'GEOMETRY_SOURCE_FENCED'
            if self.catalogs is not None and not self.catalogs.replace_publication_context(context,now_us=now_us):return 'WAIT_ORIGINAL_CONTEXT_TERMINAL'
            scope=self._make_scope(context,now_us=now_us)
            if self.collector is not None:self.collector.rebind_scope(scope)
            self._scope=scope;self._head_ready=False
            if self._checkpoint_ref is not None:
                # 새 full publication용 실제 read다. 이전 read/COMMIT의 성공이나 기한을 재사용하지 않는다.
                self._head_load=LoadRequest(StorageJob(context,str(uuid4()),now_us,now_us+self._job_lifetime),
                    self._checkpoint_ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
                self._head_offered=False
        if self._scope is None:self._scope=self._make_scope(context,now_us=now_us)
        self._context=context
        if self._fenced:return 'GEOMETRY_SOURCE_FENCED'
        if self.mailbox.context_sha256!=context.sha256:self.mailbox.fence(context.sha256,now_us=now_us)
        return 'BOUND'

    def _grant(self,result,request,now_us):
        if (type(result) is not WireProvenanceGrant or result.request is not request
                or not result.current(request,self._context.sha256,now_us)):
            return False
        scope=self._make_scope(self._context,now_us=now_us)
        collector=MapCheckpointCollector(scope,result,now_us=now_us)
        catalogs=MapperCatalogs(self._context,mailbox=self.mailbox,clock_id=self.raw.fields['clock_epoch'],
            map_frame_id='odom:'+self._context.odom_epoch,body=self.calibration.body,
            method_manifest_sha256=self.profile.sha256,input_kind='LIVE',calibration_profile=self.calibration,
            parking_aisles=self.profile.parking_aisles,aisle_lane_indices=self._roles,crossing=self._crossing,
            lane_profile=self.profile.pipeline.registration)
        if self._complete is not None:scope=catalogs.restore_complete(self._complete,current_scope=scope,now_us=now_us);collector.rebind_scope(scope)
        self.collector,self.catalogs,self._scope=collector,catalogs,scope
        if self._complete is not None and self._head_load is None:
            self._head_load=LoadRequest(StorageJob(self._context,str(uuid4()),now_us,now_us+self._job_lifetime),
                self._complete.checkpoint_ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
            self._head_offered=False
        self._cursor_floor=collector._next;return True

    def _consume(self,delivery,now_us):
        pending=self._pending
        if pending is None or delivery.request is not pending.request:return False
        result=delivery.admitted_result
        if type(result) is CommitReceipt and result.status=='UNKNOWN':return False
        from .storage_messages import DbMessage
        if type(result) is DbMessage and result.op=='RANGE_RESULT' and result.body['result']=='UNKNOWN':return False
        current=bool(delivery.current)
        if pending.role=='PROVENANCE':
            try:current=current and self._grant(result,pending.request,now_us)
            except (ValueError,CatalogNotReady,MapCheckpointNotReady):current=False
        elif pending.role=='CHECKPOINT':
            if current and type(result) is CommitReceipt:
                current=self.collector.accept_commit(pending.request,result,now_us=now_us)
                if current:self._checkpoint_ref=checkpoint_reference(pending.request,result,now_us=now_us)
                if current and not self._head_ready and self._head_load is None:
                    self._head_load=LoadRequest(StorageJob(self._context,str(uuid4()),now_us,now_us+self._job_lifetime),
                        self._checkpoint_ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
                    self._head_offered=False
            else:self.collector.retire_checkpoint(pending.request)
        elif pending.role=='HEAD_LOAD':
            if current and type(result) is LoadedCheckpoint:
                self.catalogs.observe_heads(result.heads,pending.request.job,now_us=now_us);self._head_ready=True
        self._terminal=MappingTerminal(pending.request,result,current,now_us);self._pending=None
        return True

    def _retire_unoffered_catalog(self):
        """정상 publication 교체의 미게시 CP만 닫는다. 전송/UNKNOWN은 여기를 통과하지 않는다."""
        pending=self._pending
        catalog=self.catalogs
        terminal=None if catalog is None else catalog._terminal
        if (pending is None or not pending.via_catalog or pending.role!='CHECKPOINT'
                or terminal is None or terminal.request is not pending.request
                or terminal.reason!='SOURCE_REPLACED' or catalog.pending is not None
                or catalog._queued is not None or catalog._submitted
                or self.mailbox.main_peek() is not None):return False
        if self.collector._pending is not pending.request:
            raise MappingNotReady('original unpublished collector checkpoint differs')
        catalog.take_terminal()
        self.collector.retire_checkpoint(pending.request)
        self._terminal=MappingTerminal(pending.request,StorageAbort('SOURCE_REPLACED'),False,terminal.observed_us)
        self._pending=None
        return True

    def maintenance(self,*,now_us,catalog_arguments=None):
        """최종 frame 결정 뒤에만 호출한다. 같은 원 요청을 유지하고 실패 작업을 자동 재발행하지 않는다."""
        self._owner();uint(now_us)
        if self._staged is not None:raise MappingNotReady('original geometry draft is not terminal')
        if not self._qualified():return 'PROFILE_NOT_READY'
        bound=self._bind_context(now_us)
        self._retire_unoffered_catalog()
        if bound!='BOUND':
            if bound=='WAIT_ORIGINAL_CONTEXT_TERMINAL' and self.catalogs is not None:
                self.catalogs.maintenance(now_us=now_us)
                if self._pending is not None and self._pending.via_catalog:
                    delivery=self.catalogs.take_delivery()
                    if delivery is not None:self._consume(delivery,now_us)
            return bound
        self.mailbox.compute_encode_pending(current_context_sha256=self._context.sha256,now_us=now_us)
        if self._pending is not None:
            pending=self._pending
            if pending.via_catalog:
                self.catalogs.maintenance(now_us=now_us)
                delivery=self.catalogs.take_delivery()
            else:
                peek=self.mailbox.main_peek()
                delivery=None if peek is None or peek.request is not pending.request else self.mailbox.compute_take(
                    current_context_sha256=self._context.sha256,now_us=now_us)
            if delivery is not None:self._consume(delivery,now_us)
            if self._pending is not None:
                if now_us>=pending.request.job.expires_us and self.mailbox.main_peek() is None:
                    self._terminal=MappingTerminal(pending.request,StorageAbort('EXPIRED'),False,now_us)
                    if pending.role=='CHECKPOINT':self.collector.retire_checkpoint(pending.request)
                    self._pending=None;return 'UNSTARTED_EXPIRED'
                if not pending.via_catalog:self.mailbox.compute_offer(pending.request,now_us=now_us)
                return 'WAIT_ORIGINAL_D_JOB'
        if self.mailbox.main_peek() is not None:return 'WAIT_PHYSICAL_RELEASE'
        if self.collector is None:
            if self._range_request is not None:return 'PROVENANCE_ORIGINAL_TERMINAL'
            if json_bytes(self._material,2048)['bootstrap']['status']=='COMPLETE' and self._complete is None:return 'WAIT_SOURCE_COMPLETE'
            self._range_request=ProvenanceRequest(StorageJob(self._context,str(uuid4()),now_us,now_us+self._job_lifetime),str(uuid4()),self._count)
            self._pending=MappingPending('PROVENANCE',self._range_request)
            offered=self.mailbox.compute_offer(self._range_request,now_us=now_us)
            return 'PROVENANCE_OFFERED' if offered else 'WAIT_MAILBOX'
        if self._head_load is not None and not self._head_offered:
            self._pending=MappingPending('HEAD_LOAD',self._head_load)
            self._head_offered=True
            offered=self.mailbox.compute_offer(self._head_load,now_us=now_us)
            return 'HEAD_LOAD_OFFERED' if offered else 'WAIT_MAILBOX'
        arguments={} if catalog_arguments is None else catalog_arguments
        if type(arguments) is not dict:raise ValueError('explicit original catalog arguments required')
        return self.catalogs.maintenance(now_us=now_us,**arguments)

    def prepare_frame(self,item,pipeline,observation,inputs,sensors,now_us):
        """큰 frame/proof 작업은 compute에서만 한다. ready=False 후보는 metric 채택하면 안 된다."""
        self._owner();uint(now_us)
        if self._staged is not None:raise MappingNotReady('one original frame draft is unresolved')
        if (type(item) is not MapperInput or type(pipeline) is not PerceptionPipeline or type(inputs) is not MetricInputs
                or type(sensors) is not MapperSensorState or now_us>=item.expires_us):raise ValueError('actual current compute input required')
        context=self.context_port.read();collector=None;frame=None;reason='PROVENANCE_NOT_READY'
        if self.collector is not None:collector=_fork_collector(self.collector,pipeline);collector._next=max(collector._next,self._cursor_floor)
        ready=False
        if (collector is not None and not self._fenced and context is not None and context is self._context
                and inputs.storage_context==context and inputs.pose is not None and inputs.clock_lease is not None):
            source=json_bytes(item.context_bytes,65536)
            if source.get('context_sha256')!=context.sha256 or source.get('storage_context')!=context.fields:
                raise ValueError('original job/source context differs from actual IdentityACK')
            try:
                scope=self._make_scope(context,now_us=now_us,inputs=inputs,sensors=sensors);collector.rebind_scope(scope)
                if self._complete is not None and not self._restore_done:
                    collector.retain_complete(self._complete,pipeline,selected_bootstrap=json_bytes(self._material,2048)['bootstrap'])
                bracket=sensors.original_pose_bracket(inputs.pose);originals=list(bracket.originals)
                if inputs.vehicle is None:raise MappingNotReady('actual current registration reference Vehicle unavailable')
                anchor=sensors.original_vehicle_admission(inputs.vehicle)
                if all(row is not anchor for row in originals):originals.append(anchor)
                parents=[];contexts={}
                for original in originals:
                    record,record_context=collector.vehicle_admission_parent(original,sensor_state=sensors)
                    parents.append(record);contexts[record_context.sha256]=record_context
                pi=inputs.clock_lease.j_interval_to_pi(observation.capture_interval,now_us)
                frame=FrameEvidence.from_capture(scope,observation,item.sample,pi_interval=pi,
                    observation_sequence=observation.rgb.stamp.sequence,parents=tuple(parents),contexts=tuple(contexts.values()),
                    clock_lease=inputs.clock_lease,now_us=now_us,reference_vehicle=inputs.vehicle,admission=sensors.sensors,
                    registration_valid_for_us=self.profile.localization.registration_valid_for_us,
                    pose_bracket=bracket,sensor_state=sensors)
            except (ValueError,MappingNotReady,MapCheckpointNotReady):reason='ORIGINAL_FRAME_PROOF_NOT_READY'
            else:ready=True;reason='READY'
        observer=None if collector is None else collector.frame_observer(frame,pipeline=pipeline)
        self._staged=MappingFrameDraft(item,context,pipeline,collector,inputs,frame,observer,self._revision,ready,reason)
        return self._staged

    def finish_frame(self,draft,result,*,now_us):
        self._owner();uint(now_us)
        if draft is not self._staged or type(result) is not PerceptionResult:raise ValueError('actual original frame draft/result required')
        collector=draft.collector;catalog=None;ready=draft.ready;reason=draft.reason
        try:
            if ready:
                if result.summary.get('quarantined') is True:ready=False;reason='MAP_QUARANTINED'
                else:
                    collector.observe_result(draft.frame,result,draft.pipeline,raw_pose=draft.inputs.pose)
                    capture=sha256(canonical(draft.frame.observation.rgb.stamp.key)).hexdigest()
                    root=collector._reference_geometry.get(capture,collector._rgb_refs.get(capture))
                    if root is None:raise MappingNotReady('actual original RGB provenance missing')
                    evidence=EvidenceTable(tuple(collector._records[key] for key in sorted(collector._records)),
                        tuple(collector._contexts[key] for key in sorted(collector._contexts)))
                    pose=draft.inputs.pose
                    if result.registration is not None and result.registration.status=='BOUNDED':
                        fit=result.registration;pose=replace(pose,sample=replace(pose.sample,x=fit.representative[0],
                            y=fit.representative[1],yaw=fit.representative[2],position_error_m=fit.position_error_m,yaw_error_rad=fit.yaw_error_rad))
                    elif draft.pipeline._alignment is not None:pose=draft.pipeline._alignment.apply(pose)
                    if list((pose.sample.x,pose.sample.y,pose.sample.yaw))!=result.summary.get('mapped_pose'):
                        raise MappingNotReady('catalog pose differs from actual mapped result')
                    grid=draft.pipeline.grid;spec=grid.spec;x,y=spec.origin_xy
                    catalog=self.catalogs.prepare(draft.original_job,result,context=draft.context,stamp=draft.frame.observation.rgb.stamp,
                        capture_prior=pose,registration_revision=0 if draft.pipeline.reference is None else draft.pipeline.reference.revision,
                        prior_qualified=result.summary.get('localization_qualified') is True,
                        metric_evidence=self.catalogs.metric_from_frame(draft.frame),lane_provenance_ids=(root,root),
                        base_evidence=evidence,now_us=now_us,roi_bounds=(x,y,x+spec.width*spec.resolution_m,y+spec.height*spec.resolution_m))
        except (ValueError,MappingNotReady,MapCheckpointNotReady,CatalogNotReady):ready=False;reason='FRAME_OR_CATALOG_NOT_READY'
        finally:
            if collector is not None:self._cursor_floor=max(self._cursor_floor,collector._next)
        self._staged=replace(draft,ready=ready,reason=reason,catalog_draft=catalog,result=result)
        return self._staged

    def accept(self,draft):
        """최종 current/기한 판단 뒤 참조만 바꾼다. 원 range cursor는 취소해도 재사용하지 않는다."""
        self._owner()
        if draft is not self._staged or draft.accepted_revision!=self._revision or draft.result is None or not draft.ready:
            raise ValueError('original finalized ready mapping draft required')
        if draft.catalog_draft is not None:self.catalogs.accept(draft.catalog_draft)
        self.collector,self._pipeline=draft.collector,draft.pipeline;self._scope=draft.collector.scope
        self._restore_done|=self._complete is not None;self._revision+=1;self._staged=None

    def cancel(self,draft):
        self._owner()
        if draft is not self._staged:raise ValueError('original staged mapping draft required')
        if draft.catalog_draft is not None:self.catalogs.discard(draft.catalog_draft)
        if draft.collector is not None:self._cursor_floor=max(self._cursor_floor,draft.collector._next)
        self._staged=None

    def queue_checkpoint(self,job,*,now_us,scene=None,subsystem_state=None):
        """명시 원 job으로 확정 상태를 고정한다. 이 함수는 D offer/fsync를 하지 않는다."""
        self._owner();uint(now_us)
        if (not self.ready or self._pipeline is None or self._staged is not None or self._pending is not None
                or self.mailbox.main_peek() is not None or type(job) is not StorageJob or job.context is not self._context):
            raise MappingNotReady('accepted source/map and one original current checkpoint job required')
        if not self._head_ready and self._checkpoint_ref is not None:
            raise MappingNotReady('actual current publication LOAD head required before catalog checkpoint')
        state=self.catalogs.state_fields()
        if subsystem_state is not None:
            if type(subsystem_state) is not dict or set(subsystem_state)&set(state):raise ValueError('independent original subsystem state required')
            state.update(subsystem_state)
        if not self._head_ready:
            request=self.collector.capture(self._pipeline,job,now_us=now_us,scene=scene,subsystem_state=state)
            self._pending=MappingPending('CHECKPOINT',request)
        else:
            snapshot=self.collector.snapshot(self._pipeline,job,now_us=now_us,subsystem_state=state)
            metadata={} if scene is None else dict(scene)
            if 'mapper_state' in metadata:raise ValueError('original mapper snapshot owns its META reference')
            metadata['mapper_state']=snapshot.mapper_state_ref
            request=None
            try:
                request=self.catalogs.prepare_checkpoint(job,base_evidence=snapshot.evidence,now_us=now_us,
                    tiles=snapshot.tiles,tile_references=snapshot.tile_references,
                    durable_cutoff_pi_us=snapshot.durable_cutoff_pi_us,scene=metadata)
                self.collector.track_checkpoint(request,catalog_extension=True)
            except Exception:
                if self.collector._pending is None:self.collector.discard_snapshot(snapshot)
                if request is not None:self._fenced=True
                raise
            self._pending=MappingPending('CHECKPOINT',request,True)
        return request

    def cancel_unoffered_checkpoint(self,request,*,now_us):
        """scene 최종검산 실패의 미게시 CP다. 원 D 결과/회수 완료를 생성하지 않는다."""
        self._owner();uint(now_us)
        pending=self._pending
        if (pending is None or pending.role!='CHECKPOINT' or pending.request is not request
                or self._staged is not None or self.mailbox.main_peek() is not None):
            raise MappingNotReady('exact original unpublished checkpoint required')
        if pending.via_catalog:self.catalogs.cancel_unoffered_checkpoint(request,now_us=now_us)
        self.collector.retire_checkpoint(request)
        self._pending=None;self._terminal=MappingTerminal(request,StorageAbort('CANCELLED'),False,now_us)

    def take_terminal(self):
        self._owner();result,self._terminal=self._terminal,None;return result
