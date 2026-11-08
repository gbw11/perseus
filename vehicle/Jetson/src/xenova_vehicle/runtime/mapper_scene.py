"""실제 compute 후보와 Scene 조사 상태를 연결한다. 이동/scene 사용권은 발급하지 않는다.

원 RGB gate와 Vehicle admission의 참조를 보존한다. prepare는 외부 D offer를 하지
않고 accept만 Scene 참조를 교환한다. CP는 Mapping owner가 한 번 만든 원 요청을
adopt하며 SQLite, IPC, fsync, 새 thread/process를 소유하지 않는다.
"""

from copy import copy
from dataclasses import dataclass,replace
from hashlib import sha256
import math
from threading import get_ident

from .scene import MapperScene,SurveyProfile,SurveyObservation,SceneEvidenceScope,SceneError,_codec
from .mapper_sensor_state import MapperSensorState
from ..mapping.alignment import MapOdomAlignment
from ..mapping.odometry import PoseEstimate
from ..mapping.registration import FrozenReference
from ..perception.pipeline import PerceptionResult
from ..storage.contracts import StorageContext,StorageJob,CommitReceipt,canonical,uint
from ..storage.port import CheckpointRequest


@dataclass(frozen=True,init=False)
class SceneStationWitness:
    """원 등록 Vehicle 창과 별도 MAP capture다. 새 정합을 과거 창에 적용하지 않는다.

    원 wire/admission/clock은 originals에 참조로 보존하며 다시 latest로 수락하지
    않는다. descriptor의 창 digest는 이 원본을 대체하는 내구 복원 증명이 아니다.
    """
    sensor_state: object
    originals: tuple
    raw_pose: PoseEstimate
    map_pose: PoseEstimate
    observation: SurveyObservation
    context: StorageContext
    _window: bytes

    @classmethod
    def from_frame(cls,sensors,observation,context,profile,*,now_j_us):
        if (type(sensors) is not MapperSensorState or type(observation) is not SurveyObservation
                or type(context) is not StorageContext or type(profile) is not SurveyProfile):
            raise SceneError('actual SensorState/capture/context/profile required')
        bracket=sensors.original_pose_bracket(observation.raw_pose)
        if bracket.pose is not observation.raw_pose:raise SceneError('original PoseBuffer capture was replaced')
        capture=observation.raw_pose.captured_us;first_limit=capture[0]-profile.station_window_us
        if first_limit<0:raise SceneError('whole stationary window has not elapsed')
        rows=tuple(sensors._vehicle_admissions.values())
        if len(rows)>256:raise SceneError('original Vehicle window exceeds capacity')
        matching=sorted((row for row in rows if row.evidence.pose_sample.context==observation.raw_pose.sample.context
            and row.evidence.pose_sample.at_us<=bracket.pose.bracket_us[1]),key=lambda row:row.evidence.pose_sample.at_us)
        left=next((index for index in range(len(matching)-1,-1,-1)
            if matching[index].evidence.pose_sample.at_us<=first_limit),None)
        if left is None:raise SceneError('registered Vehicle window does not cover original stationary interval')
        originals=tuple(matching[left:])
        result=object.__new__(cls)
        for name,value in dict(sensor_state=sensors,originals=originals,raw_pose=observation.raw_pose,
                map_pose=observation.pose,observation=observation,context=context,_window=b'').items():
            object.__setattr__(result,name,value)
        window=result._verify(observation,context,profile,now_j_us)
        object.__setattr__(result,'_window',canonical(window))
        return result

    @property
    def window_fields(self):
        from ..storage.contracts import json_bytes
        return json_bytes(self._window,4096)

    def validate(self,observation,context,profile,now_j_us):
        if self._window!=canonical(self._verify(observation,context,profile,now_j_us)):
            raise SceneError('original stationary window changed')

    def _verify(self,observation,context,profile,now):
        uint(now)
        if (observation is not self.observation or context!=self.context
                or self.raw_pose is not observation.raw_pose or self.map_pose is not observation.pose
                or not 2<=len(self.originals)<=256):
            raise SceneError('station witness differs from the exact accepted observation')
        sensors=self.sensor_state;raw=self.raw_pose;sample=raw.sample;capture=raw.captured_us
        bracket=sensors.original_pose_bracket(raw)
        if any(not any(row is endpoint for row in self.originals) for endpoint in bracket.originals):
            raise SceneError('original capture bracket is missing from stationary window')
        poses=[];digests=[];origin=context.geometry_origin_fields
        if origin is None:raise SceneError('actual original Pi origin required')
        for row in self.originals:
            evidence=sensors.validate_vehicle_admission(row);pose=evidence.pose_sample
            if (not row.received_j_us<=row.admitted_j_us<=now or pose.context!=sample.context
                    or evidence.clock_lease.source!=sensors.sensors.context.source
                    or evidence.clock_lease.pi_host_boot!=context.pi_host_boot
                    or evidence.clock_lease.pi_interval_to_j(evidence.pi_interval,row.admitted_j_us)!=evidence.j_interval
                    or not evidence.j_interval[0]<=pose.at_us<=evidence.j_interval[1]):
                raise SceneError('original Vehicle clock/admission/context differs')
            wire=evidence.message()
            if ((wire.vehicle,wire.host_boot,wire.gateway_instance,wire.device_session,wire.pi_session,wire.odom_epoch,wire.calibration)
                    !=tuple(origin[name] for name in ('vehicle','pi_host_boot','gateway_instance','device_session',
                        'pi_session','odom_epoch','calibration'))):
                raise SceneError('station source differs from original admitted Pi geometry origin')
            poses.append(pose);digests.append(evidence.wire_sha256)
        if (poses[0].at_us>capture[0]-profile.station_window_us or poses[-1].at_us<capture[1]
                or (poses[0].at_us,poses[-1].at_us)!=(self.originals[0].evidence.pose_sample.at_us,raw.bracket_us[1])):
            raise SceneError('station window does not contain full original capture')
        gaps=[b.at_us-a.at_us for a,b in zip(poses,poses[1:])]
        if any(not 0<gap<=profile.max_pose_gap_us for gap in gaps):raise SceneError('station Vehicle gap/order')
        # 양 끝이 정지해도 중간 운동은 가능하다. 설치의 실제 속도 상한으로 빈 구간을 덮는다.
        linear=max(gaps)*sensors.pose_args['max_speed_mps']/2_000_000
        angular=max(gaps)*sensors.pose_args['max_yaw_rate_rps']/2_000_000
        if any(math.hypot(p.x-sample.x,p.y-sample.y)+p.position_error_m+sample.position_error_m+linear>profile.station_motion_m
                or abs(math.remainder(p.yaw-sample.yaw,2*math.pi))+p.yaw_error_rad+sample.yaw_error_rad+angular>profile.station_yaw_rad
                for p in poses):raise SceneError('station continuous motion/error bound is too large')
        mapped=self.map_pose.sample
        return dict(started_us=poses[0].at_us,completed_us=poses[-1].at_us,sample_count=len(poses),
            coordinate='RAW_ODOM_WINDOW_WITH_MAP_CAPTURE_V1',capture_us=capture,
            source_window_sha256=sha256(canonical(digests)).hexdigest(),
            x_bounds=(min(p.x-p.position_error_m for p in poses)-linear,max(p.x+p.position_error_m for p in poses)+linear),
            y_bounds=(min(p.y-p.position_error_m for p in poses)-linear,max(p.y+p.position_error_m for p in poses)+linear),
            yaw_about_last=(min(math.remainder(p.yaw-sample.yaw,2*math.pi)-p.yaw_error_rad for p in poses)-angular,
                max(math.remainder(p.yaw-sample.yaw,2*math.pi)+p.yaw_error_rad for p in poses)+angular),
            raw_capture=(sample.at_us,sample.x,sample.y,sample.yaw,sample.position_error_m,sample.yaw_error_rad),
            map_capture=(mapped.at_us,mapped.x,mapped.y,mapped.yaw,mapped.position_error_m,mapped.yaw_error_rad))


