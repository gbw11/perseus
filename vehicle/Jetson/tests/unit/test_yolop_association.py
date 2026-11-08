"""유한 원 입력 association의 순서·시각·epoch 반례를 검사한다."""

from dataclasses import replace
from itertools import permutations
import unittest

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.yolop_association import YolopAssociation
from xenova_vehicle.oak.yolop_frames import NNInputStamp, NNPacket, RuntimeTensor


_TENSORS = (
    RuntimeTensor("det_out", 0, 0x4321, 1, (37800,), (2,), 0),
    RuntimeTensor("drive_area_seg", 0, 0x4321, 1, (204800,), (2,), 75600),
    RuntimeTensor("lane_line_seg", 0, 0x4321, 1, (204800,), (2,), 485200),
)
_NN_BYTES = bytes(894800)
_RGB_BYTES = bytes(345600)


def sample(sequence=1, *, rgb_j=None, input_j=None, output_j=None, context="epoch-1"):
    sdk = 1_000_000 + 1000 * sequence
    device = sdk - 100_000
    rgb_j = sdk if rgb_j is None else rgb_j
    input_j = rgb_j + 50_000 if input_j is None else input_j
    output_j = rgb_j + 100_000 if output_j is None else output_j
    return (
        RawFrame("rgb_video", sequence, device, sdk, rgb_j, 640, 360, "NV12", _RGB_BYTES),
        NNInputStamp("nn_input", sequence, device, sdk, input_j, 320, 320, "BGR888p", context),
        NNPacket("nn_output", sequence, device, sdk, output_j, _NN_BYTES, _TENSORS, context),
    )


def observe(tracker, item):
    return tracker.observe(item, context_key="epoch-1")


