"""부팅/종료의 실제 unit 그래프와 autostart 공유 예산을 검사한다.

실제 Platform/Execution/CPU를 사용하며 Xen·시계·작은 자산만 대역이다.
로컬 systemd loader 검사는 서비스를 실행하거나 전원 부팅을 대체하지 않는다.
"""
import configparser
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import FILES
import test_xenova_cpu as cpu_fixture
import test_xenova_default_deployment as deployment_fixture
from test_xenova_platform import FakeXen, fixture, record
import xenova_vm as vm
from xenova_runtime import operation_lock


class AutostartBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        a, b = record(), record('debian-dev')
        self.records = {r['id']: r for r in (a, b)}
        for r in self.records.values():
            fixture(self.base, r)
        self.xen = FakeXen(self.base, self.records)
        self.ticks = 10.0
        self.p = vm.Platform(self.base, self.base / 'run', self.xen,
                             lambda seconds: setattr(self, 'ticks', self.ticks + seconds),
                             lambda: self.ticks)
        self.p.boot_id_reader = lambda: '10000000-1111-4222-8333-123456789abc'
        storage = patch.object(vm, 'storage_check')
        storage.start()
        self.addCleanup(storage.stop)

    def results(self):
        return json.loads((self.p.runtime / 'autostart-results.json').read_text())

    def run_boot(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            self.p.autostart(self.records, list(self.records), **kwargs)

    def test_all_vm_launches_and_network_preparation_share_one_deadline(self):
        deadlines = []
        original = self.p.start
        def start(record, *, deadline):
            deadlines.append(deadline)
            self.ticks += 2
            return original(record, deadline=deadline)
        with patch.object(self.p, 'prepare_dhcp') as dhcp, patch.object(self.p, 'start', side_effect=start):
            self.run_boot(deadline=20)
        self.assertEqual(deadlines, [20, 20])
        self.assertEqual([call.kwargs['deadline'] for call in dhcp.call_args_list], [20, 20])
        self.assertEqual(len(self.xen.rows), 2)
        self.assertTrue(all(v['deadline_monotonic_ms'] == 20000 for v in self.results().values()))

    def test_elapsed_batch_retains_completed_guest_and_does_not_attempt_next(self):
        original = self.p.start
        def start(record, *, deadline):
            result = original(record, deadline=deadline)
            self.ticks = deadline
            return result
        with patch.object(self.p, 'prepare_dhcp'), patch.object(self.p, 'start', side_effect=start):
            with self.assertRaisesRegex(vm.PlatformError, 'pending guests not started'):
                self.run_boot(deadline=20)
        a, b = self.records
        self.assertEqual(self.results()[a]['result'], 'running')
        self.assertEqual(self.results()[b]['result'], 'not-started')
        self.assertFalse(self.results()[b]['start_invoked'])
        self.assertEqual(len(self.xen.rows), 1)
        self.assertFalse(any(c[:2] in (['xl', 'shutdown'], ['xl', 'destroy']) for c in self.xen.calls))
        self.assertIsNone(self.p.execution().intent(self.records[b]))

    def test_deadline_expired_before_decision_never_changes_intent_or_admission(self):
        with patch.object(self.p.execution(), 'boot_decision', side_effect=AssertionError('must not decide')):
            with self.assertRaisesRegex(vm.PlatformError, 'batch deadline exceeded'):
                self.run_boot(deadline=self.ticks)
        self.assertTrue(all(v['result'] == 'not-started' for v in self.results().values()))
        self.assertFalse(self.xen.rows)
        for r in self.records.values():
            self.assertIsNone(self.p.execution().intent(r))
            self.assertIsNone(self.p.execution().admission(r))

    def test_preparation_consuming_last_budget_does_not_invoke_start(self):
        def prepare(records, *, deadline):
            self.ticks = deadline
        with patch.object(self.p, 'prepare_dhcp', side_effect=prepare), patch.object(self.p, 'start') as start:
            with self.assertRaises(vm.PlatformError):
                self.run_boot(deadline=20)
        start.assert_not_called()
        self.assertTrue(all(v['result'] == 'not-started' for v in self.results().values()))

    def test_timeout_after_create_is_failed_attempt_not_not_started(self):
        original = self.p.start
        def start(record, *, deadline):
            original(record, deadline=deadline)
            self.ticks = deadline
            raise vm.PlatformError('submitted create; final validation timed out')
        with patch.object(self.p, 'prepare_dhcp'), patch.object(self.p, 'start', side_effect=start):
            with self.assertRaises(vm.PlatformError):
                self.run_boot(deadline=20)
        a, b = self.records
        self.assertEqual(self.results()[a]['result'], 'failed')
        self.assertTrue(self.results()[a]['start_invoked'])
        self.assertEqual(self.results()[b]['result'], 'not-started')
        self.assertEqual(len(self.xen.rows), 1)

    def test_service_retry_reuses_running_domains_without_new_identity_or_create(self):
        before = copy.deepcopy(self.records)
        with patch.object(self.p, 'prepare_dhcp'):
            self.run_boot()
            first = {r['uuid']: self.p.execution().run_record(r)['execution_id'] for r in self.records.values()}
            count = sum(c[:2] == ['xl', 'create'] for c in self.xen.calls)
            self.run_boot()
        self.assertEqual(sum(c[:2] == ['xl', 'create'] for c in self.xen.calls), count)
        self.assertEqual(first, {r['uuid']: self.p.execution().run_record(r)['execution_id'] for r in self.records.values()})
        self.assertEqual(self.records, before)

    def test_main_deadline_starts_before_registry_and_xen_readiness(self):
        defaults = self.base / 'defaults.json'
        defaults.write_text(json.dumps({'instances': list(self.records.values())}))
        self.p.defaults_path = defaults
        def records(**kwargs):
            self.ticks += 5
            return self.records
        with patch.object(vm, 'Platform', return_value=self.p), \
             patch.object(self.p, 'records', side_effect=records), \
             patch.object(self.p, 'check_xen') as check, \
             patch.object(self.p, 'autostart') as boot:
            vm.main(['autostart'])
        self.assertEqual(check.call_args.kwargs['deadline'], 10 + vm.AUTOSTART_TIMEOUT)
        self.assertEqual(boot.call_args.kwargs['deadline'], 10 + vm.AUTOSTART_TIMEOUT)

    def test_storage_failure_prevents_registry_fallback_and_all_guest_starts(self):
        output = io.StringIO()
        with patch.object(vm, 'Platform', return_value=self.p), \
             patch.object(vm, 'storage_check', side_effect=vm.PlatformError('data mount unavailable')), \
             patch.object(self.p, 'autostart') as boot, redirect_stdout(output):
            with self.assertRaises(SystemExit) as failure:
                vm.main(['autostart'])
        self.assertEqual(failure.exception.code, 4)
        error = json.loads(output.getvalue())['error']
        self.assertEqual(error['code'], 'ARTIFACT_INVALID')
        self.assertIn('data mount unavailable', error['message'])
        boot.assert_not_called()
        self.assertFalse(self.xen.calls)

    def test_standalone_xen_ready_uses_its_own_unit_budget(self):
        with patch.object(vm, 'Platform', return_value=self.p), patch.object(self.p, 'check_xen') as check:
            vm.main(['check-xen'])
        self.assertEqual(check.call_args.kwargs['deadline'], self.ticks + 25)

    def test_autostart_lock_contention_preserves_immediate_busy_contract(self):
        entered = self.ticks
        output = io.StringIO()
        with operation_lock(self.p.runtime), patch.object(vm, 'Platform', return_value=self.p), redirect_stdout(output):
            with self.assertRaises(SystemExit) as failure:
                vm.main(['autostart'])
        self.assertEqual(failure.exception.code, 3)
        error = json.loads(output.getvalue())['error']
        self.assertEqual(error['code'], 'OPERATION_IN_PROGRESS')
        self.assertTrue(error['retryable'])
        self.assertEqual(self.ticks, entered)
        self.assertFalse(self.xen.calls)

    def test_service_restart_does_not_override_same_host_explicit_user_stop(self):
        with patch.object(self.p, 'prepare_dhcp'):
            self.run_boot()
            target = next(iter(self.records.values()))
            with patch.object(vm.os, 'sync'):
                self.p.request_control('stop', target, idempotency_key='explicit-stop-before-service-retry')
            count = sum(c[:2] == ['xl', 'create'] for c in self.xen.calls)
            self.run_boot()
        self.assertEqual(sum(c[:2] == ['xl', 'create'] for c in self.xen.calls), count)
        self.assertEqual(self.results()[target['id']]['result'], 'skipped')
        self.assertEqual(self.p.execution().intent(target)['desired_state'], 'stopped')

    def test_pending_legacy_boot_choice_keeps_autostart_without_rewriting_intent(self):
        for r in self.records.values():
            self.p.execution().initialize(r, 'stopped', 'create')
        before = {r['uuid']: self.p.execution().intent(r) for r in self.records.values()}
        with patch.object(self.p, 'prepare_dhcp'):
            self.run_boot()
        self.assertEqual(len(self.xen.rows), 2)
        for r in self.records.values():
            self.assertEqual(self.p.execution().intent(r), before[r['uuid']])
            self.assertEqual(self.results()[r['id']]['result'], 'running')
            admission = self.p.execution().admission(r)
            self.assertEqual(admission['reason'], 'BOOT_POLICY_PENDING')
            self.assertFalse(admission['allowed'])


class FirstRegistrationBoundaryTest(unittest.TestCase):
    setUp = deployment_fixture.DefaultDeploymentTest.setUp
    current_board = deployment_fixture.DefaultDeploymentTest.current_board

    def test_resume_uses_durable_uuid_plan_then_each_boot_only_verifies_registration(self):
        board, _ = self.current_board()
        save = board.p.save
        state = board.data / '.first-install-state.json'
        def interrupted(path, value):
            save(path, value)
            if path == state and value.get('phase') == 'identities-planned':
                raise ValueError('power interruption after durable plan')
        with patch.object(board.p, 'save', side_effect=interrupted):
            with self.assertRaisesRegex(ValueError, 'durable plan'):
                board.p.register()
        planned = json.loads(state.read_text())['identities']
        board.p.register()
        records = vm.load_records(board.data)
        expected = {key: value['uuid'] for key, value in planned.items()}
        self.assertEqual({key: r['uuid'] for key, r in records.items()}, expected)
        before = {p.relative_to(board.data): p.read_bytes()
                  for p in (board.data / 'instances').rglob('*') if p.is_file()}
        board.p.register()
        self.assertEqual(before, {p.relative_to(board.data): p.read_bytes()
                                 for p in (board.data / 'instances').rglob('*') if p.is_file()})

    def test_registration_rerun_preserves_user_next_boot_choice_and_assets(self):
        board, _ = self.current_board()
        board.p.register()
        path = board.data / 'instances/debian-dev/instance.json'
        changed = json.loads(path.read_text())
        changed['autostart'] = False
        path.write_text(json.dumps(changed))
        before = {p.relative_to(board.data): p.read_bytes()
                  for p in (board.data / 'instances').rglob('*') if p.is_file()}
        board.p.register()
        self.assertEqual(before, {p.relative_to(board.data): p.read_bytes()
                                 for p in (board.data / 'instances').rglob('*') if p.is_file()})


class BootCPUReadinessTest(unittest.TestCase):
    setUp = cpu_fixture.CPUTest.setUp
    command = cpu_fixture.CPUTest.command
    sleep = cpu_fixture.CPUTest.sleep
    ok = cpu_fixture.CPUTest.ok
    fails = cpu_fixture.CPUTest.fails
    spec = cpu_fixture.CPUTest.spec
    policy = cpu_fixture.CPUTest.policy
    prepare = cpu_fixture.CPUTest.prepare
    activate = cpu_fixture.CPUTest.activate

    def boot_record(self):
        self.ok(self.m.call('autostart-set', {'vm_id':self.z['uuid'], 'value':'on'}, execute=True))
        r = self.p.records(validate_assets=False)[self.z['id']]
        self.p.execution().accept(r, {'vm_id': r['uuid'], 'action': 'start', 'force': False},
                                  'boot-boundary-start', source='boot-policy')
        return r

    def boot(self, record):
        with redirect_stdout(io.StringIO()):
            self.p.autostart({record['id']: record}, [], deadline=self.ticks + 25)

    def test_configured_policy_requires_actual_current_host_readiness_before_create(self):
        self.ok(self.policy())
        r = self.boot_record()
        with self.assertRaisesRegex(vm.PlatformError, 'prepare this host boot'):
            self.boot(r)
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_prior_boot_receipt_is_rejected_even_when_dom0_mask_still_matches(self):
        self.activate()
        old = self.host
        self.host = '20000000-1111-4222-8333-123456789abc'
        r = self.boot_record()
        with self.assertRaisesRegex(vm.PlatformError, 'prepare this host boot'):
            self.boot(r)
        self.assertNotEqual(self.p.execution().host, old)
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_service_active_or_receipt_does_not_hide_external_dom0_affinity_change(self):
        self.activate()
        self.affinities[0][0]['pcpus'] = [0, 1, 2, 3]
        r = self.boot_record()
        with self.assertRaises(vm.PlatformError):
            self.boot(r)
        self.assertFalse(any(c[:2] == ['xl', 'create'] for c in self.xen.calls))

    def test_unconfigured_install_keeps_legacy_autostart_without_cpu_preparation(self):
        r = self.boot_record()
        self.boot(r)
        self.assertEqual(len(self.xen.rows), 1)
        self.assertFalse(any(c[:2] == ['xl', 'vcpu-pin'] for c in self.xen.calls))


class BootUnitBoundaryTest(unittest.TestCase):
    def unit(self, name, directory=FILES):
        result = configparser.ConfigParser(interpolation=None, strict=False)
        result.optionxform = str
        result.read(directory / name)
        return result

    def test_dependency_graph_preserves_storage_registration_toolstack_and_reverse_shutdown(self):
        files = {p.name: self.unit(p.name) for p in FILES.glob('*.service')}
        files['srv-xenova.mount'] = self.unit('srv-xenova.mount')
        vms, admission, stop = (files[n] for n in ('xenova-vms.service', 'xenova-host-admission.service', 'xenova-vm-shutdown.service'))
        required = {'srv-xenova.mount', 'xenova-first-boot.service', 'xenova-xen-ready.service', 'xenova-vm-shutdown.service'}
        self.assertTrue(required <= set(vms['Unit']['Requires'].split()))
        self.assertTrue(required <= set(vms['Unit']['After'].split()))
        self.assertIn('xenova-vms.service', admission['Unit']['After'].split())
        self.assertIn('xenova-vm host-quiesce', admission['Service']['ExecStop'])
        self.assertIn('xenova-vm shutdown-all', vms['Service']['ExecStop'])
        self.assertIn('xenova-vm shutdown-all', stop['Service']['ExecStop'])
        for prerequisite in ('srv-xenova.mount', 'xenstored.service', 'xenconsoled.service', 'xen-init-dom0.service', 'xen-qemu-dom0-disk-backend.service'):
            self.assertIn(prerequisite, stop['Unit']['After'].split())
        self.assertIn('xenova-data-prepare.service', files['srv-xenova.mount']['Unit']['Requires'].split())
        self.assertIn('srv-xenova.mount', files['xenova-first-boot.service']['Unit']['Requires'].split())

    def test_cpu_ordering_does_not_propagate_cpu_service_stop_into_guests(self):
        vms = self.unit('xenova-vms.service')
        self.assertIn('xenova-cpu-prepare.service', vms['Unit']['Wants'].split())
        self.assertIn('xenova-cpu-prepare.service', vms['Unit']['After'].split())
        for relation in ('Requires', 'BindsTo', 'PartOf', 'Requisite'):
            self.assertNotIn('xenova-cpu-prepare.service', vms['Unit'].get(relation, '').split())
        cpu = self.unit('xenova-cpu-prepare.service')
        self.assertNotIn('ExecStop', cpu['Service'])

    def test_autostart_inner_budget_and_shutdown_budget_fit_distinct_unit_limits(self):
        vms = self.unit('xenova-vms.service')
        limit = float(vms['Service']['TimeoutStartSec'].rstrip('s'))
        self.assertGreater(limit, vm.AUTOSTART_TIMEOUT)
        self.assertEqual(limit, 150)
        self.assertGreater(float(vms['Service']['TimeoutStopSec'].rstrip('s')), 90)
        self.assertEqual(self.unit('xenova-first-boot.service')['Service']['TimeoutStartSec'], '300s')

    def test_actual_loader_accepts_graph_and_rejects_injected_cycle(self):
        from check_xenova_first_install_units import verify
        with tempfile.TemporaryDirectory() as path:
            base = Path(path)
            for name in ('normal', 'cycle'):
                (base / name).mkdir()
            normal = verify(base / 'normal')
            cycle = verify(base / 'cycle', cycle=True)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertNotEqual(cycle.returncode, 0)
        self.assertIn('cycle', cycle.stderr.lower())


if __name__ == '__main__':
    unittest.main()
