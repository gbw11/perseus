"""출력 예약·결과 파일 I/O의 유한 자식. acquisition과 동시에 실행하지 않는다."""

import json
import multiprocessing
import os
from pathlib import Path
import signal
import time

from .channel import ControlChannel, control_pair


def _receipt_worker(endpoint, output, report, reserve, filename="capture-result.json"):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    channel = ControlChannel(endpoint)
    try:
        if filename not in ("capture-result.json", "yolop-bench-result.json"):
            raise ValueError("unsupported bench receipt filename")
        output = Path(output)
        if reserve:
            output.mkdir(mode=0o700, parents=True, exist_ok=False)
        temporary = output / (filename + f".tmp.{os.getpid()}")
        with temporary.open("xb") as target:
            payload = json.dumps(report, ensure_ascii=True, allow_nan=False,
                                 sort_keys=True).encode("ascii") + b"\n"
            if target.write(payload) != len(payload):
                raise OSError("short receipt write")
        # 부모가 사라졌거나 취소를 관측한 상태에서 success snapshot을 publish하지 않는다.
        until = time.monotonic() + 5.0
        prepared = False
        decision = None
        while time.monotonic() < until:
            if not prepared:
                prepared = channel.send({"kind": "prepared"})
            messages = channel.receive()
            decision = next((m for m in messages if m.get("kind") in ("commit", "abort")), None)
            if decision is not None:
                break
            time.sleep(0.001)
        if decision is None:
            raise TimeoutError("parent did not authorize receipt publication")
        if decision["kind"] == "abort":
            override = decision.get("override", {})
            if (set(override) != {"complete", "exit_code", "reason"}
                    or override["complete"] is not False
                    or (override["exit_code"], override["reason"]) not in (
                        (130, "interrupted"), (143, "terminated"))):
                raise ValueError("invalid aborted receipt decision")
            report.update(override)
            payload = json.dumps(report, ensure_ascii=True, allow_nan=False,
                                 sort_keys=True).encode("ascii") + b"\n"
            with temporary.open("wb") as target:
                if target.write(payload) != len(payload):
                    raise OSError("short aborted receipt write")
        channel.receive()  # 이미 관측 가능한 EOF는 atomic rename 전에 거절한다.
        temporary.replace(output / filename)
        result = {"ok": True, "decision": decision["kind"]}
    except Exception as exc:
        result = {"ok": False, "error_type": type(exc).__name__,
                  "error": str(exc).encode("ascii", "backslashreplace").decode()[:512]}
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        try:
            if channel.send(result):
                break
        except (OSError, EOFError):
            break
        time.sleep(0.001)
    channel.close()


def write_receipt(output: Path, report: dict, *, reserve=False, timeout_s=5.0,
                  filename="capture-result.json", deadline=None, decide=None) -> dict:
    """별도 spawn I/O와 실회수를 원 deadline에 묶는다. 미회수는 저장 성공이 아니다.

    deadline은 단조시각 절대값이며 마지막 2초를 TERM/KILL 회수에 예약한다.
    생략하면 기존 I/O timeout+회수2초다. decide는 PREPARED에서 부모가 단1회 호출한다.
    COMMIT/ABORT 결정 이후의 신호는 호출자가 too_late로 기록한다.
    """
    parent, child = control_pair()
    channel = ControlChannel(parent)
    process = multiprocessing.get_context("spawn").Process(
        target=_receipt_worker, name="capture-receipt", args=(child, str(output), report, reserve, filename))
    result = None
    started = False
    process_pid = None
    forced = False
    began = time.monotonic()
    deadline = began + timeout_s + 2.0 if deadline is None else deadline
    io_deadline = min(began + timeout_s, deadline - 2.0)
    prepared = False
    published = False
    decision = None
    decision_j_us = None
    try:
        if began >= io_deadline:
            raise TimeoutError("no receipt I/O budget remains before reap reserve")
        process.start()
        started = True
        process_pid = process.pid
        child.close()
        while time.monotonic() < io_deadline:
            try:
                for message in channel.receive():
                    if message.get("kind") == "prepared":
                        prepared = True
                    else:
                        result = message
            except EOFError:
                pass
            if prepared and not published:
                if decision is None:
                    decision = {"kind": "commit"} if decide is None else decide()
                    if decision.get("kind") not in ("commit", "abort"):
                        raise ValueError("invalid receipt decision")
                    decision_j_us = time.monotonic_ns() // 1000
                published = channel.send(decision)
            if not process.is_alive():
                try:
                    for message in channel.receive():
                        if message.get("kind") != "prepared":
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
                    process.join(max(0.0, min(1.0, deadline - time.monotonic())))
        confirmed = not started or not process.is_alive()
        exit_code = process.exitcode if started else None
        if confirmed:
            process.close()
        channel.close()
    return {**(result or {"ok": False, "error": "receipt I/O timed out or returned no result"}),
            "ok": bool(result and result.get("ok") and confirmed and not forced
                       and exit_code == 0),
            "process_exit_confirmed": confirmed, "forced_termination": forced,
            "decision": decision["kind"] if decision else None,
            "decision_j_us": str(decision_j_us) if decision_j_us is not None else None,
            "pid": process_pid,
            "elapsed_s": round(time.monotonic() - began, 6)}
