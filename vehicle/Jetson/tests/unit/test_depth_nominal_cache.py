"""고정 카메라 norm/q_error 캐시와 원 block 계산의 exact 동치 회귀다.

카메라/자격/깊이는 명시 software 입력이다. 시간·물리 수락이나 실제 성능 개선을
증명하지 않으며 원 산술과 bytes/선택/자격/시각의 보존을 확인한다.
"""

from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from xenova_vehicle.perception import depth as module
from xenova_vehicle.perception.calibration import CameraModel, CalibrationProfile, Plane
from xenova_vehicle.perception.inputs import FrameStamp


def camera(width=47, height=31, *, skew=0., pixel=.5, ray=.0001, roi=None):
    return CameraModel(width, height, ((73.25, skew, width*.43), (0., 69.75, height*.57), (0., 0., 1.)),
        ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (.07, -.02, 1.), roi=roi,
        pixel_error=pixel, ray_error_rad=ray, rotation_error_rad=.002, translation_error_m=.001,
        near_m=.01, far_m=3., qualified=True, remap_qualified=True)


def original_terms(model, indices):
    """캐시/제품 계수 helper를 호출하지 않는 원 분류 수식이다."""
    import numpy as np
    k = model.k
    centered_x = np.arange(model.width, dtype=np.float64)-k[0][2]
    row_qy = (np.arange(model.height, dtype=np.float64)-k[1][2])/k[1][1]
    yy, xx = indices//model.width, indices%model.width
    qy = row_qy[yy]; qx = (centered_x[xx]-k[0][1]*qy)/k[0][0]
    q = np.stack((qx, qy, np.ones_like(qx)), axis=-1)
    pixel = math.hypot(model.pixel_error/k[1][1], model.pixel_error/k[0][0]
        + abs(k[0][1])*model.pixel_error/(k[0][0]*k[1][1]))
    delta = 2*math.sin(model.ray_error_rad/2)
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        norm = np.sqrt((q*q).sum(axis=-1))
        minimum = 1/(norm+pixel)-delta
        error = pixel+np.where(minimum>0, delta*(1+norm+pixel)/minimum, np.inf)
    return norm, error


def snapshot(evidence):
    fields = tuple(getattr(evidence, name) for name in evidence.__dataclass_fields__
        if name not in ('hit_boxes', 'patches'))
    return fields, tuple(evidence.hit_boxes), tuple(evidence.patches)


class DepthNominalCacheTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('existing NumPy is required for the exact numerical comparison')
        self.np = np; module._NOMINAL_CACHE.clear()
        self.addCleanup(module._NOMINAL_CACHE.clear)

    def test_random_skew_pixel_and_ray_errors_match_original_float64_bytes_exactly(self):
        np = self.np; rng = np.random.default_rng(411)
        models = [camera(skew=skew, pixel=pixel, ray=ray) for skew, pixel, ray in
            ((0., 0., 0.), (3.125, .5, .0001), (-9.25, 1.25, .01), (17., 2., math.pi))]
        for _ in range(6):
            value = camera(); k = ((float(rng.uniform(20, 150)), float(rng.uniform(-12, 12)), float(rng.uniform(-5, 50))),
                (0., float(rng.uniform(25, 160)), float(rng.uniform(-5, 40))), (0., 0., 1.))
            models.append(replace(value, k=k, pixel_error=float(rng.uniform(0, 2)), ray_error_rad=float(rng.uniform(0, .03))))
        for model in models:
            with self.subTest(skew=model.k[0][1], ray=model.ray_error_rad):
                indices = rng.integers(0, model.width*model.height, 111, dtype=np.int32)
                terms = module._nominal_terms(model); expected = original_terms(model, indices)
                for cached, original in zip(terms, expected):
                    self.assertEqual(cached.dtype, np.dtype(np.float64))
                    self.assertEqual(cached[indices].tobytes(), original.tobytes())

    def test_block_boundary_and_repeated_reordered_indices_keep_original_bits(self):
        np = self.np; model = camera(129, 65, skew=-2.25)
        terms = module._nominal_terms(model)
        indices = np.array([8384, 8192, 8191, 8192, 0, 17, 128, 129], dtype=np.int32)
        for cached, original in zip(terms, original_terms(model, indices)):
            self.assertEqual(cached[indices].tobytes(), original.tobytes())

    def test_roi_invalid_depth_stride_caps_and_original_stamp_match_disabled_cache(self):
        np = self.np; rng = np.random.default_rng(991)
        model = camera(33, 25, skew=2.75, roi=(1.5, 2.5, 31., 24.))
        plane = Plane(normal_error_rad=.002, offset_error_m=.001, qualified=True)
        profile = CalibrationProfile(model, model, plane, ((-3., -3.), (3., -3.), (3., 3.), (-3., 3.)),
            kind='SYNTHETIC', provenance={'measured': False, 'purpose': 'CACHE_ARITHMETIC_TEST'},
            first_surface_qualified=True, minimum_target_qualified=True, time_qualified=True,
            depth_error_m=.001, depth_relative_error=.002)
        data = rng.integers(1, 1800, size=(25, 33), dtype=np.uint16).astype(np.float64)
        data.reshape(-1)[:7] = (0., -1., np.nan, np.inf, -np.inf, 65536., 4.)
        stamp = FrameStamp('software', 'right', 7, 'original-clock', (900_000, 900_010), 1_000_000)
        for stride, hits, rays in ((1, 7, 9), (4, 11, 11), (64, 0, 0)):
            config = module.DepthConfig(stride=stride, max_hit_boxes=hits, max_patches=rays)
            args = dict(stamp=stamp, profile=profile, input_kind='SYNTHETIC', config=config)
            with patch.object(module, '_nominal_terms', return_value=None):
                original = module.analyze_depth(data, model, plane, **args)
            cached = module.analyze_depth(data, model, plane, **args)
            self.assertEqual(snapshot(cached), snapshot(original))
            self.assertIs(cached.stamp, stamp)
            self.assertEqual(cached.calibration_id, profile.sha256)

    def test_all_invalid_frame_and_unbounded_q_error_keep_unknown_and_original_counts(self):
        np = self.np
        for data, model in ((np.zeros((31, 47), dtype=np.uint16), camera()),
                            (np.ones((31, 47), dtype=np.uint16)*1000, camera(ray=math.pi))):
            args = dict(config=module.DepthConfig(max_hit_boxes=5, max_patches=0))
            with patch.object(module, '_nominal_terms', return_value=None):
                original = module.analyze_depth(data, model, Plane(), **args)
            actual = module.analyze_depth(data, model, Plane(), **args)
            self.assertEqual(snapshot(actual), snapshot(original))
            self.assertFalse(actual.metric_qualified)
            self.assertEqual(actual.mode, 'UNKNOWN')

    def test_one_camera_cache_uses_immutable_bytes_and_exact_documented_payload_budget(self):
        model = camera(640, 480)
        norm, error = module._nominal_terms(model)
        self.assertEqual(len(module._NOMINAL_CACHE), 1)
        payloads = module._NOMINAL_CACHE[model]
        self.assertEqual(tuple(map(type, payloads)), (bytes, bytes))
        self.assertEqual(sum(map(len, payloads)), 4_915_200)
        self.assertEqual(module.NOMINAL_CACHE_BYTES, 4_915_200)
        self.assertEqual(module.NOMINAL_CACHE_FREEZE_PEAK_BYTES, 7_372_800)
        again = module._nominal_terms(model)
        self.assertIs(again[0].base, norm.base); self.assertIs(again[1].base, error.base)
        for value in (norm, error):
            self.assertFalse(value.flags.writeable)
            with self.assertRaises(ValueError): value.setflags(write=True)
        second = camera(2, 2)
        module._nominal_terms(second)
        self.assertEqual(tuple(module._NOMINAL_CACHE), (second,))

    def test_previous_camera_is_evicted_before_allocating_new_complete_columns(self):
        np = self.np; module._nominal_terms(camera(4, 4)); observed = []; allocate = np.empty
        def allocation(*args, **kwargs):
            observed.append(len(module._NOMINAL_CACHE))
            return allocate(*args, **kwargs)
        with patch.object(np, 'empty', side_effect=allocation): module._nominal_terms(camera(5, 4))
        self.assertEqual(observed, [0, 0])

    def test_fixed_native_camera_hash_does_not_serialize_its_json_sha(self):
        def forbidden(_): raise AssertionError('camera SHA serialization is not a cache lookup')
        with patch.object(CameraModel, 'sha256', property(forbidden)):
            model = camera(); first = module._nominal_terms(model)
            second = module._nominal_terms(replace(model))
        self.assertIs(first[0].base, second[0].base)

    def test_larger_active_grid_uses_original_block_path_and_invalid_distortion_still_rejects(self):
        np = self.np; held = camera(2, 2); module._nominal_terms(held)
        for model in (camera(641, 2), camera(2, 481)):
            self.assertIsNone(module._nominal_terms(model))
            data = np.full((model.height, model.width), 1000, dtype=np.uint16)
            args = dict(config=module.DepthConfig(max_hit_boxes=2, max_patches=0))
            with patch.object(module, '_nominal_terms', return_value=None):
                original = module.analyze_depth(data, model, Plane(), **args)
            self.assertEqual(snapshot(module.analyze_depth(data, model, Plane(), **args)), snapshot(original))
            self.assertEqual(tuple(module._NOMINAL_CACHE), (held,))
        invalid = replace(camera(skew=0.), distortion=(.1, 0., 0., 0.))
        with self.assertRaises(ValueError):
            module.analyze_depth(np.zeros((31, 47), dtype=np.uint16), invalid, Plane())
