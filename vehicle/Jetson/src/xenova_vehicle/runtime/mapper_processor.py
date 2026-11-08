"""M-compute 하나의 parser와 후보 core를 소유한다. actor/SDK/원 token은 만지지 않는다.

기존 채택 core를 독립 후보로 복사해 처리하고, compute owner의 최종 현재성 판정 뒤
accepted()에서 참조만 교체한다. 취소는 후보만 버리며 기존 map/HWM을 초기화하지 않는다.
결과는 main과 공유할 mutable array 없이 유한 JSON bytes로 넘긴다.
"""

from copy import deepcopy
from dataclasses import dataclass,replace
import json
from threading import get_ident

from .resident_transport import j_us


@dataclass(frozen=True)
class _FrameCandidate:
    """같은 관측 경계에서 채택할 원 sequence/scene/hazard 참조. 외부 요청은 없다."""
    sequence: int
    scene: object=None
    hazard: object=None
    scene_row: bytes | None=None


class ResidentMapperProcessor:
    """생성·parse·geometry·최종 채택은 모두 같은 compute thread의 책임이다."""

    def __init__(self, profile_bytes, binding, *, clock=j_us,mapper_profile_bytes=None,
                 source_policy=None,clock_policy=None,mapper_worker=None,mapper_instance=None,
                 mapping_context_port=None,mapping_mailbox=None,checkpoint_interval_us=None,publication_port=None):
        from .resident_perception import ResidentPerception
        if (mapping_context_port is None)!=(mapping_mailbox is None):raise ValueError('mapping port and mailbox must be supplied together')
        if mapping_context_port is not None:
            from .mapper_mapping import MappingContextPort
            from .mapper_storage import MapperStorageMailbox
            if type(mapping_context_port) is not MappingContextPort or type(mapping_mailbox) is not MapperStorageMailbox:
                raise ValueError('actual mapping context port and storage mailbox required')
        if publication_port is not None:
            from .mapper_publication import MapperPublicationPort
            if (type(publication_port) is not MapperPublicationPort
                    or publication_port.context_port is not mapping_context_port):
                raise ValueError('publication and mapping must share the original context port')
        self._publication_port=publication_port;self._world_sequence=0
        self._owner_thread=get_ident();self._admission_revision=0
        self._scene=self._hazard=self._scene_draft=self._hazard_draft=None
        self._frame_sequence=0;self._scene_status='DISABLED'
        self._latest_source_inputs=None;self._scene_row=self._scene_delivery=None
        self._scene_summary_sent=None;self._scene_freeze_request=None
        self._scene_freeze_attempt=None
        self._scene_freeze_prequeue=None
        self._scene_freeze_prequeue_fenced=False
        self._scene_restore_archive=None
        self.clock=clock
        if checkpoint_interval_us is not None:
            from ..storage.contracts import uint
            uint(checkpoint_interval_us,5_000_000,positive=True)
        self._checkpoint_interval=checkpoint_interval_us;self._storage_schedule=None
        self._mapping_context_port,self._mapping_mailbox=mapping_context_port,mapping_mailbox
        self._mapping=self._mapping_draft=None;self._mapping_status='DISABLED' if mapping_context_port is None else 'WAIT_MATERIAL'
        self._retired=None
        self._config=self._diagnostic=None
        self._profile_bytes,self._binding=profile_bytes,binding
        if mapper_profile_bytes is not None:
            from .mapper_profile import MapperProfile
            from ..storage.contracts import uuid
            self._config=MapperProfile.from_bytes(mapper_profile_bytes)
            self._accepted=ResidentPerception(profile_bytes,binding,pipeline_profile=self._config.pipeline,
                targets=self._config.targets,rolling_policy=self._config.rolling_roi)
            if source_policy is None or clock_policy is None:raise ValueError('mapper requires the original source/clock policies')
            source_policy.check_profile(self._accepted.calibration)
            self._config.check(self._accepted.calibration,vehicle_configuration=source_policy.configuration,
                               vehicle_calibration=source_policy.calibration)
            # 센서 HWM/history는 main 소유다. compute에는 원 등록 identity만 고정한다.
            self._mapper_worker,self._mapper_instance=uuid(mapper_worker),uuid(mapper_instance)
        else:
            self._accepted=(None if profile_bytes is None else ResidentPerception(profile_bytes,binding))
        self._staged=None
        self._bootstrap_source=self._durable_source=None
        self._restore_attempt=None

    def admission_fenced(self,revision,reason):
        """같은 compute owner에서만 원 admission 철회를 소비한다. map/parser는 유지한다.

        실제 queued D job은 원 terminal을 기다린다. 미게시 freeze도 원 job/E를 남기고
        같은 grant의 자동 재발급만 막는다. main은 Scene 객체를 직접 변경하지 않는다.
        """
        from ..storage.contracts import uint
        if get_ident()!=self._owner_thread:raise RuntimeError('admission fence belongs to original compute owner')
        uint(revision)
        if type(reason) is not str or not 1<=len(reason)<=64 or not reason.isascii():
            raise ValueError('bounded original admission fence reason required')
        if revision<self._admission_revision:raise ValueError('admission fence revision regressed')
        if revision==self._admission_revision:return False
        self._admission_revision=revision
        retiring=(self._staged,self._latest_source_inputs)
        self._cancel_mapping_draft();self._staged=None
        self._latest_source_inputs=None;self._scene_summary_sent=None
        if self._scene_freeze_prequeue is not None:self._scene_freeze_prequeue_fenced=True
        self._scene_status='ADMISSION_FENCED'
        if self._scene is not None:self._scene.revoke('SOURCE',now_j_us=self.clock())
        reports=() if self._publication_port is None else self._publication_port.discard_pending()
        self._retired=(retiring,reports)
        return True

    def maintenance(self):
        """compute만 원 material/D 요청을 처리한다. dirty 일정은 caller가 명시한 경우만 켠다."""
        self._retired=None
        if self._mapping_context_port is None:return 'MAPPING_DISABLED'
        if self._config is None or self._accepted is None:return 'MAPPING_PROFILE_DISABLED'
        if self._staged is not None:return 'MAPPING_DRAFT_PENDING'
        if self._mapping is None:
            material=self._mapping_context_port.read_material()
            if material is None:return 'WAIT_MATERIAL'
            from .mapper_mapping import MapperMapping
            from ..storage.contracts import json_bytes
            value=json_bytes(material,2048)
            if type(value) is not dict or set(value)!={'binding','bootstrap'}:raise ValueError('exact original D material required')
            self._mapping=MapperMapping(self._mapping_context_port,self._mapping_mailbox,self._binding,
                self._accepted.calibration,self._config,initial_storage_material=(value['binding'],value['bootstrap']),
                complete=self._durable_source,clock_us=self.clock)
            if self._checkpoint_interval is not None:
                from .mapper_storage_schedule import MapperStorageSchedule
                self._storage_schedule=MapperStorageSchedule(self._mapping,interval_us=self._checkpoint_interval,
                    checkpoint_arguments=self._scene_checkpoint_arguments)
        now=self.clock()
        arguments={} if self._storage_schedule is None else self._storage_schedule.next_catalog_arguments(now_us=now)
        self._mapping_status=self._mapping.maintenance(now_us=now,catalog_arguments=arguments)
        self._maintain_scene()
        status=(self._mapping_status if self._storage_schedule is None else
            self._storage_schedule.step(self._mapping_status,now_us=self.clock()))
        # step이 실제 원 terminal을 소비한 뒤 동일 compute owner에 전달한다.
        if self._scene is not None:
            from .scene import SceneError
            try:self._consume_scene_terminal(self.clock())
            except SceneError:self._scene_status='SCENE_STORAGE_NOT_READY'
        return status

    def _maintain_scene(self):
        """원 operator/clock/DB terminal을 compute에서 소비한다. 현재 관측이나 승인을 만들지 않는다."""
        if self._config is None or self._config.survey is None:return
        from .scene import SceneError
        from .mapper_sensor_state import _row
        from ..storage.contracts import json_bytes
        from xenova_drive_world import decode,schema
        now=self.clock();row=self._mapping_context_port.read_scene()
        try:
            if row is not None and row is not self._scene_row:
                delivery=_row(json_bytes(row,2048))
                if delivery[1]>now:raise SceneError('future original SceneGrant delivery')
                self._scene_delivery=delivery;self._scene_row=row
            if self._scene is not None and row is not None:
                raw=self._scene_delivery[0];grant=decode(raw,schema.SceneGrant)
                if grant.lifecycle_state in (3,4):
                    inputs=self._latest_source_inputs
                    lease=None if inputs is None else inputs.clock_lease
                    if self._scene.maintenance(raw,lease,now_j_us=now):
                        self._scene_status=self._scene.scene.blocked_reason or 'REVOKED'
                        return
                    # 옛 nonce의 부정 사건은 현재 복원을 취소하지 않는다. 현재 Scene의
                    # 실제 원 lease/만료는 별도로 확인하며 새 기한을 부여하지 않는다.
                    try:self._scene.scene._now(now)
                    except SceneError:
                        self._scene.revoke('CLOCK',now_j_us=now);self._scene_status='WAIT_SCENE_CLOCK'
                    else:self._scene_status='STALE_SCENE_NEGATIVE'
                    return
        except (ValueError,TypeError,OverflowError):
            self._scene_status='SCENE_INPUT_NOT_READY'
            if self._scene is not None:self._scene.revoke('SOURCE',now_j_us=now)
            return
        if self._mapping is None or self._mapping.evidence_scope is None:
            self._scene_status='WAIT_MAPPING_SCOPE';return
        inputs=self._latest_source_inputs;scope=self._mapping.evidence_scope
        if inputs is None or inputs.source is None or inputs.clock_lease is None:
            self._scene_status='WAIT_SCENE_CLOCK'
            if self._scene is not None:self._scene.revoke('CLOCK',now_j_us=self.clock())
            return
        if row is None:
            self._scene_status='WAIT_SCENE_GRANT'
            if self._scene is not None:self._scene.revoke('SOURCE',now_j_us=self.clock())
            return
        from .mapper_scene import SceneCoordinator
        now=self.clock()
        try:
            if scope.context is not self._mapping_context_port.read():
                self._scene_status='WAIT_SCENE_PUBLICATION';return
            if self._scene is None:
                self._scene=SceneCoordinator(scope.context,inputs.source,self._config.survey,
                    gateway_instance=scope.context.geometry_origin_fields['gateway_instance'],evidence_scope=scope)
            elif self._scene.scene.evidence_scope is not scope:self._scene.rebind_scope(scope)
            self._scene.maintenance(self._scene_delivery[0],inputs.clock_lease,now_j_us=now)
            self._consume_scene_terminal(now)
            self._scene_status=self._scene.scene.blocked_reason or 'ELIGIBLE'
            self._queue_scene_freeze(now)
            self._offer_scene_summary(now)
        except SceneError:
            self._scene_status='SCENE_NOT_READY'
            if self._scene is not None:self._scene.revoke('CLOCK',now_j_us=now)

    def _scene_checkpoint_arguments(self):
        """증분 CP에 원 동결 base 계보를 보존한다. 새 scene/셀 시각을 발급하지 않는다."""
        arguments={}
        hazard=self._hazard_checkpoint_state()
        if hazard:arguments['subsystem_state']=hazard
        if self._scene is None:return arguments
        frozen=self._scene.scene._frozen
        if frozen is None or frozen.receipt is None:return arguments
        from xenova_drive_world import decode,schema
        grant=decode(self._scene.scene.grant_bytes,schema.SceneGrant)
        # 철회된 frozen은 역사 owner로 남을 수 있다. 일반 geometry CP는 계속 허용한다.
        if not self._scene.scene.eligible or grant.lifecycle_state!=2:return arguments
        coordinator=self._scene;original_grant=coordinator.scene.grant_bytes;context=self._mapping._context
        def descriptor(snapshot):
            from .scene import SceneError
            owner=self._mapping
            if (self._scene is not coordinator or coordinator.scene.grant_bytes!=original_grant
                    or owner._context is not context or owner.context_port.read() is not context
                    or snapshot is not owner.collector._draft_snapshot or snapshot.job.context is not context):
                raise SceneError('original Scene/publication changed during checkpoint capture')
            owner.collector.current_tile_references(owner._pipeline,snapshot)
            if self._hazard is None or self._hazard.snapshot is None or self._hazard.snapshot.revision!=coordinator.scene._hazard:
                raise SceneError('current accepted hazard differs from Scene snapshot')
            return coordinator.scene.descendant_scene(snapshot,frame=owner.collector._last,now_j_us=self.clock())
        arguments['scene_at_snapshot']=descriptor
        return arguments

    def _hazard_checkpoint_state(self):
        """동일 compute owner의 채택 map과 보수 occupied/floor만 원 CP에 보존한다."""
        if (self._hazard is None or self._hazard.snapshot is None or self._mapping is None
                or self._mapping._pipeline is None or self._mapping._context is None):return {}
        return self._hazard.state_fields(self._mapping._pipeline,self._mapping._context,self._config.sha256)

    def _queue_scene_freeze(self,now):
        """원 Pi commit nonce의 CP 하나만 준비한다. 큰 사본/검산은 maintenance 밖으로 새지 않는다."""
        from xenova_drive_world import decode,schema
        from .scene import SceneError
        from .mapper_mapping import MappingNotReady
        from .mapper_catalogs import CatalogNotReady
        from .map_checkpoint import MapCheckpointNotReady
        from .mapper_atlas import MapperAtlasNotReady
        from ..mapping.atlas import AtlasResource
        from ..storage.contracts import StorageJob,PersistenceError
        from uuid import UUID,uuid4
        owner=self._mapping;coordinator=self._scene
        grant=decode(coordinator.scene.grant_bytes,schema.SceneGrant)
        if grant.lifecycle_state!=1 or grant.purpose or coordinator._freeze_job is not None:return
        if (self._hazard is None or self._hazard.snapshot is None or owner._pipeline is None
                or not coordinator.scene._stations or coordinator.scene._observation is None):
            self._scene_status='WAIT_SCENE_OBSERVATION';return
        if (owner.pending is not None or owner.mailbox.main_peek() is not None
                or owner.catalogs.pending is not None or owner.catalogs._queued is not None
                or not owner._head_ready and owner._checkpoint_ref is not None):return
        hazard=self._hazard.snapshot.revision
        attempt=(coordinator.scene.grant_bytes,owner._revision,hazard)
        if attempt==self._scene_freeze_attempt:return
        pending=self._scene_freeze_prequeue
        if self._scene_freeze_prequeue_fenced:
            if pending is not None and pending[0]==coordinator.scene.grant_bytes:
                self._scene_status='SCENE_FREEZE_ADMISSION_FENCED';return
            self._scene_freeze_prequeue_fenced=False
        if pending is None or pending[0]!=coordinator.scene.grant_bytes:
            trust=decode(coordinator.scene._lease.trust_bytes,schema.WorldTrust)
            expires=min(now+owner._job_lifetime,grant.survey_deadline_pi_us-trust.upper_offset_us,
                grant.valid_until_pi_us-trust.upper_offset_us,
                coordinator.scene._lease.expires_pi_us-trust.upper_offset_us)
            if expires<=now:self._scene_status='SCENE_FREEZE_EXPIRED';return
            job=StorageJob(owner._context,str(uuid4()),now,expires,str(UUID(bytes=grant.request_nonce)))
            self._scene_freeze_prequeue=(coordinator.scene.grant_bytes,attempt,job)
        else:
            _,original_attempt,job=pending
            # 미게시 작업도 원 context/revision/기한을 고정한다. 새 lease나 frame으로 재포장하지 않는다.
            if original_attempt!=attempt or job.context is not owner._context:
                self._scene_status='SCENE_FREEZE_INPUT_CHANGED';return
            if not job.current(owner._context.sha256,now):
                self._scene_status='SCENE_FREEZE_EXPIRED';return
        snapshot=request=None;described=False
        try:
            hazard_state=self._hazard_checkpoint_state()
            snapshot=owner.collector.snapshot(owner._pipeline,job,now_us=now,
                subsystem_state=dict(owner.catalogs.state_fields(),**hazard_state),include_unchanged=True)
            refs=owner.collector.current_tile_references(owner._pipeline,snapshot)
            descriptor=coordinator.freeze_scene(job,current_tile_references=refs,current_hazard_revision=hazard,now_j_us=now)
            described=True
            request=owner.queue_checkpoint(job,now_us=now,scene=descriptor,subsystem_state=hazard_state)
            # 이 원 request의 수락 뒤부터 기존 pending/terminal owner가 회수한다.
            self._scene_freeze_attempt=attempt;self._scene_freeze_prequeue=None
            archive=(None if snapshot.atlas_owner is None else
                snapshot.atlas_owner.retain_scene(snapshot.atlas_snapshot,request))
            coordinator.adopt_freeze(request,current_tile_references=refs,current_hazard_revision=hazard,
                now_j_us=self.clock(),all_tiles=snapshot.full_tiles(),mapper_state=snapshot.mapper_state_ref,
                archive=archive)
        except (SceneError,MappingNotReady,CatalogNotReady,MapCheckpointNotReady,PersistenceError,
                MapperAtlasNotReady,AtlasResource):
            if request is not None:
                owner.cancel_unoffered_checkpoint(request,now_us=self.clock())
                if described:coordinator.abort_freeze(request,now_j_us=self.clock())
            else:
                if snapshot is not None and owner.collector._draft_snapshot is snapshot:owner.collector.discard_snapshot(snapshot)
                if described:coordinator.cancel_freeze_descriptor(job,now_j_us=self.clock())
            self._scene_status='SCENE_FREEZE_NOT_READY';return
        self._scene_freeze_request=request;self._scene_status='WAIT_SCENE_COMMIT'

    def _consume_scene_terminal(self,now):
        if self._scene_freeze_request is None or self._storage_schedule is None:return
        terminal=self._storage_schedule.last_terminal
        if terminal is None or terminal.request is not self._scene_freeze_request:return
        from ..storage.contracts import CommitReceipt
        try:
            if type(terminal.result) is CommitReceipt:
                self._scene.complete_freeze(terminal.request,terminal.result,now_j_us=now)
            else:self._scene.abort_freeze(terminal.request,terminal=terminal.result,now_j_us=now)
        finally:
            if self._scene._freeze_terminal:self._scene_freeze_request=None

    def _offer_scene_summary(self,now):
        if self._publication_port is None:return
        snapshot=self._scene.summary_snapshot(now_j_us=now)
        if snapshot is None:return
        context=self._mapping_context_port.read()
        if context is None or snapshot.context_sha256!=context.sha256:return
        from hashlib import sha256
        from xenova_drive_world import decode,schema
        from .mapper_publication import PublicationReport
        summary=decode(snapshot.summary,schema.SurveySummary)
        trust=decode(snapshot.clock_lease.trust_bytes,schema.WorldTrust)
        expires=min(snapshot.valid_until_pi_us-trust.upper_offset_us,snapshot.completed_j_us+5_000_000)
        if summary.status!=3:expires=min(expires,snapshot.observed_j_interval[0]+self._config.survey.observation_max_age_us)
        if not snapshot.completed_j_us<=now<expires:return
        key=(context,snapshot.summary,snapshot.grant_sha256)
        if key==self._scene_summary_sent:return
        report=PublicationReport(context,'SURVEY_SUMMARY',snapshot.summary,snapshot.completed_j_us,expires,
            sha256(context.source_binding).hexdigest(),snapshot.grant_sha256)
        if self._publication_port.offer(report,admission_revision=self._admission_revision,
                scene_row=self._scene_row):self._scene_summary_sent=key

    def queue_mapping_checkpoint(self,job,*,now_us=None,scene=None,subsystem_state=None,scene_at_snapshot=None):
        """명시 원 job을 compute 유지 단계에서 고정한다. 호출 자체는 외부 D offer가 아니다."""
        if self._mapping is None:raise RuntimeError('actual mapping controller is not initialized')
        return self._mapping.queue_checkpoint(job,now_us=self.clock() if now_us is None else now_us,
            scene=scene,subsystem_state=subsystem_state,scene_at_snapshot=scene_at_snapshot)

    def queue_mapping_head_load(self,job):
        if self._mapping is None:raise RuntimeError('actual mapping controller is not initialized')
        return self._mapping.queue_head_load(job)

    def queue_mapping_provenance(self,request,*,now_us=None):
        if self._mapping is None:raise RuntimeError('actual mapping controller is not initialized')
        return self._mapping.queue_provenance(request,now_us=self.clock() if now_us is None else now_us)

    def _cancel_mapping_draft(self):
        if self._scene_draft is not None:self._scene.cancel(self._scene_draft);self._scene_draft=None
        if self._hazard_draft is not None:self._hazard.cancel(self._hazard_draft);self._hazard_draft=None
        draft,self._mapping_draft=self._mapping_draft,None
        if draft is not None:self._mapping.cancel(draft)

    def accept_bootstrap_source(self,complete):
        """compute가 실제 모든 LOAD를 확인한 원 자료만 보존한다. 현재 지도 승인이 아니다.

        source/clock ACK 전에도 자료는 도착할 수 있다. 새 현재 문맥과 보정이 결박된
        기하 호출에서 정적 복원을 시도하며, 정상 D 재결합이 현재 지도를 덮지 않는다.
        """
        from .map_checkpoint import SourceComplete
        if type(complete) is not SourceComplete:
            raise ValueError('actual decoded complete checkpoint source required')
        if self._bootstrap_source is not None and self._bootstrap_source.checkpoint_ref!=complete.checkpoint_ref:
            raise ValueError('original selected bootstrap checkpoint changed')
        self._durable_source=complete
        if self._accepted is not None:
            pipeline=self._accepted.pipeline
            if (pipeline.grid is not None and not pipeline.grid.frame_id.startswith('frame:')
                    and not pipeline._restore_pending):
                return
        self._bootstrap_source=complete
        if self._mapping is not None:self._mapping.adopt_source_complete(complete)

    def _restore(self,candidate,inputs):
        """정적 source adoption도 후보 core 안에서 수행한다. 취소된 호출은 채택하지 않는다."""
        source=self._bootstrap_source
        if source is None:return self._restore_attempt,None
        if getattr(candidate.pipeline,'_restored_checkpoint_ref',None)==source.checkpoint_ref:
            return self._restore_attempt,None
        context=inputs.storage_context
        if context is None:return self._restore_attempt,'WAIT_SOURCE_FOR_MAP_RESTORE'
        key=(source.checkpoint_ref,context.sha256,
             tuple((value.sensor_id,value.sensor_instance,value.sensor_generation,value.leaf_sha256) for value in inputs.tof))
        attempt=self._restore_attempt
        if attempt is not None and attempt[0]==key:
            return attempt,attempt[1]
        from .map_checkpoint import MapCheckpointNotReady,restore_pipeline
        from .scene import SceneEvidenceScope,SceneError
        try:
            leaves=self._config.sensors.tof_leaves
            sources=tuple((leaves[value.sensor_id][0],leaves[value.sensor_id][1],
                           value.sensor_instance,value.sensor_generation) for value in inputs.tof
                          if value.sensor_id in leaves and all((leaves[value.sensor_id][1].extrinsics_qualified,
                              leaves[value.sensor_id][1].first_surface_qualified,
                              leaves[value.sensor_id][1].coverage_qualified,leaves[value.sensor_id][1].time_qualified)))
            scope=SceneEvidenceScope(context,self._binding,candidate.calibration,self._config.sha256,
                tof_sources=sources,approved_tof_assets=self._config.sensors.tof_sources)
            scope=scope.with_complete_history(source,now_us=self.clock())
            restore_pipeline(candidate.pipeline,source,scope)
        except (MapCheckpointNotReady,SceneError):
            # 완전한 저장 자료라도 다른 알고리즘/보정 또는 legacy metadata이면 현재
            # 좌표 복원은 미수락이다. 빈 지도/현재 시각으로 바꾸지 않고 원 자료를 남긴다.
            issue='MAP_RESTORE_NOT_READY'
        else:issue=None
        return (key,issue),issue

    def _metric(self,item,parsed,candidate,checkpoint):
        from .resident_perception import ResidentPerception
        from .mapper_sensor_state import SensorFrameSnapshot,MetricInputs
        from ..mapping.free_schedule import stopping_region
        config=self._config;sensors=item.sensor_snapshot
        observation=candidate.prepare(item.sample,nn_candidates=parsed,timing=config.capture_timing)
        available=max(observation.rgb.stamp.available_us,
                      observation.rgb.stamp.available_us if observation.depth is None else observation.depth.stamp.available_us)
        hull=replace(observation.rgb.stamp,captured_us=observation.capture_interval,available_us=available)
        now=self.clock()
        if sensors is None:
            inputs=MetricInputs(None,None,None,None,None,(),(),(),('SENSOR_SNAPSHOT_UNAVAILABLE',))
        else:
            if type(sensors) is not SensorFrameSnapshot:raise ValueError('original frozen sensor snapshot required')
            if sensors.fence_revision!=self._admission_revision:
                from .mapper_compute import MapperCancelled
                raise MapperCancelled('ADMISSION_FENCE')
            sensors.validate_frame(job_id=item.job_id,source_generation=item.source_generation,
                source_key=item.source_key,expires_us=item.expires_us,capture_stamp=hull,
                now_us=now,context_bytes=item.context_bytes)
            inputs=sensors.metric_inputs
            if (sensors.calibration.sha256!=candidate.calibration.sha256
                    or inputs.storage_context is not None and (
                        inputs.storage_context.mapper_worker,inputs.storage_context.origin_mapper_instance)
                        !=(self._mapper_worker,self._mapper_instance)):
                raise ValueError('snapshot changed registered mapper/calibration')
            # 원 admission은 다시 만들지 않는다. 계산 시작 시 현재 lease/age만 단축 검사한다.
            reasons=list(inputs.reasons);lease=inputs.clock_lease
            if lease is not None:
                try:lease.pi_interval_to_j((lease.probe_pi_send_us,lease.probe_pi_send_us),now)
                except ValueError:lease=None
            vehicle=inputs.vehicle
            if vehicle is not None and not sensors.sensors.current_vehicle(vehicle,now):
                vehicle=None;reasons.append('VEHICLE_UNAVAILABLE')
            tofs=tuple(value for value in inputs.tof if sensors.sensors.current_tof(value,now))
            if lease is None:
                inputs=replace(inputs,clock_lease=None,pose=None,vehicle=None,tof=(),
                    reasons=tuple(dict.fromkeys((*reasons,'PI_J_CLOCK_UNAVAILABLE'))))
            else:inputs=replace(inputs,vehicle=vehicle,tof=tofs,reasons=tuple(dict.fromkeys(reasons)))
        # main history는 frame 취소/채택과 무관하다. 이 참조는 Scene 유지의 원 조회일 뿐이다.
        self._latest_source_inputs=None if sensors is None else inputs
        restore_attempt,restore_issue=self._restore(candidate,inputs)
        checkpoint()
        diagnostic=self._diagnostic;mapping_enabled=self._mapping_context_port is not None
        adopt_metric=True;mapping_reason='DISABLED'
        if mapping_enabled:
            mapping_reason='WAIT_MAINTENANCE' if self._mapping is None else 'METRIC_INPUT_NOT_READY'
            adopt_metric=False
            if self._mapping is not None and inputs.pose is not None and restore_issue is None:
                self._mapping_draft=self._mapping.prepare_frame(item,candidate.pipeline,observation,inputs,sensors,self.clock())
                adopt_metric=self._mapping_draft.ready;mapping_reason=self._mapping_draft.reason
                if adopt_metric and self._durable_source is not None and config.survey is not None:
                    from .mapper_hazard import MapperHazard
                    if self._hazard is None:self._hazard=MapperHazard(complete=self._durable_source)
                    self._hazard.restore_complete(self._durable_source,pipeline=candidate.pipeline,
                        scope=self._mapping_draft.frame.scope,now_us=self.clock())
                    if self._scene_restore_archive is None and self._mapping._atlas is not None:
                        from .mapper_atlas import MapperAtlasNotReady
                        from ..mapping.atlas import AtlasResource
                        try:self._scene_restore_archive=self._mapping._atlas.archive_from_complete(self._durable_source,
                            current_scope=self._mapping_draft.frame.scope,now_us=self.clock())
                        except (MapperAtlasNotReady,AtlasResource,ValueError):pass
                if not adopt_metric:self._cancel_mapping_draft()
        diagnostic_only=inputs.pose is None or restore_issue is not None or mapping_enabled and not adopt_metric
        if diagnostic_only:
            adopt_metric=False
            # 일시적인 clock/pose 부재가 이미 채택한 odom 지도를 frame-local 지도로
            # 교체하지 않게 한다. 진단 코어는 별도로 원 age와 HWM을 보존한다.
            diagnostic=(ResidentPerception(self._profile_bytes,self._binding,pipeline_profile=config.pipeline,targets=())
                        if diagnostic is None else deepcopy(diagnostic))
            result=diagnostic.process(observation,now_us=self.clock(),checkpoint=checkpoint,completed_clock=self.clock)
        else:
            def needed(grid,pose,at_us):
                if inputs.vehicle is None or pose is None or not sensors.sensors.current_vehicle(inputs.vehicle,self.clock()):return None
                limits=config.stopping
                return stopping_region(grid.spec,inputs.vehicle,pose,candidate.calibration,
                    admission=sensors.sensors,frame_id=grid.frame_id,now_us=at_us,
                    max_speed_mps=limits.max_speed_mps,min_braking_mps2=limits.min_braking_mps2,
                    reaction_latency_us=limits.reaction_latency_us,cycle_bound_us=limits.cycle_bound_us,
                    completion_bound_us=limits.completion_bound_us,body_error_m=limits.body_error_m)
            localization={} if config.localization is None else dict(
                localization_valid_for_us=config.localization.registration_valid_for_us,
                localization_position_drift_mps=config.localization.position_drift_mps,
                localization_yaw_drift_rps=config.localization.yaw_drift_rps)
            result=candidate.process(observation,now_us=self.clock(),pose=inputs.pose,
                checkpoint=checkpoint,completed_clock=self.clock,
                motion_qualified=config.motion_qualified and config.localization_configured,
                needed_region_factory=needed,cycle_bound_us=config.stopping.cycle_bound_us,
                completion_bound_us=config.stopping.completion_bound_us,
                **({} if self._mapping_draft is None else dict(evidence_observer=self._mapping_draft.observer,
                    roi_reframe=self._mapping_draft.roi_reframe)),**localization)
            if self._mapping_draft is not None:
                self._mapping_draft=self._mapping.finish_frame(self._mapping_draft,result,now_us=self.clock())
                adopt_metric=self._mapping_draft.ready;mapping_reason=self._mapping_draft.reason
                if not adopt_metric:self._cancel_mapping_draft()
        summary=dict(result.summary,mapper_profile_sha256=config.sha256,
            metric_input_reasons=list(inputs.reasons),source_vehicle_sequence=None if inputs.vehicle is None else inputs.vehicle.anchor.sequence,
            source_binding_revision=None if inputs.storage_context is None else inputs.storage_context.fields['source_binding']['source_binding_revision'],
            source_deliveries=[list(row) for row in inputs.delivery_metadata],
            tof_inputs=len(inputs.tof),sensor_resets=len(inputs.sensor_resets),
            bootstrap_source_complete=self._durable_source is not None,map_restore_issue=restore_issue,
            localization_configured=config.localization_configured,
            preserved_metric_map=(diagnostic_only or mapping_enabled and not adopt_metric) and self._accepted.pipeline.grid is not None)
        if mapping_enabled:summary.update(mapping_status=self._mapping_status,mapping_ready=adopt_metric,mapping_reason=mapping_reason,
            storage_schedule='DISABLED' if self._storage_schedule is None else self._storage_schedule.status)
        return summary,sensors,diagnostic,restore_attempt,adopt_metric

    def _world_report(self,sensors,frame_state=None):
        """원 실제 기하 후보에서만 작성한다. hash/encode/시각 읽기는 최종 채택 lock 밖이다."""
        if self._publication_port is None:return None,'DISABLED'
        if self._frame_sequence>=2**63-1:return None,'SEQUENCE_EXHAUSTED'
        draft=self._mapping_draft
        if draft is None or not draft.ready or draft.catalog_draft is None:return None,'MAPPING_NOT_READY'
        if draft.result.summary.get('rgb_updated') is not True:return None,'NO_NEW_RGB'
        inputs=draft.inputs;lease=inputs.clock_lease
        if lease is None or sensors is None or sensors.sensors is None:return None,'CLOCK_OR_SENSOR_NOT_READY'
        from hashlib import sha256
        from xenova_drive_world import decode,schema
        from .mapper_world import build_mapping_world
        from .mapper_publication import PublicationReport
        state=draft.catalog_draft.state;basis=state.view.road_basis
        completed=self.clock();published=self.clock()
        built=build_mapping_world(draft,state,basis,context=draft.context,source=inputs.source,
            calibration=draft.pipeline.calibration,sensors=sensors.sensors,current_vehicle=inputs.vehicle,
            clock_lease=lease,sequence=self._frame_sequence+1,completed_j_us=completed,published_j_us=published,
            scene_draft=None if frame_state is None else frame_state.scene,
            hazard_draft=None if frame_state is None else frame_state.hazard,atlas_owner=self._mapping._atlas)
        if built is None:return None,'WORLD_NOT_READY'
        alignment=draft.pipeline._alignment
        trust=decode(lease.trust_bytes,schema.WorldTrust)
        expires=min(draft.original_job.expires_us,alignment.observed_j_interval[0]+alignment.valid_for_us,
            lease.expires_pi_us-trust.upper_offset_us)
        if frame_state is not None and frame_state.scene is not None:
            expires=min(expires,frame_state.scene.expires_j_us)
        if not published<expires<2**63:return None,'WORLD_EXPIRED'
        try:
            report=PublicationReport(draft.context,'WORLD_CANDIDATE',built.payload,published,expires,
                sha256(draft.context.source_binding).hexdigest())
        except ValueError:return None,'WORLD_REPORT_NOT_READY'
        return report,'READY'

    def _prepare_frame_state(self,sensors):
        """원 frame의 후보 상태다. negative hazard는 채택 취소로 철회되지 않는다."""
        draft=self._mapping_draft
        scene_row=None if self._mapping_context_port is None else self._mapping_context_port.read_scene()
        if draft is None or not draft.ready:return _FrameCandidate(self._frame_sequence,scene_row=scene_row)
        updated=draft.result.summary.get('rgb_updated') is True
        sequence=self._frame_sequence+int(updated)
        if sequence>=2**63:raise OverflowError('original observation sequence exhausted')
        if self._config.survey is None:return _FrameCandidate(sequence,scene_row=scene_row)
        from .mapper_hazard import MapperHazard
        from .scene import SceneError
        if self._hazard is None:self._hazard=MapperHazard(complete=self._durable_source)
        now=self.clock();hazard=self._hazard.prepare(draft,now_us=now);self._hazard_draft=hazard
        if self._scene is None:return _FrameCandidate(sequence,hazard=hazard,scene_row=scene_row)
        try:
            if not hazard.ready:
                self._scene.revoke('RESOURCE',now_j_us=now);self._scene_status=hazard.reason
            else:
                self._scene.hazard(hazard.revision,now_j_us=now)
                self._scene_draft=self._scene.prepare_frame(draft,draft.result,sensors,
                    world_sequence=sequence,hazard_revision=hazard.revision,now_j_us=now,
                    restore_source=self._durable_source,restore_archive=self._scene_restore_archive)
                self._scene_status=self._scene_draft.reason
        except SceneError:
            self._scene.revoke('ALIGNMENT',now_j_us=now);self._scene_status='SCENE_FRAME_NOT_READY'
        return _FrameCandidate(sequence,self._scene_draft,hazard,self._scene_row)

    def parse(self, item, checkpoint):
        from ..perception.yolop import decode_yolop
        from ..perception.decoders import decode_depth, decode_rgb
        checkpoint()
        sample=item.sample
        candidates=decode_yolop(sample.nn_output)
        if self._accepted is None:
            rgb,depth=decode_rgb(sample.rgb),decode_depth(sample.depth)
            rgb=depth=None
        checkpoint()
        return candidates

    def geometry(self, item, parsed, checkpoint):
        checkpoint()
        if self._staged is not None:
            raise RuntimeError('previous geometry candidate has no terminal decision')
        if self._accepted is None:
            return None
        self._retired=None
        try:
            # profile의 유한 RGB/graph/grid/cache만 복사한다. mailbox/Lock/controller는 복사하지 않는다.
            candidate=deepcopy(self._accepted)
            checkpoint()
            sensors=None;diagnostic=self._diagnostic;restore_attempt=self._restore_attempt;adopt_metric=True
            if self._config is None:
                summary=candidate.consume(item.sample,now_us=self.clock(),nn_candidates=parsed,
                    checkpoint=checkpoint,completed_clock=self.clock)
            else:
                summary,sensors,diagnostic,restore_attempt,adopt_metric=self._metric(item,parsed,candidate,checkpoint)
            frame_state=self._prepare_frame_state(sensors)
            report=None
            if self._publication_port is not None:
                report,world_reason=self._world_report(sensors,frame_state)
                summary=dict(summary,world_candidate_ready=report is not None,world_candidate_reason=world_reason)
            if self._config is not None and self._config.survey is not None:
                summary=dict(summary,scene_status=self._scene_status,
                    hazard_revision=None if frame_state.hazard is None else frame_state.hazard.revision)
            checkpoint()
            processed=(candidate if adopt_metric else self._accepted).processed+(0 if diagnostic is None else diagnostic.processed)
            payload=json.dumps(dict(processed=processed,latest=summary),sort_keys=True,
                ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')
            from .mapper_compute import MAX_RESULT_BYTES
            if len(payload)>MAX_RESULT_BYTES:
                raise ValueError('mapper summary exceeds immutable result budget')
            checkpoint()
            # 최종 참조 교환 때의 큰 dict/array 해제도 다음 compute 유지 단계로 미룬다.
            retiring=(self._accepted,None,self._diagnostic,
                None if self._mapping is None else self._mapping.collector,
                None if self._mapping is None or self._mapping.catalogs is None else self._mapping.catalogs._accepted,
                None if self._scene is None else self._scene.scene,
                None if self._hazard is None else self._hazard.snapshot)
            self._staged=(item.job_id,candidate,sensors,diagnostic,restore_attempt,self._mapping_draft,adopt_metric,retiring,report,frame_state)
            return payload
        except BaseException:
            self._cancel_mapping_draft();self._staged=None
            raise

    def accepted_at(self,item,checked_j_us):
        """compute의 원 마지막 검사 시각을 받는다. 시계를 다시 읽거나 기한을 발급하지 않는다."""
        if type(checked_j_us) is not int or not 0<=checked_j_us<2**64:
            raise ValueError('original final checked J microseconds required')
        self.accepted(item,checked_j_us=checked_j_us)

    def accepted(self, item,*,checked_j_us=None):
        """최종 원 job/current/expiry 검사가 끝난 compute owner가 참조만 교체한다."""
        if self._accepted is None:
            return
        if self._staged is None or self._staged[0]!=item.job_id:
            raise RuntimeError('accepted geometry differs from the original staged job')
        staged=self._staged
        if staged[5] is not None and self._mapping_context_port.read() is not staged[5].context:
            from .mapper_compute import MapperCancelled
            raise MapperCancelled('PUBLICATION_CHANGED')
        state=staged[9]
        if state.scene is not None:
            from .mapper_compute import MapperCancelled
            scene=state.scene
            if (type(checked_j_us) is not int or scene is not self._scene._draft
                    or scene.version!=self._scene._version
                    or not scene.prepared_j_us<=checked_j_us<scene.expires_j_us):
                raise MapperCancelled('SCENE_FENCED_OR_EXPIRED')
            if self._mapping_context_port.read_scene() is not state.scene_row:
                raise MapperCancelled('SCENE_INPUT_CHANGED')
        if state.hazard is not None and state.hazard is not self._hazard._staged:
            from .mapper_compute import MapperCancelled
            raise MapperCancelled('HAZARD_FENCED')
        if staged[5] is not None:self._mapping.accept(staged[5])
        if state.hazard is not None:self._hazard.accept(state.hazard)
        if state.scene is not None:self._scene.accept(state.scene,now_j_us=checked_j_us)
        self._frame_sequence=state.sequence
        if staged[8] is not None and self._publication_port.offer(staged[8],admission_revision=self._admission_revision,
                scene_row=state.scene_row):self._world_sequence=state.sequence
        self._retired=staged
        if staged[6]:self._accepted=staged[1]
        self._diagnostic=staged[3]
        if staged[6]:self._restore_attempt=staged[4]
        self._staged=self._mapping_draft=self._scene_draft=self._hazard_draft=None

    def cancelled(self, item, reason):
        """원 후보의 실패/만료는 이미 채택한 지도나 source 시각을 갱신하지 않는다."""
        self._cancel_mapping_draft();self._staged=None
