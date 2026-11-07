"""Verified profile acceptance: evidence, durable authority and physical submission."""
import json
import os
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from xenova_mqtt import topics,Identity
from xenova_mqtt.envelope import build
from xenova_mqtt.client import Message
from xenova_mqtt.drive_binding import BindingCache,atomic_json
from xenova_gateway.authority import AuthorityStore,AuthorityError,GatewayLease
from xenova_gateway.machine import Mode,ForwardDrive,PublishControl,PublishEvent,ForwardStop
from xenova_gateway.verified import VerifiedDriveMachine
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.sink import MockSink,SinkError

A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
HOST='11111111-1111-4111-8111-111111111111'
BOOT='22222222-2222-4222-8222-222222222222'
RUN='33333333-3333-4333-8333-333333333333'

class World:
    def __init__(self,path,first=A,mode='AUTO'):
        self.t=100.;self.alive={A,B};self.perception=True;self.state_ready=True
        self.execs={A:str(uuid4()),B:str(uuid4())};self.instances={A:RUN,B:RUN};self.progress={A:0,B:0}
        self.auth=AuthorityStore(path,HOST,uid=os.geteuid(),mountpoint=None)
        self.auth.initialize(first);self.auth.update(mode=mode,generation=42,phase='DISARMED')
        self.cache=BindingCache(HOST,clock=lambda:self.t);self.bind()
        self.m=VerifiedDriveMachine(vm_ids=(A,B),authority=self.auth,bindings=self.cache,clock=lambda:self.t)
        self.actions=self.m.start(self.t);self.m.note_transport(True,self.t)
    def bind(self):
        self.cache.install({'schema_version':'1.0-drive-bindings','host_boot_id':HOST,
            'observed_monotonic_ms':int(self.t*1000),'expires_monotonic_ms':int(self.t*1000)+3000,
            'vms':{vm:{'status':'observed','execution_id':self.execs[vm],'boot_id':BOOT,
                'app_instance_id':self.instances[vm],'domid':i+1,'xen_unique_id':str(10+i),
                'intent_revision':1,'lifecycle_generation':1,'observed_monotonic_ms':int(self.t*1000)}
                for i,vm in enumerate((A,B)) if vm in self.alive}})
    def tick(self,seconds=.02,*,apply=True,delivery=True):
        for _ in range(round(seconds/.02)):
            self.t+=.02;self.bind();self.m.note_delivery(self.m.generation,'acknowledged' if delivery else 'failed',self.t)
            for vm in self.alive:
                assert self.m.accept_session(vm,BOOT,self.instances[vm],self.t)
                self.progress[vm]+=1
                for app in ('manual','autonomous'):
                    reason='STATE_PREPARING' if not self.state_ready else 'INPUT_STALE' if app=='autonomous' and not self.perception else None
                    if apply:self.m.on_readiness(app,vm,{'applied_generation':self.m.generation,
                        'progress_seq':str(self.progress[vm]),'readiness':'READY' if reason is None else 'NOT_READY','reason':reason},self.t)
                    self.m.on_command(app,vm,{'generation':self.m.generation if apply else self.m.generation-1,
                        'v_mps':.1 if vm==A else .2,'steer_rad':0},self.t,{'message_id':str(uuid4())})
            self.actions=self.m.step(self.t)
    def resume(self):
        return self.m.on_resume(self.t,'test',expected_generation=self.m.generation,message_id=str(uuid4()))
    def arm(self):
        self.tick(3.2);assert self.resume();self.tick(6.4);assert self.m.output_permitted

@pytest.mark.parametrize('first,other',[(A,B),(B,A)])
def test_symmetric_continuous_scenario_and_rejoin(tmp_path,first,other):
    w=World(tmp_path/'authority',first);w.arm();assert w.m.mode is Mode.AUTO
    old_exec=dict(w.execs);w.perception=False;w.tick(.1)
    assert w.m.mode is Mode.MANUAL and w.m.active_vm==first and w.execs==old_exec
    assert not w.m.output_permitted
    w.tick(3.2);assert w.resume();w.tick(6.4)
    w.alive.remove(first);w.tick(.2)
    assert w.m.mode is Mode.MANUAL and w.m.active_vm==other
    w.perception=True;w.tick(3.4)
    assert w.m.mode is Mode.AUTO_READY and w.m.active_vm==other
    w.tick(3.2);assert w.resume();w.tick(3.2)
    assert w.m.mode is Mode.AUTO and w.m.output_permitted
    w.alive.add(first);w.execs[first]=str(uuid4());w.instances[first]=str(uuid4());w.progress[first]=0
    w.tick(.1);assert w.m.node_status(first,w.t)=='unready'
    w.tick(3.2);assert w.m.node_status(first,w.t)=='standby' and w.m.active_vm==other
    assert not w.m.accept_session(first,BOOT,RUN,w.t)
    w.alive.remove(other);w.tick(.2)
    assert w.m.active_vm==first and w.m.mode is Mode.AUTO
    assert any(isinstance(a,ForwardDrive) for a in w.actions)

