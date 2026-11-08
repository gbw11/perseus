"""유한 endpoint 정책의 원 생산·체크포인트 호환 경계를 검사한다.

영상과 보정은 기존 명시 software fixture를 사용한다. SQLite COMMIT/LOAD와
pipeline 정합·격리는 실제 구현이며 새 시각·map epoch·scene 권한을 만들지 않는다.
"""

from dataclasses import asdict,replace
import os
import unittest
from unittest.mock import patch

from xenova_drive_world import encode
from xenova_vehicle.mapping.odometry import PoseEstimate
from xenova_vehicle.mapping.registration import (FrozenReference,PointFeature,PoseBox,
    RegistrationProfile,SegmentFeature,register)
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.runtime.map_checkpoint import CheckpointPages,MapCheckpointNotReady,MapRestoreNotReady,restore_pipeline
from xenova_vehicle.storage.contracts import LoadRequest,canonical,json_bytes
from xenova_vehicle.storage.mapper_state import decode_mapper_state,encode_mapper_state
from xenova_vehicle.storage.records import CheckpointPayload

import test_alignment_flow as alignment_fixture
import test_map_checkpoint as checkpoint_fixture


POLICY='PHYSICAL_STRIP_ENDPOINTS_V1'


class RegistrationFeatureMetadataTests(unittest.TestCase):
    def test_policy_is_explicit_but_generic_registration_keeps_its_existing_contract(self):
        segment=SegmentFeature((0.,0.),(1.,0.),.001,'finite-software-segment','old-capture')
        reference=FrozenReference(7,'calibration',(segment,))
        self.assertEqual(reference.feature_policy,'UNSPECIFIED')
        self.assertEqual(asdict(replace(reference,feature_policy=POLICY))['feature_policy'],POLICY)
        for value in (None,1,'physical_junction','PHYSICAL_STRIP_ENDPOINTS_V2'):
            with self.subTest(policy=value),self.assertRaises(ValueError):
                replace(reference,feature_policy=value)
        # 독립 producer의 generic feature는 이 pipeline 전용 정책으로 거절하지 않는다.
        result=register((PointFeature((.5,0.),.001,'point','new-capture'),),reference,
            PoseBox((0.,0.),(0.,0.),(0.,0.)),RegistrationProfile(),calibration_id='calibration')
        self.assertEqual(result.status,'BOUNDED')
        self.assertTrue(result.complete)

    def test_current_legacy_reference_quarantines_without_reseed_or_alignment_refresh(self):
        fixture=alignment_fixture.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        pipeline=PerceptionPipeline(fixture.calibration,fixture.profile)
        fixture.process(pipeline,1,fixture.graph(alignment_fixture.CORNER_GT,0.),fixture.pose(1))
        result=fixture.process(pipeline,2,fixture.graph(alignment_fixture.CORNER_GT,.1),
            fixture.pose(2,.2,error=.16),truth_x=.1)
        self.assertEqual(result.registration.status,'BOUNDED')
        legacy=replace(pipeline.reference,feature_policy='UNSPECIFIED');pipeline.reference=legacy
        grid,alignment,context=pipeline.grid,pipeline._alignment,pipeline._context
        revision=grid.revision;state=grid.state.copy()
        result=fixture.process(pipeline,3,fixture.graph(alignment_fixture.CORNER_GT,.1),
            fixture.pose(3,.2,error=.16),truth_x=.1)
        self.assertIsNone(result.registration)
        self.assertTrue(result.summary['quarantined'])
        self.assertIs(pipeline.reference,legacy)
        self.assertIs(pipeline._alignment,alignment)
        self.assertIs(pipeline.grid,grid)
        self.assertEqual(pipeline._context,context)
        self.assertEqual(grid.revision,revision)
        self.assertTrue((grid.state==state).all())


