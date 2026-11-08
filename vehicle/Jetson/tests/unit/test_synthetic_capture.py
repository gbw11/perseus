"""합성 first-surface·원 snapshot·paired 파일 흐름의 핵심 반례.

ideal/noisy/hole 모델은 실물 stereo 평가가 아니다. 실행은 통합 환경에서 수행한다.
"""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid

from xenova_vehicle.perception.calibration import synthetic_calibration
from xenova_vehicle.perception.geometry import camera_ray
from xenova_vehicle.simulation.capture import DepthEffects, SyntheticCamera
from xenova_vehicle.simulation.dataset import SyntheticDatasetError, SyntheticDatasetReader, SyntheticDatasetWriter
from xenova_vehicle.simulation.scene import Scene, SceneSnapshot, canonical, demo_scene, oracle


class SyntheticCaptureTests(unittest.TestCase):
    def setUp(self):
        self.calibration = synthetic_calibration()
        fields = demo_scene().fields
        fields['track']['layout']['obstacle_seeds'] = []
        self.scene = Scene(canonical(fields))
        self.snapshot = SceneSnapshot(str(uuid.uuid4()), 0, 0, (2.0, 1.5, 0.0), self.scene)

    def with_box(self, ahead):
        fields = self.scene.fields
        camera = self.calibration.right
        fields['track']['layout']['obstacle_seeds'] = [dict(id='test', kind='box',
            x=self.snapshot.pose[0] + camera.translation[0] + ahead,
            y=self.snapshot.pose[1] + camera.translation[1], w=.06, h=.20)]
        return replace(self.snapshot, scene=Scene(canonical(fields)))

    def test_axial_z_first_surface_and_near_hit_never_returns_background(self):
        observation = SyntheticCamera(self.calibration).capture(self.snapshot)
        camera = self.calibration.right
        u, v = 500, 360
        ray = camera_ray(u, v, camera)
        axial_m = -camera.translation[2] / ray[2]
        self.assertAlmostEqual(int(observation.depth.array()[v, u]), axial_m * 1000, delta=.51)
        euclidean_m = axial_m * sum(value**2 for value in ray)**.5
        self.assertGreater(abs(euclidean_m - axial_m) * 1000, 5)
        visible = SyntheticCamera(self.calibration).capture(self.with_box(.4))
        background = int(observation.depth.array()[240, 320])
        self.assertGreater(background, int(visible.depth.array()[240, 320]))
        self.assertGreater(int(visible.depth.array()[240, 320]), 0)
        near = SyntheticCamera(self.calibration).capture(self.with_box(.10))
        self.assertEqual(int(near.depth.array()[240, 320]), 0)

    def test_frozen_source_time_sequence_and_seeded_holes_are_preserved(self):
        effects = DepthEffects(seed=42, noise_std_mm=2, dropout_fraction=.01, hole_rects=((310, 230, 330, 250),))
        first = SyntheticCamera(self.calibration, effects)
        observation = first.capture(self.snapshot)
        second = SyntheticCamera(self.calibration, effects).capture(self.snapshot)
        self.assertEqual(observation.depth.payload, second.depth.payload)
        self.assertEqual(int(observation.depth.array()[240, 320]), 0)
        self.assertIs(first.capture(self.snapshot), observation)
        self.assertEqual(observation.rgb.stamp.captured_us, (0, 0))
        self.assertEqual(observation.depth.stamp.sequence, observation.rgb.stamp.sequence)
        self.assertEqual(len(observation.rgb.payload), 345600)
        self.assertEqual(len(observation.depth.payload), 614400)
        self.assertIsNone(observation.nn)
        self.assertFalse(observation.live_authority)
        self.assertFalse(first.calibration.metric_qualified)
        self.assertFalse(first.calibration.free_qualified)
        self.assertEqual(observation.calibration_id, first.calibration.sha256)
        with self.assertRaises(ValueError):
            first.capture(replace(self.snapshot, pose=(2.1, 1.5, 0.0)))
        with self.assertRaises(ValueError):
            first.capture(replace(self.snapshot, sequence=1))

    def test_export_replay_checks_pair_hash_and_keeps_original_clock(self):
        observation = SyntheticCamera(self.calibration).capture(self.snapshot)
        with tempfile.TemporaryDirectory(prefix='xenova-synthetic-') as temporary:
            directory = Path(temporary) / 'capture'
            with SyntheticDatasetWriter(directory, self.calibration, scene=self.scene) as writer:
                writer.write(observation, oracle=oracle(self.snapshot))
            reader = SyntheticDatasetReader(directory)
            replayed = list(reader)
            self.assertTrue(reader.complete)
            self.assertEqual(replayed, [observation])
            self.assertFalse(hasattr(replayed[0], 'pose'))
            payload = directory / 'frames/000000-right.bin'
            raw = payload.read_bytes()
            payload.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            with self.assertRaisesRegex(SyntheticDatasetError, 'hash'):
                list(SyntheticDatasetReader(directory))

    def test_incomplete_tail_requires_explicit_prefix_and_oracle_is_not_input(self):
        observation = SyntheticCamera(self.calibration).capture(self.snapshot)
        with tempfile.TemporaryDirectory(prefix='xenova-synthetic-tail-') as temporary:
            directory = Path(temporary) / 'capture'
            writer = SyntheticDatasetWriter(directory, self.calibration, scene=self.scene)
            writer.write(observation, oracle=oracle(self.snapshot))
            writer.close(complete=False)
            with self.assertRaisesRegex(SyntheticDatasetError, 'incomplete'):
                SyntheticDatasetReader(directory)
            self.assertEqual(list(SyntheticDatasetReader(directory, allow_incomplete=True)), [observation])
            # 자기 임시 기록에만 crash 꼬리를 주입한다. 완료 receipt가 없는 indexed prefix다.
            (directory / 'result.json').unlink()
            with (directory / 'snapshots.jsonl').open('ab') as output:
                output.write(b'{"unfinished"')
            (directory / 'oracle.jsonl').write_text('invalid GT must never be opened', encoding='utf-8')
            reader = SyntheticDatasetReader(directory, allow_incomplete=True)
            self.assertFalse(reader.complete)
            self.assertEqual(list(reader), [observation])
