"""실제 계정 작성자의 기존 비밀 보존·부분 게시·프로세스 재시작 경계 회귀."""
import errno
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'platform/deploy/mosquitto/scripts/xenova_broker.py'
spec = importlib.util.spec_from_file_location('broker_accounts', MODULE)
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)
A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'
REAL_RUN = subprocess.run


def fixture_hash(argv, **kwargs):
    assert argv[1:4] == ['-H', 'sha512-pbkdf2', '-U']
    assert '-b' not in argv
    path = Path(argv[-1])
    lines = [line.partition(':') for line in path.read_text().splitlines()]
    path.write_text(''.join(name + ':$7$fixture$' + hashlib.sha256(value.encode()).hexdigest() + '\n'
                            for name, _, value in lines))
    return type('Result', (), {'returncode': 0})()


class Accounts(unittest.TestCase):
    def test_display_export_reuses_only_observer_without_mutating_broker(self):
        self.run_change()
        value = self.load()
        before = {str(p): p.read_bytes() for p in value['path'].rglob('*') if p.is_file()}
        folder = self.data / 'display'
        folder.mkdir(mode=0o700)
        output = folder / 'mqtt.env'
        plan = broker.export(value, 'observer', 'display-env', output)
        self.assertTrue(plan['execute_required'])
        self.assertFalse(output.exists())
        broker.export(value, 'observer', 'display-env', output, execute=True)
        self.assertIn('XENOVA_DISPLAY_MQTT_PASSWORD=' + value['secrets']['observer'], output.read_text())
        self.assertNotIn('MQTT_USERNAME=backend', output.read_text())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, {str(p): p.read_bytes() for p in value['path'].rglob('*') if p.is_file()})
        with self.assertRaises(broker.SetupError):
            broker.export(value, 'backend', 'display-env', folder / 'wrong.env')
        acl_path = value['path'] / 'acl'
        acl_path.write_text(acl_path.read_text().replace(
            'user observer\ntopic read xenova/v1/#',
            'user observer\ntopic read xenova/v1/#\ntopic write xenova/v1/#'))
        with self.assertRaises(broker.SetupError):
            broker.export(value, 'observer', 'display-env', folder / 'unsafe.env')

    def setUp(self):
        if os.geteuid() != 0:
            self.skipTest('root in an owned Linux fixture is required for ownership assertions')
        self.temp = tempfile.TemporaryDirectory(prefix='xenova-account-test-')
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.target = self.data / 'mqtt'
        self.rules = broker.policy()
        guard = patch.object(broker, 'storage', lambda data=broker.DATA: data / 'mqtt')
        guard.start()
        self.addCleanup(guard.stop)
        hasher = patch.object(broker.subprocess, 'run', side_effect=fixture_hash)
        self.hasher = hasher.start()
        self.addCleanup(hasher.stop)

    def run_change(self, action='initialize', name=None, execute=True, **kwargs):
        return broker.change(action, name, execute=execute, data=self.data, gid=0, rules=self.rules, **kwargs)

    def load(self):
        return broker.load(self.target, 0, self.rules)

    def test_plan_has_no_state_lock_or_hashing(self):
        result = self.run_change(execute=False)
        self.assertTrue(result['execute_required'])
        self.assertEqual(list(self.data.iterdir()), [])
        self.hasher.assert_not_called()
        self.assertNotIn(A, result['accounts'])
        self.assertIn('interaction', result['accounts'])
        self.assertNotIn('message-board', result['accounts'])

    def test_initialization_retry_preserves_all_secret_hash_and_generation_bytes(self):
        self.run_change(vms=[A])
        old = self.load()
        before = {str(p.relative_to(old['path'])): p.read_bytes() for p in old['path'].rglob('*') if p.is_file()}
        repeated = self.run_change(vms=[A])
        self.assertTrue(repeated['reused'])
        self.assertEqual(self.load()['path'], old['path'])
        self.assertEqual(before, {str(p.relative_to(old['path'])): p.read_bytes() for p in old['path'].rglob('*') if p.is_file()})
        self.assertNotIn(next(iter(old['secrets'].values())), json.dumps(repeated))

    def test_add_remove_and_repeated_initialize_never_rotate_unrelated_accounts(self):
        self.run_change(vms=[A])
        original = self.load()
        self.run_change('add-account', B)
        added = self.load()
        for name, value in original['secrets'].items():
            self.assertEqual(added['secrets'][name], value)
            self.assertEqual(added['hashes'][name], original['hashes'][name])
        generation = added['path']
        self.run_change('add-account', B)
        self.assertEqual(self.load()['path'], generation)
        self.run_change('remove-account', A)
        self.assertNotIn(A, self.load()['profiles'])
        self.assertIn('telemetry-' + A, self.load()['profiles'])
        self.run_change('initialize')
        self.assertIn(B, self.load()['profiles'])
        self.assertEqual(self.load()['secrets']['backend'], original['secrets']['backend'])

    def test_explicit_telemetry_account_is_independent(self):
        self.run_change()
        self.run_change('add-account', 'telemetry-' + B)
        value = self.load()
        self.assertNotIn(B, value['profiles'])
        block = broker.grants((value['path'] / 'acl').read_text())['telemetry-' + B]
        self.assertEqual(len(block), 5)
        self.assertTrue(all('xenova/v2/vms/' + B + '/telemetry/' in line for line in block))

    def test_read_only_profiles_cannot_publish(self):
        self.run_change(vms=[A])
        users = broker.grants((self.load()['path'] / 'acl').read_text())
        for name in ('observer', 'telemetry-reference-subscriber'):
            self.assertFalse(any(line.startswith('topic write ') for line in users[name]))
        self.assertFalse(any('/vehicle/control' in line for line in users[A]))

    def test_private_permissions_and_pinned_config(self):
        previous = os.umask(0o077)
        try:
            self.run_change()
        finally:
            os.umask(previous)
        value = self.load()
        for name, mode in [('credentials.json', 0o600), ('manifest.json', 0o600), ('passwd', 0o640), ('acl', 0o640)]:
            self.assertEqual((value['path'] / name).stat().st_mode & 0o777, mode)
        config = (value['path'] / 'config.d/accounts.conf').read_text()
        self.assertNotIn('/current/', config)
        self.assertIn(str(value['path'] / 'passwd'), config)

    def test_explicit_policy_update_preserves_passwords_and_does_not_require_reset(self):
        self.run_change()
        before = self.load()
        changed = deepcopy(self.rules)
        changed['services']['backend']['write'].remove('xenova/v1/probes/#')
        with self.assertRaises(broker.SetupError):
            broker.load(self.target, 0, changed)
        with self.assertRaises(broker.SetupError):
            broker.change('add-account', A, execute=True, data=self.data, gid=0, rules=changed)
        plan = broker.change('initialize', data=self.data, gid=0, rules=changed)
        self.assertTrue(plan['policy_changed'])
        broker.change('initialize', execute=True, data=self.data, gid=0, rules=changed)
        after = broker.load(self.target, 0, changed)
        self.assertGreater(after['revision'], before['revision'])
        self.assertEqual(after['secrets'], before['secrets'])
        self.assertEqual(after['hashes'], before['hashes'])

    def test_hash_failure_is_not_reinterpreted_as_empty_or_reissued(self):
        with patch.object(broker.subprocess, 'run', return_value=type('R', (), {'returncode': 1})()):
            with self.assertRaises(broker.SetupError):
                self.run_change()
        secrets = next(self.target.glob('.pending-*/credentials.json')).read_bytes()
        self.run_change()
        self.assertEqual((self.load()['path']/'credentials.json').read_bytes(), secrets)

    def test_storage_failure_before_publication_preserves_current_and_same_retry_secrets(self):
        self.run_change()
        before = self.load()
        real = broker.storage
        calls = 0

        def storage(data):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError(errno.ENOSPC, 'injected')
            return real(data)
        with patch.object(broker, 'storage', side_effect=storage):
            with self.assertRaises(OSError):
                self.run_change('add-account', A)
        pending = next(self.target.glob('.pending-*/credentials.json'))
        accepted_secret = json.loads(pending.read_text())['accounts'][A]
        self.assertEqual(self.load()['path'], before['path'])
        self.run_change('add-account', A)
        self.assertEqual(self.load()['secrets'][A], accepted_secret)

    def test_post_pointer_fsync_error_retry_keeps_published_generation(self):
        self.run_change()
        real = broker.sync_directory

        def sync(path):
            if path == self.target and (self.target / 'current').is_symlink() and A in self.load()['profiles']:
                raise OSError(errno.EIO, 'injected')
            real(path)
        with patch.object(broker, 'sync_directory', side_effect=sync):
            with self.assertRaises(OSError):
                self.run_change('add-account', A)
        value = self.load()
        self.run_change('add-account', A)
        self.assertEqual(self.load()['path'], value['path'])
        self.assertEqual(self.load()['secrets'], value['secrets'])

    def test_unfinished_other_operation_blocks_mutation_but_current_remains_readable(self):
        self.run_change()
        with patch.object(broker.os, 'rename', side_effect=OSError(errno.EIO, 'injected')):
            with self.assertRaises(OSError):
                self.run_change('add-account', A)
        self.assertNotIn(A, self.load()['profiles'])
        with self.assertRaises(broker.SetupError):
            self.run_change('add-account', B)
        self.run_change('add-account', A)
        self.assertIn(A, self.load()['profiles'])

    def test_missing_current_never_becomes_first_install(self):
        self.run_change()
        (self.target / 'current').rename(self.target / 'preserved-current')
        with self.assertRaises(broker.SetupError):
            self.run_change()

    def test_tampered_hash_acl_and_pointer_are_rejected(self):
        self.run_change()
        value = self.load()
        (value['path'] / 'acl').write_text('topic readwrite #\n')
        with self.assertRaises(broker.SetupError):
            self.load()
        current = self.target / 'current'
        current.rename(self.target / 'preserved-current')
        current.symlink_to('../../outside')
        with self.assertRaises(broker.SetupError):
            self.load()

    def test_other_process_holding_writer_lock_rejects_new_writer(self):
        lock = self.data / '.mqtt-initialize.lock'
        code = 'import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read()'
        process = subprocess.Popen([sys.executable, '-c', code, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertEqual(process.stdout.readline().strip(), 'locked')
        with self.assertRaises(broker.SetupError):
            self.run_change()
        process.communicate('finish', timeout=3)

    def test_process_kill_after_generation_rename_retries_same_prepared_secret(self):
        self.run_change()
        code = '''import importlib.util,sys,os,signal
from pathlib import Path
spec=importlib.util.spec_from_file_location('broker',sys.argv[1]); b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
b.storage=lambda data: data/'mqtt'
real=b.os.rename
def stop(source,target):
 real(source,target);os.kill(os.getpid(),signal.SIGKILL)
b.os.rename=stop
def hashed(argv,**kw):
 p=Path(argv[-1]);p.write_text(''.join(l.split(':')[0]+':$7$fixture$childhash\\n' for l in p.read_text().splitlines()));return type('R',(),{'returncode':0})()
b.subprocess.run=hashed
b.change('add-account',sys.argv[3],execute=True,data=Path(sys.argv[2]),gid=0)
'''
        result = REAL_RUN([sys.executable, '-B', '-c', code, str(MODULE), str(self.data), A], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertNotIn(A, self.load()['profiles'])
        transaction = broker.read_json(self.target / 'transaction.json')
        generation = self.target / 'generations' / transaction['id']
        expected = broker.read_json(generation / 'credentials.json')['accounts'][A]
        self.run_change('add-account', A)
        self.assertEqual(self.load()['secrets'][A], expected)

    def test_private_account_export_is_one_account_only_and_never_changes_guest(self):
        self.run_change(vms=[A])
        folder = self.data / 'private'
        folder.mkdir(mode=0o700)
        output = folder / 'agent.env'
        planned = broker.export(self.load(), A, 'agent-env', output)
        self.assertFalse(output.exists())
        result = broker.export(self.load(), A, 'agent-env', output, execute=True)
        self.assertFalse(result['consumer_configuration_changed'])
        self.assertEqual(len(output.read_text().splitlines()), 2)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.load()['secrets'][A], json.dumps(result) + json.dumps(planned))
        with self.assertRaises(broker.SetupError):
            broker.export(self.load(), A, 'agent-env', output, execute=True)

    def test_bootstrap_export_keeps_v1_v2_credentials_separate(self):
        self.run_change(vms=[A])
        folder = self.data / 'private'
        folder.mkdir(mode=0o700)
        for name, form in [(A, 'drive'), ('telemetry-' + A, 'telemetry')]:
            output = folder / (form + '.json')
            broker.export(self.load(), name, form, output, execute=True)
            self.assertEqual(broker.read_json(output)['username'], name)
            self.assertEqual(broker.read_json(output)['vm_id'], A)
        with self.assertRaises(broker.SetupError):
            broker.export(self.load(), A, 'telemetry', folder / 'wrong.json', execute=True)

    def test_old_candidate_migration_preserves_every_original_secret_hash_and_legacy_account(self):
        broker.mkdir(self.target, 0)
        profiles = broker.default_accounts(self.rules, [A])
        profiles.pop('interaction')
        profiles['message-board'] = 'service'
        secrets = {name: 'SYNTHETIC-' + name for name in profiles}
        broker.write(self.target / 'credentials.json', json.dumps({'schema_version': 1, 'accounts': secrets}))
        broker.write(self.target / 'passwd', ''.join(name + ':$7$fixture$original\n' for name in profiles), 0o640)
        broker.write(self.target / 'acl', broker.acl(profiles, self.rules), 0o640)
        manifest = {'schema_version': 1, 'vm_ids': [A], 'accounts': sorted(profiles),
                    'sha256': {name: broker.sha(self.target / name) for name in ('passwd', 'acl', 'credentials.json')}}
        broker.write(self.target / 'manifest.json', json.dumps(manifest))
        before = {p.name: p.read_bytes() for p in self.target.iterdir()}
        self.run_change()
        value = self.load()
        for name, secret in secrets.items():
            self.assertEqual(value['secrets'][name], secret)
            self.assertEqual(value['hashes'][name], '$7$fixture$original')
        self.assertEqual(before, {name: (self.target / name).read_bytes() for name in before})
        self.assertIn('interaction', value['profiles'])
        self.assertIn('message-board', value['profiles'])

    def test_byungwoo_legacy_import_is_read_only_and_preserves_passwords(self):
        source = self.data / 'legacy'
        source.mkdir(mode=0o700)
        broker.write(source / 'passwd.credentials', 'backend:SYNTHETIC-backend\n' + A + ':SYNTHETIC-vm\n')
        broker.write(source / 'passwd', 'backend:$7$fixture$oldbackend\n' + A + ':$7$fixture$oldvm\n')
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        self.run_change('import-legacy', source=source)
        self.assertEqual(self.load()['secrets']['backend'], 'SYNTHETIC-backend')
        self.assertEqual(self.load()['hashes'][A], '$7$fixture$oldvm')
        self.run_change('import-legacy', source=source)
        self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
        self.hasher.assert_not_called()

    def test_fresh_initialize_does_not_silently_replace_older_etc_accounts(self):
        source = self.data / 'legacy'
        source.mkdir(mode=0o700)
        broker.write(source / 'passwd', 'backend:$7$fixture$original\n')
        with self.assertRaises(broker.SetupError):
            broker.change('initialize', execute=True, data=self.data, gid=0, rules=self.rules, legacy_source=source)
        self.assertFalse(self.target.exists())
        self.hasher.assert_not_called()


if __name__ == '__main__':
    unittest.main()
