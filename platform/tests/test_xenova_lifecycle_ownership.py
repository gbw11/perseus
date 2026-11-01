"""Lifecycle 보호의 실제 thread/flock/상속 FD와 OS 선택 경계를 시험한다. Xen·fsck만 대역이다."""
from contextlib import contextmanager
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_xenova_read_progress as fixtures
import xenova_preparation as ownership
import xenova_management_cli as cli
from contextlib import redirect_stdout
import io
from xenova_runtime import OperationBusy


class LifecycleOwnershipTest(unittest.TestCase):
    @contextmanager
    def held(self, distribution='debian', *, accepted=False):
        case=fixtures.ReadProgressTest('runTest');case.setUp()
        try:
            other=case.other_vm()
            case.select(distribution)
            with case.linux_boundaries():
                made=case.m.call('create',case.spec,execute=True)
            self.assertTrue(made['ok'],made)
            identity=case.spec['vm_id']
            self.assertTrue(case.m.call('retire',{'vm_id':identity},execute=True,idempotency_key='retire-A')['ok'])
            entered,release=threading.Event(),threading.Event()
            request=case.m.fork();commands=[];results=[]
            command=request.p.command
            def fsck(args,**kw):
                if args[0]=='e2fsck':
                    commands.append(args)
                    if not accepted:
                        entered.set()
                        if not release.wait(15):raise RuntimeError('unreleased owned fsck')
                    return ''
                return command(args,**kw)
            request.p.command=fsck
            usage=request.usage_check;visits=[]
            def checking(*args):
                usage(*args);visits.append(args)
                if accepted and len(visits)==2:
                    entered.set()
                    if not release.wait(15):raise RuntimeError('unreleased owned usage')
            request.usage_check=checking
            worker=threading.Thread(target=lambda:results.append(request.call('restore',{'vm_id':identity},execute=True,idempotency_key='restore-A')))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                yield case,identity,other,worker,release,commands,results
            finally:
                release.set();worker.join(15)
                self.assertFalse(worker.is_alive())
        finally:self.addCleanup(case.doCleanups)

    def evidence(self,name,value):
        folder=os.environ.get('XENOVA_STAGE4_EVIDENCE')
        if folder:
            with (Path(folder)/(name+'.json')).open('x') as stream:json.dump(value,stream,indent=2)

    def test_both_selected_linux_templates_allow_stop_and_polling_before_restore_finishes(self):
        for distribution in ('debian','ubuntu'):
            with self.subTest(os=distribution),self.held(distribution) as state:
                case,identity,other,worker,release,commands,results=state
                counts=[]
                def poll():
                    request=case.m.fork()
                    counts.append([request.call('get',{'vm_id':other},budget_ms=2000)['ok'] for _ in range(3)])
                pollers=[threading.Thread(target=poll) for _ in range(2)]
                for thread in pollers:thread.start()
                for thread in pollers:thread.join(5);self.assertFalse(thread.is_alive())
                self.assertTrue(all(all(row) for row in counts),counts)
                stop=case.m.fork().call('stop',{'vm_id':other},idempotency_key='independent-stop',budget_ms=2000)
                self.assertTrue(stop['ok'],stop)
                original=case.m.fork().call('control-operation',{'idempotency_key':'independent-stop'})
                self.assertEqual(original['data']['operation']['phase'],'completed')
                self.assertTrue(worker.is_alive());self.assertFalse(release.is_set())
                source=next(op for op in case.m._operations() if op['vm_id']==identity and op['action']=='create')
                self.assertEqual(source['template']['source']['distribution'],distribution)
                self.evidence('F-T01-'+distribution,{'selected':source['template'],'stop':stop,'original':original,'polls':counts,'restore_released':False})
                release.set();worker.join(10)
                self.assertTrue(results[0]['ok'],results);self.assertEqual(len(commands),1)

    def test_preacceptance_target_protection_rejects_competitors_without_journal(self):
        with self.held() as state:
            case,identity,_,_,_,_,results=state
            before=case.m._operations()
            for action,key in (('restore','restore-A'),('restore','other-key'),('purge','purge-A'),('start','start-A')):
                spec={'vm_id':identity,**({'confirm_vm_id':identity} if action=='purge' else {})}
                response=case.m.fork().call(action,spec,execute=True,idempotency_key=key)
                self.assertFalse(response['ok'],response)
                self.assertEqual(response['error']['code'],'OPERATION_IN_PROGRESS',response)
            self.assertFalse(any(op['idempotency_key']=='restore-A' for op in case.m._operations()))
            self.assertEqual(case.m._operations(),before)
            with self.assertRaises(OperationBusy):ownership.ensure_quiet(case.p)
            with ownership.request_output():
                with self.assertRaises(OperationBusy):
                    with ownership.lifecycle_slot(case.p):self.fail('second lifecycle I/O admitted')
        self.assertTrue(results[0]['ok'],results)

    def test_public_cli_queries_and_key_lookup_finish_before_other_restore_barrier(self):
        with self.held() as state:
            case,identity,other,worker,release,_,results=state
            for action,extra in (('intents',[]),('get',[other]),('probe',[other]),
                                 ('execution-context',[other]),('templates',[])):
                with redirect_stdout(io.StringIO()):
                    reply=cli.main([action,*extra,'--json','--budget-ms=2000'],manager=case.m.fork())
                self.assertTrue(reply['ok'],reply)
            with redirect_stdout(io.StringIO()):
                stopped=cli.main(['stop',other,'--idempotency-key','cli-barrier-stop','--budget-ms=2000'],manager=case.m.fork())
                original=cli.main(['control-operation','--idempotency-key','cli-barrier-stop','--json'],manager=case.m.fork())
            self.assertTrue(stopped['ok'],stopped)
            self.assertEqual(original['data']['operation']['phase'],'completed')
            self.assertTrue(worker.is_alive());self.assertFalse(release.is_set())
            release.set();worker.join(10);self.assertTrue(results[0]['ok'],results)

    def test_accepted_active_replay_returns_original_progress_without_second_fsck(self):
        with self.held(accepted=True) as state:
            case,identity,_,_,_,commands,results=state
            replay=case.m.fork().call('restore',{'vm_id':identity},execute=True,idempotency_key='restore-A')
            self.assertTrue(replay['ok'],replay)
            self.assertTrue(replay['replayed']);self.assertEqual(replay['data']['effective_status'],'RUNNING')
            original=case.m.fork().call('operation',{'idempotency_key':'restore-A'})
            self.assertEqual(original['data']['effective_status'],'RUNNING')
            self.assertEqual(len(commands),1)
            code="""import json,sys
from xenova_management import Manager
from xenova_vm import Platform
reply=Manager(Platform(sys.argv[1],sys.argv[2])).call('restore',{'vm_id':sys.argv[3]},execute=True,idempotency_key='restore-A')
print(json.dumps(reply))
"""
            child=subprocess.run([sys.executable,'-B','-c',code,str(case.p.data),str(case.p.runtime),identity],
                                 text=True,capture_output=True,timeout=5,check=True)
            cold=json.loads(child.stdout)
            self.assertTrue(cold['ok'],cold);self.assertEqual(cold['operation_id'],replay['operation_id'])
            self.assertEqual(cold['data']['effective_status'],'RUNNING')
        self.assertTrue(results[0]['ok'],results)
        replay=case.m.call('restore',{'vm_id':identity},execute=True,idempotency_key='restore-A')
        self.assertTrue(replay['ok'],replay);self.assertTrue(replay['replayed'])
        with ownership.request_output(),ownership.lifecycle_slot(case.p):
            busy=case.m.fork().call('restore',{'vm_id':identity},execute=True,idempotency_key='restore-A')
            self.assertTrue(busy['ok'],busy);self.assertTrue(busy['replayed'])
            self.assertEqual(busy['data']['vm_id'],identity)
            self.assertEqual(busy['data']['original_result'],replay['data']['original_result'])

    def test_gate_close_and_reopen_invalidates_preacceptance_work(self):
        with self.held() as state:
            case,identity,_,_,release,_,results=state
            gate=case.p.execution().quiesce('owned-test')
            case.p.execution().resume_host(gate['barrier_id'])
            release.set()
        self.assertFalse(results[0]['ok'],results)
        self.assertEqual(results[0]['error']['code'],'TARGET_CHANGED')
        self.assertFalse(any(op['idempotency_key']=='restore-A' for op in case.m._operations()))
        fresh=case.m.call('restore',{'vm_id':identity},execute=True,idempotency_key='restore-A')
        self.assertTrue(fresh['ok'],fresh)

    def test_inherited_fd_blocks_new_owner_and_drain_until_child_exit(self):
        case=fixtures.ReadProgressTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        identity=case.f.create()
        with ownership.request_output():
            with ownership.AssetLease(case.p,identity,'retire','owned-key') as lease:
                child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.buffer.read(1)'],
                                       stdin=subprocess.PIPE,pass_fds=(lease.fd,))
            try:
                with self.assertRaises(OperationBusy):ownership.guard_asset(case.p,identity)
                with self.assertRaises(OperationBusy):ownership.ensure_quiet(case.p)
            finally:
                child.communicate(b'x',timeout=5)
        ownership.guard_asset(case.p,identity);ownership.ensure_quiet(case.p)
        with ownership.request_output(),ownership.lifecycle_slot(case.p):
            with self.assertRaises(OperationBusy):ownership.ensure_quiet(case.p)

    def test_purge_unlink_barrier_allows_other_stop_and_same_key_resumes_after_io_failure(self):
        case=fixtures.ReadProgressTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        first,second=case.pair()
        self.assertTrue(case.m.call('stop',{'vm_id':first},idempotency_key='purge-stop')['ok'])
        self.assertTrue(case.m.call('retire',{'vm_id':first},execute=True,idempotency_key='purge-retire')['ok'])
        entered,release=threading.Event(),threading.Event();results=[]
        request=case.m.fork();unlink=request._unlink_owned;calls=[]
        def interrupted(*args):
            calls.append(args);entered.set()
            if not release.wait(10):raise RuntimeError('unreleased owned unlink')
            unlink(*args)
            raise OSError('owned failure after unlink')
        request._unlink_owned=interrupted
        spec={'vm_id':first,'confirm_vm_id':first}
        thread=threading.Thread(target=lambda:results.append(request.call('purge',spec,execute=True,idempotency_key='purge-owned')))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            stop=case.m.fork().call('stop',{'vm_id':second},idempotency_key='purge-other-stop')
            self.assertTrue(stop['ok'],stop)
            original=case.m.fork().call('control-operation',{'idempotency_key':'purge-other-stop'})
            self.assertEqual(original['data']['operation']['phase'],'completed')
            self.assertTrue(thread.is_alive());self.assertFalse(release.is_set())
        finally:release.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertFalse(results[0]['ok'])
        self.assertEqual(results[0]['error']['code'],'CLEANUP_INCOMPLETE')
        resumed=case.m.fork().call('purge',spec,execute=True,idempotency_key='purge-owned')
        self.assertTrue(resumed['ok'],resumed);self.assertEqual(resumed['operation_id'],results[0]['operation_id'])

    def test_killed_parent_does_not_release_child_owned_target(self):
        case=fixtures.ReadProgressTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        identity=case.f.create()
        self.assertTrue(case.m.call('retire',{'vm_id':identity},execute=True,idempotency_key='owner-origin')['ok'])
        op=next(value for value in case.m._operations() if value['idempotency_key']=='owner-origin')
        code="""import json,subprocess,sys
from xenova_vm import Platform
from xenova_preparation import request_output,AssetLease
p=Platform(sys.argv[1],sys.argv[2]);op=json.loads(sys.argv[3])
with request_output():
 with AssetLease(p,op['vm_id'],op['action'],op['idempotency_key']) as owner:
  owner.bind(op)
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],pass_fds=(owner.fd,))
  print(json.dumps({'child':child.pid}),flush=True)
  sys.stdin.readline()
"""
        parent=subprocess.Popen([sys.executable,'-B','-c',code,str(case.p.data),str(case.p.runtime),json.dumps(op)],
                                text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        child=None
        try:
            import select
            self.assertTrue(select.select([parent.stdout],[],[],5)[0])
            child=json.loads(parent.stdout.readline())['child']
            parent.kill();parent.wait(timeout=5)
            self.assertEqual(ownership.asset_state(case.p,op),'residual')
            with self.assertRaises(OperationBusy):ownership.guard_asset(case.p,identity)
            with self.assertRaises(OperationBusy):ownership.ensure_quiet(case.p)
        finally:
            if child is not None:
                try:os.kill(child,signal.SIGTERM)
                except ProcessLookupError:pass
            if parent.poll() is None:parent.kill()
            parent.communicate(timeout=5)
        end=time.monotonic()+5
        while True:
            try:ownership.guard_asset(case.p,identity);break
            except OperationBusy:
                if time.monotonic()>=end:raise
                time.sleep(.01)
        self.assertEqual(ownership.asset_state(case.p,op),'absent')
        self.assertTrue(case.m.call('restore',{'vm_id':identity},execute=True,idempotency_key='after-owner-exit')['ok'])

    def test_last_publication_callback_cannot_replace_checked_root(self):
        case=fixtures.ReadProgressTest('runTest');case.setUp();self.addCleanup(case.doCleanups)
        identity=case.f.create()
        self.assertTrue(case.m.call('retire',{'vm_id':identity},execute=True,idempotency_key='revision-retire')['ok'])
        def changed(phase,op):
            if phase=='moving' and op['action']=='restore':
                with (case.p.data/op['source']/'root.raw').open('ab') as stream:stream.write(b'changed-after-fsck')
        request=case.m.fork();request.checkpoint=changed
        rejected=request.call('restore',{'vm_id':identity},execute=True,idempotency_key='revision-restore')
        self.assertFalse(rejected['ok']);self.assertEqual(rejected['error']['code'],'TARGET_CHANGED')
        op=next(value for value in case.m._operations() if value['idempotency_key']=='revision-restore')
        self.assertTrue((case.p.data/op['source']).exists());self.assertFalse((case.p.data/op['target']).exists())
        finished=case.m.call('restore',{'vm_id':identity},execute=True,idempotency_key='revision-restore')
        self.assertTrue(finished['ok'],finished);self.assertEqual(finished['operation_id'],rejected['operation_id'])
