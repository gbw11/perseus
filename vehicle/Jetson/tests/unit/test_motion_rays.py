"""전체 노출 pose box의 연속 ray 변환을 독립 SE2 표본과 extrema로 검산한다.

표본은 interval 구현을 대신하지 않는 회귀 oracle다. 실제 odom/slip/센서 오차와
시간 수락은 시험하지 않으며 ORACLE_SIMULATION pose를 FREE 자격으로 쓰지 않는다.
"""

from dataclasses import replace
import math
import unittest

from xenova_vehicle.mapping.odometry import PoseBuffer, PoseEstimate, PoseSample
from xenova_vehicle.perception.calibration import CalibrationProfile, CameraModel, Plane
from xenova_vehicle.perception.free_space import RayBundle, TargetClass, certify_cell
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.motion import sin_cos_bounds, widen_motion_rays
from xenova_vehicle.perception.ray_batch import RayBundleBatch


def profile():
    camera = CameraModel(3, 3, ((100., 0., 1.), (0., 100., 1.), (0., 0., 1.)),
        ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (.1, -.05, 1.),
        pixel_error=0., near_m=.01, far_m=3., qualified=True, remap_qualified=True)
    return CalibrationProfile(camera, camera, Plane(qualified=True),
        ((-3., -3.), (3., -3.), (3., 3.), (-3., 3.)), kind='SYNTHETIC',
        time_qualified=True, first_surface_qualified=True, minimum_target_qualified=True)


def stamp():
    return FrameStamp('source', 'right', 7, 'simulation-clock', (100000, 140000), 160000)


def rays(original=None):
    original = stamp() if original is None else original
    return (RayBundle((.1, -.05, 1.), (.1, -.05, 1.), (.6, 0., -.8), (.6, 0., -.8),
                1.2, 1.3, .01, 3., True, original.key, (1., 1.)),
            RayBundle((.2, .1, 1.), (.2, .1, 1.), (0., .6, -.8), (0., .6, -.8),
                1.1, 1.4, .01, 3., False, original.key, (2., 1.)))


def pose(calibration, *, captured=None):
    captured = stamp().captured_us if captured is None else captured
    point = PoseSample('odom', stamp().clock_id, calibration.sha256, 120000,
        2., -3., math.pi / 3, .04, .2, 'SYNTHETIC_ODOMETRY')
    return PoseEstimate(point, captured, (80000, 160000))


