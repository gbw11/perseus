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
            # Independent request context, same persistent execution locks.
            pending=self.f.m.fork().call('stop',{'vm_id':self.f.vm_id},
                                         execute=True,idempotency_key='race-stop')
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
            pending=self.f.m.fork().call('stop',{'vm_id':self.f.vm_id},
                                         execute=True,idempotency_key='waiting-stop')
            self.f.fails(pending,'OPERATION_IN_PROGRESS')
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



class SupersededStopReconciliationTest(unittest.TestCase):
    """Public Manager replay; only Xen/systemctl/storage are fault fixtures."""
    setUp = StopAcceptanceTest.setUp

    def prepare_pair(self, successor_action='stop'):
        self.f.start('superseded-original-start')
        self.xen.hang_shutdown=True
        first=self.f.call('stop',key='superseded-graceful')
        self.f.fails(first,'RECONCILE_REQUIRED')
        self.original_path=self.p.data/'control-requests'/(first['operation_id']+'.json')
        self.original=self.p.execution().read(self.original_path)
        self.assertEqual(self.original['phase'],'uncertain')
        spec={'vm_id':self.f.vm_id,'force':True}
        force=self.f.call(successor_action,spec,key='explicit-superseding-force')
        self.f.ok(force)
        self.successor_path=self.p.data/'control-requests'/(force['operation_id']+'.json')
        self.successor=self.p.execution().read(self.successor_path)
        self.assertEqual(self.successor['base_revision'],self.original['intent_revision'])
        self.assertEqual(self.successor['intent_revision'],self.original['intent_revision']+1)
        self.assertEqual(self.successor['result']['observation']['presence'],'absent')
        self.original_bytes=self.original_path.read_bytes()
        self.other_requests={path.name:path.read_bytes()
            for path in (self.p.data/'control-requests').glob('*.json') if path!=self.original_path}
        self.intent_bytes=(self.p.data/'control'/(self.f.vm_id+'.json')).read_bytes()
        self.run_path=self.p.runtime/'executions'/(self.f.vm_id+'.json')
        self.run_bytes=self.run_path.read_bytes()
        self.physical_before=[c for c in self.xen.calls if len(c)>1 and c[0]=='xl'
                              and c[1] in ('create','shutdown','destroy','reboot')]

    def replay(self):
        return self.f.call('stop',key='superseded-graceful')

    def assert_preserved(self):
        self.assertEqual(self.intent_bytes,(self.p.data/'control'/(self.f.vm_id+'.json')).read_bytes())
        self.assertEqual(self.run_bytes,self.run_path.read_bytes())
        self.assertEqual(self.other_requests,{path.name:path.read_bytes()
            for path in (self.p.data/'control-requests').glob('*.json') if path!=self.original_path})
        self.assertEqual(self.physical_before,[c for c in self.xen.calls if len(c)>1 and c[0]=='xl'
                          and c[1] in ('create','shutdown','destroy','reboot')])

    def test_direct_completed_force_closes_original_failed_and_keeps_first_cause(self):
        self.prepare_pair()
        original_requests=execution.Execution.requests
        with patch.object(execution.Execution,'requests',autospec=True,side_effect=original_requests) as lookup:
            replay=self.replay()
        cause=self.original['error']['details']['cause']
        self.f.fails(replay,cause['code'])
        self.assertTrue(replay['replayed'])
        settled=self.p.execution().read(self.original_path)
        self.assertEqual(settled['phase'],'failed')
        for key,value in self.original.items():
            if key not in ('phase','events','error'):self.assertEqual(settled[key],value,key)
        self.assertEqual(settled['events'][:-1],self.original['events'])
        self.assertEqual(settled['events'][-1]['phase'],'failed')
        proof=settled['error']['details']['stop_reconciliation']
        self.assertTrue(proof['metadata_only'])
        self.assertEqual(proof['original_error'],self.original['error'])
        self.assertEqual(proof['superseding_operation_id'],self.successor['operation_id'])
        self.assertEqual(settled['error']['details']['cause'],cause)
        self.assertEqual(lookup.call_count,2)  # Existing admission scan + control scan, no third scan.
        settled_bytes=self.original_path.read_bytes()
        again=self.replay();self.f.fails(again,cause['code'])
        self.assertEqual(self.original_path.read_bytes(),settled_bytes)
        self.assert_preserved()

    def test_explicit_destroy_is_also_a_direct_forced_stop(self):
        self.prepare_pair('destroy')
        self.f.fails(self.replay(),self.original['error']['details']['cause']['code'])
        self.assertEqual(self.p.execution().read(self.original_path)['phase'],'failed')
        self.assert_preserved()

    def test_original_deadline_failure_keeps_deadline_and_first_error(self):
        self.prepare_pair()
        op=copy.deepcopy(self.original)
        op['error']['details']['cause']={'code':'DEADLINE_EXCEEDED','message':'original stop budget expired',
                                       'retryable':False,'details':{'original_marker':'kept'}}
        self.p.execution().write(self.original_path,op)
        replay=self.replay();self.f.fails(replay,'DEADLINE_EXCEEDED')
        final=self.p.execution().read(self.original_path)
        self.assertEqual(final['deadline_monotonic_ms'],op['deadline_monotonic_ms'])
        self.assertEqual(final['error']['details']['stop_reconciliation']['original_error'],op['error'])
        self.assertEqual(final['error']['details']['cause'],op['error']['details']['cause'])
        self.assert_preserved()

    def test_changed_host_generation_or_unfinished_successor_keeps_hold(self):
        self.prepare_pair()
        original_successor=copy.deepcopy(self.successor)
        for change in ({'phase':'uncertain'}, {'host_boot_id':foundation.HOST_B},
                       {'lifecycle_generation':self.original['lifecycle_generation']+1},
                       {'base_revision':self.original['intent_revision']-1},
                       {'source':'boot-policy'}):
            with self.subTest(change=change):
                self.p.execution().write(self.successor_path,{**original_successor,**change})
                response=self.replay()
                self.assertFalse(response['ok'])
                self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        self.p.execution().write(self.successor_path,original_successor)
        self.assert_preserved()

    def test_new_present_execution_or_newer_stopped_run_does_not_release_original(self):
        self.prepare_pair()
        self.xen.rows=[foundation.domain(self.r,self.f.directory/'root.raw',77)]
        response=self.replay();self.f.fails(response,'RECONCILE_REQUIRED')
        self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        self.xen.rows.clear()
        run=self.p.execution().read(self.run_path)
        for change in ({'phase':'observed'}, {'intent_revision':self.original['intent_revision']},
                       {'stopped_monotonic_ms':self.successor['events'][-1]['monotonic_ms']+1}):
            with self.subTest(change=change):
                self.p.execution().write(self.run_path,{**run,**change})
                self.assertFalse(self.replay()['ok'])
                self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        self.p.execution().write(self.run_path,run)
        self.assert_preserved()

    def test_intermediate_start_prevents_adopting_a_later_force(self):
        self.prepare_pair()
        self.f.start('intermediate-start')
        self.f.ok(self.f.call('stop',{'vm_id':self.f.vm_id,'force':True},key='later-force'))
        response=self.replay();self.f.fails(response,'TARGET_CHANGED')
        self.assertEqual(self.original_path.read_bytes(),self.original_bytes)

    def test_required_settlement_storage_failure_restores_uncertain_without_success(self):
        import errno
        for after_publication in (False,True):
            with self.subTest(after_publication=after_publication):
                if not hasattr(self,'original'):self.prepare_pair()
                write=execution.Execution.write
                attempts=[]
                def failing(owner,path,value):
                    if Path(path)==self.original_path:
                        attempts.append(value['phase'])
                        if value['phase']=='failed':
                            if after_publication:write(owner,path,value)
                            raise OSError(errno.EIO,'injected settlement EIO')
                    return write(owner,path,value)
                with patch.object(execution.Execution,'write',autospec=True,side_effect=failing):
                    response=self.replay()
                self.f.fails(response,'RECONCILE_REQUIRED')
                self.assertEqual(response['error']['details']['rollback'],'restored')
                self.assertEqual(attempts,['failed','uncertain'])
                self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
                self.assert_preserved()

    def test_parent_directory_fsync_failure_keeps_original_uncertain(self):
        import errno
        self.prepare_pair()
        with patch.object(execution,'sync_directory',side_effect=OSError(errno.EIO,'injected parent fsync')):
            response=self.replay()
        self.f.fails(response,'RECONCILE_REQUIRED')
        self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        self.assert_preserved()



    def test_owned_scope_still_requires_readonly_cleanup_when_mode_flag_is_off(self):
        from xenova_guest_process import scope_name
        self.prepare_pair()
        run=self.p.execution().read(self.run_path)
        run['process_scope']=scope_name(self.f.vm_id,run['execution_id'])
        self.p.execution().write(self.run_path,run);self.run_bytes=self.run_path.read_bytes()
        self.assertFalse(self.p.process_isolation)
        timed_command=self.p.timed_command
        calls=[]
        def observed(args,deadline):
            if args[:2]==['systemctl','show']:
                calls.append(list(args))
                return {'LoadState':'loaded','ActiveState':'active',
                        'Before':'xenova-vm-shutdown.service'}[args[args.index('-p')+1]]
            return timed_command(args,deadline)
        with patch.object(self.p,'timed_command',side_effect=observed):
            response=self.replay()
        self.f.fails(response,'RECONCILE_REQUIRED')
        self.assertTrue(calls)
        self.assertTrue(all(call[:2]==['systemctl','show'] for call in calls))
        self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        with patch.object(self.p,'timed_command',side_effect=lambda args,deadline:
                          'not-found' if args[:2]==['systemctl','show'] else timed_command(args,deadline)):
            self.f.fails(self.replay(),self.original['error']['details']['cause']['code'])
        self.assertEqual(self.p.execution().read(self.original_path)['phase'],'failed')
        self.assert_preserved()

    def test_new_stop_accepted_during_observation_invalidates_settlement(self):
        self.prepare_pair()
        command=self.p.command
        accepted=[]
        def observe_and_supersede(args,**kwargs):
            if args==['xl','list','-l'] and not accepted:
                accepted.append(self.m.fork().call('stop',{'vm_id':self.f.vm_id},execute=True,
                                                  idempotency_key='new-intent-during-observation'))
            return command(args,**kwargs)
        with patch.object(self.p,'command',side_effect=observe_and_supersede):
            response=self.replay()
        self.assertTrue(accepted)
        self.f.fails(accepted[0],'OPERATION_IN_PROGRESS')
        self.assertTrue(accepted[0]['error']['details']['accepted'])
        self.f.fails(response,'TARGET_CHANGED')
        self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        self.assertEqual(self.physical_before,[c for c in self.xen.calls if len(c)>1 and c[0]=='xl'
                          and c[1] in ('create','shutdown','destroy','reboot')])

    def test_failed_rollback_reports_both_storage_failures_and_original_cause(self):
        import errno
        self.prepare_pair()
        write=execution.Execution.write
        attempts=[]
        def unavailable(owner,path,value):
            if Path(path)==self.original_path:
                attempts.append(value['phase'])
                raise OSError(errno.EIO,'injected unavailable original journal')
            return write(owner,path,value)
        with patch.object(execution.Execution,'write',autospec=True,side_effect=unavailable):
            response=self.replay()
        self.f.fails(response,'RECONCILE_REQUIRED')
        details=response['error']['details']
        self.assertEqual(details['storage_errno'],errno.EIO)
        self.assertEqual(details['rollback'],'unconfirmed')
        self.assertEqual(details['rollback_error']['errno'],errno.EIO)
        self.assertEqual(details['cause'],self.original['error'])
        self.assertEqual(attempts,['failed','uncertain'])
        self.assertEqual(self.original_path.read_bytes(),self.original_bytes)
        self.assert_preserved()


if __name__=='__main__':unittest.main()
