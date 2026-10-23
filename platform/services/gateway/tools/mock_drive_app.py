"""주행 앱 목: vehicle/control 의 세대로 drive.command 를 50 Hz, app.heartbeat 를 1초마다 낸다 (MQTT 계약 v1 5·6.1장).

자율주행 앱(Zephyr)이 아직 없을 때 게이트웨이 시험용. 강제 종료하면 VM 이 죽은 것처럼 명령이 끊긴다.

    python tools/mock_drive_app.py --app autonomous --port 18832 --v 0.5
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vm-agent/mqtt-client"))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

VM_IDS = {"autonomous": "11111111-1111-4111-8111-111111111111", "manual": "22222222-2222-4222-8222-222222222222"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", choices=sorted(VM_IDS), default="autonomous")
    parser.add_argument("--vm-id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--v", type=float, default=0.5, help="활성일 때 v_mps")
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--seconds", type=float, default=0.0, help="0 이면 멈출 때까지")
    args = parser.parse_args()

    client = XenovaClient(Identity(vm_id=args.vm_id or VM_IDS[args.app], app_id=args.app), args.host, args.port,
                          username=args.username or args.app, password=args.password)
    client.subscribe(topics.vehicle_control(), qos=1)
    if not client.start(wait_s=5):
        print("브로커 연결 실패", file=sys.stderr)
        return 1
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    topic = topics.app_events(args.app)
    period = 1.0 / args.rate
    started = time.perf_counter()
    deadline = started
    next_heartbeat = started
    while not stop.is_set() and (args.seconds <= 0 or time.perf_counter() - started < args.seconds):
        control = client.latest(topics.vehicle_control())
        generation = control.payload.get("generation", 0) if control else 0
        active = bool(control) and control.payload.get("active_app") == args.app
        client.publish(topic, "drive.command", {"generation": generation, "v_mps": args.v if active else 0.0, "steer_rad": 0.0})
        now = time.perf_counter()
        if now >= next_heartbeat:
            state = "idle" if control is None else ("active" if active else "standby")
            client.publish(topic, "app.heartbeat", {"state": state, "uptime_s": round(now - started, 3)})
            next_heartbeat += 1.0
        deadline += period
        delay = deadline - time.perf_counter()
        if delay < -period:
            deadline = time.perf_counter()
        elif delay > 0:
            time.sleep(delay)   # Event.wait·time.monotonic 은 Windows 에서 15.6 ms 단위라 쓰지 않는다
    client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
