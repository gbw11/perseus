"""ray batch의 원 값·proof 결과 동치와 지연 소비를 확인한다."""

from dataclasses import replace
import math
import unittest
from unittest.mock import patch

import numpy as np

from xenova_vehicle.perception.calibration import CameraModel, CalibrationProfile, Plane
from xenova_vehicle.perception.depth import DepthConfig, analyze_depth
from xenova_vehicle.perception.free_space import RayBundle, TargetClass, certify_cell
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.ray_batch import RayBundleBatch


def profile(size=100):
    camera = CameraModel(size, size,
        ((100., 0., (size - 1) / 2), (0., 100., (size - 1) / 2), (0., 0., 1.)),
        ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (0., 0., 1.),
        pixel_error=0., near_m=.01, far_m=3., qualified=True, remap_qualified=True)
    return CalibrationProfile(camera, camera, Plane(qualified=True),
        ((-3., -3.), (3., -3.), (3., 3.), (-3., 3.)), kind='SYNTHETIC',
        first_surface_qualified=True, minimum_target_qualified=True, time_qualified=True)


def witness_rays():
    rays = []
    for x in (-.4, 0., .4):
        for y in (-.4, 0., .4):
            norm = math.sqrt(x * x + y * y + .75 * .75)
            direction = (x / norm, y / norm, -.75 / norm)
            measured = -1. / direction[2]
            rays.append(RayBundle((0., 0., 1.), (0., 0., 1.), direction, direction,
                measured, measured, .01, 3., True, ('synthetic', 'right', 'sim-clock', 1)))
    return tuple(rays)


