"""개발 wheel producer의 순수 회귀와 별도 B08 paired codec 선택 시험.

앞 9개는 실제 운동값을 명시한 software fixture이며 serial/clock install 수락이 아니다.
StructInjectedFeedbackTests의 packet은 COBS/CRC가 없는 시험 투영이다. 마지막 클래스는
전체 B08 Gateway/common이 함께 제공된 실행에서만 선택하며 primary feedback2로 대체하지 않는다.
"""

from dataclasses import FrozenInstanceError
import importlib
import math
import struct
import unittest
from uuid import UUID

from xenova_vehicle.simulation.feedback import (
    MAX_SEQUENCE, RearWheelFeedback, SimulationFeedbackError, WheelSample,
)


PI = str(UUID(int=1))
DEVICE = str(UUID(int=2))
EPOCH = str(UUID(int=3))


def producer(**changes):
    # 미실측 가상값이다. 제품은 어떤 geometry/error 기본값도 선택하지 않는다.
    values = dict(metres_per_tick=1 / 1024, track_width_m=.125, encoder_bits=32,
                  steering_error_rad=.01, pi_session=PI, device_session=DEVICE)
    values.update(changes)
    return RearWheelFeedback(**values)


def initial_sample(owner, stamp=1_000_020):
    owner.advance_motion(0., 0., 0.)
    return owner.latch(stamp)


