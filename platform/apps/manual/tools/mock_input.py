"""백엔드 입력 목: 키를 누르고 있는 동안처럼 drive.input 을 20 Hz 로 보낸다 (MQTT 계약 v1 7장).

    python tools/mock_input.py --keys up,left --seconds 1
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services/vm-agent/mqtt-client"))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

INTERVAL_S = 0.05


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="backend")
    parser.add_argument("--password")
    parser.add_argument("--keys", default="", help="쉼표 구분: up,down,left,right,brake")
    parser.add_argument("--seconds", type=float, default=1.0)
    args = parser.parse_args()
    keys = [key for key in args.keys.split(",") if key]

    client = XenovaClient(Identity(source_id="dom0-server"), args.host, args.port,
                          username=args.username, password=args.password)
    if not client.start(wait_s=5):
        print("브로커 연결 실패", file=sys.stderr)
        return 1
    end = time.monotonic() + args.seconds
    sent = 0
    while time.monotonic() < end:
        client.publish(topics.manual_input(), "drive.input", {"keys": keys})
        sent += 1
        time.sleep(INTERVAL_S)
    client.stop()
    print(f"drive.input {keys} {sent}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
