"""Real Authority/warm actor: preparation, commit, replay and crash boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from xenova_gateway.authority import AuthorityError,AuthorityStore
from xenova_gateway.machine import Mode,ForwardStop
from xenova_gateway.warm import WarmDriveMachine
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.sink import MockSink
from test_verified import A,B,HOST,Client
from test_warm import WarmWorld


def test_explicit_manual_hold_survives_auto_recovery_replay_and_cold_read(tmp_path):
    from xenova_gateway.provider import ProviderError
    w=WarmWorld(tmp_path/'authority',mode='MANUAL');w.perception=False;w.arm()
    session=str(uuid4());sequence=0;before=w.bind
    def bind():
        nonlocal sequence
        before();sequence+=1
        assert w.m.on_manual_input(dict(profile='manual-session-1',session_id=session,sample_seq=str(sequence),
            action_seq='1',generation=w.m.generation,keys=[],valid=True,age_ms=0),w.t)
    w.bind=bind;bind()
    runner=VerifiedGatewayRunner(Client(),MockSink(),authority=w.auth,bindings=w.cache,
        vm_ids=(A,B),clock=lambda:w.t,readiness_profile='warm-drive-1');runner.machine=w.m
    key=str(uuid4());spec=dict(purpose='handoff',mode='MANUAL',manual_hold=True,host_boot_id=HOST,
        target_vm=B,target_execution_id=w.executions[B],intent_revision=1,lifecycle_generation=1,
        expected_owner=A,expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    runner.provider.submit(key,spec);runner.provider.handle(dict(action='commit',request_id=key))
    w.tick(.2);assert w.m.mode is Mode.MANUAL and w.auth.value['manual_hold']
    generation=w.m.generation
    omitted={k:v for k,v in spec.items() if k!='manual_hold'}
    assert runner.provider.submit(key,omitted)['committed_generation']==generation
    with pytest.raises(ProviderError,match='CONFLICT'):runner.provider.submit(key,{**spec,'manual_hold':False})
    with pytest.raises(ValueError):runner.provider.submit(key,{**spec,'manual_hold':0})
    w.perception=True;w.tick(4.)
    assert w.m.mode is Mode.MANUAL and w.m.generation==generation and w.m.output_permitted
    cold=AuthorityStore(tmp_path/'authority',HOST,uid=os.geteuid(),mountpoint=None)
    assert cold.load()['manual_hold'] is True
    assert w.resume();w.tick(3.4)
    assert w.m.mode is Mode.AUTO and not w.auth.value['manual_hold'] and w.m.output_permitted

def prepare(w,request=None):
    request=request or str(uuid4())
    args=dict(mode='AUTO',target_vm=B,expected_owner=A,expected_generation=w.m.generation,
              deadline_ms=int(w.t*1000)+2000)
    return request,args,w.m.planning.begin(request,**args)

def test_prepare_preserves_owner_and_output_then_one_commit_applies_new_work(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();generation=w.m.generation
    request,args,row=prepare(w)
    assert row['phase']=='PREPARING' and w.m.output_permitted and w.m.active_vm==A
    assert w.auth.value['generation']==generation
    assert w.m.planning.begin(request,**args)==row
    row=w.m.planning.refresh(request);assert row['phase']=='PREPARED'
    result,actions=w.m.planning.commit(request)
    assert result['committed_generation']==generation+1 and w.m.active_vm==B
    assert any(isinstance(a,ForwardStop) for a in actions) and not w.m.output_permitted
    assert w.m.planning.commit(request)==(result,[])
    assert w.auth.value['pending']['deadline_ms']<=args['deadline_ms']
    w.tick(.2);assert w.m.output_permitted and w.m.active_vm==B
    assert not w.m.on_command('autonomous',A,dict(generation=generation,v_mps=.1,steer_rad=0),w.t,
                             {'message_id':str(uuid4())})
    w.m._change(Mode.SAFE_STOP,B,'later_user_stop',w.t)
    stopped=w.m.generation
    assert w.m.planning.begin(request,**args)['committed_generation']==generation+1
    assert w.m.planning.commit(request)==(result,[])
    assert w.m.generation==stopped and not w.m.output_permitted

@pytest.mark.parametrize('change',['target','source','expiry','negative'])
def test_precommit_guard_rejects_changed_evidence_without_revoking_healthy_owner(tmp_path,change):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w)
    assert w.m.planning.refresh(request)['phase']=='PREPARED'
    if change=='target':w.ids[B]['autonomous']=str(uuid4());w.bind()
    elif change=='source':w.executions[A]=str(uuid4());w.bind()
    elif change=='expiry':w.t+=2.1;w.bind()
    else:w.m.qualification[B,'autonomous']=None
    generation=w.m.generation
    with pytest.raises(AuthorityError):w.m.planning.commit(request)
    assert w.auth.value['owner']==A and w.auth.value['generation']==generation
    if change in ('target','negative'):assert w.m.output_permitted and w.m._ready(A,'autonomous',w.t)

def test_original_deadline_and_request_content_cannot_be_reissued(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w)
    for modified in ({**args,'deadline_ms':args['deadline_ms']+1},{**args,'target_vm':A}):
        with pytest.raises(AuthorityError):w.m.planning.begin(request,**modified)
    w.t+=2.1
    assert w.m.planning.refresh(request)['phase']=='FAILED'
    assert w.m.planning.begin(request,**args)['phase']=='FAILED'
    assert w.auth.value['generation']==args['expected_generation']

def test_restart_abandons_prepared_approval_but_keeps_original_request(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w)
    w.m.planning.refresh(request)
    auth=AuthorityStore(w.auth.directory,HOST,uid=os.geteuid(),mountpoint=None);auth.load()
    m=WarmDriveMachine(vm_ids=(A,B),authority=auth,bindings=w.cache,clock=lambda:w.t)
    assert m.planning.begin(request,**args)['phase']=='ABANDONED'
    with pytest.raises(AuthorityError):m.planning.commit(request)
    assert m.generation==args['expected_generation'] and not m.output_permitted

def test_post_replace_sync_failure_never_restores_old_authority_or_recommits(tmp_path,monkeypatch):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w);w.m.planning.refresh(request)
    from xenova_mqtt.drive_binding import atomic_json
    def uncertain(path,value):atomic_json(path,value);raise OSError('EIO after replace')
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',uncertain)
    with pytest.raises(AuthorityError):w.m.planning.commit(request)
    disk=json.loads(w.auth.path.read_text())
    assert disk['generation']==args['expected_generation']+1 and disk['owner']==B
    assert not w.m.output_permitted and w.auth.error=='AUTHORITY_STORAGE_UNCERTAIN'
    with pytest.raises(AuthorityError,match='UNCERTAIN'):w.auth.transition(request)
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',atomic_json)
    auth=AuthorityStore(w.auth.directory,HOST,uid=os.geteuid(),mountpoint=None);auth.load()
    m=WarmDriveMachine(vm_ids=(A,B),authority=auth,bindings=w.cache,clock=lambda:w.t)
    result,actions=m.planning.commit(request)
    assert result['committed_generation']==disk['generation'] and actions==[]
    assert auth.value['generation']==disk['generation'] and not m.output_permitted

@pytest.mark.parametrize('first',['cancel','commit'])
def test_cancel_commit_order_has_one_linearization_point(tmp_path,first):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w);w.m.planning.refresh(request)
    if first=='cancel':
        assert w.m.planning.cancel(request)['phase']=='CANCELLED'
        with pytest.raises(AuthorityError):w.m.planning.commit(request)
        assert w.auth.value['generation']==args['expected_generation']
    else:
        result,_=w.m.planning.commit(request)
        assert w.m.planning.cancel(request)==result
        assert w.auth.value['generation']==args['expected_generation']+1

def test_two_committers_cannot_allocate_two_generations(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w);w.m.planning.refresh(request)
    barrier=Barrier(2)
    def commit():
        barrier.wait()
        return w.auth.commit_transition(request,int(w.t*1000),lambda _:True,arm=False)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:commit(),range(2)))
    assert results[0]==results[1] and w.auth.value['generation']==args['expected_generation']+1

def test_real_runner_queues_before_durable_acceptance_and_preserves_result(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request=str(uuid4())
    runner=VerifiedGatewayRunner(Client(),MockSink(),authority=w.auth,bindings=w.cache,
        vm_ids=(A,B),clock=lambda:w.t,readiness_profile='warm-drive-1');runner.machine=w.m
    response=runner.submit_transition('prepare',request,mode='AUTO',target_vm=B,
        expected_owner=A,expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    assert response.empty() and w.auth.transition(request) is None
    runner._drain();assert response.get_nowait()['record']['phase']=='PREPARING'
    response=runner.submit_transition('refresh',request);runner._drain()
    assert response.get_nowait()['record']['phase']=='PREPARED'
    response=runner.submit_transition('commit',request);runner._drain()
    committed=response.get_nowait();assert committed['record']['phase']=='COMMITTED'
    assert runner.serial_accepted==1 and not w.m.output_permitted
    response=runner.submit_transition('commit',request);runner._drain()
    assert response.get_nowait()==committed and runner.serial_accepted==1

@pytest.mark.parametrize('mode',['AUTO','MANUAL'])
def test_planned_manual_transition_enforces_source_action_policy(tmp_path,mode):
    w=WarmWorld(tmp_path/'authority',mode=mode)
    if mode=='MANUAL':w.perception=False
    w.arm();session=str(uuid4());sequence=0
    def source(action,keys,*,age=0):
        nonlocal sequence
        sequence+=1
        return w.m.on_manual_input(dict(profile='manual-session-1',session_id=session,
            sample_seq=str(sequence),action_seq=str(action),generation=w.m.generation,
            keys=keys,valid=True,age_ms=age),w.t)
    assert source(5,['up'])
    request=str(uuid4());args=dict(mode='MANUAL',target_vm=B,expected_owner=A,
        expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    w.m.planning.begin(request,**args);w.m.planning.refresh(request)
    w.m.planning.commit(request)
    expected='CONTINUE_SESSION' if mode=='MANUAL' else 'NEUTRAL_THEN_ACTION'
    assert w.auth.payload()['manual_policy']==expected
    assert w.auth.value['manual_input_profile']=='manual-session-1'
    assert source(5,['up'])
    moving=dict(generation=w.m.generation,v_mps=.1,steer_rad=0)
    stopped={**moving,'v_mps':0}
    def command(payload):return w.m.on_command('manual',B,payload,w.t,{'message_id':str(uuid4())})
    assert command(moving) is (mode=='MANUAL')
    if mode=='AUTO':
        assert command(stopped);w.tick(.02)
        assert not source(6,[],age=1000) and not w.m.movement.allowed
        assert source(7,['up']) and not command(moving)  # stale neutral did not authorize
        assert source(8,[]);assert command(stopped);w.tick(.02)
        assert source(9,['up']) and command(moving)
    w.tick(.02);assert w.m.output_permitted and w.m.active_vm==B
    # Input expiry is independent of fresh commands/readiness and revokes output.
    w.t+=.201;w.bind();w.m._output(w.t)
    assert not w.m.output_permitted and w.m.failure=='MANUAL_INPUT_UNAVAILABLE'

def test_history_capacity_never_deletes_original_results_or_stops_healthy_owner(tmp_path,monkeypatch):
    monkeypatch.setattr('xenova_gateway.authority.TRANSITION_LIMIT',2)
    w=WarmWorld(tmp_path/'authority');w.arm();first,args,_=prepare(w)
    w.m.planning.cancel(first)
    second,_,_=prepare(w);w.m.planning.cancel(second)
    with pytest.raises(AuthorityError,match='history full'):prepare(w)
    assert w.auth.transition(first)['phase']=='CANCELLED'
    assert w.m.output_permitted and w.m.active_vm==A

@pytest.mark.parametrize('model',[None,'stateful'])
def test_no_stateless_fallback_for_unknown_or_stateful_application(tmp_path,model):
    w=WarmWorld(tmp_path/'authority');w.arm()
    value=json.loads(json.dumps(w.cache._value))
    if model is None:value['vms'][B].pop('app_state_models')
    else:value['vms'][B]['app_state_models']['autonomous']=model
    w.cache.install(value)
    if model is None:
        with pytest.raises(AuthorityError,match='supported state model'):prepare(w)
        assert not w.auth.value.get('transitions')
    else:
        request,_,row=prepare(w)
        assert row['phase']=='PREPARING'
        assert w.m.planning.refresh(request)['phase']=='PREPARING'
        with pytest.raises(AuthorityError,match='not prepared'):w.m.planning.commit(request)
    assert w.m.output_permitted

def test_competing_preparations_are_abandoned_in_same_commit(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm()
    first,args,_=prepare(w);second,_,_=prepare(w)
    w.m.planning.refresh(first);w.m.planning.refresh(second)
    w.m.planning.commit(first)
    assert w.auth.transition(second)['phase']=='ABANDONED'
    with pytest.raises(AuthorityError):w.m.planning.commit(second)
    assert w.m.generation==args['expected_generation']+1

@pytest.mark.parametrize('point',['prepared','committed'])
def test_process_exit_and_new_process_preserve_original_transition(tmp_path,point):
    request=str(uuid4());directory=tmp_path/'authority'
    script='''
import os,sys
sys.path.insert(0,sys.argv[1])
from test_warm import WarmWorld
from test_verified import A,B
w=WarmWorld(__import__('pathlib').Path(sys.argv[2]));w.arm()
w.m.planning.begin(sys.argv[3],mode='AUTO',target_vm=B,expected_owner=A,
 expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
w.m.planning.refresh(sys.argv[3])
if sys.argv[4]=='committed':w.m.planning.commit(sys.argv[3])
os._exit(37)
'''
    child=subprocess.run([sys.executable,'-B','-c',script,str(Path(__file__).parent),str(directory),request,point],
                         capture_output=True,text=True,timeout=30)
    assert child.returncode==37,child.stderr
    saved=json.loads((directory/'control.json').read_text());generation=saved['generation']
    auth=AuthorityStore(directory,HOST,uid=os.geteuid(),mountpoint=None);auth.load()
    from xenova_mqtt.drive_binding import BindingCache
    m=WarmDriveMachine(vm_ids=(A,B),authority=auth,bindings=BindingCache(HOST),clock=lambda:110.)
    row=auth.transition(request)
    assert row['spec']==saved['transitions'][0]['spec'] and auth.value['generation']==generation
    if point=='prepared':
        assert row['phase']=='ABANDONED'
        with pytest.raises(AuthorityError):m.planning.commit(request)
    else:
        assert row['phase']=='COMMITTED' and m.planning.commit(request)==(row,[])
    assert not m.output_permitted and auth.value['generation']==generation

def test_new_request_can_choose_verified_old_owner_after_committed_target_failure(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w)
    w.m.planning.refresh(request);w.m.planning.commit(request)
    w.m._fail('CONTROL_APPLY_UNCONFIRMED',w.t);w.tick(3.2)
    w.alive.remove(B);w.bind()
    retry=str(uuid4())
    w.m.planning.begin(retry,mode='AUTO',target_vm=A,expected_owner=B,
        expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    assert w.m.planning.refresh(retry)['phase']=='PREPARED'
    result,_=w.m.planning.commit(retry)
    assert result['committed_generation']==args['expected_generation']+2
    assert w.m.active_vm==A and not w.m.output_permitted
    w.tick(.2);assert not w.m.output_permitted  # fresh explicit rearm remains required

def test_replacement_can_qualify_but_cannot_inherit_committed_output(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request,_,_=prepare(w)
    w.m.planning.refresh(request);w.m.planning.commit(request);w.tick(.2)
    old=w.ids[B]['autonomous'];w.ids[B]['autonomous']=str(uuid4());w.bind()
    assert w.m.accept_session(B,HOST,w.ids[B]['autonomous'],w.t,app='autonomous') is False
    from test_verified import BOOT
    assert w.m.accept_session(B,BOOT,w.ids[B]['autonomous'],w.t,app='autonomous')
    from test_warm import report
    assert w.m.on_lifecycle('autonomous',B,report(w.m.generation,999),w.t)
    assert w.m._qualified(B,'autonomous',w.t) and not w.m._ready(B,'autonomous',w.t)
    assert not w.m.accept_session(B,BOOT,old,w.t,app='autonomous')

@pytest.mark.parametrize('action',['block','change'])
def test_abandon_storage_failure_keeps_safety_path_available(tmp_path,monkeypatch,action):
    w=WarmWorld(tmp_path/'authority');w.arm();_,args,_=prepare(w)
    def fail(*_):raise OSError('ENOSPC')
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',fail)
    actions=(w.m.block('TRANSPORT_UNAVAILABLE',w.t) if action=='block' else
             w.m._change(Mode.SAFE_STOP,A,'safety_stop',w.t))
    assert any(isinstance(a,ForwardStop) for a in actions)
    assert not w.m.output_permitted and w.auth.error
    assert w.m.generation==args['expected_generation']

def test_actual_cancel_commit_race_preserves_one_outcome(tmp_path):
    w=WarmWorld(tmp_path/'authority');w.arm();request,args,_=prepare(w);w.m.planning.refresh(request)
    barrier=Barrier(2)
    def run(action):
        barrier.wait()
        try:
            if action=='commit':return w.auth.commit_transition(request,int(w.t*1000),lambda _:True,arm=False)
            return w.auth.finish_preparation(request,'CANCELLED',reason='REQUEST_CANCELLED')
        except AuthorityError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['commit','cancel']))
    final=w.auth.transition(request)
    assert final['phase'] in ('COMMITTED','CANCELLED')
    assert w.auth.value['generation']==args['expected_generation']+(final['phase']=='COMMITTED')
    assert all(row is None or row['phase']==final['phase'] for row in results)

@pytest.mark.parametrize('loss',['expiry','replacement','invalid'])
def test_input_outage_stops_once_and_never_mints_generations_while_waiting(tmp_path,loss):
    w=WarmWorld(tmp_path/'authority',mode='MANUAL');w.perception=False;w.arm();session=str(uuid4())
    def source(seq):
        assert w.m.on_manual_input(dict(profile='manual-session-1',session_id=session,sample_seq=str(seq),
            action_seq='1',generation=w.m.generation,keys=['up'],valid=True,age_ms=0),w.t)
    source(1);request=str(uuid4())
    w.m.planning.begin(request,mode='MANUAL',target_vm=B,expected_owner=A,
        expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    w.m.planning.refresh(request);w.m.planning.commit(request);source(2);w.tick(.02)
    assert w.m.output_permitted
    if loss=='expiry':w.t+=.201
    else:
        assert w.m.on_manual_input(dict(profile='manual-session-1',session_id=(str(uuid4()) if loss=='replacement' else session),
            sample_seq='3',action_seq='1',generation=w.m.generation,keys=[],valid=loss!='invalid',age_ms=0),w.t)
    w.bind();w.m.step(w.t)
    stopped=w.m.generation;assert w.m.mode is Mode.SAFE_STOP and not w.m.output_permitted
    w.tick(3.4);assert w.m.generation==stopped and not w.m.output_permitted
