"""개발 sensor의 원 wall 표본·만료·GT 격리와 실제 first-surface 렌더를 검사한다.

주입 clock은 순서/기한 반례용이다. 실제 Nano 렌더 FPS나 물리 보정 수락이 아니다.
"""

from dataclasses import fields, replace
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from xenova_vehicle.simulation.capture import DepthEffects
from xenova_vehicle.simulation.profile import DevelopmentProfile
from xenova_vehicle.simulation.scene import demo_scene
from xenova_vehicle.simulation.wall_sensor import (WallCapture, WallDiagnosticCapture,
    WallPoseSample, WallSyntheticCamera)


EPOCH = '11111111-1111-4111-8111-111111111111'
CLOCK_EPOCH = '22222222-2222-4222-8222-222222222222'


class Clock:
    def __init__(self, now=2_000_010):
        self.now = now

    def __call__(self):
        return self.now


def fixture(effects=DepthEffects()):
    from xenova_vehicle.simulation import profile as module
    profile = DevelopmentProfile.from_bytes(
        (Path(module.__file__).parent / 'fixtures' / 'experiment-profile.json').read_bytes())
    clock = Clock()
    sensor = WallSyntheticCamera(profile, demo_scene(), epoch=EPOCH,
                                 clock_epoch=CLOCK_EPOCH, effects=effects, clock=clock)
    sample = WallPoseSample(EPOCH, 7, (2_000_000, 2_000_005), (2.81, .7, 3.141592653589793))
    return sensor, sample, clock


