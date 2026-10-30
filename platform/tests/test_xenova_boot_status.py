"""제품 부팅 표시 모델·best effort 생산자·격리된 입력 경계를 검사한다."""
import configparser
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

FILES = Path(__file__).resolve().parents[1]/'yocto/meta-xenova/recipes-core/xenova-platform/files'
sys.path.insert(0, str(FILES))
import xenova_boot_status as status
from xenova_boot_progress import Progress
import test_xenova_boot_boundaries as boot_tests

HOST = '10000000-1111-4222-8333-123456789abc'
INV = 'a'*32


def units():
    return {unit: {'Id': unit, 'LoadState': 'loaded', 'ActiveState': 'active',
                   'SubState': 'exited', 'Result': 'success', 'InvocationID': INV,
                   'InactiveExitTimestampMonotonic': '1000000'} for _, _, unit in status.STAGES}


class BootStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows = units()

    def progress(self, kind, phase='complete', **facts):
        return Progress(kind, self.root, clock=lambda: 5, host=HOST, invocation=INV).emit(phase, **facts)

    def read(self, now=10000):
        return status.snapshot(self.rows, HOST, now, self.root)

    def stage(self, value, key):
        return next(s for s in value['stages'] if s['key'] == key)

    def test_first_install_existing_verification_and_cpu_legacy_are_distinct(self):
        for mode in ('initial-registration-resume', 'existing-install-verification'):
            self.assertTrue(self.progress('registration', mode=mode))
            self.assertEqual(self.stage(self.read(), 'registration')['mode'], mode)
        self.progress('cpu', configured=False)
        self.assertIn('compatibility', self.stage(self.read(), 'cpu')['reason'])
        self.progress('cpu', configured=True, policy_revision=2)
        self.assertIn('not re-probed', self.stage(self.read(), 'cpu')['reason'])

    def test_delayed_does_not_mean_failed_and_later_success_is_normal(self):
        row = self.rows['xenova-first-boot.service']
        row.update(ActiveState='activating', SubState='start')
        self.progress('registration', phase='asset-verification')
        value = self.read(30000)
        item = self.stage(value, 'registration')
        self.assertEqual(item['state'], 'delayed')
        self.assertEqual(item['elapsed_ms'], 29000)
        self.assertIn('last change 25s ago', status.render(value))
        row.update(ActiveState='active', SubState='exited')
        self.assertEqual(self.stage(self.read(31000), 'registration')['state'], 'complete')

    def test_storage_xen_cpu_failure_defers_required_downstream_without_guess(self):
        for unit in ('srv-xenova.mount', 'xenova-xen-ready.service', 'xenova-cpu-prepare.service'):
            with self.subTest(unit=unit):
                rows = units()
                rows[unit].update(ActiveState='failed', Result='timeout', ExecMainStatus='1')
                rows['xenova-vms.service'].update(ActiveState='inactive', Requires=unit)
                value = status.snapshot(rows, HOST, 10000, self.root)
                self.assertEqual(self.stage(value, 'autostart')['state'], 'deferred')
                self.assertIn(unit, self.stage(value, 'autostart')['reason'])
                self.assertIn('systemd result=timeout', status.render(value))
                self.assertNotIn('hardware broken', status.render(value))

    def test_bridge_and_toolstack_dependency_failure_are_observed_without_new_probe(self):
        self.rows['xenova-bridge.service'].update(ActiveState='failed',Result='exit-code')
        self.rows['xenova-dhcp.service'].update(ActiveState='inactive',Requires='xenova-bridge.service')
        self.rows['xenstored.service']={'ActiveState':'failed'}
        self.rows['xenova-xen-ready.service'].update(ActiveState='inactive',Requires='xenstored.service')
        self.rows['xenova-vms.service'].update(ActiveState='inactive',Requires='xenova-xen-ready.service')
        value=self.read()
        self.assertEqual(self.stage(value,'bridge')['state'],'failed')
        self.assertIn('xenova-bridge.service',self.stage(value,'network')['reason'])
        self.assertIn('xenstored.service',self.stage(value,'autostart')['reason'])
        self.assertEqual(self.stage(value,'autostart')['state'],'deferred')

    def test_partial_verified_excluded_failed_uncertain_are_counted_separately(self):
        self.rows['xenova-vms.service'].update(ActiveState='failed', Result='exit-code')
        self.progress('autostart', vms=[{'vm_id':str(i), 'name':name, 'state':s, 'selected':s != 'excluded', 'code':'TEST'} for i,name,s in
                      ((1,'한글 이름','verified'), (2,'skip','excluded'), (3,'bad','failed'), (4,'waiting','execution-start'))])
        value = self.read()
        self.assertEqual(value['vm_counts'], {'verified':1, 'excluded':1, 'failed':1, 'not-started':0, 'uncertain':1, 'selected':3})
        self.assertIn('VM execution verified 1/3', status.render(value))
        self.assertTrue(all(v['app_readiness'] == 'unconnected' and v['os_readiness'] == 'unobserved' for v in value['vms']))

    def test_old_host_invocation_batch_and_missing_or_partial_file_are_not_success(self):
        self.assertIsNone(self.read()['vm_counts'])
        self.progress('autostart', vms=[])
        path = self.root/'boot-progress/autostart.json'
        original = json.loads(path.read_text())
        for key,value in (('host_boot_id','old'), ('invocation_id','b'*32), ('batch_id','bad'), ('updated_monotonic_ms',0)):
            with self.subTest(key=key):
                path.write_text(json.dumps(original|{key:value}))
                self.assertIsNone(self.read()['vm_counts'])
        path.write_text('{"partial":')
        self.assertIn('incomplete', self.stage(self.read(), 'autostart')['progress_issue'])
        path.unlink()
        path.symlink_to(self.root/'nope')
        self.assertIsNone(self.read()['vm_counts'])

    def test_no_external_network_or_app_dependency_and_no_fake_active_validation(self):
        self.rows['xenova-dom0-manager.service']['SubState'] = 'running'
        value = self.read()
        text = status.render(value)
        self.assertIn('service active; functionality not probed', status.render(value, details=True))
        self.assertIn('App readiness: unconnected', text)
        self.assertFalse(any('wifi' in u or 'mqtt' in u or 'timesync' in u for _,_,u in status.STAGES))

    def test_narrow_ascii_and_escape_names_do_not_execute_issue_directives(self):
        name = '주행 제어 A\n\\e[31m\x1b[2J\r'+'x'*300
        self.progress('autostart', vms=[{'vm_id':'2990cc46-4c9b-4445-b0fb-3da3f8e85298','name':name,'state':'failed','code':'OBSERVATION_UNAVAILABLE'}])
        rendered = status.render(self.read(), width=40, ascii_only=True)
        self.assertTrue(rendered.isascii())
        self.assertNotIn('\\', rendered)
        self.assertNotIn('\x1b', rendered)
        self.assertIn('2990cc46-4c9b-4445-b0fb-3da3f8e85298', rendered)
        self.assertIn('journalctl -b -u', rendered)
        self.assertIn('OBSERVATION_UNAVAILABLE', rendered)

    def test_failure_history_survives_recovery_but_not_new_host(self):
        self.rows['xenova-vms.service'].update(ActiveState='failed', Result='exit-code')
        old = status.history_update(self.read(), None)
        self.rows['xenova-vms.service'].update(ActiveState='active', InvocationID='b'*32)
        recovered = status.history_update(self.read(), old)
        self.assertEqual(self.stage(recovered,'autostart')['state'], 'complete')
        self.assertEqual(len(recovered['failure_history']),1)
        self.assertEqual(status.history_update(recovered|{'host_boot_id':'new'},old)['failure_history'], [])

    def test_incomplete_systemctl_response_is_not_success_but_known_not_found_is_valid(self):
        names=['one.service','two.service']
        class Reply:
            def __init__(self, argv, *, stdout, **kwargs):
                self.output=stdout
            def wait(self, timeout):
                self.output.write(payload.encode())
                self.output.flush()
                return 4
        for payload,expected in (("Id=one.service\nLoadState=loaded\n\n",'systemd-query-partial'),
                                 ("Id=one.service\nLoadState=not-found\n\nId=two.service\nLoadState=not-found\n\n",None)):
            with self.subTest(expected=expected),patch.object(status.subprocess,'Popen',Reply):
                rows,error=status.systemd_units(names)
                self.assertEqual(error,expected)
                self.assertIn('one.service',rows)

    def test_query_failure_remains_unknown_and_has_failure_exit(self):
        with patch.object(status, 'collect', return_value=status.snapshot({}, HOST, 1, self.root, 'systemd-query-timeout')):
            with redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(status.main(['--json']),1)
            self.assertEqual(json.loads(stream.getvalue())['issues'], ['systemd-query-timeout'])

    def test_auxiliary_write_failure_does_not_raise_or_print(self):
        with patch('xenova_boot_progress.os.replace', side_effect=OSError('unavailable')):
            with redirect_stdout(io.StringIO()) as stream:
                self.assertFalse(self.progress('storage'))
            self.assertEqual(stream.getvalue(), '')

    def test_unchanged_poll_does_not_reload_prompt_or_repeat_output(self):
        issue,reload = self.root/'issue/boot.issue',self.root/'reload'
        reader = lambda _: (self.rows, None)
        status.refresh(self.root, issue, reload, reader=reader, host=HOST, clock=lambda:10)
        mtime = issue.stat().st_mtime_ns
        status.refresh(self.root, issue, reload, reader=reader, host=HOST, clock=lambda:15)
        self.assertEqual(issue.stat().st_mtime_ns, mtime)
        self.assertIsNotNone(status.read_json(self.root/'boot-display.json')[0])

    def test_normal_selection_pending_fits_console_without_listing_unselected_vms(self):
        for key in ('cpu', 'autostart', 'admission', 'management'):
            row=next(u for k, _, u in status.STAGES if k==key)
            self.rows[row].update(ActiveState='inactive')
        self.rows['xenova-first-boot.service'].update(ActiveState='activating',SubState='start')
        self.progress('autostart',vms=[{'vm_id':str(i),'name':'not-selected-'+str(i),'state':'waiting'} for i in range(10)])
        value=self.read();text=status.render(value,ascii_only=True,width=80)
        self.assertLessEqual(len(text.splitlines()),9)
        self.assertIn('[WAIT; reason unconfirmed]',text)
        self.assertIn('selection unknown',text)
        self.assertNotIn('not-selected-',text)
        self.assertIn('not-selected-9',status.render(value,details=True))

    def test_compact_selection_keeps_current_target_and_unselected_failure(self):
        self.rows['xenova-vms.service'].update(ActiveState='activating',SubState='start')
        self.progress('autostart',phase='execution-start-and-verification',vms=[
            {'vm_id':'1','name':'current-target','state':'execution-start','selected':True},
            {'vm_id':'2','name':'unknown-target','state':'waiting'},
            {'vm_id':'3','name':'failed-before-selection','state':'failed','code':'OBSERVATION_UNAVAILABLE'}])
        text=status.render(self.read())
        self.assertIn('current-target',text)
        self.assertNotIn('unknown-target',text)
        self.assertIn('failed-before-selection',text)
        self.assertIn('OBSERVATION_UNAVAILABLE',text)

    def test_basic_wording_is_for_operator_and_details_keep_internal_phases(self):
        self.progress('registration',mode='existing-install-verification')
        self.progress('cpu',configured=True,policy_revision=1)
        self.rows['xenova-vms.service'].update(ActiveState='activating',SubState='start')
        self.progress('autostart',phase='execution-start-and-verification',vms=[])
        basic=status.render(self.read());details=status.render(self.read(),details=True)
        self.assertTrue(basic.startswith('Xenova startup\n'))
        self.assertIn('CPU policy: prepared for this boot',basic)
        self.assertIn('Registration: existing installation',basic)
        self.assertNotIn('invocation',basic)
        self.assertNotIn('execution-start-and-verification',basic)
        self.assertIn('execution-start-and-verification',details)
        self.assertIn('current affinity not re-probed',details)


