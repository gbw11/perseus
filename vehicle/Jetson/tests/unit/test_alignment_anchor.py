"""고정 전파 기준과 현재 정합 자격의 수명을 검산하는 software 반례다.

영상 추출·깊이 입력은 명시 기하 fixture이고 정합·오차 전파는 실제 코어다.
픽셀 인식·physical FREE·World 발행이나 실물 주행 수락으로 사용하지 않는다.
"""

from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.mapping.registration import transform_point

import test_alignment_flow as fixture_module


class AlignmentAnchorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.AlignmentFlowTests(methodName='runTest')
        self.fixture.setUp()
        fields = self.fixture.calibration.to_dict()
        fields.update(kind='LIVE', vehicle_bundle_sha256='b' * 64)
        self.fixture.calibration = CalibrationProfile.from_dict(fields)
        self.limits = dict(localization_valid_for_us=250_000,
            localization_position_drift_mps=0., localization_yaw_drift_rps=0.)

    def pipeline(self, *, bias=0.):
        profile = replace(self.fixture.profile, registration=replace(self.fixture.profile.registration,
            shared_bias_m=bias, shared_bias_rad=bias / 10))
        return PerceptionPipeline(self.fixture.calibration, profile)

    def execute(self, pipeline, sequence, *, at_us=None, x=0., truth_x=0., yaw=0., truth_yaw=0., error=.00001,
                tape=True, epoch='odom', clock='clock', limits=None):
        at = sequence * 100_000 if at_us is None else at_us
        value = replace(self.fixture.observation(sequence), kind='LIVE')
        value = replace(value, **{name: replace(getattr(value, name), stamp=replace(getattr(value, name).stamp,
            clock_id=clock, captured_us=(at, at), available_us=at)) for name in ('rgb', 'depth')})
        raw = self.fixture.pose(sequence, x, error=error, epoch=epoch, clock=clock)
        raw = replace(raw, sample=replace(raw.sample, at_us=at, yaw=yaw, yaw_error_rad=.00001, origin='ODOMETRY'),
            captured_us=(at, at), bracket_us=(at, at))
        graph = (self.fixture.graph(fixture_module.CORNER_GT, truth_x) if tape else
            TapeGraph((), status='UNKNOWN', complete=False, calibration_qualified=True))
        graph = replace(graph, strips=tuple(replace(strip, points_xy=tuple(
            transform_point(point, (0., 0., -truth_yaw)) for point in strip.points_xy)) for strip in graph.strips))
        corners = tuple(transform_point((px - truth_x, py), (0., 0., -truth_yaw))
            for px in (1.24, 1.26) for py in (.79, .81))
        hit = PointBounds((min(p[0] for p in corners), min(p[1] for p in corners), .05),
            (max(p[0] for p in corners), max(p[1] for p in corners), .07), True)
        depth = DepthEvidence(value.depth.stamp, self.fixture.calibration.sha256, 'LIVE',
            (hit,), (), 1, 0, 0, 0, 0, 0, True, 'HIT_ONLY')
        with patch('xenova_vehicle.perception.pipeline.extract_tape', return_value=graph), \
                patch('xenova_vehicle.perception.depth.analyze_depth', return_value=depth):
            return pipeline.process(value, pose=raw, now_us=at, **(self.limits if limits is None else limits))

    def seed(self, pipeline, *, limits=None):
        self.execute(pipeline, 1, limits=limits)
        self.assertIsNone(pipeline._propagation_anchor)
        matched = self.execute(pipeline, 2, limits=limits)
        self.assertEqual(matched.registration.status, 'BOUNDED')
        self.assertTrue(matched.summary['localization_qualified'])
        self.assertIs(pipeline._propagation_anchor, pipeline._alignment)
        return pipeline._propagation_anchor

    def test_stationary_repetition_keeps_anchor_and_nonzero_bias_without_feedback_growth(self):
        for bias in (0., .001):
            with self.subTest(bias=bias):
                pipeline = self.pipeline(bias=bias)
                anchor = self.seed(pipeline)
                fields = anchor.fields()
                errors = []
                for sequence in range(3, 43):
                    result = self.execute(pipeline, sequence)
                    self.assertEqual(result.registration.status, 'BOUNDED')
                    self.assertTrue(result.summary['localization_qualified'])
                    self.assertIs(pipeline._propagation_anchor, anchor)
                    self.assertGreaterEqual(result.registration.position_error_m, bias)
                    errors.append(result.registration.position_error_m)
                self.assertEqual(max(errors), min(errors))
                self.assertEqual(anchor.fields(), fields)
                self.assertEqual(anchor.observed_j_interval, (200_000, 200_000))
                self.assertEqual(pipeline._alignment.observed_j_interval, (4_200_000, 4_200_000))

    def test_fixed_total_elapsed_drift_is_independent_of_frame_partition(self):
        finals = []
        limits = dict(self.limits, localization_position_drift_mps=.001, localization_yaw_drift_rps=.001)
        for count in (15, 30, 60):
            pipeline = self.pipeline()
            anchor = self.seed(pipeline, limits=limits)
            for index in range(1, count + 1):
                at = 200_000 + 900_000 * index // count
                result = self.execute(pipeline, index + 2, at_us=at, limits=limits)
                self.assertEqual(result.registration.status, 'BOUNDED')
                self.assertIs(pipeline._propagation_anchor, anchor)
            finals.append((result.registration.position_error_m, result.registration.yaw_error_rad))
        for final in finals[1:]:
            self.assertAlmostEqual(final[0], finals[0][0], places=12)
            self.assertAlmostEqual(final[1], finals[0][1], places=12)

    def test_real_current_match_corrects_a_noisy_sample_without_replacing_anchor(self):
        pipeline = self.pipeline()
        anchor = self.seed(pipeline)
        result = self.execute(pipeline, 3, x=.2, truth_x=.1, error=.16)
        self.assertEqual(result.registration.status, 'BOUNDED')
        self.assertAlmostEqual(result.summary['mapped_pose'][0], .1, delta=.03)
        self.assertIs(pipeline._propagation_anchor, anchor)
        current = pipeline._alignment
        no_match = self.execute(pipeline, 4, at_us=350_000, x=.25, truth_x=.15, tape=False)
        self.assertIsNone(no_match.registration)
        self.assertTrue(no_match.summary['quarantined'])
        self.assertFalse(no_match.summary['localization_qualified'])
        self.assertIsNone(no_match.summary['mapped_pose'])
        self.assertIs(pipeline._alignment, current)
        recovered = self.execute(pipeline, 5, x=.15, truth_x=.15)
        self.assertEqual(recovered.registration.status, 'BOUNDED')
        self.assertAlmostEqual(recovered.summary['mapped_pose'][0], .15, delta=.003)
        self.assertIs(pipeline._propagation_anchor, anchor)

    def test_expiry_requires_a_fresh_match_and_profile_change_cannot_relabel_anchor(self):
        pipeline = self.pipeline()
        anchor = self.seed(pipeline)
        current = pipeline._alignment
        expired = self.execute(pipeline, 6, tape=False)
        self.assertFalse(expired.summary['localization_qualified'])
        self.assertTrue(expired.summary['quarantined'])
        self.assertIs(pipeline._alignment, current)
        self.assertIs(pipeline._propagation_anchor, anchor)
        fresh = self.execute(pipeline, 7)
        self.assertTrue(fresh.summary['localization_qualified'])
        self.assertEqual(pipeline._alignment.observed_j_interval, (700_000, 700_000))
        self.assertIs(pipeline._propagation_anchor, anchor)
        current, reference = pipeline._alignment, pipeline.reference
        changed = self.execute(pipeline, 8,
            limits=dict(self.limits, localization_position_drift_mps=.001))
        self.assertTrue(changed.summary['quarantined'])
        self.assertFalse(changed.summary['localization_qualified'])
        self.assertIs(pipeline.reference, reference)
        self.assertIs(pipeline._alignment, current)
        self.assertIs(pipeline._propagation_anchor, anchor)

    def test_rotation_uses_current_raw_heading_and_keeps_large_coordinate_lever_error(self):
        pipeline = self.pipeline()
        anchor = self.seed(pipeline)
        result = self.execute(pipeline, 3, yaw=.02, truth_yaw=.02)
        self.assertEqual(result.registration.status, 'BOUNDED')
        self.assertAlmostEqual(result.summary['mapped_pose'][2], .02, delta=.001)
        self.assertIs(pipeline._propagation_anchor, anchor)
        far = self.fixture.pose(4, 10., error=.00001)
        far = replace(far, sample=replace(far.sample, origin='ODOMETRY', yaw_error_rad=.00001))
        propagated = anchor.apply(far)
        lever = 20. * math.sin(anchor.yaw_error_rad / 2)
        self.assertGreaterEqual(propagated.sample.position_error_m,
            anchor.position_error_m + far.sample.position_error_m + lever)
        self.assertEqual(anchor.apply(far), propagated)

    def test_new_odom_or_clock_context_cannot_inherit_previous_anchor(self):
        for field in ('epoch', 'clock'):
            with self.subTest(field=field):
                pipeline = self.pipeline()
                anchor = self.seed(pipeline)
                changed = self.execute(pipeline, 3, **{field: 'new-' + field})
                self.assertIsNone(pipeline._propagation_anchor)
                self.assertIsNone(pipeline._alignment)
                self.assertFalse(changed.summary['localization_qualified'])
                self.assertEqual(anchor.observed_j_interval, (200_000, 200_000))

    def test_legacy_anchor_cannot_gain_a_configured_drift_policy_from_new_frame(self):
        pipeline = self.pipeline()
        self.execute(pipeline, 1, limits={})
        matched = self.execute(pipeline, 2, limits={})
        self.assertEqual(matched.registration.status, 'BOUNDED')
        anchor = pipeline._propagation_anchor
        self.assertFalse(anchor.has_metadata)
        current, reference = pipeline._alignment, pipeline.reference
        result = self.execute(pipeline, 3,
            limits=dict(self.limits, localization_position_drift_mps=.001))
        self.assertTrue(result.summary['quarantined'])
        self.assertFalse(result.summary['localization_qualified'])
        self.assertIs(pipeline._alignment, current)
        self.assertIs(pipeline.reference, reference)
        self.assertIs(pipeline._propagation_anchor, anchor)
        self.assertFalse(anchor.has_metadata)


if __name__ == '__main__':
    unittest.main()
