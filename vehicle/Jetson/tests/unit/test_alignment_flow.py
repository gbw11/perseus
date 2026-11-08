"""정합으로 채택한 pose가 실제 reference/hit에 쓰이고 모호하면 격리되는지 검사한다.

영상 추출·깊이 추출만 고정 typed 사실로 대체한다. static corner GT와 bias odom은
서로 별개이며 register·alignment·좌표 변환·grid 갱신은 제품 구현 그대로 실행한다.
"""

from dataclasses import replace
import unittest
from unittest.mock import patch

from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.local_map import GridSpec, OCCUPIED
from xenova_vehicle.mapping.odometry import PoseEstimate, PoseSample
from xenova_vehicle.mapping.registration import RegistrationProfile
from xenova_vehicle.perception.calibration import synthetic_calibration
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline, PipelineProfile
from xenova_vehicle.perception.tape import TapeGraph, TapeStrip


CORNER_GT = (((.3, .3), (.7, .3), (1.1, .3), (1.5, .3)),
             ((.3, .3), (.3, .7), (.3, 1.1), (.3, 1.5)))
HIT_GT = (1.25, .8)


class AlignmentFlowTests(unittest.TestCase):
    def setUp(self):
        self.calibration = synthetic_calibration()
        self.profile = PipelineProfile(grid=GridSpec(48, 40, .05, (0., -.2)),
            registration=RegistrationProfile(cell_steps=(.002, .002, .001), residual_m=.002,
                max_position_error_m=.03, max_yaw_error_rad=.01), max_free_cells=1, max_free_ray_tests=16)

    def observation(self, sequence):
        at = sequence * 100_000
        def stamp(stream):
            return FrameStamp('alignment-fixture', stream, sequence, 'clock', (at, at), at)
        return PerceptionInput('SYNTHETIC', self.calibration.sha256,
            ImageFrame(stamp('rgb'), 640, 360, 'BGR8', bytes(640 * 360 * 3)),
            ImageFrame(stamp('right'), 640, 480, 'RAW16_LE', bytes(640 * 480 * 2)))

    def pose(self, sequence, x=0., *, error=0., epoch='odom', clock='clock'):
        at = sequence * 100_000
        sample = PoseSample(epoch, clock, self.calibration.sha256, at, x, 0., 0., error, .001,
                            origin='SYNTHETIC_ODOMETRY')
        return PoseEstimate(sample, (at, at), (at, at))

    def graph(self, world_lines, truth_x):
        strips = tuple(TapeStrip(i, tuple((x - truth_x, y) for x, y in points), (.02, .03),
            .0005, 'observed_paint_end', 'observed_paint_end') for i, points in enumerate(world_lines))
        return TapeGraph(strips, calibration_qualified=True)

    def process(self, pipeline, sequence, graph, pose, *, truth_x=0.):
        def depth(_array, _camera, _plane, *, stamp, calibration_id, input_kind, profile, config):
            x, y = HIT_GT[0] - truth_x, HIT_GT[1]
            box = PointBounds((x - .005, y - .005, .05), (x + .005, y + .005, .07), True)
            return DepthEvidence(stamp, calibration_id, input_kind, (box,), (), 1, 0, 0, 0, 0, 0, True, 'HIT_ONLY')
        with patch('xenova_vehicle.perception.pipeline.extract_tape', return_value=graph), \
                patch('xenova_vehicle.perception.depth.analyze_depth', side_effect=depth):
            return pipeline.process(self.observation(sequence), pose=pose)

    def test_static_corner_corrects_bias_before_reference_and_hit_mapping(self):
        pipeline = PerceptionPipeline(self.calibration, self.profile)
        self.process(pipeline, 1, self.graph(CORNER_GT, 0.), self.pose(1))
        old_reference = pipeline.reference
        raw_odom = self.pose(2, .2, error=.16)
        result = self.process(pipeline, 2, self.graph(CORNER_GT, .1), raw_odom, truth_x=.1)
        self.assertEqual(result.registration.status, 'BOUNDED')
        self.assertFalse(result.summary['quarantined'])
        fitted_x = result.summary['mapped_pose'][0]
        self.assertAlmostEqual(fitted_x, .1, delta=.03)
        self.assertGreater(abs(fitted_x - raw_odom.sample.x), .06)
        self.assertIsNot(pipeline.reference, old_reference)
        self.assertEqual(pipeline.reference.revision, old_reference.revision + 1)
        self.assertAlmostEqual(pipeline.reference.segments[0].a[0], CORNER_GT[0][0][0], delta=.03)
        raw_reference_x = raw_odom.sample.x + CORNER_GT[0][0][0] - .1
        self.assertGreater(abs(pipeline.reference.segments[0].a[0] - raw_reference_x), .06)
        correct = pipeline.grid.touched((HIT_GT[0] - .002, HIT_GT[1] - .002, HIT_GT[0] + .002, HIT_GT[1] + .002))
        wrong = pipeline.grid.touched((HIT_GT[0] + .1 - .002, HIT_GT[1] - .002,
                                       HIT_GT[0] + .1 + .002, HIT_GT[1] + .002))
        self.assertTrue(any(pipeline.grid.state[row, col] == OCCUPIED for col, row in correct))
        self.assertTrue(all(pipeline.grid.state[row, col] != OCCUPIED for col, row in wrong))
        self.assertAlmostEqual(result.summary['map_from_odom'][0], -.1, delta=.03)

    def test_parallel_ridge_and_repeated_corner_alias_preserve_previous_map(self):
        import numpy as np
        repeated = CORNER_GT + tuple(tuple((x + 1., y) for x, y in points) for points in CORNER_GT)
        for label, reference_lines, observed_lines, raw_x, error in (
            ('parallel', CORNER_GT[:1], CORNER_GT[:1], .2, .4),
            ('repeated_alias', repeated, CORNER_GT, .5, .52)):
            with self.subTest(scene=label):
                pipeline = PerceptionPipeline(self.calibration, self.profile)
                self.process(pipeline, 1, self.graph(reference_lines, 0.), self.pose(1))
                reference, grid = pipeline.reference, pipeline.grid
                state, observed_us, revision = grid.state.copy(), grid.observed_us.copy(), grid.revision
                result = self.process(pipeline, 2, self.graph(observed_lines, 0.), self.pose(2, raw_x, error=error))
                self.assertNotEqual(result.registration.status, 'BOUNDED')
                self.assertTrue(result.summary['quarantined'])
                self.assertTrue(result.summary['map_frame'].startswith('quarantine:'))
                self.assertIs(pipeline.reference, reference)
                self.assertIs(pipeline.grid, grid)
                self.assertEqual(grid.revision, revision)
                self.assertTrue(np.array_equal(grid.state, state))
                self.assertTrue(np.array_equal(grid.observed_us, observed_us))

    def test_alignment_never_crosses_original_clock_or_odom_epoch(self):
        raw = self.pose(2, .2, error=.01)
        fitted = replace(raw, sample=replace(raw.sample, x=.1, position_error_m=.005))
        alignment = MapOdomAlignment.from_match(raw, fitted)
        self.assertAlmostEqual(alignment.apply(self.pose(3, .25)).sample.x, .15)
        with self.assertRaises(ValueError):
            alignment.apply(self.pose(3, .25, clock='other-clock'))
        with self.assertRaises(ValueError):
            alignment.apply(self.pose(3, .25, epoch='other-odom'))