class SimulationWheelFeedbackTests(unittest.TestCase):
    def test_original_configuration_is_readonly_and_motion_keeps_its_original_scale(self):
        owner = producer()
        original = dict(metres_per_tick=1 / 1024, track_width_m=.125, encoder_bits=32,
                        steering_error_rad=.01, pi_session=PI, device_session=DEVICE)
        changes = dict(metres_per_tick=1., track_width_m=1., encoder_bits=64,
                       steering_error_rad=.5, pi_session=str(UUID(int=8)), device_session=str(UUID(int=9)))
        for name, value in changes.items():
            with self.subTest(name=name), self.assertRaises(AttributeError):
                setattr(owner, name, value)
        self.assertEqual({name: getattr(owner, name) for name in original}, original)
        owner.advance_motion(.25, .125, math.atan(.12))
        self.assertEqual(owner.ticks, (248, 264))
        self.assertEqual((owner.pi_session, owner.device_session), (PI, DEVICE))

    def test_geometry_width_uncertainty_and_original_sessions_are_explicit(self):
        bad = (dict(metres_per_tick=0), dict(track_width_m=True), dict(encoder_bits=16),
               dict(encoder_bits=True), dict(steering_error_rad=0), dict(pi_session=str(UUID(int=0))),
               dict(device_session=DEVICE.upper().replace('2', 'A')),
               dict(metres_per_tick=10**1000), dict(initial_left_ticks=2**31))
        for changes in bad:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(SimulationFeedbackError):
                    producer(**changes)
        with self.assertRaises(TypeError):
            RearWheelFeedback(pi_session=PI, device_session=DEVICE)
        with self.assertRaises(SimulationFeedbackError):
            producer().latch(1)

    def test_straight_fraction_is_preserved_across_original_latches(self):
        owner = producer()
        owner.advance_motion(3 / 2048, 0., 0.)
        first = owner.latch(100)
        self.assertEqual((first.left_ticks, first.right_ticks), (1, 1))
        owner.advance_motion(3 / 2048, 0., 0.)
        second = owner.latch(200)
        self.assertEqual((second.left_ticks, second.right_ticks), (3, 3))
        self.assertEqual((first.sample_seq, first.source_us), (1, 100))
        self.assertEqual((second.sample_seq, second.source_us), (2, 200))
        self.assertEqual(second.quality, 5)
        self.assertFalse(second.physical_qualified)
        self.assertGreater(second.steering_error_mrad, 0)
        with self.assertRaises(FrozenInstanceError):
            second.source_us = 999

    def test_turn_and_reverse_use_signed_pre_wrap_yaw_and_actual_servo(self):
        owner = producer()
        servo = math.atan(.12)
        owner.advance_motion(.25, .125, servo)
        turn = owner.latch(100)
        self.assertEqual((turn.left_ticks, turn.right_ticks), (248, 264))
        self.assertEqual(turn.steering_mrad, round(servo * 1000))
        self.assertGreaterEqual(turn.steering_error_mrad / 1000,
                                .01 + abs(servo - turn.steering_mrad / 1000))
        owner.advance_motion(-.25, -.125, servo)
        reverse = owner.latch(200)
        self.assertEqual((reverse.left_ticks, reverse.right_ticks), (0, 0))
        self.assertEqual((turn.left_ticks, turn.right_ticks), (248, 264))

    def test_selected_signed_wrap_and_unlatched_half_range_are_bounded(self):
        for bits in (32, 64):
            with self.subTest(bits=bits):
                half = 1 << (bits - 1)
                owner = producer(encoder_bits=bits, initial_left_ticks=half - 2,
                                 initial_right_ticks=half - 2)
                owner.advance_motion(4 / 1024, 0., 0.)
                self.assertEqual(owner.ticks, (-half + 2, -half + 2))
                owner.latch(100)
                owner.advance_motion(-4 / 1024, 0., 0.)
                self.assertEqual(owner.ticks, (half - 2, half - 2))
        owner = producer(metres_per_tick=1.)
        owner.advance_motion(2**30, 0., 0.)
        with self.assertRaises(SimulationFeedbackError):
            owner.advance_motion(2**30, 0., 0.)
        self.assertFalse(owner.valid)
        self.assertEqual(owner.ticks, (2**30, 2**30))

    def test_original_device_time_and_sample_sequence_cannot_be_retimed(self):
        owner = producer()
        first = initial_sample(owner, 100)
        for stamp in (100, 99, True, 0, MAX_SEQUENCE + 1):
            with self.subTest(stamp=stamp), self.assertRaises(SimulationFeedbackError):
                owner.latch(stamp)
        self.assertIs(owner.last_sample, first)
        second = owner.latch(101)
        self.assertEqual((second.sample_seq, second.source_us), (2, 101))
        with self.assertRaises(SimulationFeedbackError):
            WheelSample(0, 101, 0, 0, 0, 10)

    def test_actual_invalid_motion_or_explicit_gap_is_sticky_without_reset(self):
        for motion in ((float('nan'), 0., 0.), (0., .1, 0.), (.1, 0., 1.5)):
            with self.subTest(motion=motion):
                owner = producer()
                owner.advance_motion(8 / 1024, 0., 0.)
                initial = owner.latch(100)
                with self.assertRaises(SimulationFeedbackError):
                    owner.advance_motion(*motion)
                self.assertEqual(owner.ticks, (initial.left_ticks, initial.right_ticks))
                self.assertFalse(owner.valid)
                self.assertIsNone(owner.last_sample)
                with self.assertRaises(SimulationFeedbackError):
                    owner.latch(101)
                with self.assertRaises(SimulationFeedbackError):
                    owner.advance_motion(0., 0., 0.)
        owner = producer()
        initial_sample(owner)
        owner.invalidate()
        self.assertFalse(owner.valid)
        self.assertEqual(owner.ticks, (0, 0))


class ProjectedCodec:
    """B08 struct 필드만 투영한다. 실제 serial framing/수신 FSM은 시험하지 않는다."""

    PROBE3 = struct.Struct('<16s16sQQ')
    CLOCK3 = struct.Struct('<16s16sQQQQQQqqBiH')
    SENSOR3 = struct.Struct('<16s16sQQqqBiH16sQ')
    CLOCK_REPLY = 0x0f11
    SENSOR = 0x0f15

    @staticmethod
    def packet(kind, payload):
        return struct.pack('<H', kind) + payload


