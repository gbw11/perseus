"""실제 CPU/Execution journal의 단계 근거·기한·완료 경계를 FakeXen으로 검증한다."""
import copy
from collections import Counter
import unittest
from unittest.mock import patch

import test_xenova_cpu as cpu_fixture
from test_xenova_management import Interrupted
import test_xenova_network_zephyr as network_fixture
from test_xenova_recovery_foundation import HOST_B
from xenova_cpu import CPU
from xenova_execution import Execution, ExecutionError
import xenova_vm as vm


class ManagementBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.t = cpu_fixture.CPUTest('runTest')
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.p, self.m, self.xen = self.t.p, self.t.m, self.t.xen

    def zephyr(self):
        self.t.vm_id = self.t.zid
        self.t.ok(network_fixture.NetworkPlatformTest.transition(self.t, network_fixture.NetworkPlatformTest.spec(self.t)))
        record = self.p.records(validate_assets=False)[self.t.z['id']]
        self.t.z = record
        self.xen.records[record['id']] = record
        self.t.activate()
        self.t.ok(self.t.control(record))
        return record

    def test_same_phase_cpu_evidence_is_one_use_and_keeps_full_two_sample_verification(self):
        self.t.activate()
        capture = {}
        original = CPU.sample
        calls = []
        def counted(cpu, end):
            calls.append(end)
            return original(cpu, end)
        with patch.object(CPU, 'sample', counted):
            expected = self.p.cpu().require(self.t.linux, 90, capture=capture)
            launch = self.p.execution().prepare_launch(self.t.linux, deadline=90,
                                                       expected_cpu=expected, cpu_evidence=capture['evidence'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(launch['cpu_policy'], expected)
        with self.assertRaises(ExecutionError) as caught:
            self.p.cpu().consume(self.t.linux, 90, capture['evidence'], expected=expected)
        self.assertEqual(caught.exception.error['code'], 'TARGET_CHANGED')

    def test_evidence_rejects_host_intent_record_deadline_and_policy_changes(self):
        self.t.activate()
        for change in ('host', 'intent', 'record', 'deadline', 'policy'):
            with self.subTest(change=change):
                capture = {}
                record = copy.deepcopy(self.t.linux)
                expected = self.p.cpu().require(record, 90, capture=capture)
                end = 90
                with patch.object(self.p, 'boot_id_reader', return_value=HOST_B if change == 'host' else self.t.host), \
                        patch.object(CPU, 'context', return_value={'configured':False}) if change == 'policy' else patch.object(self.p, 'execution_checkpoint'):
                    if change == 'record':
                        record['display_name'] = '다른 이름'
                    if change == 'deadline':
                        end = 91
                    if change == 'intent':
                        intent = self.p.execution().intent(record)
                        self.p.execution().write(self.p.data/'control'/(record['uuid']+'.json'),
                                                 {**intent, 'intent_revision':intent['intent_revision']+1})
                    with self.assertRaises(ExecutionError) as caught:
                        self.p.cpu().consume(record, end, capture['evidence'], expected=expected)
                    self.assertEqual(caught.exception.error['code'], 'TARGET_CHANGED')
        self.assertFalse(any(a[:2] == ['xl','create'] for a in self.xen.calls))

    def test_batch_query_uses_two_shared_snapshots_and_never_returns_partial_failure(self):
        records = [self.t.linux, self.t.z, self.t.z2]
        original = self.p.removal_snapshot
        with patch.object(self.p, 'removal_snapshot', wraps=original) as snapshot:
            result = self.p.execution().observe_many(records, 15)
        self.assertEqual(snapshot.call_count, 2)
        self.assertEqual(set(result), {r['uuid'] for r in records})
        self.assertTrue(all(v['presence'] == 'absent' for v in result.values()))
        with patch.object(self.p, 'removal_snapshot', side_effect=vm.PlatformError('unavailable')):
            with self.assertRaises(ExecutionError) as caught:
                self.p.execution().observe_many(records, 15)
        self.assertEqual(caught.exception.error['code'], 'OBSERVATION_UNAVAILABLE')

    def test_required_binding_returns_complete_observation_without_post_bind_xen_read(self):
        self.t.activate()
        original = Execution.observe
        after_binding = []
        def observe(execution, record, end):
            run = execution.run_record(record)
            if run and run.get('cpu_verification') == 'complete':
                after_binding.append(record['uuid'])
                raise AssertionError('response assembly requested another Xen observation')
            return original(execution, record, end)
        with patch.object(Execution, 'observe', observe):
            self.t.ok(self.t.control(self.t.linux, key='binding-evidence'))
        self.assertEqual(after_binding, [])
        run = self.p.execution().run_record(self.t.linux)
        self.assertEqual(run['cpu_verification'], 'complete')
        self.assertNotIn('observation', run)

    def test_cpu_reboot_keeps_fresh_boundaries_and_reduces_repeated_layouts(self):
        record = self.zephyr()
        calls, layouts = [], []
        original_command, original_require = self.p.command, CPU.require
        def command(argv, **kwargs):
            calls.append(argv[:2])
            return original_command(argv, **kwargs)
        def require(cpu, *args, **kwargs):
            layouts.append(kwargs.copy())
            return original_require(cpu, *args, **kwargs)
        with patch.object(self.p, 'command', command), patch.object(CPU, 'require', require):
            self.t.ok(self.t.control(record, 'reboot', key='cpu-boundaries'))
        # Baseline at 8666712: seven full require calls / 177 external calls for
        # this same one-running-guest fixture. Fresh pre-shutdown/start/bind remain.
        self.assertLess(len(layouts), 7)
        self.assertLess(len(calls), 177)
        self.assertGreaterEqual(len(layouts), 3)
        self.assertGreaterEqual(Counter(map(tuple,calls))[('xl','vcpu-list')], 6)
        operation = next(o for o in self.p.execution().requests() if o['idempotency_key'] == 'cpu-boundaries')
        self.assertEqual(operation['deadline_monotonic_ms'], 90000)
        self.assertEqual(operation['new_cpu_verification'], 'complete')

    def test_changed_affinity_after_preparation_is_incomplete_and_never_repeated(self):
        self.t.activate()
        def corrupt(phase, value):
            if phase == 'launch-observed':
                self.t.affinities[value['domid']][0]['pcpus'] = [0,1,2,3]
        self.p.execution_checkpoint = corrupt
        self.t.fails(self.t.control(self.t.linux, key='drift-after-guard'), 'CPU_APPLY_INCOMPLETE')
        run = self.p.execution().run_record(self.t.linux)
        self.assertEqual(run['cpu_verification'], 'incomplete')
        before = len([a for a in self.xen.calls if a[:2] == ['xl','create']])
        self.p.execution_checkpoint = lambda *args: None
        self.t.fails(self.t.control(self.t.linux, key='drift-after-guard'), 'CPU_APPLY_INCOMPLETE')
        self.assertEqual(before, len([a for a in self.xen.calls if a[:2] == ['xl','create']]))
        self.t.ok(self.t.control(self.t.linux, 'stop'))

    def test_deadline_expiry_after_acceptance_cannot_gain_execution_on_replay(self):
        def expire(phase, value):
            if phase == 'intent-executing':
                self.t.ticks = 91
        self.p.execution_checkpoint = expire
        self.t.fails(self.t.control(self.t.linux, key='expired-start'), 'DEADLINE_EXCEEDED')
        operation = next(o for o in self.p.execution().requests() if o['idempotency_key'] == 'expired-start')
        self.assertEqual(operation['deadline_monotonic_ms'], 90000)
        self.p.execution_checkpoint = lambda *args: None
        self.t.fails(self.t.control(self.t.linux, key='expired-start'), 'DEADLINE_EXCEEDED')
        self.assertFalse(any(a[:2] == ['xl','create'] for a in self.xen.calls))

    def test_reboot_observed_journal_survives_later_user_stop_without_cpu_revalidation(self):
        record = self.zephyr()
        def interrupt(phase, value):
            if phase == 'intent-new-observed':
                raise Interrupted()
        self.p.execution_checkpoint = interrupt
        with self.assertRaises(Interrupted):
            self.t.control(record, 'reboot', key='durable-new-execution')
        self.p.execution_checkpoint = lambda *args: None
        self.t.ok(self.t.control(record, 'stop'))
        before = len(self.xen.calls)
        self.t.ticks = 100
        self.t.ok(self.t.control(record, 'reboot', key='durable-new-execution'))
        self.assertFalse(any(a[:2] in (['xl','create'],['xl','reboot']) for a in self.xen.calls[before:]))

    def test_legacy_unfinished_execution_never_receives_new_physical_budget(self):
        e = self.p.execution()
        op = e.accept(self.t.linux, {'vm_id':self.t.linux['uuid'], 'action':'start','force':False}, 'legacy-no-deadline')
        e.save_request(op, 'accepted')
        self.t.fails(self.t.control(self.t.linux, key='legacy-no-deadline'), 'RECONCILE_REQUIRED')
        unchanged = next(o for o in e.requests() if o['operation_id'] == op['operation_id'])
        self.assertNotIn('deadline_monotonic_ms', unchanged)
        self.assertFalse(any(a[:2] == ['xl','create'] for a in self.xen.calls))

    def test_required_completion_write_failure_remains_uncertain_then_same_owner_closes(self):
        self.t.activate()
        original = Execution.save_request
        failed = []
        def save(execution, operation, phase):
            if phase == 'completed' and not failed:
                failed.append(operation['operation_id'])
                raise OSError('required completion fsync unavailable')
            return original(execution, operation, phase)
        with patch.object(Execution, 'save_request', save):
            self.t.fails(self.t.control(self.t.linux, key='completion-write'), 'RECONCILE_REQUIRED')
        operation = next(o for o in self.p.execution().requests() if o['idempotency_key'] == 'completion-write')
        self.assertEqual(operation['phase'], 'uncertain')
        self.assertIsNotNone(operation['result'])
        creates = len([a for a in self.xen.calls if a[:2] == ['xl','create']])
        self.t.ticks = 100
        self.t.ok(self.t.control(self.t.linux, key='completion-write'))
        operation = next(o for o in self.p.execution().requests() if o['idempotency_key'] == 'completion-write')
        self.assertEqual(operation['phase'], 'completed')
        self.assertEqual(operation['deadline_monotonic_ms'], 90000)
        self.assertEqual(creates, len([a for a in self.xen.calls if a[:2] == ['xl','create']]))

    def test_cpu_evidence_cannot_skip_creation_of_complete_direct_call_evidence(self):
        self.t.activate()
        with self.assertRaises(ExecutionError) as caught:
            self.p.execution().prepare_launch(self.t.linux, deadline=90, cpu_evidence={'validated':True})
        self.assertEqual(caught.exception.error['code'], 'TARGET_CHANGED')
        self.assertIsNone(self.p.execution().run_record(self.t.linux))

    def test_intent_change_during_full_cpu_observation_cannot_complete_bound_execution(self):
        self.t.activate()
        binding, changed = [], []
        self.p.execution_checkpoint = lambda phase, value: binding.append(True) if phase == 'launch-observed' else None
        def change(argv, kwargs):
            if binding and not changed and argv[:2] == ['xl','vcpu-list']:
                changed.append(True)
                e = self.p.execution()
                intent = e.intent(self.t.linux)
                e.write(self.p.data/'control'/(self.t.linux['uuid']+'.json'),
                        {**intent, 'intent_revision':intent['intent_revision']+1, 'desired_state':'stopped'})
        self.t.command_hook = change
        self.t.fails(self.t.control(self.t.linux, key='intent-during-cpu'), 'CPU_APPLY_INCOMPLETE')
        run = self.p.execution().run_record(self.t.linux)
        self.assertEqual(run['cpu_verification'], 'incomplete')
        self.assertEqual(run['cpu_error']['code'], 'TARGET_CHANGED')
        self.assertEqual(len([a for a in self.xen.calls if a[:2] == ['xl','create']]), 1)


if __name__ == '__main__':
    unittest.main()
