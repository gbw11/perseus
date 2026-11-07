"""별도 YOLOP profile과 기존 no-NN schema의 명시적 분리를 검사한다."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from xenova_vehicle.capture_profile import load_profile
from xenova_vehicle.yolop_profile import YolopBenchProfile, load_yolop_profile


class YolopProfileTests(unittest.TestCase):
    def test_explicit_paths_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = YolopBenchProfile("FAKE", root / "assets", root / "manifest.json").to_dict()
            value["asset_dir"] = "assets"
            value["asset_manifest"] = "manifest.json"
            path = root / "profile.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            profile = load_yolop_profile(path)
            self.assertEqual(profile.asset_dir, root / "assets")
            self.assertEqual(profile.asset_manifest, root / "manifest.json")
            self.assertEqual(profile.to_dict()["nn_requirement"], "REQUIRED")
            with self.assertRaises(ValueError):
                load_profile(path)

    def test_no_implicit_model_device_or_invalid_deadlines(self):
        profile = YolopBenchProfile("FAKE", Path("assets"), Path("manifest.json"))
        for changes in ({"device_mxid": "REPLACE_DEVICE"}, {"diagnostic_samples": 0},
                        {"nn_first_timeout_s": float("nan")}, {"nn_stall_timeout_s": 16},
                        {"stall_timeout_s": 1}, {"fps": 30}, {"max_bytes": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(profile, **changes)

    def test_unknown_fields_duplicate_keys_and_wrong_mode_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            original = YolopBenchProfile("FAKE", Path("assets"), Path("manifest.json")).to_dict()
            for change in ({"nn_requirement": "ABSENT"}, {"jpeg_enabled": True},
                           {"asset_dir": ""}, {"unexpected": 1}):
                path.write_text(json.dumps({**original, **change}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_yolop_profile(path)
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_yolop_profile(path)
