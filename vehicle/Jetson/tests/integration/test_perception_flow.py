"""paired 합성 입력→실제 core→진단과 원 geometry/late-NN/UNKNOWN 경계.

합성 target/permission/보정과 opt-in oracle은 실물 수락이나 이동 허가가 아니다.
NN은 고정 유한 FP16 fixture를 실제 decoder에 넣으며 모델 정확도를 평가하지 않는다.
"""

from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
import uuid

from xenova_vehicle.mapping.local_map import GridSpec
from xenova_vehicle.oak.yolop_frames import NNPacket, RuntimeTensor
from xenova_vehicle.perception.bench import run_perception_bench
from xenova_vehicle.perception.calibration import CalibrationProfile, synthetic_calibration
from xenova_vehicle.perception.free_space import TargetClass
from xenova_vehicle.perception.pipeline import PerceptionPipeline, PipelineProfile
from xenova_vehicle.perception.yolop import decode_yolop
from xenova_vehicle.simulation import Scene, SyntheticCamera, SceneSnapshot, demo_scene
from xenova_vehicle.simulation.scene import canonical as scene_canonical


def _nn(sequence, at):
    """세 runtime tensor의 유효한 zero FP16 bytes. 판정값/geometry 결과를 만들지 않는다."""
    tensors = (
        RuntimeTensor('det_out', 0, 801, 3, (1, 6300, 6), (75600, 12, 2), 0),
        RuntimeTensor('drive_area_seg', 0, 17185, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 75600),
        RuntimeTensor('lane_line_seg', 0, 17185, 4, (1, 2, 320, 320), (409600, 204800, 640, 2), 485200))
    packet = NNPacket('nn_output', sequence, at, at, at + 1, bytes(894800), tensors, 'synthetic-late-nn-fixture')
    return decode_yolop(packet)


