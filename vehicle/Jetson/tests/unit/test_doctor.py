"""doctor의 설치 정보·검색·보고서 경계를 장치 연결 없이 검증한다."""

from importlib import metadata
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from xenova_vehicle import _doctor_probe as probe
from xenova_vehicle import doctor


def check(name, status="PASS"):
    return {"name": name, "status": status, "summary": "test result", "details": {}}


class PackageChecksTests(unittest.TestCase):
    def distribution_info(self, version="1.26.4", files=("module.py",)):
        root = Path("/fake/site-packages")
        return SimpleNamespace(version=version, files=None if files is None else list(map(Path, files)),
                               locate_file=lambda item: root / item)

    def package_result(self, distribution="numpy", module_name="numpy",
                       expected="1.26.4", installed="1.26.4", module=None,
                       recorded_files=("module.py",)):
        if module is None:
            module = SimpleNamespace(__file__="/fake/site-packages/module.py",
                                     __version__=expected)
        distribution_info = self.distribution_info(installed, recorded_files)
        with patch.object(probe.metadata, "distribution", return_value=distribution_info), \
                patch.object(probe.importlib, "import_module", return_value=module):
            return probe.package_check(distribution, module_name, expected)

    def test_import_failure_preserves_distribution_evidence(self):
        distribution_info = self.distribution_info()
        with patch.object(probe.metadata, "distribution", return_value=distribution_info), \
                patch.object(probe.importlib, "import_module",
                             side_effect=ImportError("missing native dependency")):
            result = probe.package_check("numpy", "numpy", "1.26.4")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["details"]["distribution_version"], "1.26.4")
        self.assertIn("ImportError: missing native dependency", result["details"]["error"])

    def test_importable_module_without_distribution_metadata_fails(self):
        module = SimpleNamespace(__file__="/unmanaged/numpy.py", __version__="1.26.4")
        with patch.object(probe.metadata, "distribution",
                          side_effect=metadata.PackageNotFoundError("numpy")), \
                patch.object(probe.importlib, "import_module", return_value=module):
            result = probe.package_check("numpy", "numpy", "1.26.4")
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["details"]["distribution_version"])
        self.assertEqual(result["details"]["module_path"], "/unmanaged/numpy.py")

    def test_distribution_and_imported_module_must_both_match(self):
        for installed, module_version in (("1.26.3", "1.26.4"), ("1.26.4", "2.0.0")):
            with self.subTest(installed=installed, module_version=module_version):
                module = SimpleNamespace(__file__="/fake/site-packages/module.py",
                                         __version__=module_version)
                result = self.package_result(installed=installed, module=module)
                self.assertEqual(result["status"], "FAIL")

    def test_native_module_versions_are_checked(self):
        for distribution, module_name, expected in (
                ("numpy", "numpy", "1.26.4"),
                ("depthai", "depthai", "2.29.0.0"),
                ("protobuf", "google.protobuf", "6.33.6")):
            with self.subTest(module=module_name):
                module = SimpleNamespace(__file__="/fake/site-packages/module.py", __version__="wrong")
                result = self.package_result(distribution, module_name, expected, expected, module)
                self.assertEqual(result["status"], "FAIL")

    def test_cv2_module_and_wheel_versions_use_their_own_formats(self):
        for module_version, expected_status in (("4.10.0", "PASS"),
                                                ("4.10.0.84", "FAIL"),
                                                ("4.9.0", "FAIL")):
            with self.subTest(module_version=module_version):
                module = SimpleNamespace(__file__="/fake/site-packages/cv2.so", __version__=module_version)
                result = self.package_result("opencv-python-headless", "cv2", "4.10.0.84",
                                             "4.10.0.84", module, recorded_files=("cv2.so",))
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["details"]["module_version"], module_version)

    def test_import_without_module_origin_fails(self):
        result = self.package_result(module=SimpleNamespace(__version__="1.26.4"))
        self.assertEqual(result["status"], "FAIL")

    def test_paho_does_not_require_a_module_version_attribute(self):
        result = self.package_result("paho-mqtt", "paho.mqtt.client", "2.1.0", "2.1.0",
                                     SimpleNamespace(__file__="/fake/site-packages/paho/mqtt/client.py"),
                                     recorded_files=("paho/mqtt/client.py",))
        self.assertEqual(result["status"], "PASS")

    def test_same_version_shadow_module_is_not_owned_by_distribution(self):
        for module_path in ("/shadow/module.py", "/fake/site-packages/module.py.shadow"):
            with self.subTest(module_path=module_path):
                module = SimpleNamespace(__file__=module_path, __version__="1.26.4")
                result = self.package_result(module=module)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["details"]["module_path"], module_path)

    def test_missing_or_empty_distribution_file_inventory_fails(self):
        for recorded_files in (None, ()):
            with self.subTest(recorded_files=recorded_files):
                result = self.package_result(recorded_files=recorded_files)
                self.assertEqual(result["status"], "FAIL")

    def test_record_ownership_compares_resolved_paths(self):
        result = self.package_result(recorded_files=("package/../module.py",))
        self.assertEqual(result["status"], "PASS")

    def test_exactly_one_headless_opencv_provider_is_required(self):
        for providers, expected_status in (
                ({}, "FAIL"),
                ({"opencv-python-headless": "4.10.0.84"}, "PASS"),
                ({"opencv-python": "4.10.0.84"}, "FAIL"),
                ({"opencv-python-headless": "4.10.0.84", "opencv-python": "4.10.0.84"}, "FAIL"),
                ({"opencv-python-headless": "4.10.0.84",
                  "opencv-contrib-python-headless": "4.10.0.84"}, "FAIL")):
            with self.subTest(providers=providers):
                def version(name):
                    if name not in providers:
                        raise metadata.PackageNotFoundError(name)
                    return providers[name]

                with patch.object(probe.metadata, "version", side_effect=version):
                    result = probe.opencv_providers_check()
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["details"]["providers"], providers)

    def test_stdlib_failure_does_not_skip_remaining_imports(self):
        def import_module(name):
            if name in ("ssl", "lzma"):
                raise ImportError("native extension unavailable")
            return SimpleNamespace(__file__="/fake/" + name + ".py", sqlite_version="3.test")

        with patch.object(probe.importlib, "import_module", side_effect=import_module) as imports:
            result = probe.stdlib_check()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(imports.call_count, 6)
        self.assertEqual(set(result["details"]["modules"]),
                         {"ssl", "sqlite3", "ctypes", "bz2", "lzma", "venv"})
        self.assertIn("ImportError", result["details"]["modules"]["ssl"]["error"])


