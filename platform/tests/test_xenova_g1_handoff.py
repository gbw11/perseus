"""G1 인계물은 현재 런타임·지연 import와 검증된 펌웨어를 함께 보존해야 한다."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("g1_handoff", ROOT/"platform/scripts/package-xenova-xenstore-g1-handoff.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xenova-g1-handoff-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.firmware = self.root/"firmware"
        self.firmware.mkdir()
        for name in handoff.ARTIFACTS:
            (self.firmware/name).write_bytes(("small packaging fixture "+name).encode())
        inputs = handoff.source_inputs(ROOT)
        self.manifest = {
            "schema_version": 1, "app": "xenova-xenstore-g1",
            "repository_head": "fixture-build-head",
            "source_files": inputs,
            "source_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "build_script_sha256": handoff.sha(ROOT/"platform/scripts/build-xenova-xenstore-g1.py"),
            "references": json.loads((ROOT/"platform/guests/zephyr/xen-store/references.lock.json").read_text()),
            "files": {n: handoff.file_info(self.firmware/n) for n in handoff.ARTIFACTS}}
        self.save_manifest()
        self.output = self.root/"handoff"

    def save_manifest(self):
        (self.firmware/"g1-build.json").write_text(json.dumps(self.manifest))

    def package(self):
        return handoff.package(ROOT, self.firmware, self.output, execute=True)

    def test_plan_does_not_create_output_or_staging(self):
        before = list(self.root.iterdir())
        result = handoff.package(ROOT, self.firmware, self.output)
        self.assertEqual(result["action"], "plan")
        self.assertEqual(before, list(self.root.iterdir()))

    def test_current_runtime_and_lazy_dependencies_are_bundled(self):
        self.package()
        manifest = handoff.verify(self.output)
        mapping = handoff.runtime_sources()
        self.assertEqual(len(mapping), 23)
        for target, source in mapping.items():
            with self.subTest(target=target):
                self.assertEqual((self.output/"runtime"/target).read_bytes(), (ROOT/source).read_bytes())
        for name in ("xenova_execution", "xenova_recovery", "xenova_management", "xenova_domain_identity", "xenova_resources", "xenova_zephyr_control", "xenova_zephyr_transition", "xen_config"):
            self.assertTrue((self.output/"runtime"/(name+".py")).is_file())
        self.assertEqual(manifest["firmware_build_head"], "fixture-build-head")
        self.assertEqual((self.output/"firmware/g1-build.json").read_bytes(), (self.firmware/"g1-build.json").read_bytes())

    def test_fresh_isolated_process_imports_complete_published_runtime(self):
        self.package()
        code = (
            "import importlib.util,sys;from pathlib import Path;"
            f"d=Path({str(self.output)!r});"
            "s=importlib.util.spec_from_file_location('lab',d/'scripts/xenova-xenstore-g1-lab.py');"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);p=m.platform(d/'runtime');"
            "import xenova_execution,xenova_recovery,xenova_management,xenova_first_boot,xen_config;"
            "assert p.execution().__class__.__module__=='xenova_execution';"
            "r=xenova_management.Manager().call('create',{});assert r['error']['code']=='INVALID_SPEC';"
            "assert str(p.data)=='/srv/xenova' and str(p.runtime)=='/run/xenova';"
            "assert all(str(d/'runtime') in sys.modules[n].__file__ for n in "
            "['xenova_vm','xenova_execution','xenova_recovery','xenova_management','first_install_contract']);"
            "print('isolated complete G1 runtime PASS')")
        p = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                           cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn("isolated complete G1 runtime PASS", p.stdout)

    def test_changed_firmware_rejected_before_any_output(self):
        (self.firmware/"zephyr.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "firmware hash"):
            self.package()
        self.assertEqual({p.name for p in self.root.iterdir()}, {"firmware"})

    def test_different_g1_source_rejected_without_retagging(self):
        self.manifest["source_sha256"] = "0"*64
        self.save_manifest()
        before = (self.firmware/"g1-build.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "different app/module"):
            self.package()
        self.assertEqual((self.firmware/"g1-build.json").read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_missing_overlay_in_old_firmware_source_manifest_is_rejected(self):
        relative = "xenstore-test-app/app.overlay"
        self.assertEqual(self.manifest["source_files"][relative],
                         handoff.file_info(ROOT/"platform/guests/zephyr"/relative))
        del self.manifest["source_files"][relative]
        self.manifest["source_sha256"] = hashlib.sha256(
            json.dumps(self.manifest["source_files"], sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "different app/module"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_stale_build_script_rejected(self):
        self.manifest["build_script_sha256"] = "0"*64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "build script differs"):
            self.package()

    def test_missing_lazy_import_dependency_is_not_accepted(self):
        mapping = handoff.runtime_sources()
        del mapping["xenova_execution.py"]
        with self.assertRaisesRegex(ValueError, "unbundled runtime dependency"):
            handoff.check_imports(ROOT, mapping)

    def test_stale_or_modified_runtime_detected_on_verify(self):
        self.package()
        path = self.output/"runtime/xenova_vm.py"
        path.chmod(0o644)
        path.write_text("# stale runtime\n")
        with self.assertRaisesRegex(ValueError, "handoff hash/size"):
            handoff.verify(self.output)

    def test_existing_output_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output/"keep"
        sentinel.write_bytes(b"existing published handoff")
        with self.assertRaisesRegex(ValueError, "new absolute path"):
            self.package()
        self.assertEqual(sentinel.read_bytes(), b"existing published handoff")

    def test_symlink_firmware_refused_before_copy(self):
        alias = self.root/"alias"
        alias.symlink_to(self.firmware, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "canonical source/firmware"):
            handoff.package(ROOT, alias, self.output, execute=True)

    def test_extra_or_symlink_payload_not_silently_accepted(self):
        self.package()
        self.output.chmod(0o755)
        extra = self.output/"old-runtime.py"
        extra.symlink_to(self.output/"runtime/xenova_vm.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            handoff.verify(self.output)


if __name__ == "__main__":
    unittest.main()
