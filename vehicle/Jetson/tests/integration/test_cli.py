"""명령 진입·의존성 격리와 실제 진단 자식 프로세스의 종료를 검증한다."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from xenova_vehicle import cli, doctor


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, "-m", "xenova_vehicle", *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=20,
                              env={**os.environ, "PYTHONUTF8": "1"})

    def test_help_and_version(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doctor", result.stdout)
        self.assertEqual(self.invoke("--version").stdout.strip(), "0.1.0")

    def test_invalid_arguments_do_not_start_diagnostics(self):
        for args in ((), ("run",), ("doctor", "--timeout", "nan"),
                     ("doctor", "--timeout", "inf"), ("doctor", "--timeout", "-1"),
                     ("doctor", "--timeout", "31"), ("doctor", "--timeout", "abc")):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(*args).returncode, 2)

    def test_import_and_help_do_not_load_doctor_or_native_modules(self):
        code = """
import sys
import xenova_vehicle
import xenova_vehicle.cli
try:
    xenova_vehicle.cli.main(['--help'])
except SystemExit as exc:
    assert exc.code == 0
for name in ('xenova_vehicle.doctor', 'xenova_vehicle._doctor_probe',
             'xenova_vehicle.runtime.capture', 'xenova_vehicle.oak.device',
             'xenova_vehicle.recording.writer', 'depthai', 'cv2', 'numpy'):
    assert name not in sys.modules, name
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_capture_help_stays_light_and_invalid_options_do_not_create_output(self):
        result = self.invoke("capture", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--duration-s", result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            for duration in ("nan", "inf", "-1", "301"):
                result = self.invoke("capture", "--profile", "missing.json", "--output", str(output),
                                     "--duration-s", duration)
                self.assertEqual(result.returncode, 2, result.stderr)
            profile = Path(directory) / "profile.json"
            profile.write_text('{"schema_version":1}', encoding="utf-8")
            result = self.invoke("capture", "--profile", str(profile), "--output", str(output))
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.exists())

    def test_runtime_video_help_is_light_and_invalid_address_never_starts_runtime(self):
        code = """
import sys
from xenova_vehicle import cli
try:
    cli.main(['runtime', '--help'])
except SystemExit as exc:
    assert exc.code == 0
for name in ('xenova_vehicle.doctor', 'xenova_vehicle.runtime.resident_runtime',
             'xenova_vehicle.runtime.http_video', 'xenova_vehicle.oak.yolop_device',
             'depthai', 'cv2', 'numpy', 'sqlite3'):
    assert name not in sys.modules, name
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
            text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--http-host', result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'not-created'
            base = ('runtime', '--profile', 'missing.json', '--state-dir', directory,
                '--database', str(Path(directory) / 'not-created.sqlite3'), '--output', str(output))
            for extra in (('--http-host', 'localhost'), ('--http-host', '::1'),
                    ('--http-port', '0'), ('--http-port', '65536'), ('--http-port', 'nan')):
                with self.subTest(extra=extra):
                    result = self.invoke(*base, *extra)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('HTTP', result.stderr)
            result = self.invoke(*base, '--http-port', '8080')
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('--http-port requires explicit --http-host', result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse((Path(directory) / 'not-created.sqlite3').exists())

    def test_real_json_command_always_reports_its_scope_and_exit_status(self):
        result = self.invoke("doctor", "--json", "--timeout", "5")
        report = json.loads(result.stdout)
        self.assertEqual(report["exit_code"], result.returncode)
        self.assertEqual(report["runtime_readiness"], "not_evaluated")
        self.assertEqual(report["scope"], "installation-and-device-enumeration")
        self.assertEqual(result.returncode,
                         int(any(check["status"] != "PASS" for check in report["checks"])))


class NativeProcessTests(unittest.TestCase):
    def probe(self, source, timeout=2):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "probe.py"
            helper.write_text(source, encoding="utf-8")
            with patch.object(doctor, "_PROBE_FILE", helper):
                return doctor.native_checks(timeout)

    def test_hung_native_process_is_killed_and_returns_failure(self):
        started = time.monotonic()
        checks = self.probe("import time\ntime.sleep(60)\n", timeout=0.2)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(checks[-1]["status"], "FAIL")
        self.assertIn("timed out", checks[-1]["summary"])
        self.assertTrue(checks[-1]["details"]["process_exit_confirmed"])

    def test_crashed_native_process_is_not_success(self):
        checks = self.probe("raise SystemExit(7)\n")
        self.assertEqual(checks[-1]["status"], "FAIL")
        self.assertIn("code 7", checks[-1]["summary"])

    def test_partial_report_is_not_success(self):
        source = """import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({'complete': False, 'checks': []}))
"""
        self.assertEqual(self.probe(source)[-1]["status"], "FAIL")

    def test_unwritable_temp_directory_is_reported_as_failure(self):
        with patch.object(doctor.tempfile, "TemporaryDirectory", side_effect=PermissionError("denied")):
            checks = doctor.native_checks(1)
        self.assertEqual(checks[0]["status"], "FAIL")
        self.assertIn("access failed", checks[0]["summary"])

    def test_native_stdout_does_not_corrupt_cli_json(self):
        checks = [{"name": name, "status": "PASS", "summary": "fixture", "details": {}}
                  for name in ("stdlib", "numpy", "opencv-python-headless", "depthai",
                               "paho-mqtt", "protobuf", "opencv_providers", "oak")]
        source = ("import json, pathlib, sys\nprint('native library noise')\n"
                  "pathlib.Path(sys.argv[1]).write_text("
                  + repr(json.dumps({"complete": True, "checks": checks})) + ")\n")
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "probe.py"
            helper.write_text(source, encoding="utf-8")
            stream = io.StringIO()
            with patch.object(doctor, "_PROBE_FILE", helper), \
                    patch.object(doctor, "system_checks", return_value=[]), redirect_stdout(stream):
                self.assertEqual(cli.main(["doctor", "--json"]), 0)
            self.assertEqual(json.loads(stream.getvalue())["checks"], checks)

    def test_probe_uses_parent_import_paths(self):
        source = """import json, pathlib, sys
parent_paths = json.loads(sys.argv[2])
checks = [{'name': name, 'status': 'PASS', 'summary': 'fixture', 'details': {}}
          for name in ['stdlib', 'numpy', 'opencv-python-headless', 'depthai',
                       'paho-mqtt', 'protobuf', 'opencv_providers', 'oak']]
checks[0]['details']['paths'] = parent_paths
pathlib.Path(sys.argv[1]).write_text(json.dumps({'complete': True, 'checks': checks}))
"""
        checks = self.probe(source)
        self.assertEqual(checks[0]["details"]["paths"], sys.path)


if __name__ == "__main__":
    unittest.main()
