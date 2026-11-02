"""요청별 조회 비용·느린 관측·지속 polling의 실제 Manager/파일/잠금 수락.

Xen/mount/chroot는 기존 외부 대역이다. barrier·thread·프로세스·파이프·FD는 실제로 사용한다.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from test_xenova_spring_followup import FollowupFixture
import test_xenova_preparation_concurrency as stage3
import xenova_execution as execution
import xenova_management as management
import xenova_preparation as output
import xenova_runtime as runtime
import xenova_vm as vm


class ReadProgressTest(FollowupFixture):
    other_vm=stage3.PreparationConcurrencyTest.other_vm
    select=stage3.PreparationConcurrencyTest.select
    blocked=stage3.PreparationConcurrencyTest.blocked

    def evidence(self, name, value):
        root=os.environ.get('XENOVA_STAGE4_EVIDENCE')
        if root:
            with (Path(root)/(name+'.json')).open('x') as stream:json.dump(value,stream,indent=2)

    def pair(self):
        first=self.other_vm()
        spec={**self.f.spec,'vm_id':'650e8400-e29b-41d4-a716-446655440004','name':'read-second','ipv4':'192.168.77.44'}
        made=self.m.call('create',spec,execute=True)
        self.assertTrue(made['ok'],made)
        self.f.xen.records=self.p.records(validate_assets=False)
        started=self.m.call('start',{'vm_id':spec['vm_id']},idempotency_key='second-start')
        self.assertTrue(started['ok'],started)
        return first,spec['vm_id']

    def test_S4_T01_queries_use_one_identity_pair_per_target_and_no_detailed_disk_scan(self):
        first,second=self.pair()
        for action,spec,identities in [('list',{},4),('get',{'vm_id':first},2),
                ('operation',{'idempotency_key':'create:'+first},0),
                ('control-operation',{'idempotency_key':'other-start'},0)]:
            with self.subTest(action=action):
                before=len(self.f.xen.calls);request=self.m.fork()
                with patch.object(request,'_state',wraps=request._state) as builders:
                    result=request.call(action,spec,budget_ms=2000)
                self.assertTrue(result['ok'],result)
                self.assertTrue(all(len(call.args[1])<=1 for call in builders.call_args_list))
                calls=self.f.xen.calls[before:]
                self.assertEqual(sum(c[0]=='xenova-domain-identity' for c in calls),identities)
                self.assertEqual(sum(c==['xl','list','-v'] for c in calls),2 if identities else 0)
                self.assertFalse(any(c in (['xl','list'],['xl','list','-l']) for c in calls))
                self.assertNotIn('lock.hold',request.last_trace['summary'])
                if not identities:self.assertNotIn('platform.registry',request.last_trace['summary'])

    def test_S4_T01_list_keeps_each_targets_verified_clock(self):
        ticks=[10.0];self.p.clock=lambda:ticks[0]
        self.pair();unique=execution.Execution.unique;counts={};expected={}
        def observed(owner,row,*args,**kwargs):
            value=unique(owner,row,*args,**kwargs)
            identity=row['config']['c_info']['uuid']
            counts[identity]=counts.get(identity,0)+1
            if counts[identity]==2:
                ticks[0]+=1
                expected[identity]=int(ticks[0]*1000)
            return value
        with patch.object(execution.Execution,'unique',observed):
            response=self.m.fork().call('list',budget_ms=15000)
        self.assertTrue(response['ok'],response)
        self.assertEqual({v['vm_id']:v['observed_monotonic_ms'] for v in response['data']['vms']},expected)

    def test_S4_T01_absence_keeps_complete_list_clock_despite_other_identity_delay(self):
        ticks=[10.0];self.p.clock=lambda:ticks[0]
        first,second=self.pair()
        stopped=self.m.call('stop',{'vm_id':second},idempotency_key='absence-clock')
        self.assertTrue(stopped['ok'],stopped)
        unique=execution.Execution.unique;seen=[0]
        def observed(owner,row,*args,**kwargs):
            value=unique(owner,row,*args,**kwargs);seen[0]+=1
            if seen[0]==2:ticks[0]+=1
            return value
        with patch.object(execution.Execution,'unique',observed):
            response=self.m.fork().call('list',budget_ms=15000)
        self.assertTrue(response['ok'],response)
        clocks={v['vm_id']:v['observed_monotonic_ms'] for v in response['data']['vms']}
        self.assertEqual(clocks,{first:11000,second:10000})

    def slow_read(self, action, target, callback):
        entered,release=threading.Event(),threading.Event();replies=[];errors=[]
        command=self.p.command
        def run(args,**kwargs):
            if threading.current_thread().name=='slow-query' and args[0]=='xenova-domain-identity' and not entered.is_set():
                entered.set()
                if not release.wait(10):raise RuntimeError('owned read barrier was not released')
            return command(args,**kwargs)
        def query():
            try:replies.append(self.m.fork().call(action,{'vm_id':target} if action=='get' else {},budget_ms=5000))
            except BaseException as error:errors.append(error)
        with patch.object(self.p,'command',run):
            worker=threading.Thread(target=query,name='slow-query');worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with runtime.operation_lock(self.p.runtime):pass
                callback(worker,release)
                self.assertTrue(worker.is_alive())
                self.assertFalse(release.is_set())
            finally:release.set();worker.join(10)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        return replies[0]

    def test_S4_T02_slow_get_and_list_do_not_block_other_stop_or_original_queries(self):
        first,second=self.pair()
        for action in ('get','list'):
            if action=='list':
                started=self.m.call('start',{'vm_id':second},idempotency_key='second-again')
                self.assertTrue(started['ok'],started)
            def stop(worker,release):
                request=self.m.fork()
                asset=request.call('operation',{'idempotency_key':'create:'+second},budget_ms=2000)
                self.assertTrue(asset['ok'],asset)
                key='slow-'+action
                stopped=request.call('stop',{'vm_id':second},idempotency_key=key,budget_ms=2000)
                self.assertTrue(stopped['ok'],stopped)
                original=request.call('control-operation',{'idempotency_key':key},budget_ms=2000)
                self.assertEqual(original['data']['operation']['phase'],'completed',original)
                self.assertEqual(stopped['data']['original_result']['observation']['presence'],'absent')
                self.assertFalse(release.is_set());self.assertTrue(worker.is_alive())
                self.evidence('S4-T02-'+action,{'slow_released':False,'slow_alive':True,
                    'asset_lookup':asset,'stop':stopped,'control_lookup':original,
                    'shutdown_calls':[c for c in self.f.xen.calls if c[:2]==['xl','shutdown']]})
            response=self.slow_read(action,first,stop)
            if action=='get':self.assertTrue(response['ok'],response)
            elif not response['ok']:
                # 목록의 구성원 B가 실제 종료됐다. 혼합/부분 성공 대신 현재 관측 오류는 허용한다.
                self.assertIn(response['error']['code'],('TARGET_CHANGED','OBSERVATION_UNAVAILABLE'))
            self.assertTrue(self.m.fork().call('list',budget_ms=2000)['ok'])

    def polling(self, distribution, count):
        with self.blocked(distribution) as state:
            finish=threading.Event();ready=[threading.Event() for _ in range(count)]
            advanced=threading.Condition();totals={a:0 for a in ('list','get','operation')};failures={};cycles=[0]*count
            errors=[]
            def poll(index):
                request=self.m.fork()
                try:
                    for _ in range(100):
                        if finish.is_set():return
                        for action,spec in [('list',{}),('get',{'vm_id':state['other']}),
                            ('operation',{'idempotency_key':'create:'+self.spec['vm_id']})]:
                            response=request.call(action,spec,budget_ms=2000)
                            with advanced:
                                if response['ok']:totals[action]+=1
                                else:
                                    code=response['error']['code'];failures[code]=failures.get(code,0)+1
                                advanced.notify_all()
                        with advanced:cycles[index]+=1;advanced.notify_all()
                        ready[index].set()
                        if finish.wait(.01):return
                except BaseException as error:errors.append(error);ready[index].set()
            threads=[threading.Thread(target=poll,args=(i,),name='poll-'+str(i)) for i in range(count)]
            for thread in threads:thread.start()
            try:
                for event in ready:self.assertTrue(event.wait(5))
                self.assertEqual(errors,[])
                self.assertTrue(all(totals.values()),(totals,failures))
                key='poll-stop'
                stopped=self.m.fork().call('stop',{'vm_id':state['other']},idempotency_key=key,budget_ms=2000)
                self.evidence('S4-T03-'+distribution+'-'+str(count)+'-attempt',{
                    'stop':stopped,'successes':dict(totals),'errors':dict(failures),
                    'a_released':state['release'].is_set(),'polling_alive':all(t.is_alive() for t in threads)})
                self.assertTrue(stopped['ok'],stopped)
                original=self.m.fork().call('control-operation',{'idempotency_key':key},budget_ms=2000)
                self.assertEqual(original['data']['operation']['phase'],'completed',original)
                with advanced:
                    after_stop=sum(cycles)
                    self.assertTrue(advanced.wait_for(lambda:sum(cycles)>after_stop,timeout=5))
                self.assertFalse(finish.is_set());self.assertTrue(all(t.is_alive() for t in threads))
                self.assertFalse(state['release'].is_set());self.assertTrue(state['thread'].is_alive())
                shutdowns=[c for c in self.f.xen.calls if c[:2]==['xl','shutdown']]
                self.assertEqual(len(shutdowns),1)
                self.assertEqual(state['writes'],1)
                self.evidence('S4-T03-'+distribution+'-'+str(count),{'os':distribution,'pollers':count,
                    'interval_s':.01,'local_profile':True,'successes':dict(totals),'errors':dict(failures),
                    'cycles':list(cycles),'polling_alive':True,'a_released':False,
                    'stop':stopped,'control_lookup':original,'a_operation':state['op'],'shutdown_calls':shutdowns})
            finally:
                finish.set()
                for thread in threads:thread.join(5)
            self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[])
        self.assertEqual(self.last['response']['operation_id'],self.last['op']['operation_id'])

    def test_S4_T03_debian_one_poller(self):self.polling('debian',1)
    def test_S4_T03_debian_two_pollers(self):self.polling('debian',2)
    def test_S4_T03_debian_four_pollers(self):self.polling('debian',4)
    def test_S4_T03_ubuntu_one_poller(self):self.polling('ubuntu',1)
    def test_S4_T03_ubuntu_two_pollers(self):self.polling('ubuntu',2)
    def test_S4_T03_ubuntu_four_pollers(self):self.polling('ubuntu',4)

    def test_S4_T04_execution_replaced_during_read_never_uses_old_binding(self):
        first,second=self.pair()
        old=self.p.execution().run_record(self.p.records(validate_assets=False)[self.f.key])['execution_id']
        def replace(worker,release):
            stopped=self.m.fork().call('stop',{'vm_id':first},idempotency_key='replace-stop')
            self.assertTrue(stopped['ok'],stopped)
            command=self.p.command
            def allocate(args,**kwargs):
                value=command(args,**kwargs)
                if args[:2]==['xl','create']:
                    # FakeXen의 len(rows)+1은 중간 ID 제거 뒤 충돌한다. 실제처럼 비어 있는 ID를 재사용한다.
                    self.f.xen.rows[-1]['domid']=1
                return value
            with patch.object(self.p,'command',allocate):
                started=self.m.fork().call('start',{'vm_id':first},idempotency_key='replace-start')
            self.assertTrue(started['ok'],started)
            self.assertNotEqual(started['data']['vm']['execution_id'],old)
        response=self.slow_read('get',first,replace)
        if response['ok']:self.assertNotEqual(response['data']['vm']['execution_id'],old)
        else:self.assertEqual(response['error']['code'],'TARGET_CHANGED',response)

    def test_S4_T05_unrelated_configuration_change_does_not_restart_get(self):
        first,second=self.pair()
        def change(worker,release):
            changed=self.m.fork().call('autostart-set',{'vm_id':second,'value':'on'})
            self.assertTrue(changed['ok'],changed)
        response=self.slow_read('get',first,change)
        self.assertTrue(response['ok'],response)

    def test_S4_T04_changed_target_gets_one_new_round_and_continuous_changes_fail_bounded(self):
        identity=self.other_vm();validate=management.ReadContext.validate
        for continuous in (False,True):
            rounds=[]
            def change(read):
                rounds.append(1)
                if continuous or len(rounds)==1:
                    value=not read.states[0]['record']['autostart']
                    changed=self.m.fork().call('autostart-set',{'vm_id':identity,'value':'on' if value else 'off'})
                    self.assertTrue(changed['ok'],changed)
                return validate(read)
            with patch.object(management.ReadContext,'validate',change):
                response=self.m.fork().call('get',{'vm_id':identity},budget_ms=2000)
            self.assertEqual(len(rounds),2)
            if continuous:self.error(response,'TARGET_CHANGED')
            else:self.assertTrue(response['ok'],response)
        self.assertTrue(self.m.fork().call('get',{'vm_id':identity})['ok'])

    def test_S4_T05_new_registration_during_list_is_not_silently_omitted(self):
        first,second=self.pair()
        third='650e8400-e29b-41d4-a716-446655440005'
        def register(worker,release):
            made=self.m.fork().call('create',{**self.f.spec,'vm_id':third,'name':'late-list-member','ipv4':'192.168.77.45'},execute=True)
            self.assertTrue(made['ok'],made)
        response=self.slow_read('list',first,register)
        self.assertTrue(response['ok'],response)
        self.assertEqual({v['vm_id'] for v in response['data']['vms']},{first,second,third})

    def test_S4_T06_incomplete_registry_enumeration_is_not_an_empty_success(self):
        self.other_vm();iterate=Path.iterdir
        def partial(path):
            yield from iterate(path)
            if path==self.f.data/'instances':raise OSError('injected partial registry enumeration')
        with patch.object(Path,'iterdir',partial):
            response=self.m.fork().call('list',budget_ms=2000)
        self.error(response,'STORAGE_UNAVAILABLE')
        self.assertIsNone(response['data'])

    def test_S4_T06_missing_vm_after_partial_legacy_enumeration_is_not_not_found(self):
        self.other_vm();iterate=Path.iterdir;reads=[0]
        def partial(path):
            if path==self.f.data/'instances':
                reads[0]+=1
                if reads[0]==2:
                    yield from iterate(path)
                    raise OSError('injected legacy archive enumeration failure')
            yield from iterate(path)
        with patch.object(Path,'iterdir',partial):
            response=self.m.fork().call('get',{'vm_id':'650e8400-e29b-41d4-a716-446655440099'},budget_ms=2000)
        self.assertFalse(response['ok'],response)
        self.assertEqual(reads[0],2)
        self.assertNotEqual(response['error']['code'],'VM_NOT_FOUND')
        self.assertIsNone(response['data'])

    def test_S4_T06_late_duplicate_asset_key_is_not_reported_as_unique(self):
        identity=self.other_vm();read=self.m._operations;calls=[0]
        def scan(*args,**kwargs):
            rows=read(*args,**kwargs);calls[0]+=1
            if calls[0]==1:
                op=rows[0];copy={**op,'operation_id':'650e8400-e29b-41d4-a716-446655440009',
                    'owned_workspace':'instances/.operation-650e8400-e29b-41d4-a716-446655440009'}
                (self.f.data/'operations'/(copy['operation_id']+'.json')).write_text(json.dumps(copy))
            return rows
        with patch.object(self.m,'_operations',scan):
            response=self.m.call('operation',{'idempotency_key':'create:'+identity},budget_ms=2000)
        self.error(response,'RECONCILE_REQUIRED')

    def test_S4_T08_owned_helper_timeout_after_pipe_eof_reaps_child(self):
        identity=self.other_vm();command=self.p.command
        pidfile=self.f.data.parent/'read-child.pid'
        script='import os,time;from pathlib import Path;Path('+repr(str(pidfile))+').write_text(str(os.getpid()));os.close(1);os.close(2);time.sleep(60)'
        def run(args,**kwargs):
            if args[0]=='xenova-domain-identity':return vm.run([sys.executable,'-B','-c',script],timeout=kwargs['timeout'])
            return command(args,**kwargs)
        request=self.m.fork();request.p.command=run;request.p.clock=time.monotonic
        before=len(list(Path('/proc/self/fd').iterdir()))
        response=request.call('get',{'vm_id':identity},budget_ms=100)
        self.assertFalse(response['ok'],response)
        self.assertEqual(response['error']['code'],'DEADLINE_EXCEEDED',response)
        self.assertTrue(pidfile.exists())
        self.assertFalse((Path('/proc')/pidfile.read_text()).exists())
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)
        self.assertTrue(self.m.fork().call('get',{'vm_id':identity})['ok'])

    def test_S4_T08_empty_search_expiry_is_not_absence(self):
        for action,method,spec in [('operation','_operations',{'idempotency_key':'missing'}),
                                  ('get','_state',{'vm_id':'650e8400-e29b-41d4-a716-446655440099'})]:
            request=self.m.fork();ticks=[10.0];request.p.clock=lambda:ticks[0]
            original=getattr(request,method)
            def slow(*args,**kwargs):
                try:return original(*args,**kwargs)
                finally:ticks[0]+=.1
            with patch.object(request,method,slow):
                response=request.call(action,spec,budget_ms=10)
            self.error(response,'DEADLINE_EXCEEDED')
            self.assertIsNone(response['data'])

    def test_S4_T08_owned_helper_is_reaped_after_selector_failure(self):
        identity=self.other_vm();command=self.p.command
        pidfile=self.f.data.parent/'read-select-child.pid'
        script='import os,time;from pathlib import Path;Path('+repr(str(pidfile))+').write_text(str(os.getpid()));print("ready",flush=True);time.sleep(60)'
        def run(args,**kwargs):
            if args[0]=='xenova-domain-identity':return vm.run([sys.executable,'-B','-c',script],timeout=kwargs['timeout'])
            return command(args,**kwargs)
        request=self.m.fork();request.p.command=run
        select=output.selectors.DefaultSelector.select
        def fail(selector,*args,**kwargs):
            ready=select(selector,*args,**kwargs)
            if ready:raise OSError('injected owned selector I/O failure')
            return ready
        try:
            with patch.object(output.selectors.DefaultSelector,'select',fail):
                response=request.call('get',{'vm_id':identity},budget_ms=2000)
            self.error(response,'OBSERVATION_UNAVAILABLE')
            self.assertTrue(pidfile.exists())
            self.assertFalse((Path('/proc')/pidfile.read_text()).exists(),'owned read helper leaked after I/O failure')
        finally:
            # 이 반례 시험이 만든 자식만 회수한다. 원자료를 남기며 제품 상태를 수습하지 않는다.
            if pidfile.exists() and (Path('/proc')/pidfile.read_text()).exists():
                pid=int(pidfile.read_text());os.kill(pid,9);os.waitpid(pid,0)

    def test_S4_T09_display_read_does_not_replace_control_submission_guard(self):
        identity=self.other_vm()
        self.assertTrue(self.m.call('get',{'vm_id':identity})['ok'])
        before=len(self.f.xen.calls)
        stopped=self.m.fork().call('stop',{'vm_id':identity},idempotency_key='fresh-guard')
        self.assertTrue(stopped['ok'],stopped)
        calls=self.f.xen.calls[before:]
        self.assertTrue(any(c==['xl','list','-l'] for c in calls))
        self.assertTrue(any(c[:2]==['xl','shutdown'] for c in calls))

    def test_S4_T10_unknown_app_readiness_and_configured_ip_remain_distinct(self):
        identity=self.other_vm();response=self.m.call('get',{'vm_id':identity})
        self.assertTrue(response['ok'],response)
        view=response['data']['vm']
        self.assertEqual(view['app_readiness'],'unknown')
        self.assertEqual(view['ipv4'],view['configuration']['ipv4'])
        self.assertNotIn('agent_status',view)

    def test_S4_T12_read_capacity_does_not_reserve_control_or_operation_and_recovers(self):
        identity=self.other_vm()
        with ExitStack() as stack:
            for _ in range(runtime.OBSERVATION_SLOTS):
                stack.enter_context(runtime.observation_slot(self.p.runtime,deadline=self.p.clock()+10,clock=self.p.clock))
            self.error(self.m.fork().call('list',budget_ms=2000),'OPERATION_IN_PROGRESS')
            operation=self.m.fork().call('operation',{'idempotency_key':'create:'+identity},budget_ms=2000)
            self.assertTrue(operation['ok'],operation)
            stopped=self.m.fork().call('stop',{'vm_id':identity},idempotency_key='capacity-stop',budget_ms=2000)
            self.assertTrue(stopped['ok'],stopped)
        self.assertTrue(self.m.fork().call('list',budget_ms=2000)['ok'])

    def test_S4_T12_separate_process_respects_held_read_capacity(self):
        identity=self.other_vm()
        code='''
import json,sys
from pathlib import Path
from unittest.mock import patch
import xenova_vm as vm
import xenova_management as management
from test_xenova_platform import FakeXen
p=vm.Platform(Path(sys.argv[1]),Path(sys.argv[2]),command=FakeXen(Path(sys.argv[1]),{}))
with patch.object(vm,'storage_check'):
 print(json.dumps(management.Manager(p).call('list',budget_ms=2000)))
'''
        with ExitStack() as stack:
            for _ in range(runtime.OBSERVATION_SLOTS):
                stack.enter_context(runtime.observation_slot(self.p.runtime,deadline=self.p.clock()+10,clock=self.p.clock))
            child=subprocess.run([sys.executable,'-B','-c',code,str(self.f.data),str(self.f.run)],text=True,capture_output=True,timeout=5)
        self.assertEqual(child.returncode,0,child.stderr)
        self.error(json.loads(child.stdout),'OPERATION_IN_PROGRESS')

    def test_S4_T12_owner_exit_keeps_helper_slot_until_actual_exit_then_recovers(self):
        identity=self.other_vm()
        ready_read,ready_write=os.pipe();release_read,release_write=os.pipe()
        worker_pid=watch=None
        before=len(list(Path('/proc/self/fd').iterdir()))
        try:
            with ExitStack() as held:
                for _ in range(runtime.OBSERVATION_SLOTS-1):
                    held.enter_context(runtime.observation_slot(self.p.runtime,deadline=self.p.clock()+10,clock=self.p.clock))
                worker_pid=os.fork()
                if worker_pid==0:
                    command=self.p.command
                    def run(args,**kwargs):
                        if args[0]=='xenova-domain-identity':
                            script='import os,sys;os.write(int(sys.argv[1]),str(os.getpid()).encode()+b"\\n");os.read(int(sys.argv[2]),1)'
                            output.run([sys.executable,'-B','-c',script,str(ready_write),str(release_read)],
                                       timeout=kwargs['timeout'],pass_fds=(ready_write,release_read),check=True)
                        return command(args,**kwargs)
                    try:
                        request=self.m.fork();request.p.command=run
                        request.call('get',{'vm_id':identity},budget_ms=10000)
                    finally:os._exit(3)
                self.assertTrue(select.select([ready_read],[],[],5)[0],'real helper never entered')
                child_pid=int(os.read(ready_read,64).strip());watch=os.pidfd_open(child_pid)
                slots=[str(path) for path in (Path('/proc')/str(child_pid)/'fd').iterdir()
                       if 'observation-slot-' in os.readlink(path)]
                self.assertEqual(len(slots),1,'helper must inherit its own read slot only')
                slot=Path(os.readlink(slots[0]));inode=slot.stat().st_ino
                os.kill(worker_pid,signal.SIGKILL)
                _,status=os.waitpid(worker_pid,0);worker_pid=None
                self.assertTrue(os.WIFSIGNALED(status))
                self.assertFalse(select.select([watch],[],[],0)[0],'helper should still be blocked')
                blocked=self.m.fork().call('list',budget_ms=2000)
                self.error(blocked,'OPERATION_IN_PROGRESS')
                original=self.m.fork().call('operation',{'idempotency_key':'create:'+identity},budget_ms=2000)
                self.assertTrue(original['ok'],original)
                os.write(release_write,b'1')
                self.assertTrue(select.select([watch],[],[],5)[0],'owned helper did not exit')
                # 다른 세 슬롯을 계속 보유해 방금 상속한 슬롯의 정상 반환만 확인한다.
                with runtime.observation_slot(self.p.runtime,deadline=self.p.clock()+10,clock=self.p.clock) as fd:
                    self.assertEqual(os.fstat(fd).st_ino,inode)
                recovered=self.m.fork().call('get',{'vm_id':identity},budget_ms=2000)
                self.assertTrue(recovered['ok'],recovered)
                self.evidence('S4-T12-owner-exit',{'parent_signaled':True,'helper_alive_after_parent_exit':True,
                    'inherited_slot_count':len(slots),'capacity_error':blocked['error']['code'],
                    'original_lookup_ok':original['ok'],'helper_exit_confirmed':True,
                    'same_slot_inode_reacquired':True,'normal_get_ok':recovered['ok']})
        finally:
            os.write(release_write,b'1')
            if worker_pid is not None:
                try:os.kill(worker_pid,signal.SIGKILL)
                except ProcessLookupError:pass
                os.waitpid(worker_pid,0)
            if watch is not None:
                if not select.select([watch],[],[],5)[0]:signal.pidfd_send_signal(watch,signal.SIGKILL)
                self.assertTrue(select.select([watch],[],[],5)[0]);os.close(watch)
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)
            for fd in (ready_read,ready_write,release_read,release_write):os.close(fd)

    def test_S4_T12_slot_files_reject_unsafe_paths_modes_and_owners_without_repair(self):
        directory=runtime.ensure_runtime(self.p.runtime)/'locks';directory.mkdir(mode=0o700,exist_ok=True)
        path=directory/'observation-slot-0.lock'
        external=self.f.data.parent/'slot-external';external.write_bytes(b'preserve')
        initial=len(list(Path('/proc/self/fd').iterdir()));observed=[]
        for kind in ('symlink','hardlink','directory','fifo','permissive-mode','foreign-owner'):
            with self.subTest(kind=kind):
                if kind=='symlink':path.symlink_to(external)
                elif kind=='hardlink':os.link(external,path)
                elif kind=='directory':path.mkdir()
                elif kind=='fifo':os.mkfifo(path,0o600)
                else:path.write_bytes(b'preserve');path.chmod(0o666 if kind=='permissive-mode' else 0o600)
                info=path.lstat();fstat=os.fstat
                def foreign(fd):
                    value=fstat(fd)
                    if (value.st_dev,value.st_ino)==(info.st_dev,info.st_ino):
                        parts=list(value);parts[4]=os.geteuid()+1
                        return os.stat_result(parts)
                    return value
                try:
                    with patch.object(runtime.os,'fstat',foreign if kind=='foreign-owner' else fstat):
                        with self.assertRaises((runtime.PlatformError,OSError)):
                            with runtime.observation_slot(self.p.runtime,deadline=self.p.clock()+10,clock=self.p.clock):
                                self.fail('unsafe slot was accepted: '+kind)
                    after=path.lstat()
                    self.assertEqual((after.st_ino,after.st_mode,after.st_nlink),(info.st_ino,info.st_mode,info.st_nlink))
                    self.assertEqual(external.read_bytes(),b'preserve')
                    self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),initial)
                    observed.append(kind)
                finally:
                    # 이 시험이 만든 임시 오브젝트만 정리한다. 제품 잠금은 삭제하지 않는다.
                    if kind=='directory':path.rmdir()
                    else:path.unlink()
        with runtime.observation_slot(self.p.runtime,deadline=self.p.clock()+10,clock=self.p.clock) as fd:
            info=os.fstat(fd)
            self.assertEqual(stat.S_IMODE(info.st_mode),0o600)
            self.assertEqual(info.st_uid,os.geteuid());self.assertEqual(info.st_nlink,1)
        for kind in ('linked-runtime','linked-lock-directory'):
            alternate=self.f.data.parent/kind
            if kind=='linked-runtime':alternate.symlink_to(self.p.runtime,target_is_directory=True)
            else:
                alternate.mkdir(mode=0o700)
                (alternate/'locks').symlink_to(directory,target_is_directory=True)
            with self.assertRaises(runtime.PlatformError):
                with runtime.observation_slot(alternate,deadline=self.p.clock()+10,clock=self.p.clock):
                    self.fail('linked slot directory was accepted')
            observed.append(kind)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),initial)
        self.evidence('S4-T12-slot-files',{'rejected':observed,'normal_mode':'0600',
            'real_owner_and_single_link':True,'unsafe_objects_preserved':True,'fd_leak':False,
            'foreign_owner_boundary':'fstat UID substituted; other filesystem checks real'})

    def test_S4_T11_read_descriptors_are_not_preparation_ownership(self):
        with output.request_output() as sink:
            sink.fds=(0,1)
            self.assertFalse(output.managed_preparation())

    def test_S4_T11_same_manager_fork_does_not_inherit_a_query_context(self):
        identity=self.other_vm();request=self.m.fork()
        first=request.call('get',{'vm_id':identity},request_id='read-first',budget_ms=2000)
        self.assertTrue(first['ok'],first)
        fork=request.fork()
        self.assertFalse(hasattr(fork.p,'response_deadline'))
        changed=self.m.fork().call('autostart-set',{'vm_id':identity,'value':'on'})
        self.assertTrue(changed['ok'],changed)
        second=fork.call('get',{'vm_id':identity},request_id='read-second',budget_ms=2000)
        self.assertTrue(second['ok'],second)
        self.assertTrue(second['data']['vm']['configuration']['autostart'])
        self.assertEqual(request.last_trace['context']['request_id'],'read-first')
        self.assertEqual(fork.last_trace['context']['request_id'],'read-second')

    def test_S4_T11_delayed_success_keeps_its_clock_and_does_not_replace_newer_snapshot(self):
        self.p.clock=time.monotonic;identity=self.other_vm()
        entered,release=threading.Event(),threading.Event();replies=[]
        validate=management.ReadContext.validate
        def delayed(read):
            if threading.current_thread().name=='older-success':
                entered.set()
                if not release.wait(5):raise RuntimeError('owned publication barrier timed out')
            return validate(read)
        with patch.object(management.ReadContext,'validate',delayed):
            worker=threading.Thread(target=lambda:replies.append(self.m.fork().call('get',{'vm_id':identity},budget_ms=2000)),name='older-success')
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                newer=self.m.fork().call('get',{'vm_id':identity},budget_ms=2000)
                self.assertTrue(newer['ok'],newer)
                path=self.p.runtime/'observations'/(identity+'.json');newer_cache=path.read_bytes()
            finally:release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertTrue(replies[0]['ok'],replies)
        self.assertLess(replies[0]['data']['vm']['observed_monotonic_ms'],newer['data']['vm']['observed_monotonic_ms'])
        self.assertEqual(path.read_bytes(),newer_cache)

    def test_S4_T11_delayed_failure_does_not_invalidate_newer_success(self):
        self.p.clock=time.monotonic;identity=self.other_vm()
        entered,release=threading.Event(),threading.Event();replies=[];command=self.p.command
        def run(args,**kwargs):
            if threading.current_thread().name=='older-error' and args[0]=='xenova-domain-identity':
                entered.set()
                if not release.wait(5):raise RuntimeError('owned error barrier timed out')
                raise runtime.PlatformError('injected delayed read failure')
            return command(args,**kwargs)
        with patch.object(self.p,'command',run):
            worker=threading.Thread(target=lambda:replies.append(self.m.fork().call('get',{'vm_id':identity},budget_ms=2000)),name='older-error')
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                newer=self.m.fork().call('get',{'vm_id':identity},budget_ms=2000)
                self.assertTrue(newer['ok'],newer)
                path=self.p.runtime/'observations'/(identity+'.json');newer_cache=path.read_bytes()
            finally:release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.error(replies[0],'OBSERVATION_UNAVAILABLE')
        self.assertEqual(path.read_bytes(),newer_cache)

    def test_S4_T13_malformed_or_truncated_identity_never_falls_back_to_cached_success(self):
        identity=self.other_vm();command=self.p.command
        for bad in ('not-json','{"domid":1,"state_bits":1,"xen_unique_id":"0"}'):
            def run(args,**kwargs):
                return bad if args[0]=='xenova-domain-identity' else command(args,**kwargs)
            with patch.object(self.p,'command',run):
                response=self.m.fork().call('get',{'vm_id':identity})
            self.error(response,'OBSERVATION_UNAVAILABLE')
            self.assertIsNone(response['data'])
        def flood(args,**kwargs):
            if args[0]=='xenova-domain-identity':
                return vm.run([sys.executable,'-B','-c','print("x"*200000)'],timeout=kwargs['timeout'])
            return command(args,**kwargs)
        with patch.object(self.p,'command',flood):
            self.error(self.m.fork().call('get',{'vm_id':identity}),'OBSERVATION_UNAVAILABLE')
        self.assertTrue(self.m.fork().call('get',{'vm_id':identity})['ok'])


if __name__=='__main__':unittest.main()
