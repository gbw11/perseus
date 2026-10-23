"""Independent attached v2 oracle validates bytes from the production C adapter."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "platform/guests/zephyr/telemetry"
CORE = BASE.parent / "diagnostics"


class TelemetryModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("telemetry_v2_oracle", BASE / "contract/tools/validate_contract.py")
        cls.oracle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.oracle)

    def test_attached_golden(self):
        result = subprocess.run([shutil.which("python3"), "-B", str(BASE / "contract/tools/validate_contract.py"), "--self-test"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_bytes(self):
        self.compile_and_validate(False)

    def test_sanitized_production_bytes(self):
        self.compile_and_validate(True)

    def compile_and_validate(self, sanitized):
        with tempfile.TemporaryDirectory(prefix="xenova-telemetry-") as directory:
            binary = Path(directory) / "producer"
            command = [shutil.which("cc"), "-std=c11", "-Wall", "-Wextra", "-Werror", "-g",
                       "-I" + str(BASE / "include"), "-I" + str(CORE / "include"),
                       str(CORE / "src/core.c"), str(BASE / "src/writer.c"), str(BASE / "src/model.c"),
                       str(BASE / "tests/emit.c"), "-o", str(binary)]
            if sanitized:
                command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())
            self.assertIn(b"PASS production model", result.stderr)
            received = []
            for line in result.stdout.splitlines():
                row = json.loads(line, object_pairs_hook=self.oracle.reject_duplicate_keys)
                message = row["message"]
                raw = line.split(b'"message":', 1)[1][:-1]
                self.oracle.validate(message, row["topic"], row["qos"], row["retain"], len(raw))
                received.append(message)
            self.assertEqual({m["type"] for m in received}, {"telemetry." + s for s in self.oracle.STREAMS})
            samples = [s for m in received if m["type"] == "telemetry.metrics" for s in m["payload"].get("samples", [])]
            self.assertTrue(any(s["name"] == "loop.last_jitter_us" and s["value"] == "-100" for s in samples))
            self.assertTrue(any(s["name"] == "memory.kernel_heap.used_bytes" and s["value"] == "9007199254741003" for s in samples))
            self.assertTrue(any(s["status"] == "collection_failed" for s in samples))
            self.assertTrue(any(s["status"] == "not_registered" for s in samples))
            if os.environ.get("XENOVA_TEST_LOG_DIR"):
                out = Path(os.environ["XENOVA_TEST_LOG_DIR"])
                out.mkdir(parents=True, exist_ok=True)
                (out / ("telemetry-model-sanitized.jsonl" if sanitized else "telemetry-model.jsonl")).write_bytes(result.stdout)


class TelemetryBootstrapTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("telemetry_bootstrap", ROOT / "platform/scripts/provision-xenova-telemetry.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix="xenova-telemetry-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.context = {"vm_id": str(uuid.uuid4()), "host_boot_id": str(uuid.uuid4()),
                        "execution_id": str(uuid.uuid4()), "domid": 42, "xen_unique_id": "18446744073709551615"}
        self.credential = {"vm_id": self.context["vm_id"], "broker_ipv4": "192.0.2.1", "port": 18883,
                           "username": self.context["vm_id"], "password": "synthetic-unit-test-password",
                           "trusted_internal_plaintext": True}
        self.source = {"inode": 123, "mtime_ns": 456, "bytes": 100}
        self.calls = []
        class Store:
            def apply(inner, *args):
                self.calls.append(args)
        self.store = Store()

    def apply(self, path, previous=None, observe=None, source=None):
        return self.module.provision(self.context, self.credential, source or self.source,
                                     self.directory / path, self.directory / previous if previous else None,
                                     store=self.store, observe=observe or (lambda: dict(self.context)))

    def test_private_receipt_idempotency_and_no_secret(self):
        first = self.apply("first.json")
        second = self.apply("first.json")
        self.assertEqual(first, second)
        self.assertEqual(self.calls[0][1], self.calls[1][1])
        receipt = (self.directory / "first.json").read_text()
        self.assertNotIn(self.credential["password"], receipt + json.dumps(first))
        self.assertEqual((self.directory / "first.json").stat().st_mode & 0o777, 0o600)

    def test_publisher_restart_preserves_boot_and_execution(self):
        first = self.apply("first.json")
        second = self.apply("second.json", "first.json")
        self.assertEqual(first["boot_id"], second["boot_id"])
        self.assertEqual(first["context"], second["context"])
        self.assertNotEqual(first["publisher_instance_id"], second["publisher_instance_id"])
        self.assertEqual(self.calls[-1][2]["publisher_id"], first["publisher_instance_id"])

    def test_changed_context_refuses_and_post_write_uncertainty_is_durable(self):
        changed = {**self.context, "xen_unique_id": "1"}
        with self.assertRaises(ValueError):
            self.apply("changed-before.json", observe=lambda: changed)
        self.assertEqual(self.calls, [])
        values = iter([self.context, changed])
        with self.assertRaises(ValueError):
            self.apply("changed-after.json", observe=lambda: next(values))
        self.assertEqual(json.loads((self.directory / "changed-after.json").read_text())["phase"], "uncertain")
        with self.assertRaises(ValueError):
            self.apply("changed-after.json")
        self.assertEqual(len(self.calls), 1)

    def test_new_boot_or_changed_credentials_cannot_reuse_receipt(self):
        self.apply("first.json")
        self.context["host_boot_id"] = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            self.apply("first.json")
        self.assertEqual(len(self.calls), 1)

    def test_store_outcome_uncertainty_prevents_blind_retry(self):
        def uncertain(*args):
            raise OSError("commit response lost")
        self.store.apply = uncertain
        with self.assertRaises(OSError):
            self.apply("uncertain.json")
        self.assertEqual(json.loads((self.directory / "uncertain.json").read_text())["phase"], "uncertain")
        with self.assertRaises(ValueError):
            self.apply("uncertain.json")

    def test_private_credentials_mode_identity_and_plaintext_boundary(self):
        path = self.directory / "credentials.json"
        path.write_text(json.dumps(self.credential))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context["vm_id"])
        path.chmod(0o600)
        self.assertEqual(self.module.credentials(path, self.context["vm_id"])[0], self.credential)
        with self.assertRaises(ValueError):
            self.module.credentials(path, str(uuid.uuid4()))
        path.write_text(json.dumps({**self.credential, "trusted_internal_plaintext": False}))
        with self.assertRaises(ValueError):
            self.module.credentials(path, self.context["vm_id"])
