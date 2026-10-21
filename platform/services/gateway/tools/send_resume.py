"""재개 요청 목: drive.resume.requested 를 한 번 발행한다 (MQTT 계약 v1.2 6장). 게이트웨이는 AUTO_READY 일 때만 받는다.

기본은 메시지 보드 VM 경로(MQTT-06), --console 이면 운영자 콘솔 경로(MQTT-13, 백엔드 계정 backend·source_id dom0-server).
받아들여지면 게이트웨이가 drive.resume.accepted 를 내고 3초 뒤 AUTO 로 바꾼다.

    python tools/send_resume.py --port 18832
    python tools/send_resume.py --port 18832 --console --requested-by admin
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vm-agent/mqtt-client"))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

MESSAGE_BOARD_VM = "33333333-3333-4333-8333-333333333333"
CONSOLE_RESUME = f"{topics.PREFIX}/console/resume"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--console", action="store_true", help="xenova/v1/console/resume 로 보낸다 (백엔드 대신)")
    parser.add_argument("--username", help="기본: 메시지 보드 경로 message-board, --console 이면 backend")
    parser.add_argument("--password")
    parser.add_argument("--vm-id", default=MESSAGE_BOARD_VM)
    parser.add_argument("--requested-by", default="operator")
    args = parser.parse_args()

    if args.console:
        identity, topic, username = Identity(source_id="dom0-server"), CONSOLE_RESUME, "backend"
    else:
        identity = Identity(vm_id=args.vm_id, app_id="message-board")
        topic, username = topics.app_events("message-board"), "message-board"
    client = XenovaClient(identity, args.host, args.port, username=args.username or username, password=args.password)
    if not client.start(wait_s=5):
        print("브로커 연결 실패", file=sys.stderr)
        return 1
    client.publish(topic, "drive.resume.requested", {"requested_by": args.requested_by})
    client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
