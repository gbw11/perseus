"""원본 파일·index 일치와 저장 지연/공간 부족의 유한 실패를 검증한다."""

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.recording.writer import CaptureWriter


def rgb(sequence=1):
    return RawFrame("rgb_video", sequence, 100, 200, 300, 2, 2, "NV12", b"\x01\x02\x03\x04\x05\x06", 50)


def depth(sequence=1):
    return RawFrame("depth_right", sequence, 110, 210, 310, 2, 2, "RAW16_LE",
                    b"\x00\x00\xe8\x03\xd0\x07\xff\xff")


def pair_metadata(sequence="1", epoch="epoch-one", delta=10):
    return {"capture_epoch": epoch, "candidate_pair": {
        "other_stream": "rgb_video", "other_sequence": sequence,
        "other_device_timestamp_us": "100", "sdk_timestamp_delta_us": delta,
        "qualification": "timestamp_candidate_only"}}


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)

    def writer(self, **changes):
        return CaptureWriter(self.output, {"schema_version": 1, "fixture": True},
                             **{"max_bytes": 1024 * 1024, "min_free_bytes": 0, **changes})

    def test_raw_roundtrip_preserves_metadata_and_depth_zero(self):
        writer = self.writer()
        writer.start()
        metadata = {"stream_instance": "rgb-instance", "sdk_bridge": {"lower": -5, "upper": 8}}
        self.assertTrue(writer.submit(rgb(), metadata))
        metadata["sdk_bridge"]["lower"] = 999
        self.assertTrue(writer.submit(depth(), {"stream_instance": "depth-instance"}))
        summary = writer.close(3)
        self.assertTrue(summary["writer_exit_confirmed"])
        self.assertIsNone(summary["error"])
        self.assertEqual(summary["written_frames"], 2)
        self.assertEqual(summary["per_stream_counts"], {"rgb_video": 1, "depth_right": 1})
        records = [json.loads(line) for line in (self.output / "frames.jsonl").read_text().splitlines()]
        for record, original in zip(records, (rgb(), depth())):
            stored = (self.output / record["payload_file"]).read_bytes()
            self.assertEqual(stored, original.payload)
            self.assertEqual(record["sha256"], hashlib.sha256(stored).hexdigest())
            self.assertEqual(record["sdk_timestamp_us"], str(original.sdk_us))
        self.assertEqual(records[0]["sdk_bridge"]["lower"], -5)
        self.assertEqual(records[1]["dtype"], "<u2")
        self.assertEqual(records[1]["invalid_depth_value"], 0)
        self.assertEqual(summary["written_bytes"], sum(path.stat().st_size
                         for path in self.output.rglob("*") if path.is_file()))
        self.assertFalse((self.output / "summary.json").exists())

    def test_existing_payload_or_index_is_not_overwritten(self):
        for name in ("frames", "manifest.json", "frames.jsonl"):
            with self.subTest(name=name):
                path = self.output / name
                path.write_bytes(b"user contents")
                with self.assertRaises(FileExistsError):
                    self.writer()
                self.assertEqual(path.read_bytes(), b"user contents")
                path.unlink()

    def test_mid_capture_io_failure_does_not_index_partial_payload(self):
        writer = self.writer()
        original_write = writer._write_bytes

        def fail_payload(destination, data):
            if str(destination.name).endswith(".bin"):
                original_write(destination, data[:2])
                raise OSError("fixture disk error")
            return original_write(destination, data)

        with patch.object(writer, "_write_bytes", side_effect=fail_payload):
            writer.start()
            writer.submit(rgb(), {})
            summary = writer.close(3)
        self.assertTrue(summary["writer_exit_confirmed"])
        self.assertIn("fixture disk error", summary["error"])
        self.assertEqual(summary["written_frames"], 0)
        self.assertEqual((self.output / "frames.jsonl").read_bytes(), b"")
        self.assertFalse(writer.submit(rgb(2), {}))

    def test_byte_quota_reserves_summary_and_stops_before_payload(self):
        writer = self.writer()
        writer._max_bytes = len(writer._manifest_bytes) + 65536 + 10
        writer.start()
        writer.submit(rgb(), {})
        summary = writer.close(3)
        self.assertIn("quota", summary["error"])
        self.assertEqual(summary["written_frames"], 0)
        self.assertEqual(list((self.output / "frames").iterdir()), [])

    def test_free_space_floor_is_checked_for_each_frame(self):
        writer = self.writer(min_free_bytes=1000)
        enough = SimpleNamespace(free=10**9)
        exhausted = SimpleNamespace(free=65536 + 1000)
        with patch("xenova_vehicle.recording.writer.shutil.disk_usage",
                   side_effect=[enough, exhausted]):
            writer.start()
            writer.submit(rgb(), {})
            summary = writer.close(3)
        self.assertIn("free-space floor", summary["error"])
        self.assertEqual(summary["written_frames"], 0)

    def test_full_queue_and_hung_io_do_not_block_submit_or_close(self):
        writer = self.writer(queue_size=1)
        entered, release = threading.Event(), threading.Event()
        original_write = writer._write_frame

        def slow_write(*args):
            entered.set()
            release.wait(5)
            return original_write(*args)

        try:
            with patch.object(writer, "_write_frame", side_effect=slow_write):
                writer.start()
                writer.submit(rgb(), {})
                self.assertTrue(entered.wait(2))
                self.assertTrue(writer.submit(rgb(2), {}))
                began = time.monotonic()
                self.assertFalse(writer.submit(rgb(3), {}))
                summary = writer.close(0.03)
                self.assertLess(time.monotonic() - began, 0.5)
                self.assertFalse(summary["writer_exit_confirmed"])
                self.assertEqual(summary["accepted_frames"], 2)
                self.assertEqual(summary["dropped_frames"], 1)
                release.set()
                summary = writer.close(3)
        finally:
            release.set()
            writer.close(3)
        self.assertTrue(summary["writer_exit_confirmed"])
        self.assertIsNone(summary["error"])
        self.assertEqual(summary["written_frames"], 2)

    def test_invalid_metadata_cannot_replace_source_or_expand_unbounded(self):
        writer = self.writer()
        writer.start()
        try:
            for metadata in ({"sequence": "fake"}, {"payload_file": "../elsewhere"},
                             {"large": "x" * 16384}, {"bad": float("nan")},
                             {"not_json": object()}):
                with self.subTest(metadata=tuple(metadata)):
                    with self.assertRaises(ValueError):
                        writer.submit(rgb(), metadata)
        finally:
            summary = writer.close(3)
        self.assertEqual(summary["accepted_frames"], 0)

    def test_close_is_idempotent_and_cannot_be_reopened(self):
        writer = self.writer()
        self.assertTrue(writer.close(0)["writer_exit_confirmed"])
        self.assertTrue(writer.close(0)["writer_exit_confirmed"])
        with self.assertRaises(RuntimeError):
            writer.start()

    def test_pair_count_requires_both_written_payloads_and_original_context(self):
        writer = self.writer()
        writer.start()
        writer.submit(rgb(), {"capture_epoch": "epoch-one"})
        writer.submit(depth(), pair_metadata(epoch="another-epoch"))
        writer.submit(depth(2), pair_metadata(delta=999))
        writer.submit(depth(3), pair_metadata())
        summary = writer.close(3)
        self.assertIsNone(summary["error"])
        self.assertEqual(summary["recorded_candidate_pairs"], 1)

    def test_received_pair_with_dropped_counterpart_is_not_recorded_pair(self):
        writer = self.writer(queue_size=1)
        entered, release = threading.Event(), threading.Event()
        original_write = writer._write_frame

        def slow_write(*args):
            entered.set()
            release.wait(5)
            return original_write(*args)

        try:
            with patch.object(writer, "_write_frame", side_effect=slow_write):
                writer.start()
                writer.submit(rgb(), {"capture_epoch": "epoch-one"})
                self.assertTrue(entered.wait(2))
                self.assertTrue(writer.submit(rgb(2), {"capture_epoch": "epoch-one"}))
                self.assertFalse(writer.submit(rgb(3), {"capture_epoch": "epoch-one"}))
                release.set()
                deadline = time.monotonic() + 2
                while writer.snapshot()["written_frames"] < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(writer.snapshot()["written_frames"], 2)
                self.assertTrue(writer.submit(depth(3), pair_metadata(sequence="3")))
                summary = writer.close(3)
        finally:
            release.set()
            writer.close(3)
        self.assertEqual(summary["per_stream_counts"], {"rgb_video": 2, "depth_right": 1})
        self.assertEqual(summary["dropped_frames"], 1)
        self.assertEqual(summary["recorded_candidate_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
