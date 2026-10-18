"""WIC 전체 dom0 그래프의 수정 전 순환과 실제 레시피 설치 후 그래프를 비교한다."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from dom0_ssh_fixture import extract, install_policy, verify, FILES, ROOT


class Dom0SSHGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='perseus-dom0-unit-graph-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        extract(self.root)

    def assertAccepted(self, result):
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, text)
        for diagnostic in ('Found ordering cycle', 'deleted to break ordering cycle',
                           'Ordering cycle found, skipping'):
            self.assertNotIn(diagnostic, text)
        self.assertIn('Requires: sshdgenkeys.service', result.stdout)
        self.assertRegex(result.stderr, r'sshdgenkeys\.service: (Installed new job|Merged) sshdgenkeys\.service/start')
        self.assertIn('sshd.service: Enqueued job', result.stderr)

    def test_original_real_dom0_cycle_and_job_deletion_then_same_fixture_corrected(self):
        before = verify(self.root, 'sshd.socket')
        self.assertIn('Found ordering cycle', before.stderr)
        self.assertRegex(before.stderr, r'Job \S+/start deleted to break ordering cycle')
        for diagnostic in ('Found ordering cycle', 'deleted to break ordering cycle'):
            synthetic_zero = subprocess.CompletedProcess(before.args, 0, before.stdout + '\nRequires: sshdgenkeys.service',
                '\n'.join(line for line in before.stderr.splitlines() if diagnostic in line))
            with self.assertRaisesRegex(AssertionError, diagnostic):
                self.assertAccepted(synthetic_zero)
        install_policy(self.root, replay_recipe=True)
        self.assertAccepted(verify(self.root, 'sshd.service'))

    def test_first_and_subsequent_boot_keep_key_gate_without_socket_or_generator(self):
        install_policy(self.root, replay_recipe=True)
        for boot in ('first', 'subsequent'):
            with self.subTest(boot=boot):
                self.assertAccepted(verify(self.root, 'sshd.service'))
        units = self.root / 'usr/lib/systemd/system'
        self.assertNotIn('DefaultDependencies=no', (units / 'sshdgenkeys.service').read_text())
        self.assertFalse((units / 'sshd.socket').exists())
        self.assertFalse((units / 'sshd@.service').exists())
        self.assertEqual((self.root / 'etc/systemd/system-generators/systemd-ssh-generator').readlink(), Path('/dev/null'))
        drop = self.root / 'etc/systemd/system/sshd.service.d/10-perseus-keys.conf'
        self.assertEqual(drop.read_bytes(), (FILES / 'perseus-sshd-keys.conf').read_bytes())
        self.assertIn('ExecStartPre=/usr/sbin/sshd -t $SSHD_OPTS', drop.read_text())
        self.assertIn('ExecStart=/usr/sbin/sshd -D $SSHD_OPTS', drop.read_text())

    def test_injected_cycle_is_rejected_with_real_dependencies(self):
        install_policy(self.root, replay_recipe=True)
        drop = self.root / 'etc/systemd/system/sshd.service.d/injected-cycle.conf'
        drop.write_text('[Unit]\nBefore=basic.target\n')
        result = verify(self.root, 'sshd.service')
        self.assertIn('Found ordering cycle', result.stderr)
        with self.assertRaises(AssertionError): self.assertAccepted(result)

    def test_actual_recipe_install_preserves_dom0_identity_keys_and_password(self):
        protected = {}
        for name, value in [('etc/machine-id', b'existing-machine-id\n'),
                            ('etc/shadow', b'existing-password-placeholder\n'),
                            ('etc/ssh/ssh_host_ed25519_key', b'isolated-test-key-placeholder')]:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value); protected[name] = (path.read_bytes(), path.stat().st_ino)
        install_policy(self.root, replay_recipe=True)
        for name, expected in protected.items():
            path = self.root / name
            self.assertEqual((path.read_bytes(), path.stat().st_ino), expected)
        self.assertAccepted(verify(self.root, 'sshd.service'))
