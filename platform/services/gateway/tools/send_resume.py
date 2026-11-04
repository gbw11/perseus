"""재개 요청 제출 도구. --verified는 현재 control 문맥을 결합하고, 생략은 legacy다.

기본은 인터랙션 VM 경로(MQTT-06), --console 이면 운영자 콘솔 경로(MQTT-13, 백엔드 계정 backend·source_id dom0-server).
받아들여지면 게이트웨이가 drive.resume.accepted 를 내고 3초 뒤 AUTO 로 바꾼다.

    python tools/send_resume.py --port 18832
    python tools/send_resume.py --port 18832 --console --requested-by admin
    python tools/send_resume.py --port 18832 --console --verified --cancel   # 카운트다운 취소 (계약 v1.7)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vm-agent/mqtt-client"))

from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

INTERACTION_VM = "33333333-3333-4333-8333-333333333333"
CONSOLE_RESUME = f"{topics.PREFIX}/console/resume"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--console", action="store_true", help="xenova/v1/console/resume 로 보낸다 (백엔드 대신)")
    parser.add_argument("--username", help="기본: 인터랙션 VM 경로 interaction, --console 이면 backend")
    parser.add_argument("--password",default=os.environ.get('XENOVA_RESUME_BROKER_PASSWORD'),
                        help="권장: root 전용 환경의 XENOVA_RESUME_BROKER_PASSWORD")
    parser.add_argument("--vm-id", default=INTERACTION_VM)
    parser.add_argument("--requested-by", default="operator")
    parser.add_argument("--verified",action="store_true",help="현재 control의 세대·게시 ID에 결합한 공통 A/B 재개 요청")
    parser.add_argument("--cancel",action="store_true",help="재개 대신 drive.resume.cancel_requested (카운트다운 취소)")
    args = parser.parse_args()

    if args.console:
        identity, topic, username = Identity(source_id="dom0-server"), CONSOLE_RESUME, "backend"
    else:
        identity = Identity(vm_id=args.vm_id, app_id="interaction")
        topic, username = topics.app_events("interaction"), "interaction"
    client = XenovaClient(identity, args.host, args.port, username=args.username or username, password=args.password)
    if args.verified:client.subscribe(topics.vehicle_control(),qos=1)
    if not client.start(wait_s=5):
        print("브로커 연결 실패", file=sys.stderr)
        client.stop()
        return 1
    try:
        payload={"requested_by":args.requested_by};cause=None
        if args.verified:
            end=time.monotonic()+5;control=None
            while time.monotonic()<end:
                control=client.latest(topics.vehicle_control())
                if control is not None:break
                time.sleep(.025)
            if control is None:raise ValueError('current control unavailable; request not submitted')
            generation=control.payload.get('generation')
            if type(generation) is not int or not 1<=generation<=0xffffffff:
                raise ValueError('control generation invalid')
            payload['expected_generation']=generation;cause=control.envelope['message_id']
        kind="drive.resume.cancel_requested" if args.cancel else "drive.resume.requested"
        client.publish(topic,kind,payload,causation_id=cause,wait_s=1)
        print('request submitted; verify drive.resume.accepted and the resulting control separately')
    finally:client.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
