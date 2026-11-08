"""원 정합 시각/기한과 drift의 데이터 흐름. 값은 실물 측정이 아닌 독립 software fixture다.

강한 fresh prior의 별도 양성만 사용하며 기존4096 탐색 cap/오차 수락 기준을 바꾸지 않는다.
정상 frame/clock 진전은 원 정합 시각을 새로 발급하는 사건이 아니다.
"""

from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.odometry import PoseEstimate,PoseSample
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.geometry import PointBounds

import test_alignment_flow as fixture_module


def pose(at,*,captured=None):
    sample=PoseSample('odom','clock','a'*64,at,0.,0.,0.,.001,.002,'ODOMETRY')
    interval=(at,at) if captured is None else captured
    return PoseEstimate(sample,interval,interval)


class AlignmentLifetimeTests(unittest.TestCase):
    def test_legacy_exact_four_fields_and_partial_or_synthesized_metadata_rejection(self):
        raw=pose(120_000,captured=(100_000,140_000));fitted=replace(raw,sample=replace(raw.sample,x=.1))
        legacy=MapOdomAlignment.from_match(raw,fitted)
        self.assertEqual(set(legacy.fields()),{'pose','position_error_m','yaw_error_rad','context'})
        self.assertFalse(legacy.has_metadata or legacy.valid_at(150_000))
        self.assertEqual(MapOdomAlignment.from_fields(legacy.fields()),legacy)
        with self.assertRaises(ValueError):MapOdomAlignment.from_match(raw,fitted,observed_j_interval=raw.captured_us)
        with self.assertRaises(ValueError):MapOdomAlignment.from_fields(dict(legacy.fields(),observed_j_interval=None,
            registration_revision=None,valid_for_us=None,position_drift_mps=None,yaw_drift_rps=None))
        with self.assertRaises(ValueError):MapOdomAlignment.from_match(raw,fitted,observed_j_interval=(110_000,140_000),
            registration_revision=1,valid_for_us=100_000,position_drift_mps=.1,yaw_drift_rps=.1)

    def test_drift_uses_original_interval_once_and_expiry_does_not_remove_a_registration_prior(self):
        alignment=MapOdomAlignment((1.,0.,0.),.01,.02,pose(120_000).sample.context,
            (100_000,140_000),7,200_000,.2,.1)
        first=pose(210_000,captured=(200_000,220_000));later=pose(310_000,captured=(300_000,320_000))
        a=alignment.apply(first);b=alignment.apply(later)
        self.assertGreaterEqual(a.sample.position_error_m,.01+.001+.2*.12)
        self.assertGreaterEqual(a.sample.yaw_error_rad,.02+.002+.1*.12)
        self.assertAlmostEqual(b.sample.position_error_m-a.sample.position_error_m,.02,places=12)
        self.assertEqual(alignment.apply(later),b)
        self.assertEqual(alignment.position_error_m,.01);self.assertEqual(later.sample.position_error_m,.001)
        self.assertEqual(alignment.observed_j_interval,(100_000,140_000));self.assertEqual(alignment.registration_revision,7)
        self.assertTrue(alignment.valid_at(140_000));self.assertTrue(alignment.valid_at(299_999))
        self.assertFalse(alignment.valid_at(139_999));self.assertFalse(alignment.valid_at(300_000))
        self.assertFalse(alignment.valid_at(320_000));self.assertEqual(b.captured_us,later.captured_us)
        long=replace(alignment,yaw_drift_rps=1.).apply(pose(10_000_000))
        self.assertEqual(long.sample.yaw_error_rad,math.pi)
        self.assertEqual(MapOdomAlignment.from_fields(alignment.fields()),alignment)

    def test_operational_pipeline_keeps_original_stamp_quarantines_expiry_and_can_match_fresh(self):
        fixture=fixture_module.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        fields=fixture.calibration.to_dict();fields.update(kind='LIVE',vehicle_bundle_sha256='b'*64)
        fixture.calibration=CalibrationProfile.from_dict(fields)
        pipeline=PerceptionPipeline(fixture.calibration,fixture.profile)
        limits=dict(localization_valid_for_us=250_000,localization_position_drift_mps=.001,localization_yaw_drift_rps=.001)
        def execute(sequence,*,tape=True):
            observation=replace(fixture.observation(sequence),kind='LIVE')
            original=fixture.pose(sequence,error=.00001)
            estimate=replace(original,sample=replace(original.sample,origin='ODOMETRY',yaw_error_rad=.00001))
            graph=fixture.graph(fixture_module.CORNER_GT,0.) if tape else TapeGraph((),status='UNKNOWN',complete=False,calibration_qualified=True)
            box=PointBounds((1.24,.79,.05),(1.26,.81,.07),True)
            depth=DepthEvidence(observation.depth.stamp,fixture.calibration.sha256,'LIVE',(box,),(),1,0,0,0,0,0,True,'HIT_ONLY')
            with patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph), \
                    patch('xenova_vehicle.perception.depth.analyze_depth',return_value=depth):
                return pipeline.process(observation,pose=estimate,now_us=sequence*100_000,**limits)
        seeded=execute(1)
        self.assertFalse(seeded.summary['localization_qualified'])
        fitted=execute(2);self.assertEqual(fitted.registration.status,'BOUNDED')
        alignment=pipeline._alignment
        self.assertEqual(alignment.observed_j_interval,(200_000,200_000));self.assertEqual(alignment.registration_revision,1)
        self.assertTrue(fitted.summary['localization_qualified'])
        execute(3,tape=False)
        self.assertIs(pipeline._alignment,alignment);self.assertEqual(pipeline._alignment.observed_j_interval,(200_000,200_000))
        grid,reference=pipeline.grid,pipeline.reference;revision=grid.revision;state=grid.state.copy()
        expired=execute(5,tape=False)
        self.assertTrue(expired.summary['quarantined']);self.assertFalse(expired.summary['localization_qualified'])
        self.assertIs(pipeline.grid,grid);self.assertIs(pipeline.reference,reference)
        self.assertEqual(grid.revision,revision);self.assertTrue((grid.state==state).all())
        self.assertIs(pipeline._alignment,alignment)
        fresh=execute(6)
        self.assertEqual(fresh.registration.status,'BOUNDED');self.assertTrue(fresh.registration.complete)
        self.assertFalse(fresh.summary['quarantined']);self.assertTrue(fresh.summary['localization_qualified'])
        self.assertEqual(pipeline._alignment.observed_j_interval,(600_000,600_000))
        self.assertEqual(pipeline._alignment.registration_revision,2)
        self.assertFalse(fresh.summary['world_valid'] or fresh.summary['live_authority'])

    def test_partial_runtime_limits_reject_before_source_hwm_changes(self):
        fixture=fixture_module.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        pipeline=PerceptionPipeline(fixture.calibration,fixture.profile)
        with self.assertRaises(ValueError):pipeline.process(fixture.observation(1),pose=fixture.pose(1),localization_valid_for_us=100_000)
        self.assertEqual(pipeline.rgb_gate._heads,{})
        self.assertIsNone(pipeline.grid)
