"""Warm qualification cannot stand in for activation, work or output permission."""
import os
from uuid import uuid4
import pytest
from xenova_mqtt.lifecycle import parse,PROFILE
from xenova_mqtt.readiness import parse as legacy_parse
from xenova_mqtt.drive_binding import BindingCache
from xenova_gateway.authority import AuthorityStore
from xenova_gateway.warm import WarmDriveMachine
from xenova_gateway.machine import Mode,ForwardDrive
from test_verified import A,B,HOST,BOOT

def report(generation,check,progress=0,phase='WAITING',state='QUALIFIED',reason=None):
    return dict(profile=PROFILE,applied_generation=generation,check_seq=str(check),progress_seq=str(progress),
                phase=phase,readiness=state,reason=reason)

class WarmWorld:
    def __init__(self,path,first=A,mode='AUTO'):
        self.t=100.;self.alive={A,B};self.perception=True
        self.ids={vm:{app:str(uuid4()) for app in ('autonomous','manual')} for vm in (A,B)}
        self.executions={vm:str(uuid4()) for vm in (A,B)}
        self.count={};self.progress={}
        self.auth=AuthorityStore(path,HOST,uid=os.geteuid(),mountpoint=None)
        self.auth.initialize(first);self.auth.update(mode=mode,generation=42,phase='DISARMED')
        self.cache=BindingCache(HOST,clock=lambda:self.t);self.bind()
        self.m=WarmDriveMachine(vm_ids=(A,B),authority=self.auth,bindings=self.cache,clock=lambda:self.t)
        self.m.start(self.t);self.m.note_transport(True,self.t);self.actions=[]
    def bind(self):
        self.cache.install(dict(schema_version='1.0-drive-bindings',host_boot_id=HOST,
            observed_monotonic_ms=int(self.t*1000),expires_monotonic_ms=int(self.t*1000)+3000,
            vms={vm:dict(status='observed',profile=PROFILE,execution_id=self.executions[vm],boot_id=BOOT,
                app_instance_id=str(uuid4()),app_instances=self.ids[vm],domid=i+1,xen_unique_id=str(i+1),
                app_state_models={'autonomous':'stateless','manual':'stateless'},
                intent_revision=1,lifecycle_generation=1,observed_monotonic_ms=int(self.t*1000))
                for i,vm in enumerate((A,B)) if vm in self.alive}))
    def tick(self,seconds=.02,*,apply=True):
        for _ in range(round(seconds/.02)):
            self.t+=.02;self.bind();self.m.note_delivery(self.m.generation,'acknowledged',self.t)
            for vm in self.alive:
                for app in ('autonomous','manual'):
                    key=vm,app;identity=self.ids[vm][app]
                    assert self.m.accept_session(vm,BOOT,identity,self.t,app=app)
                    self.count[key]=self.count.get(key,0)+1
                    active=vm==self.m.active_vm and app==self.m.active_app
                    if active:self.progress[key]=self.progress.get(key,0)+1
                    reason='INPUT_STALE' if app=='autonomous' and not self.perception else None
                    payload=report(self.m.generation,self.count[key],self.progress.get(key,0),
                        'EXECUTING' if active else 'WAITING','NOT_READY' if reason else 'READY' if active else 'QUALIFIED',reason)
                    if apply:self.m.on_lifecycle(app,vm,payload,self.t)
                    if active:self.m.on_command(app,vm,{'generation':self.m.generation,'v_mps':.1,'steer_rad':0},self.t,
                        {'message_id':str(uuid4())})
            self.actions=self.m.step(self.t)
    def resume(self):return self.m.on_resume(self.t,'test',expected_generation=self.m.generation,message_id=str(uuid4()))
    def arm(self):self.tick(3.2);assert self.resume();self.tick(6.4);assert self.m.output_permitted

