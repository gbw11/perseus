"""seed/일반 SSH 옵션과 전용 chroot 런타임의 성공·실패·경로 방어 회귀."""
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from first_install_fixture import assemble

backend = assemble.prepare.shared_make_instance.__globals__
ssh = backend['verify_guest_ssh']
runtime = backend['guest_runtime']
module = __import__(ssh.__module__)
POLICY = 'permitrootlogin yes\npasswordauthentication yes\npermitemptypasswords no\nauthenticationmethods any\n'


class SeedSSHTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ('dev', 'run'):
            (self.root / relative).mkdir()
        self.work = SimpleNamespace(mounts=[self.root])
        self.calls = []

    def command(self, args, **kwargs):
        self.calls.append([str(a) for a in args])

    def device(self, path, mode, device):
        self.assertEqual(stat.S_IFMT(mode), stat.S_IFCHR)
        self.assertEqual(device, module.os.makedev(1, 3))
        Path(path).touch()

    def test_seed_has_both_G_and_T_before_root_Match_and_bounded_timeout(self):
        with patch.object(module.subprocess, 'check_output', return_value=POLICY) as run:
            ssh(self.root, seed=True)
        args = run.call_args.args[0]
        self.assertEqual(args[3:6], ['-G', '-T', '-C'])
        self.assertIn('user=root,', args[-1])
        self.assertEqual(run.call_args.kwargs['timeout'], 60)
        self.assertNotIn('-A', args)

    def test_normal_instance_uses_T_and_does_not_skip_host_keys(self):
        with patch.object(module.subprocess, 'check_output', return_value=POLICY) as run:
            ssh(self.root)
        self.assertEqual(run.call_args.args[0][3:5], ['-T', '-C'])
        self.assertNotIn('-G', run.call_args.args[0])

    def test_bad_effective_auth_and_sshd_errors_propagate(self):
        with patch.object(module.subprocess, 'check_output', return_value=POLICY.replace('permitemptypasswords no', 'permitemptypasswords yes')):
            with self.assertRaisesRegex(ValueError, 'effective policy'): ssh(self.root, seed=True)
        for error in (subprocess.CalledProcessError(255, 'sshd'), subprocess.TimeoutExpired('sshd', 60)):
            with patch.object(module.subprocess, 'check_output', side_effect=error):
                with self.assertRaises(type(error)): ssh(self.root, seed=True)

    def test_private_runtime_mounts_only_tmpfs_and_unmounts_before_root(self):
        with patch.object(module, 'command', side_effect=self.command), patch.object(module.os, 'mknod', side_effect=self.device):
            with runtime(self.work, self.root):
                self.assertTrue((self.root / 'run/sshd').is_dir())
                self.assertEqual((self.root / 'dev/null').stat().st_mode & 0o777, 0o666)
                self.assertEqual(self.work.mounts, [self.root, self.root / 'dev', self.root / 'run'])
        self.assertEqual(self.work.mounts, [self.root])
        self.assertEqual([c[0] for c in self.calls], ['mount', 'mount', 'umount', 'umount'])
        self.assertEqual([c[-1] for c in self.calls[-2:]], [str(self.root / 'run'), str(self.root / 'dev')])
        self.assertFalse(any('--bind' in c or 'devtmpfs' in c for c in self.calls))

    def test_sshd_failure_and_timeout_still_release_temporary_mounts(self):
        for error in (subprocess.CalledProcessError(255, 'sshd'), subprocess.TimeoutExpired('sshd', 60)):
            # fixture의 실제 파일은 mock mount와 달리 가려지지 않으므로 각 반복의 검사 디렉터리만 비운다.
            if (self.root / 'run/sshd').exists(): (self.root / 'run/sshd').rmdir()
            with patch.object(module, 'command', side_effect=self.command), patch.object(module.os, 'mknod', side_effect=self.device):
                with self.assertRaises(type(error)):
                    with runtime(self.work, self.root): raise error
            self.assertEqual(self.work.mounts, [self.root])

    def test_second_mount_failure_releases_only_successful_first_mount(self):
        def fail_run(args, **kwargs):
            self.command(args)
            if args[0] == 'mount' and args[-1] == self.root / 'run': raise subprocess.CalledProcessError(1, args)
        with patch.object(module, 'command', side_effect=fail_run), patch.object(module.os, 'mknod', side_effect=self.device):
            with self.assertRaises(subprocess.CalledProcessError):
                with runtime(self.work, self.root): self.fail('must not enter')
        self.assertEqual(self.work.mounts, [self.root])
        self.assertEqual(self.calls[-1], ['umount', str(self.root / 'dev')])

    def test_device_creation_failure_unmounts_dev_and_skips_sshd(self):
        with patch.object(module, 'command', side_effect=self.command), patch.object(module.os, 'mknod', side_effect=PermissionError('no capability')):
            with self.assertRaises(PermissionError):
                with runtime(self.work, self.root): self.fail('must not enter')
        self.assertEqual(self.work.mounts, [self.root])
        self.assertEqual([c[0] for c in self.calls], ['mount', 'umount'])

    def test_symlinked_mountpoint_is_rejected_before_mount_or_host_access(self):
        (self.root / 'dev').rmdir(); (self.root / 'dev').symlink_to('/dev')
        with patch.object(module, 'command', side_effect=AssertionError('must not mount')):
            with self.assertRaisesRegex(ValueError, 'unlinked directory'):
                with runtime(self.work, self.root): self.fail('must not enter')
        self.assertEqual(self.work.mounts, [self.root])

    def test_unmount_failure_stays_tracked_and_fails_closed(self):
        def fail_unmount(args, **kwargs):
            self.command(args)
            if args[0] == 'umount': raise subprocess.CalledProcessError(1, args)
        with patch.object(module, 'command', side_effect=fail_unmount), patch.object(module.os, 'mknod', side_effect=self.device):
            with self.assertRaises(subprocess.CalledProcessError):
                with runtime(self.work, self.root): pass
        self.assertEqual(self.work.mounts, [self.root, self.root / 'dev', self.root / 'run'])
