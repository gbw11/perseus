"""보정·좌표·연속 interval의 수치 회귀. 합성 수치는 실물 수락값이 아니다."""

from dataclasses import replace
import hashlib
import json
import math
from types import SimpleNamespace
import unittest

from xenova_vehicle.oak.calibration_snapshot import snapshot_factory
from xenova_vehicle.perception.calibration import (CameraModel, CalibrationProfile, IDENTITY,
    Plane, extrinsic_cm_to_m, synthetic_calibration)
from xenova_vehicle.perception.geometry import (capture_hull, capture_interval, depth_point,
    ground_to_pixel, project_ground, project_ground_bounds, project_point,
    axial_ray_bounds, axial_ray_bounds_many, project_ground_bounds_many)


class MetricGeometryTests(unittest.TestCase):
    def test_bundle_roundtrip_and_component_provenance_are_immutable(self):
        original = synthetic_calibration()
        decoded = CalibrationProfile.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(original.sha256, decoded.sha256)
        self.assertEqual(original.leaf_hashes, decoded.leaf_hashes)
        value = original.to_dict()
        value['right']['translation'] = [99, 99, 99]
        value['provenance']['measured'] = True
        self.assertEqual(original.right.translation, (.20, -.0375, .12))
        self.assertFalse(original.provenance['measured'])
        self.assertGreaterEqual(original.depth_error_m, .0005)
        self.assertEqual(original.provenance['depth_storage'], 'ROUND_NEAREST_RAW16_AXIAL_MM')
        with self.assertRaises(ValueError):
            CalibrationProfile.from_dict(dict(original.to_dict(), schema_version=True))

    def test_depth_is_axial_z_and_does_not_use_radial_range_for_near_far(self):
        camera = CameraModel(100, 100, ((100, 0, 50), (0, 100, 50), (0, 0, 1)),
                             IDENTITY, (0, 0, 0), pixel_error=0, near_m=.01, far_m=1.01)
        self.assertEqual(depth_point(75, 50, 1000, camera), (.25, 0., 1.))
        self.assertAlmostEqual(math.dist((0, 0, 0), depth_point(75, 50, 1000, camera)), math.sqrt(1.0625))
        self.assertIsNone(depth_point(75, 50, 0, camera))

    def test_rectification_inverse_and_right_optical_origin_survive_projection(self):
        profile = synthetic_calibration()
        theta = .2
        rect = ((math.cos(theta), -math.sin(theta), 0.),
                (math.sin(theta), math.cos(theta), 0.), (0., 0., 1.))
        camera = replace(profile.right, rectification=rect)
        point = depth_point(210, 280, 600, camera)
        uv = project_point(point, camera)
        self.assertAlmostEqual(uv[0], 210, places=9)
        self.assertAlmostEqual(uv[1], 280, places=9)
        raw = tuple(sum(camera.rotation[j][i] * (point[j] - camera.translation[j]) for j in range(3)) for i in range(3))
        active_z = sum(rect[2][i] * raw[i] for i in range(3))
        self.assertAlmostEqual(active_z, .6, places=12)
        self.assertNotEqual(camera.translation, profile.rgb.translation)

    def test_rgb_plane_roundtrip_and_continuous_box_include_perturbed_pixels(self):
        profile = synthetic_calibration()
        camera = replace(profile.rgb, translation_error_m=.001, rotation_error_rad=.002)
        plane = Plane(normal_error_rad=.001, offset_error_m=.001, qualified=True)
        uv = ground_to_pixel(1., .1, camera, plane)
        nominal = project_ground(*uv, camera, plane)
        self.assertAlmostEqual(nominal[0], 1., places=10)
        self.assertAlmostEqual(nominal[1], .1, places=10)
        bound = project_ground_bounds(*uv, camera, plane)
        self.assertIsNotNone(bound)
        self.assertTrue(bound.qualified)
        for du in (-.4, 0., .4):
            for dv in (-.4, 0., .4):
                point = project_ground(uv[0] + du, uv[1] + dv, camera, plane)
                self.assertTrue(all(a <= x <= b for a, x, b in zip(bound.lower, point, bound.upper)))
        self.assertGreater(bound.xy_error_from(nominal), 0)

    def test_horizon_roi_and_missing_nonlinear_bound_remain_unknown(self):
        camera = synthetic_calibration(pitch_rad=0.).rgb
        u, v = camera.k[0][2], camera.k[1][2]
        self.assertIsNone(project_ground(u, v, camera, Plane(qualified=True)))
        self.assertIsNone(project_ground_bounds(u, v, camera, Plane(qualified=True)))
        self.assertIsNone(project_ground_bounds(0, camera.height - 2, camera, Plane(qualified=True)))
        distorted = replace(camera, distortion=(.01, 0., 0., 0., 0.))
        self.assertIsNone(project_ground_bounds(u, v + 20, distorted, Plane(qualified=True)))

    def test_extrinsic_centimeters_are_converted_once(self):
        matrix = ((1, 0, 0, 12), (0, 1, 0, -3), (0, 0, 1, 4), (0, 0, 0, 1))
        rotation, translation = extrinsic_cm_to_m(matrix)
        self.assertEqual(rotation, IDENTITY)
        self.assertEqual(translation, (.12, -.03, .04))
        with self.assertRaises(ValueError):
            extrinsic_cm_to_m((rotation, translation))

    def test_capture_interval_adds_errors_and_preserves_unknown(self):
        end = (10000, 10002)
        interval = capture_interval(end, 1000, clock_error_us=3, timestamp_error_us=2, readout_error_us=5)
        self.assertEqual(interval, (8990, 10012))
        self.assertEqual(end, (10000, 10002))
        self.assertEqual(capture_hull((interval, (8000, 11000))), (8000, 11000))
        self.assertIsNone(capture_interval(end, 1000, timestamp_error_us=2, readout_error_us=5))
        self.assertIsNone(capture_hull((interval, None)))

    def test_factory_snapshot_never_claims_active_intrinsics_or_device_readback(self):
        class Handler:
            def eepromToJson(self): return dict(factory='synthetic SDK fixture')
            def getDefaultIntrinsics(self, socket): return IDENTITY, 640, 480
            def getDistortionModel(self, socket): return 'Perspective'
            def getDistortionCoefficients(self, socket): return [0.] * 14
            def getCameraIntrinsics(self, socket, width, height): return IDENTITY
            def getStereoLeftCameraId(self): return 'B'
            def getStereoRightCameraId(self): return 'C'
            def getStereoLeftRectificationRotation(self): return IDENTITY
            def getStereoRightRectificationRotation(self): return IDENTITY
            def getBaselineDistance(self, a, b, spec):
                assert spec is False
                return 7.5
            def getCameraExtrinsics(self, a, b, spec):
                assert spec is False
                return ((1, 0, 0, 3), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
            def getLensPosition(self, socket): return 123
        dai = SimpleNamespace(__version__='2.29.0.0', CameraBoardSocket=SimpleNamespace(CAM_A='A', CAM_B='B', CAM_C='C'))
        raw = b'original-synthetic-eeprom'
        value = snapshot_factory(Handler(), dai, eeprom_raw=raw, device_id='SYNTHETIC')
        self.assertEqual(value['eeprom_raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertIsNone(value['active']['k_rect'])
        self.assertIsNone(value['active']['r_rect'])
        self.assertIsNone(value['active']['right_left_support_roi'])
        self.assertFalse(value['metric_qualified'])
        self.assertEqual(value['calibrated_right_to_rgb_extrinsics_cm'][0][3], 3)

    def test_missing_optional_sdk_getters_leave_partial_factory_facts(self):
        class OlderHandler:
            def eepromToJson(self): return dict(factory='older SDK fixture')
        dai = SimpleNamespace(__version__='2.29.0.0', CameraBoardSocket=SimpleNamespace(CAM_A='A', CAM_B='B', CAM_C='C'))
        value = snapshot_factory(OlderHandler(), dai)
        self.assertEqual(value['eeprom'], {'factory': 'older SDK fixture'})
        self.assertIsNone(value['stored_left_rectification_rotation'])
        self.assertIsNone(value['stored_right_rectification_rotation'])
        self.assertEqual(value['getter_errors']['left_r'], 'AttributeError')
        self.assertEqual(value['getter_errors']['right_r'], 'AttributeError')
        self.assertFalse(value['metric_qualified'])

    def test_batch_axial_bounds_preserve_scalar_error_formula_and_unit_box(self):
        import numpy as np
        camera = CameraModel(640, 480, ((500., 17., 320.), (0., 500., 240.), (0., 0., 1.)),
            IDENTITY, (.1, .2, .3), rotation_error_rad=.05, ray_error_rad=.001, translation_error_m=.003)
        u, v, z, dz = [320., 280., 500.], [240., 300., 150.], [1000., 550., 1600.], [.0005, .002, .001]
        outputs = axial_ray_bounds_many(u, v, z, camera, depth_error_m=dz)
        self.assertTrue(outputs[-1].all())
        for index in range(3):
            origin, direction, radial, near, far = axial_ray_bounds(u[index], v[index], z[index], camera, depth_error_m=dz[index])
            expected = ([x.lo for x in origin], [x.hi for x in origin], [x.lo for x in direction],
                        [x.hi for x in direction], radial.lo, radial.hi, near, far)
            for actual, scalar in zip(outputs[:-1], expected):
                np.testing.assert_allclose(actual[index], scalar, rtol=2e-13, atol=2e-13)
            self.assertTrue((outputs[2][index] >= -1).all())
            self.assertTrue((outputs[3][index] <= 1).all())
        invalid = axial_ray_bounds_many([0, 320, 320], [0, 240, 240], [1000, 0, 1000], camera,
                                        depth_error_m=[0, 0, -1])
        self.assertFalse(invalid[-1].any())
        self.assertTrue(all(not output.flags.writeable for output in outputs))

    def test_fixed_ground_homography_box_covers_continuous_rectangle_oracle(self):
        import numpy as np
        camera, plane = synthetic_calibration().rgb, Plane(qualified=True)
        # 가까운 (.4,-.2)는 RGB의 가로 ROI 밖이다. 세 점 모두 실제 가시 영역에 둔다.
        uv = [ground_to_pixel(x, y, camera, plane) for x, y in ((.7, -.2), (.8, .1), (1.4, .6))]
        self.assertTrue(all(point is not None for point in uv))
        lower, upper, valid = project_ground_bounds_many([p[0] for p in uv], [p[1] for p in uv],
                                                        camera, plane, pixel_error=1.5)
        self.assertTrue(valid.all())
        # z=0의 고정 평면 oracle. batch helper/Interval 연산을 재사용하지 않는다.
        for index, (u, v) in enumerate(uv):
            for du in (-1.5, -.7, 0., .3, 1.5):
                for dv in (-1.5, -.4, 0., .8, 1.5):
                    qy = (v + dv - camera.k[1][2]) / camera.k[1][1]
                    q = ((u + du - camera.k[0][2] - camera.k[0][1] * qy) / camera.k[0][0], qy, 1.)
                    raw = [sum(camera.rectification[j][i] * q[j] for j in range(3)) for i in range(3)]
                    ray = [sum(camera.rotation[i][j] * raw[j] for j in range(3)) for i in range(3)]
                    distance = -camera.translation[2] / ray[2]
                    point = [camera.translation[i] + distance * ray[i] for i in range(3)]
                    self.assertTrue((lower[index] <= point).all())
                    self.assertTrue((upper[index] >= point).all())
        self.assertTrue(all(not x.flags.writeable for x in (lower, upper, valid)))

    def test_general_ground_batch_crosses_block_and_preserves_unknown(self):
        import numpy as np
        camera = replace(synthetic_calibration().rgb, translation_error_m=.001, rotation_error_rad=.002)
        plane = Plane(normal_error_rad=.001, offset_error_m=.001, qualified=True)
        uv = ground_to_pixel(1., .1, camera, plane)
        u, v = np.full(8201, uv[0]), np.full(8201, uv[1])
        u[-1], v[-1] = 0., 0.
        lower, upper, valid = project_ground_bounds_many(u, v, camera, plane)
        scalar = project_ground_bounds(*uv, camera, plane)
        self.assertTrue(valid[:-1].all())
        self.assertFalse(valid[-1])
        for index in (0, 8191, 8192, 8199):
            np.testing.assert_allclose(lower[index], scalar.lower, rtol=2e-13, atol=2e-13)
            np.testing.assert_allclose(upper[index], scalar.upper, rtol=2e-13, atol=2e-13)
        horizon = replace(synthetic_calibration(pitch_rad=0.).rgb, pixel_error=0.)
        self.assertFalse(project_ground_bounds_many([horizon.k[0][2]], [horizon.k[1][2]], horizon, Plane())[-1][0])
        distorted = replace(camera, distortion=(.01, 0., 0., 0., 0.))
        self.assertFalse(project_ground_bounds_many([uv[0]], [uv[1]], distorted, plane)[-1][0])


if __name__ == '__main__':
    unittest.main()
