"""새 Linux 템플릿에만 apt 패키지·기존 Agent 설치 도구를 적용한다.

일반 VM/seed 생성에서는 네트워크·apt를 호출하지 않는다. 원본 cloud image와
게시 템플릿은 수정하지 않으며 서비스 실행 억제·DNS 임시 설정은 실패해도 복원한다.
"""
from contextlib import contextmanager
import hashlib
import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess

from prepare_rootfs import inside, verify_guest_agent

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'platform/services/vm-agent'
INSTALLER = SOURCE / 'agent/deploy/install_into_rootfs.py'
PACKAGES = ['python3-paho-mqtt']


def source_files():
    """실제로 복사하는 두 패키지·unit·설치 도구의 입력을 source lock에 포함한다."""
    files = [INSTALLER, SOURCE / 'agent/deploy/xenova-agent.service',
             SOURCE / 'agent/deploy/xenova-agent-identity.conf',
             SOURCE / 'agent/pyproject.toml', SOURCE / 'mqtt-client/pyproject.toml']
    for project, package in [('agent', 'xenova_agent'), ('mqtt-client', 'xenova_mqtt')]:
        for path in sorted((SOURCE / project / package).rglob('*')):
            parts = path.relative_to(SOURCE / project / package).parts
            if any(fnmatch.fnmatch(part, pattern) for part in parts
                   for pattern in ('tests', '__pycache__', '*.pyc', '*.egg-info')):
                continue
            if path.is_symlink():
                raise ValueError('Agent input must not contain symlinks')
            if path.is_file():
                files.append(path)
    for path in files:
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError('Agent input must be a canonical regular file')
    return files


def apt_command(argv, *, check=True, timeout):
    """자신이 시작한 apt 프로세스 그룹만 회수한 뒤 timeout/실패를 전달한다."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
    def terminate(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    try:
        status = process.wait(timeout=timeout)
    except BaseException:
        terminate(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            terminate(signal.SIGKILL)
            process.wait()
        raise
    if check and status:
        terminate(signal.SIGKILL)
        raise subprocess.CalledProcessError(status, argv)
    return subprocess.CompletedProcess(argv, status)


@contextmanager
def package_environment(root):
    """chroot 전용 DNS와 policy-rc.d만 임시 교체한다. 기존 링크·모드도 복원한다."""
    saved = []
    try:
        settings = [
            ('usr/sbin/policy-rc.d', b'#!/bin/sh\nexit 101\n', 0o755),
            ('etc/resolv.conf', Path('/etc/resolv.conf').read_bytes(), 0o644),
            # 배포판 apt 설정은 보존하고 이번 명령에만 추천 명령·GUI·MOTD 후처리를 제외한다.
            ('run/xenova-agent-apt.conf',
             b'#clear APT::Update::Post-Invoke;\n'
             b'#clear APT::Update::Post-Invoke-Success;\n'
             b'#clear DPkg::Post-Invoke;\n', 0o600),
        ]
        for relative, data, mode in settings:
            path = inside(root, relative, follow_final=False)
            if path.is_symlink():
                original = ('link', os.readlink(path), None)
            elif path.exists():
                if not path.is_file():
                    raise ValueError('invalid temporary package configuration')
                original = ('file', path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            else:
                original = ('absent', None, None)
            saved.append((path, original))
            if path.is_symlink():
                path.unlink()
            path.write_bytes(data); path.chmod(mode)
        yield
    finally:
        for path, (kind, data, mode) in reversed(saved):
            if path.is_symlink() or kind != 'file':
                path.unlink(missing_ok=True)
            if kind == 'link':
                path.symlink_to(data)
            elif kind == 'file':
                path.write_bytes(data); path.chmod(mode)


def prepare(root, policy, *, command=apt_command):
    """승인한 recipe의 Paho를 설치하고 검증 결과·해시를 새 rootfs에 기록한다."""
    root = Path(root)
    if policy != {'packages': PACKAGES}:
        raise ValueError('unsupported Linux guest Agent package policy')
    if (not inside(root, 'etc/xenova/template-prepared').is_file()
            or inside(root, 'etc/xenova/instance-id').exists()
            or inside(root, 'etc/xenova/deployment-seed.json').exists()):
        raise ValueError('Agent package installation requires a new prepared template')
    prefix = ['chroot', str(root), '/usr/bin/env',
              'DEBIAN_FRONTEND=noninteractive', 'SYSTEMD_OFFLINE=1',
              'PYTHONDONTWRITEBYTECODE=1']
    with package_environment(root):
        command(prefix + ['apt-get', '-c', '/run/xenova-agent-apt.conf', '-o', 'APT::Update::Error-Mode=any',
                          '-o', 'Acquire::Retries=2', 'update'], check=True, timeout=300)
        command(prefix + ['apt-get', '-c', '/run/xenova-agent-apt.conf', '-y', '--no-install-recommends', '--no-upgrade',
                          '-o', 'Dpkg::Use-Pty=0', '-o',
                          'Binary::apt-get::APT::Keep-Downloaded-Packages=true',
                          'install', *PACKAGES], check=True, timeout=900)
    spec = importlib.util.spec_from_file_location('xenova_guest_agent_installer', INSTALLER)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.install(root, SOURCE)
    result = verify_guest_agent(root, seed=False, required=True)
    fmt = chr(36) + '{binary:Package}\t' + chr(36) + '{Version}\t' + chr(36) + '{Architecture}\n'
    packages = subprocess.check_output(['chroot', str(root), 'dpkg-query', '-W',
                                       '-f=' + fmt, 'python3', *PACKAGES],
                                      text=True, timeout=60)
    archives = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(inside(root, 'var/cache/apt/archives').glob('*.deb'))}
    receipt = {'schema': 1, 'scope': 'template-before-instance-identity', 'packages': packages.splitlines(), 'archives_sha256': archives,
               'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in source_files()},
               'verification': result, 'broker_connection': 'not-tested'}
    inside(root, 'etc/xenova/agent-install.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt
