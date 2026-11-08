"""합성 depth와 opaque-core certificate의 양성/음성. 실물 센서 수락을 주장하지 않는다."""

from dataclasses import replace
import math
import unittest

from xenova_vehicle.perception.calibration import CameraModel, CalibrationProfile, Plane
from xenova_vehicle.perception.depth import DepthConfig, analyze_depth
from xenova_vehicle.perception.free_space import RayBundle, TargetClass, certify_cell
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.geometry import axial_ray_bounds


def profile(size=100):
    k = ((100., 0., (size - 1) / 2), (0., 100., (size - 1) / 2), (0., 0., 1.))
    camera = CameraModel(size, size, k, ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)),
        (0., 0., 1.), pixel_error=0., near_m=.01, far_m=3., qualified=True, remap_qualified=True)
    return CalibrationProfile(camera, camera, Plane(qualified=True),
        ((-3., -3.), (3., -3.), (3., 3.), (-3., 3.)), kind='SYNTHETIC',
        provenance=dict(model='IDEAL_FLOOR_AND_OPAQUE_VERTICAL_CORES', measured=False),
        first_surface_qualified=True, minimum_target_qualified=True, time_qualified=True)


def witness_rays():
    result = []
    for x in (-.4, 0., .4):
        for y in (-.4, 0., .4):
            delta = (x, y, -.75)
            norm = math.sqrt(sum(v * v for v in delta))
            direction = tuple(v / norm for v in delta)
            ground_range = -1. / direction[2]
            result.append(RayBundle((0., 0., 1.), (0., 0., 1.), direction, direction,
                ground_range, ground_range, .01, 3., True, ('synthetic', 'right', 'sim-clock', 1)))
    return tuple(result)


