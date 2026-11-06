"""설치·연결 확인용 진단. 런타임 기동이나 이동 허가를 판정하지 않는다."""

import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

_MAX_REPORT_BYTES = 65536
_PROBE_FILE = Path(__file__).with_name("_doctor_probe.py")
_NATIVE_NAMES = {"stdlib", "numpy", "opencv-python-headless", "depthai",
                 "paho-mqtt", "protobuf", "opencv_providers", "oak"}


def _check(name: str, passed: bool, summary: str, **details: object) -> dict:
    return {"name": name, "status": "PASS" if passed else "FAIL",
            "summary": summary, "details": details}


def _system_text(path: Path) -> str | None:
    try:
        with path.open("rb") as source:
            data = source.read(4097)
    except FileNotFoundError:
        return None
    if len(data) > 4096:
        raise ValueError(f"system information exceeds 4096 bytes: {path}")
    return data.decode("utf-8", errors="replace").strip("\x00\r\n ")


def system_checks() -> list[dict]:
    """현재 interpreter와 읽을 수 있는 Nano/L4T 정보를 검사한다."""
    version = platform.python_version()
    checks = [_check("python", version == "3.11.18", f"Python {version}; expected 3.11.18",
                     executable=sys.executable, prefix=sys.prefix, base_prefix=sys.base_prefix)]
    system, machine = platform.system(), platform.machine()
    try:
        model = (_system_text(Path("/proc/device-tree/model"))
                 or _system_text(Path("/sys/firmware/devicetree/base/model")))
        checks.append(_check("jetson", system == "Linux" and machine == "aarch64"
                             and bool(model and "NVIDIA Jetson Nano" in model),
                             f"{system}/{machine}; model={model or 'unavailable'}",
                             model=model, system=system, machine=machine,
                             carrier_revision="not_verified"))
    except (OSError, ValueError) as exc:
        checks.append(_check("jetson", False, f"Cannot read board information: {exc}"))
    try:
        release = _system_text(Path("/etc/nv_tegra_release"))
        match = re.search(r"\bR(\d+)\s+\(release\),\s*REVISION:\s*([\d.]+)", release or "")
        l4t = f"{match[1]}.{match[2]}" if match else None
        checks.append(_check("l4t", l4t == "32.7.6", f"L4T {l4t or 'unavailable'}; expected 32.7.6",
                             release=release, expected_jetpack="4.6.6",
                             jetpack_inventory="not_verified", libc=platform.libc_ver()))
    except (OSError, ValueError) as exc:
        checks.append(_check("l4t", False, f"Cannot read L4T information: {exc}"))
    return checks


def _read_probe(path: Path) -> dict:
    with path.open("rb") as source:
        data = source.read(_MAX_REPORT_BYTES + 1)
    if len(data) > _MAX_REPORT_BYTES:
        raise ValueError("native probe report is too large")
    result = json.loads(data)
    if not isinstance(result, dict) or type(result.get("complete")) is not bool:
        raise ValueError("invalid native probe report")
    checks = result.get("checks")
    if not isinstance(checks, list) or len(checks) > 16:
        raise ValueError("invalid native check list")
    names = set()
    for item in checks:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or item["name"] not in _NATIVE_NAMES or item["name"] in names
                or item.get("status") not in ("PASS", "FAIL")
                or not isinstance(item.get("summary"), str)
                or not isinstance(item.get("details"), dict)):
            raise ValueError("invalid native check")
        names.add(item["name"])
    return result


def native_checks(timeout_s: float) -> list[dict]:
    """진단 임시 경로/프로세스를 만들 수 없어도 구조화된 실패를 반환한다."""
    try:
        return _native_checks(timeout_s)
    except OSError as exc:
        return [_check("native_probe", False, f"Diagnostic process/file access failed: {exc}")]


def _native_checks(timeout_s: float) -> list[dict]:
    """같은 interpreter의 별도 프로세스에서 native import·SDK 검색을 제한한다.

    환경을 바꾸는 -I/-S를 쓰지 않아 실제 import 경로를 조사한다. stdout/stderr는
    폐기하고 크기가 제한된 결과 파일만 읽어 SDK 출력과 JSON이 섞이지 않게 한다.
    """
    with tempfile.TemporaryDirectory(prefix="xenova-doctor-") as directory:
        report_path = Path(directory) / "probe.json"
        command = [sys.executable, "-B", str(_PROBE_FILE), str(report_path), json.dumps(sys.path)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=flags)
        except OSError as exc:
            return [_check("native_probe", False, f"Cannot start diagnostic process: {exc}")]
        failure = None
        try:
            code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return [_check("native_probe", False, "Probe timed out; process exit is not confirmed",
                               pid=process.pid, timeout_s=timeout_s)]
            failure = _check("native_probe", False, "Native import/device enumeration timed out",
                             timeout_s=timeout_s, process_exit_confirmed=True)
        else:
            if code != 0:
                failure = _check("native_probe", False, f"Diagnostic process exited with code {code}")
        try:
            result = _read_probe(report_path)
        except (OSError, ValueError, UnicodeError) as exc:
            return [failure or _check("native_probe", False, f"No valid native report: {exc}")]
        checks = result["checks"]
        if failure:
            failure["details"]["active_check"] = result.get("active")
            checks.append(failure)
        elif not result["complete"] or {item["name"] for item in checks} != _NATIVE_NAMES:
            checks.append(_check("native_probe", False, "Native checks did not finish"))
        return checks


def diagnose(timeout_s: float = 10.0) -> dict:
    """진단 결과를 반환한다. 0은 명시된 검사 통과, 1은 실패/미확인이다."""
    checks = system_checks() + native_checks(timeout_s)
    return {"schema_version": 1, "scope": "installation-and-device-enumeration",
            "runtime_readiness": "not_evaluated", "checks": checks,
            "exit_code": int(any(item["status"] != "PASS" for item in checks))}


def render_text(report: dict) -> str:
    lines = ["Xenova vehicle doctor (manual installation/connectivity diagnostic)"]
    for item in report["checks"]:
        lines.append(f"[{item['status']}] {item['name']}: {item['summary']}")
        for key, value in item["details"].items():
            lines.append(f"  {key}: {value}")
    lines.append("Scope: no Device open, capture, calibration, USB-speed, model, or driving verification.")
    lines.append("Runtime readiness: not evaluated. Exit code: " + str(report["exit_code"]))
    return "\n".join(lines)
