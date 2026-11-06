"""실제 spawn/소켓/파일로 종료·고장 격리를 검증한다. OAK 하드웨어 시험은 아니다."""

from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import capture_fixtures as fixtures
from xenova_vehicle.capture_profile import CaptureProfile
from xenova_vehicle.runtime.capture import run_capture
from xenova_vehicle.runtime import receipts


class CaptureRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "capture"
        self.profile = CaptureProfile("FAKE_TEST_DEVICE", startup_timeout_s=5,
                                      close_timeout_s=0.5)

    def capture(self, source=fixtures.FakeSource, duration=0.35, **changes):
        report = run_capture(replace(self.profile, **changes), duration, self.output,
                             source_factory=source)
        self.assertTrue(report["process_exit_confirmed"], report)
        self.assertFalse(multiprocessing.active_children(), report)
        return report

    def test_duration_capture_records_original_bytes_and_clean_exit(self):
        report = self.capture()
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["exit_code"], 0)
        self.assertFalse(report["forced_termination"])
        self.assertFalse(report["metric_ready"])
        records = [json.loads(s) for s in (self.output / "frames.jsonl").read_text().splitlines()]
        self.assertGreaterEqual(len(records), 2)
        for record in records:
            raw = (self.output / record["payload_file"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record["sha256"])
            self.assertEqual(record["source_key"][1], record["sequence"])
            self.assertFalse(record["clock_evidence"]["metric_time_qualified"])
        rgb = next(r for r in records if r["stream"] == "rgb_video")
        depth = next(r for r in records if r["stream"] == "depth_right")
        self.assertNotEqual(rgb["stream_instance"], depth["stream_instance"])
        self.assertNotEqual(rgb["sequence"], depth["sequence"])
        stored = json.loads((self.output / "capture-result.json").read_text())
        self.assertEqual(stored["complete"], report["complete"])
        self.assertTrue(stored["process_exit_confirmed"])

    def test_boot_hang_is_killed_without_recording_success(self):
        started = time.monotonic()
        report = self.capture(fixtures.BootHangSource, startup_timeout_s=0.5)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(report["reason"], "startup_timeout")
        self.assertFalse(report["complete"])
        self.assertTrue(report["forced_termination"])

    def test_poll_hang_is_detected_even_when_worker_pid_is_alive(self):
        report = self.capture(fixtures.PollHangSource, duration=3)
        self.assertEqual(report["reason"], "capture_stalled")
        self.assertTrue(report["forced_termination"])

    def test_close_hang_does_not_report_success(self):
        report = self.capture(fixtures.CloseHangSource)
        self.assertFalse(report["complete"])
        self.assertTrue(report["forced_termination"])

    def test_long_korean_error_cannot_skip_device_close(self):
        report = self.capture(fixtures.FailingSource)
        self.assertFalse(report["complete"])
        self.assertFalse(report["forced_termination"])
        self.assertIn("RuntimeError", report["error"])
        self.assertTrue(report["last_worker_status"]["device_close_confirmed"])

    def test_final_status_is_retried_when_control_output_is_full(self):
        report = self.capture(fixtures.FinalRetrySource)
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["last_worker_status"]["kind"], "final")

    def test_one_missing_stream_cannot_pass_capture(self):
        report = self.capture(fixtures.NoDepthSource)
        self.assertFalse(report["complete"])
        self.assertIn("no synchronized", report["error"])

    def test_unexpected_process_exit_is_not_success(self):
        report = self.capture(fixtures.CrashingSource)
        self.assertFalse(report["complete"])
        self.assertEqual(report["worker_exit_code"], 7)

    def test_blocked_writer_is_reaped_without_waiting_for_sd(self):
        report = self.capture(fixtures.RecordingHangSource)
        self.assertFalse(report["complete"])
        self.assertTrue(report["forced_termination"])

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "user.txt"
        marker.write_text("preserve")
        report = self.capture()
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(marker.read_text(), "preserve")
        self.assertEqual(set(p.name for p in self.output.iterdir()), {"user.txt"})

    def test_receipt_io_hang_has_its_own_deadline(self):
        with patch.object(receipts, "_receipt_worker", fixtures.hang_receipt):
            result = receipts.write_receipt(self.output, {}, reserve=True, timeout_s=0.2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["process_exit_confirmed"])
        self.assertTrue(result["forced_termination"])
        self.assertFalse(multiprocessing.active_children())

    @unittest.skipUnless(sys.platform == "linux", "SIGINT semantics require Linux")
    def test_sigint_stops_and_closes_without_restarting(self):
        script = Path(self.temporary.name) / "interrupted.py"
        script.write_text("""import json,sys
from pathlib import Path
from xenova_vehicle.capture_profile import CaptureProfile
from xenova_vehicle.runtime.capture import run_capture
from capture_fixtures import FakeSource
if __name__ == '__main__':
    result=run_capture(CaptureProfile('FAKE',close_timeout_s=2),30,Path(sys.argv[1]),source_factory=FakeSource)
    print(json.dumps(result))
    raise SystemExit(result['exit_code'])
""")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join([str(Path(fixtures.__file__).parent),
                                                    environment.get("PYTHONPATH", "")])
        process = subprocess.Popen([sys.executable, str(script), str(self.output)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=environment)
        try:
            deadline = time.monotonic() + 10
            while not (self.output / "frames.jsonl").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=10)
            report = json.loads(stdout)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertFalse(report["complete"])
            self.assertTrue(report["process_exit_confirmed"])
            self.assertFalse(report["forced_termination"])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
