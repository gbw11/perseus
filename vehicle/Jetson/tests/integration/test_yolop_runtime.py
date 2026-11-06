"""NN 벤치의 실제 spawn·유한 종료·별도 진단·실패를 검사한다. 카메라는 사용하지 않는다."""

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

import yolop_fixtures as fixtures
from xenova_vehicle.yolop_profile import YolopBenchProfile
from xenova_vehicle.runtime.yolop_bench import run_yolop_bench
from xenova_vehicle.recording.reader import DatasetError, DatasetReader


class YolopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "bench"
        self.profile = YolopBenchProfile("FAKE", self.root / "assets", self.root / "manifest.json",
                                         startup_timeout_s=5, close_timeout_s=2,
                                         nn_first_timeout_s=0.8, nn_stall_timeout_s=0.5)

    def run_bench(self, source=fixtures.FakeYolopSource, duration=0.65, **changes):
        result = run_yolop_bench(replace(self.profile, **changes), duration, self.output, source_factory=source)
        self.assertTrue(result["process_exit_confirmed"], result)
        self.assertFalse(multiprocessing.active_children(), result)
        self.assertFalse(result["metric_ready"])
        return result

    def test_normal_diagnostic_hashes_layout_origin_and_v1_separation(self):
        result = self.run_bench()
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["forced_termination"])
        self.assertFalse(result["nn_ready"])
        self.assertTrue((self.output / "yolop-bench-result.json").is_file())
        self.assertFalse((self.output / "capture-result.json").exists())
        manifest = json.loads((self.output / "bench-manifest.json").read_text())
        self.assertEqual(manifest["setup"]["pipeline_start_count"], 1)
        self.assertEqual(manifest["setup"]["blob_registration_count"], 1)
        samples = list((self.output / "samples").iterdir())
        self.assertGreaterEqual(len(samples), 1)
        self.assertLessEqual(len(samples), self.profile.diagnostic_samples)
        for directory in samples:
            for name, info in json.loads((directory / "sample-index.json").read_text())["payloads"].items():
                raw = (directory / name).read_bytes()
                self.assertEqual(len(raw), info["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), info["sha256"])
            meta = json.loads((directory / "source.json").read_text())
            self.assertEqual(meta["rgb"]["sequence"], meta["nn_input"]["sequence"])
            self.assertEqual(meta["nn_input"]["sequence"], meta["nn_output"]["sequence"])
            self.assertNotEqual(meta["rgb"]["sequence"], meta["depth"]["sequence"])
            self.assertTrue(meta["nn_input"]["metadata_only"])
            self.assertFalse(meta["nn_output"]["compiler_layout_used"])
        with self.assertRaises(DatasetError):
            DatasetReader(self.output).validate()

    def test_no_nn_has_original_finite_probe_deadline(self):
        start = time.monotonic()
        result = self.run_bench(fixtures.NoNnSource)
        self.assertFalse(result["complete"])
        self.assertLess(time.monotonic() - start, 8)
        # 같은 원 기한을 S와 A가 감시하므로 먼저 관측한 쪽의 timeout이 남는다.
        self.assertTrue("probe deadline" in result["error"]
                        or result["reason"] == "native_stage_timeout", result)

    def test_missing_passthrough_is_not_a_valid_saved_nn(self):
        result = self.run_bench(fixtures.NoPassthroughSource, duration=1.2)
        self.assertFalse(result["complete"])
        self.assertIn(result["reason"], ("nn_first_result_timeout", "nn_association_timeout"))
        self.assertEqual(result["last_worker_status"]["nn_bench"]["matched"], 0)

    def test_bad_asset_layout_and_golden_fail_with_close(self):
        for number, source in enumerate((fixtures.BadAssetSource, fixtures.BadLayoutSource,
                                         fixtures.BadGoldenSource)):
            self.output = self.root / f"bad-{number}"
            with self.subTest(source=source):
                result = self.run_bench(source)
                self.assertFalse(result["complete"])
                self.assertEqual(result["exit_code"], 1)
                self.assertTrue(result["error"])
                self.assertTrue(result["last_worker_status"]["device_close_confirmed"])
                if source in (fixtures.BadLayoutSource, fixtures.BadGoldenSource):
                    failure = self.output / "failure"
                    meta = json.loads((failure / "source.json").read_text())
                    self.assertTrue(meta["raw_unvalidated"])
                    self.assertFalse(meta["nn_ready"])
                    index = json.loads((failure / "sample-index.json").read_text())
                    for name, info in index["payloads"].items():
                        self.assertEqual(hashlib.sha256((failure / name).read_bytes()).hexdigest(), info["sha256"])

    def test_nn_stall_is_separate_from_camera_progress(self):
        result = self.run_bench(fixtures.NnStopsSource, duration=2)
        self.assertFalse(result["complete"])
        self.assertEqual(set(result["fault_streams"]), {"nn_input", "nn_output"})
        self.assertTrue(result["last_worker_status"]["last_stream_progress_j_us"]["rgb_video"])

    def test_missing_depth_is_not_hidden_by_rgb_and_nn(self):
        result = self.run_bench(fixtures.NoDepthSource, duration=3, startup_timeout_s=2)
        self.assertFalse(result["complete"])
        self.assertEqual(result["reason"], "capture_warmup_timeout")
        self.assertEqual(result["fault_streams"], ["depth_right"])

    def test_diagnostic_quota_failure_is_not_normal_completion(self):
        result = self.run_bench(max_bytes=1024**2)
        self.assertFalse(result["complete"])
        self.assertIn("quota", result["error"])
        self.assertFalse((self.output / "capture-result.json").exists())

    def test_fresh_but_unmatched_nn_cannot_renew_pairing_progress(self):
        result = self.run_bench(fixtures.PairingStopsSource, duration=2)
        self.assertFalse(result["complete"])
        self.assertEqual(result["reason"], "nn_association_stalled")
        self.assertEqual(result["fault_streams"], ["nn_pairing"])
        stats = result["last_worker_status"]["nn_bench"]
        self.assertEqual(stats["matched"], 1)
        self.assertGreater(stats["received"]["nn_input"], stats["matched"])
        self.assertGreater(stats["received"]["nn_output"], stats["matched"])

    def test_lower_nn_rate_and_bounded_single_drop_remain_valid(self):
        result = self.run_bench(fixtures.SlowIntermittentNnSource, duration=2,
                                nn_stall_timeout_s=1.1, nn_first_timeout_s=1.2)
        self.assertTrue(result["complete"], result)
        self.assertGreaterEqual(result["last_worker_status"]["nn_bench"]["matched"], 2)

    def test_close_hang_cannot_leave_success_or_unreaped_owner(self):
        result = self.run_bench(fixtures.CloseHangSource, close_timeout_s=0.3)
        self.assertFalse(result["complete"])
        self.assertTrue(result["forced_termination"])

    def test_existing_output_is_preserved(self):
        self.output.mkdir()
        (self.output / "marker").write_bytes(b"keep")
        result = self.run_bench()
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual((self.output / "marker").read_bytes(), b"keep")

    @unittest.skipUnless(sys.platform == "linux", "real signals require Linux")
    def test_signal_during_clean_close_cannot_report_complete(self):
        code = """from pathlib import Path
import json,sys
from xenova_vehicle.yolop_profile import YolopBenchProfile
from xenova_vehicle.runtime.yolop_bench import run_yolop_bench
from yolop_fixtures import CloseSignalSource
if __name__=='__main__':
 p=YolopBenchProfile('FAKE',Path(sys.argv[2]),Path('/tmp/manifest.json'),close_timeout_s=3)
 r=run_yolop_bench(p,0.65,Path(sys.argv[1]),source_factory=CloseSignalSource)
 print(json.dumps(r));raise SystemExit(r['exit_code'])
"""
        path = self.root / "close_signal_driver.py"
        path.write_text(code)
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                output = self.root / f"close-signal-{number}"
                marker = self.root / f"closing-{number}"
                process = subprocess.Popen([sys.executable, str(path), str(output), str(marker)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
                try:
                    deadline = time.monotonic() + 10
                    while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
                        time.sleep(0.01)
                    self.assertTrue(marker.exists())
                    # CLOSING 통지를 부모가 읽은 뒤에도 신호 결과가 일관돼야 한다.
                    time.sleep(0.1)
                    process.send_signal(number)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 128 + number, stderr)
                    result = json.loads(stdout)
                    self.assertFalse(result["complete"], result)
                    self.assertEqual(result["reason"], "interrupted" if number == signal.SIGINT else "terminated")
                    self.assertTrue(result["process_exit_confirmed"])
                    self.assertFalse(result["forced_termination"])
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

    @unittest.skipUnless(sys.platform == "linux", "real signals require Linux")
    def test_sigint_sigterm_and_restart_make_new_context(self):
        code = """from pathlib import Path
import json,sys
from xenova_vehicle.yolop_profile import YolopBenchProfile
from xenova_vehicle.runtime.yolop_bench import run_yolop_bench
from yolop_fixtures import FakeYolopSource
if __name__=='__main__':
 p=YolopBenchProfile('FAKE',Path('/tmp/assets'),Path('/tmp/manifest.json'),close_timeout_s=2)
 r=run_yolop_bench(p,30,Path(sys.argv[1]),source_factory=FakeYolopSource)
 print(json.dumps(r));raise SystemExit(r['exit_code'])
"""
        path = self.root / "signal_driver.py"
        path.write_text(code)
        epochs = []
        for number in (signal.SIGINT, signal.SIGTERM):
            output = self.root / f"signal-{number}"
            process = subprocess.Popen([sys.executable, str(path), str(output)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
            try:
                deadline = time.monotonic() + 10
                while not (output / "samples").exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                process.send_signal(number)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 128 + number, stderr)
                result = json.loads(stdout)
                self.assertFalse(result["complete"])
                self.assertTrue(result["process_exit_confirmed"])
                self.assertFalse(result["forced_termination"])
                epochs.append(result["context"]["pipeline_instance"])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertNotEqual(*epochs)
