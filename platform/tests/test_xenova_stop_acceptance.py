"""별도 Manager 요청이 실제 실행 flock 보유 중에도 정지 의도를 저장하는지 검증한다.

Xen은 기존 FakeXen 경계다. 의도를 시험 안에서 직접 고치지 않고 공개 stop으로 수락한다.
"""
import concurrent.futures
import copy
import os
from pathlib import Path
import signal
import threading
import time
import unittest
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
from test_xenova_recovery_foundation import management, execution, vm
from test_xenova_management import Interrupted
from xenova_runtime import operation_lock


class StopAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.f=foundation.FoundationTest('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.p,self.m,self.xen,self.r=self.f.p,self.f.m,self.f.xen,self.f.r

    def test_actual_execution_lock_accepts_stop_and_same_request_executes_later(self):
        started=self.f.start()
        with operation_lock(self.p.runtime):
            result=self.f.call('stop',key='pending-stop')
            self.f.fails(result,'OPERATION_IN_PROGRESS')
            self.assertTrue(result['error']['details']['accepted'])
            self.assertEqual(result['error']['details']['completion'],'pending')
            self.assertIsNone(result['data'])
            intent=copy.deepcopy(self.p.execution().intent(self.r))
            self.assertEqual(intent['desired_state'],'stopped')
            self.assertEqual(intent['intent_revision'],started['intent_revision']+1)
            self.assertEqual(self.f.physical('shutdown'),[])
            repeated=self.f.call('stop',key='pending-stop')
            self.assertEqual(result['operation_id'],repeated['operation_id'])
            self.assertEqual(intent,self.p.execution().intent(self.r))
        self.f.ok(self.f.call('stop',key='pending-stop'))
        self.assertEqual(len(self.f.physical('shutdown')),1)
        self.assertEqual(self.f.state()['xen_state'],'stopped')
        self.f.ok(self.f.call('stop',key='pending-stop'))
        self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_short_admission_contention_is_busy_not_original_execution_deadline_expiry(self):
        self.f.start();before=copy.deepcopy(self.p.execution().intent(self.r))
        with execution.short_lock(self.p.runtime,'admission'):
            result=self.f.call('stop',key='admission-contention')
        self.f.fails(result,'OPERATION_IN_PROGRESS')
        self.assertTrue(result['error']['retryable'])
        self.assertEqual(result['error']['details']['acceptance'],'not-checked')
        self.assertNotIn('accepted',result['error']['details'])
        self.assertEqual(before,self.p.execution().intent(self.r))
        self.assertEqual(self.f.physical('shutdown'),[])
        self.f.ok(self.f.call('stop',key='admission-contention'))
        self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_completed_controls_keep_original_success_when_current_view_lock_is_busy(self):
        original={action:self.f.call(action,key='completed-'+action) for action in ('start','reboot','stop')}
        for response in original.values():self.f.ok(response)
        self.f.start('later-running-intent')
        intent=copy.deepcopy(self.p.execution().intent(self.r));count=len(self.xen.calls)
        with operation_lock(self.p.runtime):
            for action,response in original.items():
                replay=self.f.call(action,key='completed-'+action)
                self.f.ok(replay)
                self.assertTrue(replay['replayed'])
                self.assertEqual(replay['operation_id'],response['operation_id'])
                self.assertIsNone(replay['data']['vm'])
                self.assertEqual(replay['data']['original_result'],response['data']['original_result'])
                self.assertTrue(replay['data']['diagnostic_warnings'])
        self.assertEqual(len(self.xen.calls),count)
        self.assertEqual(self.p.execution().intent(self.r),intent)

    def test_completed_recovery_busy_replay_preserves_original_after_later_stop(self):
        self.f.start();spec=self.f.spec();original=self.f.recover(spec);self.f.ok(original)
        self.f.stop('later-stop');count=len(self.xen.calls)
        self.f.ticks=100  # 만료 뒤 과거 결과 조회는 새 물리 실행 예산이 아니다.
        with operation_lock(self.p.runtime):
            replay=self.f.recover(spec);self.f.ok(replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_id'],original['operation_id'])
        self.assertEqual(replay['data']['original_result'],original['data']['original_result'])
        self.assertIsNone(replay['data']['current_observation'])
        self.assertEqual(len(self.xen.calls),count)

    def test_busy_replay_does_not_promote_failed_or_uncertain_journal(self):
        self.f.start();result=self.f.call('stop',key='not-complete');self.f.ok(result)
        path=self.p.data/'control-requests'/(result['operation_id']+'.json')
        op=self.p.execution().read(path);count=len(self.xen.calls)
        for phase in ('uncertain','failed'):
            self.p.execution().write(path,{**op,'phase':phase})
            with operation_lock(self.p.runtime):
                replay=self.f.call('stop',key='not-complete')
            self.assertFalse(replay['ok'])
            self.assertEqual(self.p.execution().read(path)['phase'],phase)
        self.assertEqual(len(self.xen.calls),count)

    def race(self,phase,*,after_create=False):
        self.f.start(); spec=self.f.spec()
        initial=self.p.execution().intent(self.r)['intent_revision']
        entered,release=threading.Event(),threading.Event()
        def checkpoint(current,value):
            if current==phase:
                entered.set()
                if not release.wait(10):raise AssertionError('checkpoint release missing')
        self.p.execution_checkpoint=checkpoint
        results=[]
        thread=threading.Thread(target=lambda:results.append(self.f.recover(spec)))
        thread.start()
        try:
            self.assertTrue(entered.wait(10),phase)
            pending=self.f.call('stop',key='race-stop')
            self.f.fails(pending,'OPERATION_IN_PROGRESS')
            self.assertTrue(pending['error']['details']['accepted'])
            self.assertEqual(self.p.execution().intent(self.r)['intent_revision'],initial+1)
            # 원 attempt/operation과 stop operation을 혼합하지 않는다.
            self.assertNotEqual(pending['operation_id'],spec['recovery_attempt_id'])
        finally:
            release.set(); thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0]['ok'],results)
        before=len(self.f.physical('create'))
        self.assertEqual(before,2 if after_create else 1)
        self.p.execution_checkpoint=lambda *args:None
        self.f.ok(self.f.call('stop',key='race-stop'))
        state=self.f.state()
        self.assertEqual((state['desired_state'],state['xen_state']),('stopped','stopped'))
        self.assertEqual(len(self.f.physical('create')),before)
        self.assertEqual(self.f.physical('destroy'),[])
        journal=self.p.execution().read(self.p.data/'recovery-attempts'/(spec['recovery_attempt_id']+'.json'))
        self.assertEqual(journal['request'],spec)
        self.assertEqual(journal['request']['deadline_monotonic_ms'],spec['deadline_monotonic_ms'])
        self.assertIn(journal['phase'],('failed','uncertain'))

    def test_stop_before_old_shutdown(self):self.race('recovery-stopping')
    def test_stop_after_old_removal(self):self.race('recovery-removed')
    def test_stop_immediately_before_create(self):self.race('launch-prepared')
    def test_stop_after_create_effect_before_binding(self):self.race('launch-observed',after_create=True)

    def test_stop_before_submission_allows_a_later_explicit_user_start(self):
        self.race('launch-prepared')
        aborted=self.p.execution().run_record(self.r)
        self.assertEqual(aborted['phase'],'stopped')
        self.assertTrue(aborted['launch_aborted_before_submit'])
        self.f.ok(self.f.call('start',key='later-explicit-start'))
        self.assertEqual(self.f.state()['desired_state'],'running')
        self.assertEqual(len(self.f.physical('create')),2)

    def test_unsubmitted_abort_storage_failure_preserves_uncertainty(self):
        original=execution.Execution.write
        def fail_abort(e,path,value):
            if value.get('launch_aborted_before_submit'):raise OSError('abort record storage failure')
            return original(e,path,value)
        with patch.object(execution.Execution,'write',autospec=True,side_effect=fail_abort):
            self.race('launch-prepared')
        self.assertEqual(self.p.execution().run_record(self.r)['phase'],'uncertain')
        self.f.fails(self.f.call('start',key='blocked-without-abort-proof'),'RECONCILE_REQUIRED')
        self.assertEqual(len(self.f.physical('create')),1)

    def test_waiting_for_old_removal_accepts_stop_without_forcing(self):
        self.f.start(); spec=self.f.spec(); self.xen.hang_shutdown=True
        entered,release=threading.Event(),threading.Event()
        original=self.p.sleep
        def sleep(seconds):
            entered.set()
            if not release.wait(10):raise AssertionError('waiting boundary not released')
            original(seconds)
        self.p.sleep=sleep
        result=[]; thread=threading.Thread(target=lambda:result.append(self.f.recover(spec)))
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            pending=self.f.call('stop',key='waiting-stop')
            self.assertTrue(pending['error']['details']['accepted'])
            self.assertEqual(len(self.f.physical('shutdown')),1)
            self.xen.rows.clear()  # 이미 제출된 graceful 요청의 외부 효과.
        finally:release.set();thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0]['ok'])
        self.p.sleep=original
        self.f.ok(self.f.call('stop',key='waiting-stop'))
        self.assertEqual(len(self.f.physical('create')),1)
        self.assertEqual(self.f.physical('destroy'),[])

    def test_intent_storage_failure_is_not_an_acceptance(self):
        self.f.start(); before=copy.deepcopy(self.p.execution().intent(self.r))
        target=self.p.data/'control'/(self.r['uuid']+'.json')
        original=execution.atomic_write
        def fail(path,*args,**kwargs):
            if Path(path)==target:raise OSError('injected intent storage failure')
            return original(path,*args,**kwargs)
        with operation_lock(self.p.runtime),patch.object(execution,'atomic_write',side_effect=fail):
            result=self.f.call('stop',key='failed-save')
        self.f.fails(result,'STORAGE_UNAVAILABLE')
        self.assertNotIn('accepted',result['error']['details'])
        self.assertEqual(before,self.p.execution().intent(self.r))
        self.assertEqual(self.f.physical('shutdown'),[])
        self.f.ok(self.f.call('stop',key='failed-save'))
        self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_restart_after_durable_intent_reuses_operation_and_original_deadline(self):
        self.f.start()
        self.f.checkpoint('intent-published')
        with self.assertRaises(Interrupted):self.f.call('stop',key='crash-stop')
        intent=copy.deepcopy(self.p.execution().intent(self.r))
        op=self.p.execution().read(self.p.data/'control-requests'/(intent['accepted_operation_id']+'.json'))
        self.p.execution_checkpoint=lambda *args:None
        self.f.m=management.Manager(self.p)
        self.f.ticks+=1
        result=self.f.call('stop',key='crash-stop')
        self.f.ok(result)
        replay=self.p.execution().read(self.p.data/'control-requests'/(intent['accepted_operation_id']+'.json'))
        self.assertEqual(op['deadline_monotonic_ms'],replay['deadline_monotonic_ms'])
        self.assertEqual(intent,self.p.execution().intent(self.r))
        self.assertEqual(result['operation_id'],op['operation_id'])
        self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_real_process_exit_after_durable_intent_is_resumed_by_original_stop(self):
        self.f.start()
        original_execution=copy.deepcopy(self.p.execution().run_record(self.r))
        physical_before=len(self.xen.calls)
        child=os.fork()
        if child==0:
            self.p.execution_checkpoint=lambda phase,value:os._exit(73) if phase=='intent-published' else None
            self.f.call('stop',key='process-exit-stop')
            os._exit(74)
        end=time.monotonic()+10
        while True:
            pid,status=os.waitpid(child,os.WNOHANG)
            if pid:break
            if time.monotonic()>=end:
                os.kill(child,signal.SIGKILL);os.waitpid(child,0)
                self.fail('owned child did not reach durable stop boundary')
            time.sleep(.01)
        self.assertEqual(os.waitstatus_to_exitcode(status),73)
        intent=copy.deepcopy(self.p.execution().intent(self.r))
        operation=self.p.execution().read(self.p.data/'control-requests'/(intent['accepted_operation_id']+'.json'))
        self.assertEqual(operation['phase'],'prepared')
        self.assertEqual(operation['host_boot_id'],self.p.execution().host)
        self.assertEqual(operation['intent_revision'],intent['intent_revision'])
        self.assertEqual(operation['lifecycle_generation'],intent['lifecycle_generation'])
        self.assertEqual(self.p.execution().run_record(self.r),original_execution)
        self.assertEqual(len(self.xen.calls),physical_before)
        deadline=operation['deadline_monotonic_ms']
        self.f.ticks+=1
        self.f.m=management.Manager(self.p)
        result=self.f.call('stop',key='process-exit-stop');self.f.ok(result)
        self.assertEqual(result['operation_id'],operation['operation_id'])
        self.assertEqual(intent,self.p.execution().intent(self.r))
        self.assertEqual(len(self.f.physical('shutdown')),1)
        self.assertEqual(self.f.state()['xen_state'],'stopped')
        completed=self.p.execution().read(self.p.data/'control-requests'/(operation['operation_id']+'.json'))
        self.assertEqual(completed['deadline_monotonic_ms'],deadline)
        self.assertEqual(completed['request'],operation['request'])
        self.assertEqual(completed['intent_revision'],operation['intent_revision'])
        self.assertEqual(completed['phase'],'completed')
        replay=self.f.call('stop',key='process-exit-stop');self.f.ok(replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_id'],operation['operation_id'])
        self.assertEqual(len(self.f.physical('shutdown')),1)
        self.assertEqual(self.f.physical('destroy'),[])

    def test_newer_user_start_supersedes_pending_stop_without_old_stop_execution(self):
        self.f.start()
        with operation_lock(self.p.runtime):self.f.call('stop',key='old-stop')
        self.f.start('new-start')
        intent=self.p.execution().intent(self.r)
        result=self.f.call('stop',key='old-stop')
        self.assertFalse(result['ok'])
        self.assertTrue(result['error']['details']['accepted'])
        self.assertEqual(result['error']['details']['completion'],'superseded')
        self.assertEqual(intent,self.p.execution().intent(self.r))
        self.assertEqual(self.f.physical('shutdown'),[])

    def test_pending_stop_expiry_preserves_intent_without_renewing_execution_permission(self):
        self.f.start()
        with operation_lock(self.p.runtime):result=self.f.call('stop',key='expired-stop')
        self.f.ticks=91
        replay=self.f.call('stop',key='expired-stop')
        self.f.fails(replay,'DEADLINE_EXCEEDED')
        self.assertTrue(replay['error']['details']['accepted'])
        self.assertTrue(replay['error']['details']['execution_deadline_expired'])
        self.assertEqual(replay['operation_id'],result['operation_id'])
        self.assertEqual(self.f.physical('shutdown'),[])
        self.f.ok(self.f.call('stop',key='explicit-new-stop'))
        self.assertEqual(len(self.f.physical('shutdown')),1)

    def test_legacy_uncertain_stop_without_deadline_does_not_crash_or_reissue(self):
        self.f.start();self.xen.hang_shutdown=True
        first=self.f.call('stop',key='legacy-uncertain-stop')
        path=self.p.data/'control-requests'/(first['operation_id']+'.json')
        value=self.p.execution().read(path);value.pop('deadline_monotonic_ms')
        self.p.execution().write(path,value)
        count=len(self.f.physical('shutdown'))
        replay=self.f.call('stop',key='legacy-uncertain-stop')
        self.f.fails(replay,'RECONCILE_REQUIRED')
        self.assertTrue(replay['error']['details']['accepted'])
        self.assertIsNone(replay['error']['details']['deadline_monotonic_ms'])
        self.assertIsNone(replay['error']['details']['execution_deadline_expired'])
        self.assertEqual(len(self.f.physical('shutdown')),count)
        self.assertNotIn('deadline_monotonic_ms',self.p.execution().read(path))

    def test_stop_reconciliation_requires_readonly_process_cleanup_confirmation(self):
        self.f.start();self.xen.hang_shutdown=True
        first=self.f.call('stop',key='cleanup-uncertain-stop')
        count=len(self.f.physical('shutdown'));self.xen.rows.clear()
        self.p.process_isolation=True
        with patch('xenova_guest_process.cleanup',side_effect=vm.PlatformError('owned backend still alive')) as cleanup:
            replay=self.f.call('stop',key='cleanup-uncertain-stop')
        self.f.fails(replay,'RECONCILE_REQUIRED')
        self.assertTrue(cleanup.call_args.kwargs['observe_only'])
        operation=self.p.execution().read(self.p.data/'control-requests'/(first['operation_id']+'.json'))
        self.assertEqual(operation['phase'],'uncertain');self.assertIsNone(operation['result'])
        with patch('xenova_guest_process.cleanup'):
            self.f.ok(self.f.call('stop',key='cleanup-uncertain-stop'))
        self.assertEqual(len(self.f.physical('shutdown')),count)

    def test_outer_wait_timeout_does_not_cancel_or_duplicate_original_operation(self):
        self.f.start();spec=self.f.spec()
        entered,release=threading.Event(),threading.Event()
        def checkpoint(phase,value):
            if phase=='recovery-removed':entered.set();release.wait(10)
        self.p.execution_checkpoint=checkpoint
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.f.recover,spec)
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises(concurrent.futures.TimeoutError):future.result(timeout=.01)
            finally:release.set()
            self.f.ok(future.result(timeout=10))
        before=len(self.f.physical('create'))
        self.f.ok(self.f.recover(spec))
        self.assertEqual(len(self.f.physical('create')),before)


if __name__=='__main__':unittest.main()
