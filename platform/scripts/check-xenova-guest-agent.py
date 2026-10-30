#!/usr/bin/env python3
"""오프라인 Linux rootfs 또는 실제 게스트의 Agent 설치를 읽기 전용으로 검사한다.

Broker 접속이나 서비스 시작·재시작은 수행하지 않는다. --live는 게스트 내부 전용이다.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'platform/guests/common'))
from prepare_rootfs import verify_guest_agent, inside


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--root', type=Path, help='새 Linux 템플릿/인스턴스의 오프라인 rootfs')
    target.add_argument('--live', action='store_true', help='현재 게스트 내부에서 실행')
    parser.add_argument('--seed', action='store_true', help='오프라인 factory seed의 신원 gate 검사')
    args = parser.parse_args(argv)
    root = Path('/') if args.live else args.root
    try:
        seed = args.seed or inside(root, 'etc/xenova/deployment-seed.json', create_parents=False).exists()
        result = verify_guest_agent(root, seed=seed, required=True)
        if args.live:
            vm_id = inside(root, 'etc/xenova/instance-id', create_parents=False).read_text().strip()
            if str(uuid.UUID(vm_id)) != vm_id:
                raise ValueError('noncanonical guest instance identity')
            result['instance_id'] = vm_id
            status = subprocess.run(['systemctl', 'is-active', 'xenova-agent.service'],
                                    capture_output=True, text=True, timeout=30)
            result['service_state'] = status.stdout.strip()
            if status.returncode:
                raise ValueError('Agent unit is not active')
        print(json.dumps({'ok': True, 'data': result}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__,
                          'message': str(exc), 'broker_connection': 'not-tested'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