class MotionRayTests(unittest.TestCase):
    def test_small_qualified_exposure_motion_retains_conditional_positive_certificate(self):
        from test_depth_evidence import profile as ideal_profile, witness_rays
        calibration = ideal_profile()
        original = FrameStamp('synthetic', 'right', 1, 'sim-clock', (1000, 2000), 2100)
        sample = PoseSample('odom', original.clock_id, calibration.sha256, 1500,
            0., 0., 0., .00001, .00001, 'SYNTHETIC_ODOMETRY')
        estimate = PoseEstimate(sample, original.captured_us, (900, 2100))
        transformed = widen_motion_rays(witness_rays(), original, calibration, estimate,
            input_kind='SYNTHETIC', motion_qualified=True)
        certificate = certify_cell((-.01, -.01, .01, .01), transformed,
            (TargetClass(.5, .5, .5, qualified=True),), calibration, max_tiles=64, max_ray_tests=256)
        self.assertEqual(certificate.status, 'CLEAR_REGION')
        self.assertTrue(certificate.complete)
        self.assertLessEqual(certificate.work, 256)

    def test_analytic_trig_includes_interior_extrema_wrap_and_whole_turn(self):
        for lower, upper, sine, cosine in (
                (0., math.pi, (0., 1.), (-1., 1.)),
                (-math.pi, 0., (-1., 0.), (-1., 1.)),
                (-4., 4., (-1., 1.), (-1., 1.)),
                (math.tau - .2, math.tau + .2, (-math.sin(.2), math.sin(.2)), (math.cos(.2), 1.))):
            with self.subTest(lower=lower, upper=upper):
                actual_sine, actual_cosine = sin_cos_bounds(lower, upper)
                self.assertLessEqual(actual_sine[0], sine[0])
                self.assertGreaterEqual(actual_sine[1], sine[1])
                self.assertLessEqual(actual_cosine[0], cosine[0])
                self.assertGreaterEqual(actual_cosine[1], cosine[1])
        self.assertEqual(sin_cos_bounds(0., 0.), ((0., 0.), (1., 1.)))

    def test_every_ray_origin_and_direction_cover_relative_exposure_pose_box(self):
        import numpy as np
        calibration, original = profile(), stamp()
        source = rays(original)
        bound = pose(calibration)
        result = widen_motion_rays(RayBundleBatch.from_rays(source), original, calibration, bound,
            input_kind='SYNTHETIC', motion_qualified=True)
        self.assertEqual(len(result), len(source))
        for row, ray in enumerate(source):
            for theta in np.linspace(-.2, .2, 33):
                c, s = math.cos(float(theta)), math.sin(float(theta))
                for dx in (-.04, 0., .04):
                    for dy in (-.04, 0., .04):
                        a = bound.sample.yaw
                        tx, ty = math.cos(a) * dx + math.sin(a) * dy, -math.sin(a) * dx + math.cos(a) * dy
                        ox, oy, oz = ray.origin_lower
                        vx, vy, vz = ray.direction_lower
                        points = ((tx + c * ox - s * oy, ty + s * ox + c * oy, oz),
                                  (c * vx - s * vy, s * vx + c * vy, vz))
                        for low, high, actual in ((result[row].origin_lower, result[row].origin_upper, points[0]),
                                                  (result[row].direction_lower, result[row].direction_upper, points[1])):
                            self.assertTrue(all(lo <= value <= hi for lo, value, hi in zip(low, actual, high)))
            self.assertEqual((result[row].range_lower_m, result[row].range_upper_m, result[row].near_m, result[row].far_m),
                (ray.range_lower_m, ray.range_upper_m, ray.near_m, ray.far_m))
            self.assertEqual((result[row].pixel, result[row].source_key, result[row].qualified),
                (ray.pixel, ray.source_key, ray.qualified))
        self.assertGreater(result[0].origin_upper[0] - result[0].origin_lower[0], .08)
        self.assertGreater(result[0].direction_upper[1] - result[0].direction_lower[1], .1)
        self.assertTrue(np.all(result.numbers[:, 6:12] >= -1))
        self.assertTrue(np.all(result.numbers[:, 6:12] <= 1))
        with self.assertRaises(ValueError):
            result.numbers.setflags(write=True)

    def test_missing_time_pose_profile_or_original_source_never_qualifies_free(self):
        import numpy as np
        calibration, original = profile(), stamp()
        estimate = pose(calibration)
        fields = calibration.to_dict()
        fields['first_surface_qualified'] = False
        unqualified = CalibrationProfile.from_dict(fields)
        cases = (
            (original, calibration, None, True),
            (replace(original, captured_us=None), calibration, estimate, True),
            (original, calibration, estimate, False),
            (original, unqualified, estimate, True),
            (original, calibration, replace(estimate, sample=replace(estimate.sample, clock_id='other')), True),
            (original, calibration, replace(estimate, sample=replace(estimate.sample, calibration_id='b' * 64)), True),
            (original, calibration, replace(estimate, bracket_us=(110000, 160000)), True),
            (original, calibration, replace(estimate, sample=replace(estimate.sample, origin='ORACLE_SIMULATION')), True),
        )
        for source_stamp, p, estimate, qualified in cases:
            result = widen_motion_rays(rays(original), source_stamp, p, estimate,
                input_kind='SYNTHETIC', motion_qualified=qualified)
            self.assertFalse(result.qualified.any())
            np.testing.assert_array_equal(result.numbers[:, 12:18], RayBundleBatch.from_rays(rays(original)).numbers[:, 12:18])
        foreign = (replace(rays(original)[0], source_key=('different-source',)),)
        result = widen_motion_rays(foreign, original, calibration, pose(calibration),
            input_kind='SYNTHETIC', motion_qualified=True)
        self.assertFalse(result.qualified.any())

    def test_pose_buffer_whole_interval_and_explicit_full_hull_are_bound_without_new_time(self):
        calibration, original = profile(), stamp()
        buffer = PoseBuffer(max_gap_us=100000, max_speed_mps=.5, max_yaw_rate_rps=2.)
        for at, x, angle in ((80000, 0., 0.), (160000, .02, .08)):
            buffer.add(PoseSample('odom', original.clock_id, calibration.sha256, at,
                x, 0., angle, .001, .002, 'SYNTHETIC_ODOMETRY'))
        expanded = widen_motion_rays(rays(original), original, calibration, buffer,
            input_kind='SYNTHETIC', motion_qualified=True)
        self.assertTrue(expanded[0].qualified)
        full = (90000, 150000)
        estimate = pose(calibration, captured=full)
        self.assertFalse(widen_motion_rays(rays(original), original, calibration, estimate,
            input_kind='SYNTHETIC', motion_qualified=True).qualified.any())
        result = widen_motion_rays(rays(original), original, calibration, estimate,
            input_kind='SYNTHETIC', motion_qualified=True, capture_interval=full)
        self.assertTrue(result[0].qualified)
        self.assertEqual(result[0].source_key, original.key)
        self.assertEqual(original.captured_us, (100000, 140000))
        self.assertFalse(widen_motion_rays(rays(original), original, calibration, estimate,
            input_kind='LIVE', motion_qualified=True, capture_interval=full).qualified.any())
