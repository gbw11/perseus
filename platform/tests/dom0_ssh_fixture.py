"""실제 dom0 WIC의 전체 unit/link fixture와 고정 레시피 설치 동작을 격리 재생한다.

명령 파일은 존재 확인용 스텁이며 실행하지 않는다. unit·기본 target은 스텁이 아니다.
새 WIC/게스트 seed를 생성하지 않으며, PID1 또는 SSH 리스너 시험을 대신하지 않는다.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'
FIXTURE = ROOT / 'platform/tests/fixtures/dom0-systemd'
PROVENANCE = json.loads((FIXTURE / 'provenance.json').read_text())


def vendor_service():
    data = (FIXTURE / 'sshd.service.in').read_bytes()
    if hashlib.sha256(data).hexdigest() != PROVENANCE['vendor_service_sha256']:
        raise ValueError('fixed OpenSSH service source digest mismatch')
    text = data.decode()
    for key, value in {'BASE_BINDIR': '/usr/bin', 'SBINDIR': '/usr/sbin',
                       'BINDIR': '/usr/bin', 'LIBEXECDIR': '/usr/libexec'}.items():
        text = text.replace('@' + key + '@', value)
    return text


def extract(root):
    data = (FIXTURE / PROVENANCE['archive']).read_bytes()
    if hashlib.sha256(data).hexdigest() != PROVENANCE['sha256']:
        raise ValueError('real dom0 fixture digest mismatch')
    links = []
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for item in tar:
            name = Path(item.name)
            if name.is_absolute() or '..' in name.parts:
                raise ValueError('unsafe dom0 fixture path')
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            spec = PROVENANCE['files'][item.name]
            if item.issym() and item.linkname == spec.get('link'):
                links.append((target, item.linkname))
            elif item.isfile():
                value = tar.extractfile(item).read()
                if hashlib.sha256(value).hexdigest() != spec.get('sha256'):
                    raise ValueError('dom0 unit differs from original WIC')
                target.write_bytes(value); target.chmod(item.mode)
            else:
                raise ValueError('unexpected dom0 fixture member')
    for target, value in links:
        target.symlink_to(value)
    (root / 'lib').symlink_to('usr/lib')
    # 그래프 로더의 실행 파일 존재 검사만 충족한다. generator도 실행하지 않는다.
    commands = set()
    for path in (root / 'usr/lib/systemd/system').rglob('*'):
        if path.is_file() and not path.is_symlink():
            for match in re.finditer(r'^Exec\w*=[-+!:@]*([^\s;]+)', path.read_text(), re.M):
                if match[1].startswith('/'):
                    commands.add(match[1])
    commands.update(('/usr/sbin/sshd', '/usr/bin/mkdir', '/usr/sbin/xenova-vm'))
    for name in commands:
        path = root / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)



def project_xenova_units(root):
    """검증된 과거 fixture의 프로젝트 유닛만 새 소스로 투영한다. 원본 증거는 불변이다."""
    old_units = ('perseus-bridge.service', 'perseus-data-prepare.service',
                 'perseus-dhcp.service', 'perseus-first-boot.service',
                 'perseus-vm-shutdown.service', 'perseus-vms.service',
                 'perseus-xen-ready.service', 'srv-perseus.mount')
    units = root / 'usr/lib/systemd/system'
    for old in old_units:
        current = old.replace('perseus', 'xenova')
        old_path = units / old
        if old_path.exists():
            old_path.unlink()
            origin = (ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-bringup/files'
                      if old in ('perseus-bridge.service', 'perseus-dhcp.service') else FILES)
            # bringup 레시피와 동일한 두 경로 치환만 적용한다.
            text = (origin / current).read_text().replace('@SBINDIR@', '/usr/sbin').replace('@BINDIR@', '/usr/bin')
            (units / current).write_text(text)
    wants = root / 'etc/systemd/system/multi-user.target.wants'
    if wants.exists():
        for old in old_units:
            link = wants / old
            if link.is_symlink():
                link.unlink()
                (wants / old.replace('perseus', 'xenova')).symlink_to(
                    '/usr/lib/systemd/system/' + old.replace('perseus', 'xenova'))
    for old in ('98-perseus-bringup.preset', '98-perseus-platform.preset'):
        path = root / 'usr/lib/systemd/system-preset' / old
        if path.exists():
            text = path.read_text()
            path.unlink()
            path.with_name(old.replace('perseus', 'xenova')).write_text(text.replace('perseus', 'xenova'))
    for unit in ('sshd.service', 'sshd.socket', 'sshd@.service'):
        old = root / 'etc/systemd/system' / (unit + '.d/10-perseus-keys.conf')
        old.unlink(missing_ok=True)
    for command in ('xenova-vm', 'xenova-first-boot', 'xenova-bridge-up'):
        path = root / 'usr/sbin' / command
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text('#!/bin/sh\\nexit 0\\n'); path.chmod(0o755)


def install_policy(root, *, replay_recipe=False):
    """새 패키지 설치 결과를 재생한다. 이전 WIC에 제자리 갱신하는 도구가 아니다."""
    project_xenova_units(root)
    append = ROOT / 'platform/yocto/meta-xenova/recipes-connectivity/openssh/openssh_%.bbappend'
    text = append.read_text()
    if ('PACKAGECONFIG:remove = "systemd-sshd-socket-mode"' not in text
            or 'PACKAGECONFIG:append = " systemd-sshd-service-mode"' not in text):
        raise ValueError('OpenSSH recipe no longer selects the tested service mode')
    # clean rootfs의 openssh-sshd/xenova-platform 패키지 소유 파일 차이를 재생한다.
    for name in ('sshd.socket', 'sshd@.service'):
        (root / 'usr/lib/systemd/system' / name).unlink(missing_ok=True)
    for name in ('sshd.service', 'sshd.socket', 'sshd@.service'):
        (root / 'etc/systemd/system' / (name + '.d/10-xenova-keys.conf')).unlink(missing_ok=True)
    (root / 'etc/systemd/system/sockets.target.wants/sshd.socket').unlink(missing_ok=True)
    unit = root / 'usr/lib/systemd/system/sshd.service'
    unit.parent.mkdir(parents=True, exist_ok=True); unit.write_text(vendor_service())
    preset = root / 'usr/lib/systemd/system-preset/98-openssh-sshd.preset'
    preset.parent.mkdir(parents=True, exist_ok=True); preset.write_text('enable sshd.service\n')
    enabled = root / 'etc/systemd/system/multi-user.target.wants/sshd.service'
    enabled.parent.mkdir(parents=True, exist_ok=True)
    if not enabled.is_symlink():
        enabled.symlink_to('/usr/lib/systemd/system/sshd.service')
    if replay_recipe:
        recipe = ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/xenova-platform_1.0.bb'
        body = recipe.read_text().split('do_install() {\n', 1)[1].split('\n}\n', 1)[0]
        with tempfile.TemporaryDirectory(prefix='xenova-dom0-recipe-source-') as tmp:
            source = Path(tmp)
            for directory in (FILES, ROOT / 'platform/scripts', ROOT / 'platform/guests/common', ROOT / 'platform/first-install'):
                for path in directory.iterdir():
                    if path.is_file():
                        (source / path.name).write_bytes(path.read_bytes())
            for os_id in ('ubuntu', 'debian'):
                shutil.copytree(ROOT / 'platform/guests' / os_id, source / os_id)
            env = {**os.environ, 'D': str(root), 'S': str(source), 'sbindir': '/usr/sbin',
                   'libdir': '/usr/lib', 'datadir': '/usr/share', 'sysconfdir': '/etc',
                   'systemd_system_unitdir': '/usr/lib/systemd/system'}
            subprocess.run(['sh', '-eu', '-c', body], env=env, check=True, capture_output=True)
    else:
        drop = root / 'etc/systemd/system/sshd.service.d/10-xenova-keys.conf'
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_bytes((FILES / 'xenova-sshd-keys.conf').read_bytes())
        generator = root / 'etc/systemd/system-generators/systemd-ssh-generator'
        generator.parent.mkdir(parents=True, exist_ok=True); generator.symlink_to('/dev/null')


def verify(root, *units):
    prefix = json.loads(os.environ.get('XENOVA_DOM0_SYSTEMD_ANALYZE', '["systemd-analyze"]'))
    version = subprocess.run(prefix + ['--version'], text=True, capture_output=True, check=True)
    if int(version.stdout.split()[1]) < 259:
        raise ValueError('dom0 fixture requires a systemd >=259 loader; set XENOVA_DOM0_SYSTEMD_ANALYZE JSON argv')
    empty_generators = root / 'disabled-environment-generators'
    empty_generators.mkdir(exist_ok=True)
    result = subprocess.run(prefix + ['verify', '--recursive-errors=yes', '--generators=no',
        '--man=no', '--root=' + str(root), 'multi-user.target', *units], text=True, capture_output=True,
        env={**os.environ, 'SYSTEMD_LOG_LEVEL': 'debug',
             'SYSTEMD_ENVIRONMENT_GENERATOR_PATH': str(empty_generators)}, timeout=60)
    if os.environ.get('XENOVA_TEST_LOG_DIR'):
        log = Path(os.environ['XENOVA_TEST_LOG_DIR']) / (root.name + '-' + str(time.monotonic_ns()) + '.dom0-systemd.log')
        log.write_text(json.dumps({'argv': result.args, 'returncode': result.returncode}) + '\n' + result.stdout + result.stderr)
    return result
