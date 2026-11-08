"""원본 bytes/시각 보존과 CLI 격리·실패·취소를 검증한다. 카메라는 사용하지 않는다."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from replay_fixtures import create_dataset
from xenova_vehicle.recording import replay


def fingerprints(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = create_dataset(self.root / "dataset")
        self.output = self.root / "replayed"

    def test_payload_original_identifiers_and_trace_are_preserved(self):
        before = fingerprints(self.dataset)
        samples = []
        report = replay.run_replay(self.dataset, output=self.output, speed=0, consumer=samples.append)
        self.assertEqual(report["exit_code"], 0, report)
        self.assertTrue(report["replay_completed"])
        self.assertTrue(report["source_complete"])
        self.assertFalse(report["live_authority"])
        self.assertFalse(report["metric_ready"])
        records = [json.loads(line) for line in (self.dataset / "frames.jsonl").read_text().splitlines()]
        events = [json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(samples), 4)
        for ordinal, (sample, record, event) in enumerate(zip(samples, records, events), 1):
            self.assertEqual(sample.ordinal, ordinal)
            self.assertEqual(sample.frame.payload, (self.dataset / record["payload_file"]).read_bytes())
            self.assertEqual(sample.frame.sequence, int(record["sequence"]))
            self.assertEqual(sample.frame.device_us, int(record["device_timestamp_us"]))
            self.assertEqual(sample.frame.sdk_us, int(record["sdk_timestamp_us"]))
            self.assertEqual(sample.frame.received_j_us, int(record["received_j_us"]))
            self.assertEqual(event["source"], record)
            self.assertEqual(event["input_kind"], "REPLAY")
            self.assertFalse(event["live_authority"])
        self.assertEqual(fingerprints(self.dataset), before)
        saved = json.loads((self.output / "replay-result.json").read_text())
        self.assertTrue(saved["replay_completed"])
        self.assertEqual(saved["replayed_frames"], 4)

    def test_late_corrupt_payload_is_rejected_before_any_delivery(self):
        rows = [json.loads(s) for s in (self.dataset / "frames.jsonl").read_text().splitlines()]
        target = self.dataset / rows[-1]["payload_file"]
        damaged = bytearray(target.read_bytes())
        damaged[-1] ^= 1
        target.write_bytes(damaged)
        delivered = []
        report = replay.run_replay(self.dataset, output=self.output, speed=0, consumer=delivered.append)
        self.assertEqual(report["exit_code"], 1)
        self.assertFalse(report["replay_completed"])
        self.assertEqual(delivered, [])
        self.assertFalse((self.output / "events.jsonl").exists())

    def test_partial_source_requires_explicit_option_and_stays_partial(self):
        receipt_path = self.dataset / "capture-result.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(complete=False, exit_code=130, reason="interrupted")
        receipt["last_worker_status"]["reason"] = "stopped"
        receipt_path.write_text(json.dumps(receipt))
        denied = replay.run_replay(self.dataset, speed=0)
        self.assertEqual(denied["exit_code"], 1)
        permitted = replay.run_replay(self.dataset, output=self.output, speed=0, allow_incomplete=True)
        self.assertEqual(permitted["exit_code"], 0, permitted)
        self.assertTrue(permitted["replay_completed"])
        self.assertFalse(permitted["source_complete"])
        self.assertTrue(permitted["source_validation"]["incomplete_reasons"])

    def test_output_cannot_overwrite_or_mutate_input(self):
        before = fingerprints(self.dataset)
        for output in (self.dataset, self.dataset / "output", self.dataset.parent):
            with self.subTest(output=output), self.assertRaises(ValueError):
                replay.run_replay(self.dataset, output=output, speed=0)
        self.output.mkdir()
        marker = self.output / "user.txt"
        marker.write_text("preserve")
        with self.assertRaises(FileExistsError):
            replay.run_replay(self.dataset, output=self.output, speed=0)
        self.assertEqual(marker.read_text(), "preserve")
        self.assertEqual(fingerprints(self.dataset), before)

    def test_consumer_metadata_mutation_does_not_change_original_trace(self):
        original = json.loads((self.dataset / "frames.jsonl").read_text().splitlines()[0])
        def mutate(sample):
            sample.metadata["sequence"] = "999"
            sample.metadata["source_key"][1] = "999"
        report = replay.run_replay(self.dataset, output=self.output, speed=0, consumer=mutate)
        self.assertEqual(report["exit_code"], 0, report)
        event = json.loads((self.output / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(event["source"], original)

    def test_consumer_failure_or_cancellation_cannot_be_success(self):
        delivered = []
        def fail(sample):
            delivered.append(sample)
            if len(delivered) == 2:
                raise RuntimeError("fixture consumer error")
        failed = replay.run_replay(self.dataset, speed=0, consumer=fail)
        self.assertEqual(failed["exit_code"], 1)
        self.assertFalse(failed["replay_completed"])
        self.assertEqual(len(delivered), 2)
        delivered.clear()
        cancelled = replay.run_replay(self.dataset, speed=0, consumer=delivered.append,
                                      cancelled=lambda: len(delivered) >= 1)
        self.assertEqual(cancelled["exit_code"], 130)
        self.assertEqual(cancelled["replayed_frames"], 1)
        self.assertFalse(cancelled["replay_completed"])

    def test_failed_result_or_trace_write_is_not_success(self):
        original = replay._write_result
        def fail_final(output, report, *, initial=False):
            if initial:
                return original(output, report, initial=True)
            raise OSError("fixture disk full")
        with patch.object(replay, "_write_result", side_effect=fail_final):
            report = replay.run_replay(self.dataset, output=self.output, speed=0)
        self.assertEqual(report["exit_code"], 1)
        self.assertFalse(report["replay_completed"])
        self.assertIn("disk full", report["error"])
        with patch.object(replay, "_MAX_TRACE_BYTES", 1):
            report = replay.run_replay(self.dataset, output=self.root / "small-trace", speed=0)
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("trace", report["error"])

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-S", "-m", "xenova_vehicle", *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=20,
                              env={**os.environ, "PYTHONUTF8": "1"})

    def test_cli_has_no_camera_native_or_network_dependency(self):
        code = """
