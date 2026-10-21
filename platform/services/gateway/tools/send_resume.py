"""메시지 보드 목: drive.resume.requested 를 한 번 발행한다 (MQTT 계약 v1 6장). 게이트웨이는 AUTO_READY 일 때만 받는다.

    python tools/send_resume.py --port 18832
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vm-agent/mqtt-client"))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

MESSAGE_BOARD_VM = "33333333-3333-4333-8333-333333333333"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="message-board")
    parser.add_argument("--password")
    parser.add_argument("--vm-id", default=MESSAGE_BOARD_VM)
    parser.add_argument("--requested-by", default="operator")
    args = parser.parse_args()

    client = XenovaClient(Identity(vm_id=args.vm_id, app_id="message-board"), args.host, args.port,
                          username=args.username, password=args.password)
    if not client.start(wait_s=5):
        print("브로커 연결 실패", file=sys.stderr)
        return 1
    client.publish(topics.app_events("message-board"), "drive.resume.requested", {"requested_by": args.requested_by})
    client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
