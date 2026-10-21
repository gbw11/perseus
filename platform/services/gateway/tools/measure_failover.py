"""게이트웨이 전환 측정 (로컬 브로커). 게이트웨이·수동주행 앱·자율주행 목을 띄우고 장애를 넣어 시간을 잰다.

한 회차: AUTO → 자율 목 강제 종료 → MANUAL → 자율 목 재시작 → AUTO_READY → 재개 → AUTO.
마지막: MANUAL 중 재개 요청(무시 확인), 두 앱 모두 종료 → SAFE_STOP → 수동 앱 재시작 → MANUAL.

    python tools/measure_failover.py --port 18832 --runs 10

두 가지 시각을 같이 본다.
- 게이트웨이 내부(MEASURE 로그): 마지막 자율 명령 수신 → 판정(gap_ms), → 새 세대 첫 전달(since_last_active_ms)
- 구독자(이 스크립트): 마지막 자율 명령 수신 → vehicle/control MANUAL 수신. 브로커 왕복이 양쪽에 다 들어간다
시각은 time.perf_counter (Windows 의 time.monotonic 은 15.6 ms 단위).
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1]
MQTT_CLIENT_DIR = GATEWAY_DIR.parent / "vm-agent/mqtt-client"
MANUAL_DIR = GATEWAY_DIR.parents[1] / "apps/manual"
sys.path.insert(0, str(MQTT_CLIENT_DIR))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

MANUAL_VM = "22222222-2222-4222-8222-222222222222"


class Observer:
    def __init__(self, host: str, port: int) -> None:
        self.lock = threading.Lock()
        self.last_auto_cmd: float | None = None
        self.first_auto_cmd_since: dict[float, float] = {}
        self.controls: list[tuple[float, dict]] = []
        self.mode_events: list[tuple[float, dict]] = []
        self.client = XenovaClient(Identity(source_id="measure"), host, port, username="measure")
        self.client.subscribe(topics.app_events("autonomous"), self._auto, qos=0)
        self.client.subscribe(topics.vehicle_control(), self._control, qos=1)
        self.client.subscribe(topics.app_events("vehicle"), self._vehicle, qos=1)

    def _auto(self, message) -> None:
        if message.type == "drive.command":
            with self.lock:
                self.last_auto_cmd = time.perf_counter()

    def _control(self, message) -> None:
        with self.lock:
            self.controls.append((time.perf_counter(), message.payload))

    def _vehicle(self, message) -> None:
        if message.type == "drive.mode.changed":
            with self.lock:
                self.mode_events.append((time.perf_counter(), message.payload))

    def mode(self) -> dict | None:
        with self.lock:
            return self.controls[-1][1] if self.controls else None

    def wait_mode(self, mode: str, timeout: float, after: float = 0.0) -> tuple[float, dict] | None:
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            with self.lock:
                for t, payload in self.controls:
                    if t >= after and payload.get("mode") == mode:
                        return t, payload
            time.sleep(0.005)
        return None

    def first_auto_after(self, after: float, timeout: float) -> float | None:
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            with self.lock:
                if self.last_auto_cmd is not None and self.last_auto_cmd > after:
                    return self.last_auto_cmd
            time.sleep(0.002)
        return None


def env() -> dict[str, str]:
    e = dict(os.environ)
    e["PYTHONPATH"] = os.pathsep.join([str(GATEWAY_DIR), str(MQTT_CLIENT_DIR), str(MANUAL_DIR)])
    e["PYTHONUTF8"] = "1"
    return e


def spawn(args: list[str], log: Path, cwd: Path, extra: dict[str, str] | None = None) -> subprocess.Popen:
    e = env()
    e.update(extra or {})
    handle = open(log, "a", encoding="utf-8")
    return subprocess.Popen([sys.executable, *args], cwd=cwd, env=e, stdout=handle, stderr=subprocess.STDOUT)


def pct(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"n={len(values)} min={min(values):.1f} p50={statistics.median(values):.1f} max={max(values):.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18832)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--manual", choices=("reference", "mock"), default="reference",
                        help="reference = platform/apps/manual Python 참조 앱")
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args()

    work = Path(args.workdir or tempfile.mkdtemp(prefix="gw98-"))
    work.mkdir(parents=True, exist_ok=True)
    gw_log, frame_log = work / "gateway.log", work / "frames.log"
    print(f"작업 폴더 {work}")
    broker = ["--host", args.host, "--port", str(args.port)]

    def start_auto() -> subprocess.Popen:
        return spawn([str(GATEWAY_DIR / "tools/mock_drive_app.py"), "--app", "autonomous", *broker], work / "autonomous.log", GATEWAY_DIR)

    def start_manual() -> subprocess.Popen:
        if args.manual == "mock":
            return spawn([str(GATEWAY_DIR / "tools/mock_drive_app.py"), "--app", "manual", *broker], work / "manual.log", GATEWAY_DIR)
        return spawn(["-m", "xenova_manual"], work / "manual.log", MANUAL_DIR, {
            "XENOVA_MANUAL_VM_ID": MANUAL_VM, "XENOVA_MANUAL_BROKER_HOST": args.host,
            "XENOVA_MANUAL_BROKER_PORT": str(args.port)})

    def resume() -> None:
        subprocess.run([sys.executable, str(GATEWAY_DIR / "tools/send_resume.py"), *broker], env=env(), check=True)

    obs = Observer(args.host, args.port)
    obs.client.start(wait_s=5)
    procs: dict[str, subprocess.Popen] = {"manual": start_manual(), "autonomous": start_auto()}
    time.sleep(1.5)
    procs["gateway"] = spawn(["-m", "xenova_gateway", *broker, "--frame-log", str(frame_log)], gw_log, GATEWAY_DIR)
    failures: list[str] = []
    observer_gaps: list[float] = []
    ready_s: list[float] = []
    try:
        started = obs.wait_mode("AUTO", 5)
        if not started:
            failures.append(f"시작 AUTO 안 됨: {obs.mode()}")
            return 1
        time.sleep(1.0)
        for run in range(1, args.runs + 1):
            if (obs.mode() or {}).get("mode") != "AUTO":
                failures.append(f"run {run}: AUTO 아님 {obs.mode()}")
                break
            time.sleep(0.5 + 0.037 * run)   # 50 Hz 위상과 엇갈리게
            procs["autonomous"].kill()
            procs["autonomous"].wait()
            t_kill = time.perf_counter()
            got = obs.wait_mode("MANUAL", 2.0, after=t_kill - 0.2)
            if not got:
                failures.append(f"run {run}: MANUAL 안 됨")
                break
            with obs.lock:
                last_auto = obs.last_auto_cmd
            gap = (got[0] - last_auto) * 1000
            observer_gaps.append(gap)
            if run == 1:   # E7: MANUAL 에서 재개 요청은 무시
                before = len(obs.mode_events)
                resume()
                time.sleep(0.5)
                if len(obs.mode_events) != before or obs.mode().get("mode") != "MANUAL":
                    failures.append("MANUAL 에서 재개 요청이 무시되지 않음")
                else:
                    print("E7 MANUAL 중 재개 요청 무시 확인 (mode.changed 증가 없음)")
            time.sleep(0.3)
            t_restart = time.perf_counter()
            procs["autonomous"] = start_auto()
            first = obs.first_auto_after(t_restart, 10)
            ready = obs.wait_mode("AUTO_READY", 8.0, after=t_restart)
            if not first or not ready:
                failures.append(f"run {run}: AUTO_READY 안 됨")
                break
            ready_s.append(ready[0] - first)
            ready_gen = ready[1]["generation"]
            resume()
            auto = obs.wait_mode("AUTO", 2.0, after=ready[0])
            if not auto or auto[1]["generation"] != ready_gen + 1:
                failures.append(f"run {run}: 재개 후 AUTO gen 불일치 {auto}")
                break
            print(f"run {run}: MANUAL 구독자 간격 {gap:.1f} ms, AUTO_READY {ready_s[-1]:.3f} s, AUTO gen {ready_gen}→{ready_gen + 1}")
            time.sleep(0.5)

        # E6: 두 앱 모두 종료 → SAFE_STOP
        procs["manual"].kill()
        procs["autonomous"].kill()
        t_kill = time.perf_counter()
        safe = obs.wait_mode("SAFE_STOP", 2.0, after=t_kill - 0.2)
        if not safe:
            failures.append("SAFE_STOP 안 됨")
        else:
            time.sleep(0.5)
            print(f"SAFE_STOP gen {safe[1]['generation']} reason {safe[1]['reason']}")
        procs["manual"] = start_manual()
        manual = obs.wait_mode("MANUAL", 5.0, after=time.perf_counter())
        if not manual:
            failures.append("SAFE_STOP 뒤 수동 재시작해도 MANUAL 안 됨")
        else:
            print(f"수동 재시작 → MANUAL gen {manual[1]['generation']} reason {manual[1]['reason']}")
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
        obs.client.stop()

    # 게이트웨이 MEASURE 로그 분석
    text = gw_log.read_text(encoding="utf-8", errors="replace")
    measures = [dict(re.findall(r"(\w+)=(\S+)", line.split("MEASURE ", 1)[1])) for line in text.splitlines() if "MEASURE " in line]
    timeouts = [m for m in measures if m.get("event") == "transition" and m.get("reason") == "command_timeout"]
    safes = [m for m in measures if m.get("event") == "transition" and m.get("to") == "SAFE_STOP" and m.get("gap_ms") != "None"]
    first_by_gen = {m["gen"]: m for m in measures if m.get("event") == "first_output"}
    detect = [float(m["gap_ms"]) for m in timeouts]
    forward = [float(first_by_gen[m["gen"]]["since_last_active_ms"]) for m in timeouts if m["gen"] in first_by_gen]
    forward_after_detect = [float(first_by_gen[m["gen"]]["since_detect_ms"]) for m in timeouts if m["gen"] in first_by_gen]
    stop_lines = [line for line in frame_log.read_text(encoding="utf-8").splitlines() if " STOP " in line]

    print()
    print("AUTO→MANUAL 게이트웨이 판정 간격 ms (마지막 자율 명령 → 판정):", pct(detect))
    print("AUTO→MANUAL 첫 수동 프레임 전달 ms (마지막 자율 명령 → 전달):", pct(forward))
    print("AUTO→MANUAL 판정 → 첫 수동 프레임 ms:", pct(forward_after_detect))
    print("AUTO→MANUAL 구독자 기준 ms (마지막 자율 명령 → control MANUAL 수신):", pct(observer_gaps))
    print("자율 첫 명령 → AUTO_READY ms:", pct([s * 1000 for s in ready_s]))
    print("SAFE_STOP 판정 간격 ms:", pct([float(m["gap_ms"]) for m in safes]))
    print(f"STOP 프레임 {len(stop_lines)}줄 (frames.log)")
    if failures:
        print("실패:", *failures, sep="\n  ")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