class OakEnumerationTests(unittest.TestCase):
    def enumerate(self, infos=None, error=None):
        # Device 생성은 진단 범위를 벗어난다. 검색만 성공해도 생성 호출은 실패시킨다.
        device = Mock(side_effect=AssertionError("doctor must not open a Device"))
        device.getAllAvailableDevices = Mock(return_value=infos, side_effect=error)
        with patch.object(probe.importlib, "import_module",
                          return_value=SimpleNamespace(Device=device)) as imports:
            result = probe.oak_check(True)
        imports.assert_called_once_with("depthai")
        device.assert_not_called()
        device.getAllAvailableDevices.assert_called_once_with()
        return result

    def test_invalid_depthai_prevents_even_enumeration(self):
        with patch.object(probe.importlib, "import_module") as imports:
            result = probe.oak_check(False)
        imports.assert_not_called()
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("Not checked", result["summary"])

    def test_empty_discovery_fails_without_claiming_hardware_absence(self):
        result = self.enumerate([])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["details"]["devices"], [])
        self.assertIn("absent, busy, or inaccessible", result["details"]["note"])

    def test_sdk_enumeration_exception_is_a_failed_check(self):
        result = self.enumerate(error=RuntimeError("USB enumeration failed"))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["details"]["error"], "RuntimeError: USB enumeration failed")

    def test_discovery_reports_identity_without_opening_a_device(self):
        info = SimpleNamespace(getMxId=lambda: "fake-mxid", name="OAK-test", state="UNBOOTED")
        result = self.enumerate([info])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["details"]["devices"],
                         [{"mxid": "fake-mxid", "name": "OAK-test", "state": "UNBOOTED"}])
        self.assertIn("does not verify OAK-D-Lite model", result["details"]["note"])

    def test_enumeration_count_cap_is_enforced_and_output_is_bounded(self):
        info = SimpleNamespace(getMxId=lambda: "fake-mxid", name="n" * 300, state="s" * 300)
        result = self.enumerate([info] * 17)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["details"]["devices"]), 16)
        self.assertEqual(len(result["details"]["devices"][0]["name"]), 256)
        self.assertIn("17", result["summary"])

    def test_completed_probe_contains_eight_checks_and_gates_sdk_on_package_result(self):
        for depthai_status in ("PASS", "FAIL"):
            with self.subTest(depthai_status=depthai_status), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "probe.json"

                def package(distribution, *_args):
                    return check(distribution, depthai_status if distribution == "depthai" else "PASS")

                with patch.object(probe, "stdlib_check", return_value=check("stdlib")), \
                        patch.object(probe, "package_check", side_effect=package), \
                        patch.object(probe, "opencv_providers_check",
                                     return_value=check("opencv_providers")), \
                        patch.object(probe, "oak_check", return_value=check("oak")) as oak:
                    probe.run_probe(path)
                report = doctor._read_probe(path)
                self.assertTrue(report["complete"])
                self.assertIsNone(report["active"])
                self.assertEqual(len(report["checks"]), 8)
                self.assertEqual({item["name"] for item in report["checks"]},
                                 {"stdlib", "numpy", "opencv-python-headless", "depthai",
                                  "paho-mqtt", "protobuf", "opencv_providers", "oak"})
                oak.assert_called_once_with(depthai_status == "PASS")


