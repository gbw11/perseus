"""실제 Platform.reboot에 Xen 출력·시간·작은 자산 경계만 대입한다."""
import copy
import io
from contextlib import redirect_stdout
import unittest

import test_xenova_platform as fixtures
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
