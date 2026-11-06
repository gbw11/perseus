"""receipt 허가 전 취소·허가 뒤 late signal·EOF·원 기한을 실제 Linux spawn에서 검사한다."""

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

import receipt_fixtures as fixtures
from xenova_vehicle.runtime import receipts
from xenova_vehicle.runtime.channel import ControlChannel, control_pair


RESULT_NAME = "yolop-bench-result.json"


@unittest.skipUnless(sys.platform == "linux", "real signals and seqpacket ownership require Linux")
class ReceiptFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.driver = self.root / "receipt_driver.py"
        self.driver.write_text("""from contextlib import nullcontext
import json,os,sys
from pathlib import Path
from unittest.mock import patch
import receipt_fixtures
from xenova_vehicle.runtime import receipts
from xenova_vehicle.runtime import bench_supervisor
from xenova_vehicle.runtime.yolop_bench import run_yolop_bench
from xenova_vehicle.yolop_profile import YolopBenchProfile
from yolop_fixtures import FakeYolopSource
if __name__ == '__main__':
    output=Path(sys.argv[1])
    mode=sys.argv[2]
    if mode == 'just_before_decision':
        original_write=bench_supervisor.write_receipt
        def before_decision(*args,**kwargs):
            original_decide=kwargs.get('decide')
            if original_decide is not None and not kwargs.get('reserve',False):
                def inject():
                    os.kill(os.getpid(),int(sys.argv[3]))
                    return original_decide()
                kwargs['decide']=inject
            return original_write(*args,**kwargs)
        context=patch.object(bench_supervisor,'write_receipt',before_decision)
    else:
        wrapper=getattr(receipt_fixtures,mode) if mode != 'normal' else None
        context=patch.object(receipts,'_receipt_worker',wrapper) if wrapper else nullcontext()
    profile=YolopBenchProfile('FAKE',output.parent/'assets',output.parent/'manifest.json',
                             startup_timeout_s=5,close_timeout_s=2,
                             nn_first_timeout_s=0.8,nn_stall_timeout_s=0.5)
    with context:
        result=run_yolop_bench(profile,0.65,output,source_factory=FakeYolopSource)
    print(json.dumps(result,ensure_ascii=True,allow_nan=False),flush=True)
    raise SystemExit(result['exit_code'])
""", encoding="utf-8")

    def start_driver(self, output, mode, signal_number=None):
        args = [sys.executable, str(self.driver), str(output), mode]
        if signal_number is not None:
            args.append(str(signal_number))
        return subprocess.Popen(args,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True,
                                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path),
                                     "PYTHONUTF8": "1"})

    def wait_marker(self, process, output, phase):
        path = output / f"{phase}.marker.json"
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.005)
        self.assertTrue(path.exists(), f"receipt marker absent: {phase}; driver exit={process.poll()}")
        # open/flush 경계의 짧은 부분 파일을 읽어 실패하지 않도록 완전 JSON까지 기다린다.
        while time.monotonic() < deadline:
            try:
                return json.loads(path.read_text(encoding="ascii"))
            except json.JSONDecodeError:
                time.sleep(0.001)
        self.fail(f"receipt marker JSON did not finish: {phase}")

    def assert_pid_reaped(self, pid):
        self.assertIsInstance(pid, int)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def finish_driver(self, process, expected_exit):
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, expected_exit, stderr or stdout)
        result = json.loads(stdout)
        self.assertTrue(result["process_exit_confirmed"], result)
        self.assertTrue(result["final_receipt"]["process_exit_confirmed"], result)
        self.assertFalse(result["final_receipt"]["forced_termination"], result)
        self.assert_pid_reaped(result["worker_pid"])
        self.assert_pid_reaped(result["final_receipt"]["pid"])
        return result

    def clean_driver(self, process):
        try:
            # 테스트가 만든 별도 session만 정리한다. 실패 시 지연 fixture도 남기지 않는다.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.communicate(timeout=5)

    def seed_result(self, output):
        output.mkdir()
        initial = {"complete": False, "exit_code": 1, "reason": "reserved"}
        raw = (json.dumps(initial, sort_keys=True) + "\n").encode("ascii")
        (output / RESULT_NAME).write_bytes(raw)
        return raw

    def test_normal_bench_has_committed_disk_and_reaped_receipt_owner(self):
        output = self.root / "normal"
        process = self.start_driver(output, "normal")
        try:
            result = self.finish_driver(process, 0)
            self.assertTrue(result["complete"], result)
            self.assertEqual(result["final_receipt"]["decision"], "commit")
            self.assertEqual(result["final_receipt"]["too_late_signals"], {"2": 0, "15": 0})
            self.assertTrue(json.loads((output / RESULT_NAME).read_text())["complete"])
        finally:
            self.clean_driver(process)

    def test_prepared_callback_is_called_once_for_normal_publication(self):
        output = self.root / "once"
        self.seed_result(output)
        calls = []

        def decide():
            calls.append(time.monotonic())
            return {"kind": "commit"}

        result = receipts.write_receipt(output, {"complete": True, "exit_code": 0},
                                         filename=RESULT_NAME, deadline=time.monotonic() + 7,
                                         decide=decide)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["decision"], "commit")
        self.assertTrue(result["process_exit_confirmed"])
        self.assert_pid_reaped(result["pid"])
        self.assertFalse(multiprocessing.active_children())

    def test_signal_before_prepared_publishes_abort_for_both_signals(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                output = self.root / f"before-{number}"
                process = self.start_driver(output, "delay_before_prepared")
                try:
                    marker = self.wait_marker(process, output, "before-prepared")
                    os.kill(marker["pid"], 0)
                    self.assertFalse(json.loads((output / RESULT_NAME).read_text())["complete"])
                    process.send_signal(number)
                    result = self.finish_driver(process, 128 + number)
                    disk = json.loads((output / RESULT_NAME).read_text())
                    self.assertFalse(result["complete"], result)
                    self.assertFalse(disk["complete"])
                    self.assertEqual(disk["exit_code"], 128 + number)
                    self.assertEqual(disk["reason"], "interrupted" if number == signal.SIGINT else "terminated")
                    self.assertEqual(result["final_receipt"]["decision"], "abort")
                    self.assertEqual(result["final_receipt"]["pid"], marker["pid"])
                    self.assertEqual(result["final_receipt"]["too_late_signals"], {"2": 0, "15": 0})
                finally:
                    self.clean_driver(process)

    def test_signal_after_commit_is_reported_too_late_and_keeps_committed_result(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                output = self.root / f"after-{number}"
                process = self.start_driver(output, "delay_after_commit")
                try:
                    marker = self.wait_marker(process, output, "after-commit")
                    os.kill(marker["pid"], 0)
                    self.assertIsNone(process.poll())
                    process.send_signal(number)
                    result = self.finish_driver(process, 0)
                    disk = json.loads((output / RESULT_NAME).read_text())
                    self.assertTrue(result["complete"], result)
                    self.assertTrue(disk["complete"])
                    self.assertEqual(disk["exit_code"], 0)
                    self.assertEqual(result["final_receipt"]["decision"], "commit")
                    self.assertEqual(result["final_receipt"]["pid"], marker["pid"])
                    self.assertEqual(result["final_receipt"]["too_late_signals"][str(number)], 1)
                    other = "15" if number == signal.SIGINT else "2"
                    self.assertEqual(result["final_receipt"]["too_late_signals"][other], 0)
                finally:
                    self.clean_driver(process)

    def test_signal_immediately_before_decision_wins_over_normal_commit(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                output = self.root / f"at-decision-{number}"
                process = self.start_driver(output, "just_before_decision", number)
                try:
                    result = self.finish_driver(process, 128 + number)
                    disk = json.loads((output / RESULT_NAME).read_text())
                    self.assertFalse(result["complete"], result)
                    self.assertFalse(disk["complete"])
                    self.assertEqual(disk["exit_code"], 128 + number)
                    self.assertEqual(result["final_receipt"]["decision"], "abort")
                    self.assertEqual(result["final_receipt"]["too_late_signals"], {"2": 0, "15": 0})
                finally:
                    self.clean_driver(process)

    def test_actual_final_write_error_keeps_false_reservation_without_decision(self):
        output = self.root / "write-error"
        initial = self.seed_result(output)
        calls = []
        with patch.object(receipts, "_receipt_worker", fixtures.collide_temporary):
            result = receipts.write_receipt(output, {"complete": True, "exit_code": 0},
                                             filename=RESULT_NAME, deadline=time.monotonic() + 7,
                                             decide=lambda: calls.append(True) or {"kind": "commit"})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_type"], "FileExistsError")
        self.assertIsNone(result["decision"])
        self.assertEqual(calls, [])
        self.assertTrue(result["process_exit_confirmed"])
        self.assertFalse(result["forced_termination"])
        self.assertEqual((output / RESULT_NAME).read_bytes(), initial)
        collision = output / (RESULT_NAME + f".tmp.{result['pid']}")
        self.assertEqual(collision.read_bytes(), b"owned-receipt-collision-fixture")
        self.assert_pid_reaped(result["pid"])
        self.assertFalse(multiprocessing.active_children())

    def test_prepare_hang_obeys_original_deadline_and_preserves_false_reservation(self):
        output = self.root / "timeout"
        initial = self.seed_result(output)
        began = time.monotonic()
        deadline = began + 3.0
        calls = []
        with patch.object(receipts, "_receipt_worker", fixtures.hang_before_prepared):
            result = receipts.write_receipt(output, {"complete": True, "exit_code": 0},
                                             filename=RESULT_NAME, timeout_s=1.0, deadline=deadline,
                                             decide=lambda: calls.append(True) or {"kind": "commit"})
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["forced_termination"])
        self.assertTrue(result["process_exit_confirmed"])
        self.assertEqual(calls, [])
        self.assertIsNone(result["decision"])
        self.assertLess(time.monotonic() - began, 3.4)
        self.assertTrue((output / "hung-before-prepared.marker.json").exists())
        self.assertEqual((output / RESULT_NAME).read_bytes(), initial)
        self.assert_pid_reaped(result["pid"])
        self.assertFalse(multiprocessing.active_children())

    def test_parent_eof_before_authorization_leaves_false_reservation(self):
        output = self.root / "eof"
        initial = self.seed_result(output)
        parent, child = control_pair()
        channel = ControlChannel(parent)
        process = multiprocessing.get_context("spawn").Process(
            target=receipts._receipt_worker,
            args=(child, str(output), {"complete": True, "exit_code": 0}, False, RESULT_NAME))
        process.start()
        pid = process.pid
        child.close()
        try:
            until = time.monotonic() + 3
            prepared = False
            while time.monotonic() < until and not prepared:
                prepared = any(value.get("kind") == "prepared" for value in channel.receive())
                time.sleep(0.001)
            self.assertTrue(prepared)
            channel.close()  # 유일한 parent endpoint를 닫아 EOF를 전달한다.
            process.join(3)
            self.assertFalse(process.is_alive(), "receipt child did not exit after parent EOF")
            self.assertEqual(process.exitcode, 0)
            self.assertEqual((output / RESULT_NAME).read_bytes(), initial)
            self.assert_pid_reaped(pid)
        finally:
            channel.close()
            for action in (process.terminate, process.kill):
                if process.is_alive():
                    action()
                    process.join(1)
            if not process.is_alive():
                process.close()
        self.assertFalse(multiprocessing.active_children())


if __name__ == "__main__":
    unittest.main()
