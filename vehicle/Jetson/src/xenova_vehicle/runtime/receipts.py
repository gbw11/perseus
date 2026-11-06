"""출력 예약·결과 파일 I/O의 유한 자식. acquisition과 동시에 실행하지 않는다."""

import json
import multiprocessing
from pathlib import Path
import signal
import time

from .channel import ControlChannel, control_pair


def _receipt_worker(endpoint, output, report, reserve):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    channel = ControlChannel(endpoint)
    try:
        output = Path(output)
        if reserve:
            output.mkdir(mode=0o700, parents=True, exist_ok=False)
        temporary = output / "capture-result.json.tmp"
        with temporary.open("xb") as target:
            target.write(json.dumps(report, ensure_ascii=True, allow_nan=False,
                                    sort_keys=True).encode("ascii") + b"\n")
        temporary.replace(output / "capture-result.json")
        result = {"ok": True}
    except Exception as exc:
        result = {"ok": False, "error_type": type(exc).__name__,
                  "error": str(exc).encode("ascii", "backslashreplace").decode()[:512]}
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        if channel.send(result):
            break
        time.sleep(0.001)
    channel.close()


def write_receipt(output: Path, report: dict, *, reserve=False, timeout_s=5.0) -> dict:
    """별도 spawn에서만 파일을 쓴다. timeout/미회수는 결과 저장 성공이 아니다."""
    parent, child = control_pair()
    channel = ControlChannel(parent)
    process = multiprocessing.get_context("spawn").Process(
        target=_receipt_worker, name="capture-receipt", args=(child, str(output), report, reserve))
    result = None
    started = False
    forced = False
    try:
        process.start()
        started = True
        child.close()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                for message in channel.receive():
                    result = message
            except EOFError:
                pass
            if not process.is_alive():
                try:
                    for message in channel.receive():
                        result = message
                except EOFError:
                    pass
                break
            time.sleep(0.01)
    except Exception as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:512]}
    finally:
        child.close()
        if started:
            for action in (process.terminate, process.kill):
                if process.is_alive():
                    forced = True
                    action()
                    process.join(1.0)
        confirmed = not started or not process.is_alive()
        exit_code = process.exitcode if started else None
        if confirmed:
            process.close()
        channel.close()
    return {**(result or {"ok": False, "error": "receipt I/O timed out or returned no result"}),
            "ok": bool(result and result.get("ok") and confirmed and not forced and exit_code == 0),
            "process_exit_confirmed": confirmed, "forced_termination": forced}
