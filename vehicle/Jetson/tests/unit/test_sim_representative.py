"""대표 입력 선언·결측·actual core hit/UNKNOWN·실패 보존의 software 경계 시험이다.

NN은 여기서 고정 finite tensor를 실제 decoder로 읽는 단위 fixture다. 대표 runner의
actual OAK 추론을 대체하는 PASS가 아니며 장치 실행은 별도 명시 CLI만 수행한다.
"""

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from xenova_vehicle.oak import sim_nn
from xenova_vehicle.perception.calibration import synthetic_calibration
from xenova_vehicle.perception.free_space import TargetClass
from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline, PipelineProfile
from xenova_vehicle.perception.yolop import decode_yolop
from xenova_vehicle.simulation.profile import DevelopmentProfile
from xenova_vehicle.simulation.scene import Scene, canonical, demo_scene
from xenova_vehicle.simulation import representative as subject
from xenova_vehicle.yolop_profile import YolopBenchProfile

from test_yolop_frames import packet


def profile():
    path = Path(__file__).parents[2]/'src/xenova_vehicle/simulation/fixtures/experiment-profile.json'
    return DevelopmentProfile.from_bytes(path.read_bytes())


def observed(calibration, millimeters=0):
    stamp = FrameStamp('sim-unit', 'rgb_video', 7, 'original-J-unit', (100, 101), 200)
    rgb = ImageFrame(stamp, 640, 360, 'NV12', bytes(640*360*3//2), 0)
    depth = ImageFrame(replace(stamp, stream_id='depth_right'), 640, 480, 'RAW16_LE',
        millimeters.to_bytes(2, 'little')*(640*480), 0)
    candidates = decode_yolop(packet(sequence=7, received_j_us=300))
    return PerceptionInput('SYNTHETIC', calibration.sha256, rgb, depth,
        candidates, ('sim-unit', 'rgb_video', 'original-J-unit', 7, 900_000, 1_000_000, '1'*64, rgb.sha256))


class SimRepresentativeTests(unittest.TestCase):
    def test_case_bundle_has_explicit_three_box_positions_same_geometry_and_unsupported_vehicle(self):
        original = demo_scene()
        original_bytes = original.canonical_bytes
        value = profile()
        cases = subject.representative_cases(original)
        self.assertLessEqual(len(cases), subject.MAX_CASES)
        self.assertEqual(len({case.name for case in cases}), len(cases))
        self.assertEqual({case.depth_mode for case in cases}, {'NATIVE', 'NONE', 'ZERO'})
        boxes = [case for case in cases if case.name.startswith('P03-')]
        self.assertEqual(len(boxes), 3)
        self.assertEqual(len({case.scene.sha256 for case in boxes}), 3)
        model = value.calibration_model_sha256
        calibration_sha = value.calibration().sha256
        for case in cases:
            derived = value.for_scene(case.scene)
            derived.check_scene(case.scene)
            self.assertEqual(derived.calibration_model_sha256, model)
            self.assertEqual(derived.calibration().sha256, calibration_sha)
            self.assertEqual(case.scene.fields['track']['layout']['camera'],
                original.fields['track']['layout']['camera'])
            self.assertEqual(case.scene.fields['track']['layout']['vehicle'],
                original.fields['track']['layout']['vehicle'])
        vehicle, = [case for case in cases if case.name.startswith('P06-')]
        self.assertTrue(vehicle.visual_limit.startswith('UNSUPPORTED_SCENE_ASSET'))
        self.assertFalse(vehicle.expect_depth_hit)
        self.assertEqual(original.canonical_bytes, original_bytes)

    def test_depth_faults_preserve_exact_original_rgb_stamp_and_native_bytes(self):
        calibration = synthetic_calibration()
        original = observed(calibration, 1000)
        missing = subject._depth_variant(original, 'NONE')
        zero = subject._depth_variant(original, 'ZERO')
        self.assertIsNone(missing.depth)
        for value in (missing, zero):
            self.assertIs(value.rgb, original.rgb)
            self.assertIs(value.rgb.stamp, original.rgb.stamp)
            self.assertIs(value.nn, original.nn)
            self.assertEqual(value.nn_key, original.nn_key)
            self.assertEqual(value.kind, 'SYNTHETIC')
        self.assertIs(zero.depth.stamp, original.depth.stamp)
        self.assertEqual(zero.depth.payload, bytes(640*480*2))
        self.assertEqual(original.depth.payload[:2], b'\xe8\x03')
        self.assertIs(subject._depth_variant(original, 'NATIVE'), original)

    def test_actual_core_keeps_no_bbox_depth_hits_independent_and_fresh_missing_maps_unknown(self):
        calibration = synthetic_calibration(height_m=1., forward_m=.4, pitch_rad=math.pi/2)
        pipeline_profile = PipelineProfile(permission_polygon=calibration.support_polygon,
            permission_source='UNIT_SOFTWARE_INSTALL_POLICY')
        target = (TargetClass(.03, .06, .15, qualified=True),)
        scene = demo_scene()
        for mode, millimeters in (('NONE', 0), ('ZERO', 0), ('NATIVE', 900)):
            with self.subTest(mode=mode):
                original = subject._depth_variant(observed(calibration, millimeters), mode)
                core = PerceptionPipeline(calibration, pipeline_profile, targets=target)
                result = core.process(original, now_us=1000, completed_clock=lambda: 1000)
                case = subject.RepresentativeCase('unit-'+mode, scene, (0., 0., 0.), mode,
                    expect_depth_hit=mode == 'NATIVE')
                facts = subject._metrics(case, result, original,
                    geometry_e=500_100, nn_e=5_000_100, completed_j_us=1000)
                self.assertEqual(facts['nn_vehicle_detections'], 0)
                self.assertEqual(facts['nn_decoder_counts'], original.nn.counts)
                self.assertEqual(facts['neural_layer_counts'], result.neural.counts)
                self.assertEqual(facts['omitted_hits'], 0 if result.depth is None else result.depth.omitted_hit_count)
                self.assertEqual(facts['omitted_ray_patches'], 0 if result.depth is None else result.depth.omitted_patch_count)
                self.assertEqual(facts['depth_floor_pixels'], 0 if result.depth is None else result.depth.floor_count)
                self.assertEqual(facts['depth_invalid_pixels'], 0 if result.depth is None else result.depth.invalid_count)
                self.assertEqual(facts['tape_status'], result.summary['tape_status'])
                self.assertEqual(facts['tape_reasons'], result.summary['tape_reasons'])
                self.assertEqual(facts['road_status'], result.summary['road_status'])
                self.assertEqual(facts['road_reasons'], result.summary['road_reasons'])
                self.assertEqual(facts['depth_pixel_classification_mask'], 'NOT_AVAILABLE_IN_DepthEvidence')
                self.assertFalse(facts['nn_box_miss_is_failure'])
                self.assertEqual(facts['failures'], [])
                self.assertEqual(facts['actual_frame_local_free_cells'], 0)
                if mode == 'NATIVE':
                    self.assertGreater(facts['actual_depth_hit_boxes'], 0)
                    self.assertGreater(facts['actual_frame_local_occupied_cells'], 0)
                    self.assertEqual(facts['status'], 'INCOMPLETE')
                    self.assertIn('INCOMPLETE_FREE_EVIDENCE', facts['incomplete'])
                    # GT 바꿈은 원 core 배열/NN/depth 집계 값에 관여하지 않는다.
                    fields = scene.fields
                    fields['track']['layout']['obstacle_seeds'] = []
                    other_gt = replace(case, scene=Scene(canonical(fields)))
                    self.assertEqual(facts, subject._metrics(other_gt, result, original,
                        geometry_e=500_100, nn_e=5_000_100, completed_j_us=1000))
                    import numpy as np
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory)
                        files = subject._save_map_arrays(path, result)
                        self.assertEqual(len(files), 7)
                        metadata = json.loads((path/'map-arrays.json').read_bytes())
                        self.assertFalse(metadata['gt_used'])
                        for name, array in result.map_snapshot['arrays'].items():
                            saved = np.load(path/('map-'+name+'.npy'), allow_pickle=False)
                            self.assertEqual(saved.dtype, array.dtype)
                            self.assertTrue(np.array_equal(saved, array))
                            self.assertEqual(metadata['arrays'][name]['raw_row_major_sha256'],
                                hashlib.sha256(array.tobytes(order='C')).hexdigest())
                            self.assertFalse(array.flags.writeable)
                else:
                    self.assertEqual(facts['actual_depth_hit_boxes'], 0)
                    self.assertEqual(facts['actual_frame_local_occupied_cells'], 0)
                    self.assertEqual(facts['actual_frame_local_unknown_cells'], 4096)
                self.assertEqual(facts['pixel_gt'], 'NOT_AVAILABLE')
                self.assertIsNone(facts['road_iou'])
                self.assertFalse(facts['live_authority'])

    def test_unique_output_retains_actual_asset_start_failure_without_nn_absent_fallback(self):
        value, scene = profile(), demo_scene()
        oak = YolopBenchProfile('approved-unit-oak', Path('absent-assets'), Path('absent-manifest'))
        with tempfile.TemporaryDirectory() as directory:
            directories = []
            for _ in range(2):
                with patch.object(sim_nn, 'load_yolop_asset', side_effect=ValueError('actual asset unavailable')), \
                        patch.object(sim_nn.importlib, 'import_module') as importer:
                    output, report = subject.run_representative(value, scene, oak, directory,
                        cases=subject.representative_cases(scene)[:1])
                    importer.assert_not_called()
                directories.append(output)
                self.assertEqual(report['status'], 'FAILED')
                self.assertEqual(report['exit_code'], 1)
                self.assertTrue(report['actual_nn_required'])
                self.assertEqual(report['case_count'], 0)
                self.assertTrue((output/'error.txt').is_file())
                self.assertEqual(json.loads((output/'result.json').read_bytes())['error']['type'], 'ValueError')
                self.assertEqual(hashlib.sha256((output/'profile.json').read_bytes()).hexdigest(), value.sha256)
                self.assertEqual((output/'scene.json').read_bytes(), scene.canonical_bytes)
                self.assertFalse(report['live_authority'])
            self.assertNotEqual(directories[0], directories[1])


if __name__ == '__main__':
    unittest.main()
