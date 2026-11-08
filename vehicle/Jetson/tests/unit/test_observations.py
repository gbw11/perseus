"""원 시각·stream별 순번과 제한된 동기 후보를 검사한다."""

from dataclasses import replace
import unittest

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.observations import ClockEvidence, ObservationTracker


def frame(stream="rgb_video", sequence=1, stamp=1_000_000, received=None):
    rgb = stream == "rgb_video"
    return RawFrame(stream, sequence, stamp - 500, stamp,
                    stamp if received is None else received,
                    640, 360 if rgb else 480, "NV12" if rgb else "RAW16_LE",
                    bytes(345600 if rgb else 614400))


class ObservationTests(unittest.TestCase):
    def test_pair_uses_timestamps_and_independent_sequences(self):
        tracker = ObservationTracker()
        self.assertIsNone(tracker.observe(frame(sequence=900)))
        pair = tracker.observe(frame("depth_right", sequence=3, stamp=1_002_000))
        self.assertEqual(pair["other_sequence"], "900")
        self.assertEqual(tracker.snapshot()["candidate_pairs"], 1)
        self.assertFalse(tracker.snapshot()["metric_ready"])

    def test_same_sequence_outside_tolerance_is_not_a_pair(self):
        tracker = ObservationTracker()
        tracker.observe(frame())
        self.assertIsNone(tracker.observe(frame("depth_right", stamp=1_020_000)))
        self.assertEqual(tracker.snapshot()["candidate_pairs"], 0)

    def test_matching_ring_is_bounded_and_expires(self):
        tracker = ObservationTracker()
        for n in range(12):
            tracker.observe(frame(sequence=n, stamp=1_000_000 + 1000 * n))
        self.assertEqual(len(tracker.pending["rgb_video"]), 8)
        tracker.observe(frame("depth_right", stamp=2_000_000))
        self.assertEqual(len(tracker.pending["rgb_video"]), 0)
        self.assertEqual(tracker.snapshot()["unmatched_frames"]["rgb_video"], 12)

    def test_duplicate_or_reversed_time_rejected_and_gap_counted(self):
        tracker = ObservationTracker()
        original = frame()
        tracker.observe(original)
        for changed in (original, replace(original, sequence=2, sdk_us=original.sdk_us - 1),
                        replace(original, sequence=2, device_us=original.device_us - 1)):
            with self.assertRaises(ValueError):
                tracker.observe(changed)
        tracker.observe(frame(sequence=4, stamp=1_100_000))
        self.assertEqual(tracker.snapshot()["sequence_gaps"]["rgb_video"], 2)

    def test_shape_length_and_encoding_are_not_silently_converted(self):
        for changed in (replace(frame(), payload=b"partial"),
                        replace(frame(), encoding="BGR"), replace(frame(), width=320),
                        replace(frame(), sequence=True)):
            with self.subTest(changed=changed.encoding):
                with self.assertRaises(ValueError):
                    ObservationTracker().observe(changed)

    def test_bridge_records_clock_offset_without_claiming_capture_bound(self):
        bridge = ClockEvidence()
        bridge.update({"j_before_us": 1_100_000, "sdk_us": 9_100_000, "j_after_us": 1_100_010})
        observation = frame(stamp=9_150_000, received=1_150_020)
        evidence = bridge.describe(observation)
        self.assertEqual(evidence["sdk_host_timestamp_j_bracket_us"], ["1149999", "1150011"])
        self.assertIsNone(evidence["oak_sync_error_bound_us"])
        self.assertFalse(evidence["metric_time_qualified"])
        self.assertEqual(observation.sdk_us, 9_150_000)

    def test_invalid_or_expired_bridge_is_rejected(self):
        bridge = ClockEvidence()
        with self.assertRaises(ValueError):
            bridge.describe(frame())
        for sample in ({"j_before_us": 2, "sdk_us": 1, "j_after_us": 1},
                       {"j_before_us": 1, "sdk_us": True, "j_after_us": 2}):
            with self.assertRaises(ValueError):
                bridge.update(sample)
        bridge.update({"j_before_us": 100, "sdk_us": 10, "j_after_us": 101})
        with self.assertRaises(ValueError):
            bridge.describe(frame(received=2_000_000))
        with self.assertRaises(ValueError):
            bridge.update({"j_before_us": 1, "sdk_us": 11, "j_after_us": 2})
