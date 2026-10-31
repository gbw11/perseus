"""동일 잠금 구간의 실제 read/fsync 계수와 구간 밖 재검증을 확인한다."""
import errno
import copy
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch
import unittest

from benchmark_management_cost import one
import test_xenova_creation_reconcile as creation_fixtures
import test_xenova_read_progress as read_fixtures
import xenova_creation as creation
import xenova_runtime as runtime


class ManagementCostTest(unittest.TestCase):
    def test_duplicate_key_at_history_end_cannot_replay_the_first_origin(self):
        case=creation_fixtures.CreationReconcileTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        case.f.create();op=case.m._operations()[0];duplicate=copy.deepcopy(op)
        duplicate['operation_id']='ffffffff-ffff-4fff-afff-ffffffffffff'
        duplicate['owned_workspace']='instances/.operation-'+duplicate['operation_id']
        case.m._write(case.f.data/'operations'/(duplicate['operation_id']+'.json'),duplicate)
        queried=case.m.call('operation',{'idempotency_key':op['idempotency_key']})
        self.assertFalse(queried['ok']);self.assertEqual(queried['error']['code'],'RECONCILE_REQUIRED')
        before=case.m._operations()
        replay=case.m.call('create',case.f.spec,execute=True)
        self.assertFalse(replay['ok'],replay)
        self.assertEqual(replay['error']['code'],'RECONCILE_REQUIRED')
        self.assertEqual(case.m._operations(),before)

    def test_new_plan_reads_history_once_and_confirms_each_release_once(self):
        for kind in ('success','settled'):
            with self.subTest(kind=kind):
                row=one('plan',10,kind)
                self.assertEqual(row['counts']['operation_reads'],11 if kind=='success' else 22)
                self.assertEqual(row['counts'].get('operation_fsync',0),0 if kind=='success' else 22)

    def test_ip_inventory_does_not_scan_the_same_history_twice(self):
        self.assertEqual(one('ip',10,'success')['counts']['operation_reads'],11)

    def test_restore_keeps_one_final_fsck(self):
        self.assertEqual(one('restore',0,'success')['counts']['fsck'],1)

    def test_independent_inventory_calls_reconfirm_durability(self):
        case=creation_fixtures.CreationReconcileTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        op=case.fail_copy();request=case.request(op)
        self.assertTrue(case.m.call('operation-reconcile',request,execute=True)['ok'])
        with patch.object(creation,'released',wraps=creation.released) as release:
            for _ in range(2):
                with runtime.operation_lock(case.p.runtime):case.m._ip_inventory({})
        self.assertEqual(release.call_count,2)
        self.assertTrue(all(call.kwargs.get('durable') for call in release.call_args_list))
        with patch.object(creation.os,'fsync',side_effect=OSError(errno.EIO,'owned sync failure')):
            with runtime.operation_lock(case.p.runtime):
                with self.assertRaises(OSError):case.m._ip_inventory({})

    def restored_case(self):
        case=creation_fixtures.CreationReconcileTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        identity=case.f.create()
        self.assertTrue(case.m.call('retire',{'vm_id':identity},execute=True,idempotency_key='cost-retire')['ok'])
        return case,identity

    def test_fsck_failure_still_precedes_durable_restore_acceptance(self):
        case,identity=self.restored_case();before=case.m._operations();checked=[]
        command=case.p.command
        def broken(args,**kwargs):
            if args[0]=='e2fsck':checked.append(args);raise runtime.PlatformError('owned fsck rejection')
            return command(args,**kwargs)
        with patch.object(case.p,'command',broken):
            result=case.m.call('restore',{'vm_id':identity},execute=True,idempotency_key='cost-restore')
        self.assertEqual(len(checked),1)
        self.assertFalse(result['ok']);self.assertIsNone(result['operation_id'])
        self.assertEqual(case.m._operations(),before)

    def test_changed_root_after_acceptance_requires_another_fsck(self):
        case,identity=self.restored_case();calls=[]
        command=case.p.command
        def fsck(args,**kw):
            if args[0]=='e2fsck':calls.append(args);return ''
            return command(args,**kw)
        def changed(phase,op):
            if phase=='recorded' and op['action']=='restore':
                with (case.f.data/op['source']/'root.raw').open('ab') as stream:stream.write(b'changed')
        case.m.checkpoint=changed
        with patch.object(case.p,'command',fsck):
            result=case.m.call('restore',{'vm_id':identity},execute=True,idempotency_key='cost-restore')
        self.assertTrue(result['ok'],result);self.assertEqual(len(calls),2)

    def test_restore_barrier_exposes_remaining_physical_serialization(self):
        """수정하지 않은 장기 잠금의 한계를 재현한다. pending을 정지 완료로 세지 않는다."""
        case=read_fixtures.ReadProgressTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        first,second=case.pair()
        self.assertTrue(case.m.call('stop',{'vm_id':first},idempotency_key='retire-stop')['ok'])
        self.assertTrue(case.m.call('retire',{'vm_id':first},execute=True,idempotency_key='cost-retire')['ok'])
        entered,release=threading.Event(),threading.Event();responses=[]
        command=case.p.command
        def fsck(args,**kw):
            if args[0]=='e2fsck':
                entered.set()
                if not release.wait(10):raise RuntimeError('owned restore barrier not released')
                return ''
            return command(args,**kw)
        request=case.m.fork()
        with patch.object(case.p,'command',fsck):
            request.p.command=fsck
            worker=threading.Thread(target=lambda:responses.append(request.call('restore',{'vm_id':first},execute=True,idempotency_key='barrier-restore')))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(case.m.fork().call('get',{'vm_id':second},budget_ms=2000)['ok'])
                self.assertTrue(case.m.fork().call('operation',{'idempotency_key':'create:'+second},budget_ms=2000)['ok'])
                pending=case.m.fork().call('stop',{'vm_id':second},idempotency_key='barrier-stop',budget_ms=2000)
                self.assertFalse(pending['ok']);self.assertTrue(pending['error']['details']['accepted'])
                self.assertEqual(pending['error']['details']['completion'],'pending')
                original=case.m.fork().call('control-operation',{'idempotency_key':'barrier-stop'},budget_ms=2000)
                self.assertTrue(original['ok']);self.assertNotEqual(original['data']['operation']['phase'],'completed')
                self.assertTrue(worker.is_alive());self.assertFalse(release.is_set())
                folder=os.environ.get('XENOVA_STAGE4_EVIDENCE')
                if folder:
                    (Path(folder)/'P55-T06-remaining-restore-lock.json').write_text(json.dumps({'restore_released':False,'get_success':True,
                        'original_query_success':True,'stop':pending,'control_operation':original,
                        'physical_stop_completed_before_release':False}))
            finally:release.set();worker.join(10)
        self.assertFalse(worker.is_alive());self.assertTrue(responses[0]['ok'],responses)
        finished=case.m.fork().call('stop',{'vm_id':second},idempotency_key='barrier-stop',budget_ms=2000)
        self.assertTrue(finished['ok'],finished)
