"""이미지 재빌드의 ACL·템플릿 라우팅·sparse 복사 경계를 실제 구현으로 검사한다."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from first_install_fixture import ROOT, assemble

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

resolver = load("image_rebuild_resolver", ROOT / "platform/scripts/resolve-xenova-templates.py")
broker = load("image_rebuild_broker", ROOT / "platform/deploy/mosquitto/scripts/xenova_broker.py")


class ImageRebuildContractTest(unittest.TestCase):
    def test_backend_can_read_all_v2_telemetry_but_has_no_v2_write_grant(self):
        rules = broker.policy(ROOT / "platform/deploy/mosquitto/etc/policy.json")
        grants = broker.grants(broker.acl(broker.default_accounts(rules), rules))["backend"]
        self.assertIn("topic read xenova/v2/vms/+/telemetry/+", grants)
        self.assertFalse(any(g.startswith("topic write xenova/v2/") for g in grants))
        self.assertIn("topic write xenova/v1/apps/manual/input", grants)

    def test_current_drive_catalog_routes_to_rtos_without_requiring_legacy_aliases(self):
        catalog = json.loads((ROOT / "platform/guests/default-templates.lock.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for entry in catalog["templates"]:
                dest = root / entry["directory"]
                dest.mkdir(parents=True)
                if entry["guest_kind"] == "linux":
                    (dest / "template.json").write_text(json.dumps(
                        {"platform": "xenova", "version": entry["version"]}))
            selected = next(e for e in catalog["templates"] if e["guest_kind"] == "zephyr")
            with patch.object(resolver.assembly, "template_info") as linux, \
                 patch.object(resolver.assembly.rtos, "info", return_value={
                     "id": selected["id"], "metadata": {"template_version": selected["version"]}}) as rtos:
                result = resolver.resolve(root, catalog)
            self.assertEqual(len(result["linux_templates"]), 2)
            self.assertEqual(len(result["rtos_templates"]), 1)
            self.assertEqual(linux.call_count, 2)
            rtos.assert_called_once_with(root / selected["directory"], selected["manifest_sha256"])

    def test_sparse_copy_preserves_bytes_offsets_and_guard_regions(self):
        chunk = 8 * 1024 * 1024
        payload = b"A" * chunk + bytes(chunk) + b"B" * 31
        prefix, suffix = b"BOOT-GUARD", b"AFTER-GUARD"
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "target"
            src.write_bytes(payload)
            with dst.open("wb") as out:
                out.write(prefix)
                out.truncate(len(prefix) + len(payload) + len(suffix))
                out.seek(len(prefix) + len(payload))
                out.write(suffix)
            assemble.copy_range(src, dst, len(prefix), len(payload), zeroed_destination=True)
            self.assertEqual(dst.read_bytes(), prefix + payload + suffix)
            self.assertEqual(src.read_bytes(), payload)
            stat = dst.stat()
            if hasattr(stat, "st_blocks"):
                self.assertLess(stat.st_blocks * 512, stat.st_size * 3 // 4)

    def test_zeroed_stream_preserves_compressed_bytes_and_trailing_zero_size(self):
        import bz2
        import io
        payload = b"BOOT" + bytes(2 * 1024 * 1024) + b"DATA" + bytes(1024 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "expanded"
            with bz2.open(io.BytesIO(bz2.compress(payload)), "rb") as source, target.open("xb") as out:
                assemble._copy_zeroed_stream(source, out)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(target.stat().st_size, len(payload))
            self.assertLess(target.stat().st_blocks * 512, len(payload) // 4)

    def test_default_copy_overwrites_existing_nonzero_bytes_with_zeros(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "target"
            src.write_bytes(bytes(4096))
            dst.write_bytes(b"X" * 8192)
            assemble.copy_range(src, dst, 1024, 4096)
            self.assertEqual(dst.read_bytes(), b"X" * 1024 + bytes(4096) + b"X" * 3072)

    def test_truncated_source_is_not_accepted_as_complete_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "target"
            src.write_bytes(b"short")
            dst.write_bytes(bytes(4096))
            with self.assertRaisesRegex(ValueError, "truncated prepared data filesystem"):
                assemble.copy_range(src, dst, 0, 4096, zeroed_destination=True)
