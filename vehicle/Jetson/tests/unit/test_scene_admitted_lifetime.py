"""원 SQL 동결과 ADMITTED 사용 수명을 조사 예산에서 분리한다.

기존 Scene fixture의 SYNTHETIC 영역을 유지하며 물리 LIVE로 바꾸지 않는다.
clock은 원 protobuf와 공용 returned-probe 검산 경로로 갱신한다. 별도 LIVE
Vehicle 창 음성은 기존 실제 admission fixture를 사용하며 장치 수락을 주장하지 않는다.
"""

from dataclasses import replace
import math
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import test_scene as fixture
import test_mapper_scene as live_fixture
from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import ClockPolicy, restore_returned_probe
from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.registration import FrozenReference, SegmentFeature, PoseBox
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.inputs import ImageFrame, PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline, PerceptionResult
from xenova_vehicle.runtime.map_checkpoint import FrameEvidence
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_mapping import MappingFrameDraft
from xenova_vehicle.runtime.mapper_scene import SceneCoordinator, SceneStationWitness
from xenova_vehicle.runtime.mapper_sensor_state import MapperSensorState, MetricInputs
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.scene import MapperScene, SceneError
from xenova_vehicle.storage.contracts import LoadRequest, StorageJob, canonical
from xenova_vehicle.storage.records import CELL


def uid(): return str(uuid4())


def policy():
    source=fixture.source()
    return ClockPolicy(source['vehicle'],source['device'],source['configuration'],source['calibration'],source['primitive'],
        timestamp_error_us=0,drift_ppm=0,max_offset_width_us=100,lease_us=1_000_000,
        vehicle_max_age_us=200_000,lookback_us=1_000_000)


def renewed_clock(original,now):
    """새 원 Probe13/trust를 검산한다. 이전 lease/SceneGrant의 expiry는 수정하지 않는다."""
    source=fixture.source();probe=decode(original.raw_probe_bytes,schema.MapperClockProbe)
    probe.probe_seq+=1;probe.nonce=UUID(uid()).bytes;probe.pi_send_us=now-1000
    for name in ('mapper_boot','mapper_instance','clock_epoch'):setattr(probe,'expected_'+name,source[name])
    trust=decode(original.trust_bytes,schema.WorldTrust);trust.expires_pi_us=now+500_000
    probe.mapper_trust.CopyFrom(trust)
    return restore_returned_probe(encode(probe),policy(),source,fixture.uid(1),now)


def moved(observation,x):
    """명시된 software 정합 결과와 그 원 raw→MAP 관계를 함께 바꾼다."""
    sample=replace(observation.pose.sample,x=x);pose=replace(observation.pose,sample=sample)
    result=replace(observation.registration,representative=(x,sample.y,sample.yaw),
        feasible=(PoseBox((x-.001,x+.001),(sample.y-.001,sample.y+.001),(-.001,.001)),))
    alignment=MapOdomAlignment.from_match(observation.raw_pose,pose,observed_j_interval=pose.captured_us,
        registration_revision=observation.accepted_alignment.registration_revision,valid_for_us=500_000,
        position_drift_mps=.001,yaw_drift_rps=.001)
    return replace(observation,pose=pose,registration=result,accepted_alignment=alignment)


