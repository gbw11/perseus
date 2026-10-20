"""R4 신원 오류 분류와 R5 복원 용량 경계. 작은 실제 파일/JSON과 sparse fixture를 쓴다."""
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_xenova_management as fixtures
import xenova_management as management
import xenova_management_cli as cli
import xenova_vm as vm

GIB = 1024 ** 3
MIB = 1024 ** 2


class ReviewFixture(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManagementTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def assert_error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], code, result)

    def controls(self):
        return [a for a in self.f.xen.calls if a[:2] in
                (['xl', 'shutdown'], ['xl', 'reboot'], ['xl', 'destroy'], ['xl', 'create'])]


class TargetIdentityCodeTest(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.identity = self.f.create()
        self.directory = self.f.data / f'instances/{self.f.key}'
        self.record = json.loads((self.directory / 'instance.json').read_text())

    def mismatched_live_domain(self):
        row = fixtures.domain(self.record, self.directory / 'root.raw', 5)
        row['config']['c_info']['uuid'] = 'bbbb0000-1111-4222-8333-123456789abc'
        self.f.xen.rows = [row]

    def test_uuid_mismatch_is_target_changed_for_control_and_read_calls(self):
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        for action in ('start', 'stop', 'reboot', 'get', 'list'):
            with self.subTest(action=action):
                self.mismatched_live_domain()
                spec = {} if action == 'list' else {'vm_id': self.identity}
                with patch.object(self.f.p, 'check_xen'), patch.object(self.f.p, 'check_network'):
                    result = self.f.m.call(action, spec)
                self.assert_error(result, 'TARGET_CHANGED')
                self.assertEqual(management.exit_code(result), 3)
                self.assertEqual(self.controls(), [])
                self.assertEqual({p.name: p.read_bytes() for p in self.directory.iterdir()}, before)

    def test_cli_stop_uuid_mismatch_returns_one_target_changed_json(self):
        self.mismatched_live_domain()
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            cli.main(['stop', '--vm-id', self.identity, '--json'], manager=self.f.m)
        self.assertEqual(caught.exception.code, 3)
        result = json.loads(output.getvalue())
        self.assert_error(result, 'TARGET_CHANGED')
        self.assertEqual(self.controls(), [])

    def test_live_name_mismatch_is_target_changed_without_control(self):
        row = fixtures.domain(self.record, self.directory / 'root.raw', 5)
        row['config']['c_info']['name'] = 'replacement'
        self.f.xen.rows = [row]
        result = self.f.m.call('stop', {'vm_id': self.identity})
        self.assert_error(result, 'TARGET_CHANGED')
        self.assertEqual(self.controls(), [])

    def test_replacement_during_control_recheck_retains_typed_error(self):
        self.f.xen.rows = [fixtures.domain(self.record, self.directory / 'root.raw', 5)]
        calls = [0]
        def command(args, **kwargs):
            if args == ['xl', 'list', '-l']:
                calls[0] += 1
                if calls[0] == 2:
                    self.mismatched_live_domain()
            return self.f.xen(args, **kwargs)
        self.f.p.command = command
        result = self.f.m.call('stop', {'vm_id': self.identity})
        self.assert_error(result, 'TARGET_CHANGED')
        self.assertEqual(self.controls(), [])
        self.assertEqual(calls[0], 2)

    def test_xen_query_failure_is_not_misclassified_as_identity_change(self):
        def command(args, **kwargs):
            if args[:2] == ['xl', 'list']:
                raise vm.PlatformError('query command failed')
            return self.f.xen(args, **kwargs)
        self.f.p.command = command
        result = self.f.m.call('stop', {'vm_id': self.identity})
        self.assert_error(result, 'OBSERVATION_UNAVAILABLE')
        self.assertIsNone(result['data'])
        self.assertEqual(self.controls(), [])

    def test_all_common_actions_reject_non_object_input_before_external_work(self):
        for action in ('templates', 'get', 'list', 'operation', 'start', 'stop', 'reboot',
                       'autostart-set', 'board-shutdown-set', 'config', 'create', 'retire', 'restore', 'purge'):
            with self.subTest(action=action):
                result = self.f.m.call(action, ['invalid'], execute=True, idempotency_key='invalid')
                self.assert_error(result, 'INVALID_SPEC')
        self.assertEqual(self.controls(), [])


class RestoreSpaceTest(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.identity = self.f.create()
        disk = self.f.data / f'instances/{self.f.key}/root.raw'
        with disk.open('r+b') as stream:
            stream.truncate(8 * GIB)
        self.assertLess(disk.stat().st_blocks * 512, MIB)
        result = self.f.call('retire', {'vm_id': self.identity})
        self.assertTrue(result['ok'], result)
        self.tomb_path = self.f.data / 'tombstones' / (self.identity + '.json')
        self.tomb = json.loads(self.tomb_path.read_text())
        self.archive = self.f.data / self.tomb['archive']
        self.disk = self.archive / 'root.raw'
        self.original_identity = (self.archive / 'instance.json').read_bytes()
        self.original_inode = self.disk.stat().st_ino

    def free(self, value):
        return patch.object(vm.os, 'statvfs', return_value=SimpleNamespace(f_bavail=value, f_frsize=1))

    def restore(self, execute=True):
        return self.f.call('restore', {'vm_id': self.identity}, execute=execute)

    def assert_archive_preserved(self):
        self.assertTrue(self.archive.is_dir())
        self.assertEqual((self.archive / 'instance.json').read_bytes(), self.original_identity)
        self.assertEqual(self.disk.stat().st_ino, self.original_inode)
        with self.disk.open('rb') as stream:
            stream.seek(1080)
            self.assertEqual(stream.read(2), b'\x53\xef')
        self.assertFalse((self.f.data / f'instances/{self.f.key}').exists())
        self.assertEqual(self.f.copies, 1)

    def interrupt_restore(self, phase):
        def checkpoint(current, op):
            if op['action'] == 'restore' and current == phase:
                raise fixtures.Interrupted()
        self.f.m.checkpoint = checkpoint
        with self.assertRaises(fixtures.Interrupted):
            self.restore()
        self.f.m.checkpoint = lambda *args: None
        return next(o for o in self.f.m._operations() if o['action'] == 'restore')

    def test_plan_rejects_sparse_growth_without_recording_operation(self):
        before = sorted(p.name for p in (self.f.data / 'operations').iterdir())
        with self.free(3 * GIB):
            result = self.restore(execute=False)
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertEqual(sorted(p.name for p in (self.f.data / 'operations').iterdir()), before)
        self.assert_archive_preserved()

    def test_execute_rejects_sparse_growth_and_preserves_uuid_policy_and_disk(self):
        before = self.tomb_path.read_bytes()
        with self.free(3 * GIB):
            result = self.restore()
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertEqual(self.tomb_path.read_bytes(), before)
        self.assert_archive_preserved()

    def test_exact_growth_budget_does_not_charge_existing_archive_blocks_twice(self):
        growth = self.disk.stat().st_size - self.disk.stat().st_blocks * 512
        with self.free(2 * GIB + growth):
            result = self.restore()
            self.assertTrue(result['ok'], result)
            self.f.p.reserve_space(0, self.f.p.records(validate_assets=False))
        self.assertEqual(result['data']['vm_id'], self.identity)
        target = self.f.data / f'instances/{self.f.key}'
        self.assertEqual((target / 'instance.json').read_bytes(), self.original_identity)
        self.assertEqual((target / 'root.raw').stat().st_ino, self.original_inode)

    def test_existing_registered_disk_growth_is_included(self):
        other = fixtures.record('debian-dev')
        directory = fixtures.fixture(self.f.data, other)
        with (directory / 'root.raw').open('r+b') as stream:
            stream.truncate(4 * GIB)
        with self.free(12 * GIB):
            result = self.restore()
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assert_archive_preserved()

    def test_fully_allocated_archive_needs_only_existing_two_gib_reserve(self):
        content = bytearray(4 * MIB)
        content[1080:1082] = b'\x53\xef'
        self.disk.write_bytes(content)
        self.assertEqual(self.disk.stat().st_size, self.disk.stat().st_blocks * 512)
        with self.free(2 * GIB):
            result = self.restore()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['vm_id'], self.identity)
        self.assertEqual(self.f.copies, 1)

    def test_recorded_restore_retry_checks_space_then_recovers_same_operation(self):
        op = self.interrupt_restore('recorded')
        with self.free(3 * GIB):
            result = self.restore()
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertEqual(result['operation_id'], op['operation_id'])
        self.assert_archive_preserved()
        with self.free(11 * GIB):
            result = self.restore()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['replayed'])
        self.assertEqual(result['operation_id'], op['operation_id'])
        self.assertEqual(result['data']['vm_id'], self.identity)

    def test_recorded_restore_retry_plan_checks_space_without_changing_journal(self):
        op = self.interrupt_restore('recorded')
        path = self.f.data / 'operations' / (op['operation_id'] + '.json')
        before = path.read_bytes()
        with self.free(3 * GIB):
            result = self.restore(execute=False)
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertEqual(path.read_bytes(), before)
        self.assert_archive_preserved()

    def test_after_move_retry_checks_one_restored_disk_and_never_rolls_back_assets(self):
        op = self.interrupt_restore('moved')
        target = self.f.data / f'instances/{self.f.key}'
        self.assertTrue(target.is_dir())
        with self.free(3 * GIB):
            result = self.restore()
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertTrue(target.is_dir())
        self.assertEqual((target / 'root.raw').stat().st_ino, self.original_inode)
        self.assertEqual((target / 'instance.json').read_bytes(), self.original_identity)
        with self.free(11 * GIB):
            result = self.restore()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['operation_id'], op['operation_id'])
        self.assertEqual(self.f.copies, 1)

    def test_successful_old_restore_replay_is_read_only_even_if_free_space_later_falls(self):
        with self.free(11 * GIB):
            first = self.restore()
        self.assertTrue(first['ok'], first)
        with self.free(0):
            replay = self.restore()
        self.assertTrue(replay['ok'], replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_id'], first['operation_id'])

    def pending_creation(self, *, allocated=0):
        # 4MiB의 작은 template fixture로 별도 미완료 작업의 약정 공간을 만든다.
        source = self.f.data / 'templates/source'
        root = source / 'root.raw'
        root.chmod(0o644)
        with root.open('r+b') as stream:
            stream.truncate(4 * MIB)
        root.chmod(0o444)
        meta_path = source / 'template.json'
        meta = json.loads(meta_path.read_text())
        meta['root_sha256'] = fixtures.catalog.sha(root)
        meta['files']['root.raw'] = {'sha256': fixtures.catalog.sha(root), 'bytes': root.stat().st_size}
        meta_path.chmod(0o644)
        meta_path.write_text(json.dumps(meta))
        meta_path.chmod(0o444)
        descriptor = self.f.data / 'catalog/ubuntu-24.04-arm64/1.0.0.json'
        self.f.descriptor['manifest_sha256'] = fixtures.catalog.sha(meta_path)
        descriptor.write_text(json.dumps(self.f.descriptor))
        self.f.m.checkpoint = lambda phase, op: (_ for _ in ()).throw(fixtures.Interrupted()) if phase == 'recorded' else None
        with self.assertRaises(fixtures.Interrupted):
            self.f.call('create', {**self.f.spec, 'vm_id':'950e8400-e29b-41d4-a716-446655440000','name': 'pending', 'ipv4': '192.168.77.31'}, key='pending-create')
        self.f.m.checkpoint = lambda *args: None
        op = next(o for o in self.f.m._operations() if o['idempotency_key'] == 'create:950e8400-e29b-41d4-a716-446655440000')
        if allocated:
            stage = self.f.data / op['owned_workspace'] / 'vm-950e8400e29b41d4a716446655440000'
            stage.mkdir(parents=True)
            with (stage / 'root.raw').open('wb') as stream:
                stream.write(bytes(allocated))
                stream.truncate(4 * MIB)
        return op

    def small_restore_disk(self):
        with self.disk.open('r+b') as stream:
            stream.truncate(2 * MIB)

    def test_pending_recorded_creation_keeps_its_growth_budget_during_restore(self):
        self.small_restore_disk()
        op = self.pending_creation()
        path = self.f.data / 'operations' / (op['operation_id'] + '.json')
        before = path.read_bytes()
        with self.free(2 * GIB + 4 * MIB):
            result = self.restore()
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertEqual(path.read_bytes(), before)
        self.assert_archive_preserved()

    def test_partly_copied_creation_keeps_unallocated_budget_without_double_charge(self):
        self.small_restore_disk()
        op = self.pending_creation(allocated=MIB)
        stage = self.f.data / op['owned_workspace'] / 'vm-950e8400e29b41d4a716446655440000/root.raw'
        before = (stage.stat().st_ino, stage.stat().st_size, stage.stat().st_blocks)
        with self.free(2 * GIB + 4 * MIB):
            result = self.restore(execute=False)
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertEqual((stage.stat().st_ino, stage.stat().st_size, stage.stat().st_blocks), before)
        with self.free(2 * GIB + 6 * MIB):
            result = self.restore()
        self.assertTrue(result['ok'], result)
        self.assertTrue(stage.exists())
        self.assertEqual(self.f.copies, 1)


    def test_pending_fully_allocated_blocks_are_not_reserved_twice(self):
        self.small_restore_disk()
        op = self.pending_creation(allocated=4 * MIB)
        stage = self.f.data / op['owned_workspace'] / 'vm-950e8400e29b41d4a716446655440000/root.raw'
        self.assertEqual(stage.stat().st_size, stage.stat().st_blocks * 512)
        with self.free(2 * GIB + 3 * MIB):
            result = self.restore()
        self.assertTrue(result['ok'], result)
        self.assertTrue(stage.exists())

    def test_new_create_also_respects_pending_creation_commitments(self):
        self.small_restore_disk()
        self.pending_creation(allocated=MIB)
        with self.free(2 * GIB + 6 * MIB):
            result = self.f.call('create', {**self.f.spec, 'vm_id':'a50e8400-e29b-41d4-a716-446655440000','name': 'another', 'ipv4': '192.168.77.32'}, key='another')
        self.assert_error(result, 'INSUFFICIENT_STORAGE')
        self.assertFalse((self.f.data / 'instances/vm-a50e8400e29b41d4a716446655440000').exists())
        self.assert_archive_preserved()

    def test_published_incomplete_creation_is_counted_once_as_registered_disk(self):
        self.small_restore_disk()
        self.pending_creation()
        def checkpoint(phase, op):
            if phase == 'published' and op['action'] == 'create':
                disk = self.f.data / 'instances/vm-950e8400e29b41d4a716446655440000/root.raw'
                with disk.open('r+b') as stream:
                    stream.truncate(4 * MIB)
                raise fixtures.Interrupted()
        self.f.m.checkpoint = checkpoint
        spec = {**self.f.spec, 'vm_id':'950e8400-e29b-41d4-a716-446655440000','name': 'pending', 'ipv4': '192.168.77.31'}
        with self.assertRaises(fixtures.Interrupted):
            self.f.call('create', spec, key='pending-create')
        self.f.m.checkpoint = lambda *args: None
        disk = self.f.data / 'instances/vm-950e8400e29b41d4a716446655440000/root.raw'
        before = disk.stat().st_ino
        with self.free(2 * GIB + 7 * MIB):
            result = self.restore()
        self.assertTrue(result['ok'], result)
        self.assertEqual(disk.stat().st_ino, before)
