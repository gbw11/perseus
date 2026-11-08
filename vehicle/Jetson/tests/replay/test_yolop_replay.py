"""선택 진단의 실제 bytes 전달·새 timeline·0건 preflight·CLI 격리를 검사한다."""

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

from xenova_vehicle.recording.yolop_replay import run_yolop_replay
from yolop_dataset_fixtures import make_dataset, read_json, refresh_index


class FakeClock:
    def __init__(self):
        self.current = 100.0
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += seconds


def aggregate(root):
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class YolopReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = make_dataset(self.root / "dataset")

    def test_consumer_receives_owned_raw_bytes_descriptors_and_original_identity(self):
        original = aggregate(self.dataset)
        received = []
        output = self.root / "replay"
        report = run_yolop_replay(self.dataset, output=output, speed=0, consumer=received.append)
        self.assertEqual(report["exit_code"], 0, report)
        self.assertTrue(report["replay_completed"])
        self.assertEqual(report["replayed_samples"], 3)
        self.assertEqual(report["consumer_calls"], 3)
        for sample in received:
            meta = sample.metadata
            self.assertEqual(sample.rgb.payload, sample.payloads["rgb.nv12"])
            self.assertEqual(len(sample.depth.payload), 614400)
            self.assertEqual(sample.nn_output.payload, sample.payloads["nn-buffer.bin"])
            for name in ("det_out", "drive_area_seg", "lane_line_seg"):
                self.assertEqual(sample.nn_output.layer_bytes(name), sample.payloads[name + ".fp16"])
            for value in sample.payloads.values():
                self.assertIs(type(value), bytes)
            self.assertEqual(sample.rgb.sequence, int(meta["rgb"]["sequence"]))
            self.assertEqual(sample.nn_output.sdk_us, int(meta["nn_output"]["sdk_timestamp_us"]))
            self.assertEqual(sample.availability_j_us,
                             max(int(meta[k]["received_j_us"]) for k in ("rgb", "depth", "nn_input", "nn_output")))
            self.assertEqual(sample.input_kind, "REPLAY")
            self.assertFalse(sample.live_authority)
            self.assertFalse(sample.nn_ready)
            self.assertFalse(sample.metric_ready)
        events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["source"], received[0].metadata)
        self.assertEqual(events[0]["payloads"]["nn-buffer.bin"]["sha256"],
                         hashlib.sha256(received[0].nn_output.payload).hexdigest())
        self.assertEqual(aggregate(self.dataset), original)

    def test_availability_timing_and_fresh_replay_epoch_preserve_source(self):
        clock = FakeClock()
        output = self.root / "timed"
        one = run_yolop_replay(self.dataset, output=output, speed=2,
                               clock=clock.now, sleeper=clock.sleep)
        two = run_yolop_replay(self.dataset, speed=0)
        self.assertEqual(one["exit_code"], 0, one)
        self.assertEqual(two["exit_code"], 0, two)
        self.assertNotEqual(one["replay_instance"], two["replay_instance"])
        self.assertEqual(one["source_context"], two["source_context"])
        events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        first = int(events[0]["source_availability_j_us"])
        for event in events:
            expected = (int(event["source_availability_j_us"]) - first + 1) // 2
            self.assertEqual(event["delivery"]["target_elapsed_us"], expected)
            self.assertGreaterEqual(event["delivery"]["actual_elapsed_us"], expected)
            self.assertFalse(event["live_authority"])
        self.assertTrue(clock.sleeps)
        self.assertTrue(all(0 < seconds <= .05 for seconds in clock.sleeps))

    def test_partial_is_distinct_and_requires_explicit_allowance(self):
        source = make_dataset(self.root / "partial", complete=False)
        calls = []
        denied = run_yolop_replay(source, consumer=calls.append, speed=0)
        self.assertEqual(denied["exit_code"], 1)
        self.assertEqual(denied["error_kind"], "incomplete_not_allowed")
        self.assertEqual(calls, [])
        allowed = run_yolop_replay(source, consumer=calls.append, speed=0, allow_incomplete=True)
        self.assertEqual(allowed["exit_code"], 0, allowed)
        self.assertFalse(allowed["source_complete"])
        self.assertTrue(allowed["replay_completed"])
        self.assertEqual(len(calls), 3)

    def test_preflight_hash_error_delivers_zero_even_when_partial_allowed(self):
        path = self.dataset / "samples/0003/rgb.nv12"
        data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(data)
        received = []
        report = run_yolop_replay(self.dataset, consumer=received.append, speed=0, allow_incomplete=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["error_kind"], "invalid_diagnostics")
        self.assertEqual(received, [])

    def test_active_nan_is_rejected_after_consistent_index_and_slice_rehash(self):
        directory = self.dataset / "samples/0003"
        for name in ("nn-buffer.bin", "det_out.fp16"):
            path = directory / name
            raw = bytearray(path.read_bytes()); raw[:2] = b"\x00\x7e"; path.write_bytes(raw)
        refresh_index(directory)
        received = []
        report = run_yolop_replay(self.dataset, consumer=received.append, speed=0, allow_incomplete=True)
        self.assertEqual(report["exit_code"], 1, report)
        self.assertEqual(received, [])

    def test_cancel_then_new_run_does_not_reuse_replay_epoch(self):
        received = []
        one = run_yolop_replay(self.dataset, consumer=received.append, speed=0,
                               cancelled=lambda: len(received) >= 1)
        self.assertEqual(one["exit_code"], 130, one)
        self.assertFalse(one["replay_completed"])
        self.assertEqual(len(received), 1)
        two = run_yolop_replay(self.dataset, speed=0)
        self.assertEqual(two["exit_code"], 0, two)
        self.assertNotEqual(one["replay_instance"], two["replay_instance"])

    def test_api_cancel_during_trace_close_precedes_final_decision(self):
        output = self.root / "close-cancel"
        cancelled = [False]
        original_open = Path.open

        class Trace:
            def __init__(self, target):
                self.target = target

            def write(self, data):
                return self.target.write(data)

            def close(self):
                self.target.close()
                cancelled[0] = True

        def opened(path, *args, **kwargs):
            target = original_open(path, *args, **kwargs)
            return Trace(target) if path == output / "events.jsonl" else target

        with patch.object(Path, "open", opened):
            report = run_yolop_replay(self.dataset, output=output, speed=0,
                                      cancelled=lambda: cancelled[0])
        self.assertTrue(cancelled[0])
        self.assertEqual(report["replayed_samples"], 3)
        self.assertEqual(report["exit_code"], 130, report)
        self.assertFalse(report["replay_completed"])
        self.assertEqual(report["finalization"]["decision"], "abort")
        disk = read_json(output / "yolop-replay-result.json")
        self.assertFalse(disk["replay_completed"])
        self.assertEqual(disk["exit_code"], 130)

    def test_trace_preserves_metadata_before_consumer_mutation_and_errors(self):
        expected = read_json(self.dataset / "samples/0001/source.json")
        output = self.root / "trace"

        def consume(sample):
            sample.metadata["rgb"]["sequence"] = "999999"

        report = run_yolop_replay(self.dataset, output=output, consumer=consume, speed=0)
        self.assertEqual(report["exit_code"], 0, report)
        event = json.loads((output / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(event["source"], expected)

        def fail(_sample):
            raise RuntimeError("consumer rejected fixture")

        failed = run_yolop_replay(self.dataset, consumer=fail, speed=0)
        self.assertEqual(failed["exit_code"], 1)
        self.assertEqual(failed["consumer_calls"], 1)
        self.assertFalse(failed["replay_completed"])

    def test_source_overlap_and_existing_output_are_preserved(self):
        original = aggregate(self.dataset)
        with self.assertRaises(ValueError):
            run_yolop_replay(self.dataset, output=self.dataset / "new")
        existing = self.root / "existing"
        existing.mkdir(); (existing / "user.txt").write_text("preserve")
        with self.assertRaises(FileExistsError):
            run_yolop_replay(self.dataset, output=existing)
        self.assertEqual((existing / "user.txt").read_text(), "preserve")
        self.assertEqual(aggregate(self.dataset), original)

    def test_cli_runs_with_native_and_network_imports_forbidden(self):
        code = """import builtins,sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in ('depthai','numpy','cv2','paho','socket'):raise AssertionError(name)
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from xenova_vehicle.cli import main
raise SystemExit(main(['yolop-replay','--dataset',sys.argv[1],'--output',sys.argv[2],'--speed','0','--json']))
"""
        result = subprocess.run([sys.executable, "-S", "-c", code, str(self.dataset), str(self.root / "cli")],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["replayed_samples"], 3)
        self.assertFalse(report["live_authority"])

    def test_invalid_cli_speed_does_not_create_output(self):
        output = self.root / "not-created"
        for speed in ("nan", "inf", "-1", "17"):
            with self.subTest(speed=speed):
                result = subprocess.run([sys.executable, "-m", "xenova_vehicle", "yolop-replay",
                                         "--dataset", str(self.dataset), "--output", str(output), "--speed", speed],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())

    @unittest.skipUnless(sys.platform == "linux", "symbolic-link loop requires Linux")
    def test_cli_source_symlink_loop_is_an_argument_error_without_output(self):
        loop = self.root / "loop"
        loop.symlink_to("loop")
        output = self.root / "loop-output"
        result = subprocess.run([sys.executable, "-m", "xenova_vehicle", "yolop-replay",
                                 "--dataset", str(loop), "--output", str(output)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(output.exists())

    @unittest.skipUnless(sys.platform == "linux", "real signals require Linux")
    def test_sigint_sigterm_during_timing_are_structured_and_restartable(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            output = self.root / f"signal-{number}"
            process = subprocess.Popen([sys.executable, "-m", "xenova_vehicle", "yolop-replay",
                                        "--dataset", str(self.dataset), "--output", str(output),
                                        "--speed", "0.01", "--json"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                until = time.monotonic() + 10
                event = output / "events.jsonl"
                while time.monotonic() < until and process.poll() is None:
                    if event.exists() and event.stat().st_size:
                        break
                    time.sleep(.005)
                self.assertTrue(event.exists() and event.stat().st_size)
                process.send_signal(number)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 128 + number, stderr)
                report = json.loads(stdout)
                self.assertFalse(report["replay_completed"])
                self.assertLess(report["replayed_samples"], 3)
                self.assertFalse(report["live_authority"])
            finally:
                if process.poll() is None:
                    process.kill(); process.communicate()
        self.assertEqual(run_yolop_replay(self.dataset, speed=0)["exit_code"], 0)