import importlib.abc,sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        prefixes=('depthai','numpy','cv2','paho','socket','xenova_vehicle.doctor',
                  'xenova_vehicle.oak.device','xenova_vehicle.runtime.capture')
        if any(fullname==p or fullname.startswith(p+'.') for p in prefixes):
            raise AssertionError('forbidden replay import: '+fullname)
sys.meta_path.insert(0,Deny())
from xenova_vehicle.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
        result = subprocess.run([sys.executable, "-S", "-c", code, "replay", "--dataset",
                                 str(self.dataset), "--output", str(self.output), "--speed", "0", "--json"],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["replayed_frames"], 4)
        self.assertFalse(report["live_authority"])

    def test_help_and_bad_options_are_lightweight(self):
        self.assertEqual(self.invoke("replay", "--help").returncode, 0)
        for value in ("nan", "inf", "-1", "17", "abc"):
            result = self.invoke("replay", "--dataset", str(self.dataset), "--output", str(self.output),
                                 "--speed", value)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(sys.platform == "linux", "signal delivery requires Linux")
    def test_cli_signals_close_trace_and_report_cancelled(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                self.check_cli_signal(number, self.root / f"signal-{number}")

    def check_cli_signal(self, number, output):
        process = subprocess.Popen([sys.executable, "-S", "-m", "xenova_vehicle", "replay",
                                    "--dataset", str(self.dataset), "--output", str(output),
                                    "--speed", "0.001", "--json"], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            deadline = time.monotonic() + 10
            trace = output / "events.jsonl"
            while time.monotonic() < deadline:
                if trace.exists() and trace.stat().st_size:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            process.send_signal(number)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 128 + number, stderr)
            self.assertFalse(json.loads(stdout)["replay_completed"])
            self.assertEqual(json.loads((output / "replay-result.json").read_text())["phase"], "CANCELLED")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_invalid_unicode_in_source_error_still_writes_final_failure(self):
        manifest = self.dataset / "manifest.json"
        raw = manifest.read_bytes()
        manifest.write_bytes(b'{"\\ud800":1,"\\ud800":2,' + raw[1:])
        report = replay.run_replay(self.dataset, output=self.output, speed=0)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["phase"], "FAILED")
        self.assertIn("duplicate JSON key", report["error"])
        report["error"].encode("utf-8")
        self.assertEqual(json.loads((self.output / "replay-result.json").read_text()), report)
