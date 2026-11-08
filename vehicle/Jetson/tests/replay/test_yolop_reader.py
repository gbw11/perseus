"""합성 S02 자료로 경로·상한·partial·사용 직전 무결성을 검사한다. 실기 성능 시험이 아니다."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from yolop_dataset_fixtures import make_dataset, read_json, refresh_index, write_json
from xenova_vehicle.recording.reader import DatasetError
from xenova_vehicle.recording.yolop_reader import IncompleteDiagnosticError, YolopDiagnosticReader


_PAYLOADS = ("rgb.nv12", "depth.raw16le", "nn-buffer.bin", "det_out.fp16",
             "drive_area_seg.fp16", "lane_line_seg.fp16")


class YolopDiagnosticReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dataset = make_dataset(Path(self.temporary.name) / "dataset", samples=2)

    def source(self, ordinal=1):
        return self.dataset / "samples" / f"{ordinal:04d}" / "source.json"

    def receipt(self):
        return read_json(self.dataset / "yolop-bench-result.json")

    def update_receipt(self, *, incomplete=False, failures=0):
        value = self.receipt()
        if incomplete:
            value.update(complete=False, exit_code=130, reason="interrupted")
        recording = value["last_worker_status"]["recording"]
        recording["failure_samples_saved"] = failures
        recording["written_frames"] = recording["saved_samples"] + failures
        recording["written_bytes"] = sum(path.stat().st_size for path in self.dataset.rglob("*")
            if path.is_file() and path.name != "yolop-bench-result.json" and ".tmp" not in path.name)
        write_json(self.dataset / "yolop-bench-result.json", value)

    def normal_tail(self):
        (self.dataset / "yolop-bench-result.json").unlink(missing_ok=True)
        directory = self.dataset / "samples" / "0003"
        directory.mkdir()
        return directory

    def assert_payload_not_opened(self, dataset, name, limit):
        """부모 directory FD는 허용하되 해당 inode의 open은 선검사 전에 금지한다."""
        original = os.open

        def guarded(path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, name, "rejected payload inode must not be opened")
            return original(path, *args, **kwargs)

        with patch("xenova_vehicle.recording.yolop_reader.os.open", side_effect=guarded) as opened:
            supported = os.supports_dir_fd | {opened}
            with patch("xenova_vehicle.recording.yolop_reader.os.supports_dir_fd", supported):
                with self.assertRaises(DatasetError):
                    YolopDiagnosticReader(dataset)._read(str(Path("samples") / "0001" / name), limit)

    def test_auto_preflight_owned_bytes_original_stamps_and_no_authority(self):
        before = {path.relative_to(self.dataset): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.dataset.rglob("*") if path.is_file()}
        reader = YolopDiagnosticReader(self.dataset)
        samples = list(reader.samples())
        report = reader.validate()
        self.assertEqual(report["sample_count"], 2)
        self.assertTrue(report["source_complete"])
        self.assertFalse(report["continuous_nn_recording"])
        self.assertLess(len(json.dumps(report)), 65536)
        for ordinal, sample in enumerate(samples, 1):
            original = read_json(self.source(ordinal))
            self.assertEqual(sample.metadata, original)
            self.assertEqual(sample.ordinal, ordinal)
            self.assertEqual(set(sample.payloads), set(_PAYLOADS))
            for name in _PAYLOADS:
                self.assertIs(type(sample.payloads[name]), bytes)
                self.assertEqual(sample.payloads[name], (self.source(ordinal).parent / name).read_bytes())
            self.assertEqual(sample.rgb.device_us, int(original["rgb"]["device_timestamp_us"]))
            self.assertEqual(sample.nn_output.sdk_us, int(original["nn_output"]["sdk_timestamp_us"]))
            self.assertEqual(sample.availability_j_us, max(int(original[key]["received_j_us"])
                for key in ("rgb", "depth", "nn_input", "nn_output")))
            self.assertEqual(sample.input_kind, "REPLAY")
            self.assertFalse(sample.live_authority or sample.nn_ready or sample.metric_ready)
        self.assertEqual(before, {path.relative_to(self.dataset): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in self.dataset.rglob("*") if path.is_file()})

    def test_public_metadata_cannot_change_private_contract(self):
        reader = YolopDiagnosticReader(self.dataset)
        reader.validate()
        reader.manifest["profile"]["asset_dir"] = "/mutated-do-not-open"
        reader.receipt = None
        first = next(reader.samples())
        first.metadata["context"]["device_id"] = "mutated"
        again = next(reader.samples())
        self.assertNotEqual(again.metadata["context"]["device_id"], "mutated")
        self.assertEqual(again.rgb.payload, first.rgb.payload)

    def test_incomplete_and_missing_receipt_require_explicit_permission(self):
        self.update_receipt(incomplete=True)
        with self.assertRaises(IncompleteDiagnosticError):
            list(YolopDiagnosticReader(self.dataset).samples())
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        self.assertEqual(len(list(reader.samples())), 2)
        self.assertFalse(reader.validate()["source_complete"])
        (self.dataset / "yolop-bench-result.json").unlink()
        self.assertEqual(YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()["receipt_status"], "missing")

    def test_zero_valid_partial_has_no_nn_yield(self):
        empty = make_dataset(Path(self.temporary.name) / "empty", samples=0, complete=False)
        reader = YolopDiagnosticReader(empty, allow_incomplete=True)
        report = reader.validate()
        self.assertTrue(report["zero_valid_samples"])
        self.assertTrue(report["no_nn_yield"])
        self.assertEqual(list(reader.samples()), [])

    def test_one_short_written_tail_is_excluded_with_byte_interval(self):
        self.update_receipt(incomplete=True)
        tail = self.dataset / "samples" / "0003"
        tail.mkdir()
        (tail / "rgb.nv12").write_bytes(b"short write")
        value = self.receipt()
        recording = value["last_worker_status"]["recording"]
        recording.update(accepted_samples=3, pending_frames=1, error="OSError: short diagnostic file write")
        write_json(self.dataset / "yolop-bench-result.json", value)
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        report = reader.validate()
        self.assertEqual(report["unindexed_tail_count"], 1)
        self.assertEqual(report["unindexed_tail_bytes"], 11)
        self.assertEqual(report["writer_counter_qualification"], "failed_unindexed_tail_byte_interval")
        self.assertEqual(len(list(reader.samples())), 2)

    def test_empty_tail_and_final_truncated_source_are_quarantined(self):
        tail = self.normal_tail()
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        self.assertEqual(reader.validate()["unindexed_tail_count"], 1)
        self.assertEqual(len(list(reader.samples())), 2)
        for name in _PAYLOADS:
            (tail / name).write_bytes((self.source().parent / name).read_bytes())
        (tail / "source.json").write_bytes(b'{"unfinished":')
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        self.assertEqual(reader.validate()["unindexed_tail_count"], 1)
        self.assertEqual(len(list(reader.samples())), 2)

    def test_tail_rejects_source_without_the_prior_six_payloads(self):
        tail = self.normal_tail()
        (tail / "source.json").write_bytes(b'{"unfinished":')
        with self.assertRaisesRegex(DatasetError, "writer-order prefix"):
            YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()

    def test_tail_rejects_a_short_file_before_the_last_file(self):
        tail = self.normal_tail()
        (tail / "rgb.nv12").write_bytes(b"short first file")
        (tail / "depth.raw16le").write_bytes(b"short final file")
        with self.assertRaisesRegex(DatasetError, "short nonfinal"):
            YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()

    def test_tail_nn_bound_comes_from_the_validated_probe_length(self):
        tail = self.normal_tail()
        for name in _PAYLOADS[:2]:
            (tail / name).write_bytes((self.source().parent / name).read_bytes())
        probe = read_json(self.dataset / "bench-manifest.json")["first_nn_layout_probe"]
        length = probe["payload_length"]
        self.assertLess(length, 1024**2)
        (tail / "nn-buffer.bin").write_bytes(bytes(length + 1))
        with self.assertRaisesRegex(DatasetError, "oversized"):
            YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()
        (tail / "nn-buffer.bin").write_bytes(bytes(length - 1))
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        self.assertEqual(reader.validate()["unindexed_tail_count"], 1)
        self.assertEqual(len(list(reader.samples())), 2)

    def test_indexed_corruption_is_not_misclassified_as_incomplete(self):
        self.update_receipt(incomplete=True)
        (self.source(2).parent / "rgb.nv12").write_bytes(b"broken")
        for allowed in (False, True):
            with self.subTest(allowed=allowed), self.assertRaises(DatasetError) as caught:
                YolopDiagnosticReader(self.dataset, allow_incomplete=allowed).validate()
            self.assertNotIsInstance(caught.exception, IncompleteDiagnosticError)

    def test_tail_with_index_missing_payload_is_corrupt(self):
        self.update_receipt(incomplete=True)
        (self.source(2).parent / "depth.raw16le").unlink()
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()

    def test_two_unindexed_directories_are_not_a_prefix(self):
        self.update_receipt(incomplete=True)
        (self.dataset / "samples" / "0003").mkdir()
        (self.dataset / "samples" / "0004").mkdir()
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()

    def test_failure_raw_bytes_are_hashed_without_tensor_interpretation(self):
        failure = self.dataset / "failure"
        failure.mkdir()
        payload = b"not a valid tensor"
        (failure / "nn-buffer.bin").write_bytes(payload)
        write_json(failure / "source.json", {"record_only": True, "qualification": "unvalidated"})
        write_json(failure / "sample-index.json", {"schema_version": 1,
                   "scope": "yolop-independent-diagnostic-sample", "payloads": {"nn-buffer.bin": {
                       "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}})
        self.update_receipt(incomplete=True, failures=1)
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        self.assertEqual(reader.validate()["failure_indexed_count"], 1)
        self.assertEqual(len(list(reader.samples())), 2)
        (failure / "nn-buffer.bin").write_bytes(b"tampered")
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset, allow_incomplete=True).validate()

    def test_unindexed_failure_is_reported_but_never_yielded(self):
        failure = self.dataset / "failure"
        failure.mkdir()
        (failure / "nn-buffer.bin").write_bytes(b"partial raw")
        self.update_receipt(incomplete=True)
        value = self.receipt()
        value["last_worker_status"]["recording"].update(pending_frames=1, error="OSError: failure write")
        write_json(self.dataset / "yolop-bench-result.json", value)
        reader = YolopDiagnosticReader(self.dataset, allow_incomplete=True)
        self.assertEqual(reader.validate()["failure_unindexed_count"], 1)
        self.assertEqual(len(list(reader.samples())), 2)

    def test_all_samples_preflight_before_first_delivery(self):
        (self.source(2).parent / "lane_line_seg.fp16").write_bytes(b"bad final sample")
        calls = []
        with self.assertRaises(DatasetError):
            for sample in YolopDiagnosticReader(self.dataset).samples():
                calls.append(sample)
        self.assertEqual(calls, [])

    def test_mutation_after_preflight_is_rejected_before_first_delivery(self):
        reader = YolopDiagnosticReader(self.dataset)
        reader.validate()
        path = self.source(2).parent / "nn-buffer.bin"
        value = bytearray(path.read_bytes())
        value[-1] ^= 1
        path.write_bytes(value)
        with self.assertRaises(DatasetError):
            next(reader.samples())

    def test_metadata_changed_between_deliveries_is_rejected(self):
        reader = YolopDiagnosticReader(self.dataset)
        iterator = reader.samples()
        self.assertEqual(next(iterator).ordinal, 1)
        value = read_json(self.source(2))
        value["rgb"]["exposure_us"] += 1
        write_json(self.source(2), value)
        with self.assertRaises(DatasetError):
            next(iterator)

    def test_duplicate_json_keys_and_nonfinite_json_are_rejected(self):
        original = self.source().read_bytes()
        for bad in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.subTest(value=bad):
                self.source().write_bytes(bad)
                with self.assertRaises(DatasetError):
                    YolopDiagnosticReader(self.dataset).validate()
        self.source().write_bytes(original)

    def test_rehashed_active_nan_and_derived_slice_mismatch_are_rejected(self):
        directory = self.source().parent
        raw = bytearray((directory / "nn-buffer.bin").read_bytes())
        raw[0:2] = b"\x00\x7e"
        (directory / "nn-buffer.bin").write_bytes(raw)
        derived = bytearray((directory / "det_out.fp16").read_bytes())
        derived[0:2] = raw[0:2]
        (directory / "det_out.fp16").write_bytes(derived)
        refresh_index(directory)
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()
        raw[0:2] = b"\x00\x00"
        (directory / "nn-buffer.bin").write_bytes(raw)
        refresh_index(directory)
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()

    def test_duplicate_rgb_nn_source_is_rejected_even_when_each_sample_is_valid(self):
        first = read_json(self.source())
        write_json(self.source(2), first)
        self.update_receipt()
        with self.assertRaisesRegex(DatasetError, "duplicate|reversed"):
            YolopDiagnosticReader(self.dataset).validate()

    def test_depth_may_be_reused_only_with_identical_descriptor_and_hash(self):
        first, second = read_json(self.source()), read_json(self.source(2))
        for name in ("rgb", "nn_input", "nn_output"):
            for stamp in ("device_timestamp_us", "sdk_timestamp_us", "received_j_us"):
                second[name][stamp] = str(int(first[name][stamp]) + 1000)
        second["depth"] = deepcopy(first["depth"])
        second["depth_candidate_sdk_delta_us"] = -1725
        second["rgb_source_key"][2] = second["rgb"]["device_timestamp_us"]
        second["capture_clock"] = deepcopy(first["capture_clock"])
        for key, stamp in second["capture_clock"]["bridge_sample"].items():
            second["capture_clock"]["bridge_sample"][key] = str(int(stamp) + 1000)
        second["capture_clock"]["sdk_host_timestamp_j_bracket_us"] = [
            str(int(stamp) + 1000) for stamp in first["capture_clock"]["sdk_host_timestamp_j_bracket_us"]]
        write_json(self.source(2), second)
        self.update_receipt()
        self.assertEqual(len(list(YolopDiagnosticReader(self.dataset).samples())), 2)
        payload = self.source(2).parent / "depth.raw16le"
        value = bytearray(payload.read_bytes())
        value[0] = 1
        payload.write_bytes(value)
        refresh_index(payload.parent)
        with self.assertRaisesRegex(DatasetError, "reused depth"):
            YolopDiagnosticReader(self.dataset).validate()

    def test_filename_index_and_entry_count_bounds(self):
        rogue = self.source().parent / "../escape.bin"
        rogue.write_bytes(b"x")
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()
        rogue.unlink()
        for number in range(3, 10):
            (self.dataset / "samples" / f"{number:04d}").mkdir()
        with self.assertRaisesRegex(DatasetError, "entry limit"):
            YolopDiagnosticReader(self.dataset).validate()

    def test_metadata_payload_and_file_count_bounds(self):
        path = self.source()
        original = path.read_bytes()
        path.write_bytes(b" " * (64 * 1024 + 1))
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()
        path.write_bytes(original)
        (path.parent / "nn-buffer.bin").write_bytes(bytes(1024**2 + 1))
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()
        with patch("xenova_vehicle.recording.yolop_reader._FILE_COUNT_LIMIT", 1):
            with self.assertRaisesRegex(DatasetError, "file count"):
                YolopDiagnosticReader(self.dataset).validate()

    def test_unexpected_sample_filename_and_index_path_are_rejected(self):
        index = self.source().parent / "sample-index.json"
        value = read_json(index)
        value["payloads"]["../rgb.nv12"] = value["payloads"].pop("rgb.nv12")
        write_json(index, value)
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()

    @unittest.skipUnless(os.name == "posix", "POSIX symlink/hardlink checks")
    def test_symlink_hardlink_and_parent_symlink_are_rejected(self):
        payload = self.source().parent / "rgb.nv12"
        saved = payload.read_bytes()
        target = Path(self.temporary.name) / "outside.bin"
        target.write_bytes(saved)
        payload.unlink()
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    payload.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, payload)
                with self.assertRaises(DatasetError):
                    YolopDiagnosticReader(self.dataset).validate()
                self.assert_payload_not_opened(self.dataset, "rgb.nv12", 345600)
                payload.unlink()
        payload.write_bytes(saved)
        samples = self.dataset / "samples"
        moved = self.dataset / "saved-samples"
        samples.rename(moved)
        samples.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(DatasetError):
            YolopDiagnosticReader(self.dataset).validate()

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO check")
    def test_fifo_is_rejected_without_blocking(self):
        # WSL의 /mnt/c는 FIFO를 지원하지 않으므로 이 한 반례만 native 임시 FS를 쓴다.
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            dataset = make_dataset(Path(directory) / "fifo-case", samples=1)
            payload = dataset / "samples" / "0001" / "rgb.nv12"
            payload.unlink()
            os.mkfifo(payload)
            with self.assertRaises(DatasetError):
                YolopDiagnosticReader(dataset).validate()
            self.assert_payload_not_opened(dataset, "rgb.nv12", 345600)

    def test_device_inode_and_oversize_are_rejected_before_file_open(self):
        payload = self.source().parent / "rgb.nv12"
        real_lstat = Path.lstat

        def device_lstat(path, *args, **kwargs):
            if path == payload:
                return SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_nlink=1, st_size=0,
                                       st_file_attributes=0)
            return real_lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", device_lstat):
            self.assert_payload_not_opened(self.dataset, "rgb.nv12", 345600)
        payload.write_bytes(bytes(345601))
        self.assert_payload_not_opened(self.dataset, "rgb.nv12", 345600)

    @unittest.skipUnless(os.name == "posix", "POSIX directory replacement check")
    def test_parent_replacement_after_validation_is_rejected(self):
        reader = YolopDiagnosticReader(self.dataset)
        reader.validate()
        directory = self.source().parent
        directory.rename(directory.with_name("old"))
        directory.mkdir()
        with self.assertRaises(DatasetError):
            next(reader.samples())


class ExistingYolopFixtureTests(unittest.TestCase):
    def test_available_recorded_fixtures_are_read_only_and_preserve_original_fields(self):
        base = Path(__file__).resolve().parents[2] / "build" / "yolop-bench-20261107"
        datasets = [(base / "device" / "final-3s", 3, True),
                    (base / "device" / "normal-30s", 3, True),
                    (base / "device" / "interrupt-sigint", 3, False),
                    (base / "device" / "interrupt-sigterm", 2, False),
                    (base / "device-receipt" / "receipt-3s", 3, True)]
        available = [row for row in datasets if row[0].is_dir()]
        if not available:
            self.skipTest("local S02 recorded fixtures are not present")
        for dataset, count, complete in available:
            with self.subTest(dataset=dataset.name):
                before = {path.relative_to(dataset): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in dataset.rglob("*") if path.is_file()}
                reader = YolopDiagnosticReader(dataset, allow_incomplete=not complete)
                report = reader.validate()
                self.assertEqual((report["sample_count"], report["source_complete"]), (count, complete))
                self.assertEqual(len(list(reader.samples())), count)
                self.assertEqual(before, {path.relative_to(dataset): hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in dataset.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
