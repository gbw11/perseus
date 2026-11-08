"""명시 장치·고정 graph·유한 설정을 카메라 없이 검증한다."""

import json
from pathlib import Path
import tempfile
import unittest

from xenova_vehicle.capture_profile import CaptureProfile, load_profile


class CaptureProfileTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
            return load_profile(path)

    def test_explicit_profile_roundtrip(self):
        profile = CaptureProfile("OAK_TEST_1")
        self.assertEqual(self.load(profile.to_dict()), profile)
        self.assertEqual(profile.to_dict()["nn_requirement"], "ABSENT")
        self.assertIs(profile.to_dict()["jpeg_enabled"], False)

    def test_unknown_fields_and_implicit_device_are_rejected(self):
        base = CaptureProfile("OAK_TEST_1").to_dict()
        for change in ({"unrecognized": True}, {"device_mxid": "../other"},
                       {"device_mxid": "REPLACE_WITH_OAK_MXID"},
                       {"nn_requirement": "OPTIONAL"}, {"jpeg_enabled": 0},
                       {"schema_version": True}, {"fps": True}, {"fps": 30},
                       {"min_free_bytes": 0}, {"max_bytes": 5 * 1024**3},
                       {"sync_tolerance_us": 10_001}, {"startup_timeout_s": 91},
                       {"stall_timeout_s": 0.6}, {"close_timeout_s": 16}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load({**base, **change})
        del base["device_mxid"]
        with self.assertRaises(ValueError):
            self.load(base)

    def test_nan_duplicate_and_oversized_json_are_rejected(self):
        for value in ('{"schema_version":1,"schema_version":1}',
                      '{"fps":NaN}', '{"fps":Infinity}', '[]', '[' * 1100 + ']' * 1100,
                      '"' + "a" * 17_000 + '"'):
            with self.subTest(value=value[:50]), self.assertRaises(ValueError):
                self.load(value)

    def test_dataclass_itself_enforces_bounds(self):
        for change in ({"max_bytes": True}, {"startup_timeout_s": float("inf")},
                       {"stall_timeout_s": 0}, {"close_timeout_s": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                CaptureProfile("OAK_TEST_1", **change)
        self.assertEqual(CaptureProfile("OAK_TEST_1", startup_timeout_s=0.1).startup_timeout_s, 0.1)


if __name__ == "__main__":
    unittest.main()