class StructInjectedFeedbackTests(unittest.TestCase):
    def test_original_probe_latch_and_duplicate_reply_preserve_bytes(self):
        owner = producer()
        sample = initial_sample(owner)
        probe = ProjectedCodec.PROBE3.pack(UUID(PI).bytes, UUID(DEVICE).bytes, 1, 500_000)
        wire = owner.clock_reply(ProjectedCodec, probe, sample,
                                received_device_us=1_000_010, sent_device_us=1_000_030)
        self.assertEqual(struct.unpack('<H', wire[:2])[0], ProjectedCodec.CLOCK_REPLY)
        self.assertEqual(ProjectedCodec.CLOCK3.unpack(wire[2:]),
                         (UUID(PI).bytes, UUID(DEVICE).bytes, 1, 500_000,
                          1_000_010, 1_000_020, 1_000_030, 1, 0, 0, 5, 0, 10))
        self.assertIs(owner.clock_reply(ProjectedCodec, probe, sample,
                      received_device_us=1_000_010, sent_device_us=1_000_030), wire)
        for delta in (-1, 1):
            with self.assertRaises(SimulationFeedbackError):
                owner.clock_reply(ProjectedCodec, probe, sample,
                                  received_device_us=1_000_010, sent_device_us=1_000_030 + delta)
        new_probe = ProjectedCodec.PROBE3.pack(UUID(PI).bytes, UUID(DEVICE).bytes, 2, 500_100)
        with self.assertRaises(SimulationFeedbackError):
            owner.clock_reply(ProjectedCodec, new_probe, sample,
                              received_device_us=1_000_010, sent_device_us=1_000_030)
        newer = owner.latch(1_000_120)
        self.assertIs(owner.clock_reply(ProjectedCodec, probe, sample,
                      received_device_us=1_000_010, sent_device_us=1_000_030), wire)
        new_wire = owner.clock_reply(ProjectedCodec, new_probe, newer,
                                    received_device_us=1_000_110, sent_device_us=1_000_130)
        self.assertEqual(ProjectedCodec.CLOCK3.unpack(new_wire[2:])[5], newer.source_us)

    def test_identity_time_current_sample_and_explicit_clock_reference_are_checked(self):
        owner = producer()
        sample = initial_sample(owner)
        for pi, device in ((UUID(int=4).bytes, UUID(DEVICE).bytes),
                           (UUID(PI).bytes, UUID(int=5).bytes)):
            probe = ProjectedCodec.PROBE3.pack(pi, device, 1, 500_000)
            with self.assertRaises(SimulationFeedbackError):
                owner.clock_reply(ProjectedCodec, probe, sample,
                                  received_device_us=1_000_010, sent_device_us=1_000_030)
        probe = ProjectedCodec.PROBE3.pack(UUID(PI).bytes, UUID(DEVICE).bytes, 1, 500_000)
        for d2, d3 in ((1_000_021, 1_000_030), (1_000_010, 1_000_019)):
            with self.assertRaises(SimulationFeedbackError):
                owner.clock_reply(ProjectedCodec, probe, sample,
                                  received_device_us=d2, sent_device_us=d3)
        with self.assertRaises(SimulationFeedbackError):
            owner.clock_reply(ProjectedCodec, probe, object(),
                              received_device_us=1_000_010, sent_device_us=1_000_030)
        wire = owner.sensor_wire(ProjectedCodec, sample, clock_epoch=EPOCH, clock_sequence=7)
        self.assertEqual(ProjectedCodec.SENSOR3.unpack(wire[2:]),
                         (UUID(PI).bytes, UUID(DEVICE).bytes, 1, 1_000_020,
                          0, 0, 5, 0, 10, UUID(EPOCH).bytes, 7))
        with self.assertRaises(SimulationFeedbackError):
            owner.sensor_wire(object(), sample, clock_epoch=EPOCH, clock_sequence=7)
        owner.latch(1_000_021)
        with self.assertRaises(SimulationFeedbackError):
            owner.sensor_wire(ProjectedCodec, sample, clock_epoch=EPOCH, clock_sequence=7)
        owner.invalidate()
        with self.assertRaises(SimulationFeedbackError):
            owner.sensor_wire(ProjectedCodec, sample, clock_epoch=EPOCH, clock_sequence=7)


