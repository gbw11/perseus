"""5번 실제 원장/검증 복사/정리와 정상 후속 생성. Xen·mount/guest와 전역 proc 열람은 외부 대역이다."""
from contextlib import contextmanager
from contextlib import redirect_stdout
import errno
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from test_xenova_spring_followup import FollowupFixture
import test_xenova_preparation_concurrency as stage3
import prepare_instance as preparation
import xenova_creation as creation
import xenova_management as management
import xenova_management_cli as cli
import xenova_preparation as ownership
import xenova_runtime as runtime
import xenova_vm as vm


class Interrupted(BaseException):pass


class CreationReconcileTest(FollowupFixture):
    select=stage3.PreparationConcurrencyTest.select
    other_vm=stage3.PreparationConcurrencyTest.other_vm

    def setUp(self):
        super().setUp()
        command=self.p.command
        def external(args,**kwargs):
            if args[0]=='losetup':return json.dumps({'loopdevices':[]})
            return command(args,**kwargs)
        self.p.command=external
        iterate=Path.iterdir
        def processes(path):
            # 실제 보드 root의 전역 proc 접근 대신 이 시험 프로세스의 실제 FD를 사용한다.
            if path==Path('/proc'):yield Path('/proc')/str(os.getpid())
            else:yield from iterate(path)
        patched=patch.object(Path,'iterdir',processes);patched.start();self.addCleanup(patched.stop)

    def evidence(self,name,value):
        folder=os.environ.get('XENOVA_STAGE4_EVIDENCE')
        if folder:
            with (Path(folder)/(name+'-'+self._testMethodName+'.json')).open('x') as stream:json.dump(value,stream,indent=2)

    def fail_copy(self,distribution='debian'):
        self.select(distribution)
        write=os.write;written=[]
        def broken(fd,data):
            if Path(os.readlink('/proc/self/fd/'+str(fd))).name=='root.raw':
                if written:raise OSError(errno.EIO,'owned copy fault')
                count=write(fd,data[:1]);written.append(count);return count
            return write(fd,data)
        with self.linux_boundaries(),patch.object(preparation.os,'write',broken):
            response=self.m.call('create',self.spec,execute=True)
        self.error(response,'RECOVERY_REQUIRED')
        op=next(o for o in self.m._operations() if o['vm_id']==self.spec['vm_id'])
        self.assertEqual(written,[1]);self.assertIsNotNone(op['error'])
        with runtime.operation_lock(self.p.runtime):pass
        return op

    def request(self,op):
        spec={'operation_id':op['operation_id'],'vm_id':op['vm_id'],
            'expected_spec_sha256':op['spec_hash'],'purpose':'discard-private'}
        diagnostic=self.m.fork().call('operation-reconcile',spec)
        self.assertTrue(diagnostic['ok'],diagnostic)
        return {**spec,'expected_context_sha256':diagnostic['data']['context_sha256'],
            'reconciliation_id':'750e8400-e29b-41d4-a716-446655440000'}

    def cold(self,request):
        code='''
import json,os,sys
from pathlib import Path
from unittest.mock import patch
from test_xenova_platform import FakeXen
from first_install_fixture import FILES
import xenova_vm as vm
import xenova_management as management
data,run=map(Path,sys.argv[1:]);xen=FakeXen(data,{})
def command(args,**kwargs):
 if args[0]=='losetup':return json.dumps({'loopdevices':[]})
 return xen(args,**kwargs)
iterate=Path.iterdir
def processes(path):
 if path==Path('/proc'):
  yield Path('/proc')/str(os.getpid());yield Path('/proc')/str(os.getppid())
 else:yield from iterate(path)
p=vm.Platform(data,run,command=command,defaults_path=FILES/'defaults.json')
with patch.object(vm,'storage_check'),patch.object(Path,'iterdir',processes):
 print(json.dumps(management.Manager(p).call('operation-reconcile',json.loads(sys.stdin.read()),execute=True)))
'''
        child=subprocess.run([sys.executable,'-B','-c',code,str(self.f.data),str(self.f.run)],
            input=json.dumps(request),text=True,capture_output=True,timeout=20)
        self.assertEqual(child.returncode,0,child.stderr)
        return json.loads(child.stdout)

    def finish_and_create(self,op,request):
        result=self.m.fork().call('operation-reconcile',request,execute=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['data']['effective_status'],'FAILED')
        settled=self.m._operations()[-1]
        self.assertEqual(settled['error'],op['error'])
        with patch.object(preparation,'_copy_verified',side_effect=AssertionError('same key must not recopy')):
            replay=self.m.fork().call('create',self.spec,execute=True)
        self.assertFalse(replay['ok'],replay)
        self.assertTrue(replay['error']['details']['reconciliation']['release'])
        self.assertEqual(replay['operation_id'],op['operation_id'])
        self.spec={**self.spec,'vm_id':'850e8400-e29b-41d4-a716-446655440000'}
        with self.linux_boundaries():fresh=self.m.call('create',self.spec,execute=True)
        self.assertTrue(fresh['ok'],fresh)
        self.assertNotEqual(fresh['operation_id'],op['operation_id'])
        self.assertEqual(fresh['data']['ipv4'],op['assigned']['ipv4'])
        self.evidence('S5-T08-'+op['template']['template_id'],{'original_operation':op['operation_id'],
            'original_error':op['error'],'reconciliation':result,'same_key_replay':replay,
            'new_create':fresh,'one_verified_copy_per_create':True})
        return result

    def test_S5_T08_debian_failed_copy_settles_and_new_creation_succeeds(self):
        self.center('debian')

    def test_S5_T08_ubuntu_failed_copy_settles_and_new_creation_succeeds(self):
        self.center('ubuntu')

    def center(self,distribution):
        op=self.fail_copy(distribution);request=self.request(op)
        def interrupted(phase,current):
            if phase=='reconcile-after-delete':raise Interrupted()
        self.m.checkpoint=interrupted
        with self.assertRaises(Interrupted):self.m.call('operation-reconcile',request,execute=True)
        self.m.checkpoint=lambda *args:None
        cold=self.cold(request);self.assertTrue(cold['ok'],cold)
        self.finish_and_create(op,request)

    def deadline_copy(self,distribution):
        self.select(distribution);ticks=[10.0];self.p.clock=lambda:ticks[0]
        self.p.creation_policy_path.write_text(json.dumps({'schema_version':'1.0','call_ms':1000,'execution_ms':100,'cleanup_ms':100}))
        write=os.write
        def delayed(fd,data):
            count=write(fd,data)
            if Path(os.readlink('/proc/self/fd/'+str(fd))).name=='root.raw':ticks[0]=10.2
            return count
        with self.linux_boundaries(),patch.object(preparation.os,'write',delayed):
            response=self.m.call('create',self.spec,execute=True)
        self.error(response,'DEADLINE_EXCEEDED')
        op=self.m._operations()[0];self.assertEqual(op['creation']['deadline_monotonic_ms'],10100)
        self.assertFalse((self.f.data/'instances'/management.internal_name(op['vm_id'])).exists())
        resources=json.loads((self.f.data/op['owned_workspace']/'.preparation-resources.json').read_text())
        self.assertTrue(resources['clean']);self.assertEqual(resources['loops'],[])
        self.finish_and_create(op,self.request(op))

    def test_S5_T02_debian_deadline_inside_copy_keeps_partial_and_cleans_resources(self):
        self.deadline_copy('debian')

    def test_S5_T02_ubuntu_deadline_inside_copy_keeps_partial_and_cleans_resources(self):
        self.deadline_copy('ubuntu')

    def test_S5_T01_missing_policy_refuses_new_acceptance_without_blocking_reads(self):
        self.p.creation_policy_path.unlink()
        response=self.m.call('create',self.spec,execute=True)
        self.error(response,'RECONCILE_REQUIRED')
        self.assertEqual(self.m._operations(),[])
        self.assertTrue(self.m.call('list')['ok'])

    def test_S5_T13_workspace_absent_can_settle_without_deleting_anything(self):
        self.select()
        def stop(phase,op):
            if phase=='recorded':raise Interrupted()
        self.m.checkpoint=stop
        with self.assertRaises(Interrupted):self.m.call('create',self.spec,execute=True)
        self.m.checkpoint=lambda *args:None
        op=self.m._operations()[0]
        self.assertFalse((self.f.data/op['owned_workspace']).exists())
        request=self.request(op)
        result=self.m.call('operation-reconcile',request,execute=True)
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['data']['reconciliation']['plan']['absent'])
        fresh_spec={**self.spec,'vm_id':'850e8400-e29b-41d4-a716-446655440000'}
        with self.linux_boundaries():fresh=self.m.call('create',fresh_spec,execute=True)
        self.assertTrue(fresh['ok'],fresh)

    def test_S5_T07_interruption_after_unlink_resumes_same_saved_plan(self):
        op=self.fail_copy();request=self.request(op)
        def interrupted(phase,current):
            if phase=='reconcile-after-delete':raise Interrupted()
        self.m.checkpoint=interrupted
        with self.assertRaises(Interrupted):self.m.call('operation-reconcile',request,execute=True)
        self.m.checkpoint=lambda *args:None
        cold=self.cold(request);self.assertTrue(cold['ok'],cold)
        self.finish_and_create(op,request)

    def test_S5_T14_damaged_source_does_not_prevent_owned_private_discard(self):
        op=self.fail_copy()
        root=self.f.data/op['template']['asset_directory']/'root.raw'
        root.chmod(0o600);root.write_bytes(b'damaged source');root.chmod(0o444)
        (root.parent/'template.json').unlink()
        request=self.request(op)
        with patch.object(creation,'digest',wraps=creation.digest),patch.object(preparation,'_copy_verified',side_effect=AssertionError('discard must not copy')):
            result=self.m.call('operation-reconcile',request,execute=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(root.read_bytes(),b'damaged source')

    def cleanup_barrier(self,distribution):
        other=self.other_vm();op=self.fail_copy(distribution);request=self.request(op)
        entered,release=threading.Event(),threading.Event();responses=[]
        stop_poll=threading.Event();condition=threading.Condition();normal=[0];poll_errors=[]
        def polling():
            for _ in range(200):
                if stop_poll.is_set():break
                reply=self.m.fork().call('list',budget_ms=2000)
                with condition:
                    if reply['ok']:normal[0]+=1
                    else:poll_errors.append(reply['error']['code'])
                    condition.notify_all()
        def blocked(phase,current):
            if phase=='reconcile-before-delete' and not entered.is_set():
                entered.set()
                if not release.wait(10):raise RuntimeError('owned cleanup barrier timed out')
        self.m.checkpoint=blocked
        worker=threading.Thread(target=lambda:responses.append(self.m.fork().call('operation-reconcile',request,execute=True)))
        poller=threading.Thread(target=polling);worker.start()
        try:
            self.assertTrue(entered.wait(5))
            poller.start()
            with condition:self.assertTrue(condition.wait_for(lambda:normal[0]>0,5))
            for action,spec in [('list',{}),('get',{'vm_id':other}),('operation',{'operation_id':op['operation_id']})]:
                reply=self.m.fork().call(action,spec,budget_ms=2000)
                self.assertTrue(reply['ok'],reply)
            with condition:before_stop=normal[0]
            stopped=self.m.fork().call('stop',{'vm_id':other},idempotency_key='cleanup-stop',budget_ms=2000)
            self.assertTrue(stopped['ok'],stopped)
            result=self.m.fork().call('control-operation',{'idempotency_key':'cleanup-stop'})
            self.assertTrue(result['ok'],result);self.assertEqual(result['data']['operation']['phase'],'completed')
            self.assertTrue(worker.is_alive());self.assertFalse(release.is_set())
            duplicate=self.m.fork().call('operation-reconcile',request,execute=True)
            self.error(duplicate,'OPERATION_IN_PROGRESS')
            self.assertFalse(duplicate['error']['details']['accepted'])
            with condition:self.assertTrue(condition.wait_for(lambda:normal[0]>before_stop+1,5))
            self.assertTrue(poller.is_alive())
            self.evidence('S5-T09-cleanup-barrier',{'cleanup_alive':True,'released':False,'stop':stopped,
                'original_control':result,'polling_alive':True,'normal_lists':normal[0],'poll_errors':poll_errors})
        finally:
            stop_poll.set()
            if poller.ident is not None:poller.join(5)
            release.set();worker.join(10)
        self.assertFalse(poller.is_alive())
        self.assertFalse(worker.is_alive());self.assertTrue(responses[0]['ok'],responses)

    def test_S5_T09_debian_cleanup_keeps_polling_and_other_vm_stop_progress(self):
        self.cleanup_barrier('debian')

    def test_S5_T09_ubuntu_cleanup_keeps_polling_and_other_vm_stop_progress(self):
        self.cleanup_barrier('ubuntu')

    def test_S5_T15_visible_complete_json_does_not_release_until_writer_fsync_recovers(self):
        op=self.fail_copy();request=self.request(op);fsync=os.fsync
        directory=self.f.data/'operations'
        def fail_parent(fd):
            if Path(os.readlink('/proc/self/fd/'+str(fd)))==directory:
                current=self.m._read(directory/(op['operation_id']+'.json'))
                if current.get('reconciliation',{}).get('phase')=='complete':raise OSError(errno.EIO,'completion directory sync failed')
            return fsync(fd)
        with patch.object(runtime.os,'fsync',fail_parent):
            failed=self.m.call('operation-reconcile',request,execute=True)
            self.assertFalse(failed['ok'],failed)
            raw=self.m._operations()[0]
            self.assertEqual(raw['reconciliation']['phase'],'complete','the ambiguous bytes must really be visible')
            with self.assertRaises(OSError):creation.holds(self.m,raw)
            proposed={**self.spec,'vm_id':'850e8400-e29b-41d4-a716-446655440000'}
            rejected=self.m.fork().call('create',proposed,execute=True)
            self.assertFalse(rejected['ok'],rejected);self.assertEqual(len(self.m._operations()),1)
        recovered=self.cold(request);self.assertTrue(recovered['ok'],recovered)
        self.finish_and_create(op,request)

    def test_S5_T06_diagnostic_target_replacement_and_open_inode_are_not_deleted(self):
        op=self.fail_copy();request=self.request(op)
        root=self.f.data/op['owned_workspace'];stage=root/op['assigned']['internal_name'];disk=stage/'root.raw'
        held=os.open(disk,os.O_RDONLY)
        try:
            failed=self.m.call('operation-reconcile',request,execute=True)
            self.error(failed,'ASSET_IN_USE');self.assertTrue(disk.exists())
        finally:os.close(held)
        original=disk.read_bytes();replacement=self.f.data/'replacement'
        replacement.write_bytes(original+b'replaced');os.replace(replacement,disk)
        changed=self.m.call('operation-reconcile',request,execute=True)
        self.error(changed,'TARGET_CHANGED');self.assertEqual(disk.read_bytes(),original+b'replaced')
        request=self.request(op)
        self.finish_and_create(op,request)

    def test_S5_T03_legacy_other_host_no_resume_but_explicit_discard_is_supported(self):
        op=self.fail_copy();path=self.f.data/'operations'/(op['operation_id']+'.json')
        op.pop('creation');op['schema_version']='1.0';path.write_text(json.dumps(op))
        replay=self.m.call('create',self.spec,execute=True)
        self.assertFalse(replay['ok'],replay)
        self.assertNotIn('creation',self.m._operations()[0])
        result=self.cold(self.request(op));self.assertTrue(result['ok'],result)
        self.assertNotIn('creation',self.m._operations()[0])

    def test_S5_T07_plan_write_failure_deletes_nothing_then_same_request_finishes(self):
        op=self.fail_copy();request=self.request(op);root=self.f.data/op['owned_workspace']
        before={str(p.relative_to(root)):p.stat().st_ino for p in root.rglob('*')}
        save=self.m._save_op
        def unavailable(current,*args,**kwargs):
            if 'reconciliation' in current:raise OSError(errno.ENOSPC,'owned plan record full')
            return save(current,*args,**kwargs)
        with patch.object(self.m,'_save_op',unavailable):
            failed=self.m.call('operation-reconcile',request,execute=True)
        self.assertFalse(failed['ok'],failed)
        self.assertEqual(before,{str(p.relative_to(root)):p.stat().st_ino for p in root.rglob('*')})
        self.assertNotIn('reconciliation',self.m._operations()[0])
        self.finish_and_create(op,request)

    def test_S5_T04_unlinked_private_fd_is_still_usage_until_closed(self):
        op=self.fail_copy();plan=creation.inventory(self.m,op,self.p.clock()+10)
        disk=self.f.data/op['owned_workspace']/op['assigned']['internal_name']/'root.raw'
        held=os.open(disk,os.O_RDONLY)
        try:
            disk.unlink()
            with self.assertRaises(management.ManagementError) as blocked:
                creation.usage(self.m,op,plan,self.p.clock()+10)
            self.assertEqual(blocked.exception.error['code'],'ASSET_IN_USE')
        finally:os.close(held)
        creation.usage(self.m,op,plan,self.p.clock()+10)

    def test_S5_T05_published_dhcp_confirmed_recovers_after_deadline_without_external_effects(self):
        self.select();ticks=[10.0];self.p.clock=lambda:ticks[0]
        def interrupted(phase,current):
            if phase=='dhcp-applied':raise Interrupted()
        self.m.checkpoint=interrupted
        with self.linux_boundaries(),self.assertRaises(Interrupted):self.m.call('create',self.spec,execute=True)
        self.m.checkpoint=lambda *args:None
        op=self.m._operations()[0];ticks[0]=op['creation']['deadline_monotonic_ms']/1000+1
        record=self.p.records(validate_assets=False)[op['assigned']['internal_name']]
        # 미완료 create의 HTTP stop 지원을 가정하지 않는다. 실제 짧은 admission 원시 연산으로
        # 이미 수락된 stopped 의도를 구성하고 결과 정산의 비덮어쓰기를 확인한다.
        self.p.execution().accept(record,{'vm_id':op['vm_id'],'action':'stop','force':False},
                                  key='late-user-stop',deadline=ticks[0]+1)
        intent=self.p.execution().intent(record)
        self.assertEqual(intent['desired_state'],'stopped')
        with patch.object(self.p,'prepare_dhcp',side_effect=AssertionError('no late DHCP replay')):
            completed=self.m.call('create',self.spec,execute=True)
        self.assertTrue(completed['ok'],completed)
        self.assertEqual(self.p.execution().intent(record),intent)
        self.assertEqual(self.m._operations()[0]['creation'],op['creation'])

    def test_S5_T09_quiesce_stops_cleanup_and_explicit_replay_after_reopen_finishes(self):
        op=self.fail_copy();request=self.request(op)
        def gate(phase,current):
            if phase=='reconcile-before-delete':self.p.execution().quiesce('owned-test-shutdown')
        self.m.checkpoint=gate
        failed=self.m.call('operation-reconcile',request,execute=True)
        self.assertFalse(failed['ok'],failed)
        self.assertTrue((self.f.data/op['owned_workspace']).exists())
        barrier=self.p.execution().barrier()
        self.p.execution().resume_host(barrier['barrier_id']);self.m.checkpoint=lambda *args:None
        self.finish_and_create(op,request)

    def killed_cleanup(self,boundary):
        op=self.fail_copy();request=self.request(op)
        ready_read,ready_write=os.pipe();release_read,release_write=os.pipe();pid=None
        try:
            pid=os.fork()
            if pid==0:
                def checkpoint(phase,current):
                    if phase==boundary:
                        os.write(ready_write,b'1');os.read(release_read,1)
                self.m.checkpoint=checkpoint
                try:self.m.call('operation-reconcile',request,execute=True)
                finally:os._exit(3)
            self.assertTrue(select.select([ready_read],[],[],5)[0],'owned cleanup did not reach boundary')
            self.assertEqual(os.read(ready_read,1),b'1')
            os.kill(pid,signal.SIGKILL);_,status=os.waitpid(pid,0);pid=None
            self.assertTrue(os.WIFSIGNALED(status))
            result=self.cold(request);self.assertTrue(result['ok'],result)
            self.finish_and_create(op,request)
            self.evidence('S5-T07-kill',{'boundary':boundary,'real_sigkill':True,'fresh_process':result})
        finally:
            if pid is not None:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
                os.waitpid(pid,0)
            for fd in (ready_read,ready_write,release_read,release_write):os.close(fd)

    def test_S5_T07_sigkill_after_unlink_then_fresh_process_finishes(self):
        self.killed_cleanup('reconcile-after-delete')

    def test_S5_T07_sigkill_after_root_removal_then_fresh_process_finishes(self):
        self.killed_cleanup('reconcile-before-complete')

    def test_S5_T16_settlement_releases_active_reservations_but_keeps_original_uuid_history(self):
        op=self.fail_copy();self.assertTrue(creation.holds(self.m,op));request=self.request(op)
        result=self.m.call('operation-reconcile',request,execute=True)
        self.assertTrue(result['ok'],result)
        settled=self.m._operations()[0];self.assertFalse(creation.holds(self.m,settled))
        _,ips=self.m._ip_inventory(self.p.records(validate_assets=False))
        self.assertNotIn(op['assigned']['ipv4'],ips)
        rejected=self.m.call('create',self.spec,execute=True,idempotency_key='new-key-old-uuid')
        self.error(rejected,op['error']['code'])
        self.assertTrue(rejected['replayed'])  # create는 기존 create:UUID 정규 key를 유지한다.
        self.assertEqual(rejected['operation_id'],op['operation_id'])
        with self.assertRaises(management.ManagementError) as conflict:
            self.m._conflicts(self.spec,self.p.records(validate_assets=False))
        self.assertEqual(conflict.exception.error['code'],'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.m._operations()[0]['error'],op['error'])

    def test_S5_T02_cleanup_margin_is_shared_across_original_creation_phases(self):
        ticks=[10.0];state={}
        with ownership.creation_work(20,lambda:ticks[0],100,state=state):
            with ownership.cleanup_output():ownership.check_creation()
        first=state['cleanup_deadline'];ticks[0]=10.2
        with ownership.creation_work(20,lambda:ticks[0],100,state=state):
            with ownership.cleanup_output(),self.assertRaises(management.ExecutionError) as expired:
                ownership.check_creation()
        self.assertEqual(expired.exception.error['code'],'DEADLINE_EXCEEDED')
        self.assertEqual(state['cleanup_deadline'],first)

    def test_S5_T03_other_boot_keeps_original_deadline_and_can_discard_private(self):
        op=self.fail_copy();timing=dict(op['creation'])
        self.p.boot_id_reader=lambda:'950e8400-e29b-41d4-a716-446655440000'
        with self.assertRaises(management.ManagementError) as blocked:
            with creation.work(self.m,op,{'entered':self.p.clock(),'budget_ms':10000}):pass
        self.assertEqual(blocked.exception.error['code'],'TARGET_CHANGED')
        result=self.m.call('operation-reconcile',self.request(op),execute=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.m._operations()[0]['creation'],timing)

    def test_S5_T12_lower_cli_diagnosis_is_strict_and_does_not_apply_cleanup(self):
        op=self.fail_copy();request=self.request(op)
        request.pop('reconciliation_id');request.pop('expected_context_sha256')
        path=self.f.data/'diagnostic-request.json';path.write_text(json.dumps(request))
        before=(self.f.data/'operations'/(op['operation_id']+'.json')).read_bytes()
        stream=io.StringIO()
        with patch.object(cli,'Manager',return_value=self.m),redirect_stdout(stream):
            result=vm.main(['operation-reconcile','--spec-file',str(path),'--budget-ms','15000','--json'])
        self.assertTrue(result['ok'],result);self.assertTrue(json.loads(stream.getvalue())['ok'])
        self.assertEqual(before,(self.f.data/'operations'/(op['operation_id']+'.json')).read_bytes())
        refused=self.m.call('operation-reconcile',{**request,'path':'/outside-owned-fixture'},execute=True)
        self.error(refused,'INVALID_SPEC')

    def test_S5_T06_broken_public_symlink_before_completion_does_not_release(self):
        op=self.fail_copy();request=self.request(op)
        target=self.f.data/'instances'/op['assigned']['internal_name']
        def conflict(phase,current):
            if phase=='reconcile-before-complete':target.symlink_to(self.f.data/'missing-outside-workspace')
        self.m.checkpoint=conflict
        refused=self.m.call('operation-reconcile',request,execute=True)
        self.error(refused,'TARGET_CHANGED');self.assertTrue(target.is_symlink())
        self.assertFalse(self.m._operations()[0]['reconciliation']['release'])

    def test_S5_T08_completed_reconciliation_id_cannot_be_replaced(self):
        op=self.fail_copy();request=self.request(op)
        completed=self.m.call('operation-reconcile',request,execute=True)
        self.assertTrue(completed['ok'],completed)
        changed=self.m.call('operation-reconcile',{**request,'reconciliation_id':'a50e8400-e29b-41d4-a716-446655440000'},execute=True)
        self.error(changed,'IDEMPOTENCY_CONFLICT')

    def test_S5_T06_unexplained_missing_other_asset_blocks_before_first_unlink(self):
        op=self.fail_copy();request=self.request(op);root=self.f.data/op['owned_workspace']
        disk=root/op['assigned']['internal_name']/'root.raw';original=disk.read_bytes();inode=disk.stat().st_ino
        changed=[False]
        def remove_after_plan(phase,current):
            if current.get('reconciliation',{}).get('phase')=='planned' and not changed[0]:
                changed[0]=True;(root/'.preparation-resources.json').unlink()
        self.m.checkpoint=remove_after_plan
        refused=self.m.call('operation-reconcile',request,execute=True)
        self.error(refused,'TARGET_CHANGED')
        self.assertEqual((disk.stat().st_ino,disk.read_bytes()),(inode,original))

    def test_S5_T01_expiry_during_acceptance_keeps_original_record_without_preparation(self):
        self.select();ticks=[10.0];self.p.clock=lambda:ticks[0]
        self.p.creation_policy_path.write_text(json.dumps({'schema_version':'1.0','call_ms':1000,'execution_ms':100,'cleanup_ms':100}))
        def slow_record(phase,current):
            if phase=='recorded':ticks[0]=10.2
        self.m.checkpoint=slow_record
        with patch.object(preparation,'make_instance',side_effect=AssertionError('expired acceptance must not prepare')):
            result=self.m.call('create',self.spec,execute=True)
        self.error(result,'DEADLINE_EXCEEDED')
        op=self.m._operations()[0];timing=dict(op['creation'])
        self.assertEqual(timing['deadline_monotonic_ms'],10100)
        self.assertFalse((self.f.data/op['owned_workspace']).exists())
        self.m.checkpoint=lambda *args:None
        replay=self.m.fork().call('create',self.spec,execute=True,budget_ms=1000)
        self.error(replay,'DEADLINE_EXCEEDED')
        self.assertEqual(replay['operation_id'],op['operation_id'])
        self.assertEqual(self.m._operations()[0]['creation'],timing)

    def expire_stage(self,distribution,boundary):
        self.select(distribution);ticks=[10.0];self.p.clock=lambda:ticks[0]
        self.p.creation_policy_path.write_text(json.dumps({'schema_version':'1.0','call_ms':1000,'execution_ms':100,'cleanup_ms':100}))
        def expire(*args,**kwargs):ticks[0]=10.2
        def checkpoint(phase,current):
            if phase==boundary:expire()
        self.m.checkpoint=checkpoint
        with self.linux_boundaries():
            with patch.object(preparation,'prepare_instance',side_effect=expire if boundary=='guest' else None):
                result=self.m.call('create',self.spec,execute=True)
        self.error(result,'DEADLINE_EXCEEDED')
        op=self.m._operations()[0]
        self.assertFalse((self.f.data/'instances'/op['assigned']['internal_name']).exists())
        self.assertTrue((self.f.data/op['owned_workspace']).exists())
        self.m.checkpoint=lambda *args:None
        self.finish_and_create(op,self.request(op))

    def test_S5_T02_ubuntu_guest_return_after_deadline_cannot_publish(self):
        self.expire_stage('ubuntu','guest')

    def test_S5_T02_debian_expiry_immediately_before_publish_keeps_private(self):
        self.expire_stage('debian','before-publish')

    def test_S5_T02_owned_child_timeout_wait_uses_only_cleanup_margin(self):
        waits=[];popen=subprocess.Popen
        def child(*args,**kwargs):
            process=popen(*args,**kwargs);wait=process.wait
            def bounded_wait(*args,**kwargs):
                waits.append(kwargs.get('timeout'));return wait(*args,**kwargs)
            process.wait=bounded_wait
            return process
        with ownership.request_output(),ownership.creation_work(time.monotonic()+.03,time.monotonic,50):
            with patch.object(ownership.subprocess,'Popen',child),self.assertRaises(subprocess.TimeoutExpired):
                ownership.run([sys.executable,'-B','-c','import time;time.sleep(5)'],timeout=10)
        self.assertTrue(waits)
        self.assertTrue(all(value is not None and value<=.05 for value in waits),waits)

    def test_S5_T06_unsafe_links_fifo_and_permissions_do_not_start_discard(self):
        op=self.fail_copy();request=self.request(op);root=self.f.data/op['owned_workspace']
        stage=root/op['assigned']['internal_name'];disk=stage/'root.raw';extra=stage/'initrd'
        before=(disk.stat().st_ino,disk.read_bytes())
        for kind in ('symlink','hardlink','fifo','permissions'):
            with self.subTest(kind=kind):
                if kind=='symlink':extra.symlink_to(disk)
                elif kind=='hardlink':os.link(disk,extra)
                elif kind=='fifo':os.mkfifo(extra)
                else:root.chmod(0o755)
                try:
                    refused=self.m.call('operation-reconcile',request,execute=True)
                    self.error(refused,'ASSET_IN_USE')
                    self.assertNotIn('reconciliation',self.m._operations()[0])
                    self.assertEqual((disk.stat().st_ino,disk.read_bytes()),before)
                finally:
                    if kind=='permissions':root.chmod(0o700)
                    else:extra.unlink()
        self.finish_and_create(op,request)

    def test_S5_T04_loop_use_and_observation_failure_block_then_same_discard_finishes(self):
        op=self.fail_copy();request=self.request(op);command=self.p.timed_command
        root=self.f.data/op['owned_workspace'];disk=root/op['assigned']['internal_name']/'root.raw'
        def loop(args,*rest,**kwargs):
            if args[0]=='losetup':return json.dumps({'loopdevices':[{'name':'/dev/owned-fixture-loop','back-file':str(disk)}]})
            return command(args,*rest,**kwargs)
        with patch.object(self.p,'timed_command',loop):
            self.error(self.m.call('operation-reconcile',request,execute=True),'ASSET_IN_USE')
        def unreadable(args,*rest,**kwargs):
            if args[0]=='losetup':raise OSError(errno.EIO,'owned usage observation failure')
            return command(args,*rest,**kwargs)
        with patch.object(self.p,'timed_command',unreadable):
            result=self.m.call('operation-reconcile',request,execute=True)
            self.assertFalse(result['ok'],result)
        self.assertTrue(disk.exists());self.assertNotIn('reconciliation',self.m._operations()[0])
        self.finish_and_create(op,request)

    def test_S5_T02_expired_work_closes_owned_mounts_and_loops_in_cleanup_margin(self):
        ticks=[10.0];calls=[]
        with ownership.creation_work(10.1,lambda:ticks[0],100):
            work=preparation.Workspace(self.f.data)
            work.mounts=[work.path/'root',work.path/'root/dev'];work.loops=['/dev/loop987','/dev/loop988']
            ticks[0]=10.2
            with patch.object(preparation,'command',side_effect=lambda args,**kw:calls.append(args)):
                with ownership.cleanup_output():work.close();work.seal_clean()
        self.assertEqual(calls,[['umount',work.path/'root/dev'],['umount',work.path/'root'],
            ['losetup','--detach','/dev/loop988'],['losetup','--detach','/dev/loop987']])
        self.assertTrue(json.loads((work.path/'.preparation-resources.json').read_text())['clean'])

    def test_S5_T04_unconfirmed_child_defers_workspace_close_until_exit_confirmed(self):
        with ownership.request_output() as output:
            work=preparation.Workspace(self.f.data)
            work.mounts=[work.path/'root'];work.loops=['/dev/loop987'];output.unconfirmed_child=True
            with patch.object(preparation,'command') as command,self.assertRaisesRegex(ValueError,'child exit unconfirmed'):
                work.close()
            command.assert_not_called();self.assertEqual(work.loops,['/dev/loop987'])
            output.unconfirmed_child=False
            with patch.object(preparation,'command') as command:
                work.close();work.seal_clean()
            self.assertEqual(command.call_count,2)

    def test_S5_T16_cpu_guard_stops_treating_settled_failure_as_active_operation(self):
        op=self.fail_copy()
        with self.assertRaises(management.ExecutionError) as held:
            self.p.cpu().guard_stopped({}, {'domains':{}})
        self.assertEqual(held.exception.error['code'],'OPERATION_IN_PROGRESS')
        request=self.request(op);settled=self.m.call('operation-reconcile',request,execute=True)
        self.assertTrue(settled['ok'],settled)
        # CPU 정책 변경 자체를 수행하지 않고 실제 전역 예약 guard의 회복을 확인한다.
        self.p.cpu().guard_stopped({}, {'domains':{}})


if __name__=='__main__':unittest.main()
