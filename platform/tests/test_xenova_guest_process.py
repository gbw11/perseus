"""VM 실행 scope의 소유권·원 문맥 전달·정리 경계를 검사한다. 실제 PID1 시험은 별도다."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
from first_install_contract import uuid4
import xenova_guest_process as guest
from xenova_runtime import PlatformError


class GuestProcessTest(unittest.TestCase):
    def setUp(self):
        self.f=foundation.FoundationTest('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.p,self.r=self.f.p,self.f.r

    def test_launch_context_uses_accepted_revision_not_latest_intent(self):
        self.f.start()
        launch=self.p.execution().run_record(self.r)
        launch={**launch,'process_scope':guest.scope_name(self.r['uuid'],launch['execution_id'])}
        before=copy.deepcopy(self.p.execution().intent(self.r))
        self.f.stop()
        command=guest.command(self.p,self.r,90,launch=launch)
        self.assertEqual(command[0:3],['systemd-run','--scope','--quiet'])
        self.assertIn('--property=Before=xenova-vm-shutdown.service',command)
        self.assertEqual(command[command.index('--intent-revision')+1],str(before['intent_revision']))
        reboot=guest.command(self.p,self.r,90,target={'domid':7,'xen_unique_id':'123'},reboot_launch=launch)
        self.assertEqual(reboot[reboot.index('--execution-id')+1],launch['execution_id'])
        self.assertNotIn('systemd-run',reboot)

    def test_scope_owner_cannot_select_another_vm_or_arbitrary_unit(self):
        name=guest.scope_name(self.r['uuid'],uuid4())
        self.assertTrue(guest.owned_scope(self.r['uuid'],name))
        for bad in ('sshd.service','../x.scope',guest.scope_name(uuid4(),uuid4()),None):
            self.assertFalse(guest.owned_scope(self.r['uuid'],bad))

    def test_create_keeps_existing_45_second_subprocess_cap(self):
        with patch.object(self.p,'command',wraps=self.p.command) as command:self.f.start()
        calls=[c for c in command.call_args_list if c.args[0][:2]==['xl','create']]
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].kwargs['timeout'],45)

    def failed_scope(self,reason,*,effect=False):
        """실제 제출 경로의 실패가 raw xl fallback이나 같은 요청 재생성을 만들지 않는다."""
        self.p.process_isolation=True
        command=self.p.command;submitted=[]
        def fail(args,**kwargs):
            if args[0]=='systemd-run':
                submitted.append(list(args))
                if effect:
                    command(['xl','create',str(self.p.runtime/(self.r['id']+'.cfg'))],**kwargs)
                raise PlatformError(reason)
            self.assertNotEqual(args[:2],['xl','create'],'scope isolation bypassed')
            return command(args,**kwargs)
        self.p.command=fail
        result=self.f.call('start',key='failed-scope-start')
        self.f.fails(result,'RECONCILE_REQUIRED')
        self.assertTrue(result['error']['details']['accepted'])
        operation=next(q for q in self.p.execution().requests() if q['operation_id']==result['operation_id'])
        self.assertEqual(operation['phase'],'uncertain')
        self.assertEqual(len(submitted),1)
        self.assertEqual(len(self.f.physical('create')),int(effect))
        replay=self.f.call('start',key='failed-scope-start')
        self.assertEqual(replay['operation_id'],result['operation_id'])
        self.assertEqual(len(submitted),1)
        self.assertEqual(len(self.f.physical('create')),int(effect))
        self.assertEqual(self.f.physical('destroy'),[])
        if effect:self.assertTrue(self.f.xen.rows,'uncertain create effect must be preserved')

    def test_missing_systemd_run_never_bypasses_scope(self):
        self.failed_scope('systemd-run failed: No such file or directory')

    def test_failed_scope_creation_never_falls_back_to_direct_xl(self):
        self.failed_scope('scope creation failed: access denied')

    def test_uncertain_scope_command_with_create_effect_never_recreates(self):
        self.failed_scope('scope command response timed out',effect=True)

    def test_isolated_zephyr_reboot_submits_cooperative_request_without_linux_launch(self):
        self.f.start()
        expected=self.p.resolve_domain(self.r,deadline=90)
        zephyr_record={**self.r,'guest_kind':'zephyr'}
        self.p.process_isolation=True
        with patch.object(self.p,'resolve_domain',return_value=expected),patch.object(guest,'command') as helper:
            self.p.control(zephyr_record,'reboot',expected,deadline=90)
        helper.assert_not_called()
        self.assertEqual(len(self.f.physical('reboot')),1)

    def test_final_validation_time_cannot_authorize_exec_after_original_deadline(self):
        self.f.start();e=self.p.execution();launch=e.run_record(self.r)
        original=self.p.config
        def delayed(record):
            text=original(record);self.f.ticks=91;return text
        with patch.object(self.p,'config',side_effect=delayed):
            with self.assertRaises(foundation.execution.ExecutionError) as caught:e.guard_launch(self.r,launch,90)
        self.assertEqual(caught.exception.error['code'],'DEADLINE_EXCEEDED')

    def test_unsubmitted_abort_never_discards_an_existing_domain(self):
        self.f.start();e=self.p.execution();launch=e.prepare_launch(self.r,deadline=90)
        with self.assertRaises(foundation.execution.ExecutionError):e.abort_unsubmitted_launch(self.r,launch,90)
        self.assertEqual(e.run_record(self.r),launch)

    def test_new_create_gets_new_scope_linux_toolstack_reboot_reuses_existing_scope(self):
        self.f.start()
        e=self.p.execution();previous=e.run_record(self.r)
        previous['process_scope']=guest.scope_name(self.r['uuid'],previous['execution_id'])
        e.write(e._path(e.runtime,'executions',self.r['uuid']),previous)
        self.p.process_isolation=True
        legacy={k:v for k,v in previous.items() if k!='process_scope'}
        e.write(e._path(e.runtime,'executions',self.r['uuid']),legacy)
        legacy_reboot=e.prepare_launch(self.r,deadline=90,reuse_process_scope=True)
        self.assertIsNone(legacy_reboot['process_scope'])
        e.write(e._path(e.runtime,'executions',self.r['uuid']),previous)
        launch=e.prepare_launch(self.r,deadline=90,reuse_process_scope=True)
        self.assertEqual(launch['process_scope'],previous['process_scope'])
        e.write(e._path(e.runtime,'executions',self.r['uuid']),previous)
        fresh=e.prepare_launch(self.r,deadline=90)
        self.assertNotEqual(fresh['process_scope'],previous['process_scope'])

    def test_helper_stop_guard_closes_only_unsubmitted_launch_and_permits_later_start(self):
        self.f.start();self.f.xen.rows.clear()
        self.p.process_isolation=True;e=self.p.execution()
        launch=e.prepare_launch(self.r,deadline=90)
        (self.p.runtime/(self.r['id']+'.cfg')).write_text(self.p.config(self.r))
        original=Path.read_text
        def read(path,*args,**kwargs):
            return '0::/system.slice/'+launch['process_scope']+'\n' if str(path)=='/proc/self/cgroup' else original(path,*args,**kwargs)
        argv=['create','--data',str(self.p.data),'--runtime',str(self.p.runtime),'--vm-id',self.r['uuid'],
              '--host-boot-id',e.host,'--parent-pid',str(os.getppid()),'--deadline-ms','90000',
              '--intent-revision',str(launch['intent_revision']),'--execution-id',launch['execution_id']]
        from xenova_runtime import operation_lock
        with operation_lock(self.p.runtime):
            pending=self.f.call('stop',key='helper-guard-stop')
            self.assertTrue(pending['error']['details']['accepted'])
            with patch.object(foundation.vm,'Platform',return_value=self.p),patch.object(guest.os,'geteuid',return_value=0),patch.object(Path,'read_text',autospec=True,side_effect=read),patch.object(guest,'arm_parent_death'),patch.object(guest.os,'execvp') as execute:
                with self.assertRaises(foundation.execution.ExecutionError):guest.main(argv)
            execute.assert_not_called()
        self.assertTrue(e.run_record(self.r)['launch_aborted_before_submit'])
        self.assertEqual(e.run_record(self.r)['phase'],'stopped')
        self.p.process_isolation=False
        self.f.ok(self.f.call('stop',key='helper-guard-stop'))
        self.f.ok(self.f.call('start',key='after-helper-guard-start'))

    def test_cleanup_requires_absence_and_owned_shutdown_order_and_observes_final_state(self):
        name=guest.scope_name(self.r['uuid'],uuid4())
        calls=[];states=iter(('active','deactivating','inactive'))
        original=self.p.timed_command
        def command(argv,end):
            if argv[0]!='systemctl':return original(argv,end)
            calls.append(argv)
            if 'LoadState' in argv:return 'loaded'
            if 'ActiveState' in argv:return next(states)
            if 'Before' in argv:return 'xenova-vm-shutdown.service shutdown.target'
            return ''
        with patch.object(self.p,'timed_command',side_effect=command):
            guest.cleanup(self.p,self.r,{'process_scope':name},90)
        self.assertEqual(sum(a[:2]==['systemctl','kill'] for a in calls),1)
        self.assertIn('--signal=TERM',next(a for a in calls if a[:2]==['systemctl','kill']))
        self.assertFalse(any('--signal=KILL' in a for a in calls))
        self.assertFalse(any(a[:2]==['xl','destroy'] for a in calls))
        self.f.start()
        with self.assertRaises(PlatformError):guest.cleanup(self.p,self.r,{'process_scope':name},90)

    def test_empty_scope_observation_is_not_success(self):
        name=guest.scope_name(self.r['uuid'],uuid4());states=iter(('active',''))
        original=self.p.timed_command
        def command(argv,end):
            if argv[0]!='systemctl':return original(argv,end)
            if 'LoadState' in argv:return 'loaded'
            if 'ActiveState' in argv:return next(states)
            if 'Before' in argv:return 'xenova-vm-shutdown.service'
            return ''
        with patch.object(self.p,'timed_command',side_effect=command):
            with self.assertRaisesRegex(PlatformError,'final state unavailable'):
                guest.cleanup(self.p,self.r,{'process_scope':name},90)

    def test_reconciliation_scope_cleanup_is_observation_only(self):
        name=guest.scope_name(self.r['uuid'],uuid4());states=iter(('active','deactivating','inactive'))
        original=self.p.timed_command;calls=[]
        def command(argv,end):
            if argv[0]!='systemctl':return original(argv,end)
            calls.append(argv)
            if 'LoadState' in argv:return 'loaded'
            if 'ActiveState' in argv:return next(states)
            if 'Before' in argv:return 'xenova-vm-shutdown.service'
            return ''
        with patch.object(self.p,'timed_command',side_effect=command):
            guest.cleanup(self.p,self.r,{'process_scope':name},90,observe_only=True)
        self.assertFalse(any(a[:2]==['systemctl','kill'] for a in calls))

    def test_recipe_and_g1_include_helper_and_systemd_run_provider(self):
        root=Path(__file__).resolve().parents[2]
        recipe=(root/'platform/yocto/meta-xenova/recipes-core/xenova-platform/xenova-platform_1.0.bb').read_text()
        self.assertIn('file://xenova_guest_process.py',recipe)
        self.assertIn('xenova_cpu.py xenova_guest_process.py',recipe)
        self.assertIn('python3-ctypes',recipe)
        # 기존 systemd-run 제공 패키지를 명시해 scope 실행 도구의 포함을 보장한다.
        dependencies=recipe.split('RDEPENDS:${PN} = "',1)[1].split('"',1)[0].split()
        self.assertIn('systemd-extra-utils',dependencies)
        unit=(root/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova-vms.service').read_text()
        self.assertNotIn('KillMode=process',unit)
        self.assertNotIn('KillMode=none',unit)

    def test_exec_releases_admission_before_long_command_wait(self):
        runtime=self.p.runtime;ready=self.f.directory/'exec-ready'
        root=Path(__file__).resolve().parents[2]
        paths=[str(root/'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'),str(root/'platform/first-install')]
        target="import signal;from pathlib import Path;Path("+repr(str(ready))+").write_text('ready');signal.pause()"
        code=(f'import sys;sys.path[:0]={paths!r};'
              'from xenova_execution import short_lock;from xenova_guest_process import exec_owned;'
              f'exec_owned([sys.executable,"-c",{target!r}],{os.getpid()},lambda:short_lock({str(runtime)!r},"admission"))')
        process=subprocess.Popen([sys.executable,'-B','-c',code],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        try:
            end=time.monotonic()+8
            while not ready.exists():
                self.assertIsNone(process.poll())
                self.assertLess(time.monotonic(),end)
                time.sleep(.01)
            from xenova_execution import short_lock
            with short_lock(runtime,'admission'):self.assertIsNone(process.poll())
        finally:
            process.terminate();process.wait(timeout=8)


if __name__=='__main__':unittest.main()
