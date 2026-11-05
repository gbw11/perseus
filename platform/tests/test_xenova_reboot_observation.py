"""실제 Platform.reboot에 Xen 출력·시간·작은 자산 경계만 대입한다."""
import copy
import io
import json
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

import test_xenova_platform as fixtures
import test_xenova_recovery_foundation as foundation
from test_xenova_platform import domain, record, vm


class RebootObservationTest(unittest.TestCase):
    setUp = fixtures.LifecycleTest.setUp
    sleep = fixtures.LifecycleTest.sleep

    def sequence(self, mutation=None):
        with redirect_stdout(io.StringIO()):
            self.p.start(self.r)
        old = copy.deepcopy(self.runner.rows[0])
        self.previous_execution = self.p.execution().run_record(self.r)['execution_id']
        accepted, samples = False, 0

        def command(args, **kwargs):
            nonlocal accepted, samples
            if args[:2] == ['xl', 'reboot']:
                self.runner.calls.append(args)
                accepted = True
                return ''
            if accepted and args == ['xl', 'list', '-l'] and samples < 3:
                self.runner.calls.append(args)
                return '[]'
            if accepted and args == ['xl', 'list', '-v']:
                samples += 1
                if mutation == 'malformed':
                    return 'broken output'
                if samples <= 2 or mutation == 'persistent-null':
                    row = copy.deepcopy(old)
                    row['config']['c_info']['name'] = '(null)'
                    flags = {'running-null': '-b----', 'paused-null': '--p---',
                             'persistent-null': '-b----'}.get(mutation, '--ps--')
                    self.runner.rows = [row]
                    self.runner.states = {old['domid']: flags}
                    if mutation == 'uuid-reuse':
                        row['config']['c_info']['uuid'] = record('debian-dev')['uuid']
                    if mutation == 'renamed-old':
                        row['config']['c_info']['name'] = 'unexpected-name'
                else:
                    row = domain(self.r, self.directory/'root.raw', old['domid']+10)
                    self.runner.rows = [row]
                    self.runner.states = {row['domid']: '--p---' if samples == 3 else '-b----'}
                    if mutation == 'wrong-new-uuid':
                        row['config']['c_info']['uuid'] = record('debian-dev')['uuid']
                    if mutation == 'wrong-new-name':
                        row['config']['c_info']['name'] = 'unexpected-new-name'
            return self.runner(args, **kwargs)
        self.p.command = command
        return old

    def assert_completed(self, mutation=None):
        old = self.sequence(mutation)
        with redirect_stdout(io.StringIO()):
            self.p.reboot(self.r, timeout=10)
        run = self.p.execution().run_record(self.r)
        self.assertEqual(run['phase'], 'observed')
        self.assertEqual(run['domid'], old['domid']+10)
        self.assertNotEqual(run['execution_id'], self.previous_execution)
        self.assertEqual([c for c in self.runner.calls if c[:2] == ['xl', 'reboot']],
                         [['xl', 'reboot', str(old['domid'])]])
        self.assertFalse(any(c[:2] in (['xl', 'destroy'], ['xl', 'shutdown'])
                             for c in self.runner.calls))

    def test_reboot_waits_for_old_cleanup_and_new_unpause_before_strict_binding(self):
        self.assert_completed()

    def test_old_null_with_earlier_blocked_flags_waits_for_verified_new_domain(self):
        self.assert_completed('running-null')

    def test_old_null_with_earlier_paused_flags_waits_for_verified_new_domain(self):
        self.assert_completed('paused-null')

    def test_persistent_old_null_is_never_absence_or_success(self):
        old = self.sequence('persistent-null')
        with self.assertRaisesRegex(vm.PlatformError, 'deadline'):
            self.p.reboot(self.r, timeout=5)
        self.assertEqual(self.p.execution().run_record(self.r)['phase'], 'launching')
        self.assertEqual([c for c in self.runner.calls if c[:2] == ['xl', 'reboot']],
                         [['xl', 'reboot', str(old['domid'])]])
        self.assertFalse(any(c[:2] in (['xl', 'destroy'], ['xl', 'shutdown'])
                             for c in self.runner.calls))

    def assert_replacement_refused(self, mutation):
        self.sequence(mutation)
        with self.assertRaisesRegex(vm.PlatformError, 'replacement'):
            self.p.reboot(self.r, timeout=10)

    def test_reboot_rejects_old_domid_with_reused_uuid(self):
        self.assert_replacement_refused('uuid-reuse')

    def test_reboot_rejects_unexpected_non_null_old_name(self):
        self.assert_replacement_refused('renamed-old')

    def test_reboot_rejects_new_domain_with_wrong_uuid(self):
        self.assert_replacement_refused('wrong-new-uuid')

    def test_reboot_rejects_new_domain_with_wrong_name(self):
        self.assert_replacement_refused('wrong-new-name')

    def test_reboot_does_not_treat_observation_failure_as_absence(self):
        self.sequence('malformed')
        with self.assertRaisesRegex(vm.PlatformError, 'header'):
            self.p.reboot(self.r, timeout=10)