@pytest.mark.parametrize('delivery',[True,False])
def test_old_generation_liveness_does_not_extend_application_deadline(tmp_path,delivery):
    w=World(tmp_path/'authority');w.arm();w.perception=False;w.tick(.02)
    generation=w.m.generation;deadline=w.auth.value['pending']['deadline_ms']
    w.tick(2,apply=False,delivery=delivery)
    assert w.m.phase=='FAILED' and w.m.generation==generation and not w.m.output_permitted
    assert w.auth.value['pending']['deadline_ms']==deadline
    assert w.m.failure==('CONTROL_APPLY_UNCONFIRMED' if delivery else 'CONTROL_PUBLISH_UNCONFIRMED')

def test_safe_stop_can_select_other_vm_without_output_to_get_ready(tmp_path):
    w=World(tmp_path/'authority',mode='SAFE_STOP');w.alive={B};w.tick(3.4)
    assert w.m.active_vm==B and w.m.mode is Mode.MANUAL and not w.m.output_permitted
    w.perception=False;w.tick(3.2);assert w.resume();w.tick(3.2)
    assert w.m.active_vm==B and w.m.mode is Mode.MANUAL and w.m.output_permitted

def test_restart_preserves_selection_but_never_output_or_old_request(tmp_path):
    w=World(tmp_path/'authority',B);w.arm();generation=w.m.generation
    request=str(uuid4());assert w.auth.consume_request(request,generation)
    w.auth=AuthorityStore(w.auth.directory,HOST,uid=os.geteuid(),mountpoint=None);w.auth.load()
    w.m=VerifiedDriveMachine(vm_ids=(A,B),authority=w.auth,bindings=w.cache,clock=lambda:w.t)
    w.m.start(w.t);w.m.note_transport(True,w.t);w.tick(4)
    assert w.m.generation==generation and w.m.active_vm==B and not w.m.output_permitted
    assert not w.m.on_resume(w.t,expected_generation=generation,message_id=request)
    assert not w.m.on_resume(w.t,expected_generation=generation-1,message_id=str(uuid4()))

def test_progress_duplicates_state_sync_and_expiry_are_not_ready(tmp_path):
    w=World(tmp_path/'authority');w.state_ready=False;w.tick(4)
    assert w.m.node_status(B,w.t)=='unready' and not w.resume()
    w.state_ready=True;w.tick(3.2);assert w.m.node_status(B,w.t)=='standby'
    p=w.m.prepared[B,'autonomous'];original=p.observed
    payload={'applied_generation':w.m.generation,'progress_seq':str(p.progress),'readiness':'READY','reason':None}
    assert not w.m.on_readiness('autonomous',B,payload,w.t+.29) and p.observed==original
    assert not w.m._ready(B,'autonomous',w.t+.31)
    assert not w.m.on_readiness('autonomous',B,{**payload,'applied_generation':w.m.generation-1,'progress_seq':str(p.progress+1)},w.t)

def test_authority_loss_conflict_exhaustion_and_single_owner(tmp_path,monkeypatch):
    w=World(tmp_path/'authority');w.arm()
    with pytest.raises(AuthorityError):w.auth.reconcile({**w.auth.payload(),'generation':w.m.generation+1})
    w.auth.update(generation=0xffffffff,pending=None,phase='DISARMED')
    with pytest.raises(AuthorityError):w.auth.issue(Mode.AUTO,A,'test',w.t,1,arm=True)
    atomic_json(w.auth.path,{'broken':True});assert not w.auth.verify()
    w.tick(.02);assert not w.m.output_permitted
    with pytest.raises(AuthorityError):AuthorityStore(w.auth.directory,HOST,uid=os.geteuid(),mountpoint=None).load()
    first=GatewayLease(tmp_path/'owner.lock').acquire()
    try:
        with pytest.raises(AuthorityError):GatewayLease(tmp_path/'owner.lock').acquire()
    finally:first.close()