@unittest.skipUnless(os.name=='posix','실제 D SQLite/lock은 대상 POSIX에서 검사한다')
class RegistrationFeatureRestoreTests(unittest.TestCase):
    def fixture(self):
        fixture=checkpoint_fixture.MapCheckpointTests(methodName='runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        restored,pipeline,_,_=fixture._restored_nonidentity_fixture(
            raw_x=.11,position_error=.012,spacing_us=200_000)
        return fixture,restored,pipeline

    def load(self,fixture):
        ref=canonical(fixture.store.latest_complete(expires_us=fixture.sensor.now+5_000_000))
        request=LoadRequest(fixture.job(),ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=fixture.store.load(request,current_context_sha256=fixture.context.sha256)
        return loaded,CheckpointPages(ref,fixture.context).accept(loaded,now_us=fixture.sensor.now)

    def rewrite_reference(self,fixture,change):
        loaded,_=self.load(fixture)
        manifest=json_bytes(loaded.payload.manifest,16384)
        state,old_pages=decode_mapper_state(manifest['scene'],loaded.payload.evidence.contexts)
        change(state['reference'])
        metadata,pages=encode_mapper_state(state)
        contexts=tuple(item for item in loaded.payload.evidence.contexts if item not in old_pages)+pages
        evidence=replace(loaded.payload.evidence,contexts=contexts)
        job=fixture.job()
        payload=CheckpointPayload.build(job,(),evidence,tile_references=loaded.payload.tile_references,
            scene=dict(manifest['scene'],mapper_state=metadata),durable_cutoff_pi_us=manifest['durable_cutoff_pi_us'])
        receipt=fixture.store.commit(job,payload,current_context_sha256=fixture.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        return self.load(fixture)[1]

    def test_actual_checkpoint_and_load_preserve_original_physical_feature_policy(self):
        fixture,restored,pipeline=self.fixture()
        _,complete=self.load(fixture)
        state=json_bytes(complete.state,512*1024)
        self.assertEqual(state['reference']['feature_policy'],POLICY)
        self.assertEqual(restored.reference,pipeline.reference)
        self.assertEqual(restored.reference.feature_policy,POLICY)
        self.assertTrue(restored._restore_pending)
        self.assertFalse(restored.grid.state.any() or restored.grid.time_known.any())

    def test_original_four_field_reference_remains_legacy_and_cannot_relocalize_nonidentity_map(self):
        fixture,_,pipeline=self.fixture()
        complete=self.rewrite_reference(fixture,lambda reference:reference.pop('feature_policy'))
        state=json_bytes(complete.state,512*1024)
        self.assertEqual(set(state['reference']),{'revision','calibration_id','segments','shared_error_m'})
        candidate=PerceptionPipeline(fixture.calibration,pipeline.profile)
        with self.assertRaisesRegex(MapRestoreNotReady,'endpoint policy is unspecified'):
            restore_pipeline(candidate,complete,fixture.scope)
        self.assertIsNone(candidate.grid)
        self.assertIsNone(candidate.reference)
        self.assertEqual(json_bytes(complete.state,512*1024),state)

    def test_original_identity_checkpoint_restores_legacy_reference_but_new_frame_cannot_reseed_it(self):
        fixture=checkpoint_fixture.MapCheckpointTests(methodName='runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        graph_fixture=alignment_fixture.AlignmentFlowTests(methodName='runTest');graph_fixture.setUp()
        graph=graph_fixture.graph(alignment_fixture.CORNER_GT,0.)
        pipeline=fixture.pipeline
        with patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph), \
                patch('xenova_vehicle.perception.depth.analyze_depth',return_value=fixture.depth):
            result=pipeline.process(fixture.observation,pose=fixture.pose,now_us=fixture.sensor.now,
                evidence_observer=fixture.collector.frame_observer(fixture.frame,pipeline=pipeline))
        fixture.collector.observe_result(fixture.frame,result,pipeline,raw_pose=fixture.pose)
        self.assertIsNone(pipeline._alignment)
        self.assertIsNone(result.registration)
        self.assertEqual(pipeline.reference.feature_policy,POLICY)
        request=fixture.collector.capture(pipeline,fixture.job(),now_us=fixture.sensor.now)
        receipt=fixture.store.commit(request.job,request.payload,current_context_sha256=fixture.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        self.assertTrue(fixture.collector.accept_commit(request,receipt,now_us=fixture.sensor.now))
        _,original=self.load(fixture)
        original_state=json_bytes(original.state,512*1024)
        self.assertTrue(original_state['map_from_odom_identity'])
        self.assertIsNone(original_state['alignment'])
        complete=self.rewrite_reference(fixture,lambda reference:reference.pop('feature_policy'))
        restored=PerceptionPipeline(fixture.calibration,pipeline.profile)
        self.assertIs(restore_pipeline(restored,complete,fixture.scope),complete)
        self.assertFalse(restored._restore_pending)
        self.assertEqual(restored.reference.feature_policy,'UNSPECIFIED')
        self.assertEqual(restored.rgb_gate._heads,pipeline.rgb_gate._heads)
        self.assertEqual(restored.depth_gate._heads,pipeline.depth_gate._heads)
        self.assertEqual(restored.reference.segments,pipeline.reference.segments)
        grid,reference,context=restored.grid,restored.reference,restored._context
        revision=grid.revision;retained_state=complete.state;state=grid.state.copy()
        fixture.sensor.now+=20_000
        message=fixture.sensor.vehicle(2,1_120_050,0.)
        message.clock_error_us=0;message.position_error_m=0.;message.heading_error_rad=0.
        vehicle=fixture.admission.accept_vehicle(encode(message),fixture.sensor.lease,fixture.sensor.now)
        pose=PoseEstimate(vehicle.pose_sample,vehicle.j_interval,vehicle.j_interval)
        stamp=replace(fixture.observation.rgb.stamp,sequence=2,captured_us=pose.captured_us,available_us=fixture.sensor.now)
        observation=replace(fixture.observation,rgb=replace(fixture.observation.rgb,stamp=stamp),depth=None)
        with patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph):
            result=restored.process(observation,pose=pose,now_us=fixture.sensor.now)
        self.assertTrue(result.summary['rgb_updated'] and result.summary['quarantined'])
        self.assertIsNone(result.registration)
        self.assertIsNone(restored._alignment)
        self.assertIs(restored.grid,grid);self.assertIs(restored.reference,reference)
        self.assertEqual(restored._context,context);self.assertEqual(grid.revision,revision)
        self.assertTrue((grid.state==state).all())
        self.assertEqual(restored.depth_gate._heads,pipeline.depth_gate._heads)
        self.assertEqual(next(iter(restored.rgb_gate._heads.values()))[0],2)
        self.assertIs(complete.state,retained_state)
        self.assertEqual(fixture.context.database_uuid,fixture.db.metadata['database_uuid'])

    def test_unknown_policy_or_extra_reference_field_rejects_before_pipeline_mutation(self):
        fixture,_,pipeline=self.fixture()
        original_policy=POLICY
        for name,change in (
                ('unknown',lambda reference:reference.update(feature_policy='PHYSICAL_STRIP_ENDPOINTS_V2')),
                ('null',lambda reference:reference.update(feature_policy=None)),
                ('extra',lambda reference:reference.update(feature_policy=original_policy,endpoint_approved=True))):
            with self.subTest(metadata=name):
                complete=self.rewrite_reference(fixture,change)
                candidate=PerceptionPipeline(fixture.calibration,pipeline.profile)
                with self.assertRaises(MapCheckpointNotReady):restore_pipeline(candidate,complete,fixture.scope)
                self.assertIsNone(candidate.grid)
                self.assertIsNone(candidate.reference)