class SystemChecksTests(unittest.TestCase):
    def system_result(self, *, python="3.11.18", system="Linux", machine="aarch64",
                      model="NVIDIA Jetson Nano Developer Kit",
                      release="# R32 (release), REVISION: 7.6, GCID: test", fallback=None):
        values = {"/proc/device-tree/model": model,
                  "/sys/firmware/devicetree/base/model": fallback,
                  "/etc/nv_tegra_release": release}
        with patch.object(doctor.platform, "python_version", return_value=python), \
                patch.object(doctor.platform, "system", return_value=system), \
                patch.object(doctor.platform, "machine", return_value=machine), \
                patch.object(doctor.platform, "libc_ver", return_value=("glibc", "2.27")), \
                patch.object(doctor, "_system_text", side_effect=lambda path: values[path.as_posix()]):
            return {item["name"]: item for item in doctor.system_checks()}

    def test_nano_l4t_release_is_parsed_without_claiming_carrier_or_jetpack_inventory(self):
        checks = self.system_result()
        self.assertTrue(all(item["status"] == "PASS" for item in checks.values()))
        self.assertIn("L4T 32.7.6", checks["l4t"]["summary"])
        self.assertEqual(checks["jetson"]["details"]["carrier_revision"], "not_verified")
        self.assertEqual(checks["l4t"]["details"]["jetpack_inventory"], "not_verified")

    def test_model_fallback_is_supported(self):
        checks = self.system_result(model=None, fallback="NVIDIA Jetson Nano")
        self.assertEqual(checks["jetson"]["status"], "PASS")

    def test_version_architecture_model_and_release_mismatches_fail_independently(self):
        for overrides, name in (({"python": "3.11.19"}, "python"),
                                ({"system": "Windows"}, "jetson"),
                                ({"machine": "x86_64"}, "jetson"),
                                ({"model": "NVIDIA Jetson Orin Nano"}, "jetson"),
                                ({"release": "# R32 (release), REVISION: 7.5"}, "l4t"),
                                ({"release": "32.7.6"}, "l4t"),
                                ({"release": None}, "l4t"),
                                ({"model": None}, "jetson")):
            with self.subTest(overrides=overrides):
                checks = self.system_result(**overrides)
                self.assertEqual(checks[name]["status"], "FAIL")
                for other_name, result in checks.items():
                    if other_name != name:
                        self.assertEqual(result["status"], "PASS")

    def test_system_read_error_becomes_a_failed_check(self):
        with patch.object(doctor, "_system_text", side_effect=PermissionError("not readable")):
            checks = {item["name"]: item for item in doctor.system_checks()}
        self.assertEqual(checks["jetson"]["status"], "FAIL")
        self.assertEqual(checks["l4t"]["status"], "FAIL")
        self.assertIn("not readable", checks["jetson"]["summary"])

    def test_system_text_strips_device_tree_terminators_and_bounds_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "system-info"
            self.assertIsNone(doctor._system_text(path))
            path.write_bytes(b" NVIDIA Jetson Nano\x00\r\n")
            self.assertEqual(doctor._system_text(path), "NVIDIA Jetson Nano")
            path.write_bytes(b"x" * 4096)
            self.assertEqual(len(doctor._system_text(path)), 4096)
            path.write_bytes(b"x" * 4097)
            with self.assertRaises(ValueError):
                doctor._system_text(path)