def test_authority_save_failure_does_not_issue_or_publish(tmp_path,monkeypatch):
    w=World(tmp_path/'authority');old=w.m.generation
    def fail(*_):raise OSError('fsync failed')
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',fail)
    actions=w.m._change(Mode.MANUAL,B,'test',w.t)
    assert w.m.generation==old and not any(isinstance(a,PublishControl) for a in actions)
    assert not w.m.output_permitted and w.auth.error

def test_uninitialized_and_interrupted_initialization_cannot_guess_generation(tmp_path,monkeypatch):
    store=AuthorityStore(tmp_path/'authority',HOST,uid=os.geteuid(),mountpoint=None)
    with pytest.raises(AuthorityError,match='initialization'):store.load()
    def fail(*_):raise OSError('disk')
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',fail)
    with pytest.raises(AuthorityError):store.initialize(A)
    with pytest.raises(AuthorityError,match='already'):store.initialize(A)
    with pytest.raises(AuthorityError,match='missing/corrupt'):store.load()

def test_uncertain_parent_fsync_never_rolls_back_committed_generation(tmp_path,monkeypatch):
    w=World(tmp_path/'authority');generation=w.m.generation
    from xenova_mqtt.drive_binding import atomic_json as real_write
    def uncertain(path,value):
        real_write(path,value)
        raise OSError('parent fsync outcome uncertain after replace')
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',uncertain)
    w.m._change(Mode.MANUAL,B,'test',w.t)
    assert json.loads(w.auth.path.read_text())['generation']==generation+1
    assert w.m.generation==generation and not w.m.output_permitted and w.auth.error
    w.m._fail('another failure',w.t)
    assert json.loads(w.auth.path.read_text())['generation']==generation+1

def test_mountpoint_alone_cannot_create_authority_on_unverified_storage(tmp_path,monkeypatch):
    import sys
    def refused(_):raise RuntimeError('not Xenova data')
    monkeypatch.setitem(sys.modules,'xenova_vm',SimpleNamespace(storage_check=refused))
    monkeypatch.setattr('os.path.ismount',lambda _:True)
    with pytest.raises(AuthorityError,match='ext4'):
        AuthorityStore(tmp_path/'control',HOST,uid=os.geteuid(),mountpoint=tmp_path)
    assert not (tmp_path/'control').exists()

def test_current_execution_replaces_more_than_64_sessions(tmp_path):
    w=World(tmp_path/'authority')
    for _ in range(80):
        previous=w.instances[B];w.execs[B]=str(uuid4());w.instances[B]=str(uuid4());w.bind()
        assert w.m.accept_session(B,BOOT,w.instances[B],w.t)
        assert not w.m.accept_session(B,BOOT,previous,w.t)

def test_auto_ready_waits_for_both_app_applications_without_generation_bounce(tmp_path):
    w=World(tmp_path/'authority');w.arm()
    w.m._change(Mode.AUTO_READY,A,'test',w.t,arm=True);generation=w.m.generation
    w.m.note_delivery(generation,'acknowledged',w.t)
    w.m.on_readiness('manual',A,{'applied_generation':generation,'progress_seq':'9999','readiness':'READY','reason':None},w.t)
    w.m.step(w.t);assert w.m.phase=='PENDING' and not w.m.output_permitted
    w.m.on_readiness('autonomous',A,{'applied_generation':generation,'progress_seq':'9999','readiness':'READY','reason':None},w.t)
    w.m.step(w.t);assert w.m.phase=='APPLIED' and w.m.generation==generation

def test_legacy_auto_deadline_selects_prepared_mirror_without_vm_distrust(tmp_path):
    w=World(tmp_path/'authority');w.arm();old=w.m.generation
    payload={'trust':'UNTRUSTED','reason':'AUTO_DEADLINE_MISS','consecutive_misses':5,'command_id':'delay-1'}
    actions=w.m.on_vm_untrusted(A,'autonomous',payload,w.t)
    assert w.m.active_vm==B and w.m.generation==old+1 and not w.m.output_permitted
    assert w.auth.value['owner']==B and w.auth.value['pending']['arm_after_apply']
    assert not w.auth.value.get('distrusted') and not w.m.untrusted
    assert any(isinstance(action,PublishControl) for action in actions)
    # The normal MANUAL session remains valid; control ownership still excludes
    # A's AUTO output while B applies the new generation.
    assert w.m._bound(A,w.t,'manual')
    assert w.m.on_vm_untrusted(A,'autonomous',payload,w.t)==[]
    w.tick(.1);assert w.m.output_permitted and w.m.active_vm==B
    assert any(isinstance(action,ForwardDrive) for action in w.actions)