class DepthEvidenceTests(unittest.TestCase):
    def test_positive_certificate_covers_every_expanded_target_placement(self):
        target = TargetClass(.5, .5, .5, qualified=True)
        certificate = certify_cell((-.01, -.01, .01, .01), witness_rays(), (target,), profile(),
                                   max_tiles=64, max_ray_tests=256)
        self.assertEqual(certificate.status, 'CLEAR_REGION')
        self.assertTrue(certificate.complete)
        self.assertEqual(certificate.tested_tiles, certificate.required_tiles)
        self.assertGreater(certificate.required_tiles, 1)
        self.assertLessEqual(certificate.work, 256)

    def test_blind_volume_and_long_protrusion_are_not_cleared(self):
        p = profile()
        target = TargetClass(.5, .5, .5, qualified=True)
        only_center = (witness_rays()[4],)
        blind = certify_cell((-.01, -.01, .01, .01), only_center, (target,), p)
        self.assertEqual(blind.status, 'UNKNOWN')
        protrusion = TargetClass(.5, .5, 1., qualified=True)
        overhang = certify_cell((-.01, -.01, .01, .01), witness_rays(), (protrusion,), p)
        self.assertEqual(overhang.status, 'UNKNOWN')
        self.assertFalse(overhang.complete)

    def test_untested_tile_or_ray_work_cap_never_becomes_free(self):
        target = TargetClass(.5, .5, .5, qualified=True)
        tile_limited = certify_cell((-.01, -.01, .01, .01), witness_rays(), (target,), profile(), max_tiles=1)
        self.assertEqual(tile_limited.status, 'UNKNOWN')
        self.assertLess(tile_limited.tested_tiles, tile_limited.required_tiles)
        ray_limited = certify_cell((-.01, -.01, .01, .01), witness_rays(), (target,), profile(), max_ray_tests=1)
        self.assertEqual(ray_limited.status, 'UNKNOWN')
        self.assertLessEqual(ray_limited.work, 1)

    def test_unqualified_first_surface_or_minimum_target_blocks_certificate(self):
        p = profile()
        fields = p.to_dict()
        fields['first_surface_qualified'] = False
        unqualified = CalibrationProfile.from_dict(fields)
        target = TargetClass(.5, .5, .5, qualified=True)
        self.assertEqual(certify_cell((-.01, -.01, .01, .01), witness_rays(), (target,), unqualified).status, 'UNKNOWN')
        self.assertEqual(certify_cell((-.01, -.01, .01, .01), witness_rays(),
            (TargetClass(.5, .5, .5, qualified=False),), p).status, 'UNKNOWN')

    def test_depth_zero_is_unknown_and_positive_hit_keeps_original_stamp(self):
        import numpy as np
        p = profile(5)
        stamp = FrameStamp('synthetic', 'right', 7, 'simulation-clock', (100, 100), 100)
        depth = np.full((5, 5), 1000, dtype=np.uint16)
        depth[0, 0] = 0
        depth[2, 2] = 500
        value = analyze_depth(depth, p.right, p.plane, stamp=stamp, profile=p, input_kind='SYNTHETIC')
        self.assertIs(value.stamp, stamp)
        self.assertEqual(value.valid_count, 24)
        self.assertEqual(value.invalid_count, 1)
        self.assertEqual(value.floor_count, 23)
        self.assertEqual(len(value.hit_boxes), 1)
        self.assertGreater(value.hit_boxes[0].lower[2], .49)
        self.assertTrue(value.metric_qualified)
        self.assertTrue(all(ray.source_key == stamp.key for ray in value.patches))
        missing_time = replace(stamp, captured_us=None)
        unknown = analyze_depth(depth, p.right, p.plane, stamp=missing_time, profile=p, input_kind='SYNTHETIC')
        self.assertFalse(unknown.metric_qualified)
        self.assertTrue(all(not ray.qualified for ray in unknown.patches))

    def test_depth_caps_report_omissions_and_synthetic_cannot_be_live(self):
        import numpy as np
        p = profile(5)
        stamp = FrameStamp('synthetic', 'right', 7, 'simulation-clock', (100, 100), 100)
        data = np.full((5, 5), 1000, dtype=np.uint16)
        data[2, 2] = 500
        config = DepthConfig(max_patches=2, max_hit_boxes=0)
        value = analyze_depth(data, p.right, p.plane, stamp=stamp, profile=p, input_kind='SYNTHETIC', config=config)
        self.assertLessEqual(len(value.patches), 2)
        self.assertEqual(value.omitted_hit_count, 1)
        self.assertGreater(value.omitted_patch_count, 0)
        with self.assertRaises(ValueError):
            analyze_depth(data, p.right, p.plane, stamp=stamp, profile=p, input_kind='LIVE')

    def test_nonunit_ray_and_small_extent_contracts_are_rejected(self):
        with self.assertRaises(ValueError):
            RayBundle((0, 0, 0), (0, 0, 0), (1, 1, 1), (1, 1, 1), 1, 1, .1, 2, True)
        with self.assertRaises(ValueError):
            TargetClass(.5, .5, .1, qualified=True)

    def test_finite_rotation_error_keeps_unit_direction_box_and_pipeline_valid(self):
        import numpy as np
        camera = CameraModel(640, 480, ((500., 0., 320.), (0., 500., 240.), (0., 0., 1.)),
            ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)), (0., 0., 0.), rotation_error_rad=.05)
        ray = axial_ray_bounds(320, 240, 1000, camera)
        self.assertIsNotNone(ray)
        self.assertTrue(all(-1 <= d.lo <= d.hi <= 1 for d in ray[1]))
        depth = np.zeros((480, 640), dtype=np.uint16)
        depth[240, 320] = 1000
        evidence = analyze_depth(depth, camera, Plane(), config=DepthConfig(max_patches=1))
        self.assertEqual(evidence.valid_count, 1)
        self.assertEqual(len(evidence.patches), 1)
        self.assertFalse(evidence.metric_qualified)

    def test_full_grid_counts_cross_block_edges_when_runtime_ray_output_is_disabled(self):
        import numpy as np
        camera = CameraModel(640, 480, ((500., 0., 320.), (0., 500., 240.), (0., 0., 1.)),
            ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (0., 0., 1.), pixel_error=0.)
        depth = np.full((480, 640), 1000, dtype=np.uint16)
        flat = depth.reshape(-1)
        flat[[0, 8191, 8192, len(flat) - 1]] = 0
        flat[[8193, len(flat) - 2]] = 500
        evidence = analyze_depth(depth, camera, Plane(),
            config=DepthConfig(max_patches=0, max_hit_boxes=1))
        self.assertEqual(evidence.valid_count, 640 * 480 - 4)
        self.assertEqual(evidence.floor_count, 640 * 480 - 6)
        self.assertEqual(evidence.invalid_count, 4)
        self.assertEqual(len(evidence.hit_boxes), 1)
        self.assertEqual(evidence.omitted_hit_count, 1)
        self.assertEqual(evidence.patches, ())
        self.assertEqual(evidence.omitted_patch_count, evidence.valid_count)
        self.assertEqual(evidence.unknown_count, 5)

    def test_translation_box_and_tilted_plane_cannot_hide_a_possible_obstacle(self):
        import numpy as np
        t, angle = .01405, .05
        camera = CameraModel(1, 1, ((500., 0., 0.), (0., 500., 0.), (0., 0., 1.)),
            ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (0., 0., 1.),
            pixel_error=0., translation_error_m=t)
        plane = Plane(normal_error_rad=angle)
        config = DepthConfig(floor_tolerance_m=.0148, obstacle_min_height_m=.015, max_patches=0)
        # 허용된 translation box 모서리와 기울어진 unit normal의 실제 높이다.
        allowed_height = t * (math.sqrt(2) * math.sin(angle) + math.cos(angle))
        self.assertGreater(allowed_height, config.obstacle_min_height_m)
        evidence = analyze_depth(np.array([[1000]], dtype=np.uint16), camera, plane, config=config)
        self.assertEqual(evidence.floor_count, 0)
        self.assertEqual(len(evidence.hit_boxes), 1)
        self.assertFalse(evidence.metric_qualified)

    def test_ray_cap_uses_two_dimensional_actual_pixel_distribution(self):
        import numpy as np
        camera = CameraModel(64, 48, ((100., 0., 32.), (0., 100., 24.), (0., 0., 1.)),
            ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (0., 0., 1.), pixel_error=0.)
        evidence = analyze_depth(np.full((48, 64), 1000, dtype=np.uint16), camera, Plane(),
                                 config=DepthConfig(max_patches=48, max_hit_boxes=0))
        self.assertEqual(evidence.valid_count, 64 * 48)
        self.assertEqual(evidence.floor_count, 64 * 48)
        self.assertEqual(len(evidence.patches), 48)
        self.assertEqual(evidence.omitted_patch_count, 64 * 48 - 48)
        pixels = [ray.pixel for ray in evidence.patches]
        self.assertEqual(len(set(pixels)), 48)
        self.assertGreater(len({p[0] for p in pixels}), 4)
        self.assertGreater(len({p[1] for p in pixels}), 4)
        self.assertEqual({(int(x >= 32), int(y >= 24)) for x, y in pixels}, {(0, 0), (0, 1), (1, 0), (1, 1)})


if __name__ == '__main__':
    unittest.main()