@unittest.skipUnless(os.name=='posix','실제 D SQL/lock 원 commit은 대상 POSIX에서 검사한다')
class AdmittedLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.MapperSceneTests(methodName='runTest');self.addCleanup(self.f.doCleanups);self.f.setUp()

    def admitted(self):
        f=self.f;value,request,receipt=f.committed()
        summary=decode(f.mapper.summary_bytes(now_j_us=f.now+10),schema.SurveySummary)
        value.lifecycle_state=2;value.lifecycle_revision+=1
        value.frozen_evidence_sha256=summary.evidence_sha256;value.frozen_survey_revision=summary.survey_revision
        value.frozen_hazard_revision=summary.hazard_revision
        self.assertTrue(f.mapper.receive_grant(encode(value),f.clock,now_j_us=f.now+20))
        self.assertTrue(f.mapper.eligible);self.assertEqual(receipt.job_digest,request.digest)
        return f.mapper,value,request,receipt

    def restored_admitted(self):
        f=self.f;value,request,receipt=f.committed()
        selected=canonical(f.store.latest_complete(expires_us=f.now+500_000))
        load=LoadRequest(StorageJob(f.context,uid(),f.now,f.now+500_000),selected,
            canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=f.store.load(load,current_context_sha256=f.context.sha256)
        value.lifecycle_revision+=1;value.purpose=1;value.request_nonce=UUID(uid()).bytes
        value.frozen_evidence_sha256=bytes.fromhex(request.payload.sha256);value.frozen_survey_revision=1
        value.frozen_hazard_revision=1;value.restore_checkpoint_sha256=bytes.fromhex(request.payload.sha256)
        mapper=MapperScene(f.context,fixture.source(),f.profile,gateway_instance=fixture.uid(4),evidence_scope=f.scope)
        mapper.receive_grant(encode(value),f.clock,now_j_us=f.now+20)
        current=fixture.observation(2,1_020_200,calibration_id=f.calibration.sha256)
        mapper.observe(current,now_j_us=1_020_220);f.station(mapper,current)
        summary=decode(mapper.restore_ready(loaded,receipt,current,now_j_us=1_020_240),schema.SurveySummary)
        self.assertEqual(summary.status,4)
        value.lifecycle_state=2;value.lifecycle_revision+=1
        self.assertTrue(mapper.receive_grant(encode(value),f.clock,now_j_us=1_020_250))
        self.assertTrue(mapper.eligible)
        self.assertEqual(CELL.unpack_from(loaded.payload.tiles[0].payload)[7:9],(1_010_000,1_010_002))
        return mapper,value,request

    def test_admitted_after_original_180_seconds_uses_new_original_clock_not_a_new_scene_deadline(self):
        mapper,grant,request,_=self.admitted();old_grant=mapper.grant_bytes;old_clock=self.f.clock
        at=grant.issued_pi_us+180_000_001;now=at+30
        clock=renewed_clock(old_clock,now);self.assertIsNotNone(clock)
        mapper.renew_clock(clock,now_j_us=now)
        original=fixture.observation(2,at,calibration_id=self.f.calibration.sha256)
        self.assertTrue(mapper.observe(original,now_j_us=now));self.assertTrue(mapper.eligible)
        self.assertEqual(mapper.grant_bytes,old_grant)
        self.assertEqual(old_clock.expires_pi_us,2_000_000)
        self.assertIs(mapper._frozen.request,request)
        self.assertEqual(CELL.unpack_from(request.payload.tiles[0].payload)[7:9],(1_010_000,1_010_002))

    def test_capture_gap_exceeding_station_vehicle_gap_remains_fresh_without_renewing_observation(self):
        mapper,_,_,_=self.admitted();f=self.f
        observation=fixture.observation(2,1_080_000,calibration_id=f.calibration.sha256)
        self.assertGreater(observation.pose.sample.at_us-mapper._observation.pose.sample.at_us,f.profile.max_pose_gap_us)
        self.assertTrue(mapper.observe(observation,now_j_us=1_080_030))
        self.assertIs(mapper._observation,observation)
        self.assertEqual(observation.stamp.captured_us,(1_080_000,1_080_002))
        # 조사 단계에서도 RGB 처리율은 stationary Vehicle 창의 최대 간격이 아니다.
        other=fixture.MapperSceneTests(methodName='runTest');self.addCleanup(other.doCleanups);other.setUp()
        self.assertTrue(other.mapper.observe(fixture.observation(2,1_080_000,
            calibration_id=other.calibration.sha256),now_j_us=1_080_030))

    def test_admitted_does_not_spend_supervised_collection_distance_but_collection_cap_stays_bounded(self):
        mapper,_,_,_=self.admitted();before=mapper._travel
        observation=moved(fixture.observation(2,1_080_000,calibration_id=self.f.calibration.sha256),11.1)
        self.assertTrue(mapper.observe(observation,now_j_us=1_080_030))
        self.assertEqual(mapper._travel,before);self.assertTrue(mapper.eligible)
        collecting=fixture.MapperSceneTests(methodName='runTest');self.addCleanup(collecting.doCleanups);collecting.setUp()
        with self.assertRaisesRegex(SceneError,'10m'):
            collecting.mapper.observe(moved(fixture.observation(2,1_080_000,calibration_id=collecting.calibration.sha256),11.1),now_j_us=1_080_030)
        self.assertTrue(math.isfinite(collecting.mapper._travel));self.assertLessEqual(collecting.mapper._travel,10.)

    def mapping(self,observation,lease,now):
        """기존 SYNTHETIC 결과를 실제 Coordinator DTO 포트에 전달한다. 센서 read는 없다."""
        f=self.f;rgb=ImageFrame(observation.stamp,2,2,'NV12',bytes(6))
        depth=ImageFrame(replace(observation.stamp,stream_id=f.raw.fields['stream_instances']['depth_right']),2,2,'RAW16_LE',bytes(8))
        capture=PerceptionInput('SYNTHETIC',f.calibration.sha256,rgb,depth)
        raws=tuple(RawFrame(name,observation.stamp.sequence,observation.stamp.captured_us[0],observation.stamp.captured_us[0],
            now,2,2,image.encoding,image.payload,0) for name,image in (('rgb_video',rgb),('depth_right',depth)))
        clocks={name:dict(bridge_sample=dict(j_before_us=str(now),sdk_us=str(now),j_after_us=str(now)),
            sdk_host_timestamp_j_bracket_us=list(map(str,observation.raw_pose.captured_us))) for name in ('rgb_video','depth_right')}
        item=MapperInput(uid(),f.raw.sha256,CaptureSample(*raws,None,None,clocks),now+100_000)
        frame=FrameEvidence(f.scope,capture,lease.j_interval_to_pi(capture.capture_interval,now),(),
            observation.world_sequence,(),(),lease,now)
        pipeline=PerceptionPipeline(f.calibration);pipeline._alignment=observation.accepted_alignment
        pipeline.reference=FrozenReference(observation.accepted_alignment.registration_revision,f.calibration.sha256,
            (SegmentFeature((0.,0.),(1.,0.),.001,'paint','explicit-software-reference'),))
        result=PerceptionResult(dict(rgb_updated=True,quarantined=False,rgb_source_key=observation.stamp.key,
            alignment_metadata=observation.accepted_alignment.fields(),reference_revision=pipeline.reference.revision,
            mapped_pose=list(observation.registration.representative)),None,None,None,observation.registration,{},None)
        inputs=MetricInputs(f.context,fixture.source(),lease,observation.raw_pose,None,(),(),(),())
        return MappingFrameDraft(item,f.context,pipeline,None,inputs,frame,None,0,True,'READY',result=result)

    def sensor_port(self):
        f=self.f
        return MapperSensorState(f.calibration,f.raw,policy(),tof_leaves={},vehicle_max_age_us=200_000,
            tof_max_age_us=200_000,max_speed_mps=.5,max_yaw_rate_rps=2.,max_gap_us=100_000,capacity=16,
            mapper_worker=f.context.mapper_worker,mapper_instance=f.context.origin_mapper_instance)

    def test_restore_admitted_coordinator_outlives_restore_deadline_and_25_frames_add_no_station(self):
        mapper,grant,_=self.restored_admitted();f=self.f;stations=mapper._stations;windows=mapper._station_windows
        coordinator=SceneCoordinator(f.context,fixture.source(),f.profile,gateway_instance=fixture.uid(4),evidence_scope=f.scope)
        coordinator.scene=mapper;sensors=self.sensor_port();original=mapper.grant_bytes
        with patch.object(SceneStationWitness,'from_frame',side_effect=AssertionError('ADMITTED must not collect a station')):
            for index in range(25):
                at=grant.survey_deadline_pi_us+1000+index*3000;now=at+30
                lease=renewed_clock(f.clock,now)
                observation=fixture.observation(index+3,at,calibration_id=f.calibration.sha256)
                mapping=self.mapping(observation,lease,now)
                draft=coordinator.prepare_frame(mapping,mapping.result,sensors,world_sequence=index+3,hazard_revision=1,now_j_us=now)
                self.assertTrue(draft.observed,draft.reason);self.assertIsNone(draft.station)
                self.assertGreater(draft.expires_j_us,now)
                self.assertLessEqual(draft.expires_j_us,mapping.original_job.expires_us)
                coordinator.accept(draft,now_j_us=now)
        self.assertTrue(coordinator.scene.eligible)
        self.assertFalse(coordinator.scene.station((),now_j_us=now))
        self.assertFalse(coordinator.scene._station_record(None,{},f.profile))
        self.assertIs(coordinator.scene._stations,stations);self.assertIs(coordinator.scene._station_windows,windows)
        self.assertEqual(coordinator.scene.grant_bytes,original)

    def test_original_scene_expiry_stale_observation_and_collection_cutoff_still_reject(self):
        mapper,grant,_,_=self.admitted();at=1_080_000;late=at+self.f.profile.observation_max_age_us+1
        mapper.renew_clock(renewed_clock(self.f.clock,late),now_j_us=late)
        with self.assertRaisesRegex(SceneError,'freshness'):
            mapper.observe(fixture.observation(2,at,calibration_id=self.f.calibration.sha256),now_j_us=late)
        expiry=grant.valid_until_pi_us
        mapper.renew_clock(renewed_clock(self.f.clock,expiry),now_j_us=expiry)
        with self.assertRaises(SceneError):mapper.summary_bytes(now_j_us=expiry)
        self.assertFalse(mapper.eligible)
        other=fixture.MapperSceneTests(methodName='runTest');self.addCleanup(other.doCleanups);other.setUp()
        at=other.grant.issued_pi_us+180_000_001;now=at+30
        other.mapper.renew_clock(renewed_clock(other.clock,now),now_j_us=now)
        with self.assertRaisesRegex(SceneError,'collection'):
            other.mapper.observe(fixture.observation(2,at,calibration_id=other.calibration.sha256),now_j_us=now)


class VehicleStationGapTests(unittest.TestCase):
    def test_original_live_vehicle_window_gap_is_still_checked_independently(self):
        f=live_fixture.MapperSceneInputsTests(methodName='runTest');self.addCleanup(f.doCleanups);f.setUp()
        strict=replace(f.profile,max_pose_gap_us=10_000)
        f.coordinator.scene.profile=strict
        draft=f.prepare();self.assertTrue(draft.observed)
        self.assertIsNone(draft.station);self.assertEqual(draft.reason,'OBSERVED_STATION_NOT_READY')
        with self.assertRaisesRegex(SceneError,'gap'):
            SceneStationWitness.from_frame(f.f.state,draft.scene._observation,f.f.context,strict,now_j_us=f.now)
        self.assertFalse(f.coordinator.scene.eligible);f.coordinator.cancel(draft)


if __name__=='__main__':unittest.main()
