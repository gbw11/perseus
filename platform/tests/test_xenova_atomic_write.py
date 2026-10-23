"""원자 쓰기의 공개/비밀 권한과 DHCP 게시 실패 경계를 실제 파일로 확인한다."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import vm
from test_xenova_platform import record
import xenova_runtime as runtime


@contextmanager
def private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


class AtomicModeTest(unittest.TestCase):
    def test_explicit_public_mode_is_honored_under_umask_077(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'public'
            with private_umask():
                runtime.atomic_write(path, 'reservation\n', 0o644)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_default_private_mode_replaces_public_file_with_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'private'
            path.write_text('old\n')
            path.chmod(0o644)
            with private_umask():
                runtime.atomic_write(path, 'private fixture\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_chmod_failure_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'dhcp-hosts'
            path.write_text('original\n')
            path.chmod(0o600)
            with patch.object(runtime.os, 'fchmod', side_effect=OSError('injected chmod failure')):
                with self.assertRaises(OSError):
                    runtime.atomic_write(path, 'replacement\n', 0o644)
            self.assertEqual(path.read_text(), 'original\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def dhcp_platform(self, root):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            return ''
        return vm.Platform(root, root/'run', command), calls

    def test_prepare_dhcp_publishes_readable_registered_reservation_under_umask_077(self):
        with tempfile.TemporaryDirectory() as directory:
            p, calls = self.dhcp_platform(Path(directory))
            r = record()
            with private_umask(), patch.object(p, 'check_network'):
                p.prepare_dhcp({r['id']: r})
            path = p.runtime/'dhcp-hosts'
            self.assertEqual(path.read_text(),
                             f"{r['mac']},{r['ipv4']},{r['hostname']},infinite\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertEqual(calls[-1], ['systemctl', 'reload', 'xenova-dhcp.service'])

    def test_prepare_dhcp_chmod_failure_does_not_reload_or_replace_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            p, calls = self.dhcp_platform(Path(directory))
            runtime.ensure_runtime(p.runtime)
            path = p.runtime/'dhcp-hosts'
            original = '02:11:22:33:44:55,192.168.77.80,unrelated,infinite\n'
            runtime.atomic_write(path, original, 0o644)
            r = record()
            with patch.object(p, 'check_network'), \
                 patch.object(runtime.os, 'fchmod', side_effect=OSError('injected chmod failure')):
                with self.assertRaises(OSError):
                    p.prepare_dhcp({r['id']: r})
            self.assertEqual(path.read_text(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertNotIn(['systemctl', 'reload', 'xenova-dhcp.service'], calls)