@dataclass(frozen=True)
class SceneDraft:
    original_mapping: object
    original_result: PerceptionResult
    scene: MapperScene
    version: int
    observed: bool
    station: SceneStationWitness | None
    reason: str
    expires_j_us: int
    prepared_j_us: int


@dataclass(frozen=True)
class SceneSummarySnapshot:
    """원 요약/부모/유효기한이다. 저장/encode 성공은 Pi scene 사용권이 아니다."""
    summary: bytes
    grant: bytes
    grant_sha256: str
    context_sha256: str
    clock_lease: object
    completed_j_us: int
    valid_until_pi_us: int
    observed_j_interval: tuple
    eligible: bool


@dataclass(frozen=True)
class SceneFreezeTerminal:
    """직전 원 작업의 참조다. UNKNOWN이나 새 작업의 성공 영수증으로 재포장하지 않는다."""
    request: object
    receipt: CommitReceipt | None
    completed_j_us: int
    accepted: bool
    abort: object = None


class SceneCoordinator:
    """단일 compute owner의 tentative 조사와 원 CP terminal을 보존한다."""
    def __init__(self,context,source,profile,*,gateway_instance,evidence_scope):
        if type(evidence_scope) is not SceneEvidenceScope:raise SceneError('explicit approved scene scope required')
        self.scene=MapperScene(context,source,profile,gateway_instance=gateway_instance,evidence_scope=evidence_scope)
        self._thread=get_ident();self._version=0;self._draft=None;self._freeze_job=None;self._descriptor=None
        self._freeze_request=None;self._freeze_terminal=False
        self._last_terminal=None;self.operation_wait_reason=None

    @property
    def last_terminal(self):return self._last_terminal

    def _owner(self):
        if get_ident()!=self._thread:raise RuntimeError('SceneCoordinator belongs to the original compute owner')

    def maintenance(self,grant_raw,clock_lease,*,now_j_us):
        """수신한 원 grant를 소비한다. 반복/정상 clock은 관측/CP job을 다시 발급하지 않는다."""
        self._owner();uint(now_j_us)
        if type(grant_raw) is not bytes or not 0<len(grant_raw)<=1024:raise SceneError('bounded original SceneGrant bytes required')
        before=self.scene.grant_bytes;candidate=copy(self.scene)
        accepted=candidate.receive_grant(grant_raw,clock_lease,now_j_us=now_j_us)
        changed=candidate.grant_bytes!=before
        s,_,decode=_codec()
        previous=None if before is None else decode(before,s.SceneGrant)
        current=None if candidate.grant_bytes is None else decode(candidate.grant_bytes,s.SceneGrant)
        new_operation=(changed and previous is not None and current is not None
            and (current.scene.admission_id!=previous.scene.admission_id or current.request_nonce!=previous.request_nonce))
        if new_operation and self._freeze_job is not None:
            if not self._freeze_terminal:
                self.operation_wait_reason='WAIT_ORIGINAL_FREEZE_TERMINAL';return False
            # 새 Pi 명령이 실제 수락된 경우에만 슬롯을 넘긴다. 반복 grant/clock은 이 경로가 아니다.
            if accepted:
                self._freeze_job=self._descriptor=self._freeze_request=None;self._freeze_terminal=False
        self.scene=candidate;self.operation_wait_reason=None
        if changed:self._version+=1
        return accepted

    def rebind_scope(self,scope):
        """실제 scene-only ACK 이후의 문맥만 연결한다. 원 관측/CP/context bytes는 불변이다."""
        from ..storage.geometry_scope import same_catalog_geometry
        self._owner();old=self.scene.evidence_scope
        if (type(scope) is not SceneEvidenceScope or not same_catalog_geometry(self.scene.context,scope.context,'SLOT')
                or scope.raw_binding!=old.raw_binding or scope.calibration.sha256!=old.calibration.sha256
                or scope.method_manifest_sha256!=old.method_manifest_sha256):
            raise SceneError('new scene scope changed actual geometry/source/approved assets')
        if self.scene._frozen is not None and self.scene._frozen.receipt is None:
            raise SceneError('original pending freeze must reach terminal before publication rebind')
        if scope.context!=self.scene.context or scope is not old:
            self.scene.context=scope.context;self.scene.evidence_scope=scope;self._version+=1

    def prepare_frame(self,mapping,result,sensors,*,world_sequence,hazard_revision,now_j_us,
                      restore_source=None,restore_archive=None):
        """실제 finalized Mapping draft의 새 RGB만 소비한다. D offer는 하지 않는다."""
        from .mapper_mapping import MappingFrameDraft
        self._owner();uint(now_j_us)
        if (self._draft is not None or type(mapping) is not MappingFrameDraft or type(result) is not PerceptionResult
                or mapping.result is not result or type(sensors) is not MapperSensorState):
            raise SceneError('one actual finalized Mapping draft/result/SensorState required')
        candidate=copy(self.scene);observed=False;station=None;reason='NEW_RGB_NOT_READY'
        if mapping.ready and mapping.context==candidate.context and mapping.frame is not None:
            candidate.renew_clock(mapping.frame.clock_lease,now_j_us=now_j_us)
            capture=mapping.frame.observation;raw=mapping.inputs.pose;pipeline=mapping.pipeline
            summary=result.summary;alignment=pipeline._alignment;reference=pipeline.reference;fit=result.registration
            scope=mapping.frame.scope;approved=candidate.evidence_scope
            if (scope.context!=candidate.context or scope.raw_binding!=approved.raw_binding
                    or scope.calibration.sha256!=approved.calibration.sha256
                    or scope.method_manifest_sha256!=approved.method_manifest_sha256):
                raise SceneError('actual frame evidence changed approved current scope')
            candidate.evidence_scope=scope
            if (summary.get('rgb_updated') is True and summary.get('quarantined') is not True
                    and tuple(summary.get('rgb_source_key',()))==capture.rgb.stamp.key
                    and type(raw) is PoseEstimate and type(alignment) is MapOdomAlignment
                    and type(reference) is FrozenReference and fit is not None and fit.status=='BOUNDED'
                    and fit.representative is not None):
                if (reference.calibration_id!=candidate.evidence_scope.calibration.sha256
                        or reference.revision!=alignment.registration_revision
                        or raw.captured_us!=capture.capture_interval
                        or summary.get('alignment_metadata')!=alignment.fields()
                        or summary.get('reference_revision')!=reference.revision):
                    raise SceneError('accepted map reference/alignment/full capture changed')
                fitted=PoseEstimate(replace(raw.sample,x=fit.representative[0],y=fit.representative[1],yaw=fit.representative[2],
                    position_error_m=fit.position_error_m,yaw_error_rad=fit.yaw_error_rad),raw.captured_us,raw.bracket_us)
                if summary.get('mapped_pose')!=[fitted.sample.x,fitted.sample.y,fitted.sample.yaw]:
                    raise SceneError('actual mapped capture pose differs')
                observation=SurveyObservation(capture.rgb.stamp,fitted,fit,world_sequence,hazard_revision,capture.kind,raw,alignment)
                observed=candidate.observe(observation,now_j_us=now_j_us)
                reason='OBSERVED' if observed else 'DUPLICATE_RGB'
                if observed and candidate._now(now_j_us)[0].lifecycle_state in (0,1):
                    try:
                        station=SceneStationWitness.from_frame(sensors,observation,candidate.context,candidate.profile,now_j_us=now_j_us)
                        candidate.station(station,now_j_us=now_j_us)
                    except ValueError:station=None;reason='OBSERVED_STATION_NOT_READY'
        # decode/시계 환산은 compute prepare에서 끝낸다. 최종 lock은 이 원 절대기한만 본다.
        grant,trust=candidate._now(now_j_us)
        if (restore_source is not None and observed and station is not None
                and grant.purpose==1 and grant.lifecycle_state==1 and candidate._frozen is None):
            try:candidate.restore_complete(restore_source,observation,now_j_us=now_j_us,archive=restore_archive)
            except ValueError:reason='RESTORE_NOT_READY'
            else:reason='RESTORE_READY'
        expires=min(mapping.original_job.expires_us,candidate._lease.expires_pi_us-trust.upper_offset_us,
            grant.valid_until_pi_us-trust.upper_offset_us)
        if grant.lifecycle_state in (0,1):
            expires=min(expires,grant.survey_deadline_pi_us-trust.upper_offset_us)
        if candidate._observation is not None:
            original=candidate._observation
            expires=min(expires,original.raw_pose.captured_us[0]+candidate.profile.observation_max_age_us,
                original.accepted_alignment.observed_j_interval[0]+original.accepted_alignment.valid_for_us)
        self._draft=SceneDraft(mapping,result,candidate,self._version,observed,station,reason,expires,now_j_us)
        return self._draft

    def accept(self,draft,*,now_j_us):
        """호출자는 Mapping/current publication 최종검사를 먼저 한다. 채택은 참조 교환이다."""
        self._owner()
        if (draft is not self._draft or draft.version!=self._version or type(now_j_us) is not int
                or not draft.prepared_j_us<=now_j_us<draft.expires_j_us):raise SceneError('original Scene draft was fenced/expired')
        draft.scene._lease=self.scene._lease
        self.scene=draft.scene;self._draft=None;self._version+=1

    def cancel(self,draft):
        self._owner()
        if draft is not self._draft:raise SceneError('original Scene draft required')
        self._draft=None

    def freeze_scene(self,job,*,current_tile_references,current_hazard_revision,now_j_us):
        self._owner()
        if self._draft is not None or self._freeze_job is not None:raise SceneError('one original freeze job only; terminal is not automatic retry')
        descriptor=self.scene.freeze_scene(job,current_tile_references=current_tile_references,
            current_hazard_revision=current_hazard_revision,now_j_us=now_j_us)
        self._freeze_job=job;self._descriptor=canonical(descriptor)
        return descriptor

    def cancel_freeze_descriptor(self,job,*,now_j_us):
        """최종 CP 생성 전의 원 준비 실패다. D 작업 완료나 UNKNOWN 수습을 뜻하지 않는다."""
        from .mapper_storage import StorageAbort
        self._owner();uint(now_j_us)
        if job is not self._freeze_job or self._freeze_request is not None or self._freeze_terminal:
            raise SceneError('exact original unpublished freeze descriptor required')
        self._freeze_terminal=True
        self._last_terminal=SceneFreezeTerminal(job,None,now_j_us,False,StorageAbort('CANCELLED'))
        self.scene.revoke('STORAGE',now_j_us=now_j_us)

    def adopt_freeze(self,request,*,current_tile_references,current_hazard_revision,now_j_us,
                     all_tiles=None,mapper_state=None,archive=None):
        self._owner()
        if (type(request) is not CheckpointRequest or request.job is not self._freeze_job
                or self._freeze_request is not None or self._freeze_terminal):raise SceneError('same original queued freeze request required')
        descriptor=self.scene.freeze_scene(request.job,current_tile_references=current_tile_references,
            current_hazard_revision=current_hazard_revision,now_j_us=now_j_us)
        if canonical(descriptor)!=self._descriptor:raise SceneError('original freeze descriptor changed before adoption')
        self.scene.adopt_freeze(request,current_tile_references=current_tile_references,
            current_hazard_revision=current_hazard_revision,now_j_us=now_j_us,
            all_tiles=all_tiles,mapper_state=mapper_state,archive=archive)
        self._freeze_request=request;return request

    def complete_freeze(self,request,receipt,*,now_j_us):
        self._owner()
        if self._freeze_terminal or request is not self._freeze_request:return False
        uint(now_j_us)
        if (type(receipt) is not CommitReceipt or (receipt.database_uuid,receipt.job_id,receipt.job_digest,receipt.origin_context_sha256)
                !=(request.job.context.database_uuid,request.job.job_id,request.digest,request.job.context.sha256)
                or receipt.committed_j_us is not None and receipt.committed_j_us>now_j_us):return False
        if receipt.status=='UNKNOWN':
            self.operation_wait_reason='WAIT_ORIGINAL_FREEZE_TERMINAL';return False
        try:accepted=self.scene.complete_freeze(request,receipt,now_j_us=now_j_us)
        except SceneError:
            self._freeze_terminal=True;self._last_terminal=SceneFreezeTerminal(request,receipt,now_j_us,False)
            raise
        self._freeze_terminal=True;self._last_terminal=SceneFreezeTerminal(request,receipt,now_j_us,accepted)
        self.operation_wait_reason=None
        if not accepted:self.scene.revoke('STORAGE',now_j_us=now_j_us)
        return accepted

    def abort_freeze(self,request,*,now_j_us,terminal=None):
        """미게시 실패 또는 원 owner가 전달한 typed terminal만 소비한다.

        adopted 요청에는 실제 channel admission을 마친 StorageAbort/DbAbort가 필요하다.
        이 함수는 native 정리를 실행하거나 증명하지 않는다. UNKNOWN/시간 경과만으로
        정리를 추측하지 않으며, notice 관측시각을 현재 callback 시각으로 바꾸지 않는다.
        """
        self._owner()
        if (type(request) is not CheckpointRequest or self._freeze_terminal
                or request.job is not self._freeze_job
                or self._freeze_request is not None and request is not self._freeze_request):return False
        uint(now_j_us);event_us=now_j_us
        if terminal is not None:
            from .mapper_storage import StorageAbort
            from .resident_db import DbAbort
            from .storage_messages import prepare_payload
            if type(terminal) is not StorageAbort or type(terminal.original_notice) is not DbAbort:return False
            notice=terminal.original_notice;job=request.job
            if (notice.operation!='CHECKPOINT' or notice.request_id!=job.job_id
                    or (notice.issued_us,notice.expires_us)!=(job.issued_us,job.expires_us)
                    or notice.mapper.worker_instance!=job.context.mapper_worker
                    or notice.origin_context_sha256!=job.context.sha256
                    or notice.body_bytes!=canonical(prepare_payload(request)['body'])
                    or not job.issued_us<=notice.observed_us<=now_j_us):return False
            event_us=notice.observed_us
        elif self._freeze_request is not None:return False
        self._freeze_request=request;self._freeze_terminal=True
        self._last_terminal=SceneFreezeTerminal(request,None,event_us,False,terminal);self.operation_wait_reason=None
        self.scene.revoke('STORAGE',now_j_us=event_us);return True

    def revoke(self,reason,*,now_j_us):
        self._owner();self.scene.revoke(reason,now_j_us=now_j_us);self._version+=1

    def hazard(self,revision,*,now_j_us):
        self._owner();before=self.scene._hazard
        self.scene.hazard(revision,now_j_us=now_j_us)
        if before!=revision:self._version+=1

    def summary_snapshot(self,*,now_j_us):
        self._owner()
        raw=self.scene.summary_bytes(now_j_us=now_j_us)
        if raw is None:return None
        s,_,decode=_codec();summary=decode(raw,s.SurveySummary);grant,trust=self.scene._now(now_j_us)
        if not 0<len(raw)<=288:raise SceneError('bounded original SurveySummary required')
        # 음성 사건의 completed만 새로울 수 있다. positive age는 원 관측 lower로 제한한다.
        observed=(summary.last_observed_peer_us-summary.timestamp_error_us,
            summary.last_observed_peer_us+summary.timestamp_error_us)
        if (summary.status!=3 and (now_j_us-observed[0]>self.scene.profile.observation_max_age_us
                or now_j_us+trust.upper_offset_us>=grant.survey_deadline_pi_us and summary.status in (0,1,4))):return None
        return SceneSummarySnapshot(raw,self.scene.grant_bytes,sha256(self.scene.grant_bytes).hexdigest(),
            self.scene.context.sha256,self.scene._lease,summary.completed_peer_us,
            min(grant.valid_until_pi_us,self.scene._lease.expires_pi_us),observed,self.scene.eligible)

    def close(self,*,now_j_us):
        self.revoke('SOURCE',now_j_us=now_j_us)