class RebootControlTest(unittest.TestCase):
    setUp = foundation.FoundationTest.setUp
    sleep = foundation.FoundationTest.sleep
    call = foundation.FoundationTest.call
    ok = foundation.FoundationTest.ok
    fails = foundation.FoundationTest.fails
    start = foundation.FoundationTest.start
    state = foundation.FoundationTest.state
    physical = foundation.FoundationTest.physical
    spec = foundation.FoundationTest.spec
    recover = foundation.FoundationTest.recover

    def test_consecutive_reboots_wait_for_new_domid_with_unpublished_name(self):
        self.start()
        self.ok(self.call('reboot'))
        first = self.state()
        self.ticks += 20
        command = self.p.command
        submitted = False
        seen = False
        settling = False

        def transient(args, **kwargs):
            nonlocal submitted, seen, settling
            output = command(args, **kwargs)
            if args == ['xl', 'list', '-l']:
                return json.dumps([{'domid':0}, *json.loads(output)])
            if args[:2] == ['xl', 'reboot']:
                submitted = True
            if submitted and not seen and args == ['xl', 'list', '-v']:
                seen = True
                return output.replace(self.r['name'], '(null)')
            if submitted and not settling and args == ['xl', 'list']:
                settling = True
                lines = output.splitlines(keepends=True)
                return ''.join(line.replace('r-----', '------') if line.startswith(self.r['name']+' ') else line
                               for line in lines)
            return output

        with patch.object(self.p, 'command', transient):
            response = self.call('reboot', key='second')
        self.ok(response)
        self.assertTrue(seen and settling)
        second = self.state()
        self.assertNotEqual(first['execution_id'], second['execution_id'])
        self.assertNotEqual(first['xen_unique_id'], second['xen_unique_id'])
        self.assertEqual(second['intent_revision'], first['intent_revision']+1)
        self.assertEqual(len(self.physical('reboot')), 2)
        op = self.ok(self.call('control-operation', {'operation_id':response['operation_id']}, execute=False))
        self.assertEqual(op['operation']['phase'], 'completed')
        self.ok(self.recover(self.spec()))
        self.assertIsNotNone(self.state()['execution_id'])
        self.assertNotEqual(self.state()['execution_id'], second['execution_id'])

    def interrupt_binding(self, *, returned=True):
        old = self.start()
        original = self.p.command
        submitted = False

        def fail(args, **kwargs):
            nonlocal submitted
            output = original(args, **kwargs)
            if args[:2] == ['xl', 'reboot']:
                submitted = True
                if not returned:
                    raise foundation.vm.PlatformError('reboot response was lost')
            if submitted and args == ['xl', 'list', '-v']:
                return 'bad list'
            return output

        with patch.object(self.p, 'command', fail):
            failure = self.fails(self.call('reboot', key='unbound'), 'RECONCILE_REQUIRED')
        run = self.p.execution().run_record(self.r)
        self.assertEqual(run['phase'], 'launching')
        self.assertEqual(run['reboot_from'], {'domid':old['domid'], 'xen_unique_id':old['xen_unique_id']})
        self.assertIs(run.get('reboot_returned', False), returned)
        self.assertIsNone(self.state()['execution_id'])
        return failure, run

    def test_same_key_binds_after_deadline_without_reboot_or_create(self):
        failure, run = self.interrupt_binding()
        self.fails(self.call('reboot', key='new-key'), 'RECONCILE_REQUIRED')
        before = self.p.execution().intent(self.r)
        deadline = next(op['deadline_monotonic_ms'] for op in self.p.execution().requests()
                        if op['operation_id'] == failure['operation_id'])
        self.ticks = deadline/1000+1
        result = self.call('reboot', key='unbound')
        self.ok(result)
        self.assertTrue(result['replayed'])
        self.assertEqual(result['operation_id'], failure['operation_id'])
        self.assertEqual(self.state()['execution_id'], run['execution_id'])
        self.assertEqual(self.p.execution().intent(self.r), before)
        op = self.ok(self.call('control-operation', {'operation_id':failure['operation_id']}, execute=False))
        self.assertEqual(op['operation']['phase'], 'completed')
        self.assertEqual(op['operation']['deadline_monotonic_ms'], deadline)
        self.ok(self.call('reboot', key='unbound'))
        self.assertEqual(len(self.physical('reboot')), 1)
        self.assertEqual(len(self.physical('create')), 1)
        self.assertFalse(self.physical('shutdown') or self.physical('destroy'))
        self.ok(self.call('reboot', key='after-reconcile'))

    def test_same_key_never_adopts_a_different_root_disk(self):
        _, run = self.interrupt_binding()
        other = self.directory/'other.raw'
        other.touch()
        self.xen.rows[0]['config']['disks'][0]['pdev_path'] = str(other)
        self.fails(self.call('reboot', key='unbound'), 'RECONCILE_REQUIRED')
        self.assertEqual(self.p.execution().run_record(self.r), run)
        self.assertIsNone(self.state()['execution_id'])
        self.assertEqual(len(self.physical('reboot')), 1)

    def test_same_key_never_adopts_a_domain_without_confirmed_reboot_return(self):
        _, run = self.interrupt_binding(returned=False)
        self.fails(self.call('reboot', key='unbound'), 'RECONCILE_REQUIRED')
        self.assertEqual(self.p.execution().run_record(self.r), run)
        self.assertEqual(len(self.physical('reboot')), 1)


    def test_same_key_rejects_rebinding_after_new_stop_intent(self):
        _, run = self.interrupt_binding()
        e = self.p.execution()
        e.accept(self.r, {'action':'stop', 'vm_id':self.vm_id, 'force':False}, 'later-stop')
        self.fails(self.call('reboot', key='unbound'), 'TARGET_CHANGED')
        self.assertEqual(e.run_record(self.r), run)
        self.assertEqual(e.intent(self.r)['desired_state'], 'stopped')
        self.assertEqual(len(self.physical('reboot')), 1)
