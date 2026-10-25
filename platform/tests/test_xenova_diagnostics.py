"""Compile production diagnostics core with deterministic clocks and sanitizers."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "platform/guests/zephyr/diagnostics"


class DiagnosticsTest(unittest.TestCase):
    def test_production_core(self):
        self.compile_and_run(False)

    def test_production_core_sanitized(self):
        self.compile_and_run(True)

    def compile_and_run(self, sanitized):
        cc = shutil.which("cc")
        self.assertIsNotNone(cc, "existing C compiler required")
        with tempfile.TemporaryDirectory(prefix="xenova-diagnostics-") as directory:
            binary = Path(directory) / "core-test"
            command = [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                       "-I" + str(BASE / "include"), str(BASE / "src/core.c"),
                       str(BASE / "tests/test_core.c"), "-o", str(binary)]
            if sanitized:
                command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS:", result.stdout)
            if os.environ.get("XENOVA_TEST_LOG_DIR"):
                output = Path(os.environ["XENOVA_TEST_LOG_DIR"])
                output.mkdir(parents=True, exist_ok=True)
                (output / ("diagnostics-sanitized.log" if sanitized else "diagnostics-native.log")).write_text(
                    result.stdout + result.stderr)
