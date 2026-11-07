"""실제 binding guard로 clock responder의 생략·경합·실패 경계를 검사한다.

Xen/Store와 ARM UTC 표본만 외부 대역이다. 등록부, intent, 실행 파일,
ReadContext와 binding 판정은 실제 create/start 및 관측 경로를 사용한다.
"""
import errno
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from first_install_fixture import ROOT
import test_xenova_recovery_foundation as foundation
from test_xenova_lifecycle import application_bundle
from test_xenova_zephyr import bundle
import xenova_catalog as catalog
import xenova_drive_binding as binding
from xenova_execution import ExecutionError
import xenova_management as management
import xenova_telemetry_support as support
import xenova_vm as vm


def wire(value):
    return json.dumps(value, separators=(',', ':')).encode()


class ClockStore:
    """Store 경계 대역: transaction write는 성공한 commit에서만 공개한다."""
    def __init__(self):
        self.handle = object()
        self.values = {}
        self.reads = []
        self.ends = []
        self.writes = []
        self.staged = {}
        self.next_tx = 1
        self.before_read = None
        self.on_begin = None
        self.read_error = None
        self.write_ok = True
        self.commit_ok = True
        self.closed = 0
        self.lib = SimpleNamespace(xs_transaction_start=self.begin,
                                   xs_transaction_end=self.end,
                                   xs_write=self.write)

    def begin(self, handle):
        tx = self.next_tx
        self.next_tx += 1
        self.staged[tx] = {}
        if self.on_begin:
            self.on_begin()
        return tx

    def read(self, tx, path):
        self.reads.append((tx, path))
        if self.before_read:
            self.before_read(tx, path)
        if self.read_error and path.endswith(self.read_error):
            raise OSError(errno.EIO, 'synthetic Store read failure')
        return self.staged.get(tx, {}).get(path, self.values.get(path))

    def write(self, handle, tx, path, value, length):
        path = path.decode()
        value = bytes(value[:length])
        self.writes.append((tx, path, value))
        if not self.write_ok:
            return False
        self.staged[tx][path] = value
        return True

    def end(self, handle, tx, abort):
        self.ends.append((tx, abort))
        staged = self.staged.pop(tx, {})
        if not abort and self.commit_ok:
            self.values.update(staged)
        return bool(abort or self.commit_ok)

    def close(self):
        self.closed += 1