def test_legacy_auto_deadline_without_prepared_mirror_enters_safe_stop(tmp_path):
    w=World(tmp_path/'authority');w.arm();w.m.prepared[B,'autonomous'].observed=None
    actions=w.m.on_vm_untrusted(A,'autonomous',
        {'trust':'UNTRUSTED','reason':'AUTO_DEADLINE_MISS','consecutive_misses':5,'command_id':'delay-2'},w.t)
    assert w.m.mode is Mode.SAFE_STOP and w.m.active_vm==A and not w.m.output_permitted
    assert not w.m.untrusted and not w.auth.value.get('distrusted')
    assert any(isinstance(action,ForwardStop) for action in actions)

def test_legacy_heap_occupancy_is_warning_and_rejects_bad_evidence(tmp_path):
    w=World(tmp_path/'authority');w.arm();old=w.m.generation
    payload={'trust':'UNTRUSTED','reason':'MEMORY_PRESSURE','metric':'memory.kernel_heap.used_bytes',
        'used_percent':89,'threshold_percent':85,'consecutive_samples':5,'command_id':'memory-1'}
    actions=w.m.on_vm_untrusted(A,'autonomous',payload,w.t)
    assert w.m.active_vm==A and w.m.generation==old and w.m.output_permitted
    assert not w.m.untrusted and not w.auth.value.get('distrusted')
    assert len(actions)==1 and actions[0].event=='resource_warning'
    assert actions[0].fields['reason']=='MEMORY_PRESSURE'
    invalid={**payload,'command_id':'memory-invalid','used_percent':80}
    assert w.m.on_vm_untrusted(B,'autonomous',invalid,w.t)==[]

def test_legacy_heap_warning_preserves_normal_manual_owner(tmp_path):
    w=World(tmp_path/'authority',mode='MANUAL');w.perception=False;w.arm();old=w.m.generation
    payload={'trust':'UNTRUSTED','reason':'MEMORY_PRESSURE','metric':'memory.kernel_heap.used_bytes',
        'used_percent':90,'threshold_percent':85,'consecutive_samples':5,'command_id':'memory-manual'}
    actions=w.m.on_vm_untrusted(A,'autonomous',payload,w.t)
    assert w.m.active_vm==A and w.m.mode is Mode.MANUAL and w.m.generation==old
    assert w.m.output_permitted and not w.m.untrusted and not w.auth.value.get('distrusted')
    assert len(actions)==1 and actions[0].event=='resource_warning'


def test_legacy_resource_diagnostic_copies_only_bounded_contract_fields(tmp_path):
    w=World(tmp_path/'authority');w.arm()
    payload={'trust':'UNTRUSTED','reason':'MEMORY_PRESSURE','metric':'memory.kernel_heap.used_bytes',
        'used_percent':90,'threshold_percent':85,'consecutive_samples':5,'command_id':'memory-extra',
        'raw_body':'fixture-do-not-copy','credential':'fixture-do-not-copy','vm_id':B}
    actions=w.m.on_vm_untrusted(A,'autonomous',payload,w.t)
    fields=actions[0].fields
    assert fields['vm_id']==A and fields['execution_id']==w.execs[A]
    assert fields['reason']=='MEMORY_PRESSURE' and fields['command_id']=='memory-extra'
    assert 'raw_body' not in fields and 'credential' not in fields

class Client:
    connected=True;connections=1
    def __init__(self):self.handlers={};self.published=[];self.status='acknowledged'
    def subscribe(self,f,h=None,**_):self.handlers[f]=h
    def subscribed(self,_):return True
    def latest(self,_):return None
    def publish(self,*args,**kwargs):self.published.append((args,kwargs))
    def publish_tracked(self,topic,kind,payload):
        ticket=SimpleNamespace(envelope={'message_id':str(uuid4())},status=lambda _:self.status)
        self.published.append((topic,kind,payload));return ticket

