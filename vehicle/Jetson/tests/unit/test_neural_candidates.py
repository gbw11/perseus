"""실제 YOLOP decoder의 합성 tensor로 NN 후보의 출처·상한·보조 소비를 검사한다.

합성 score/mask는 모델 정확도나 실물 장면 수락이 아니다. NumPy가 없으면 수치
시험을 skip하며 실제 모델·SDK·카메라·파일·네트워크를 사용하지 않는다.
"""

from dataclasses import replace
import unittest

from xenova_vehicle.oak.yolop_frames import NNPacket, RuntimeTensor
from xenova_vehicle.perception.calibration import CameraModel, CalibrationProfile, Plane
from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.perception.neural import assist_road, build_neural_layer
from xenova_vehicle.perception.road import RoadResult, infer_road
from xenova_vehicle.perception.tape import TapeGraph, TapeStrip
from xenova_vehicle.perception.yolop import decode_yolop


class NeuralCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy as np
        except ImportError as error:
            raise unittest.SkipTest('기존 NumPy가 있어야 합성 NN 수치 시험을 수행함') from error
        cls.np = np
        camera = CameraModel(640, 360, ((100., 0., 319.5), (0., 100., 179.5), (0., 0., 1.)),
            ((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)), (.8, 0., 1.), pixel_error=0.,
            near_m=.01, far_m=3., qualified=True, remap_qualified=True)
        cls.calibration = CalibrationProfile(camera, camera, Plane(qualified=True),
            ((-10., -10.), (10., -10.), (10., 10.), (-10., 10.)), kind='SYNTHETIC',
            first_surface_qualified=True, time_qualified=True, minimum_target_qualified=True)

    def observation(self, *, road=None, lane=None, detections=0, captured=(100, 120), sequence=7):
        np = self.np
        values = np.zeros(894800 // 2, dtype='<f2')
        det = values[:37800].reshape(1, 6300, 6)
        for i in range(detections):
            det[0, i] = (10 + 10 * (i % 20), 80 + 8 * (i // 20), 4, 4, 1, 1)
        for label, begin in ((road, 37800), (lane, 242600)):
            head = values[begin:begin + 204800].reshape(1, 2, 320, 320)
            head[:, 0] = 1
            if label is not None:
                mask = label(np) if callable(label) else label
                head[0, 0, 70:250][mask] = 0
                head[0, 1, 70:250][mask] = 1
        tensors = (
            RuntimeTensor('det_out', 0, 801, 3, (1, 6300, 6), (75600, 12, 2), 0),
            RuntimeTensor('drive_area_seg', 0, 17185, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 75600),
            RuntimeTensor('lane_line_seg', 0, 17185, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 485200))
        packet = NNPacket('nn_output', sequence, 1000, 1001, 2000, values.tobytes(), tensors,
                          'synthetic-neural-candidate')
        candidates = decode_yolop(packet)
        stamp = FrameStamp('synthetic', 'rgb', sequence, 'rgb-clock', captured, 1300)
        frame = ImageFrame(stamp, 640, 360, 'BGR8', bytes(640 * 360 * 3))
        return PerceptionInput('SYNTHETIC', self.calibration.sha256, frame, nn=candidates,
                               nn_key=('synthetic', 'nn_output', 'opaque-nn-clock', sequence))

    def road(self, source_key):
        strips = tuple(TapeStrip(i, ((.2, y), (1.4, y)), (.02, .03), .001,
                                'observed_paint_end', 'observed_paint_end')
                       for i, y in enumerate((.6, .3, 0., -.3)))
        return infer_road(TapeGraph(strips, source_key=source_key))

    def test_source_cell_projection_uses_the_2x2_footprint_and_preserves_original_times(self):
        mask = self.np.zeros((180, 320), dtype=self.np.bool_)
        mask[100, 50] = True
        observation = self.observation(road=mask)
        layer = build_neural_layer(observation, self.calibration)
        self.assertEqual(layer.rgb_source_key, observation.rgb.stamp.key)
        self.assertEqual(layer.nn_source_key, observation.nn_key)
        self.assertEqual(layer.captured_us, (100, 120))
        self.assertEqual((layer.nn_device_us, layer.nn_sdk_us, layer.nn_received_j_us), (1000, 1001, 2000))
        self.assertEqual(layer.counts['road_source_positive_pixels'], 4)
        self.assertEqual(len(layer.road_points), 1)
        point = layer.road_points[0]
        self.assertEqual(point.cell_index, 100 * 320 + 50)
        self.assertEqual(point.source_pixel_xy, (100.5, 200.5))
        # 독립 top-down pinhole: x=.8+(u-319.5)/100, y=-(v-179.5)/100, z=0.
        nominal = (-1.39, -.21, 0.)
        self.assertTrue(all(a <= value <= b for a, value, b in zip(point.bounds.lower, nominal, point.bounds.upper)))
        self.assertGreaterEqual(point.bounds.upper[0] - point.bounds.lower[0], .019999)
        self.assertFalse(point.bounds.qualified)
        self.assertFalse(layer.geometry_qualified or layer.metric_ready or layer.live_authority or layer.free_authority)
        self.assertFalse(layer.road_mask.flags.writeable)

    def test_sampling_cap_counts_omissions_without_duplicate_source_replica_votes(self):
        observation = self.observation(road=lambda np: np.ones((180, 320), dtype=np.bool_),
                                       lane=lambda np: np.ones((180, 320), dtype=np.bool_))
        layer = build_neural_layer(observation, self.calibration, max_points=7)
        projected = layer.road_points + layer.lane_points
        self.assertEqual(layer.counts['selected_cells'], 7)
        self.assertEqual(layer.counts['omitted_at_cap'], 2 * 180 * 320 - 7)
        self.assertEqual(len(projected) + len(layer.unprojected), 7)
        keys = [(p.kind, p.cell_index) for p in projected] + [(p[0], p[1]) for p in layer.unprojected]
        self.assertEqual(len(set(keys)), 7)
        self.assertFalse(layer.sampling_complete)

    def test_source_detection_boxes_are_candidates_with_an_explicit_bound(self):
        observation = self.observation(detections=40)
        layer = build_neural_layer(observation, self.calibration)
        self.assertEqual(layer.detections_source, observation.nn.detections_source[:32])
        self.assertEqual(layer.counts['source_detections'], 40)
        self.assertEqual(layer.counts['retained_detections'], 32)
        self.assertEqual(layer.counts['omitted_detections'], 8)
        self.assertFalse(layer.road_points or layer.lane_points)

    def test_model_support_reorders_but_never_resolves_geometric_ambiguity(self):
        observation = self.observation(road=lambda np: np.indices((180, 320))[0] >= 90)
        layer = build_neural_layer(observation, self.calibration)
        road = self.road(observation.rgb.stamp.key)
        assisted = assist_road(road, layer, self.calibration)
        self.assertEqual(road.status, 'AMBIGUOUS')
        self.assertEqual(assisted.status, road.status)
        self.assertEqual(assisted.complete, road.complete)
        self.assertEqual(set(h.local_id for h in assisted.hypotheses), set(h.local_id for h in road.hypotheses))
        original = {h.local_id: h for h in road.hypotheses}
        for hypothesis in assisted.hypotheses:
            before = original[hypothesis.local_id]
            self.assertEqual(hypothesis.divider, before.divider)
            self.assertEqual(hypothesis.left_center, before.left_center)
            self.assertEqual(hypothesis.right_center, before.right_center)
            self.assertEqual(hypothesis.error_m, before.error_m)
            self.assertEqual(hypothesis.semantic_source, layer.source)
        self.assertNotEqual(assisted.hypotheses[0].local_id, road.hypotheses[0].local_id)
        self.assertGreater(assisted.hypotheses[0].semantic_support.combined_fraction,
                           assisted.hypotheses[-1].semantic_support.combined_fraction)

    def test_same_neural_source_is_idempotent_and_never_adds_votes_or_renews_capture(self):
        observation = self.observation(road=lambda np: np.ones((180, 320), dtype=np.bool_))
        layer = build_neural_layer(observation, self.calibration)
        first = assist_road(self.road(observation.rgb.stamp.key), layer, self.calibration)
        second = assist_road(first, layer, self.calibration)
        self.assertEqual(first, second)
        self.assertEqual(second.semantic_scored, len(first.hypotheses))
        self.assertEqual(layer.captured_us, observation.rgb.stamp.captured_us)
        self.assertEqual(layer.available_us, 2000)

    def test_late_nn_for_another_rgb_never_changes_the_current_road(self):
        observation = self.observation()
        layer = build_neural_layer(observation, self.calibration)
        current = self.road(replace(observation.rgb.stamp, sequence=8).key)
        self.assertIs(assist_road(current, layer, self.calibration), current)
        self.assertEqual(layer.rgb_source_key, observation.rgb.stamp.key)
        self.assertEqual(layer.captured_us, (100, 120))

    def test_65th_unscored_hypothesis_is_preserved_and_partial_scoring_is_explicit(self):
        observation = self.observation()
        layer = build_neural_layer(observation, self.calibration)
        base = self.road(observation.rgb.stamp.key).hypotheses[0]
        road = RoadResult('AMBIGUOUS', tuple(replace(base, local_id=i) for i in range(65)), True,
                          (), observation.rgb.stamp.key, False)
        result = assist_road(road, layer, self.calibration)
        self.assertEqual(len(result.hypotheses), 65)
        self.assertEqual(result.semantic_scored, 64)
        self.assertEqual(result.semantic_total, 65)
        self.assertFalse(result.semantic_complete)
        self.assertTrue(result.complete)
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertIsNone(next(h for h in result.hypotheses if h.local_id == 64).semantic_support)

    def test_missing_capture_and_opaque_nn_clock_do_not_gain_qualification(self):
        observation = self.observation(captured=None)
        layer = build_neural_layer(observation, self.calibration)
        self.assertIsNone(layer.captured_us)
        self.assertEqual(layer.nn_source_key[2], 'opaque-nn-clock')
        self.assertEqual(layer.rgb_stamp.clock_id, 'rgb-clock')
        self.assertFalse(layer.geometry_qualified or layer.metric_ready or layer.free_authority)

    def test_wrong_parent_calibration_and_invalid_budget_are_rejected(self):
        observation = self.observation()
        different_rgb = replace(observation.rgb, stamp=replace(observation.rgb.stamp, sequence=8))
        with self.assertRaises(ValueError):
            build_neural_layer(replace(observation, rgb=different_rgb), self.calibration)
        with self.assertRaises(ValueError):
            build_neural_layer(replace(observation, calibration_id='e' * 64), self.calibration)
        for cap in (True, 0, 129):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                build_neural_layer(observation, self.calibration, cap)


if __name__ == '__main__':
    unittest.main()