class ClockResponderTest(unittest.TestCase):
    def setUp(self):
        self.f = foundation.FoundationTest('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.make_zephyr()
        self.run = self.f.p.execution().run_record(self.record)
        self.context = {key: self.run[key] for key in
                        ('vm_id', 'host_boot_id', 'execution_id', 'domid', 'xen_unique_id')}
        self.parent = f"/local/domain/{self.context['domid']}/data/xenova-telemetry/clock"
        self.request_path = self.parent + '/request'
        self.response_path = self.parent + '/response'
        self.store = ClockStore()
        self.store.values[f"/local/domain/{self.context['domid']}/vm"] = ('/vm/' + self.vm_id).encode()
        self.request = {'nonce': '123', 'execution_id': self.context['execution_id']}
        self.store.values[self.request_path] = wire(self.request)

        spec = importlib.util.spec_from_file_location(
            'clock_responder_bootstrap', ROOT / 'platform/scripts/provision-xenova-telemetry.py')
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'provision_xenova_telemetry': bootstrap}).start()
        patch.object(bootstrap, 'XenStore', return_value=self.store).start()
        patch.object(vm, 'Platform', return_value=self.f.p).start()
        self.sample = patch.object(support, 'clock_sample', return_value={
            'unix_ms': 1767225600001, 'source_error_ms': 0,
            'drift_ppm': 0, 'source': 'dom0_adjtimex'}).start()
        self.observed = patch.object(binding, 'observe', wraps=binding.observe).start()
        self.commands_before = len(self.f.xen.calls)

    def make_zephyr(self):
        source = application_bundle(bundle(self.f.p.data / 'clock-legacy'),
                                    self.f.p.data / 'rtos-templates/clock-firmware')
        manifest = source / 'firmware.json'
        meta = json.loads(manifest.read_text())
        meta.update(id='clock-firmware', kind='zephyr-xen-firmware', version='1.0.0')
        manifest.chmod(0o644)
        manifest.write_text(json.dumps(meta))
        manifest.chmod(0o444)
        descriptor = {'schema_version': '1.0', 'template_id': 'clock-zephyr',
                      'template_version': '1.0.0', 'guest_kind': 'zephyr',
                      'asset_directory': 'rtos-templates/clock-firmware',
                      'source_template_id': 'clock-firmware',
                      'manifest_sha256': catalog.sha(manifest)}
        directory = self.f.p.data / 'catalog/clock-zephyr'
        directory.mkdir()
        (directory / '1.0.0.json').write_text(json.dumps(descriptor))
        manager = management.Manager(self.f.p, defaults_path=self.f.m.defaults_path)
        self.manager = manager
        self.vm_id = '650e8400-e29b-41d4-a716-446655440000'
        self.f.ok(manager.call('create', {'vm_id': self.vm_id, 'name': 'clock-vm',
                    'template_id': 'clock-zephyr', 'template_version': '1.0.0'}, execute=True))
        records = self.f.p.records(validate_assets=False)
        self.record = next(value for value in records.values() if value['uuid'] == self.vm_id)
        self.f.xen.records = records
        self.f.ok(manager.call('start', {'vm_id': self.vm_id}, execute=True))

    def answer(self, **changes):
        return {'host_boot_id': self.context['host_boot_id'],
                'execution_id': self.context['execution_id'],
                'nonce': self.request['nonce'], 'unix_ms': '1767225600001',
                'source_error_ms': 0, 'drift_ppm': 0, 'source': 'dom0_adjtimex', **changes}

    def assert_idle(self):
        self.assertEqual(self.observed.call_count, 0)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.f.xen.calls[self.commands_before:], [])
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.ends, [])

    def assert_aborted(self):
        self.assertTrue(self.store.ends)
        self.assertTrue(self.store.ends[-1][1])
        self.assertNotIn(self.response_path, self.store.values)

    def test_absent_or_empty_request_skips_xen_and_transaction(self):
        for raw in (None, b''):
            with self.subTest(raw=raw):
                self.store.values[self.request_path] = raw
                self.assertEqual(support.clock_once(), 0)
                self.assert_idle()
        self.assertTrue(all(tx == 0 for tx, _ in self.store.reads))

    def test_completed_response_with_zero_uncertainty_skips_xen_and_write(self):
        self.store.values[self.response_path] = wire(self.answer())
        self.assertEqual(support.clock_once(), 0)
        self.assert_idle()

    def test_stopped_zephyr_keeps_the_original_binding_fallback_classification(self):
        with patch.object(vm.os, 'sync'):
            self.f.ok(self.manager.call('stop', {'vm_id': self.vm_id, 'force': True}, execute=True))
        self.assertEqual(support.clock_once(), 0)
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.ends, [])

    def test_absent_domain_skips_response_after_actual_binding_observation(self):
        self.f.xen.rows = [row for row in self.f.xen.rows
                           if row['config']['c_info']['uuid'] != self.vm_id]
        self.assertEqual(support.clock_once(), 0)
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.ends, [])

    def test_old_host_hint_uses_the_original_absence_fallback(self):
        execution = self.f.p.execution()
        run = execution.run_record(self.record)
        run['host_boot_id'] = foundation.HOST_B
        execution.write(self.f.p.runtime / 'executions' / (self.vm_id + '.json'), run)
        self.f.xen.rows = [row for row in self.f.xen.rows
                           if row['config']['c_info']['uuid'] != self.vm_id]
        self.assertEqual(support.clock_once(), 0)
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.ends, [])

    def test_old_host_matching_response_cannot_skip_the_live_binding_guard(self):
        execution = self.f.p.execution()
        run = execution.run_record(self.record)
        run['host_boot_id'] = foundation.HOST_B
        execution.write(self.f.p.runtime / 'executions' / (self.vm_id + '.json'), run)
        self.store.values[self.response_path] = wire(self.answer(host_boot_id=foundation.HOST_B))
        with self.assertRaisesRegex(vm.PlatformError, 'managed live execution required'):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.reads, [])

    def test_pending_request_observes_once_and_new_nonce_recovers(self):
        self.assertEqual(support.clock_once(), 1)
        self.assertEqual(self.observed.call_count, 1)
        calls = self.f.xen.calls[self.commands_before:]
        self.assertEqual(calls.count(['xl', 'list', '-v']), 2)
        self.assertEqual(sum(call[:2] == ['xenova-domain-identity', '--domid'] for call in calls), 2)
        self.assertEqual(json.loads(self.store.values[self.response_path])['nonce'], '123')
        self.assertEqual(support.clock_once(), 0)
        self.assertEqual(self.observed.call_count, 1)

        self.f.ticks += 10
        self.request['nonce'] = '10123'
        self.store.values[self.request_path] = wire(self.request)
        self.assertEqual(support.clock_once(), 1)
        self.assertEqual(self.observed.call_count, 2)
        self.assertEqual(json.loads(self.store.values[self.response_path])['nonce'], '10123')

    def test_main_calls_the_real_responder_and_keeps_its_response_count_output(self):
        for expected in (1, 0):
            with self.subTest(expected=expected):
                output = io.StringIO()
                with (patch.object(support.os, 'geteuid', return_value=0), redirect_stdout(output)):
                    self.assertEqual(support.main(['clock-once']), 0)
                self.assertEqual(json.loads(output.getvalue()), {'responses': expected})
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(len(self.store.writes), 1)

    def test_valid_response_for_another_tuple_remains_pending(self):
        for changes in ({'nonce': '122'}, {'host_boot_id': foundation.HOST_B},
                        {'execution_id': '750e8400-e29b-41d4-a716-446655440000'}):
            with self.subTest(changes=changes):
                self.store.values[self.response_path] = wire(self.answer(**changes))
                before = self.observed.call_count
                self.assertEqual(support.clock_once(), 1)
                self.assertEqual(self.observed.call_count, before + 1)

    def test_malformed_request_is_refused_before_observation(self):
        bad = [b'{broken', wire([]), wire({**self.request, 'nonce': True}),
               wire({**self.request, 'nonce': None}), wire({**self.request, 'nonce': 123}),
               wire({**self.request, 'nonce': ''}), wire({**self.request, 'nonce': '1' * 21}),
               wire({**self.request, 'extra': 1}),
               wire({**self.request, 'execution_id': foundation.HOST_B})]
        for raw in bad:
            with self.subTest(raw=raw):
                self.store.values[self.request_path] = raw
                with self.assertRaises(ValueError):
                    support.clock_once()
                self.assert_idle()

    def test_malformed_response_fields_are_refused_before_observation(self):
        bad = [b'{broken', wire([])]
        fields = ('nonce', 'host_boot_id', 'execution_id', 'unix_ms', 'source',
                  'source_error_ms', 'drift_ppm')
        for field in fields:
            missing = self.answer()
            del missing[field]
            bad.append(wire(missing))
        for field, values in (
                ('nonce', (True, None, 123, '')),
                ('host_boot_id', (True, None, 0)),
                ('execution_id', (True, None, 0)),
                ('unix_ms', (True, None, 1767225600001, '0', '4102444800001')),
                ('source', (True, None, '', '\0', 'x' * 32)),
                ('source_error_ms', (True, None, '0', -1, 10001)),
                ('drift_ppm', (True, None, '0', -1, 1001))):
            bad.extend(wire(self.answer(**{field: value})) for value in values)
        for raw in bad:
            with self.subTest(raw=raw):
                self.store.values[self.response_path] = raw
                with self.assertRaises(ValueError):
                    support.clock_once()
                self.assert_idle()

    def test_store_read_error_is_not_treated_as_an_absent_request(self):
        for suffix in ('/request', '/response'):
            with self.subTest(suffix=suffix):
                self.store.read_error = suffix
                with self.assertRaises(OSError) as raised:
                    support.clock_once()
                self.assertEqual(raised.exception.errno, errno.EIO)
                self.assert_idle()

    def test_changed_or_removed_request_aborts_after_actual_observation(self):
        for replacement in (wire({**self.request, 'nonce': '124'}), None, b''):
            with self.subTest(replacement=replacement):
                self.store.values[self.request_path] = wire(self.request)
                self.store.on_begin = lambda value=replacement: self.store.values.__setitem__(self.request_path, value)
                before = self.observed.call_count
                with self.assertRaises(vm.PlatformError):
                    support.clock_once()
                self.assertEqual(self.observed.call_count, before + 1)
                self.assertEqual(self.sample.call_count, 0)
                self.assertEqual(self.store.writes, [])
                self.assert_aborted()

    def test_request_spacing_and_key_order_changes_preserve_the_same_exchange(self):
        variants = (json.dumps(self.request, indent=2).encode(),
                    json.dumps(self.request, sort_keys=True, separators=(', ', ': ')).encode())
        for replacement in variants:
            with self.subTest(replacement=replacement):
                self.store.values[self.request_path] = wire(self.request)
                self.store.values.pop(self.response_path, None)
                self.store.on_begin = lambda value=replacement: self.store.values.__setitem__(self.request_path, value)
                before = self.observed.call_count
                self.assertEqual(support.clock_once(), 1)
                self.assertEqual(self.observed.call_count, before + 1)
                response = json.loads(self.store.values[self.response_path])
                self.assertEqual(response['nonce'], self.request['nonce'])
                self.assertEqual(response['execution_id'], self.request['execution_id'])
                self.assertFalse(self.store.ends[-1][1])

    def test_changed_execution_and_invalid_transaction_requests_still_abort(self):
        variants = (wire({**self.request, 'execution_id': foundation.HOST_B}),
                    wire({**self.request, 'nonce': True}), wire({**self.request, 'nonce': None}),
                    wire({**self.request, 'nonce': 123}), wire({**self.request, 'execution_id': None}),
                    wire({**self.request, 'extra': 1}), wire(None), wire([]), b'{broken')
        for replacement in variants:
            with self.subTest(replacement=replacement):
                self.store.values[self.request_path] = wire(self.request)
                self.store.on_begin = lambda value=replacement: self.store.values.__setitem__(self.request_path, value)
                before = self.observed.call_count
                with self.assertRaises(ValueError):
                    support.clock_once()
                self.assertEqual(self.observed.call_count, before + 1)
                self.assertEqual(self.sample.call_count, 0)
                self.assertEqual(self.store.writes, [])
                self.assert_aborted()

    def test_response_completed_during_observation_is_not_rewritten(self):
        self.store.on_begin = lambda: self.store.values.__setitem__(self.response_path, wire(self.answer()))
        self.assertEqual(support.clock_once(), 0)
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])

    def test_store_owner_mismatch_aborts_before_clock_sample(self):
        self.store.values[f"/local/domain/{self.context['domid']}/vm"] = ('/vm/' + foundation.HOST_B).encode()
        with self.assertRaises(ValueError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()

    def test_execution_change_after_observation_aborts(self):
        def changed():
            execution = self.f.p.execution()
            run = execution.run_record(self.record)
            run['execution_id'] = '750e8400-e29b-41d4-a716-446655440000'
            execution.write(self.f.p.runtime / 'executions' / (self.vm_id + '.json'), run)
        self.store.on_begin = changed
        with self.assertRaises(vm.PlatformError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()

    def test_run_change_outside_the_context_tuple_still_aborts(self):
        def changed():
            execution = self.f.p.execution()
            run = execution.run_record(self.record)
            run['prepared_monotonic_ms'] += 1
            execution.write(self.f.p.runtime / 'executions' / (self.vm_id + '.json'), run)
        self.store.on_begin = changed
        with self.assertRaises(vm.PlatformError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()

    def test_new_intent_revision_after_observation_aborts(self):
        def changed():
            execution = self.f.p.execution()
            intent = execution.intent(self.record)
            intent['intent_revision'] += 1
            execution.write(self.f.p.data / 'control' / (self.vm_id + '.json'), intent)
        self.store.on_begin = changed
        with self.assertRaises(vm.PlatformError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()

    def test_host_change_after_observation_aborts(self):
        self.store.on_begin = lambda: setattr(self.f, 'host', foundation.HOST_B)
        with self.assertRaises(vm.PlatformError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()

    def test_deadline_expiry_after_observation_aborts(self):
        self.store.on_begin = lambda: setattr(self.f, 'ticks', 7)
        with self.assertRaises(TimeoutError):
            support.clock_once()
        self.assertEqual(self.sample.call_count, 0)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()

    def test_failed_store_write_aborts_without_publishing_a_response(self):
        self.store.write_ok = False
        with self.assertRaises(OSError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 1)
        self.assert_aborted()

    def test_clock_sample_cannot_extend_the_original_deadline(self):
        def slow_sample():
            self.f.ticks = 7
            return self.sample.return_value
        self.sample.side_effect = slow_sample
        with self.assertRaises(TimeoutError):
            support.clock_once()
        self.assertEqual(self.observed.call_count, 1)
        self.assertEqual(self.sample.call_count, 1)
        self.assertEqual(self.store.writes, [])
        self.assert_aborted()


class ClockMainErrorsTest(unittest.TestCase):
    def test_platform_failures_emit_structured_nonzero_diagnostics(self):
        errors = (ExecutionError('DEADLINE_EXCEEDED', 'synthetic clock budget expired'),
                  vm.PlatformError('managed live execution required'),
                  vm.PlatformError('execution binding changed during observation'))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                output, diagnostic = io.StringIO(), io.StringIO()
                with (patch.object(support.os, 'geteuid', return_value=0),
                      patch.object(vm, 'storage_check'),
                      patch.object(support, 'clock_once', side_effect=error),
                      redirect_stdout(output), redirect_stderr(diagnostic)):
                    self.assertNotEqual(support.main(['clock-once']), 0)
                self.assertEqual(output.getvalue(), '')
                value = json.loads(diagnostic.getvalue())
                self.assertIs(value['ok'], False)
                self.assertEqual(value['stage'], 'clock-once')
                serialized = json.dumps(value)
                self.assertIn(type(error).__name__, serialized)
                self.assertEqual(value['message'], str(error))
                if hasattr(error, 'error'):
                    self.assertIn(error.error['code'], serialized)

    def test_unknown_platform_failure_preserves_the_same_exception(self):
        error = vm.PlatformError('synthetic registry unavailable\nprivate helper body')
        output, diagnostic = io.StringIO(), io.StringIO()
        with (patch.object(support.os, 'geteuid', return_value=0),
              patch.object(vm, 'storage_check'),
              patch.object(support, 'clock_once', side_effect=error),
              redirect_stdout(output), redirect_stderr(diagnostic)):
            with self.assertRaises(vm.PlatformError) as raised:
                support.main(['clock-once'])
        self.assertIs(raised.exception, error)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(diagnostic.getvalue(), '')

    def test_execution_reason_is_bounded_to_one_line(self):
        error = ExecutionError('DEADLINE_EXCEEDED', 'clock budget\n' + 'x' * 1000)
        diagnostic = io.StringIO()
        with (patch.object(support.os, 'geteuid', return_value=0),
              patch.object(vm, 'storage_check'),
              patch.object(support, 'clock_once', side_effect=error),
              redirect_stderr(diagnostic)):
            self.assertEqual(support.main(['clock-once']), 1)
        value = json.loads(diagnostic.getvalue())
        self.assertEqual(value['code'], 'DEADLINE_EXCEEDED')
        self.assertLessEqual(len(value['message']), 256)
        self.assertNotIn('\n', value['message'])

    def test_store_and_wire_failures_keep_their_existing_diagnostics(self):
        for error in (OSError(errno.EIO, 'synthetic Store failure'), ValueError('synthetic invalid wire')):
            with self.subTest(error=type(error).__name__):
                output, diagnostic = io.StringIO(), io.StringIO()
                with (patch.object(support.os, 'geteuid', return_value=0),
                      patch.object(vm, 'storage_check'),
                      patch.object(support, 'clock_once', side_effect=error),
                      redirect_stdout(output), redirect_stderr(diagnostic)):
                    self.assertNotEqual(support.main(['clock-once']), 0)
                self.assertEqual(output.getvalue(), '')
                value = json.loads(diagnostic.getvalue())
                self.assertIs(value['ok'], False)
                self.assertEqual(value['error'], type(error).__name__)
                self.assertEqual(value['errno'], getattr(error, 'errno', None))
                self.assertEqual(value['stage'], 'clock-once')

    def test_internal_type_and_key_errors_remain_visible(self):
        for error in (TypeError('synthetic internal type error'), KeyError('synthetic internal key error')):
            with self.subTest(error=type(error).__name__):
                with (patch.object(support.os, 'geteuid', return_value=0),
                      patch.object(vm, 'storage_check'),
                      patch.object(support, 'clock_once', side_effect=error)):
                    with self.assertRaises(type(error)):
                        support.main(['clock-once'])
