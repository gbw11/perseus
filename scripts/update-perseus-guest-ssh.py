#!/usr/bin/env python3
"""기존 Perseus Linux 게스트의 SSH 유닛만 명시적으로 갱신한다. 키/암호 초기화는 하지 않는다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / 'platform/guests/common'), str(HERE)]
from prepare_rootfs import check_ssh_service, configure_ssh_service, install_identity_gate, inside


def identities(root):
    paths = ['etc/shadow', 'etc/machine-id', 'etc/perseus/instance-id',
             'etc/perseus/identity-complete.json', 'etc/perseus/identity-planned.json']
    paths += [str(p.relative_to(root)) for p in (root / 'etc/ssh').glob('ssh_host_*')]
    return {name: hashlib.sha256(inside(root, name, create_parents=False).read_bytes()).hexdigest()
            for name in paths if inside(root, name, create_parents=False).is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    root = args.root
    if not root.is_absolute() or root.resolve() != root or root.is_symlink():
        raise ValueError('canonical guest root required')
    if not inside(root, 'etc/perseus/template-prepared', create_parents=False).is_file():
        raise ValueError('explicit Perseus guest root required; refusing host configuration')
    # 설정 충돌은 백업 디렉터리와 identity gate를 쓰기 전, plan에서도 동일하게 거부한다.
    check_ssh_service(root)
    if not args.execute:
        print(json.dumps({'action': 'plan-guest-ssh-service-policy', 'root': str(root),
                          'identity_reinitialization': False, 'starts_or_stops_services': False}))
        return
    before = identities(root)
    backup = inside(root, 'var/lib/perseus/ssh-policy-backup-' + os.urandom(16).hex() + '/marker', create_parents=False).parent
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('etc/systemd/system', 'etc/systemd/system-generators', 'usr/lib/perseus',
                 'usr/lib/systemd/system/perseus-guest-identity.service'):
        source = inside(root, name, follow_final=False, create_parents=False)
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.exists() or source.is_symlink():
            shutil.copy2(source, target, follow_symlinks=False)
    os.sync()
    if inside(root, 'etc/perseus/deployment-seed.json', create_parents=False).is_file():
        install_identity_gate(root)
    configure_ssh_service(root)
    if identities(root) != before:
        raise ValueError('guest identity files changed unexpectedly; stop and inspect backup')
    os.sync()
    print(json.dumps({'action': 'guest-ssh-service-policy-updated', 'backup': str(backup),
                      'identity_files_preserved': True, 'starts_or_stops_services': False}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print('[perseus-guest-ssh] ERROR:', exc, file=sys.stderr)
        sys.exit(1)