class RayBatchTests(unittest.TestCase):
    def test_tuple_certificate_is_identical_without_eager_iteration_or_revalidation(self):
        rays = witness_rays() * 900
        batch = RayBundleBatch.from_rays(rays)
        self.assertEqual(tuple(batch), rays)
        target = (TargetClass(.5, .5, .5, qualified=True),)
        arguments = ((-.01, -.01, .01, .01), target, profile())
        expected = certify_cell(arguments[0], rays, *arguments[1:], max_tiles=64, max_ray_tests=256)
        self.assertEqual(expected.status, 'CLEAR_REGION')
        original_getitem = RayBundleBatch.__getitem__
        consumed = []
        def getitem(value, index):
            consumed.append(index)
            return original_getitem(value, index)
        with (patch.object(RayBundleBatch, '__iter__', side_effect=AssertionError('eager expansion')),
                patch.object(RayBundle, '__post_init__', side_effect=AssertionError('revalidation')),
                patch.object(RayBundleBatch, '__getitem__', getitem)):
            actual = certify_cell(arguments[0], batch, *arguments[1:], max_tiles=64, max_ray_tests=256)
        self.assertEqual(actual, expected)
        self.assertEqual(len(consumed), len(set(consumed)))
        self.assertLess(len(consumed), len(batch))
        # 원 pixel bin과 누락 자격을 섞어도 후보 순서·work·source가 tuple 경로와 같다.
        mixed = tuple(replace(ray, pixel=(float(i * 8), float(i)),
            qualified=i != 4, source_key=('source', i)) for i, ray in enumerate(witness_rays()))
        batch = RayBundleBatch.from_rays(mixed)
        self.assertEqual(tuple(batch), mixed)
        for cap in (1, 256):
            self.assertEqual(certify_cell(arguments[0], mixed, *arguments[1:], max_ray_tests=cap),
                certify_cell(arguments[0], batch, *arguments[1:], max_ray_tests=cap))

    def test_storage_has_no_mutable_array_alias_and_empty_stays_empty(self):
        origin = np.array([[0., 0., 1.]])
        direction = np.array([[0., 0., -1.]])
        measured, near, far = np.array([1.]), np.array([.01]), np.array([3.])
        flags, pixels = np.array([True]), np.array([[10., 20.]])
        batch = RayBundleBatch(origin, origin, direction, direction,
            measured, measured, near, far, flags, ('source', 1), pixels)
        original = batch[0]
        for array in (origin, direction, measured, near, far, pixels):
            array.fill(99)
        flags.fill(False)
        self.assertEqual(batch[0], original)
        for array in (batch.numbers, batch.pixels, batch.qualified, batch.pixel_present):
            with self.assertRaises(ValueError):
                array.setflags(write=True)
        with self.assertRaises(TypeError):
            batch.bins[(0, 0)] = (0,)
        empty = RayBundleBatch.from_rays(())
        self.assertEqual(tuple(empty), ())
        self.assertEqual(empty.accepted_indices, ())

    def test_pixel_inventory_keeps_missing_negative_extreme_values_and_bin_order(self):
        base = witness_rays()[0]
        huge = float.fromhex('0x1.fffffffffffffp+1023')
        points = (None, (-.01, -8.), (0., 0.), (7.999, 8.), (8., 8.), (0., 0.),
                  (float(2 ** 67), -float(2 ** 70)), (huge, -huge),
                  (math.nextafter(8., -math.inf), math.nextafter(-8., -math.inf)))
        rays = tuple(replace(base, pixel=pixel, qualified=i != 4,
                             source_key=None if i % 2 else ('source', i))
                     for i, pixel in enumerate(points))
        batch = RayBundleBatch.from_rays(rays)
        accepted = tuple(i for i, ray in enumerate(rays) if ray.qualified)
        pixels = tuple(rays[i].pixel for i in accepted)
        bins = {}
        for i, pixel in enumerate(pixels):
            if pixel is not None:
                bins.setdefault(tuple(math.floor(v / 8) for v in pixel), []).append(i)
        self.assertEqual(batch.accepted_indices, accepted)
        self.assertEqual(batch.accepted_pixels, pixels)
        self.assertEqual(list(batch.bins.items()), [(k, tuple(v)) for k, v in bins.items()])
        self.assertEqual(tuple(batch), rays)
        self.assertTrue(all(type(i) is int for i in batch.accepted_indices))
        self.assertTrue(all(pixel is None or (type(pixel) is tuple
            and all(type(v) is float for v in pixel)) for pixel in batch.accepted_pixels))
        self.assertIsNone(batch.accepted_pixels[0])

    def test_all_present_maximum_rows_preserve_raw_storage_source_and_readonly_views(self):
        count = 8192
        origin = np.tile((0., 0., 1.), (count, 1))
        direction = np.tile((0., 0., -1.), (count, 1))
        measured, near, far = np.ones(count), np.full(count, .01), np.full(count, 3.)
        pixels = np.column_stack((np.arange(count) * 16., np.zeros(count)))
        batch = RayBundleBatch(origin, origin, direction, direction,
            measured, measured, near, far, True, None, pixels)
        expected = np.column_stack((origin, origin, direction, direction,
            measured, measured, near, far, pixels)).astype('<f8').tobytes()
        self.assertEqual(batch.numbers.tobytes(), expected)
        self.assertEqual(batch.accepted_indices, tuple(range(count)))
        self.assertEqual(batch.accepted_pixels, tuple((i * 16., 0.) for i in range(count)))
        self.assertEqual(batch.bins[(0, 0)], (0,))
        self.assertEqual(batch.bins[((count - 1) * 2, 0)], (count - 1,))
        self.assertIsNone(batch[-1].source_key)
        for array in (batch.numbers, batch.pixels, batch.qualified, batch.pixel_present):
            with self.assertRaises(ValueError):
                array.setflags(write=True)

    def test_certificate_preserves_ties_work_empty_allfalse_and_absent_pixels(self):
        originals = witness_rays()
        target = (TargetClass(.5, .5, .5, qualified=True),)
        cell, calibration = (-.01, -.01, .01, .01), profile()
        variants = (
            (),
            tuple(replace(ray, qualified=False, pixel=(48., 48.)) for ray in originals),
            tuple(replace(ray, pixel=None, source_key=None) for ray in originals),
            tuple(replace(ray, pixel=(48. if i % 2 else 56., 48.),
                          qualified=i != 4, source_key=None if i % 2 else ('source', i))
                  for i, ray in enumerate(originals)),
        )
        for rays in variants:
            batch = RayBundleBatch.from_rays(rays)
            self.assertEqual(batch.accepted_indices, tuple(i for i, ray in enumerate(rays) if ray.qualified))
            self.assertEqual(batch.accepted_pixels, tuple(ray.pixel for ray in rays if ray.qualified))
            for cap in (0, 1, 256):
                with self.subTest(count=len(rays), cap=cap):
                    expected = certify_cell(cell, rays, target, calibration, max_ray_tests=cap)
                    actual = certify_cell(cell, batch, target, calibration, max_ray_tests=cap)
                    self.assertEqual(actual, expected)
        self.assertEqual(certify_cell(cell, variants[1], target, calibration).reason,
                         'NO_ACCEPTED_RAY_BUNDLE')

    def test_boundary_rejects_nonfinite_reversed_nonunit_and_invalid_identity(self):
        def columns(count=1):
            return [np.tile((0., 0., 1.), (count, 1)), np.tile((0., 0., 1.), (count, 1)),
                np.tile((0., 0., -1.), (count, 1)), np.tile((0., 0., -1.), (count, 1)),
                np.ones(count), np.ones(count), np.full(count, .01), np.full(count, 3.)]
        invalid = []
        values = columns(); values[0][0, 0] = np.nan; invalid.append(values)
        values = columns(); values[0][0, 0] = 1.; invalid.append(values)
        values = columns(); values[2][:] = (1., 1., 1.); values[3][:] = (1., 1., 1.); invalid.append(values)
        values = columns(); values[6][0] = 3.; invalid.append(values)
        values = columns(); values[4][0] = 2.; invalid.append(values)
        invalid.append(columns(8193))
        for index, values in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(ValueError):
                RayBundleBatch(*values)
        for options in ({'qualified': 1}, {'source_key': ('source', True)},
                        {'pixels': np.array([[np.inf, 0.]])}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                RayBundleBatch(*columns(), **options)

    def test_analyze_depth_keeps_original_rows_and_creates_no_scalar_ray(self):
        calibration = profile(5)
        stamp = FrameStamp('synthetic', 'right', 7, 'simulation-clock', (100, 100), 100)
        image = np.full((5, 5), 1000, dtype=np.uint16)
        image[0, 0] = 0
        with patch.object(RayBundle, '__post_init__', side_effect=AssertionError('eager scalar DTO')):
            evidence = analyze_depth(image, calibration.right, calibration.plane, stamp=stamp,
                profile=calibration, input_kind='SYNTHETIC')
        self.assertIs(type(evidence.patches), RayBundleBatch)
        self.assertIs(evidence.stamp, stamp)
        self.assertEqual((evidence.valid_count, evidence.invalid_count, evidence.floor_count), (24, 1, 24))
        self.assertEqual(evidence.omitted_patch_count, 0)
        self.assertEqual([ray.pixel for ray in evidence.patches],
            [(float(i % 5), float(i // 5)) for i in range(1, 25)])
        self.assertTrue(all(ray.source_key == stamp.key and ray.qualified for ray in evidence.patches))
        disabled = analyze_depth(image, calibration.right, calibration.plane,
            config=DepthConfig(max_patches=0))
        self.assertEqual(disabled.patches, ())


if __name__ == '__main__':
    unittest.main()