class YolopAssociationTests(unittest.TestCase):
    def assert_denominator(self, tracker):
        snapshot = tracker.snapshot()
        for stream, received in snapshot["received"].items():
            self.assertEqual(received, snapshot["matched"] + snapshot["dropped"][stream]
                             + snapshot["pending"][stream])

    def test_all_cross_stream_arrival_permutations_keep_original_source(self):
        items = sample()
        for order in permutations(items):
            with self.subTest(order=[item.stream for item in order]):
                tracker = YolopAssociation("epoch-1")
                matches = [match for item in order for match in observe(tracker, item)]
                self.assertEqual(len(matches), 1)
                match = matches[0]
                self.assertIs(match.rgb, items[0])
                self.assertIs(match.input, items[1])
                self.assertIs(match.packet, items[2])
                self.assertEqual(match.source_timestamp_delta_us, 0)
                self.assertEqual(match.arrival_difference_us, 100_000)
                self.assertFalse(match.descriptor()["capture_age_qualified"])
                self.assertEqual(match.packet.sdk_us, items[0].sdk_us)
                self.assert_denominator(tracker)

    def test_nn_arrival_before_rgb_is_not_relabelled_as_capture_latency(self):
        items = sample(rgb_j=1_100_000, input_j=1_090_000, output_j=1_080_000)
        tracker = YolopAssociation("epoch-1")
        matches = [match for item in reversed(items) for match in observe(tracker, item)]
        self.assertEqual(matches[0].arrival_difference_us, -20_000)
        self.assertFalse(matches[0].descriptor()["metric_ready"])

    def test_depth_sequence_never_provides_nn_input_identity(self):
        tracker = YolopAssociation("epoch-1")
        rgb, stamp, packet = sample()
        depth = RawFrame("depth_right", rgb.sequence, rgb.device_us, rgb.sdk_us,
                         rgb.received_j_us, 640, 480, "RAW16_LE", bytes(614400))
        self.assertEqual(observe(tracker, depth), [])
        observe(tracker, stamp)
        self.assertEqual(observe(tracker, packet), [])
        self.assertEqual(tracker.snapshot()["depth_frames_ignored"], 1)
        self.assertEqual(tracker.snapshot()["retained_payload_bytes"], len(packet.payload))

    def test_input_rgb_or_result_different_sequence_never_matches(self):
        for index in range(3):
            tracker = YolopAssociation("epoch-1")
            items = list(sample())
            items[index] = replace(items[index], sequence=88)
            self.assertEqual([m for item in items for m in observe(tracker, item)], [])
            tracker.expire(2_000_000)
            self.assertEqual(tracker.snapshot()["matched"], 0)
            self.assert_denominator(tracker)

    def test_same_sequence_different_source_device_stamp_is_not_equal(self):
        tracker = YolopAssociation("epoch-1")
        rgb, stamp, packet = sample()
        observe(tracker, rgb)
        observe(tracker, stamp)
        self.assertEqual(observe(tracker, replace(packet, device_us=packet.device_us + 1)), [])
        tracker.expire(2_000_000)
        self.assertEqual(tracker.snapshot()["matched"], 0)

    def test_sdk_timestamp_mismatch_drops_result_without_retimestamp(self):
        tracker = YolopAssociation("epoch-1")
        rgb, stamp, packet = sample()
        observe(tracker, rgb)
        observe(tracker, stamp)
        self.assertEqual(observe(tracker, replace(packet, sdk_us=packet.sdk_us + 1)), [])
        self.assertEqual(tracker.snapshot()["reasons"]["source_timestamp_mismatch"], 1)
        self.assertEqual(packet.sdk_us, rgb.sdk_us)
        self.assert_denominator(tracker)

    def test_context_mismatch_never_expires_or_joins_current_binding(self):
        tracker = YolopAssociation("epoch-1")
        rgb, stamp, packet = sample()
        observe(tracker, rgb)
        old = replace(packet, context_key="epoch-0", received_j_us=20_000_000)
        self.assertEqual(tracker.observe(old), [])
        self.assertEqual(len(tracker.pending["rgb_video"]), 1)
        observe(tracker, stamp)
        self.assertEqual(len(observe(tracker, packet)), 1)
        self.assertEqual(tracker.snapshot()["reasons"]["context_mismatch"], 1)
        self.assert_denominator(tracker)

    def test_raw_requires_external_context_and_new_epoch_requires_new_instance(self):
        tracker = YolopAssociation("epoch-1")
        rgb, stamp, packet = sample()
        self.assertEqual(tracker.observe(rgb), [])
        self.assertEqual(tracker.snapshot()["reasons"]["context_mismatch"], 1)
        for item in sample(context="epoch-2"):
            tracker.observe(item, context_key="epoch-2")
        self.assertEqual(tracker.snapshot()["matched"], 0)

    def test_duplicate_source_or_same_stream_time_reversal_is_not_reused(self):
        tracker = YolopAssociation("epoch-1")
        items = sample()
        for item in items:
            observe(tracker, item)
        for item in items:
            self.assertEqual(observe(tracker, item), [])
        rgb2 = sample(2)[0]
        self.assertEqual(observe(tracker, replace(rgb2, sdk_us=items[0].sdk_us - 1)), [])
        self.assertEqual(tracker.snapshot()["matched"], 1)
        self.assertEqual(tracker.snapshot()["reasons"]["duplicate_or_source_reversal"], 4)
        self.assert_denominator(tracker)

    def test_expiration_is_original_rgb_receive_time_not_late_metadata(self):
        tracker = YolopAssociation("epoch-1")
        rgb, stamp, packet = sample(rgb_j=1_000_000, input_j=1_500_001, output_j=1_450_000)
        observe(tracker, rgb)
        observe(tracker, packet)
        self.assertEqual(observe(tracker, stamp), [])
        self.assertEqual(tracker.snapshot()["pending"], dict.fromkeys(("rgb_video", "nn_input", "nn_output"), 0))
        self.assertEqual(tracker.snapshot()["matched"], 0)
        self.assert_denominator(tracker)

    def test_ring_boundary_inclusive_but_not_one_microsecond_over(self):
        for age, expected in ((500_000, 1), (500_001, 0)):
            tracker = YolopAssociation("epoch-1")
            items = sample(rgb_j=1_000_000, input_j=1_000_001, output_j=1_000_000 + age)
            result = [m for item in items for m in observe(tracker, item)]
            self.assertEqual(len(result), expected)

    def test_separate_nn_timeout_does_not_assume_camera_frame_period(self):
        tracker = YolopAssociation("epoch-1", nn_timeout_us=120_000)
        rgb, stamp, packet = sample(rgb_j=1_000_000, input_j=1_020_000, output_j=1_150_000)
        observe(tracker, rgb)
        observe(tracker, stamp)
        self.assertEqual(observe(tracker, packet), [])
        self.assertEqual(tracker.snapshot()["reasons"]["late_nn_result"], 1)
        self.assert_denominator(tracker)

    def test_idle_tick_expires_result_and_reports_missing_input(self):
        tracker = YolopAssociation("epoch-1")
        packet = sample()[2]
        observe(tracker, packet)
        tracker.expire(packet.received_j_us + 500_001)
        state = tracker.snapshot()
        self.assertEqual(state["unmatched_nn_outputs"], 1)
        self.assertEqual(state["reasons"]["expired_nn_output"], 1)
        self.assertEqual(state["reasons"]["outputs_without_pending_input"], 1)
        self.assertEqual(state["reasons"]["outputs_without_pending_rgb"], 1)
        self.assertTrue(state["reason_counts_are_nonexclusive"])
        self.assert_denominator(tracker)

    def test_capacity_and_owned_payload_are_bounded(self):
        tracker = YolopAssociation("epoch-1")
        # RGB / NN use disjoint source keys so neither ring drains by matching.
        for n in range(1, 10):
            observe(tracker, sample(n)[0])
            observe(tracker, sample(n + 100)[2])
        state = tracker.snapshot()
        self.assertEqual(state["pending"]["rgb_video"], 8)
        self.assertEqual(state["pending"]["nn_output"], 8)
        self.assertEqual(state["retained_payload_bytes"], 8 * (345600 + 894800))
        self.assertLessEqual(state["retained_payload_bytes"], state["retained_payload_bound_bytes"])
        self.assertLess(state["retained_payload_bound_bytes"], 16 * 1024 * 1024)
        self.assertEqual(state["reasons"]["capacity_rgb_video"], 1)
        self.assertEqual(state["reasons"]["capacity_nn_output"], 1)
        self.assert_denominator(tracker)

    def test_zero_fps_assumptions_and_validation_cost_accounting(self):
        tracker = YolopAssociation("epoch-1", nn_timeout_us=30_000_000)
        items = sample(output_j=1_301_000)
        for item in items:
            observe(tracker, item)
        state = tracker.snapshot()
        self.assertEqual(state["matched"], 1)
        self.assertEqual(state["validation_elements"], 447400)
        self.assertEqual(state["validation_total_us"], items[2].validation_elapsed_us)
        self.assertFalse(state["capture_age_qualified"])
        self.assertEqual(state["ring_max_age_us"], 500000)

    def test_invalid_caps_backwards_tick_and_unsupported_shapes_fail(self):
        for kwargs in ({"capacity": 9}, {"capacity": True}, {"max_age_us": 500001},
                       {"nn_timeout_us": 0}, {"max_age_us": True}):
            with self.assertRaises(ValueError):
                YolopAssociation("epoch-1", **kwargs)
        tracker = YolopAssociation("epoch-1")
        tracker.expire(100)
        with self.assertRaises(ValueError):
            tracker.expire(99)
        with self.assertRaises(ValueError):
            tracker.expire(True)
        with self.assertRaises(TypeError):
            tracker.observe(object())
        with self.assertRaises(ValueError):
            observe(tracker, RawFrame("rgb_video", 1, 1, 1, 100, 320, 320, "NV12", bytes(153600)))


if __name__ == "__main__":
    unittest.main()