class BootProducerTest(unittest.TestCase):
    """실제 Platform/FakeXen에서 기존 작업 성공과 표시 실패가 독립적인지 검사한다."""
    setUp = boot_tests.AutostartBudgetTest.setUp
    results = boot_tests.AutostartBudgetTest.results
    run_boot = boot_tests.AutostartBudgetTest.run_boot

    def test_display_failure_does_not_change_autostart_or_authoritative_results(self):
        with patch.object(Progress, 'emit', return_value=False), patch.object(self.p, 'prepare_dhcp'):
            self.run_boot(deadline=20)
        self.assertEqual(len(self.xen.rows),2)
        self.assertTrue(all(v['result']=='running' for v in self.results().values()))

    def test_missing_or_crashed_display_module_does_not_break_boot(self):
        with patch.dict(sys.modules, {'xenova_boot_progress':None}), patch.object(self.p, 'prepare_dhcp'):
            self.run_boot(deadline=20)
        self.assertEqual(len(self.xen.rows),2)
        with patch.object(Progress, 'emit', side_effect=RuntimeError('display broken')), patch.object(self.p, 'prepare_dhcp'):
            self.run_boot(deadline=20)
        self.assertEqual(len(self.xen.rows),2)

    def test_batch_restart_changes_identity_and_exposes_verified_execution_only(self):
        with patch.dict(os.environ, {'INVOCATION_ID':INV}), patch.object(self.p,'prepare_dhcp'):
            self.run_boot(deadline=20)
            path = self.p.runtime/'boot-progress/autostart.json'
            before = json.loads(path.read_text())
            self.run_boot(deadline=20)
            after = json.loads(path.read_text())
        self.assertNotEqual(before['batch_id'],after['batch_id'])
        self.assertEqual(after['invocation_id'],INV)
        self.assertEqual(len(self.xen.rows),2)
        self.assertTrue(all(v['state']=='verified' for v in after['vms']))



