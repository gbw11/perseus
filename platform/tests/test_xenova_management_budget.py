"""하위 원 작업 조회·호출 예산·독립 stop 수락 경계. HTTP/상위 연동 수락이 아니다."""
import copy
import json
from pathlib import Path
import threading
import os
import unittest
from unittest.mock import patch

import test_xenova_recovery_foundation as foundation
from xenova_runtime import operation_lock
from xenova_execution import Execution, short_lock


class ManagementBudgetTest(unittest.TestCase):
    def setUp(self):
        self.f=foundation.FoundationTest('runTest');self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.m,self.p,self.r=self.f.m,self.f.p,self.f.r

    def call(self,action,spec=None,key=None,budget=None,manager=None):
        return (manager or self.m).call(action,spec or {'vm_id':self.f.vm_id},
                    idempotency_key=key,budget_ms=budget)

    def lookup(self,key,*,manager=None,budget=3000):
        return self.call('control-operation',{'vm_id':self.f.vm_id,'idempotency_key':key},
                         manager=manager,budget=budget)

    def test_pending_key_lookup_works_during_execution_lock_and_never_executes(self):
        self.f.start();before=len(self.f.physical('shutdown'))
        with operation_lock(self.p.runtime):
            first=self.call('stop',key='stable-stop',budget=95000)
            self.assertFalse(first['ok']);self.assertTrue(first['error']['details']['accepted'])
            op=self.f.ok(self.lookup('stable-stop'))['operation']
            intent=copy.deepcopy(self.p.execution().intent(self.r))
            self.assertEqual(op['operation_id'],first['operation_id'])
            self.assertEqual(first['error']['details']['phase'],op['phase'])
            self.assertEqual(first['error']['details']['host_boot_id'],op['host_boot_id'])
            again=self.call('stop',key='stable-stop',budget=50000)
            self.assertEqual(again['operation_id'],op['operation_id'])
            same=self.f.ok(self.lookup('stable-stop'))['operation']
            # accepted 단계 재진입을 기록해도 원 요청과 intent는 변하지 않는다.
            for field in ('operation_id','request','request_hash','intent_revision','deadline_monotonic_ms'):
                self.assertEqual(same[field],op[field])
            self.assertEqual(intent,self.p.execution().intent(self.r))
        self.assertEqual(len(self.f.physical('shutdown')),before)
        self.f.ok(self.call('stop',key='stable-stop',budget=95000))
        self.f.ok(self.call('stop',key='stable-stop',budget=1000))
        self.assertEqual(len(self.f.physical('shutdown')),before+1)

    def test_operation_lookup_is_read_only_and_vm_bound(self):
        started=self.f.start('first-start')
        before={p:p.read_bytes() for p in self.p.data.rglob('*.json')}
        original=self.f.ok(self.lookup('first-start'))['operation']
        queries=[{'operation_id':original['operation_id']}]
        for spec in queries:
            result=self.call('control-operation',spec)
            self.assertEqual(self.f.ok(result)['operation'],original)
        self.f.fails(self.call('control-operation',{'idempotency_key':'first-start','vm_id':foundation.HOST_B}),'VM_NOT_FOUND')
        self.assertEqual(before,{p:p.read_bytes() for p in self.p.data.rglob('*.json')})

    def test_absent_key_is_not_accepted_and_creates_no_record(self):
        before=list(self.p.execution().requests())
        missing=self.lookup('not-yet-observed')
        self.f.fails(missing,'VM_NOT_FOUND')
        self.assertIsNone(missing['operation_id'])
        self.assertEqual(before,self.p.execution().requests())

    def test_duplicate_key_records_are_reconciliation_not_search_absence(self):
        self.f.start('duplicate')
        original=self.f.ok(self.lookup('duplicate'))['operation']
        conflicting={**original,'operation_id':foundation.HOST_B}
        execution=self.p.execution()
        execution.write(execution._path(self.p.data,'control-requests',foundation.HOST_B),conflicting)
        self.f.fails(self.lookup('duplicate'),'RECONCILE_REQUIRED')
        self.assertEqual(len(self.p.execution().requests()),2)

    def test_incomplete_enumeration_never_confirms_absence_or_unique_key(self):
        self.f.start('existing')
        scandir=os.scandir
        root=self.p.data/'control-requests'
        class Partial:
            def __enter__(owner):return owner
            def __exit__(owner,*args):return False
            def __iter__(owner):
                with scandir(root) as entries:yield next(entries)
                raise OSError('injected EIO after first directory entry')
        def broken(path):return Partial() if Path(path)==root else scandir(path)
        for key in ('existing','not-found'):
            with patch('xenova_execution.os.scandir',broken):
                self.f.fails(self.lookup(key),'OBSERVATION_UNAVAILABLE')
        with patch('xenova_execution.os.scandir',side_effect=PermissionError('injected unreadable directory')):
            self.assertFalse(self.lookup('existing')['ok'])
        # A file listed then lost is not equivalent to an absent directory.
        read=Execution.read
        def missing(execution,path):return None if path.parent==root else read(execution,path)
        with patch.object(Execution,'read',missing):
            self.f.fails(self.lookup('existing'),'OBSERVATION_UNAVAILABLE')
        self.f.ok(self.lookup('existing'))

    def test_fork_separates_concurrent_budgets_and_thread_local_trace(self):
        from xenova_trace import current
        entered,release=threading.Event(),threading.Event();seen=[];output=[]
        def checkpoint(phase,value):
            if phase=='launch-prepared':
                seen.append(('start',self.p.response_deadline,current().context['request_id']))
                entered.set()
                if not release.wait(5):raise AssertionError('test owner gate not released')
            if phase=='intent-admission-pending' and value.get('desired_state')=='stopped':
                seen.append(('stop',stop.p.response_deadline,current().context['request_id']))
        self.p.execution_checkpoint=checkpoint;stop=None
        worker=threading.Thread(target=lambda:output.append(self.m.call('start',{'vm_id':self.f.vm_id},
          idempotency_key='budget-start',request_id='start-context',budget_ms=95000)))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            stop=self.m.fork()
            self.assertFalse(hasattr(stop.p,'response_deadline'))
            self.assertEqual(stop.p.data,self.p.data);self.assertEqual(stop.p.runtime,self.p.runtime)
            self.assertIs(stop.p.command,self.p.command)
            reply=stop.call('stop',{'vm_id':self.f.vm_id},idempotency_key='budget-stop',
                            request_id='stop-context',budget_ms=700)
            self.assertTrue(reply['error']['details']['accepted'])
            self.assertEqual(self.p.response_deadline,95)
            self.assertFalse(hasattr(stop.p,'response_deadline'))
            self.assertEqual(stop.last_trace['context']['request_id'],'stop-context')
        finally:
            release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertFalse(output[0]['ok'])
        self.assertIn(('start',95,'start-context'),seen)
        self.assertIn(('stop',.7,'stop-context'),seen)
        self.assertEqual(self.m.last_trace['context']['request_id'],'start-context')
        self.assertEqual(stop.last_trace['context']['request_id'],'stop-context')
        self.assertFalse(hasattr(self.p,'response_deadline'))
        self.assertEqual(self.f.physical('create'),[])

    def test_query_error_and_budget_exhaustion_are_not_empty_success(self):
        self.f.start('start')
        original=Execution.requests
        def slow(e,**kwargs):
            rows=original(e,**kwargs);self.f.ticks+=1;return rows
        with patch.object(Execution,'requests',slow):
            self.f.fails(self.lookup('start',budget=100),'DEADLINE_EXCEEDED')
        with patch.object(Execution,'requests',side_effect=OSError('injected read failure')):
            self.assertFalse(self.lookup('start')['ok'])
        self.f.ok(self.lookup('start'))

    def test_get_and_list_use_one_remaining_budget_after_registry_cost(self):
        self.f.start()
        records=self.p.records
        def costly(**kw):
            result=records(**kw);self.f.ticks+=.2;return result
        for action,method in [('get','observe'),('list','observe_many')]:
            seen=[];real=getattr(Execution,method);self.f.ticks=10
            def capture(e,*args,**kwargs):
                seen.append(args[-1]);return real(e,*args,**kwargs)
            with patch.object(self.p,'records',costly),patch.object(Execution,method,capture):
                result=self.call(action,{'vm_id':self.f.vm_id,'timeout_ms':1000} if action=='get' else {'timeout_ms':1000},budget=500)
            self.f.ok(result)
            self.assertEqual(seen,[10.5])
            self.assertAlmostEqual(self.f.ticks,10.2)

    def test_list_observes_multiple_vms_with_the_same_deadline(self):
        other=foundation.record('debian-dev')
        foundation.fixture(self.p.data,other)
        self.f.start()
        seen=[]
        original=self.p.domains
        def capture(*,deadline=None):
            seen.append(deadline)
            self.f.ticks+=.05
            return original(deadline=deadline)
        self.f.ticks=10
        with patch.object(self.p,'domains',capture):
            result=self.f.ok(self.call('list',{'timeout_ms':500},budget=300))
        self.assertEqual(len(result['vms']),2)
        self.assertEqual({item['vm_id'] for item in result['vms']},{self.f.vm_id,other['uuid']})
        self.assertTrue(seen)
        self.assertTrue(all(item==10.3 for item in seen),seen)

    def test_invalid_budget_and_selector_never_change_intent(self):
        before=copy.deepcopy(self.p.execution().intent(self.r))
        for budget in [0,-1,True,1.5,105001,'100']:
            self.f.fails(self.call('stop',key='invalid',budget=budget),'INVALID_SPEC')
        for spec in ({'operation_id':foundation.HOST_A,'idempotency_key':'x'},
                     {'idempotency_key':''},{'idempotency_key':'x','timeout_ms':False}):
            self.f.fails(self.call('control-operation',spec),'INVALID_SPEC')
        for action in ('get','list'):
            spec={'timeout_ms':15001}
            if action=='get':spec['vm_id']=self.f.vm_id
            self.f.fails(self.call(action,spec),'INVALID_SPEC')
        self.assertEqual(before,self.p.execution().intent(self.r))

    def test_forked_stop_accepts_while_original_start_is_running_and_blocks_old_create(self):
        entered,release=threading.Event(),threading.Event()
        def checkpoint(phase,value):
            if phase=='launch-prepared':
                entered.set()
                if not release.wait(5):raise AssertionError('owned start gate was not released')
        self.p.execution_checkpoint=checkpoint
        stop=self.m.fork();output=[]
        self.assertIsNot(stop,self.m);self.assertIsNot(stop.p,self.p)
        thread=threading.Thread(target=lambda:output.append(self.call('start',key='old-start',budget=95000)))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            accepted=self.call('stop',key='concurrent-stop',budget=90000,manager=stop)
            self.assertTrue(accepted['error']['details']['accepted'])
            original=self.f.ok(self.lookup('concurrent-stop',manager=stop))['operation']
            self.assertEqual(original['phase'],'accepted')
        finally:
            release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertFalse(output[0]['ok'])
        self.assertEqual(self.f.physical('create'),[])
        self.p.execution_checkpoint=lambda *a:None
        self.f.ok(self.call('stop',key='concurrent-stop',manager=stop,budget=95000))
        self.assertEqual(self.f.physical('shutdown'),[])

    def test_retry_budget_does_not_extend_original_deadline_or_change_spec(self):
        self.f.start()
        entered=self.f.ticks
        with operation_lock(self.p.runtime):
            accepted=self.call('stop',key='expiring-stop',budget=2000)
        before=self.f.ok(self.lookup('expiring-stop'))['operation']
        self.assertEqual(before['deadline_monotonic_ms'],int(entered*1000)+2000)
        self.f.ticks=3
        expired=self.call('stop',key='expiring-stop',budget=95000)
        self.assertFalse(expired['ok'])
        after=self.f.ok(self.lookup('expiring-stop'))['operation']
        self.assertTrue(self.f.ok(self.lookup('expiring-stop'))['deadline_observation']['execution_deadline_expired'])
        self.assertEqual(after['operation_id'],accepted['operation_id'])
        self.assertEqual(after['request_hash'],before['request_hash'])
        self.assertEqual(after['request'],before['request'])
        self.assertEqual(after['deadline_monotonic_ms'],before['deadline_monotonic_ms'])
        self.assertEqual(self.f.physical('shutdown'),[])
        self.assertFalse(hasattr(self.p,'response_deadline'))

    def test_uncertain_result_recheck_is_capped_without_another_shutdown(self):
        self.f.start()
        self.f.xen.hang_shutdown=True
        first=self.call('stop',key='submitted-stop',budget=100)
        self.assertFalse(first['ok'])
        original=self.f.ok(self.lookup('submitted-stop'))['operation']
        self.assertEqual(original['phase'],'uncertain')
        before=len(self.f.physical('shutdown'))
        seen=[];observe=Execution.observe;entered=self.f.ticks
        def capture(execution,record,deadline):
            seen.append(deadline)
            return observe(execution,record,deadline)
        with patch.object(Execution,'observe',capture):
            replay=self.call('stop',key='submitted-stop',budget=200)
        self.assertFalse(replay['ok'])
        self.assertTrue(seen)
        self.assertAlmostEqual(seen[0],entered+.2)
        self.assertEqual(len(self.f.physical('shutdown')),before)
        latest=self.f.ok(self.lookup('submitted-stop'))['operation']
        for field in ('operation_id','request_hash','intent_revision','deadline_monotonic_ms'):
            self.assertEqual(latest[field],original[field])

    def test_deadline_observation_does_not_compare_clocks_across_host_boots(self):
        self.f.start('original')
        self.f.host=foundation.HOST_B
        result=self.f.ok(self.lookup('original'))
        self.assertFalse(result['deadline_observation']['original_host_matches'])
        self.assertIsNone(result['deadline_observation']['execution_deadline_expired'])
        self.assertEqual(result['operation']['host_boot_id'],foundation.HOST_A)

    def test_metadata_read_also_consumes_lookup_budget(self):
        self.f.start('original')
        calls=[]
        def host():
            calls.append(1)
            if len(calls)==3:self.f.ticks+=1
            return self.f.host
        self.p.boot_id_reader=host
        self.f.fails(self.lookup('original',budget=100),'DEADLINE_EXCEEDED')

    def test_completed_replay_preserves_result_without_observation_under_busy_lock(self):
        self.f.start('done')
        original=self.f.ok(self.lookup('done'))['operation']
        self.f.stop('later-stop')
        with operation_lock(self.p.runtime):
            replay=self.call('start',key='done',budget=1000)
        data=self.f.ok(replay)
        self.assertTrue(replay['replayed']);self.assertIsNone(data['vm'])
        self.assertEqual(data['original_result'],original['result'])
        self.assertTrue(data['diagnostic_warnings'])
        self.assertEqual(len(self.f.physical('create')),1)

    def test_admission_wait_expires_without_late_acceptance(self):
        self.f.start()
        before=copy.deepcopy(self.p.execution().intent(self.r))
        with short_lock(self.p.runtime,'admission'):
            failed=self.call('stop',key='timed-out-before-admission',budget=50)
        self.f.fails(failed,'DEADLINE_EXCEEDED')
        self.assertNotIn('accepted',failed['error']['details'])
        self.assertEqual(before,self.p.execution().intent(self.r))
        self.f.fails(self.lookup('timed-out-before-admission'),'VM_NOT_FOUND')

    def test_lookup_interrupts_enumeration_without_false_absence_or_uniqueness(self):
        self.f.start('existing')
        original=self.f.ok(self.lookup('existing'))['operation']
        root=self.p.data/'control-requests';scandir=os.scandir;seen=[]
        class Slow:
            def __enter__(owner):return owner
            def __exit__(owner,*args):return False
            def __iter__(owner):
                with scandir(root) as entries:
                    for entry in entries:
                        seen.append(entry.name);self.f.ticks+=.2;yield entry
                raise AssertionError('scan must stop before requesting another entry')
        for key in ('existing','not-found'):
            with patch('xenova_execution.os.scandir',lambda path:Slow() if Path(path)==root else scandir(path)):
                self.f.fails(self.lookup(key,budget=100),'DEADLINE_EXCEEDED')
        self.assertEqual(len(seen),2)
        latest=self.f.ok(self.lookup('existing'))['operation']
        self.assertEqual(latest,original)

    def test_stop_scan_budget_does_not_accept_or_shorten_original_execution(self):
        self.f.start('running')
        before=copy.deepcopy(self.p.execution().intent(self.r));read=Execution.read
        def delayed(execution,path):
            value=read(execution,path)
            if path.parent==self.p.data/'control-requests':self.f.ticks+=2.1
            return value
        with patch.object(Execution,'read',delayed):
            self.f.fails(self.call('stop',key='scan-expired-stop',budget=90000),'DEADLINE_EXCEEDED')
        self.assertEqual(self.p.execution().intent(self.r),before)
        self.f.fails(self.lookup('scan-expired-stop'),'VM_NOT_FOUND')
        with operation_lock(self.p.runtime):
            accepted=self.call('stop',key='scan-expired-stop',budget=90000)
        self.assertTrue(accepted['error']['details']['accepted'])
        op=self.f.ok(self.lookup('scan-expired-stop'))['operation']
        self.assertGreater(op['deadline_monotonic_ms']-int(self.f.ticks*1000),88000)


if __name__=='__main__':unittest.main()
