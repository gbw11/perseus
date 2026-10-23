"""실제 systemd unit loader로 정방향 의존성과 주입한 순환을 검사한다. 서비스를 실행하지 않는다."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
BRINGUP = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-bringup/files'


def fixture(root, *, cycle=False):
    units = root / 'usr/lib/systemd/system'
    units.mkdir(parents=True)
    for path in [*FILES.glob('*.service'), *FILES.glob('*.mount'), *BRINGUP.glob('*.service')]:
        content = path.read_text().replace('@BINDIR@', '/usr/bin').replace('@SBINDIR@', '/usr/sbin')
        if cycle and path.name == 'xenova-data-prepare.service':
            content = content.replace('[Unit]\n', '[Unit]\nAfter=xenova-vms.service\n', 1)
        (units / path.name).write_text(content)
    for name in ['sysinit.target', 'basic.target', 'local-fs.target', 'local-fs-pre.target', 'shutdown.target', 'umount.target', 'multi-user.target', 'sockets.target', 'slices.target', 'timers.target', 'paths.target']:
        (units / name).write_text('[Unit]\nDefaultDependencies=no\n')
    for name in ['xenstored.service', 'xenconsoled.service', 'xen-init-dom0.service', 'xen-qemu-dom0-disk-backend.service', 'systemd-udev-settle.service', 'systemd-udev-trigger.service', 'systemd-remount-fs.service']:
        (units / name).write_text('[Unit]\nDefaultDependencies=no\n[Service]\nType=oneshot\nExecStart=/bin/true\nRemainAfterExit=yes\n')
    for path in ('bin/true', 'bin/kill', 'usr/sbin/xenova-first-boot', 'usr/sbin/xenova-vm', 'usr/sbin/xenova-bridge-up', 'usr/bin/dnsmasq'):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#!/bin/sh\nexit 0\n')
        target.chmod(0o755)
    etc = root / 'etc'
    etc.mkdir()
    (etc / 'os-release').write_text('ID=xenova-unit-fixture\n')
    return units


def verify(root, *, cycle=False):
    if not shutil.which('systemd-analyze'):
        raise RuntimeError('systemd-analyze required for the unit dependency check')
    units = fixture(root, cycle=cycle)
    result = subprocess.run(['systemd-analyze', 'verify', '--man=no', '--generators=no', '--root=' + str(root),
                             str(units / 'xenova-vms.service'), str(units / 'xenova-host-admission.service'), str(units / 'srv-xenova.mount')],
                            text=True, capture_output=True, check=False)
    return result


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='xenova-unit-graph-') as directory:
        base = Path(directory)
        a, b = base / 'normal', base / 'cycle'
        a.mkdir(); b.mkdir()
        normal = verify(a)
        injected = verify(b, cycle=True)
        print('normal:', normal.returncode, normal.stdout, normal.stderr)
        print('injected cycle:', injected.returncode, injected.stdout, injected.stderr)
        if normal.returncode or not injected.returncode or 'cycle' not in injected.stderr.lower():
            raise SystemExit(1)
        print('PASS actual systemd loader: no dependency cycle; injected cycle detected')
