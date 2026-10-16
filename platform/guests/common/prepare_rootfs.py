"""새 템플릿/인스턴스의 오프라인 최초 설정. 실행 중인 VM이나 기존 인스턴스에는 금지.

이 모듈을 게스트 부팅 서비스로 설치하지 않는다. 사용자 데이터와 인증 변경을
보존하려면 최초 생성 뒤에는 다시 호출하지 않아야 한다.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import time

ROOT_HASH = '$6$perseus-dev$uqz/PNLD2CDIZm1VIVOFPkMANu8CHr2KHPKO3th9aIL6nu6f.aLwxZ8oHjw3haeQHSsLzWokvwcuaAcMVpy800'
AUTH = 'PermitRootLogin yes\nPasswordAuthentication yes\nPermitEmptyPasswords no\nAuthenticationMethods any\n'


def inside(root, relative, *, follow_final=True, create_parents=True):
    """게스트 기준으로 링크를 해석한다. 교체할 마지막 링크는 따라가지 않는다.

    절대 링크는 게스트 /에서 다시 시작하며, 상대 링크의 root 밖 ..와 순환은 거부한다.
    외부에서 동시에 수정하지 않는 오프라인 사본 전용이다.
    """
    root = Path(root).resolve(strict=True)
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts:
        raise ValueError(f'guest path must be relative: {relative}')
    pending, resolved, links = list(relative.parts), [], 0
    while pending:
        part = pending.pop(0)
        if part == '..':
            if not resolved:
                raise ValueError(f'guest path escapes root: {relative}')
            resolved.pop()
            continue
        candidate = root.joinpath(*resolved, part)
        if candidate.is_symlink() and (pending or follow_final):
            links += 1
            if links > 40:
                raise ValueError(f'guest symlink limit exceeded: {relative}')
            target = candidate.readlink()
            if target.is_absolute():
                resolved = []
                target_parts = target.parts[1:]
            else:
                target_parts = target.parts
            pending = list(target_parts) + pending
        else:
            resolved.append(part)
    if resolved and resolved[0] in ('proc','sys','dev') and len(resolved) > 1:
        raise ValueError(f'guest path points into a special filesystem: {relative}')
    if not resolved:
        raise ValueError(f'guest path resolves to root directory: {relative}')
    p = root.joinpath(*resolved)
    if create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def replace_link(root, relative, target):
    """부모는 게스트 안에서 해석하고 마지막 링크는 대상 파일을 건드리지 않고 교체한다."""
    link = inside(root, relative, follow_final=False)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)


def write(root, relative, text, mode=0o644):
    p = inside(root, relative)
    p.write_text(text)
    p.chmod(mode)


def enable(root, unit):
    if not inside(root, 'usr/lib/systemd/system/' + unit).is_file():
        raise ValueError(f'required guest systemd unit missing: {unit}')
    replace_link(root, 'etc/systemd/system/multi-user.target.wants/' + unit,
                 '/usr/lib/systemd/system/' + unit)


def prepare_template(root):
    root = Path(root)
    os_release = inside(root, 'etc/os-release', create_parents=False)
    if os_release.is_file() and 'ID=alpine' in os_release.read_text().replace(chr(34), '').splitlines():
        raise ValueError('Alpine is an extension point only; no supported preparation adapter')
    marker = inside(root, 'etc/perseus/template-prepared')
    if marker.exists() or inside(root, 'etc/perseus/instance-id').exists():
        raise ValueError('refusing to initialize an already prepared/instantiated rootfs')
    shadow = inside(root, 'etc/shadow')
    lines = shadow.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        fields = line.split(':')
        if fields[0] == 'root':
            fields[1], fields[2] = ROOT_HASH, str(int(time.time() // 86400))
            lines[i] = ':'.join(fields)
            found = True
    if not found:
        raise ValueError('root account absent')
    shadow.write_text('\n'.join(lines) + '\n')
    shadow.chmod(0o640)
    write(root, 'etc/ssh/perseus-auth.conf', AUTH)
    config = inside(root, 'etc/ssh/sshd_config')
    config.write_text('Include /etc/ssh/perseus-auth.conf\n' + config.read_text())
    write(root, 'etc/cloud/cloud-init.disabled', '')
    # Xen direct boot의 루트는 파티션 없는 ext4 xvda다. 펌웨어/EFI 마운트가 필요 없다.
    write(root, 'etc/fstab', '/dev/xvda / ext4 defaults 0 1\n')
    write(root, 'etc/systemd/network/05-perseus.network', '''[Match]
Name=eth0

[Network]
DHCP=ipv4
LinkLocalAddressing=no
IPv6AcceptRA=no

[DHCPv4]
UseDNS=no
UseRoutes=no
''')
    for unit in ('networking.service', 'systemd-networkd-wait-online.service', 'NetworkManager-wait-online.service'):
        replace_link(root, 'etc/systemd/system/' + unit, '/dev/null')
    enable(root, 'systemd-networkd.service')
    enable(root, 'ssh.service')
    replace_link(root, 'etc/systemd/system/getty.target.wants/serial-getty@hvc0.service',
                 '/usr/lib/systemd/system/serial-getty@.service')
    write(root, 'etc/machine-id', '')
    replace_link(root, 'var/lib/dbus/machine-id', '/etc/machine-id')
    for p in inside(root, 'etc/ssh').glob('ssh_host_*'):
        p.unlink()
    write(root, 'etc/perseus/template-prepared', '1\n')


def prepare_instance(root, record, command=subprocess.run):
    root = Path(root)
    if not inside(root, 'etc/perseus/template-prepared').exists():
        raise ValueError('template preparation marker absent')
    marker = inside(root, 'etc/perseus/instance-id')
    if marker.exists():
        raise ValueError('existing instance: initialization is forbidden')
    write(root, 'etc/hostname', record['hostname'] + '\n')
    write(root, 'etc/hosts', '127.0.0.1 localhost\n127.0.1.1 ' + record['hostname'] + '\n')
    write(root, 'etc/machine-id', os.urandom(16).hex() + '\n')
    # 호스트 ssh-keygen을 사용해 새 인스턴스에만 고유 키를 한 번 생성한다.
    for kind in ('ed25519', 'ecdsa', 'rsa'):
        target = inside(root, 'etc/ssh/ssh_host_' + kind + '_key', follow_final=False)
        if target.is_symlink() or target.exists():
            raise ValueError('template unexpectedly contains SSH host keys')
        command(['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(target)], check=True)
    write(root, 'etc/perseus/instance-id', record['uuid'] + '\n', 0o600)


def prepare_deployment_seed(root, record):
    """새 공통 이미지 전용 인스턴스 사본. 식별자는 systemd/guest SSH gate까지 비운다."""
    root = Path(root)
    if not inside(root, 'etc/perseus/template-prepared').is_file():
        raise ValueError('template preparation marker absent')
    if inside(root, 'etc/perseus/instance-id').exists() or inside(root, 'etc/perseus/deployment-seed.json').exists():
        raise ValueError('existing instance/seed must never be initialized again')
    if not inside(root, 'usr/bin/python3').is_file():
        raise ValueError('factory guest identity gate requires the cloud image Python 3')
    if any(inside(root, 'etc/ssh').glob('ssh_host_*_key')):
        raise ValueError('factory seed must not contain shared SSH host keys')
    write(root, 'etc/hostname', record['hostname'] + '\n')
    write(root, 'etc/hosts', '127.0.0.1 localhost\n127.0.1.1 ' + record['hostname'] + '\n')
    write(root, 'etc/machine-id', 'uninitialized\n')
    replace_link(root, 'var/lib/dbus/machine-id', '/etc/machine-id')
    write(root, 'usr/lib/perseus/guest_identity.py', (Path(__file__).with_name('guest_identity.py')).read_text())
    write(root, 'etc/perseus/deployment-seed.json', json.dumps({'schema': 1, 'id': record['id'], 'init': 'systemd'}) + '\n')
    unit = """[Unit]
Description=Establish factory guest identity before any SSH listener
After=systemd-remount-fs.service
Before=ssh.service ssh.socket sshd.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/lib/perseus/guest_identity.py
RemainAfterExit=yes
TimeoutStartSec=240s

[Install]
WantedBy=multi-user.target
"""
    write(root, 'usr/lib/systemd/system/perseus-guest-identity.service', unit)
    enable(root, 'perseus-guest-identity.service')
    for name in ('ssh.service', 'ssh.socket', 'sshd.service'):
        write(root, 'etc/systemd/system/' + name + '.d/10-perseus-identity.conf',
              '[Unit]\nRequires=perseus-guest-identity.service\nAfter=perseus-guest-identity.service\n')
