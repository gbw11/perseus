"""실제 SensorState 원 admission과 Scene 후보/원 CP 경계를 검사한다.

정합 결과는 이 경계에서 고정한 typed software 사실이다. 영상 추출/정합의 물리
수락은 주장하지 않는다. 센서 wire/clock 원본과 SQLite commit은 제품 경로를 쓴다.
"""

from dataclasses import replace
import os
import unittest
from uuid import UUID,uuid4

import test_mapper_sensor_state as sensor_fixture
import test_scene as scene_fixture
from xenova_drive_world import schema,encode,decode
from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.odometry import PoseEstimate
from xenova_vehicle.mapping.registration import RegistrationResult,PoseBox,FrozenReference,SegmentFeature
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.inputs import ImageFrame,PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline,PerceptionResult
from xenova_vehicle.runtime.map_checkpoint import FrameEvidence
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_mapping import MappingFrameDraft
from xenova_vehicle.runtime.mapper_scene import SceneCoordinator,SceneStationWitness
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.scene import SceneEvidenceScope,SurveyProfile,SceneError
from xenova_vehicle.storage.contracts import StorageJob,CommitReceipt,canonical,json_bytes
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload


def uid():return str(uuid4())


class MapperSceneInputsTests(unittest.TestCase):
    def setUp(self):
        self.f=sensor_fixture.MapperSensorStateTests(methodName='runTest');self.f.setUp()
        self.inputs=self.f.read();f=self.f;s=f.fixture;self.now=f.now
        self.scope=SceneEvidenceScope(f.context,f.raw,f.calibration,'e'*64)
        self.profile=SurveyProfile('d'*64,1,4096,5000,.1,.05,.1,50000,.1,.1,500000,'LIVE')
        self.coordinator=SceneCoordinator(f.context,s.source,self.profile,gateway_instance=s.gateway,evidence_scope=self.scope)
        grant=schema.SceneGrant(major=1,vehicle=s.source['vehicle'],pi_host_boot=s.pi_boot,gateway_instance=s.gateway,
            source_admission_revision=self.inputs.clock_lease.source_revision,mapper_device=s.source['device'],issued_pi_us=1_080_000,
            valid_until_pi_us=1_080_000+14_400_000_000,lifecycle_state=0,request_nonce=UUID(uid()).bytes,
            survey_deadline_pi_us=181_080_000,max_observation_stations=24,purpose=0,lifecycle_revision=1,
            frozen_survey_revision=0,frozen_hazard_revision=0,
            **{name:s.source[name] for name in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')})
        grant.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(uid()).bytes,admission_revision=1,
            survey_id=UUID(uid()).bytes,survey_revision=1,policy=1,coverage_profile_sha256=bytes.fromhex('d'*64),
            allow_restore_after_mapper_restart=True))
        self.grant=encode(grant);self.coordinator.maintenance(self.grant,self.inputs.clock_lease,now_j_us=self.now)
        rgb=RawFrame('rgb_video',12,400000,500000,self.now,640,360,'NV12',bytes(640*360*3//2),0)
        depth=RawFrame('depth_right',12,400001,500001,self.now,640,480,'RAW16_LE',bytes(640*480*2),0)
        observation=PerceptionInput('LIVE',f.calibration.sha256,
            ImageFrame(f.stamp,640,360,'NV12',rgb.payload),
            ImageFrame(replace(f.stamp,stream_id=f.raw.fields['stream_instances']['depth_right']),640,480,'RAW16_LE',depth.payload))
        raw=self.inputs.pose
        clocks={name:dict(bridge_sample=dict(j_before_us=str(self.now),sdk_us=str(self.now),j_after_us=str(self.now)),
            sdk_host_timestamp_j_bracket_us=[str(raw.captured_us[0]),str(raw.captured_us[1])]) for name in ('rgb_video','depth_right')}
        item=MapperInput(uid(),f.raw.sha256,CaptureSample(rgb,depth,None,None,clocks),self.now+100000,
            context_bytes=canonical(f.snapshot()))
        fitted=replace(raw,sample=replace(raw.sample,x=raw.sample.x+.05,position_error_m=.002,yaw_error_rad=.002))
        alignment=MapOdomAlignment.from_match(raw,fitted,observed_j_interval=raw.captured_us,
            registration_revision=1,valid_for_us=500000,position_drift_mps=.001,yaw_drift_rps=.001)
        fit=RegistrationResult('BOUNDED',(fitted.sample.x,fitted.sample.y,fitted.sample.yaw),.002,.002,
            (PoseBox((fitted.sample.x-.002,fitted.sample.x+.002),(-.002,.002),(-.002,.002)),),0,1,1,True)
        pipeline=PerceptionPipeline(f.calibration);pipeline._alignment=alignment
        pipeline.reference=FrozenReference(1,f.calibration.sha256,(SegmentFeature((0.,0.),(1.,0.),.001,'paint','capture'),))
        summary=dict(rgb_updated=True,quarantined=False,rgb_source_key=f.stamp.key,
            alignment_metadata=alignment.fields(),reference_revision=1,mapped_pose=list(fit.representative))
        self.result=PerceptionResult(summary,None,None,None,fit,{},None)
        frame=FrameEvidence(self.scope,observation,self.inputs.clock_lease.j_interval_to_pi(raw.captured_us,self.now),(),12,(),(),
            self.inputs.clock_lease,self.now)
        self.mapping=MappingFrameDraft(item,f.context,pipeline,None,self.inputs,frame,None,0,True,'READY',result=self.result)

    def prepare(self,mapping=None,result=None,sequence=1):
        return self.coordinator.prepare_frame(self.mapping if mapping is None else mapping,
            self.result if result is None else result,self.f.state,world_sequence=sequence,hazard_revision=1,now_j_us=self.now)

    def test_real_vehicle_window_keeps_interpolated_capture_separate_and_accepts_read_ref_zero(self):
        original=self.f.state.original_pose_bracket(self.inputs.pose)
        original_samples=tuple(row.evidence.pose_sample for row in original.originals)
        draft=self.prepare()
        self.assertTrue(draft.observed,draft.reason);self.assertIsInstance(draft.station,SceneStationWitness)
        self.assertEqual(draft.station.raw_pose.sample.at_us,1_000_000)
        self.assertEqual(tuple(p.at_us for p in original_samples),(990_000,1_010_000))
        self.assertIs(draft.station.raw_pose,self.inputs.pose)
        self.assertNotEqual(draft.station.raw_pose.sample.x,draft.station.map_pose.sample.x)
        self.assertEqual(tuple(row.evidence.pose_sample for row in original.originals),original_samples)
        self.assertIsNone(self.coordinator.scene._observation)
        self.coordinator.accept(draft,now_j_us=self.now)
        summary=decode(self.coordinator.summary_snapshot(now_j_us=self.now).summary,schema.SurveySummary)
        self.assertEqual((self.result.registration.reference_revision,summary.alignment_revision),(0,1))
        self.assertEqual(summary.observation_station_count,1)
        self.assertNotEqual(self.inputs.pose.sample.calibration_id,self.f.context.calibration)

    def test_cancel_and_late_nn_same_rgb_cannot_add_station_or_refresh_observation(self):
        cancelled=self.prepare()
        self.assertLessEqual(cancelled.expires_j_us,self.mapping.original_job.expires_us)
        with self.assertRaises(SceneError):self.coordinator.accept(cancelled,now_j_us=cancelled.expires_j_us)
        self.coordinator.cancel(cancelled)
        self.assertIsNone(self.coordinator.scene._observation)
        first=self.prepare();self.coordinator.accept(first,now_j_us=self.now)
        before=self.coordinator.summary_snapshot(now_j_us=self.now).summary
        late=replace(self.result,summary=dict(self.result.summary,rgb_updated=False,nn_updated=True))
        mapping=replace(self.mapping,result=late)
        draft=self.prepare(mapping,late,sequence=2)
        self.assertFalse(draft.observed);self.coordinator.accept(draft,now_j_us=self.now+1)
        self.assertEqual(self.coordinator.summary_snapshot(now_j_us=self.now+1).summary,before)
        duplicate=self.prepare(sequence=3)
        self.assertFalse(duplicate.observed);self.coordinator.cancel(duplicate)

    def test_window_requires_actual_registered_originals_and_local_profile_not_bundle(self):
        draft=self.prepare();witness=draft.station
        removed=witness.originals[0].evidence.wire_sha256
        self.f.state._vehicle_admissions.pop(removed)
        with self.assertRaises(ValueError):witness.validate(witness.observation,self.f.context,self.profile,self.now)
        self.coordinator.cancel(draft)
        raw=replace(self.inputs.pose,sample=replace(self.inputs.pose.sample,calibration_id=self.f.context.calibration))
        fit=self.result.registration;fitted=replace(raw,sample=replace(raw.sample,x=fit.representative[0],
            position_error_m=fit.position_error_m,yaw_error_rad=fit.yaw_error_rad))
        alignment=MapOdomAlignment.from_match(raw,fitted,observed_j_interval=raw.captured_us,
            registration_revision=1,valid_for_us=500000,position_drift_mps=.001,yaw_drift_rps=.001)
        from xenova_vehicle.runtime.scene import SurveyObservation
        invalid=SurveyObservation(self.f.stamp,fitted,fit,1,1,'LIVE',raw,alignment)
        with self.assertRaisesRegex(SceneError,'provenance mismatch'):
            self.coordinator.scene.observe(invalid,now_j_us=self.now)


@unittest.skipUnless(os.name=='posix','원 D SQLite writer 검사는 대상 POSIX에서 한다')
class MapperSceneCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.f=scene_fixture.MapperSceneTests(methodName='runTest');self.addCleanup(self.f.doCleanups);self.f.setUp()
        f=self.f
        self.coordinator=SceneCoordinator(f.context,scene_fixture.source(),f.profile,
            gateway_instance=scene_fixture.uid(4),evidence_scope=f.scope)
        self.coordinator.scene=f.mapper
        value=decode(encode(f.grant),schema.SceneGrant);value.lifecycle_state=1;value.lifecycle_revision=2
        value.request_nonce=UUID(scene_fixture.uid(32)).bytes;value.survey_deadline_pi_us=1_500_000
        self.coordinator.maintenance(encode(value),f.clock,now_j_us=f.now)
        self.job=StorageJob(f.context,scene_fixture.uid(23),f.now,1_500_000,scene_fixture.uid(32))
        self.tiles,self.evidence=f.copies();self.refs=tuple(t.reference for t in self.tiles)

    def request(self):
        c=self.coordinator;f=self.f
        scene=c.freeze_scene(self.job,current_tile_references=self.refs,current_hazard_revision=1,now_j_us=f.now)
        reference,pages=encode_mapper_state(dict(profile='explicit-survey-metadata-fixture',original=[1,2,3]))
        scene['mapper_state']=reference
        evidence=replace(self.evidence,contexts=(*self.evidence.contexts,*pages))
        request=CheckpointRequest(self.job,CheckpointPayload.build(self.job,self.tiles,evidence,scene=scene,
            tile_references=self.refs,durable_cutoff_pi_us=1010002))
        return request,reference

    def test_descriptor_adopts_exact_original_cp_then_commit_and_negative_event_keep_observation_time(self):
        c=self.coordinator;f=self.f;request,reference=self.request()
        self.assertIs(c.adopt_freeze(request,current_tile_references=self.refs,current_hazard_revision=1,
            now_j_us=f.now,all_tiles=self.tiles,mapper_state=reference),request)
        self.assertIsNone(c.summary_snapshot(now_j_us=f.now))
        receipt=f.store.commit(request.job,request.payload,current_context_sha256=f.context.sha256)
        self.assertTrue(c.complete_freeze(request,receipt,now_j_us=f.now+20))
        positive=decode(c.summary_snapshot(now_j_us=f.now+20).summary,schema.SurveySummary)
        c.maintenance(c.scene.grant_bytes,f.clock,now_j_us=f.now+30)
        self.assertEqual(c.summary_snapshot(now_j_us=f.now+30).completed_j_us,positive.completed_peer_us)
        c.hazard(2,now_j_us=f.now+100000)
        negative=decode(c.summary_snapshot(now_j_us=f.now+100000).summary,schema.SurveySummary)
        self.assertEqual((negative.status,negative.completed_peer_us),(3,f.now+100000))
        for field in ('started_peer_us','last_observed_peer_us','timestamp_error_us','evidence_sha256'):
            self.assertEqual(getattr(negative,field),getattr(positive,field))
        self.assertFalse(c.scene.eligible)
        self.assertIs(request.job,self.job)

    def test_unpublished_wrong_descriptor_is_terminal_and_pending_negative_has_no_durable_flag(self):
        c=self.coordinator;f=self.f;request,reference=self.request()
        manifest=json_bytes(request.payload.manifest,16384);manifest['scene']['hazard_revision']=2
        wrong=CheckpointRequest(self.job,replace(request.payload,manifest=canonical(manifest)))
        with self.assertRaisesRegex(SceneError,'descriptor'):
            c.adopt_freeze(wrong,current_tile_references=self.refs,current_hazard_revision=1,
                now_j_us=f.now,all_tiles=self.tiles,mapper_state=reference)
        self.assertTrue(c.abort_freeze(wrong,now_j_us=f.now+1))
        self.assertFalse(c.complete_freeze(wrong,None,now_j_us=f.now+2))
        with self.assertRaises(SceneError):c.freeze_scene(self.job,current_tile_references=self.refs,
            current_hazard_revision=1,now_j_us=f.now+2)

    def test_pending_freeze_revoke_does_not_claim_actual_durable_evidence(self):
        c=self.coordinator;f=self.f;request,reference=self.request()
        c.adopt_freeze(request,current_tile_references=self.refs,current_hazard_revision=1,
            now_j_us=f.now,all_tiles=self.tiles,mapper_state=reference)
        c.revoke('HAZARD',now_j_us=f.now+100)
        summary=decode(c.summary_snapshot(now_j_us=f.now+100).summary,schema.SurveySummary)
        self.assertEqual(summary.status,3);self.assertEqual(summary.evidence_flags&16,0)
        from xenova_vehicle.runtime.mapper_storage import StorageAbort
        from xenova_vehicle.runtime.resident_db import DbAbort
        from xenova_vehicle.runtime.storage_messages import prepare_payload
        from xenova_vehicle.runtime.raw_leases import Endpoint
        notice=DbAbort('CHECKPOINT',canonical(prepare_payload(request)['body']),self.job.job_id,
            self.job.issued_us,self.job.expires_us,f.now+200,Endpoint(uid(),f.context.mapper_worker),
            f.context.sha256,'OWNER_EXITED')
        wrong=replace(notice,mapper=Endpoint(uid(),uid()))
        self.assertFalse(c.abort_freeze(request,terminal=StorageAbort('CANCELLED',wrong),now_j_us=f.now+210))
        self.assertTrue(c.abort_freeze(request,terminal=StorageAbort('CANCELLED',notice),now_j_us=f.now+210))
        self.assertIs(c.last_terminal.abort.original_notice,notice)
        self.assertEqual(c.last_terminal.completed_j_us,notice.observed_us)

    def test_new_explicit_nonce_waits_for_original_terminal_and_duplicates_cannot_reset_new_slot(self):
        c=self.coordinator;f=self.f;request,reference=self.request()
        c.adopt_freeze(request,current_tile_references=self.refs,current_hazard_revision=1,
            now_j_us=f.now,all_tiles=self.tiles,mapper_state=reference)
        original_grant=c.scene.grant_bytes
        unknown=CommitReceipt(f.context.database_uuid,self.job.job_id,request.digest,'UNKNOWN',None,None,f.context.sha256)
        self.assertFalse(c.complete_freeze(request,unknown,now_j_us=f.now+1))
        new=decode(original_grant,schema.SceneGrant);new.lifecycle_revision+=1
        new.request_nonce=UUID(scene_fixture.uid(33)).bytes
        new_raw=encode(new)
        self.assertFalse(c.maintenance(new_raw,f.clock,now_j_us=f.now+2))
        self.assertEqual(c.operation_wait_reason,'WAIT_ORIGINAL_FREEZE_TERMINAL')
        self.assertEqual(c.scene.grant_bytes,original_grant);self.assertIs(c._freeze_request,request)
        self.assertIsNone(c.last_terminal)
        self.assertFalse(c.abort_freeze(request,now_j_us=f.now+2))
        receipt=f.store.commit(request.job,request.payload,current_context_sha256=f.context.sha256)
        self.assertTrue(c.complete_freeze(request,receipt,now_j_us=f.now+20))
        terminal=c.last_terminal
        self.assertTrue(c.maintenance(new_raw,f.clock,now_j_us=f.now+21))
        self.assertIs(c.last_terminal,terminal);self.assertIs(terminal.request,request);self.assertIs(terminal.receipt,receipt)
        next_job=StorageJob(f.context,scene_fixture.uid(24),f.now+22,1_500_000,scene_fixture.uid(33))
        descriptor=c.freeze_scene(next_job,current_tile_references=self.refs,current_hazard_revision=1,now_j_us=f.now+22)
        self.assertEqual(descriptor['scene_commit_nonce'],next_job.scene_commit_nonce)
        self.assertTrue(c.maintenance(new_raw,f.clock,now_j_us=f.now+23))
        self.assertIs(c._freeze_job,next_job)
        with self.assertRaises(SceneError):c.freeze_scene(next_job,current_tile_references=self.refs,
            current_hazard_revision=1,now_j_us=f.now+23)
        self.assertFalse(c.complete_freeze(request,receipt,now_j_us=f.now+24))
        self.assertIs(c._freeze_job,next_job);self.assertFalse(c._freeze_terminal)
