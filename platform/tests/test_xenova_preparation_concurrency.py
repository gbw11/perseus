"""실제 Linux 복사 barrier·Manager·원장·flock으로 진행성과 소유 보호를 함께 검증한다.

Xen 명령·mount/chroot·guest 설정은 기존 외부 대역이다. FD/프로세스/파일은 실제다.
"""
from contextlib import contextmanager
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

from first_install_fixture import FILES
from test_xenova_spring_followup import FollowupFixture
from test_xenova_linux_template_paths import DefaultLinuxTemplatePathTest
import test_xenova_management as fixtures
import prepare_instance as preparation
import xenova_management as management
import xenova_management_cli as cli
import xenova_preparation as ownership
import xenova_vm as vm
from xenova_runtime import PlatformError, OperationBusy, operation_lock


class PreparationConcurrencyTest(FollowupFixture):
    def other_vm(self):
        identity=self.f.create()
        self.f.xen.records=self.p.records(validate_assets=False)
        readiness=patch.object(self.p,'check_xen')
        readiness.start();self.addCleanup(readiness.stop)
        started=self.m.call('start',{'vm_id':identity},idempotency_key='other-start')
        self.assertTrue(started['ok'],started)
        return identity

    def select(self, distribution='debian'):
        entry,source=DefaultLinuxTemplatePathTest.select_default(self,distribution)
        self.spec.update(vm_id='550e8400-e29b-41d4-a716-446655440001',name='private-'+distribution)
        return entry,source

    @contextmanager
    def blocked(self, distribution='debian', *, other=True):
        other_id=self.other_vm() if other else None
        entry,source=self.select(distribution)
        entered,release=threading.Event(),threading.Event()
        original_write=os.write
        result={'entered':entered,'release':release,'entry':entry,'source':source,
                'other':other_id,'writes':0,'expect_ok':True}

        def write(fd,data):
            count=original_write(fd,data)
            path=Path(os.readlink('/proc/self/fd/'+str(fd)))
            if path.name=='root.raw' and path.parent.name==management.internal_name(self.spec['vm_id']):
                result['writes']+=1
                if not entered.is_set():
                    ownership.diagnostic('prepare-A-only')
                    entered.set()
                    if not release.wait(15):raise RuntimeError('owned copy barrier not released')
            return count

        with self.linux_boundaries(),patch.object(preparation.os,'write',side_effect=write):
            worker=self.m.fork()
            result['worker']=worker

            def create():
                try:result['response']=worker.call('create',self.spec,execute=True,request_id='prepare-A')
                except BaseException as error:result['exception']=error

            thread=threading.Thread(target=create,name='owned-linux-preparer')
            result['thread']=thread
            thread.start()
            try:
                self.assertTrue(entered.wait(10),result)
                self.assertTrue(thread.is_alive())
                result['op']=next(o for o in self.m._operations() if o['vm_id']==self.spec['vm_id'])
                result['partial']=self.f.data/result['op']['owned_workspace']/management.internal_name(self.spec['vm_id'])/'root.raw'
                yield result
            finally:
                release.set();thread.join(15)
            self.assertFalse(thread.is_alive(),'preparation did not terminate after owned release')
            self.assertNotIn('exception',result,result)
            self.assertEqual(result['response']['ok'],result['expect_ok'],result['response'])
            self.last=result

    def core_progress(self, distribution):
        with self.blocked(distribution) as state:
            before=(state['partial'].stat().st_ino,state['partial'].read_bytes())
            request=self.m.fork()
            listed=request.call('list',budget_ms=2000,request_id='list-B')
            detailed=request.call('get',{'vm_id':state['other']},budget_ms=2000,request_id='get-B')
            queried=request.call('operation',{'idempotency_key':'create:'+self.spec['vm_id']},budget_ms=2000)
            for response in (listed,detailed,queried):self.assertTrue(response['ok'],response)
            self.assertEqual(queried['data']['effective_status'],'RUNNING')
            self.assertFalse(queried['data']['recovery_required'])
            self.assertEqual(queried['data']['operation'],state['op'])
            self.assertEqual([v['vm_id'] for v in listed['data']['vms']],[state['other']])
            key='stop-B-during-copy'
            stopped=request.call('stop',{'vm_id':state['other']},idempotency_key=key,budget_ms=2000,request_id='stop-B')
            self.assertTrue(stopped['ok'],stopped)
            self.assertEqual(stopped['data']['original_result']['observation']['presence'],'absent')
            control=request.call('control-operation',{'idempotency_key':key})
            self.assertTrue(control['ok'],control)
            self.assertEqual(control['data']['operation']['phase'],'completed')
            self.assertTrue(any(c[:2]==['xl','shutdown'] for c in self.f.xen.calls))
            self.assertFalse(any(c[:2]==['xl','destroy'] for c in self.f.xen.calls))
            self.assertFalse(state['release'].is_set())
            self.assertTrue(state['thread'].is_alive())
            self.assertEqual((state['partial'].stat().st_ino,state['partial'].read_bytes()),before)
            self.assertEqual(next(o for o in self.m._operations() if o['vm_id']==self.spec['vm_id']),state['op'])
            output=os.environ.get('XENOVA_STAGE3_EVIDENCE')
            if output:
                evidence={'kind':'local barrier acceptance; external Xen/mount/chroot boundaries are simulated',
                    'distribution':distribution,'a_operation':state['op'],'a_released':state['release'].is_set(),
                    'a_thread_alive':state['thread'].is_alive(),'a_root_inode':before[0],
                    'list':listed,'get':detailed,'a_lookup':queried,'b_stop':stopped,'b_control_lookup':control,
                    'physical_shutdown_calls':[c for c in self.f.xen.calls if c[:2]==['xl','shutdown']],
                    'same_host_monotonic_ns':time.monotonic_ns()}
                with (Path(output)/('S3-T01-'+distribution+'-before-release.json')).open('x') as stream:
                    json.dump(evidence,stream,indent=2)
        self.assertEqual(self.last['response']['operation_id'],self.last['op']['operation_id'])
        self.assertEqual(self.m.call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})['data']['effective_status'],'SUCCEEDED')
        if os.environ.get('XENOVA_STAGE3_EVIDENCE'):
            with (Path(os.environ['XENOVA_STAGE3_EVIDENCE'])/('S3-T01-'+distribution+'-after-release.json')).open('x') as stream:
                json.dump(self.last['response'],stream,indent=2)

    def test_S3_T01_debian_queries_and_actual_other_stop_complete_before_copy_release(self):
        self.core_progress('debian')

    def test_S3_T01_ubuntu_queries_and_actual_other_stop_complete_before_copy_release(self):
        self.core_progress('ubuntu')

    def child_replay(self):
        code='''
import json,sys
from pathlib import Path
from unittest.mock import patch
from test_xenova_platform import FakeXen
import xenova_vm as vm
import xenova_management as management
data,run,defaults=sys.argv[1:]
p=vm.Platform(Path(data),Path(run),command=FakeXen(Path(data),{}),defaults_path=Path(defaults))
with patch.object(vm,'storage_check'):
 print(json.dumps(management.Manager(p).call('create',json.loads(sys.stdin.read()),execute=True)))
'''
        child=subprocess.run([sys.executable,'-B','-c',code,str(self.f.data),str(self.f.run),str(FILES/'defaults.json')],
            input=json.dumps(self.spec),text=True,capture_output=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        return json.loads(child.stdout)

    def test_S3_T02_thread_and_fresh_process_replay_use_the_same_live_origin(self):
        with self.blocked() as state:
            replies=[]
            duplicate=threading.Thread(target=lambda:replies.append(self.m.fork().call('create',self.spec,execute=True)))
            duplicate.start();duplicate.join(5)
            self.assertFalse(duplicate.is_alive())
            replies.append(self.child_replay())
            for response in replies:
                self.assertTrue(response['ok'],response)
                self.assertTrue(response['replayed'])
                self.assertEqual(response['operation_id'],state['op']['operation_id'])
                self.assertEqual(response['data']['effective_status'],'RUNNING')
                self.assertEqual(response['data']['operation']['assigned'],state['op']['assigned'])
            self.assertEqual(state['writes'],1)
            self.assertEqual(len(self.m._operations()),2)
            self.assertFalse((self.f.data/'instances'/management.internal_name(self.spec['vm_id'])).exists())

    def test_S3_T02_simultaneous_fresh_admission_rechecks_the_winning_key(self):
        self.select()
        admissions=threading.Barrier(2)
        copying,release,replayed=threading.Event(),threading.Event(),threading.Event()
        first_preflight=threading.Event()
        enter=ownership.LinuxSlot.__enter__
        copy=preparation._copy_verified
        calls=[];replies=[];errors=[]

        def acquire(slot):
            first_preflight.set()
            admissions.wait(timeout=10)
            try:return enter(slot)
            except OperationBusy:
                if not copying.wait(10):raise RuntimeError('winning admission was not observed')
                raise

        def copying_asset(*args,**kwargs):
            if args[0].name=='root.raw':
                calls.append(args[0]);copying.set()
                if not release.wait(10):raise RuntimeError('owned concurrent admission barrier timed out')
            return copy(*args,**kwargs)

        with self.linux_boundaries(),patch.object(ownership.LinuxSlot,'__enter__',acquire), \
                patch.object(preparation,'_copy_verified',side_effect=copying_asset):
            def create():
                try:
                    response=self.m.fork().call('create',self.spec,execute=True)
                    replies.append(response)
                    if response.get('data',{}).get('effective_status')=='RUNNING':replayed.set()
                except BaseException as error:errors.append(error)
            threads=[threading.Thread(target=create) for _ in range(2)]
            threads[0].start()
            try:
                self.assertTrue(first_preflight.wait(10))
                threads[1].start()
                self.assertTrue(copying.wait(10))
                self.assertTrue(replayed.wait(10))
                self.assertEqual(len(calls),1)
                self.assertEqual(len(self.m._operations()),1)
            finally:
                release.set()
                for thread in threads:
                    if thread.ident is not None:thread.join(10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors,[])
        self.assertEqual(len(replies),2)
        self.assertTrue(all(r['ok'] for r in replies),replies)
        self.assertEqual(len({r['operation_id'] for r in replies}),1)
        self.assertEqual(len(calls),1)

    def test_S3_T03_new_slot_collision_is_unaccepted_then_explicit_request_can_progress(self):
        with self.blocked() as state:
            new={**self.spec,'vm_id':'550e8400-e29b-41d4-a716-446655440002','name':'second'}
            response=self.m.fork().call('create',new,execute=True)
            self.error(response,'OPERATION_IN_PROGRESS')
            self.assertIs(response['error']['details']['accepted'],False)
            self.assertIsNone(response['operation_id'])
            self.assertEqual(len(self.m._operations()),2)
            self.assertFalse(any(o['vm_id']==new['vm_id'] for o in self.m._operations()))
            completed=self.m.fork().call('create',self.f.spec,execute=True)
            self.assertTrue(completed['ok'],completed)
            self.assertTrue(completed['replayed'])
        with self.linux_boundaries():
            response=self.m.call('create',new,execute=True)
        self.assertTrue(response['ok'],response)

    def test_S3_T04_conflicting_spec_and_unpublished_vm_changes_do_not_mutate_the_origin(self):
        with self.blocked() as state:
            self.error(self.m.fork().call('create',{**self.spec,'memory_mib':1024},execute=True),'IDEMPOTENCY_CONFLICT')
            for action in ('start','stop','retire'):
                response=self.m.fork().call(action,{'vm_id':self.spec['vm_id']},execute=True,idempotency_key='conflict-'+action)
                self.error(response,'VM_NOT_FOUND')
            self.assertEqual(next(o for o in self.m._operations() if o['vm_id']==self.spec['vm_id']),state['op'])
            self.assertEqual(state['writes'],1)

    def recovery_during_preparation(self, distribution):
        with self.blocked(distribution,other=False) as state:
            identity=self.spec['vm_id']
            attempt='650e8400-e29b-41d4-a716-446655440001'
            request={'vm_id':identity,'recovery_attempt_id':attempt,
                'expected_host_boot_id':self.p.boot_id_reader(),'expected_lifecycle_generation':0,
                'expected_intent_revision':1,'expected_execution':{'absent_observation_revision':1},
                'mode':'start-if-absent','deadline_monotonic_ms':int((self.p.clock()+10)*1000)}
            operation_path=self.f.data/'operations'/(state['op']['operation_id']+'.json')
            owner_path=self.p.runtime/'locks'/('linux-owner-'+state['op']['operation_id']+'.lock')
            workspace=self.f.data/state['op']['owned_workspace']
            files=[operation_path,owner_path,*[p for p in workspace.rglob('*') if p.is_file()]]
            before={path:(path.stat().st_ino,path.read_bytes()) for path in files}
            calls=list(self.f.xen.calls)
            responses=[]
            # 미게시 VM에는 아직 복구할 등록 실행/attempt가 없다. 부재를 create 재개로 바꾸지 않는다.
            for execute in (False,True):
                response=self.m.fork().call('recover_once',request,execute=execute)
                self.error(response,'VM_NOT_FOUND')
                responses.append(response)
                response=self.m.fork().call('recovery-reconcile',{'recovery_attempt_id':attempt},
                                           execute=execute,budget_ms=2000)
                self.error(response,'VM_NOT_FOUND')
                responses.append(response)
            response=self.m.fork().call('recovery-operation',{'recovery_attempt_id':attempt})
            self.error(response,'VM_NOT_FOUND')
            responses.append(response)
            self.assertEqual(list(self.f.xen.calls),calls)
            self.assertFalse((self.f.data/'recovery-attempts'/(attempt+'.json')).exists())
            self.assertFalse((self.f.data/'control'/(identity+'.json')).exists())
            self.assertFalse((self.f.data/'instances'/management.internal_name(identity)).exists())
            self.assertEqual({path:(path.stat().st_ino,path.read_bytes()) for path in files},before)
            self.assertEqual({p for p in workspace.rglob('*') if p.is_file()},set(files)-{operation_path,owner_path})
            current=self.m.fork().call('operation',{'idempotency_key':'create:'+identity},budget_ms=2000)
            self.assertTrue(current['ok'],current)
            self.assertEqual(current['data']['effective_status'],'RUNNING')
            self.assertEqual(current['data']['operation'],state['op'])
            self.assertEqual(state['writes'],1)
            self.assertFalse(state['release'].is_set())
            self.assertTrue(state['thread'].is_alive())
            if os.environ.get('XENOVA_STAGE3_EVIDENCE'):
                evidence={'kind':'same unpublished VM recovery entry during actual paused Linux copy',
                    'distribution':distribution,'a_operation':state['op'],'a_released':False,
                    'recovery_responses':responses,'a_lookup':current,'new_xen_calls':[],
                    'same_partial_assets':True,'same_owner_record':True,'root_writes':state['writes']}
                with (Path(os.environ['XENOVA_STAGE3_EVIDENCE'])/('S3-T04-'+distribution+'-recovery-before-release.json')).open('x') as stream:
                    json.dump(evidence,stream,indent=2)
        self.assertEqual(self.last['response']['operation_id'],self.last['op']['operation_id'])
        self.assertTrue(self.last['response']['ok'],self.last['response'])

    def test_S3_T04_debian_recovery_entries_do_not_reclaim_live_unpublished_preparation(self):
        self.recovery_during_preparation('debian')

    def test_S3_T04_ubuntu_recovery_entries_do_not_reclaim_live_unpublished_preparation(self):
        self.recovery_during_preparation('ubuntu')

    def test_S3_T05_adoption_preserves_other_configuration_and_reserved_address(self):
        with self.blocked() as state:
            changed=self.m.fork().call('autostart-set',{'vm_id':state['other'],'value':'on'})
            self.assertTrue(changed['ok'],changed)
            foreign={**self.spec,'vm_id':'550e8400-e29b-41d4-a716-446655440003',
                     'ipv4':state['op']['assigned']['ipv4']}
            self.error(self.m.fork().call('create',foreign,execute=True),'IP_CONFLICT')
            other_before=self.p.records(validate_assets=False)[self.f.key]
            source=fixtures.application_bundle(fixtures.bundle(self.f.data/'legacy-firmware'),self.f.data/'rtos-temp')
            manifest=source/'firmware.json'
            meta=json.loads(manifest.read_text());meta.update(id='stage3-firmware',kind='zephyr-xen-firmware',version='1.0.0')
            manifest.chmod(0o644);manifest.write_text(json.dumps(meta));manifest.chmod(0o444)
            (self.f.data/'rtos-templates').mkdir()
            source.rename(self.f.data/'rtos-templates/stage3-firmware')
            descriptor={'schema_version':'1.0','template_id':'zephyr-xenvm-starter-arm64','template_version':'1.0.0',
                'guest_kind':'zephyr','asset_directory':'rtos-templates/stage3-firmware','source_template_id':'stage3-firmware',
                'manifest_sha256':fixtures.catalog.sha(self.f.data/'rtos-templates/stage3-firmware/firmware.json')}
            catalog=self.f.data/'catalog/zephyr-xenvm-starter-arm64';catalog.mkdir()
            (catalog/'1.0.0.json').write_text(json.dumps(descriptor))
            registered=self.m.fork().call('create',{'vm_id':'650e8400-e29b-41d4-a716-446655440000',
                'name':'new-firmware','template_id':descriptor['template_id'],'template_version':'1.0.0','vcpus':1,'memory_mib':16},execute=True)
            self.assertTrue(registered['ok'],registered)
            firmware_before=self.p.records(validate_assets=False)[management.internal_name(registered['data']['vm_id'])]
        self.assertEqual(self.p.records(validate_assets=False)[self.f.key],other_before)
        self.assertEqual(self.p.records(validate_assets=False)[firmware_before['id']],firmware_before)
        self.assertTrue(other_before['autostart'])
        self.assertNotEqual(self.last['response']['data']['ipv4'],other_before['ipv4'])

    def test_S3_T06_completed_record_wins_while_owner_lock_is_still_held(self):
        closing,finish=threading.Event(),threading.Event()
        original=ownership.LinuxSlot.__exit__

        def close(slot,*args):
            if slot.owner_fd is not None and threading.current_thread().name=='owned-linux-preparer':
                closing.set()
                if not finish.wait(10):raise RuntimeError('owned close barrier timed out')
            return original(slot,*args)

        with patch.object(ownership.LinuxSlot,'__exit__',close),self.blocked() as state:
            try:
                state['release'].set()
                self.assertTrue(closing.wait(10))
                response=self.m.fork().call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})
                self.assertTrue(response['ok'],response)
                self.assertEqual(response['data']['effective_status'],'SUCCEEDED')
                self.assertTrue(state['thread'].is_alive())
            finally:finish.set()

    def test_S3_T06_completed_replay_preserves_original_success_when_current_observation_fails(self):
        with self.blocked() as state:
            before=next(o for o in self.m._operations() if o['vm_id']==state['other'])
            with patch.object(management.Manager,'_observe_view',side_effect=OSError('injected current observation failure')):
                response=self.m.fork().call('create',self.f.spec,execute=True)
            self.assertTrue(response['ok'],response)
            self.assertIsNone(response['data']['current'])
            self.assertEqual(response['data']['original_result'],before['result'])
            self.assertEqual(next(o for o in self.m._operations() if o['vm_id']==state['other']),before)

    def test_S3_T07_owner_read_failure_and_expired_lookup_do_not_change_the_origin(self):
        with self.blocked() as state:
            original_open=os.open

            def open_file(path,*args,**kwargs):
                if Path(path).name=='linux-owner-'+state['op']['operation_id']+'.lock':
                    raise PermissionError('injected ownership read failure')
                return original_open(path,*args,**kwargs)

            with patch.object(ownership.os,'open',side_effect=open_file):
                response=self.m.fork().call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})
            self.error(response,'OBSERVATION_UNAVAILABLE')
            request=self.m.fork()
            ticks=[0.0]
            def clock():ticks[0]+=.01;return ticks[0]
            request.p.clock=clock
            self.error(request.call('operation',{'idempotency_key':'create:'+self.spec['vm_id']},budget_ms=1),'DEADLINE_EXCEEDED')
            self.assertEqual(next(o for o in self.m._operations() if o['vm_id']==self.spec['vm_id']),state['op'])

    def test_S3_T07_asset_scan_read_format_integrity_and_duplicate_errors_are_distinct(self):
        with self.blocked(other=False) as state:
            root=self.f.data/'operations'
            path=root/(state['op']['operation_id']+'.json')
            before=path.read_bytes()
            read_text,iterdir=Path.read_text,Path.iterdir
            results={}

            def query(code,label):
                response=self.m.fork().call('operation',{'idempotency_key':'create:'+self.spec['vm_id']},budget_ms=2000)
                self.error(response,code)
                self.assertIsNone(response['data'])
                results[label]=response

            def unreadable(file,*args,**kwargs):
                if file==path:raise PermissionError('injected journal read failure')
                return read_text(file,*args,**kwargs)

            def partial_scan(directory):
                yield from iterdir(directory)
                if directory==root:raise OSError('injected incomplete directory read')

            with patch.object(Path,'read_text',unreadable):
                query('ARTIFACT_INVALID','journal-read-failure')
            with patch.object(Path,'iterdir',partial_scan):
                query('ARTIFACT_INVALID','incomplete-directory-read')
            try:
                path.write_bytes(b'{')
                query('ARTIFACT_INVALID','json-format-failure')
                path.write_text(json.dumps({**state['op'],'spec_hash':'0'*64}))
                query('RECOVERY_REQUIRED','journal-integrity-mismatch')
            finally:path.write_bytes(before)
            duplicate={**state['op'],'operation_id':'650e8400-e29b-41d4-a716-446655440002',
                       'owned_workspace':'instances/.operation-650e8400-e29b-41d4-a716-446655440002'}
            extra=root/(duplicate['operation_id']+'.json')
            try:
                extra.write_text(json.dumps(duplicate))
                query('RECONCILE_REQUIRED','duplicate-key')
            finally:extra.unlink()
            self.assertEqual(path.read_bytes(),before)
            current=self.m.fork().call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})
            self.assertTrue(current['ok'],current)
            self.assertEqual(current['data']['effective_status'],'RUNNING')
            self.assertEqual(state['writes'],1)
            self.assertFalse(state['release'].is_set())
            if os.environ.get('XENOVA_STAGE3_EVIDENCE'):
                with (Path(os.environ['XENOVA_STAGE3_EVIDENCE'])/'S3-T07-asset-lookup-errors.json').open('x') as stream:
                    json.dump({'kind':'owned temporary journal faults; no product state changes',
                               'results':results,'after_faults':current},stream,indent=2)

    def interrupted_private(self, phase):
        self.select()
        self.m.checkpoint=lambda current,op: (_ for _ in ()).throw(fixtures.Interrupted()) if current==phase else None
        with self.linux_boundaries(),self.assertRaises(fixtures.Interrupted):
            self.m.call('create',self.spec,execute=True)
        op=self.m._operations()[0]
        stage=self.f.data/op['owned_workspace']/op['assigned']['internal_name']
        before=(stage/'root.raw').stat().st_ino,(stage/'root.raw').read_bytes()
        self.assertFalse((self.f.data/'instances'/op['assigned']['internal_name']).exists())
        fresh=management.Manager(self.p,defaults_path=self.m.defaults_path)
        with patch.object(preparation,'_copy_verified',side_effect=AssertionError('must not recopy')):
            self.error(fresh.call('create',self.spec,execute=True),'RECOVERY_REQUIRED')
        self.assertEqual((stage/'root.raw').stat().st_ino,before[0])
        self.assertEqual((stage/'root.raw').read_bytes(),before[1])
        self.assertEqual(fresh.call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})['data']['effective_status'],'RECOVERY_REQUIRED')

    def test_private_ready_waits_for_short_publication_contention(self):
        waiting=threading.Event()
        original_sleep=self.p.sleep

        def sleep(seconds):
            waiting.set()
            original_sleep(seconds)

        with patch.object(self.p,'sleep',side_effect=sleep), self.blocked(other=False) as state:
            with operation_lock(self.p.runtime):
                state['release'].set()
                self.assertTrue(waiting.wait(10),'private owner did not wait to publish')
                self.assertTrue(state['thread'].is_alive())
        self.assertEqual(self.last['writes'],1)
        self.assertEqual(self.last['response']['operation_id'],self.last['op']['operation_id'])

    def test_S3_T08_private_ready_interruption_does_not_publish_or_recopy(self):
        self.interrupted_private('private-prepared')

    def test_S3_T08_before_publish_interruption_does_not_publish_or_recopy(self):
        self.interrupted_private('before-publish')

    def test_S3_T08_pre_workspace_failure_resumes_same_origin_and_reports_running(self):
        _,source=self.select()
        boot=source/'boot.json'
        original=boot.read_bytes()
        metadata=json.loads(original)
        metadata['kernel_release']='7'+metadata['kernel_release'][1:]
        changed=json.dumps(metadata).encode()
        self.assertEqual(len(changed),len(original))
        self.assertNotEqual(changed,original)
        boot.chmod(0o644);boot.write_bytes(changed);boot.chmod(0o444)
        with self.linux_boundaries():
            self.error(self.m.call('create',self.spec,execute=True),'RECOVERY_REQUIRED')
        before=self.m._operations()[0]
        self.assertFalse((self.f.data/before['owned_workspace']).exists())
        boot.chmod(0o644);boot.write_bytes(original);boot.chmod(0o444)
        entered,release=threading.Event(),threading.Event()
        copied=[];result=[]
        copy=preparation._copy_verified
        def prepare(*args,**kwargs):
            if args[0].name=='root.raw':
                copied.append(args[0]);entered.set()
                if not release.wait(10):raise RuntimeError('owned resume barrier timed out')
            return copy(*args,**kwargs)
        with self.linux_boundaries(),patch.object(preparation,'_copy_verified',side_effect=prepare):
            request=self.m.fork()
            worker=threading.Thread(target=lambda:result.append(request.call('create',self.spec,execute=True)))
            worker.start()
            try:
                self.assertTrue(entered.wait(10))
                query=self.m.fork().call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})
                self.assertTrue(query['ok'],query)
                self.assertEqual(query['data']['effective_status'],'RUNNING')
                current=query['data']['operation']
                for key in ('operation_id','idempotency_key','request','initial_spec','spec_hash','assigned','template','owned_workspace','error'):
                    self.assertEqual(current[key],before[key])
            finally:release.set();worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]['ok'],result[0])
        self.assertEqual(result[0]['operation_id'],before['operation_id'])
        self.assertEqual(len(copied),1)
        self.assertEqual(len(self.m._operations()),1)

    def test_S3_T08_wrong_private_target_is_refused_before_any_publication(self):
        self.select()
        initialize=preparation.PreparedInstance.__init__
        def wrong(result,*args,**kwargs):
            initialize(result,*args,**kwargs)
            result.output=self.f.data/'instances/foreign-target'
        with self.linux_boundaries(),patch.object(preparation.PreparedInstance,'__init__',wrong):
            response=self.m.call('create',self.spec,execute=True)
        self.error(response,'TARGET_CHANGED')
        self.assertFalse((self.f.data/'instances/foreign-target').exists())
        self.assertFalse((self.f.data/'instances'/management.internal_name(self.spec['vm_id'])).exists())
        self.assertEqual(self.m._operations()[0]['operation_id'],response['operation_id'])

    def test_S3_T09_parent_kill_with_owned_child_keeps_slot_and_blocks_storage_release(self):
        self.select()
        ready_read,ready_write=os.pipe()
        release_read,release_write=os.pipe()
        worker_pid=watch=None
        try:
            with self.linux_boundaries():
                worker_pid=os.fork()
                if worker_pid==0:
                    original_write=os.write
                    entered=[False]
                    def write(fd,data):
                        count=original_write(fd,data)
                        if Path(os.readlink('/proc/self/fd/'+str(fd))).name=='root.raw' and not entered[0]:
                            entered[0]=True
                            script='import os,sys;os.write(int(sys.argv[1]),str(os.getpid()).encode()+b"\\n");os.read(int(sys.argv[2]),1)'
                            ownership.run([sys.executable,'-B','-c',script,str(ready_write),str(release_read)],
                                pass_fds=(ready_write,release_read),check=True)
                        return count
                    try:
                        with patch.object(preparation.os,'write',side_effect=write):
                            self.m.call('create',self.spec,execute=True)
                    finally:os._exit(3)
                self.assertTrue(select.select([ready_read],[],[],10)[0])
                child_pid=int(os.read(ready_read,64).strip())
                watch=os.pidfd_open(child_pid)
                os.kill(worker_pid,signal.SIGKILL)
                _,status=os.waitpid(worker_pid,0);worker_pid=None
                self.assertTrue(os.WIFSIGNALED(status))
                op=self.m._operations()[0]
                self.assertEqual(ownership.owner_state(self.p,op),'residual')
                queried=self.m.call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})
                self.assertTrue(queried['ok'],queried)
                self.assertEqual(queried['data']['effective_status'],'RECOVERY_REQUIRED')
                self.error(self.m.call('create',self.spec,execute=True),'RECOVERY_REQUIRED')
                with self.assertRaises(OperationBusy):ownership.ensure_quiet(self.p)
                self.p.execution().quiesce('owned-parent-kill')
                self.p._shutdown_deadline=self.p.clock()+5
                with self.assertRaises(PlatformError):self.p.finish_board_shutdown('poweroff')
                self.assertFalse(any(c==['systemctl','stop','srv-xenova.mount'] for c in self.f.xen.calls))
                os.write(release_write,b'1')
                self.assertTrue(select.select([watch],[],[],10)[0])
                self.assertEqual(ownership.owner_state(self.p,op),'absent')
                # 자식이 끝나 슬롯이 풀려도 중단된 workspace의 자원 확인이 자동 완료되지는 않는다.
                with self.assertRaisesRegex(PlatformError,'resources remain unconfirmed'):
                    ownership.ensure_quiet(self.p)
        finally:
            os.write(release_write,b'1')
            if worker_pid is not None:
                try:os.kill(worker_pid,signal.SIGKILL)
                except ProcessLookupError:pass
                os.waitpid(worker_pid,0)
            if watch is not None:
                select.select([watch],[],[],10);os.close(watch)
            for fd in (ready_read,ready_write,release_read,release_write):os.close(fd)

    def test_S3_T11_quiesce_drains_outside_common_lock_and_refuses_late_publication(self):
        with self.blocked() as state:
            state['expect_ok']=False
            barrier=self.p.execution().quiesce('test-shutdown')
            blocked,finished=threading.Event(),threading.Event()
            quiet=ownership.ensure_quiet
            errors=[]

            def checked(platform):
                try:return quiet(platform)
                except OperationBusy:
                    blocked.set();raise

            def drain():
                try:ownership.drain(self.p,self.p.clock()+10)
                except BaseException as error:errors.append(error)
                finally:finished.set()

            with patch.object(ownership,'ensure_quiet',side_effect=checked):
                worker=threading.Thread(target=drain)
                worker.start()
                try:
                    self.assertTrue(blocked.wait(5))
                    self.assertFalse(finished.is_set())
                    self.assertTrue(self.m.fork().call('operation',{'idempotency_key':'create:'+self.spec['vm_id']})['ok'])
                    state['release'].set()
                    worker.join(10)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors,[])
                finally:
                    state['release'].set();worker.join(10)
        self.assertIn(self.last['response']['error']['code'],('HOST_QUIESCING','OPERATION_IN_PROGRESS'))
        op=next(o for o in self.m._operations() if o['vm_id']==self.spec['vm_id'])
        self.assertEqual(op['status'],'RECOVERY_REQUIRED')
        self.assertFalse((self.f.data/'instances'/op['assigned']['internal_name']).exists())
        self.assertEqual(self.p.execution().barrier()['barrier_id'],barrier['barrier_id'])
        ownership.ensure_quiet(self.p)

    def test_S3_T11_quiesce_then_resume_cannot_revalidate_the_old_preparation_context(self):
        with self.blocked() as state:
            state['expect_ok']=False
            barrier=self.p.execution().quiesce('test')
            self.p.execution().resume_host(barrier['barrier_id'])
        self.error(self.last['response'],'TARGET_CHANGED')
        self.assertFalse((self.f.data/'instances'/management.internal_name(self.spec['vm_id'])).exists())

    def test_S3_T12_same_process_output_fd_budget_and_trace_do_not_mix(self):
        stdout=sys.stdout
        fd=os.fstat(1)
        with self.blocked() as state:
            request=self.m.fork()
            response=request.call('stop',{'vm_id':state['other']},idempotency_key='isolated-stop',budget_ms=2000,request_id='stop-B')
            self.assertTrue(response['ok'],response)
            self.assertIs(sys.stdout,stdout)
            self.assertEqual((os.fstat(1).st_dev,os.fstat(1).st_ino),(fd.st_dev,fd.st_ino))
            self.assertNotIn(b'prepare-A-only',request.last_output['bytes'])
            self.assertEqual(request.last_trace['context']['request_id'],'stop-B')
            self.assertNotIn('response_deadline',request.p.__dict__)
        self.assertIn(b'prepare-A-only',self.last['worker'].last_output['bytes'])
        self.assertEqual(self.last['worker'].last_trace['context']['request_id'],'prepare-A')

    def test_S3_T14_cli_create_replay_uses_the_same_live_owner(self):
        with self.blocked() as state:
            output=io.StringIO()
            with patch.object(cli,'Manager',return_value=self.m.fork()),patch.object(sys,'stdout',output):
                result=cli.main(['create','--vm-id',self.spec['vm_id'],'--name',self.spec['name'],
                    '--template-id',self.spec['template_id'],'--template-version',self.spec['template_version'],
                    '--vcpus','1','--memory-mib','512','--execute','--json'])
            response=json.loads(output.getvalue())
            self.assertEqual(response['data']['effective_status'],'RUNNING')
            self.assertEqual(response['operation_id'],state['op']['operation_id'])
            self.assertEqual(state['writes'],1)

    def test_S3_T14_direct_live_helper_cannot_bypass_manager_ownership(self):
        from argparse import Namespace
        (self.f.data/'.xenova-storage.json').write_text('{"schema":1}')
        args=Namespace(data_root=self.f.data,id='direct',deployment_seed=False)
        with self.assertRaisesRegex(ValueError,'requires private preparation owned by Manager'):
            preparation.make_instance(args,vm=vm,defaults_path=FILES/'defaults.json')
        self.assertEqual(list((self.f.data/'instances').iterdir()),[])

    def test_S3_T14_legacy_stop_completes_before_other_private_copy_is_released(self):
        with self.blocked() as state:
            output=io.StringIO()
            with patch.object(vm,'Platform',return_value=self.p),patch.object(sys,'stdout',output):
                vm.main(['stop',self.f.key,'--idempotency-key','legacy-stop-during-copy'])
            queried=self.m.fork().call('control-operation',{'idempotency_key':'legacy-stop-during-copy'})
            self.assertTrue(queried['ok'],queried)
            self.assertEqual(queried['data']['operation']['phase'],'completed')
            self.assertFalse(state['release'].is_set())
            self.assertTrue(any(c[:2]==['xl','shutdown'] for c in self.f.xen.calls))


class RequestOutputTest(unittest.TestCase):
    def test_S3_T12_command_flood_is_drained_with_bounded_request_memory(self):
        with ownership.request_output() as sink:
            result=ownership.run([sys.executable,'-c','import sys;sys.stdout.write("x"*200000);sys.stderr.write("y"*200000)'],check=True)
            self.assertEqual(result.returncode,0)
            self.assertLessEqual(len(sink.content),ownership.LIMIT)
            self.assertGreater(sink.dropped,0)

    def test_S3_T12_truncated_data_is_an_error_instead_of_a_partial_normal_result(self):
        with ownership.request_output(),self.assertRaisesRegex(ValueError,'data output limit'):
            ownership.check_output([sys.executable,'-c','print("x"*200000)'],text=True)

    def test_S3_T12_command_error_retains_failure_without_changing_stdout(self):
        stdout=sys.stdout
        with ownership.request_output() as sink,self.assertRaises(subprocess.CalledProcessError) as error:
            ownership.run([sys.executable,'-c','import sys;print("owned-error",file=sys.stderr);sys.exit(7)'],check=True)
        self.assertEqual(error.exception.returncode,7)
        self.assertIn(b'owned-error',sink.content)
        self.assertIs(sys.stdout,stdout)