def message(w,vm,app,kind,payload,*,instance=None,retained=False,topic_vm=None,seq=1,causation_id=None):
    identity=Identity(vm_id=vm,app_id=app,boot_id=BOOT,agent_instance_id=instance or w.instances[vm])
    value=build(identity,kind,payload,seq)
    if causation_id:value['causation_id']=causation_id
    return Message(topics.vm_app_events(topic_vm or vm,app),value,w.t,retained)

def test_runner_bounded_drain_source_identity_and_serial_failure(tmp_path):
    w=World(tmp_path/'authority');client=Client();sink=MockSink()
    runner=VerifiedGatewayRunner(client,sink,authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m;w.arm()
    payload={'generation':w.m.generation,'v_mps':.17,'steer_rad':.03}
    valid=message(w,A,'autonomous','drive.command',payload)
    runner._enqueue(valid)
    for _ in range(2000):runner._enqueue(message(w,A,'autonomous','drive.command',payload,instance=str(uuid4())))
    runner._enqueue(message(w,A,'autonomous','drive.command',payload,topic_vm=B))
    for _ in range(1000):runner._enqueue(message(w,A,'autonomous','app.heartbeat',{'state':'active','uptime_s':w.t}))
    runner.run_once(0)
    assert runner._queue.qsize()>=900  # one pass does not drain to empty
    row=next(r for r in runner.submissions if r.get('source'))
    assert row['source']['message_id']==valid.envelope['message_id'] and row['source']['execution_id']==w.execs[A]
    assert row['serial_result']=='accepted' and row['device_applied'] is None
    class Broken:
        def write(self,*_):raise SinkError('injected')
    runner._sink=Broken();runner._submit(ForwardDrive('autonomous',w.m.generation,.2,0,source=row['source']))
    # The failed drive and same-turn best-effort STOP are separate submissions.
    assert not w.m.output_permitted and runner.serial_failed==2 and w.m.failure=='OUTPUT_PATH_FAILED'
    assert all(row['device_applied'] is None for row in list(runner.submissions)[-2:])

def test_runner_rejects_retained_and_stale_causation_resume(tmp_path):
    w=World(tmp_path/'authority');client=Client()
    runner=VerifiedGatewayRunner(client,MockSink(),authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m;w.tick(3.2)
    from xenova_gateway.runner import CONSOLE_RESUME
    def resume(cause):
        value=build(Identity(source_id='console'), 'drive.resume.requested',
                    {'expected_generation':w.m.generation},1);value['causation_id']=cause
        runner._feed(w.t,Message(CONSOLE_RESUME,value,w.t))
    runner._control_message_id=str(uuid4());resume(str(uuid4()));assert not w.m._resume_requested
    resume(runner._control_message_id);assert w.m._resume_requested
    p=w.m.prepared[A,'autonomous'].observed
    runner._enqueue(message(w,A,'autonomous','app.readiness',{'applied_generation':w.m.generation,
        'progress_seq':'9999','readiness':'READY','reason':None},retained=True));runner._drain()
    assert w.m.prepared[A,'autonomous'].observed==p

def test_runner_routes_current_nonretained_vm_trust_event(tmp_path):
    w=World(tmp_path/'authority');w.arm();client=Client()
    runner=VerifiedGatewayRunner(client,MockSink(),authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m
    payload={'trust':'UNTRUSTED','reason':'AUTO_DEADLINE_MISS','consecutive_misses':5,'command_id':'delay-3'}
    runner._enqueue(message(w,A,'autonomous','vm.trust',payload));runner._drain()
    assert w.m.active_vm==B and w.auth.value['owner']==B
    # A retained or standby-originated signal cannot revoke the new owner.
    generation=w.m.generation
    runner._enqueue(message(w,B,'autonomous','vm.trust',{**payload,'command_id':'delay-4'},retained=True));runner._drain()
    assert w.m.generation==generation and w.m.active_vm==B

def test_runner_control_puback_is_not_app_application_and_aux_failure_isolated(tmp_path):
    w=World(tmp_path/'authority');client=Client();client.status='pending'
    runner=VerifiedGatewayRunner(client,MockSink(),authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m
    runner._delivery(w.t);runner._delivery(w.t)
    assert not w.m.delivered and not w.m.output_permitted
    client.status='acknowledged';runner._delivery(w.t)
    assert w.m.delivered and not w.m.output_permitted
    client.publish=lambda *_a,**_k:(_ for _ in ()).throw(ConnectionError())
    runner._emit([PublishEvent('drive.mode.changed',{}),ForwardStop(w.m.generation)])
    assert runner.aux_publish_failures==1 and runner.serial_accepted>=1
    old=runner._control_message_id;client.connections+=1;runner._delivery(w.t)
    assert runner._control_message_id!=old and not w.m.output_permitted

def test_dequeued_first_event_survives_scheduler_pause_before_drain(tmp_path,monkeypatch):
    w=World(tmp_path/'authority')
    runner=VerifiedGatewayRunner(Client(),MockSink(),authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    seen=[];runner._feed=lambda t,message:seen.append(message)
    moments=iter((0.,1.))
    monkeypatch.setattr('xenova_gateway.verified_runner.time.perf_counter',lambda:next(moments))
    runner._drain((w.t,'already-dequeued'))
    assert seen==['already-dequeued']


# --- 운영자 취소 (계약 v1.7, S15P31E110-280) ------------------------------------

def _auto_ready(tmp_path):
    w=World(tmp_path/'authority');w.arm();w.perception=False;w.tick(.1)
    w.tick(3.2);assert w.resume();w.tick(3.4)
    w.perception=True;w.tick(3.4);assert w.m.mode is Mode.AUTO_READY
    w.tick(3.2);return w

def _cancelled(w):
    return [a.payload for a in w.actions if isinstance(a,PublishEvent) and a.type=='drive.resume.cancelled']

def test_operator_cancel_stops_countdown_and_keeps_mode(tmp_path):
    w=_auto_ready(tmp_path);generation=w.m.generation
    assert w.resume();w.tick(.5);assert w.m.counting_down
    assert w.m.on_cancel_resume(w.t,'admin',expected_generation=generation,message_id=str(uuid4()))
    w.tick(.02)
    assert _cancelled(w)==[{'reason':'operator_cancelled','generation':generation,'requested_by':'admin'}]
    assert w.m.mode is Mode.AUTO_READY and w.m.generation==generation and not w.m.counting_down
    assert not any(isinstance(a,PublishControl) for a in w.actions)
    w.tick(3.2);assert w.m.mode is Mode.AUTO_READY
    # 새 재개 요청으로만 AUTO 로 간다
    assert w.resume();w.tick(3.2);assert w.m.mode is Mode.AUTO

def test_operator_cancel_rejects_wrong_context_and_redelivery(tmp_path):
    w=_auto_ready(tmp_path);generation=w.m.generation
    assert not w.m.on_cancel_resume(w.t,'admin',expected_generation=generation,message_id=str(uuid4()))  # 카운트다운 없음
    assert w.resume();w.tick(.5)
    assert not w.m.on_cancel_resume(w.t,'admin',expected_generation=generation-1,message_id=str(uuid4()))
    assert not w.m.on_cancel_resume(w.t,'admin',expected_generation=str(generation),message_id=str(uuid4()))
    assert not w.m.on_cancel_resume(w.t,'admin',expected_generation=generation,message_id=None)
    request=str(uuid4())
    assert w.m.on_cancel_resume(w.t,'admin',expected_generation=generation,message_id=request);w.tick(.02)
    # 같은 세대의 다음 카운트다운을 QoS 1 재전달이 끄지 못한다
    assert w.resume();w.tick(.5)
    assert not w.m.on_cancel_resume(w.t,'admin',expected_generation=generation,message_id=request)
    w.tick(3.2);assert w.m.mode is Mode.AUTO

def test_runner_cancel_requires_current_control_causation(tmp_path):
    w=_auto_ready(tmp_path);client=Client()
    runner=VerifiedGatewayRunner(client,MockSink(),authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m
    from xenova_gateway.runner import CONSOLE_RESUME
    def send(kind,cause):
        value=build(Identity(source_id='dom0-server'),kind,{'expected_generation':w.m.generation,'requested_by':'admin'},1)
        value['causation_id']=cause;runner._feed(w.t,Message(CONSOLE_RESUME,value,w.t))
    runner._control_message_id=str(uuid4())
    send('drive.resume.requested',runner._control_message_id);w.tick(.5);assert w.m.counting_down
    send('drive.resume.cancel_requested',str(uuid4()));w.tick(.02);assert w.m.counting_down and not _cancelled(w)
    send('drive.resume.cancel_requested',runner._control_message_id);w.tick(.02)
    assert [c['reason'] for c in _cancelled(w)]==['operator_cancelled'] and w.m.mode is Mode.AUTO_READY
