"""관리 경계 회귀. 실제 JSON·flock·동시 호출을 쓰며 Xen/대용량 준비만 기존 fixture다."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_xenova_management as fixtures
from first_install_fixture import Board, FILES
import xenova_management as management
import xenova_management_cli as cli
import xenova_vm as vm


class ManagementBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManagementTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def snapshot(self):
        return {str(p.relative_to(self.f.data)): p.read_bytes()
                for p in self.f.data.rglob('*') if p.is_file()}

    def interrupt(self, action, spec, phase='recorded'):
        def checkpoint(current, op):
            if current == phase:
                raise fixtures.Interrupted()
        self.f.m.checkpoint = checkpoint
        with self.assertRaises(fixtures.Interrupted):
            self.f.call(action, spec)
        self.f.m.checkpoint = lambda *args: None

    def shutdown(self):
        self.f.run.mkdir(exist_ok=True)
        (self.f.run / 'shutdown-requested').write_text('reboot')

    def test_shutdown_blocks_recorded_create_resume_without_changing_journal(self):
        self.interrupt('create', self.f.spec)
        before = self.snapshot()
        self.shutdown()
        result = self.f.call('create', self.f.spec)
        self.assertEqual(result['error']['code'], 'OPERATION_IN_PROGRESS', result)
        self.assertEqual(self.f.copies, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.f.call('operation', {'idempotency_key': 'create:'+self.f.spec['vm_id']})['ok'])
        self.assertTrue(self.f.call('create', self.f.spec, execute=False)['ok'])

    def test_shutdown_blocks_published_create_recovery_without_reinitializing_assets(self):
        self.interrupt('create', self.f.spec, phase='published')
        before = self.snapshot()
        self.shutdown()
        result = self.f.call('create', self.f.spec)
        self.assertEqual(result['error']['code'], 'OPERATION_IN_PROGRESS', result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.f.copies, 1)

    def test_cli_json_setting_shutdown_error_preserves_existing_record(self):
        identity = self.f.create()
        before = self.snapshot()
        self.shutdown()
        output = io.StringIO()
        with patch.object(vm, 'Platform', return_value=self.f.p), redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            vm.main(['autostart-set', self.f.key, 'on', '--json'])
        self.assertEqual(caught.exception.code, 3)
        result = json.loads(output.getvalue())
        self.assertEqual(result['error']['code'], 'OPERATION_IN_PROGRESS', result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.f.state(identity)['configuration']['autostart'], False)

    def test_empty_atomic_temp_is_preserved_but_linked_temp_and_unknown_file_are_rejected(self):
        identity = self.f.create()
        root = self.f.data / 'tombstones'
        path = root / (identity + '.json.tmp-123-0123456789ab')
        path.touch()
        self.assertFalse(vm.retired_instance(self.f.data, self.f.key, identity))
        self.assertTrue(self.f.call('list', {})['ok'])
        self.assertEqual(path.read_bytes(), b'')
        outside = self.f.data / 'outside-record'
        outside.write_text('keep outside')
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(vm.PlatformError):
            vm.retired_instance(self.f.data, self.f.key, identity)
        self.assertFalse(self.f.call('list', {})['ok'])
        self.assertEqual(outside.read_text(), 'keep outside')
        path.unlink()
        unknown = root / 'unowned-record'
        unknown.write_text('{}')
        with self.assertRaises(vm.PlatformError):
            vm.retired_instance(self.f.data, self.f.key, identity)
        self.assertFalse(self.f.call('list', {})['ok'])
        self.assertEqual(unknown.read_text(), '{}')

    def transition_shutdown(self, action):
        identity = self.f.create()
        if action in ('restore', 'purge'):
            self.assertTrue(self.f.call('retire', {'vm_id': identity})['ok'])
        spec = {'vm_id': identity}
        if action == 'purge':
            spec['confirm_vm_id'] = identity
        self.interrupt(action, spec)
        before = self.snapshot()
        self.shutdown()
        result = self.f.call(action, spec)
        self.assertEqual(result['error']['code'], 'OPERATION_IN_PROGRESS', result)
        self.assertEqual(self.snapshot(), before)

    def test_shutdown_blocks_retire_resume(self):
        self.transition_shutdown('retire')

    def test_shutdown_blocks_restore_resume(self):
        self.transition_shutdown('restore')

    def test_shutdown_blocks_purge_resume(self):
        self.transition_shutdown('purge')

    def test_shutdown_blocks_json_policy_and_config_writes(self):
        identity = self.f.create()
        before = self.snapshot()
        self.shutdown()
        for action, value in [('autostart-set', 'on'), ('board-shutdown-set', 'graceful'), ('config', None)]:
            with self.subTest(action=action):
                spec = {'vm_id': identity}
                if value is not None:
                    spec['value'] = value
                result = self.f.call(action, spec)
                self.assertEqual(result['error']['code'], 'OPERATION_IN_PROGRESS', result)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.f.run / (self.f.key+'.cfg')).exists())

    def test_shutdown_allows_completed_replay_reads_and_plans_without_writes(self):
        identity = self.f.create()
        before = self.snapshot()
        self.shutdown()
        result = self.f.call('create', self.f.spec)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['replayed'])
        self.assertEqual(result['data']['vm_id'], identity)
        for action, spec in [('get', {'vm_id': identity}), ('list', {}),
                             ('operation', {'idempotency_key': 'create:'+self.f.spec['vm_id']})]:
            self.assertTrue(self.f.call(action, spec)['ok'])
        self.assertTrue(self.f.call('create', {**self.f.spec, 'vm_id':'750e8400-e29b-41d4-a716-446655440000','name': 'planned', 'ipv4': '192.168.77.31'},
                                    key='planned', execute=False)['ok'])
        result = self.f.call('create', {**self.f.spec, 'vm_id':'850e8400-e29b-41d4-a716-446655440000','name': 'blocked', 'ipv4': '192.168.77.32'}, key='blocked')
        self.assertEqual(result['error']['code'], 'OPERATION_IN_PROGRESS')
        self.assertEqual(self.snapshot(), before)

    def test_tombstone_temp_is_ignored_by_get_list_and_retired_lookup(self):
        identity = self.f.create()
        root = self.f.data / 'tombstones'
        temp = root / (identity + '.json.tmp-123-0123456789ab')
        temp.write_text('{"incomplete":')
        self.assertTrue(self.f.call('get', {'vm_id': identity})['ok'])
        self.assertTrue(self.f.call('list', {})['ok'])
        self.assertFalse(vm.retired_instance(self.f.data, 'ubuntu-dev'))
        self.assertFalse(vm.retired_instance(self.f.data, self.f.key, identity))
        self.assertTrue(self.f.call('retire', {'vm_id': identity})['ok'])
        self.assertTrue(vm.retired_instance(self.f.data, self.f.key, identity))
        self.assertEqual(temp.read_text(), '{"incomplete":')

    def test_corrupt_formal_tombstones_are_not_ignored(self):
        identity = self.f.create()
        path = self.f.data / 'tombstones' / (identity + '.json')
        original = json.loads(path.read_text())
        for value in ['[]', '{', json.dumps({**original, 'vm_id': 'wrong'}),
                      json.dumps({**original, 'lifecycle': 'invalid'})]:
            with self.subTest(value=value):
                path.write_text(value)
                self.assertFalse(self.f.call('list', {})['ok'])
                with self.assertRaises((vm.PlatformError, ValueError)):
                    vm.retired_instance(self.f.data, 'unrelated')
        path.write_text(json.dumps(original))

    def concurrent_calls(self, *, resume=False, fail_after_wait=False):
        if resume:
            self.interrupt('create', self.f.spec)
        entered, release = threading.Event(), threading.Event()
        result, errors = [], []
        def checkpoint(phase, op):
            expected = 'preparing' if resume else 'recorded'
            if phase == expected and op['status'] == 'RUNNING' and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('test synchronization timed out')
                if fail_after_wait:
                    raise OSError('injected preparation failure')
        self.f.m.checkpoint = checkpoint
        def first():
            try:
                result.append(self.f.call('create', self.f.spec))
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=first)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            rejected = self.f.m.call('list', {}, execute=True)
            self.assertEqual(rejected['error']['code'], 'OPERATION_IN_PROGRESS', rejected)
            self.assertIsNone(rejected['operation_id'])
            self.assertFalse(rejected['replayed'])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        op = json.loads(next((self.f.data / 'operations').glob('*.json')).read_text())
        self.assertEqual(result[0]['operation_id'], op['operation_id'], result[0])
        self.assertEqual(result[0]['replayed'], resume)
        if fail_after_wait:
            self.assertFalse(result[0]['ok'])
            self.assertEqual(op['status'], 'RECOVERY_REQUIRED')
            self.assertEqual(op['error'], result[0]['error'])
        else:
            self.assertTrue(result[0]['ok'], result[0])
            self.assertEqual(op['status'], 'SUCCEEDED')
            self.assertIsNone(op['error'])

    def test_same_manager_concurrent_rejection_preserves_success_context(self):
        self.concurrent_calls()

    def test_same_manager_concurrent_rejection_preserves_replay_context(self):
        self.concurrent_calls(resume=True)

    def test_same_manager_concurrent_rejection_preserves_failure_record(self):
        self.concurrent_calls(fail_after_wait=True)

    def test_query_types_fields_and_selectors_are_invalid_spec(self):
        for action in ('templates', 'get', 'list', 'operation'):
            for spec in (['not-an-object'], [], '', False, 1, {'unexpected': True}):
                with self.subTest(action=action, spec=spec):
                    result = self.f.m.call(action, spec)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(result['error']['code'], 'INVALID_SPEC', result)
        for spec in ({}, {'operation_id': 'wrong'}, {'idempotency_key': []},
                     {'idempotency_key': ''}, {'operation_id': 'a'*36, 'idempotency_key': 'both'}):
            result = self.f.m.call('operation', spec)
            self.assertEqual(result['error']['code'], 'INVALID_SPEC', result)
        for action, spec in [('get', {'vm_id': []}), ('list', {'lifecycle': []})]:
            self.assertEqual(self.f.m.call(action, spec)['error']['code'], 'INVALID_SPEC')
        for request_id in ([], {}, '', 'bad\nrequest', 3):
            result = self.f.m.call('list', {}, request_id=request_id)
            self.assertEqual(result['error']['code'], 'INVALID_SPEC', result)
            self.assertIsInstance(result['request_id'], str)
        for action in ([], {}, None, 1):
            self.assertEqual(self.f.m.call(action, {})['error']['code'], 'INVALID_SPEC')

    def test_valid_operation_selectors_and_request_id_are_preserved(self):
        created = self.f.call('create', self.f.spec)
        for spec in ({'operation_id': created['operation_id']}, {'idempotency_key': 'create:'+self.f.spec['vm_id']},
                     {'operation_id': created['operation_id'], 'idempotency_key': None}):
            result = self.f.m.call('operation', spec, request_id='backend-request-1')
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['request_id'], 'backend-request-1')

    def test_internal_attribute_error_is_not_disguised_as_invalid_input(self):
        self.f.create()
        with patch.object(self.f.m, '_view', side_effect=AttributeError('internal bug')):
            with self.assertRaises(AttributeError):
                self.f.m.call('list', {})

    def test_cli_operation_selector_errors_are_single_json(self):
        for args in (['operation'], ['operation', '--operation-id', 'invalid'],
                     ['operation', '--operation-id', 'a'*36, '--idempotency-key', 'both']):
            output = io.StringIO()
            with self.subTest(args=args), redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                cli.main([*args, '--json'], manager=self.f.m)
            self.assertEqual(caught.exception.code, 2)
            value = json.loads(output.getvalue())
            self.assertEqual(value['error']['code'], 'INVALID_SPEC', value)


class TombstoneBootConsumerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Board(Path(self.tmp.name))
        self.b.grow_mount()
        self.b.factory()
        self.b.p.register()
        source = self.b.data / 'instances/ubuntu-dev'
        self.record = json.loads((source / 'instance.json').read_text())
        source.rename(source.with_name('.retired-ubuntu-dev-' + self.record['uuid'] + '-g1'))
        root = self.b.data / 'tombstones'
        root.mkdir()
        tomb = {'schema_version': '1.0', 'vm_id': self.record['uuid'], 'name': 'ubuntu-dev',
                'record': self.record, 'generation': 1, 'lifecycle': 'RETIRED', 'history': []}
        (root / (self.record['uuid'] + '.json')).write_text(json.dumps(tomb))
        self.temp = root / (self.record['uuid'] + '.json.tmp-123-0123456789ab')
        self.temp.write_text('{')
        self.before = {str(p.relative_to(self.b.data)): p.read_bytes()
                       for p in self.b.data.rglob('*') if p.is_file()}

    def test_first_boot_rerun_preserves_retired_vm_and_ignores_temp(self):
        self.b.p.register()
        self.assertFalse((self.b.data / 'instances/ubuntu-dev').exists())
        self.assertEqual(self.temp.read_text(), '{')
        self.assertEqual({str(p.relative_to(self.b.data)): p.read_bytes()
                          for p in self.b.data.rglob('*') if p.is_file()}, self.before)

    def test_autostart_main_records_and_retired_filter_ignore_temp(self):
        xen = fixtures.FakeXen(self.b.data, {})
        platform = vm.Platform(self.b.data, Path(self.tmp.name) / 'autostart-run', command=xen)
        original_path = Path
        def mapped(path):
            return FILES / 'defaults.json' if str(path) == '/etc/xenova/defaults.json' else original_path(path)
        with patch.object(vm, 'Platform', return_value=platform), patch.object(vm, 'Path', side_effect=mapped), \
             patch.object(platform, 'check_xen'), patch.object(platform, 'check_network'), \
             patch.object(vm, 'storage_check'), redirect_stdout(io.StringIO()):
            xen.records = platform.records(validate_assets=False)
            vm.main(['autostart'])
        creates = [args for args in xen.calls if args[:2] == ['xl', 'create']]
        self.assertEqual(len(creates), 1)
        self.assertEqual(Path(creates[0][2]).stem, 'debian-dev')
        self.assertFalse((self.b.data / 'instances/ubuntu-dev').exists())
        self.assertEqual(self.temp.read_text(), '{')