class PerceptionFlowTests(unittest.TestCase):
    def setUp(self):
        self.calibration = synthetic_calibration()
        scene = demo_scene()
        pose = tuple(scene.fields['track']['layout']['start_pose'])
        self.observation = SyntheticCamera(self.calibration).capture(SceneSnapshot(str(uuid.uuid4()), 0, 0, pose, scene))
        self.profile = PipelineProfile(grid=GridSpec(16, 16, .1, (0., -.8)), max_free_cells=1,
            max_free_ray_tests=32, permission_polygon=self.calibration.support_polygon,
            permission_source='SYNTHETIC_INSTALL_POLICY')
        self.targets = (TargetClass(.03, .06, .15, qualified=True),)

    def test_export_core_diagnostics_and_oracle_optin_are_separate(self):
        with tempfile.TemporaryDirectory(prefix='xenova-perception-flow-') as temporary:
            root = Path(temporary)
            first = run_perception_bench(root / 'frame-local', frames=1)
            self.assertTrue(first['complete'], first['error'])
            self.assertEqual(first['frames_processed'], 1)
            self.assertEqual(first['diagnostic_images'], 2)
            self.assertIsNone(first['last_summary']['odom_origin'])
            self.assertTrue(first['last_summary']['map_frame'].startswith('frame:'))
            dataset = root / 'frame-local/dataset'
            original_manifest = (dataset / 'manifest.json').read_bytes()
            with self.assertRaises(FileExistsError):
                run_perception_bench(root / 'frame-local', frames=1)
            second = run_perception_bench(root / 'oracle', dataset=dataset, frames=1, oracle_pose=True)
            self.assertTrue(second['complete'], second['error'])
            self.assertEqual(second['pose_mode'], 'ORACLE_SIMULATION')
            self.assertEqual(second['last_summary']['odom_origin'], 'ORACLE_SIMULATION')
            self.assertTrue(second['last_summary']['map_frame'].startswith('odom:'))
            self.assertEqual((dataset / 'manifest.json').read_bytes(), original_manifest)
            self.assertFalse(second['metric_ready'] or second['world_valid'] or second['live_authority'])
            final = json.loads((root / 'oracle/perception-bench-result.json').read_bytes())
            self.assertEqual(final['complete'], second['complete'])
            self.assertEqual(len(list((root / 'oracle').glob('*.png'))), 2)

    def test_duplicate_geometry_and_late_nn_do_not_reobserve_or_renew_capture(self):
        import numpy as np
        pipeline = PerceptionPipeline(self.calibration, self.profile, targets=self.targets)
        first = pipeline.process(self.observation)
        repeated = pipeline.process(self.observation)
        self.assertFalse(repeated.summary['rgb_updated'] or repeated.summary['depth_updated'])
        self.assertEqual(first.summary['map_revision'], repeated.summary['map_revision'])
        late = replace(self.observation, nn=_nn(0, 0), nn_key=('synthetic', 'nn_output', 'clock', 0))
        processed = pipeline.process(late, now_us=1)
        self.assertTrue(processed.summary['nn_updated'])
        self.assertFalse(processed.summary['rgb_updated'] or processed.summary['depth_updated'])
        self.assertEqual(processed.summary['counts']['rgb'], 1)
        self.assertEqual(processed.summary['counts']['depth'], 1)
        self.assertEqual(processed.summary['counts']['nn'], 1)
        self.assertEqual(processed.summary['captured_us'], (0, 0))
        self.assertEqual(processed.summary['map_revision'], first.summary['map_revision'])
        self.assertTrue(np.array_equal(processed.map_snapshot['arrays']['state'], first.map_snapshot['arrays']['state']))

    def test_depth_holes_and_provisional_profile_never_become_free(self):
        hole = replace(self.observation, depth=replace(self.observation.depth, payload=bytes(614400)))
        result = PerceptionPipeline(self.calibration, self.profile, targets=self.targets).process(hole)
        self.assertEqual(result.depth.invalid_count, 640 * 480)
        self.assertFalse(result.depth.hit_boxes)
        self.assertEqual(result.summary['map_counts']['free'], 0)
        self.assertEqual(result.summary['map_counts']['occupied'], 0)
        self.assertEqual(result.summary['map_counts']['unknown'], 16 * 16)
        fields = self.calibration.to_dict()
        fields['kind'] = 'PROVISIONAL'
        provisional = CalibrationProfile.from_dict(fields)
        unknown = replace(hole, calibration_id=provisional.sha256)
        result = PerceptionPipeline(provisional, self.profile, targets=self.targets).process(unknown)
        self.assertEqual(result.depth.mode, 'UNKNOWN')
        self.assertFalse(result.summary['geometric_model_qualified'])
        self.assertEqual(result.summary['map_counts']['free'], 0)

    def test_empty_scene_center_cell_has_conditional_free_with_original_target_and_quantization(self):
        # demo의 차폐/FOV 조건과 분리한 독립 입력이다. 실제 demo 주행 성공을 뜻하지 않는다.
        original = demo_scene()
        fields = original.fields
        fields['track']['layout']['obstacle_seeds'] = []
        empty = Scene(scene_canonical(fields))
        self.assertNotEqual(empty.sha256, original.sha256)
        camera = SyntheticCamera(self.calibration)
        snapshot = SceneSnapshot(str(uuid.uuid4()), 0, 0,
            tuple(fields['track']['layout']['start_pose']), empty)
        observation = camera.capture(snapshot)
        self.assertEqual(camera.calibration.sha256, self.calibration.sha256)
        self.assertGreaterEqual(camera.calibration.depth_error_m, .0005)
        self.assertEqual([(t.core_radius_m, t.core_height_m, t.max_extent_m) for t in self.targets],
                         [(.03, .06, .15)])
        profile = replace(self.profile, free_focus_xy=(1., 0.), max_free_ray_tests=200_000)
        # pose/Scene/정답은 core에 전달하지 않는다. renderer의 RGB/native RIGHT만 쓴다.
        result = PerceptionPipeline(camera.calibration, profile, targets=self.targets).process(observation)
        self.assertTrue(result.summary['map_frame'].startswith('frame:'))
        self.assertIsNone(result.summary['input_odom_origin'])
        self.assertEqual(result.summary['captured_us'], (0, 0))
        self.assertEqual(result.summary['map_counts']['free'], 1)
        self.assertEqual(len(result.summary['free_certificates']), 1)
        certificate = result.summary['free_certificates'][0]
        self.assertEqual(certificate['status'], 'CLEAR_REGION', certificate)
        self.assertTrue(certificate['complete'])
        self.assertGreater(certificate['work'], 0)
        self.assertLessEqual(certificate['work'], profile.max_free_ray_tests)
        self.assertFalse(result.summary['metric_ready'] or result.summary['world_valid']
                         or result.summary['live_authority'])
        self.assertEqual(result.summary['free_temporal_status'], 'INSTANTANEOUS_SYNTHETIC')
        extended=replace(observation,
            rgb=replace(observation.rgb,stamp=replace(observation.rgb.stamp,captured_us=(0,100),available_us=100)),
            depth=replace(observation.depth,stamp=replace(observation.depth.stamp,captured_us=(0,100),available_us=100)))
        unbound=PerceptionPipeline(camera.calibration,profile,targets=self.targets).process(extended)
        self.assertEqual(unbound.summary['free_temporal_status'], 'MOTION_UNQUALIFIED')
        self.assertEqual(unbound.summary['map_counts']['free'], 0)
        self.assertFalse(unbound.summary['free_certificates'])

    def test_default_mount_rgb_measures_three_tapes_and_forms_two_lane_hypothesis(self):
        from xenova_vehicle.perception.road import infer_road
        from xenova_vehicle.perception.tape import TapeProfile, extract_tape

        # renderer 전용 독립 장면이다. 원 demo의 차폐물·주차 교차선·곡선 검출을 주장하지 않는다.
        fields = demo_scene().fields
        fields['track']['centerline'] = [[-1., -2.], [4., -2.], [4., 2.], [-1., 2.]]
        fields['track']['lane_width_m'] = .6
        layout = fields['track']['layout']
        layout['bounds'] = [-1., -2.5, 4., 2.5]
        layout['start_pose'] = [0., 0., 0.]
        layout['obstacle_seeds'] = []
        layout['parking_slots'] = []
        layout['paved_areas'] = [[[-.5, -.8], [2.5, -.8], [2.5, .8], [-.5, .8]]]
        layout['paint_lines'] = [[[.55, y], [1.30, y]] for y in (-.3, 0., .3)]
        scene = Scene(scene_canonical(fields))
        camera = SyntheticCamera(synthetic_calibration())
        calibration = camera.calibration
        self.assertEqual(calibration.rgb.translation, (.20, 0., .12))
        self.assertEqual((calibration.rgb.width, calibration.rgb.height), (640, 360))
        self.assertEqual((calibration.right.width, calibration.right.height), (640, 480))
        self.assertEqual(calibration.provenance['pitch_rad'], math.radians(15.))
        self.assertEqual(calibration.provenance['hfov_deg'], 69.)
        observation = camera.capture(SceneSnapshot(str(uuid.uuid4()), 0, 0, (0., 0., 0.), scene))
        # core에는 Scene/pose/정답 선분을 넘기지 않고 NV12에서 변환한 실제 RGB만 전달한다.
        profile = TapeProfile()
        tape = extract_tape(observation.rgb.array(), calibration.rgb, calibration.plane, profile,
                            stamp=observation.rgb.stamp)
        road = infer_road(tape)
        self.assertEqual(tape.source_key, observation.rgb.stamp.key)
        self.assertGreaterEqual(len(tape.strips), 3, tape.descriptor())
        self.assertTrue(all(strip.observed and strip.width_interval_m[1] > 0 for strip in tape.strips))
        self.assertIn(road.status, ('VALID', 'PARTIAL'), road.descriptor())
        self.assertGreater(len(road.hypotheses), 0)
        self.assertIsNone(tape.nn_candidates)
