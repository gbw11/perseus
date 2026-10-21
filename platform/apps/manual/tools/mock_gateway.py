"""게이트웨이 목: vehicle.control 을 retained 로 한 번 발행한다 (MQTT 계약 v1 5.4). 실제 게이트웨이가 생기기 전 시험용.

    python tools/mock_gateway.py --generation 7 --mode MANUAL
    python tools/mock_gateway.py --clear          # retained 삭제
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services/vm-agent/mqtt-client"))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

ACTIVE_APP = {"AUTO": "autonomous", "MANUAL": "manual", "AUTO_READY": "manual", "SAFE_STOP": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="gateway")
    parser.add_argument("--password")
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--mode", choices=sorted(ACTIVE_APP), default="MANUAL")
    parser.add_argument("--active-vm-id")
    parser.add_argument("--reason", default="start")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    client = XenovaClient(Identity(source_id="dom0-gateway"), args.host, args.port,
                          username=args.username, password=args.password)
    if not client.start(wait_s=5):
        print("브로커 연결 실패", file=sys.stderr)
        return 1
    if args.clear:
        client.clear_retained(topics.vehicle_control())
    else:
        client.publish(topics.vehicle_control(), "vehicle.control", {
            "generation": args.generation,
            "mode": args.mode,
            "active_app": ACTIVE_APP[args.mode],
            "active_vm_id": args.active_vm_id,
            "reason": args.reason,
        })
    client.stop()  # disconnect 전에 QoS 1 이 나가도록 paho 가 처리한다
    return 0


if __name__ == "__main__":
    sys.exit(main())