class ReportBoundaryTests(unittest.TestCase):
    def read_bytes(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_bytes(data)
            return doctor._read_probe(path)

    def read_object(self, value):
        return self.read_bytes(json.dumps(value).encode("utf-8"))

    def test_incomplete_report_preserves_completed_checks(self):
        value = {"complete": False, "active": "depthai", "checks": [check("stdlib")]}
        self.assertEqual(self.read_object(value), value)

    def test_report_shape_and_each_check_are_validated(self):
        valid = {"complete": True, "checks": [check("stdlib")]}
        invalid_reports = [None, [], {}, {**valid, "complete": 1},
                           {**valid, "complete": "true"}, {"complete": True},
                           {**valid, "checks": {}},
                           {**valid, "checks": [check(str(i)) for i in range(17)]},
                           {**valid, "checks": [check("stdlib"), check("stdlib")]},
                           {**valid, "checks": [check("native_probe")]},
                           {**valid, "checks": [None]}]
        for replacement in ({"name": 1}, {"status": "UNKNOWN"},
                            {"summary": None}, {"details": []}):
            invalid_reports.append({**valid, "checks": [{**check("stdlib"), **replacement}]})
        for value in invalid_reports:
            with self.subTest(report=value), self.assertRaises(ValueError):
                self.read_object(value)

    def test_malformed_json_and_invalid_utf8_are_rejected(self):
        for data in (b"", b'{"complete":', b"{} trailing", b"\xff"):
            with self.subTest(data=data), self.assertRaises((ValueError, UnicodeError)):
                self.read_bytes(data)

    def test_report_byte_limit_accepts_boundary_and_rejects_one_extra_byte(self):
        data = json.dumps({"complete": False, "checks": []}).encode("utf-8")
        boundary = data + b" " * (65536 - len(data))
        self.assertEqual(self.read_bytes(boundary)["checks"], [])
        with self.assertRaisesRegex(ValueError, "too large"):
            self.read_bytes(boundary + b" ")

    def test_installation_checks_never_claim_runtime_readiness(self):
        for status, expected_exit in (("PASS", 0), ("FAIL", 1)):
            with self.subTest(status=status), \
                    patch.object(doctor, "system_checks", return_value=[check("python")]), \
                    patch.object(doctor, "native_checks", return_value=[check("oak", status)]) as native:
                report = doctor.diagnose(1.5)
                native.assert_called_once_with(1.5)
                self.assertEqual(report["exit_code"], expected_exit)
                self.assertEqual(report["runtime_readiness"], "not_evaluated")
                self.assertEqual(report["scope"], "installation-and-device-enumeration")


if __name__ == "__main__":
    unittest.main()