class PairedFeedback3CodecTests(unittest.TestCase):
    """전체 B08 Gateway/common을 같이 제공한 선택 실행 전용이다. serial I/O는 없다."""

    def test_actual_paired_codec_frames_preserve_original_clock_and_sensor_fields(self):
        codec = importlib.import_module('xenova_gateway.feedback3')
        framing = importlib.import_module('xenova_gateway.feedback')
        owner = producer()
        sample = initial_sample(owner)
        probe = codec.PROBE3.pack(UUID(PI).bytes, UUID(DEVICE).bytes, 1, 500_000)
        wire = owner.clock_reply(codec, probe, sample,
                                received_device_us=1_000_010, sent_device_us=1_000_030)
        kind, payload = framing.unpack(wire)
        self.assertEqual(kind, codec.CLOCK_REPLY)
        self.assertEqual(codec.CLOCK3.unpack(payload),
                         (UUID(PI).bytes, UUID(DEVICE).bytes, 1, 500_000,
                          1_000_010, 1_000_020, 1_000_030, 1, 0, 0, 5, 0, 10))
        sensor = owner.sensor_wire(codec, sample, clock_epoch=EPOCH, clock_sequence=7)
        kind, payload = framing.unpack(sensor)
        self.assertEqual(kind, codec.SENSOR)
        self.assertEqual(codec.SENSOR3.unpack(payload),
                         (UUID(PI).bytes, UUID(DEVICE).bytes, 1, 1_000_020,
                          0, 0, 5, 0, 10, UUID(EPOCH).bytes, 7))
        self.assertLessEqual(len(wire), framing.MAX_FRAME)
        self.assertLessEqual(len(sensor), framing.MAX_FRAME)

    def test_actual_mapped_odometry_consumes_turn_reverse_and_signed_wrap(self):
        codec = importlib.import_module('xenova_gateway.feedback3')
        framing = importlib.import_module('xenova_gateway.feedback')
        odometry = importlib.import_module('xenova_gateway.odometry')
        config = odometry.OdometryConfig(
            vehicle='bench', calibration='a' * 64, metres_per_tick=1 / 1024,
            track_width_m=.125, wheelbase_m=.24, maximum_speed_mps=4.,
            maximum_clock_error_us=1000, maximum_sample_age_us=1_000_000,
            sample_period_ms=20, encoder_bits=32, position_error_m=.002,
            heading_error_rad=.005, distance_error_ratio=.01, heading_error_per_m=.001)
        odom = odometry.VehicleOdometry(config, str(UUID(int=9)))
        odom.device_session(DEVICE)
        half = 2**31
        owner = producer(initial_left_ticks=half - 100, initial_right_ticks=half - 100)

        def consume(sample):
            wire = owner.sensor_wire(codec, sample, clock_epoch=EPOCH, clock_sequence=7)
            _, payload = framing.unpack(wire)
            pi, device, seq, stamp, left, right, quality, steer, error, _, _ = codec.SENSOR3.unpack(payload)
            # 독립 software clock interval 입력이다. clock install/lease 검증 수락이 아니다.
            return odom.observe_mapped(str(UUID(bytes=pi)), str(UUID(bytes=device)), stamp, seq,
                                       left, right, quality, steer, error, stamp + 990, stamp + 1010)

        self.assertIsNone(consume(initial_sample(owner, 1_000_000)))
        servo = math.atan(.12)
        owner.advance_motion(.25, .125, servo)
        result = consume(owner.latch(1_100_000))
        self.assertLess(result['left_ticks'], 0)
        self.assertLess(result['right_ticks'], 0)
        self.assertAlmostEqual(result['odom_pose']['x'], 2 * math.sin(.125))
        self.assertAlmostEqual(result['odom_pose']['y'], 2 * (1 - math.cos(.125)))
        self.assertAlmostEqual(result['odom_pose']['yaw'], .125)
        self.assertEqual(result['source_us'], 1_100_000)
        self.assertEqual(result['pi_sample_us'], 1_101_000)
        self.assertEqual(result['pi_session'], PI)
        self.assertIn('estimated_steering_rad', result)
        self.assertNotIn('measured_steering_rad', result)
        self.assertGreater(result['steering_error_rad'], 0)
        owner.advance_motion(-.25, -.125, servo)
        result = consume(owner.latch(1_200_000))
        self.assertAlmostEqual(result['odom_pose']['x'], 0.)
        self.assertAlmostEqual(result['odom_pose']['y'], 0.)
        self.assertAlmostEqual(result['odom_pose']['yaw'], 0.)
        self.assertLess(result['velocity_mps'], 0.)


if __name__ == '__main__':
    unittest.main()