class WallSensorTests(unittest.TestCase):
    def test_original_wall_sample_is_kept_in_actual_rgb_right_and_receipt_has_no_gt(self):
        sensor, sample, clock = fixture()
        capture = sensor.capture(sample, expires_j_us=2_500_000)
        value = capture.observation
        self.assertEqual(value.kind, 'SYNTHETIC')
        self.assertFalse(value.live_authority)
        self.assertEqual(value.calibration_id, sensor.calibration.sha256)
        self.assertEqual(value.capture_interval, sample.captured_j_us)
        self.assertEqual(value.rgb.stamp.source_id, 'sim-wall:' + EPOCH)
        self.assertEqual(value.rgb.stamp.clock_id, 'jetson-monotonic:' + CLOCK_EPOCH)
        self.assertEqual(value.rgb.stamp.available_us, clock.now)
        self.assertEqual(value.rgb.stamp.sequence, value.depth.stamp.sequence)
        self.assertEqual((len(value.rgb.payload), len(value.depth.payload)), (345_600, 614_400))
        self.assertEqual(value.rgb.array().shape, (360, 640, 3))
        self.assertGreater(int(value.depth.array().max()), 0)
        self.assertIsNone(value.nn)
        self.assertEqual(capture.receipt['captured_j_us'], ['2000000', '2000005'])
        self.assertEqual(capture.receipt['rgb_sha256'], value.rgb.sha256)
        self.assertFalse({'pose', 'scene', 'parking_slots', 'free', 'goal'} & set(capture.receipt))
        self.assertEqual({f.name for f in fields(WallCapture)},
            {'observation', 'profile_sha256', 'captured_j_us', 'completed_j_us', 'expires_j_us'})
        with self.assertRaisesRegex(ValueError, 'already consumed'):
            sensor.capture(sample, expires_j_us=2_500_000)
        sensor.close()

    def test_delayed_renderer_keeps_original_expiry_and_cannot_retry_same_sample(self):
        sensor, sample, clock = fixture()
        render = sensor._renderer.capture
        def delayed(snapshot):
            result = render(snapshot)
            clock.now = 2_500_000
            return result
        with patch.object(sensor._renderer, 'capture', side_effect=delayed) as wrapped:
            self.assertIsNone(sensor.capture(sample, expires_j_us=2_500_000))
            self.assertEqual(wrapped.call_count, 1)
        self.assertEqual(sensor.counts['expired_after_render'], 1)
        with self.assertRaisesRegex(ValueError, 'already consumed'):
            sensor.capture(sample, expires_j_us=2_500_000)
        # 새 sample도 원 E가 이미 끝났다면 렌더/native 계산을 하지 않는다.
        clock.now = 3_000_000
        next_sample = replace(sample, sequence=8, captured_j_us=(2_500_001, 2_500_002))
        with patch.object(sensor._renderer, 'capture', side_effect=AssertionError('expired render')):
            self.assertIsNone(sensor.capture(next_sample, expires_j_us=3_000_000))
        self.assertEqual(sensor.counts['expired_before_render'], 1)
        sensor.close()

    def test_wrong_session_future_or_extended_lifetime_do_not_consume_original(self):
        sensor, sample, clock = fixture()
        with self.assertRaises(ValueError):
            sensor.capture(replace(sample, epoch=CLOCK_EPOCH), expires_j_us=2_500_000)
        with self.assertRaises(ValueError):
            sensor.capture(sample, expires_j_us=2_500_001)
        clock.now = 1_999_999
        with self.assertRaisesRegex(ValueError, 'future'):
            sensor.capture(sample, expires_j_us=2_500_000)
        clock.now = 2_000_010
        self.assertIsNotNone(sensor.capture(sample, expires_j_us=2_500_000))
        self.assertEqual(sensor.counts['attempted'], 1)
        with self.assertRaises(ValueError):
            sensor.capture(replace(sample, sequence=8), expires_j_us=2_500_000)
        sensor.close()

    def test_expired_diagnostic_retains_original_pixels_time_and_separate_drop_fact(self):
        sensor, sample, clock = fixture()
        render = sensor._renderer.capture
        rendered = []
        def delayed(snapshot):
            result = render(snapshot)
            rendered.append(result)
            clock.now = 2_600_000
            return result
        with patch.object(sensor._renderer, 'capture', side_effect=delayed):
            capture = sensor.render_diagnostic(sample, expires_j_us=2_500_000)
        self.assertIs(type(capture), WallDiagnosticCapture)
        self.assertTrue(capture.expired)
        self.assertEqual(capture.expires_j_us, 2_500_000)
        self.assertEqual(capture.observation.capture_interval, sample.captured_j_us)
        self.assertEqual(capture.completed_j_us, 2_600_000)
        self.assertIs(capture.observation.rgb.payload, rendered[0].rgb.payload)
        self.assertIs(capture.observation.depth.payload, rendered[0].depth.payload)
        self.assertTrue(capture.receipt['expired'])
        self.assertTrue(capture.receipt['diagnostic_only'])
        self.assertFalse(capture.receipt['live_authority'])
        self.assertEqual(sensor.counts['emitted'], 0)
        self.assertEqual(sensor.counts['diagnostic_emitted'], 1)
        self.assertEqual(sensor.counts['expired_after_render'], 1)
        with self.assertRaises(ValueError):
            WallCapture(capture.observation, capture.profile_sha256, capture.captured_j_us,
                        capture.completed_j_us, capture.expires_j_us)
        with self.assertRaises(ValueError):
            sensor.capture(sample, expires_j_us=2_500_000)
        sensor.close()

    def test_noisy_sensor_uses_its_own_unqualified_calibration_without_live_relabel(self):
        sensor, sample, _ = fixture(DepthEffects(seed=42, noise_std_mm=2))
        capture = sensor.capture(sample, expires_j_us=2_500_000)
        self.assertFalse(sensor.calibration.metric_qualified)
        self.assertFalse(sensor.calibration.free_qualified)
        self.assertEqual(capture.observation.kind, 'SYNTHETIC')
        self.assertEqual(capture.observation.calibration_id, sensor.calibration.sha256)
        with self.assertRaises(ValueError):
            replace(capture, completed_j_us=capture.expires_j_us)
        with self.assertRaises(ValueError):
            replace(capture, observation=replace(capture.observation, kind='LIVE'))
        sensor.close()

    def test_pose_brackets_owner_and_close_are_explicit(self):
        ticks = iter((10, 13))
        value = WallPoseSample.sample(EPOCH, 1, lambda: (1., 2., .3), clock=lambda: next(ticks))
        self.assertEqual(value.captured_j_us, (10, 14))
        coarse = WallPoseSample.sample(EPOCH, 2, lambda: (1., 2., .3), clock=lambda: 20)
        self.assertEqual(coarse.captured_j_us, (20, 21))
        self.assertEqual(coarse.pose, value.pose)
        backward = iter((20, 19))
        with self.assertRaisesRegex(ValueError, 'sampling clock regressed'):
            WallPoseSample.sample(EPOCH, 3, lambda: value.pose, clock=lambda: next(backward))
        with self.assertRaises(ValueError):
            replace(value, captured_j_us=(13, 10))
        with self.assertRaises(ValueError):
            replace(value, pose=(True, 2., 3.))
        with self.assertRaises(ValueError):
            replace(value, pose=(float('nan'), 2., 3.))
        sensor, sample, _ = fixture()
        errors = []
        def other_owner():
            try:
                sensor.capture(sample, expires_j_us=2_500_000)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=other_owner)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ['wall sensor belongs to one renderer thread'])
        self.assertEqual(sensor.counts['attempted'], 0)
        sensor.close()
        sensor.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            sensor.capture(sample, expires_j_us=2_500_000)

    def test_motion_binding_is_selected_before_first_sensor_capture(self):
        from xenova_vehicle.simulation.motion_profile import DevelopmentMotionProfile
        original_sensor, sample, clock = fixture()
        profile = original_sensor._profile
        original_sensor.close()
        # 명시 software fixture다. 실제 주행 오차/시계 수락이나 설치값이 아니다.
        motion = DevelopmentMotionProfile(profile, encoder_bits=64, metres_per_tick_upper=.0002,
            maximum_speed_mps=2., maximum_clock_error_us=1000, maximum_sample_age_us=500_000,
            sample_period_ms=10, position_error_m=.001, heading_error_rad=.001,
            distance_error_ratio=.01, heading_error_per_m=.01, vehicle_max_age_us=500_000,
            max_yaw_rate_rps=4., max_gap_us=20_000, pose_capacity=256, timestamp_error_us=100,
            drift_ppm=100, max_offset_width_us=10_000, lease_us=500_000, lookback_us=500_000)
        sensor = WallSyntheticCamera(profile, demo_scene(), epoch=EPOCH, clock_epoch=CLOCK_EPOCH,
                                     clock=clock, motion_profile=motion)
        capture = sensor.capture(sample, expires_j_us=2_500_000)
        self.assertEqual(sensor.motion_profile_sha256, motion.sha256)
        self.assertEqual(sensor.calibration.vehicle_bundle_sha256, motion.sha256)
        self.assertEqual(capture.observation.calibration_id, motion.bound_calibration.sha256)
        self.assertNotEqual(capture.observation.calibration_id, profile.calibration().sha256)
        self.assertEqual(capture.observation.kind, 'SYNTHETIC')
        self.assertEqual(capture.observation.capture_interval, sample.captured_j_us)
        self.assertEqual(capture.observation.rgb.stamp.clock_id, 'jetson-monotonic:' + CLOCK_EPOCH)
        self.assertIsNone(profile.calibration().vehicle_bundle_sha256)
        sensor.close()
        with self.assertRaisesRegex(ValueError, 'effects changed'):
            WallSyntheticCamera(profile, demo_scene(), epoch=EPOCH, clock_epoch=CLOCK_EPOCH,
                effects=DepthEffects(noise_std_mm=2), clock=clock, motion_profile=motion)
