#!/usr/bin/python3
"""공통 배포 seed의 게스트 식별자·SSH 키를 최초 SSH 수신 전에 준비한다. 암호는 변경하지 않는다."""
import json
import os
from pathlib import Path
import re
import subprocess


def save(path, value):
    temporary = path.with_name(path.name + '.pending')
    if temporary.is_symlink() or path.is_symlink():
        raise ValueError('linked guest identity state is forbidden')
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)
    os.sync()


def validate_keys(keys, command):
    for key in keys:
        command(['ssh-keygen', '-y', '-P', '', '-f', str(key)], check=True, timeout=30,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def ensure(root=Path('/'), command=subprocess.run):
    root = Path(root)
    directory = root / 'etc/xenova'
    if not (directory / 'deployment-seed.json').is_file():
        raise ValueError('this is not a factory deployment seed')
    marker = directory / 'identity-complete.json'
    planned = directory / 'identity-planned.json'
    identity = directory / 'instance-id'
    mid_path = root / 'etc/machine-id'
    mid = mid_path.read_text().strip()
    if not re.fullmatch('[0-9a-f]{32}', mid) or mid == '0' * 32:
        raise ValueError('guest init must establish machine-id before SSH; existing identity is never replaced')
    keys = [root / f'etc/ssh/ssh_host_{kind}_key' for kind in ('rsa', 'ecdsa', 'ed25519')]
    if marker.exists():
        state = json.loads(marker.read_text())
        if state.get('machine_id') != mid or identity.read_text().strip() != state['instance_id']:
            raise ValueError('existing guest identity changed; refusing reinitialization')
        if not all(p.is_file() and not p.is_symlink() and p.stat().st_size for p in keys):
            raise ValueError('completed guest SSH keys missing; no automatic key replacement')
        validate_keys(keys, command)
        return
    assigned=directory/'platform-identity.json'
    if assigned.exists() or assigned.is_symlink():
        import stat
        info=assigned.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:
            raise ValueError('invalid platform identity input')
        supplied=json.loads(assigned.read_text())
        if set(supplied)!={'schema','vm_id'} or supplied['schema']!=1 or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',supplied['vm_id']):
            raise ValueError('invalid platform VM identity')
        instance_id=supplied['vm_id']
    else:
        instance_id = f'{mid[:8]}-{mid[8:12]}-{mid[12:16]}-{mid[16:20]}-{mid[20:]}'
    state = {'schema': 1, 'machine_id': mid, 'instance_id': instance_id}
    if planned.exists():
        if json.loads(planned.read_text()) != state:
            raise ValueError('interrupted guest preparation belongs to another machine-id')
    else:
        save(planned, state)
    # -A는 이미 존재하는 키를 교체하지 않는다. 부분 실패는 SSH를 막고 다음 부팅에 재시도한다.
    args = ['ssh-keygen', '-A']
    if root != Path('/'):
        args += ['-f', str(root)]
    command(args, check=True, timeout=120)
    if not all(p.is_file() and not p.is_symlink() and p.stat().st_size for p in keys):
        raise ValueError('guest SSH host key generation incomplete')
    validate_keys(keys, command)
    if identity.exists() and identity.read_text().strip() != instance_id:
        raise ValueError('existing instance-id is not the planned guest identity')
    if not identity.exists():
        identity.write_text(instance_id + '\n')
        identity.chmod(0o600)
        os.sync()
    save(marker, state)


if __name__ == '__main__':
    ensure()
