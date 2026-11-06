"""doctor 전용 native 진단 프로세스. 카메라를 열거나 런타임을 기동하지 않는다."""

import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys

# 설치하지 않고 설계 v1.3의 고정 후보와 비교한다.
PACKAGES = (
    ("numpy", "numpy", "1.26.4"),
    ("opencv-python-headless", "cv2", "4.10.0.84"),
    ("depthai", "depthai", "2.29.0.0"),
    ("paho-mqtt", "paho.mqtt.client", "2.1.0"),
    ("protobuf", "google.protobuf", "6.33.6"),
)


def _result(name: str, passed: bool, summary: str, **details: object) -> dict:
    return {"name": name, "status": "PASS" if passed else "FAIL",
            "summary": summary, "details": details}


def package_check(distribution: str, module_name: str, expected: str) -> dict:
    installed, root = None, None
    distribution_info = None
    try:
        distribution_info = metadata.distribution(distribution)
        installed = distribution_info.version
        root = str(distribution_info.locate_file(""))
    except metadata.PackageNotFoundError:
        pass
    details = {"expected_version": expected, "distribution_version": installed,
               "distribution_root": root, "module": module_name}
    try:
        module = importlib.import_module(module_name)
        details.update(module_path=getattr(module, "__file__", None),
                       module_version=getattr(module, "__version__", None))
    except Exception as exc:
        return _result(distribution, False, "Module import failed", **details,
                       error=f"{type(exc).__name__}: {exc}"[:2000])
    try:
        module_path = Path(details["module_path"]).resolve() if details["module_path"] else None
        owned = bool(module_path and distribution_info and any(
            Path(distribution_info.locate_file(entry)).resolve() == module_path
            for entry in (distribution_info.files or ())))
    except (OSError, TypeError, ValueError) as exc:
        return _result(distribution, False, "Cannot verify imported module origin", **details,
                       error=f"{type(exc).__name__}: {exc}"[:2000])
    details["module_owned_by_distribution"] = owned
    passed = installed == expected and owned
    # wheel 배포판의 네 번째 버전 항목과 cv2의 세 자리 버전은 다른 표기다.
    expected_module = ".".join(expected.split(".")[:3]) if module_name == "cv2" else expected
    if module_name in ("numpy", "cv2", "depthai", "google.protobuf"):
        passed = passed and details["module_version"] == expected_module
    return _result(distribution, passed,
                   f"installed={installed or 'no metadata'}; expected={expected}", **details)


def stdlib_check() -> dict:
    details, failures = {}, []
    for name in ("ssl", "sqlite3", "ctypes", "bz2", "lzma", "venv"):
        try:
            module = importlib.import_module(name)
            details[name] = {"path": getattr(module, "__file__", None)}
            if name == "ssl":
                details[name]["version"] = module.OPENSSL_VERSION
            elif name == "sqlite3":
                details[name]["version"] = module.sqlite_version
        except Exception as exc:
            failures.append(name)
            details[name] = {"error": f"{type(exc).__name__}: {exc}"[:2000]}
    return _result("stdlib", not failures,
                   "Standard library imports OK" if not failures else f"Import failures: {failures}",
                   modules=details)


def opencv_providers_check() -> dict:
    providers = {}
    for name in ("opencv-python", "opencv-python-headless", "opencv-contrib-python",
                 "opencv-contrib-python-headless"):
        try:
            providers[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return _result("opencv_providers", list(providers) == ["opencv-python-headless"],
                   "Expected exactly one cv2 provider: opencv-python-headless", providers=providers)


def oak_check(depthai_valid: bool) -> dict:
    if not depthai_valid:
        return _result("oak", False, "Not checked: validated DepthAI 2.29 is required")
    try:
        dai = importlib.import_module("depthai")
        infos = dai.Device.getAllAvailableDevices()
        devices = [{"mxid": info.getMxId(), "name": str(info.name)[:256],
                    "state": str(info.state)[:256]} for info in infos[:16]]
    except Exception as exc:
        return _result("oak", False, "SDK device enumeration failed",
                       error=f"{type(exc).__name__}: {exc}"[:2000])
    return _result("oak", 0 < len(infos) <= 16,
                   f"SDK available-device count: {len(infos)}", devices=devices,
                   limit=16, note="Empty does not distinguish absent, busy, or inaccessible hardware; "
                   "discovery does not verify OAK-D-Lite model, sensors, or USB speed.")


def run_probe(path: Path) -> None:
    """진행 중 항목과 완료 결과를 원자 교체해 crash/timeout 원인을 남긴다."""
    report = {"complete": False, "active": None, "checks": []}

    def save() -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=True, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)

    def perform(name: str, function, *args) -> dict:
        report["active"] = name
        save()
        result = function(*args)
        report["checks"].append(result)
        save()
        return result

    perform("stdlib", stdlib_check)
    depthai_valid = False
    for distribution, module, expected in PACKAGES:
        result = perform(distribution, package_check, distribution, module, expected)
        if distribution == "depthai":
            depthai_valid = result["status"] == "PASS"
    perform("opencv_providers", opencv_providers_check)
    perform("oak", oak_check, depthai_valid)
    report.update(complete=True, active=None)
    save()


if __name__ == "__main__":
    # 스크립트 실행의 sys.path[0] 차이로 다른 설치물을 검사하지 않도록 부모와 맞춘다.
    sys.path[:] = json.loads(sys.argv[2])
    run_probe(Path(sys.argv[1]))
