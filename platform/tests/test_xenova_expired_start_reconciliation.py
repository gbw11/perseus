"""실제 Manager 원 start 재전달의 만료·부재·scope 종결과 저장 중단을 검증한다."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
import xenova_execution as execution
import xenova_guest_process as guest
from xenova_runtime import PlatformError


class ExpiredStartTest(unittest.TestCase):
    def setUp(self):
        self.f = foundation.FoundationTest('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.p, self.r = self.f.p, self.f.r
        self.p.process_isolation = True
        self.scope_state = 'not-found'
        self.scope_active = 'inactive'
        self.fail_submission = True
        self.scope_calls = []
        original = self.p.command
        self.original_command = original

        def external(argv, **kwargs):
            if argv[:2] == ['systemctl', 'show']:
                self.scope_calls.append(argv)
                prop = argv[argv.index('-p')+1]
                return {'LoadState':self.scope_state, 'ActiveState':self.scope_active,
                        'Before':'xenova-vm-shutdown.service'}[prop]
            if argv[0] == 'systemd-run':
                if self.fail_submission:
                    raise PlatformError('create helper failed before importing its runtime')
                return original(['xl', 'create', str(self.p.runtime/(self.r['id']+'.cfg'))], **kwargs)
            return original(argv, **kwargs)

        self.p.command = external
        first = self.f.call('start', key='owned-failed-start')
        self.f.fails(first, 'RECONCILE_REQUIRED')
        self.operation_id = first['operation_id']
        self.path = self.p.data/'control-requests'/(self.operation_id+'.json')
        self.run_path = self.p.runtime/'executions'/(self.r['uuid']+'.json')
        self.original = json.loads(self.path.read_text())
        self.original_run = json.loads(self.run_path.read_text())
        self.f.ticks = self.original['deadline_monotonic_ms']/1000 + 1

    def replay(self):
        return self.f.call('start', key='owned-failed-start')

    def unchanged_fields(self, final):
        for key in ('operation_id', 'request', 'request_hash', 'idempotency_key', 'deadline_monotonic_ms',
                    'host_boot_id', 'base_revision', 'intent_revision', 'lifecycle_generation'):
            self.assertEqual(final[key], self.original[key], key)

    def assert_closed(self):
        final = json.loads(self.path.read_text())
        run = json.loads(self.run_path.read_text())
        self.assertEqual(final['phase'], 'failed')
        self.assertEqual(final['error']['code'], 'XEN_EXECUTION_FAILED')
        self.assertEqual(final['reconciliation']['original_error'], self.original['error'])
        self.assertEqual(final['reconciliation']['physical_commands'], 0)
        self.unchanged_fields(final)
        self.assertEqual(run['phase'], 'stopped')
        self.assertEqual(run['execution_id'], self.original_run['execution_id'])
        self.assertNotIn('launch_aborted_before_submit', run)
        self.assertEqual(self.f.physical('create'), [])
        self.assertEqual(self.f.physical('destroy'), [])
        self.assertFalse(any(argv[:2] == ['systemctl','kill'] for argv in self.scope_calls))

    def test_expired_absent_start_closes_original_then_new_explicit_request_progresses(self):
        original_observe = execution.Execution.observe
        with patch.object(execution.Execution, 'observe', autospec=True, side_effect=original_observe) as observed:
            self.f.fails(self.replay(), 'XEN_EXECUTION_FAILED')
        self.assertEqual(observed.call_count, 2)
        self.assert_closed()
        before = self.path.read_bytes()
        self.f.fails(self.replay(), 'XEN_EXECUTION_FAILED')
        self.assertEqual(self.path.read_bytes(), before)
        self.fail_submission = False
        self.f.ok(self.f.call('start', key='explicit-following-start'))
        self.assertEqual(len(self.f.physical('create')), 1)
        newer = self.run_path.read_bytes()
        self.f.fails(self.replay(), 'XEN_EXECUTION_FAILED')
        self.assertEqual(self.run_path.read_bytes(), newer)

    def test_original_deadline_not_expired_is_not_closed(self):
        self.f.ticks = self.original['deadline_monotonic_ms']/1000 - 1
        before = self.run_path.read_bytes(), self.path.read_bytes()
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual((self.run_path.read_bytes(), self.path.read_bytes()), before)

    def test_live_scope_is_not_killed_or_closed(self):
        self.scope_state, self.scope_active = 'loaded', 'active'
        before = self.run_path.read_bytes(), self.path.read_bytes()
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual((self.run_path.read_bytes(), self.path.read_bytes()), before)
        self.assertFalse(any(argv[:2] == ['systemctl','kill'] for argv in self.scope_calls))

    def test_missing_wrong_scope_and_unknown_scope_state_remain_uncertain(self):
        for scope in (None, 'unrelated.scope'):
            run = copy.deepcopy(self.original_run)
            run['process_scope'] = scope
            self.p.execution().write(self.run_path, run)
            self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
            self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')
        self.p.execution().write(self.run_path, self.original_run)
        self.scope_state = 'unknown'
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')

    def test_no_process_isolation_does_not_infer_helper_termination(self):
        self.p.process_isolation = False
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def test_original_journal_vm_identity_must_match_its_request(self):
        previous = {**self.original, 'vm_id':foundation.uuid4()}
        self.p.execution().write(self.path, previous)
        before = self.path.read_bytes(), self.run_path.read_bytes()
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual((self.path.read_bytes(), self.run_path.read_bytes()), before)

    def test_loaded_inactive_scope_and_deadline_cause_are_supported(self):
        self.scope_state, self.scope_active = 'loaded', 'inactive'
        previous = copy.deepcopy(self.original)
        previous['error']['details']['cause']['code'] = 'DEADLINE_EXCEEDED'
        self.p.execution().write(self.path, previous)
        self.f.fails(self.replay(), 'DEADLINE_EXCEEDED')
        final = json.loads(self.path.read_text())
        self.assertEqual(final['reconciliation']['original_error'], previous['error'])
        self.assertEqual(json.loads(self.run_path.read_text())['phase'], 'stopped')

    def test_unknown_original_cause_and_corrupt_marker_are_not_repaired(self):
        previous = copy.deepcopy(self.original)
        previous['error']['details']['cause']['code'] = 'STORAGE_UNAVAILABLE'
        self.p.execution().write(self.path, previous)
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.p.execution().write(self.path, self.original)
        run = {**self.original_run, 'control_reconciliation':{'outcome':'different'}}
        self.p.execution().write(self.run_path, run)
        self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def test_host_change_at_guard_is_not_closed(self):
        def checkpoint(phase, _):
            if phase == 'control-start-reconcile-before-guard':self.f.host = foundation.HOST_B
        self.p.execution_checkpoint = checkpoint
        self.f.fails(self.replay(), 'TARGET_CHANGED')
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def test_crash_after_failed_journal_is_resumed_by_same_key(self):
        def checkpoint(phase, _):
            if phase == 'intent-failed':raise foundation.Interrupted()
        self.p.execution_checkpoint = checkpoint
        with self.assertRaises(foundation.Interrupted):self.replay()
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'failed')
        self.assertEqual(json.loads(self.run_path.read_text())['phase'], 'uncertain')
        self.p.execution_checkpoint = lambda *_:None
        self.f.fails(self.replay(), 'XEN_EXECUTION_FAILED')
        self.assert_closed()

    def test_changed_config_or_run_context_is_not_repaired(self):
        original_config = self.p.config
        with patch.object(self.p, 'config', side_effect=lambda record: original_config(record)+'\n'):
            self.f.fails(self.replay(), 'TARGET_CHANGED')
        for field, value in (('owner', {'kind':'user','operation_id':foundation.uuid4()}),
                             ('intent_revision', self.original_run['intent_revision']+1),
                             ('process_scope', guest.scope_name(self.r['uuid'], foundation.uuid4()))):
            run = {**self.original_run, field:value}
            self.p.execution().write(self.run_path, run)
            self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def test_domain_appearing_after_scope_observation_is_not_closed(self):
        def checkpoint(phase, _):
            if phase == 'control-start-reconcile-before-guard':
                self.fail_submission = False
                self.original_command(['xl','create',str(self.p.runtime/(self.r['id']+'.cfg'))])
        self.p.execution_checkpoint = checkpoint
        self.f.fails(self.replay(), 'TARGET_CHANGED')
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def test_intent_change_at_final_guard_is_preserved(self):
        def checkpoint(phase, _):
            if phase == 'control-start-reconcile-before-guard':
                intent = self.p.execution().intent(self.r)
                intent['intent_revision'] += 1
                intent['accepted_operation_id'] = foundation.uuid4()
                self.p.execution().write(self.p.data/'control'/(self.r['uuid']+'.json'), intent)
        self.p.execution_checkpoint = checkpoint
        self.f.fails(self.replay(), 'TARGET_CHANGED')
        self.assertEqual(json.loads(self.path.read_text())['phase'], 'uncertain')

    def eio(self, boundary, after_replace):
        original_write = execution.atomic_write
        fired = []
        def write(path, text, *args):
            row = json.loads(text)
            at = ('journal' if Path(path) == self.path and row.get('phase') == 'failed' else
                  'stopped' if Path(path) == self.run_path and row.get('phase') == 'stopped' else
                  'pending' if Path(path) == self.run_path and row.get('control_reconciliation') else None)
            if at == boundary and not fired:
                fired.append(at)
                if after_replace:
                    original_write(path, text, *args)
                raise OSError(5, 'injected settlement I/O failure')
            return original_write(path, text, *args)
        with patch.object(execution, 'atomic_write', side_effect=write):
            self.f.fails(self.replay(), 'RECONCILE_REQUIRED')
        self.assertEqual(fired, [boundary])
        self.assertEqual(json.loads(self.run_path.read_text())['phase'], 'uncertain')
        self.f.fails(self.replay(), 'XEN_EXECUTION_FAILED')
        self.assert_closed()

    def test_pending_write_eio_before_replace(self): self.eio('pending', False)
    def test_pending_write_eio_after_replace(self): self.eio('pending', True)
    def test_journal_write_eio_before_replace(self): self.eio('journal', False)
    def test_journal_write_eio_after_replace(self): self.eio('journal', True)
    def test_stopped_write_eio_before_replace(self): self.eio('stopped', False)
    def test_stopped_write_eio_after_replace(self): self.eio('stopped', True)


if __name__ == '__main__':
    unittest.main()