class BootDisplayUnitTest(unittest.TestCase):
    def test_boot_diagnostics_never_dump_all_chosen_properties(self):
        boot=(FILES.parents[2]/'recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in').read_text()
        self.assertNotIn('\nfdt print /chosen\n',boot)
        prints=[line.strip() for line in boot.splitlines() if line.strip().startswith('fdt print /chosen')]
        self.assertEqual(prints,['fdt print /chosen xen,xen-bootargs','fdt print /chosen xen,dom0-bootargs'])

    def test_indicator_has_no_core_dependency_or_stop_propagation(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(FILES/'xenova-boot-status.service')
        self.assertEqual(config['Unit']['DefaultDependencies'],'no')
        self.assertEqual(config['Unit']['After'],'systemd-remount-fs.service')
        self.assertEqual(config['Unit']['Conflicts'],'shutdown.target')
        self.assertEqual(config['Install']['WantedBy'],'sysinit.target')
        self.assertNotIn('ExecStop',config['Service'])
        self.assertEqual(config['Service']['Restart'],'no')
        for unit in FILES.glob('*.service'):
            if unit.name != 'xenova-boot-status.service':
                self.assertNotIn('xenova-boot-status',unit.read_text())

    def test_recipe_installs_enables_indicator_without_runtime_dependency_growth(self):
        recipe = FILES.parent/'xenova-platform_1.0.bb'
        text = recipe.read_text()
        for name in ('xenova_boot_progress.py','xenova_boot_status.py','xenova-boot-status.service','xenova-boot-status'):
            self.assertIn('file://'+name,text)
        self.assertIn('SYSTEMD_SERVICE:${PN} = "xenova-boot-status.service ',text)
        self.assertNotIn('kmscon',text)
        self.assertNotIn('plymouth',text)
        self.assertNotIn(b'\r', (FILES/'xenova-boot-status').read_bytes())
        source=(FILES/'xenova_boot_status.py').read_text()
        self.assertNotIn('Manager(',source)
        self.assertNotIn('operation_lock(',source)
        self.assertNotIn("['xl'",source)

    def test_selection_unknown_and_record_stall_are_never_fabricated_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp)
            Progress('autostart',runtime,clock=lambda:2,host=HOST,invocation=INV).emit('targets-discovered',
                vms=[{'vm_id':'2990cc46-4c9b-4445-b0fb-3da3f8e85298','name':'pending','state':'waiting'}])
            rows=units();rows['xenova-vms.service'].update(ActiveState='activating',SubState='start')
            value=status.snapshot(rows,HOST,90000,runtime)
            self.assertIsNone(value['vm_counts']['selected'])
            self.assertEqual(value['vms'][0]['state'],'waiting')
            self.assertIn('last change 88s ago',status.render(value))
            self.assertNotIn('50%',status.render(value))

    def test_entrypoint_sanitizes_fatal_observation_error(self):
        with patch.object(status,'main',side_effect=OSError('do not print raw configuration')):
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()) as stream:
                self.assertEqual(status.entrypoint(),1)
        self.assertNotIn('raw configuration',stream.getvalue())

if __name__ == '__main__':
    unittest.main()
