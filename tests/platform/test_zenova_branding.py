"""새 패키지 설치 경계와 이전 자산의 명칭·신원 계약을 구분한다."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dom0_ssh_fixture import install_policy, ROOT, FILES, extract, PROVENANCE, FIXTURE
from first_install_fixture import vm, prepare_rootfs
from test_zenova_platform import record


class ZenovaBrandingTest(unittest.TestCase):
    def test_installed_management_modules_import_and_cli_help_without_source_paths(self):
        with tempfile.TemporaryDirectory(prefix='zenova-package-') as temp:
            root = Path(temp)
            install_policy(root, replay_recipe=True)
            library = root / 'usr/lib/zenova'
            env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
            for name in ('zenova_vm', 'zenova_first_boot', 'zenova_runtime', 'zenova_zephyr',
                         'first_install_contract', 'prepare_rootfs', 'prepare_instance',
                         'guest_identity', 'update_guest_ssh'):
                result = subprocess.run([sys.executable, '-B', '-c',
                    'import sys; sys.path.insert(0, sys.argv[1]); __import__(sys.argv[2])',
                    str(library), name], cwd=temp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('zenova_vm.py', 'update_guest_ssh.py'):
                result = subprocess.run([sys.executable, '-B', str(library / name), '--help'],
                                        cwd=temp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for name, module in (('zenova-vm', 'zenova_vm.py'), ('zenova-first-boot', 'zenova_first_boot.py')):
                entry = root / 'usr/sbin' / name
                self.assertTrue(os.access(entry, os.X_OK))
                self.assertIn('/usr/lib/zenova/' + module, entry.read_text())
            self.assertFalse((root / 'usr/lib/perseus').exists())
            self.assertFalse((root / 'usr/sbin/perseus-vm').exists())

    def test_new_guest_units_use_zenova_while_instance_identity_stays_the_same(self):
        with tempfile.TemporaryDirectory(prefix='zenova-guest-layout-') as temp:
            root = Path(temp)
            prepare_rootfs.install_identity_gate(root)
            unit = root / 'usr/lib/systemd/system/zenova-guest-identity.service'
            self.assertIn('/usr/lib/zenova/guest_identity.py', unit.read_text())
            self.assertIn('Requires=zenova-guest-identity.service',
                (root / 'etc/systemd/system/ssh.service.d/10-zenova-identity.conf').read_text())
            self.assertEqual(record()['id'], 'ubuntu-dev')
            self.assertEqual(vm.DATA, Path('/srv/zenova'))
            self.assertEqual(vm.RUN, Path('/run/zenova'))
            self.assertFalse((root / 'usr/lib/systemd/system/perseus-guest-identity.service').exists())

    def test_old_prepared_linux_root_is_refused_without_identity_or_key_changes(self):
        with tempfile.TemporaryDirectory(prefix='zenova-old-template-') as temp:
            root = Path(temp)
            protected = {'etc/perseus/template-prepared': b'1\\n',
                         'etc/machine-id': b'existing-id\\n',
                         'etc/ssh/ssh_host_ed25519_key': b'isolated-key-placeholder'}
            for relative, content in protected.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            before = {name: ((root / name).read_bytes(), (root / name).stat().st_ino) for name in protected}
            for operation in (prepare_rootfs.prepare_instance, prepare_rootfs.prepare_deployment_seed):
                with self.assertRaises(ValueError):
                    operation(root, record())
            self.assertEqual(before, {name: ((root / name).read_bytes(), (root / name).stat().st_ino)
                                      for name in protected})
            self.assertFalse((root / 'etc/zenova/instance-id').exists())

    def test_historical_dom0_fixture_remains_original_until_explicit_test_projection(self):
        archive = FIXTURE / PROVENANCE['archive']
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='zenova-history-fixture-') as temp:
            root = Path(temp)
            extract(root)
            self.assertTrue((root / 'usr/lib/systemd/system/perseus-vms.service').is_file())
            install_policy(root, replay_recipe=True)
            self.assertFalse((root / 'usr/lib/systemd/system/perseus-vms.service').exists())
            unit = root / 'usr/lib/systemd/system/zenova-vms.service'
            self.assertEqual(unit.read_bytes(), (FILES / unit.name).read_bytes())
        self.assertEqual(before, PROVENANCE['sha256'])
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), before)

    def test_starter_catalog_source_matches_actual_new_files_without_version_bump(self):
        directory = ROOT / 'platform/guests/zephyr/starter-app'
        lock = json.loads((directory.parent / 'starter.lock.json').read_text())
        actual = {str(p.relative_to(directory)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                  'bytes': p.stat().st_size} for p in sorted(directory.rglob('*')) if p.is_file()}
        self.assertEqual(lock['app_files'], actual)
        digest = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(lock['app_source_sha256'], digest)
        self.assertEqual((lock['id'], lock['app'], lock['version']),
                         ('zenova-zephyr-starter-1.0.0', 'zenova-starter', '1.0.0'))
        source = (directory / 'src/main.c').read_text()
        self.assertIn('Zenova / Zephyr / Xen', source)
        self.assertNotIn('Perseus / Zephyr / Xen', source)
