"""Analysis-boundary tests; synthetic fixtures never stand in for app/board acceptance."""
import base64
import importlib.util
import json
from pathlib import Path
import struct
import signal
import socket
import subprocess
import sys
import time
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "platform/apps/common/xenova-drive-runtime/tools"


def bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


class DriveAnalysisTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("drive_analysis_test", TOOLS / "analyze.py")
        self.analyzer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.analyzer)
        self.temp = tempfile.TemporaryDirectory(prefix="xenova-drive-analysis-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boot = "11111111-1111-4111-8111-111111111111"
        self.instance = "22222222-2222-4222-8222-222222222222"
        self.publisher = "33333333-3333-4333-8333-333333333333"
        self.fixture()

    def fixture(self, stale=False, missing=False, corrupt=False):
        received, telemetry, inputs = [], [], []
        for app, vm in (("manual", "44444444-4444-4444-8444-444444444444"),
                        ("autonomous", "55555555-5555-4555-8555-555555555555")):
            session = {"vm_id": vm, "app_id": app, "boot_id": self.boot,
                       "agent_instance_id": self.instance, "execution_id": ""}
            summary = {"rows": 120, "overflow": "0", "capture_complete": True,
                       "counter_saturated": False, "clock_rc": 0, "task_rc": 0}
            lines = ["XD_SESSION " + json.dumps(session), "XD_SUMMARY " + json.dumps(summary)]
            velocity = 0 if stale else 1 if app == "manual" else .5
            for n in range(1, 121):
                begin = n * 20000
                row = [begin, begin + 10, begin + 11, begin + 12, begin + 20000, begin,
                       n, int(n == 1), 2 if n == 1 else 0, 1, 1, 100, n, n * 2 - 1, 0,
                       50, bits(velocity), bits(0), 999 if stale else 0, begin - 1, 1]
                lines.append("XD_ROW " + json.dumps(row))
                message = {"schema_version": "1.0", "message_id": str(n), "vm_id": vm,
                           "boot_id": self.boot, "agent_instance_id": self.instance,
                           "app_id": app, "seq": n * 2 - 1, "type": "drive.command",
                           "payload": {"generation": 100, "v_mps": velocity + (1 if corrupt and n == 10 else 0), "steer_rad": 0}}
                if not (missing and n == 10):
                    received.append(self.record(message, begin * 1000))
            received.append(self.record({"type": "app.heartbeat", "vm_id": vm, "boot_id": self.boot,
                                         "agent_instance_id": self.instance, "payload": {"state": "active"}}))
            (self.root / (app + ".stdout")).write_text("\n".join(lines))
            for kind in ("inventory", "heartbeat", "metrics", "events", "snapshot"):
                message = {"type": "telemetry." + kind, "vm_id": vm, "boot_id": self.boot,
                           "publisher_instance_id": self.publisher, "payload": {}}
                if kind == "metrics":
                    message["payload"]["samples"] = [{"name": "loop.completed_total", "value": "120", "status": "ok"}]
                telemetry.append(self.record(message))
        for kind in ("vehicle.control", "drive.input", "perception.result"):
            inputs.append({"message": {"type": kind, "seq": 1, "payload": {"generation": 100}}})
        for name, rows in (("drive", received), ("telemetry", telemetry), ("inputs", inputs)):
            (self.root / (name + ".jsonl")).write_text("\n".join(map(json.dumps, rows)) + "\n")
        for name in ("drive", "telemetry"):
            (self.root / (name + ".summary.json")).write_text(json.dumps({"invalid": 0, "queue_dropped": 0, "writer_errors": 0}))

    @staticmethod
    def record(message, timestamp=1):
        return {"payload_base64": base64.b64encode(json.dumps(message).encode()).decode(),
                "received_monotonic_ns": str(timestamp)}

    def test_topic_sequence_gaps_are_not_command_loss_and_window_includes_first_completion(self):
        result = self.analyzer.analyze(self.root, lossless=True)
        self.assertTrue(result["accepted"], result["failures"])
        self.assertEqual(result["apps"]["manual"]["matched_received"], 120)
        self.assertEqual(result["apps"]["manual"]["window_misses"], 1)
        self.assertEqual(result["apps"]["manual"]["window_skipped"], 2)

    def test_previous_app_heartbeat_cannot_prove_current_progress(self):
        path = self.root / "drive.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            message = json.loads(base64.b64decode(record["payload_base64"]))
            if message["type"] == "app.heartbeat":
                message["agent_instance_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                record["payload_base64"] = base64.b64encode(json.dumps(message).encode()).decode()
        path.write_text("\n".join(map(json.dumps, records)) + "\n")
        self.assertFalse(self.analyzer.analyze(self.root)["accepted"])

    def test_actual_input_age_validates_existing_stale_stop(self):
        self.fixture(stale=True)
        self.assertTrue(self.analyzer.analyze(self.root, lossless=True)["accepted"])

    def test_local_success_is_not_received_success(self):
        self.fixture(missing=True)
        result = self.analyzer.analyze(self.root, lossless=True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["apps"]["manual"]["unmatched_locally_accepted"], 1)
        self.assertTrue(self.analyzer.analyze(self.root, lossless=False)["accepted"])

    def test_received_payload_change_fails(self):
        self.fixture(corrupt=True)
        self.assertFalse(self.analyzer.analyze(self.root)["accepted"])

    def test_missing_v2_is_not_silently_passed(self):
        (self.root / "telemetry.jsonl").write_text("")
        self.assertFalse(self.analyzer.analyze(self.root)["accepted"])

    def test_retired_retained_inventory_cannot_override_current_execution(self):
        path = self.root / "telemetry.jsonl"
        old = {"type": "telemetry.inventory", "vm_id": "44444444-4444-4444-8444-444444444444",
               "boot_id": "66666666-6666-4666-8666-666666666666",
               "publisher_instance_id": self.publisher, "payload": {}}
        record = {**self.record(old), "binding_matches": False, "retained_delivery": True,
                  "interpretation": "HISTORICAL_ONLY"}
        path.write_text(path.read_text() + json.dumps(record) + "\n")
        self.assertTrue(self.analyzer.analyze(self.root)["accepted"])

    def test_truncated_or_overflowed_source_refused(self):
        path = self.root / "manual.stdout"
        original = path.read_text()
        path.write_text(original.replace('"overflow": "0"', '"overflow": "1"'))
        with self.assertRaises(ValueError):
            self.analyzer.analyze(self.root)
        path.write_text(original.rsplit("\n", 1)[0])
        with self.assertRaises(ValueError):
            self.analyzer.analyze(self.root)

    def test_exact_raw_quantile_is_not_a_histogram_upper_bound(self):
        self.assertEqual(self.analyzer.stats([1, 2, 7])["p99_us"], 7)
        self.assertNotIn("p99_upper_us", self.analyzer.stats([1, 2, 7]))

    def async_fixture(self, replace=False, missing=False):
        """The capture schema changes; the v1 wire messages remain unchanged."""
        for app in ("manual", "autonomous"):
            path = self.root / (app + ".stdout")
            lines = path.read_text().splitlines()
            out, sends = [], []
            for line in lines:
                if line.startswith("XD_SUMMARY "):
                    value = json.loads(line[len("XD_SUMMARY "):]); value["schema_version"] = 2
                    out.append("XD_SUMMARY " + json.dumps(value)); continue
                if line.startswith("XD_ROW "):
                    row = json.loads(line[len("XD_ROW "):])
                    disposition = 2 if replace and row[6] == 10 else 0
                    send = [row[6], row[1], row[3], row[3] + 50, row[3] + 60,
                            row[3] + 70, row[12], row[13], -114 if disposition else 0, disposition]
                    if not (missing and row[6] == 10): sends.append("XD_SEND " + json.dumps(send))
                    row[12] = row[13] = 0
                    line = "XD_ROW " + json.dumps(row)
                out.append(line)
            out += sends
            out.append("XD_IO " + json.dumps({"delivery_overflow": "0", "delivery_rows": len(sends)}))
            path.write_text("\n".join(out))

    def test_async_mailbox_acceptance_is_not_socket_send_and_replacement_is_not_loss(self):
        self.async_fixture(replace=True)
        result = self.analyzer.analyze(self.root, lossless=True)
        self.assertTrue(result["accepted"], result["failures"])
        self.assertEqual(result["apps"]["manual"]["matched_received"], 119)
        self.assertEqual(result["apps"]["manual"]["candidate_dispositions"], {0: 119, 2: 1})
        self.assertEqual(result["apps"]["manual"]["candidate_wait"]["max_us"], 50)
        self.assertEqual(result["apps"]["manual"]["mqtt_submit_elapsed"]["max_us"], 10)

    def test_async_accepted_without_terminal_record_cannot_pass(self):
        self.async_fixture(missing=True)
        with self.assertRaisesRegex(ValueError, "terminal result"):
            self.analyzer.analyze(self.root)

    def test_calculation_complete_does_not_hide_unconfirmed_transmissions(self):
        self.async_fixture()
        path = self.root / "manual.stdout"
        original = path.read_text()
        for prefix in ("XD_SUMMARY ", "XD_IO "):
            lines = []
            for line in original.splitlines():
                if line.startswith(prefix):
                    value = json.loads(line[len(prefix):])
                    value["transmissions_complete"] = False
                    line = prefix + json.dumps(value)
                lines.append(line)
            path.write_text("\n".join(lines))
            with self.assertRaisesRegex(ValueError, "transmission"):
                self.analyzer.analyze(self.root)

    def test_incomplete_window_clock_failure_and_counter_saturation_refuse(self):
        path = self.root / "manual.stdout"
        original = path.read_text()
        for before, after in (('"capture_complete": true', '"capture_complete": false'),
                              ('"clock_rc": 0', '"clock_rc": -5'),
                              ('"counter_saturated": false', '"counter_saturated": true')):
            path.write_text(original.replace(before, after))
            with self.assertRaises(ValueError):
                self.analyzer.analyze(self.root)


@unittest.skipUnless(sys.platform.startswith("linux"), "POSIX signal lifecycle")
class DriveReceiverShutdownTest(unittest.TestCase):
    def test_requested_stop_finishes_even_when_broker_is_unavailable(self):
        """Receiver shutdown evidence only; this does not prove Publisher delivery."""
        with tempfile.TemporaryDirectory(prefix="xenova-reader-stop-") as folder, socket.socket() as unavailable:
            root = Path(folder)
            credential = root / "credential.json"
            credential.write_text(json.dumps({"username": "fixture", "password": "fixture"}))
            credential.chmod(0o600)
            unavailable.bind(("127.0.0.1", 0))  # Reserved, deliberately not listening.
            output = root / "received.jsonl"
            child = subprocess.Popen([sys.executable, "-B", str(TOOLS / "drive_probe.py"), "receive",
                                      "--host", "127.0.0.1", "--port", str(unavailable.getsockname()[1]),
                                      "--credentials", str(credential), "--output", str(output),
                                      "--duration", "30", "--isolated-test-network"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                end = time.monotonic() + 5
                while not output.exists() and child.poll() is None and time.monotonic() < end:
                    time.sleep(0.02)
                self.assertTrue(output.exists(), "reader must initialize before signal")
                child.send_signal(signal.SIGTERM)
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
                summary = json.loads(output.with_suffix(".summary.json").read_text())
                self.assertTrue(summary["stop_requested"])
                self.assertEqual(summary["writer_errors"], 0)
                self.assertEqual(summary["queue_dropped"], 0)
                self.assertEqual(json.loads(stdout), summary)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)
