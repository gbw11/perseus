"""고정 자산의 변경·누락·검증후파일교체와 SDK metadata 불일치를 검사한다."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from xenova_vehicle.oak import yolop_asset as assets


MANIFEST = (Path(__file__).resolve().parents[2] / "assets" / "manifests" /
            "yolop-320x320-rvc2-ov2021.4-sh6.json")


def pinned_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@contextmanager
def small_asset_tree():
    """바이너리를 테스트에 복제하지 않고 hash/file 수명 동작만 작은 대체 bytes로 검사한다."""
    manifest = pinned_manifest()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        release = root / "release"
        release.mkdir()
        for index, item in enumerate(manifest["files"]):
            raw = ("fixture-" + str(index)).encode("ascii")
            (release / item["name"]).write_bytes(raw)
            item["bytes"] = len(raw)
            item["sha256"] = hashlib.sha256(raw).hexdigest()
            if item["name"] == assets.BLOB_NAME:
                blob_bytes = raw
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        canonical_hash = hashlib.sha256(assets._canonical(manifest)).hexdigest()
        with patch.object(assets, "MANIFEST_SHA256", canonical_hash), \
                patch.object(assets, "BLOB_SIZE", len(blob_bytes)), \
                patch.object(assets, "BLOB_SHA256", hashlib.sha256(blob_bytes).hexdigest()):
            yield release, path, manifest, blob_bytes


def fake_sdk_blob(manifest):
    expected = manifest["sdk_blob_metadata"]
    def tensor(value):
        return NS(**value)
    return NS(version=expected["sdk_inferred_version"], numShaves=expected["num_shaves"],
              numSlices=expected["num_slices"], stageCount=expected["stage_count"],
              networkInputs={key: tensor(value) for key, value in expected["inputs"].items()},
              networkOutputs={key: tensor(value) for key, value in expected["outputs"].items()})


class YolopAssetTests(unittest.TestCase):
    def test_repository_manifest_matches_product_pin(self):
        actual = hashlib.sha256(assets._canonical(pinned_manifest())).hexdigest()
        self.assertEqual(actual, assets.MANIFEST_SHA256)

    def test_changed_manifest_is_rejected_before_file_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            manifest = pinned_manifest()
            manifest["input"]["color_order"] = "RGB"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pinned S01"):
                assets.load_yolop_asset(Path(directory) / "absent", path)

    def test_duplicate_nonfinite_and_oversized_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for raw in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}',
                        '[' * 1100 + ']' * 1100, '"' + "a" * 65_537 + '"'):
                path.write_text(raw, encoding="utf-8")
                with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                    assets.load_yolop_asset(Path(directory), path)

    def test_loaded_bytes_are_retained_if_original_blob_changes(self):
        with small_asset_tree() as (release, path, manifest, original):
            loaded = assets.load_yolop_asset(release, path)
            self.assertEqual(loaded.blob_bytes, original)
            self.assertEqual(len(loaded.receipt["files"]), 5)
            (release / assets.BLOB_NAME).write_bytes(b"x" * len(original))
            blob = fake_sdk_blob(manifest)
            factory = MagicMock(return_value=blob)
            sdk = NS(__version__="2.29.0.0", OpenVINO=NS(Blob=factory))
            actual, metadata = assets.validated_sdk_blob(sdk, loaded)
            self.assertIs(actual, blob)
            self.assertEqual(metadata["num_shaves"], 6)
            factory.assert_called_once_with(list(original))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                assets.load_yolop_asset(release, path)

    def test_missing_truncated_or_changed_notice_is_rejected(self):
        for kind in ("missing", "truncated", "changed"):
            with self.subTest(kind=kind), small_asset_tree() as (release, path, manifest, _):
                target = release / manifest["files"][0]["name"]
                if kind == "missing":
                    target.unlink()
                elif kind == "truncated":
                    target.write_bytes(b"")
                else:
                    target.write_bytes(b"x" * target.stat().st_size)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    assets.load_yolop_asset(release, path)

    def test_sdk_tensor_or_compiler_metadata_mismatch_is_rejected(self):
        with small_asset_tree() as (release, path, manifest, _):
            loaded = assets.load_yolop_asset(release, path)
            for kind in ("shaves", "dtype", "offset", "stride"):
                blob = fake_sdk_blob(deepcopy(manifest))
                if kind == "shaves":
                    blob.numShaves = 8
                elif kind == "dtype":
                    blob.networkOutputs["det_out"].dataType = "DataType.FP32"
                elif kind == "offset":
                    blob.networkOutputs["drive_area_seg"].offset = 0
                else:
                    blob.networkOutputs["det_out"].strides = [2, 12, 75600]
                sdk = NS(__version__="2.29.0.0", OpenVINO=NS(Blob=lambda values: blob))
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "metadata differs"):
                    assets.validated_sdk_blob(sdk, loaded)

    def test_constructor_error_does_not_include_giant_argument_repr(self):
        with small_asset_tree() as (release, path, _, original):
            loaded = assets.load_yolop_asset(release, path)
            sdk = NS(__version__="2.29.0.0", OpenVINO=NS(
                Blob=MagicMock(side_effect=TypeError("giant raw model bytes: " + repr(original)))))
            with self.assertRaisesRegex(ValueError, "construction failed") as raised:
                assets.validated_sdk_blob(sdk, loaded)
            self.assertLess(len(str(raised.exception)), 100)
            self.assertNotIn("giant raw", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
