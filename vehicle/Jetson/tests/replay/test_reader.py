"""카메라 없는 원본 읽기와 손상·부분 기록·파일 교체 거절을 검사한다."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from replay_fixtures import create_dataset
from xenova_vehicle.recording.reader import DatasetError, DatasetReader


class DatasetReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dataset = create_dataset(Path(self.temporary.name) / "dataset")

    def read_json(self, name):
        return json.loads((self.dataset / name).read_text(encoding="utf-8"))

    def write_json(self, name, value):
        (self.dataset / name).write_text(json.dumps(value), encoding="utf-8")

    def rows(self):
        return [json.loads(s) for s in (self.dataset / "frames.jsonl").read_text().splitlines()]

    def write_rows(self, rows):
        (self.dataset / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_preserves_owned_bytes_original_timestamps_ids_and_metadata(self):
        original = {p.relative_to(self.dataset).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.dataset.rglob("*") if p.is_file()}
        reader = DatasetReader(self.dataset)
        report = reader.validate()
        self.assertTrue(report["source_complete"])
        self.assertTrue(report["hashes_verified"])
        self.assertEqual(report["frame_count"], 4)
        self.assertEqual(report["recorded_candidate_pairs"], 2)
        self.assertEqual(report["per_stream_counts"], {"rgb_video": 2, "depth_right": 2})
        rows = self.rows()
        samples = list(reader.frames())
        for number, (row, sample) in enumerate(zip(rows, samples), 1):
            self.assertEqual(sample.ordinal, number)
            self.assertEqual(sample.metadata, row)
            self.assertEqual(sample.frame.payload, (self.dataset / row["payload_file"]).read_bytes())
            self.assertEqual(sample.frame.sequence, int(row["sequence"]))
            self.assertEqual(sample.frame.device_us, int(row["device_timestamp_us"]))
            self.assertEqual(sample.frame.sdk_us, int(row["sdk_timestamp_us"]))
            self.assertEqual(sample.frame.received_j_us, int(row["received_j_us"]))
        samples[0].metadata["clock_evidence"]["metric_time_qualified"] = True
        self.assertFalse(next(reader.frames()).metadata["clock_evidence"]["metric_time_qualified"])
        self.assertEqual(original, {p.relative_to(self.dataset).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in self.dataset.rglob("*") if p.is_file()})

    def test_recording_drops_and_sequence_gaps_do_not_make_source_incomplete(self):
        rows = self.rows()
        for row in rows[2:]:
            row["sequence"] = "2"
            row["source_key"][1] = "2"
            if row["candidate_pair"]:
                row["candidate_pair"]["other_sequence"] = "2"
        self.write_rows(rows)
        receipt = self.read_json("capture-result.json")
        worker = receipt["last_worker_status"]
        worker["recording"]["dropped_frames"] = 2
        worker["observations"]["received_frames"] = {"rgb_video": 3, "depth_right": 3}
        worker["observations"]["candidate_pairs"] = 3
        worker["recording"]["written_bytes"] = sum((self.dataset / name).stat().st_size
            for name in ["manifest.json", "frames.jsonl", *(r["payload_file"] for r in rows)])
        self.write_json("capture-result.json", receipt)
        report = DatasetReader(self.dataset).validate()
        self.assertTrue(report["source_complete"])
        self.assertEqual(report["capture_dropped_frames"], 2)
        self.assertEqual(report["recorded_sequence_gaps"], {"rgb_video": 1, "depth_right": 1})

    def test_candidate_annotation_may_refer_to_a_frame_not_stored_in_index(self):
        rows = self.rows()
        rows[1]["candidate_pair"]["other_sequence"] = "123"
        self.write_rows(rows)
        receipt = self.read_json("capture-result.json")
        recording = receipt["last_worker_status"]["recording"]
        recording["recorded_candidate_pairs"] = 1
        recording["written_bytes"] = sum((self.dataset / name).stat().st_size
            for name in ["manifest.json", "frames.jsonl", *(r["payload_file"] for r in rows)])
        self.write_json("capture-result.json", receipt)
        reader = DatasetReader(self.dataset)
        self.assertEqual(reader.validate()["recorded_candidate_pairs"], 1)
        self.assertEqual(list(reader.frames())[1].metadata["candidate_pair"]["other_sequence"], "123")

    def test_source_output_path_is_portable_and_not_used_to_open_files(self):
        reader = DatasetReader(self.dataset)
        self.assertEqual(reader.validate()["frame_count"], 4)
        self.assertEqual(reader.receipt["output"], "/original-host/capture")

    def test_no_frames_are_exposed_before_full_preflight(self):
        reader = DatasetReader(self.dataset)
        with self.assertRaises(DatasetError):
            next(reader.frames())
        rows = self.rows()
        path = self.dataset / rows[-1]["payload_file"]
        path.write_bytes(bytes([9]) * path.stat().st_size)
        with self.assertRaisesRegex(DatasetError, "hash"):
            reader.validate()
        with self.assertRaises(DatasetError):
            next(reader.frames())

    def test_payload_corruption_or_missing_reference_is_not_partial_recovery(self):
        rows = self.rows()
        payload = self.dataset / rows[0]["payload_file"]
        payload.write_bytes(bytes([123]) * payload.stat().st_size)
        for partial in (False, True):
            with self.subTest(partial=partial), self.assertRaisesRegex(DatasetError, "hash"):
                DatasetReader(self.dataset, allow_incomplete=partial).validate()
        payload.unlink()
        for partial in (False, True):
            with self.subTest(partial=partial), self.assertRaises(DatasetError):
                DatasetReader(self.dataset, allow_incomplete=partial).validate()

    def test_interrupted_capture_requires_explicit_partial_mode(self):
        receipt = self.read_json("capture-result.json")
        receipt.update(complete=False, exit_code=130, reason="interrupted")
        self.write_json("capture-result.json", receipt)
        with self.assertRaisesRegex(DatasetError, "allow-incomplete"):
            DatasetReader(self.dataset).validate()
        reader = DatasetReader(self.dataset, allow_incomplete=True)
        report = reader.validate()
        self.assertFalse(report["source_complete"])
        self.assertIn("capture_not_complete", report["incomplete_reasons"])
        self.assertEqual(len(list(reader.frames())), 4)

    def test_missing_receipt_can_only_recover_verified_index_with_explicit_flag(self):
        (self.dataset / "capture-result.json").unlink()
        with self.assertRaises(DatasetError):
            DatasetReader(self.dataset).validate()
        report = DatasetReader(self.dataset, allow_incomplete=True).validate()
        self.assertFalse(report["source_complete"])
        self.assertIn("missing_capture_receipt", report["incomplete_reasons"])

    def test_unterminated_tail_is_discarded_even_when_its_json_is_valid(self):
        tail = json.dumps(self.rows()[0]).encode()
        with (self.dataset / "frames.jsonl").open("ab") as index:
            index.write(tail)
        with self.assertRaises(DatasetError):
            DatasetReader(self.dataset).validate()
        reader = DatasetReader(self.dataset, allow_incomplete=True)
        report = reader.validate()
        self.assertEqual(report["discarded_tail_bytes"], len(tail))
        self.assertEqual(len(list(reader.frames())), 4)
        self.assertFalse(report["source_complete"])

    def test_orphan_payload_is_not_replayed_and_marks_partial_source(self):
        orphan = self.dataset / "frames/00000005-rgb_video.bin"
        orphan.write_bytes(b"incomplete")
        with self.assertRaises(DatasetError):
            DatasetReader(self.dataset).validate()
        reader = DatasetReader(self.dataset, allow_incomplete=True)
        report = reader.validate()
        self.assertEqual(report["orphan_payloads"], ["frames/00000005-rgb_video.bin"])
        self.assertEqual(len(list(reader.frames())), 4)

    def test_bad_middle_json_duplicate_fields_and_nonfinite_numbers_are_rejected(self):
        original = (self.dataset / "frames.jsonl").read_bytes()
        for line in (b'{"stream":\n', b'{"stream":"x","stream":"x"}\n',
                     b'{"value":NaN}\n', b'{"value":1e999}\n'):
            (self.dataset / "frames.jsonl").write_bytes(line + original)
            for partial in (False, True):
                with self.subTest(line=line, partial=partial), self.assertRaises(DatasetError):
                    DatasetReader(self.dataset, allow_incomplete=partial).validate()

    def test_path_traversal_and_nonsequential_payload_ordinal_are_rejected(self):
        original = self.rows()
        for name in ("../outside.bin", "/absolute.bin", "frames/00000002-rgb_video.bin",
                     "frames\\00000001-rgb_video.bin"):
            rows = json.loads(json.dumps(original))
            rows[0]["payload_file"] = name
            self.write_rows(rows)
            with self.subTest(name=name), self.assertRaises(DatasetError):
                DatasetReader(self.dataset, allow_incomplete=True).validate()

    def test_context_shape_original_time_and_source_key_tampering_are_rejected(self):
        original = self.rows()
        for field, value in (("capture_epoch", "00000000-0000-0000-0000-000000000000"),
                             ("source_key", ["wrong", "0", "10000"]), ("sequence", "00"),
                             ("width", True), ("depth_unit", "meter"),
                             ("received_j_us", "0")):
            rows = json.loads(json.dumps(original))
            rows[0][field] = value
            self.write_rows(rows)
            with self.subTest(field=field), self.assertRaises(DatasetError):
                DatasetReader(self.dataset, allow_incomplete=True).validate()

    def test_receipt_context_and_counter_contradictions_are_always_detected(self):
        original = self.read_json("capture-result.json")
        for kind in ("context", "counters", "writer_exit"):
            receipt = json.loads(json.dumps(original))
            if kind == "context":
                receipt["context"]["device_id"] = "another-device"
            elif kind == "counters":
                receipt["last_worker_status"]["recording"]["written_frames"] += 1
            else:
                receipt["last_worker_status"]["recording"]["writer_exit_confirmed"] = False
            self.write_json("capture-result.json", receipt)
            for partial in (False, True):
                with self.subTest(kind=kind, partial=partial), self.assertRaises(DatasetError):
                    DatasetReader(self.dataset, allow_incomplete=partial).validate()

    def test_profile_mismatch_does_not_override_original_context(self):
        profile = self.read_json("manifest.json")["profile"]
        self.assertEqual(DatasetReader(self.dataset, expected_profile=profile).validate()["frame_count"], 4)
        profile["device_mxid"] = "ANOTHER"
        with self.assertRaisesRegex(DatasetError, "profile differs"):
            DatasetReader(self.dataset, expected_profile=profile).validate()

    def test_payload_replacement_during_iteration_is_rejected_before_yield(self):
        reader = DatasetReader(self.dataset)
        reader.validate()
        frames = reader.frames()
        next(frames)
        row = self.rows()[1]
        path = self.dataset / row["payload_file"]
        path.write_bytes(bytes([111]) * path.stat().st_size)
        with self.assertRaises(DatasetError):
            next(frames)

    def test_control_metadata_replacement_after_validation_is_rejected(self):
        reader = DatasetReader(self.dataset)
        reader.validate()
        receipt = self.read_json("capture-result.json")
        receipt["output"] = "tampered"
        self.write_json("capture-result.json", receipt)
        with self.assertRaises(DatasetError):
            next(reader.frames())

    def test_symlink_payloads_are_rejected(self):
        row = self.rows()[0]
        path = self.dataset / row["payload_file"]
        outside = Path(self.temporary.name) / "outside.bin"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available")
        with self.assertRaises(DatasetError):
            DatasetReader(self.dataset, allow_incomplete=True).validate()

    def test_hardlink_payloads_are_rejected(self):
        path = self.dataset / self.rows()[0]["payload_file"]
        outside = Path(self.temporary.name) / "outside.bin"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            os.link(outside, path)
        except (OSError, NotImplementedError):
            self.skipTest("hardlink creation is not available")
        with self.assertRaises(DatasetError):
            DatasetReader(self.dataset, allow_incomplete=True).validate()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is POSIX-only")
    def test_fifo_is_rejected_without_blocking(self):
        path = self.dataset / self.rows()[0]["payload_file"]
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(DatasetError):
            DatasetReader(self.dataset, allow_incomplete=True).validate()

    def test_reader_import_and_real_fixture_need_no_camera_or_array_libraries(self):
        code = """import sys
from pathlib import Path
from xenova_vehicle.recording.reader import DatasetReader
reader=DatasetReader(Path(sys.argv[1]))
reader.validate()
assert len(list(reader.frames()))==4
for name in ('depthai','numpy','cv2','xenova_vehicle.oak.device'):
    assert name not in sys.modules,name
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.dataset)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
