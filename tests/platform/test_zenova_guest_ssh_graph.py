"""실제 Ubuntu/Debian 유닛·링크·generator 그래프. PID1/SSH 실행 시험은 아니다."""
import hashlib
import io
import json
import os
import time
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, prepare_rootfs as guest
from test_zenova_platform import record, load

FIXTURES = ROOT / 'tests/platform/fixtures/guest-systemd'
PROVENANCE = json.loads((FIXTURES / 'provenance.json').read_text())
update = load('zenova_guest_ssh_update_test', ROOT / 'scripts/update-zenova-guest-ssh.py')


def extract(root, distribution):
    """일반 파일을 먼저 쓰고 링크를 마지막에 만든다. 링크를 따라 쓰지 않는다."""
    spec = PROVENANCE['guests'][distribution]
    path = FIXTURES / spec['archive']
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
        raise ValueError('real guest fixture digest mismatch')
    links = []
    with tarfile.open(path) as tar:
        for item in tar.getmembers():
            relative = Path(item.name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe unit fixture path')
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.issym():
                links.append((target, item.linkname))
            elif item.isfile():
                data = tar.extractfile(item).read()
                if hashlib.sha256(data).hexdigest() != spec['files'][item.name]['sha256']:
                    raise ValueError('unit bytes differ from original package')
                target.write_bytes(data); target.chmod(item.mode)
            else:
                raise ValueError('unexpected fixture type')
    for target, value in links:
        target.symlink_to(value)
    (root / 'lib').symlink_to('usr/lib')
    (root / 'etc/os-release').symlink_to('/usr/lib/os-release')
    # 로더의 명령 존재 검사 전용. 이 스텁과 게스트 서비스는 실행하지 않는다.
    for relative in ('usr/bin/python3', 'usr/sbin/sshd', 'bin/true', 'bin/kill'):
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
    (root / 'etc/zenova').mkdir()
    (root / 'etc/zenova/template-prepared').write_text('1\n')
    (root / 'etc/ssh').mkdir()


def verify(root):
    # Debian 257 fixture는 257 이상 로더를 사용한다. 호스트가 오래되면 JSON argv로 지정한다.
    prefix = json.loads(os.environ.get('ZENOVA_SYSTEMD_ANALYZE', '["systemd-analyze"]'))
    if not isinstance(prefix, list) or not prefix or not all(isinstance(v, str) and v for v in prefix):
        raise ValueError('ZENOVA_SYSTEMD_ANALYZE must be a nonempty JSON argv list')
    result = subprocess.run(prefix + ['verify', '--recursive-errors=yes', '--generators=no',
            '--man=no', '--root=' + str(root), 'multi-user.target', 'ssh.service',
            'zenova-guest-identity.service'], text=True, capture_output=True,
            env={**os.environ, 'SYSTEMD_LOG_LEVEL': 'debug'})
    if os.environ.get('ZENOVA_TEST_LOG_DIR'):
        log = Path(os.environ['ZENOVA_TEST_LOG_DIR']) / (root.name + '-' + str(time.monotonic_ns()) + '.systemd.log')
        log.write_text(json.dumps({'argv': result.args, 'returncode': result.returncode}) + '\n' + result.stdout + result.stderr)
    return result


class GuestSSHGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zenova-guest-unit-graph-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def root(self, distribution):
        root = self.base / distribution
        root.mkdir(); extract(root, distribution)
        return root

    def assertGraphAccepted(self, result):
        diagnostics = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, diagnostics)
        self.assertNotIn('Found ordering cycle', diagnostics)
        self.assertNotIn('deleted to break ordering cycle', diagnostics)
        # 파서가 drop-in/enable 링크를 건너뛰면 종료 코드 0이어도 통과시키지 않는다.
        self.assertIn('Requires: zenova-guest-identity.service', result.stdout)
        self.assertIn('zenova-guest-identity.service: Enqueued job', result.stderr)
        self.assertIn('ssh.service: Enqueued job', result.stderr)

    def test_original_ubuntu_socket_and_normal_identity_service_reproduce_cycle(self):
        root = self.root('ubuntu-24.04-20260926')
        guest.install_identity_gate(root)
        guest.write(root, 'etc/systemd/system/ssh.socket.d/10-zenova-identity.conf',
                    '[Unit]\nRequires=zenova-guest-identity.service\nAfter=zenova-guest-identity.service\n')
        result = verify(root)
        self.assertIn('Found ordering cycle', result.stderr)
        self.assertRegex(result.stderr, r'Job \S+/start deleted to break ordering cycle')
        # 실제 로더가 job 삭제로 복구해 rc=0을 내도 이 진단은 실패다.
        recovered = subprocess.CompletedProcess(result.args, 0, result.stdout, result.stderr)
        with self.assertRaises(AssertionError):
            self.assertGraphAccepted(recovered)
        deleted_only = subprocess.CompletedProcess(result.args, 0, result.stdout,
            '\n'.join(line for line in result.stderr.splitlines() if 'deleted to break ordering cycle' in line))
        with self.assertRaisesRegex(AssertionError, 'deleted to break ordering cycle'):
            self.assertGraphAccepted(deleted_only)
        self.assertTrue((root / 'etc/systemd/system/ssh.service.requires/ssh.socket').is_symlink())
        # 원본 fixture와 동일한 root/유닛에 실제 정책 함수를 적용해 전후를 대조한다.
        guest.configure_ssh_service(root)
        corrected = verify(root)
        self.assertGraphAccepted(corrected)

    def test_real_first_and_subsequent_boot_graphs_have_no_cycle_or_required_socket(self):
        for distribution in PROVENANCE['guests']:
            with self.subTest(distribution=distribution):
                root = self.root(distribution)
                guest.prepare_deployment_seed(root, record())
                unit = (root / 'usr/lib/systemd/system/zenova-guest-identity.service').read_text()
                self.assertNotIn('DefaultDependencies=no', unit)
                self.assertIn('Requires=zenova-guest-identity.service',
                    (root / 'etc/systemd/system/ssh.service.d/10-zenova-identity.conf').read_text())
                for boot in ('first', 'subsequent'):
                    result = verify(root)
                    self.assertGraphAccepted(result)
                    if boot == 'first':
                        (root / 'etc/zenova/identity-complete.json').write_text('{}')
                for path in ('sockets.target.wants/ssh.socket', 'ssh.service.requires/ssh.socket'):
                    self.assertFalse((root / 'etc/systemd/system' / path).is_symlink())
                self.assertEqual((root / 'etc/systemd/system/ssh.socket').readlink(), Path('/dev/null'))
                self.assertFalse((root / 'etc/systemd/system/ssh.socket.d/10-zenova-identity.conf').exists())
                for generator in (root / 'usr/lib/systemd/system-generators').glob('*ssh*'):
                    self.assertEqual((root / 'etc/systemd/system-generators' / generator.name).readlink(), Path('/dev/null'))

    def test_explicit_update_preserves_password_mid_keys_instance_and_debian_keygen(self):
        for distribution in PROVENANCE['guests']:
            with self.subTest(distribution=distribution):
                root = self.root(distribution)
                guest.prepare_deployment_seed(root, record())
                for relative, value in [('etc/shadow', 'root:USER_CHANGED_PASSWORD:20000:0:99999:7:::\n'),
                                        ('etc/machine-id', '1234567890abcdef1234567890abcdef\n'),
                                        ('etc/zenova/instance-id', record()['uuid'] + '\n'),
                                        ('etc/ssh/ssh_host_ed25519_key', 'preserved-private-key')]:
                    (root / relative).write_text(value)
                before = update.identities(root)
                vendor = {p.name: p.read_bytes() for p in (root / 'usr/lib/systemd/system').glob('ssh*') if p.is_file() and not p.is_symlink()}
                with patch.object(update.os, 'sync'), patch('sys.stdout', new=io.StringIO()):
                    update.main(['--root', str(root)])
                    self.assertFalse((root / 'var/lib/zenova').exists())
                    update.main(['--root', str(root), '--execute'])
                    update.main(['--root', str(root), '--execute'])
                self.assertEqual(update.identities(root), before)
                for name, data in vendor.items(): self.assertEqual((root / 'usr/lib/systemd/system' / name).read_bytes(), data)
                self.assertGraphAccepted(verify(root))
                self.assertEqual(len(list((root / 'var/lib/zenova').glob('ssh-policy-backup-*'))), 2)

    def test_ubuntu_00_socket_dropin_is_shadowed_and_identity_failure_remains_a_hard_gate(self):
        root = self.root('ubuntu-24.04-20260926')
        guest.write(root, 'etc/systemd/system/ssh.service.d/00-socket.conf', '[Unit]\n  Requires = ssh.socket\nWants\t= ssh.socket\nAfter = ssh.socket\n')
        guest.prepare_deployment_seed(root, record())
        self.assertEqual((root / 'etc/systemd/system/ssh.service.d/00-socket.conf').readlink(), Path('/dev/null'))
        dropin = (root / 'etc/systemd/system/ssh.service.d/10-zenova-identity.conf').read_text()
        self.assertIn('Requires=zenova-guest-identity.service', dropin)
        self.assertIn('After=zenova-guest-identity.service', dropin)
        self.assertNotIn('Wants=', dropin)
        self.assertGraphAccepted(verify(root))

    def test_new_instance_from_old_template_receives_service_policy_without_reinitializing_it(self):
        root = self.root('ubuntu-24.04-20260926')
        before_units = (root / 'usr/lib/systemd/system/ssh.service').read_bytes()
        guest.prepare_instance(root, record(), command=lambda *a, **kw: None)
        mid = (root / 'etc/machine-id').read_text()
        self.assertEqual((root / 'usr/lib/systemd/system/ssh.service').read_bytes(), before_units)
        self.assertFalse((root / 'etc/systemd/system/ssh.service.requires/ssh.socket').is_symlink())
        with self.assertRaisesRegex(ValueError, 'existing instance'):
            guest.prepare_instance(root, record(), command=lambda *a, **kw: None)
        self.assertEqual((root / 'etc/machine-id').read_text(), mid)

    def test_custom_required_socket_dependency_is_refused_before_changing_enable_links(self):
        root = self.root('ubuntu-24.04-20260926')
        guest.write(root, 'etc/systemd/system/ssh.service.d/custom.conf', '[Unit]\nRequires=ssh.socket\n')
        enabled = root / 'etc/systemd/system/sockets.target.wants/ssh.socket'
        with self.assertRaisesRegex(ValueError, 'manual review'):
            guest.configure_ssh_service(root)
        self.assertTrue(enabled.is_symlink())
        self.assertFalse((root / 'etc/systemd/system/ssh.socket').is_symlink())


    def snapshot(self, root):
        """링크를 따라 읽지 않고 전체 트리의 바이트/대상/모드/mtime을 비교한다."""
        result = {}
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                stat = path.lstat()
                value = ('link', str(path.readlink())) if path.is_symlink() else (
                    ('directory',) if path.is_dir() else ('file', hashlib.sha256(path.read_bytes()).hexdigest()))
                result[str(path.relative_to(root))] = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, value)
        return result

    def test_dependency_parent_links_never_unlink_external_assets(self):
        for suffix in ('wants', 'requires'):
            for absolute in (True, False):
                with self.subTest(suffix=suffix, absolute=absolute), tempfile.TemporaryDirectory(
                        prefix='zenova-ssh-external-') as tmp:
                    root = self.base / ('root-' + suffix + '-' + str(absolute))
                    root.mkdir(); extract(root, 'ubuntu-24.04-20260926')
                    external = Path(tmp)
                    for name in ('ssh.socket', 'sshd.socket'):
                        (external / name).symlink_to('sentinel')
                    (external / 'sentinel').write_bytes(b'external asset must stay unchanged')
                    parent = root / ('etc/systemd/system/custom.target.' + suffix)
                    parent.symlink_to(external if absolute else os.path.relpath(external, parent.parent))
                    external_before = self.snapshot(external)
                    if absolute:
                        # 같은 절대 경로의 guest 내부 사본만 정리해야 한다.
                        shadow = root / str(external).lstrip('/')
                        shadow.mkdir(parents=True)
                        for name in ('ssh.socket', 'sshd.socket'):
                            (shadow / name).symlink_to('/usr/lib/systemd/system/' + name)
                        guest.configure_ssh_service(root)
                        for name in ('ssh.socket', 'sshd.socket'):
                            self.assertFalse((shadow / name).is_symlink())
                        self.assertEqual(parent.readlink(), external)
                    else:
                        guest_before = self.snapshot(root)
                        with self.assertRaisesRegex(ValueError, 'escapes root'):
                            guest.configure_ssh_service(root)
                        self.assertEqual(self.snapshot(root), guest_before)
                    self.assertEqual(self.snapshot(external), external_before)

    def test_whitespace_socket_conflicts_are_rejected_without_any_changes(self):
        for key in ('Requires', 'Wants', 'BindsTo', 'Requisite'):
            for value in ('ssh.socket', 'sshd.socket'):
                with self.subTest(key=key, value=value):
                    root = self.base / (key + '-' + value)
                    root.mkdir(); extract(root, 'ubuntu-24.04-20260926')
                    guest.write(root, 'etc/systemd/system/ssh.service.d/custom.conf',
                                '[Unit]\n  ' + key + ' \t= network.target ' + value + '\n')
                    before = self.snapshot(root)
                    with self.assertRaisesRegex(ValueError, 'manual review'):
                        guest.configure_ssh_service(root)
                    self.assertEqual(self.snapshot(root), before)

    def test_explicit_vm_update_cli_preflights_before_backup_or_identity_writes(self):
        for spaced in (False, True):
            for execute in (False, True):
                with self.subTest(spaced=spaced, execute=execute):
                    root = self.base / ('existing-vm-' + str(spaced) + '-' + str(execute))
                    root.mkdir(); extract(root, 'ubuntu-24.04-20260926')
                    guest.install_identity_gate(root)
                    guest.write(root, 'etc/zenova/deployment-seed.json', '{}\n')
                    guest.write(root, 'usr/lib/zenova/guest_identity.py', '# existing identity code must survive\n')
                    guest.write(root, 'etc/systemd/system/ssh.service.d/custom.conf',
                                '[Unit]\n' + (' Requires = ssh.socket\n' if spaced else 'Requires=ssh.socket\n'))
                    before = self.snapshot(root)
                    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/update-zenova-guest-ssh.py'),
                                             '--root', str(root)] + (['--execute'] if execute else []),
                                            text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('manual review', result.stderr)
                    self.assertEqual(self.snapshot(root), before)
                    self.assertFalse((root / 'var/lib/zenova').exists())

    def test_comment_and_non_socket_dependencies_with_whitespace_are_preserved(self):
        root = self.root('ubuntu-24.04-20260926')
        text = '[Unit]\n# Requires = ssh.socket\n; Wants=sshd.socket\n Requires = network.target\nAfter = network.target\n'
        guest.write(root, 'etc/systemd/system/ssh.service.d/custom.conf', text)
        guest.prepare_deployment_seed(root, record())
        self.assertEqual((root / 'etc/systemd/system/ssh.service.d/custom.conf').read_text(), text)
        self.assertGraphAccepted(verify(root))


    def test_updater_import_and_help_work_without_uninstalled_uuid_module(self):
        original_import = __import__

        def without_uuid(name, *args, **kwargs):
            if name == 'uuid':
                raise ModuleNotFoundError("minimal dom0 image does not install uuid")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=without_uuid), patch('sys.stdout', new=io.StringIO()):
            minimal = load('zenova_ssh_update_minimal_image', ROOT / 'scripts/update-zenova-guest-ssh.py')
            with self.assertRaises(SystemExit) as result:
                minimal.main(['--help'])
        self.assertEqual(result.exception.code, 0)