@pytest.mark.parametrize('first,other',[(A,B),(B,A)])
def test_symmetric_warm_mode_failover_rejoin(tmp_path,first,other):
    w=WarmWorld(tmp_path/'authority',first);w.arm()
    assert w.progress.get((other,'autonomous'),0)==0 and w.m.node_status(other,w.t)=='standby'
    w.perception=False;w.tick(.1);assert w.m.mode is Mode.MANUAL and w.m.active_vm==first
    w.tick(3.2);w.alive.remove(first);w.tick(.2)
    assert w.m.active_vm==other and w.m.mode is Mode.MANUAL
    w.perception=True;w.tick(3.4);assert w.m.mode is Mode.AUTO_READY
    w.tick(3.2);assert w.resume();w.tick(3.2)
    assert w.m.mode is Mode.AUTO and w.m.output_permitted
    old=w.ids[first]['autonomous'];w.alive.add(first);w.executions[first]=str(uuid4())
    w.ids[first]={app:str(uuid4()) for app in ('autonomous','manual')}
    w.tick(3.4);assert w.m.node_status(first,w.t)=='standby' and w.m.active_vm==other
    assert not w.m.accept_session(first,BOOT,old,w.t,app='autonomous')
    w.alive.remove(other);w.tick(.2)
    assert w.m.active_vm==first and w.m.mode is Mode.AUTO
    assert any(isinstance(action,ForwardDrive) for action in w.actions)

def test_qualification_not_activation_and_no_legacy_fallback(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.tick(3.2)
    assert w.m._qualified(B,'autonomous',w.t,held=True) and not w.m._ready(B,'autonomous',w.t)
    assert not w.m.output_permitted
    assert not w.m.on_readiness('autonomous',A,dict(applied_generation=42,progress_seq='999',readiness='READY',reason=None),w.t)
    w.m._change(Mode.AUTO,B,'test',w.t,arm=True);gen=w.m.generation;end=w.auth.value['pending']['deadline_ms']
    w.m.note_delivery(gen,'acknowledged',w.t)
    w.tick(1.1,apply=False)
    assert w.m.phase=='FAILED' and w.m.generation==gen and not w.m.output_permitted
    assert w.auth.value['pending']['deadline_ms']==end

def test_app_replacement_preserves_other_app_and_old_reports_cannot_renew(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.tick(3.2)
    manual=w.m.prepared[A,'manual'];observed=manual.observed;qualified=w.m.qualification[A,'manual']
    old=w.ids[A]['autonomous'];w.ids[A]['autonomous']=str(uuid4());w.bind()
    assert w.m.accept_session(A,BOOT,w.ids[A]['autonomous'],w.t,app='autonomous')
    assert w.m.prepared[A,'manual'] is manual and manual.observed==observed and w.m.qualification[A,'manual']==qualified
    assert not w.m.accept_session(A,BOOT,old,w.t,app='autonomous')
    assert not w.cache.accepts(A,BOOT,w.ids[A]['manual'],w.t,app='autonomous')
    value=report(w.m.generation,100)
    assert w.m.on_lifecycle('autonomous',A,value,w.t)
    at=w.m.qualification[A,'autonomous'][1]
    assert not w.m.on_lifecycle('autonomous',A,value,w.t+.1)
    assert w.m.qualification[A,'autonomous'][1]==at
    assert not w.m._qualified(A,'autonomous',w.t+.301)

def test_profile_validation_and_stalled_ready_does_not_renew_work(tmp_path):
    with pytest.raises(ValueError):parse(report(42,1,0,'WAITING','READY'))
    with pytest.raises(ValueError):legacy_parse(report(42,1))
    w=WarmWorld(tmp_path/'authority');w.tick(.02)
    p=w.m.prepared[A,'autonomous'];progress=p.progress;at=p.observed
    for n in range(20):
        w.t+=.02;w.bind()
        w.m.on_lifecycle('autonomous',A,report(42,100+n,progress,'EXECUTING','READY'),w.t)
    assert p.observed==at and not w.m._ready(A,'autonomous',w.t)

def test_app_only_uncertain_binding_does_not_revoke_other_app(tmp_path):
    w=WarmWorld(tmp_path/'authority',mode='MANUAL');w.perception=False;w.arm()
    manual=w.m.prepared[A,'manual'];at=manual.observed
    rows={vm:w.cache.current(vm,w.t) for vm in (A,B)}
    rows[A]['app_status']={'autonomous':'provisioning','manual':'observed'}
    w.cache.install(dict(schema_version='1.0-drive-bindings',host_boot_id=HOST,
        observed_monotonic_ms=int(w.t*1000),expires_monotonic_ms=int(w.t*1000)+3000,vms=rows))
    assert not w.m._bound(A,w.t,'autonomous') and w.m._bound(A,w.t,'manual')
    assert w.m.prepared[A,'manual'] is manual and manual.observed==at and w.m._ready(A,'manual',w.t)
    w.m.on_command('manual',A,dict(generation=w.m.generation,v_mps=.1,steer_rad=0),w.t,{'message_id':str(uuid4())})
    assert any(isinstance(action,ForwardDrive) for action in w.m.step(w.t))
    assert w.m.mode is Mode.MANUAL and w.m.output_permitted

def test_reconnect_drops_prior_qualification_and_queued_observation(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.tick(3.2)
    assert w.m._qualified(B,'autonomous',w.t,held=True)
    earlier=w.t;w.t+=.01;w.bind();w.m.block('TRANSPORT_RECONNECTED',w.t)
    assert not w.m._qualified(B,'autonomous',w.t)
    assert not w.m.on_lifecycle('autonomous',B,report(w.m.generation,9999),earlier)
    w.tick(.02);assert w.m._qualified(B,'autonomous',w.t)
    assert not w.m._qualified(B,'autonomous',w.t,held=True) and not w.m.output_permitted

def test_queued_current_message_uses_current_binding_and_original_freshness(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.tick(.02);received=w.t
    w.t+=.05;w.bind()
    value=report(w.m.generation,999)
    assert w.m.on_lifecycle('autonomous',B,value,received)
    assert w.m.qualification[B,'autonomous'][1]==received
    command=dict(generation=w.m.generation,v_mps=.1,steer_rad=0)
    assert w.m.on_command('autonomous',A,command,received,{'message_id':str(uuid4())})
    assert w.m.nodes[A]['autonomous'].last_command_t==received
    w.t+=.11;w.bind()
    assert not w.m.on_command('autonomous',A,command,received,{'message_id':str(uuid4())})
    w.t+=.15;w.bind();assert not w.m.on_lifecycle('autonomous',B,report(w.m.generation,1000),received)

@pytest.mark.parametrize('delay,accepted',[(.05,True),(.11,False)])
def test_runner_command_queue_uses_current_binding_without_renewing_age(tmp_path,delay,accepted):
    from xenova_mqtt import Identity,topics
    from xenova_mqtt.envelope import build
    from xenova_mqtt.client import Message
    from xenova_gateway.verified_runner import VerifiedGatewayRunner
    from xenova_gateway.sink import MockSink
    from test_verified import Client
    w=WarmWorld(tmp_path/'authority');w.tick(.02);received=w.t
    runner=VerifiedGatewayRunner(Client(),MockSink(),authority=w.auth,bindings=w.cache,
        vm_ids=(A,B),clock=lambda:w.t,readiness_profile=PROFILE)
    runner.machine=w.m
    identity=Identity(vm_id=A,app_id='autonomous',boot_id=BOOT,agent_instance_id=w.ids[A]['autonomous'])
    envelope=build(identity,'drive.command',dict(generation=w.m.generation,v_mps=.1,steer_rad=0),1)
    runner._enqueue(Message(topics.vm_app_events(A,'autonomous'),envelope,received))
    w.t+=delay;w.bind();runner._drain()
    reference=w.m.references.get((A,'autonomous'),{})
    assert (reference.get('message_id')==envelope['message_id']) is accepted
    if accepted:
        assert reference['execution_id']==w.executions[A]
        assert reference['received_monotonic']==received
        assert w.m.nodes[A]['autonomous'].last_command_t==received
