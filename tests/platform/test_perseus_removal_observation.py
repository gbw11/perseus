"""실제 CLI→registry→stop_many 경로. Xen 명령 경계만 시간순 목록으로 대체한다."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest

from test_perseus_acceptance import AcceptanceTest
from first_install_fixture import vm
from test_perseus_platform import record, domain


class RemovalObservationTest(unittest.TestCase):
    setUp = AcceptanceTest.setUp
    cli = AcceptanceTest.cli
    save = AcceptanceTest.save
    live = AcceptanceTest.live
    add_linux = AcceptanceTest.add_linux
    add_zephyr = AcceptanceTest.add_zephyr
    controls = AcceptanceTest.controls

    def result(self):
        return json.loads((self.p.runtime / 'last-stop.json').read_text())

    def fake_time(self):
        ticks = [0]
        self.p.clock = lambda: ticks[0]
        self.p.sleep = lambda seconds: ticks.__setitem__(0, ticks[0] + seconds)
        return ticks

    def transient_omission(self):
        self.live(self.linux, 7); self.xen.hang_shutdown = True
        accepted, samples = False, 0
        def command(args, **kw):
            nonlocal accepted, samples
            if accepted and args == ['xl', 'list', '-l']:
                self.xen.calls.append(args)
                return '[]'  # 기존 domains()는 이어지는 짧은 목록과 불일치하여 실패한다.
            if accepted and args == ['xl', 'list', '-v']:
                samples += 1
                if samples >= 2:
                    self.xen.rows = []
            result = self.xen(args, **kw)
            if args == ['xl', 'shutdown', '7']:
                accepted = True
            return result
        self.p.command = command
        self.fake_time()

    def test_detail_omission_then_confirmed_absence_retries_observation_not_shutdown(self):
        self.transient_omission()
        self.cli('reboot-board')
        result = self.result()
        self.assertEqual(result['instances'][self.linux['id']]['stage'], 'domain-removed')
        self.assertEqual(result['observation']['changed_pairs'], 1)
        self.assertEqual(result['observation']['samples'], 4)
        self.assertEqual(self.controls(), [['xl', 'shutdown', '7']])
        self.assertEqual(self.xen.calls[-1], ['systemctl', 'reboot'])

    def test_persistent_live_identity_and_missing_details_timeout_without_false_success(self):
        self.live(self.linux, 7); self.xen.hang_shutdown = True
        accepted = False
        def command(args, **kw):
            nonlocal accepted
            if accepted and args == ['xl', 'list', '-l']:
                return '[]'
            result = self.xen(args, **kw)
            if args == ['xl', 'shutdown', '7']: accepted = True
            return result
        self.p.command = command; ticks = self.fake_time()
        with self.assertRaisesRegex(vm.PlatformError, 'graceful shutdown timed out'):
            self.cli('reboot-board')
        self.assertEqual(ticks[0], 90)
        self.assertEqual(self.result()['instances'][self.linux['id']]['stage'], 'removal-timeout')
        self.assertEqual(self.controls(), [['xl', 'shutdown', '7']])
        self.assertFalse(any(c[0] == 'systemctl' for c in self.xen.calls))

    def test_command_failure_and_malformed_output_fail_without_retry_or_empty_interpretation(self):
        for value in ('', 'Name ID Mem VCPUs State Time(s) UUID Reason-Code Security Label\n',
                      'invalid header\n', 'command-error', 'invalid-row', 'duplicate'):
            with self.subTest(value=value):
                self.xen.rows = []; self.xen.calls = []; self.live(self.linux, 7)
                count = 0
                def command(args, **kw):
                    nonlocal count
                    if args == ['xl', 'list', '-v']:
                        count += 1
                        if value == 'command-error': raise vm.PlatformError('Xen access failed')
                        if value == 'invalid-row':
                            return self.xen(args, **kw) + 'broken row\n'
                        if value == 'duplicate':
                            output = self.xen(args, **kw)
                            return output + output.splitlines()[1] + '\n'
                        return value
                    return self.xen(args, **kw)
                self.p.command = command
                with self.assertRaises(vm.PlatformError): self.cli('reboot-board')
                self.assertEqual(count, 1)
                self.assertEqual(self.result()['instances'][self.linux['id']]['stage'], 'removal-query-failed')
                self.assertFalse(any(c[0] == 'systemctl' for c in self.xen.calls))

    def test_reused_domid_changed_name_uuid_and_new_unregistered_domain_block_board(self):
        for mutation in ('domid-reuse', 'same-name', 'same-uuid', 'new'):
            with self.subTest(mutation=mutation):
                self.xen.rows = []; self.xen.calls = []; self.live(self.linux, 7)
                def command(args, **kw):
                    result = self.xen(args, **kw)
                    if args == ['xl', 'shutdown', '7']:
                        other = copy.deepcopy(self.linux)
                        other.update(uuid=record('debian-dev')['uuid'], name='unregistered')
                        domid = 8
                        if mutation == 'domid-reuse': domid = 7
                        elif mutation == 'same-name': other['name'] = self.linux['name']
                        elif mutation == 'same-uuid': other['uuid'] = self.linux['uuid']
                        self.xen.rows.append(domain(other, self.directory / 'root.raw', domid))
                    return result
                self.p.command = command
                with self.assertRaisesRegex(vm.PlatformError, 'replacement|unexpected'):
                    self.cli('poweroff')
                self.assertEqual(self.controls(), [['xl', 'shutdown', '7']])
                self.assertFalse(any(c[0] == 'systemctl' for c in self.xen.calls))

    def test_mixed_shutdown_partial_failure_and_user_retry_preserve_each_attempt(self):
        other, _ = self.add_linux()
        z = self.add_zephyr(); z['board_shutdown_policy'] = 'force-allowed'; self.save(z)
        for r, domid in ((self.linux, 1), (other, 2), (z, 3)): self.live(r, domid)
        fail = True
        def command(args, **kw):
            if fail and args == ['xl', 'shutdown', '1']:
                self.xen.calls.append(args)
                raise vm.PlatformError('one failed request')
            return self.xen(args, **kw)
        self.p.command = command
        with self.assertRaisesRegex(vm.PlatformError, 'one failed request'): self.cli('reboot-board')
        first = self.result()
        self.assertEqual(first['instances'][other['id']]['result'], 'graceful')
        self.assertEqual(first['instances'][z['id']]['result'], 'forced')
        fail = False
        self.cli('reboot-board')
        second = self.result()
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        self.assertEqual(set(second['already_stopped']), {other['id'], z['id']})
        for result in (first, second):
            saved = self.p.runtime / ('stop-attempt-' + result['attempt_id'] + '.json')
            self.assertEqual(json.loads(saved.read_text()), result)
        self.assertEqual(self.controls().count(['xl', 'shutdown', '2']), 1)
        self.assertEqual(self.controls().count(['xl', 'destroy', '3']), 1)
        self.assertNotIn(['xl', 'destroy', '1'], self.controls())

    def test_new_domain_between_observations_blocks_success_and_storage_cleanup(self):
        self.live(self.linux, 7)
        count = 0
        def command(args, **kw):
            nonlocal count
            if args == ['xl', 'list', '-v']:
                count += 1
                if count == 2:
                    other = record('debian-dev')
                    self.xen.rows.append(domain(other, self.directory / 'root.raw', 22))
            return self.xen(args, **kw)
        self.p.command = command
        with self.assertRaisesRegex(vm.PlatformError, 'unexpected'): self.cli('reboot-board')
        self.assertFalse(any(c[0] == 'systemctl' for c in self.xen.calls))

    def test_final_checks_reject_new_domain_before_backend_or_storage_cleanup(self):
        for phase in ('before-backends', 'before-storage'):
            with self.subTest(phase=phase):
                self.xen.rows = []; self.xen.calls = []; self.live(self.linux, 7)
                count = 0
                def command(args, **kw):
                    nonlocal count
                    if args == ['xl', 'list', '-v']:
                        count += 1
                        if phase == 'before-backends' and count == 5:
                            self.xen.rows.append(domain(record('debian-dev'), self.directory / 'root.raw', 22))
                    result = self.xen(args, **kw)
                    if phase == 'before-storage' and args[:3] == ['systemctl', 'stop', 'perseus-vms.service']:
                        self.xen.rows.append(domain(record('debian-dev'), self.directory / 'root.raw', 22))
                    return result
                self.p.command = command
                with self.assertRaisesRegex(vm.PlatformError, 'unexpected'): self.cli('poweroff')
                self.assertNotIn(['systemctl', 'stop', 'srv-perseus.mount'], self.xen.calls)
                self.assertNotIn(['systemctl', 'poweroff'], self.xen.calls)

    def test_observation_each_command_uses_remaining_original_monotonic_deadline(self):
        self.live(self.linux, 7); self.xen.hang_shutdown = True
        ticks = self.fake_time(); observed = []
        def command(args, **kw):
            if args == ['xl', 'list', '-v']:
                observed.append(kw['timeout'])
                ticks[0] += 0.5
            return self.xen(args, **kw)
        self.p.command = command
        with self.assertRaises(vm.PlatformError), redirect_stdout(io.StringIO()):
            self.p.stop_many([self.linux], timeout=2)
        self.assertEqual(observed, [2, 1.5])
        self.assertEqual(ticks[0], 2)
        self.assertEqual(self.controls(), [['xl', 'shutdown', '7']])

    def test_blocked_state_is_observed_without_recovery_and_becomes_stopped_after_removal(self):
        self.live(self.linux, 7, '-b----'); self.xen.hang_shutdown = True
        ticks = [0]; self.p.clock = lambda: ticks[0]
        def sleep(seconds):
            self.assertEqual(self.result()['instances'][self.linux['id']]['xen_state'], 'blocked')
            ticks[0] += seconds
            self.xen.rows = []
        self.p.sleep = sleep
        self.cli('stop', self.linux['id'])
        self.assertEqual(self.result()['instances'][self.linux['id']]['xen_state'], 'stopped')
        self.assertEqual(self.controls(), [['xl', 'shutdown', '7']])

    def test_general_domains_still_refuses_missing_live_configuration_before_any_request(self):
        self.live(self.linux, 7); self.xen.omit_config = True
        with self.assertRaisesRegex(vm.PlatformError, 'omitted'): self.cli('reboot-board')
        self.assertFalse(self.controls())
        self.assertFalse(any(c == ['xl', 'list', '-v'] for c in self.xen.calls))


del AcceptanceTest
