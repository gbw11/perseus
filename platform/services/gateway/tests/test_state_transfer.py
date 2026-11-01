"""G2: shared state adapter and real envelope/runner preparation, no real planner."""
import copy
import os
import json
from uuid import uuid4
import pytest
from xenova_mqtt import Identity,topics
from xenova_mqtt.client import Message
from xenova_mqtt.envelope import build
from xenova_mqtt.handoff import PROFILE,ManeuverAdapter,StateReceiver,snapshot
from xenova_gateway.authority import AuthorityError
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.sink import MockSink
from test_warm import WarmWorld
from test_verified import A,B,BOOT,Client

CONTEXT=dict(frame='1',configuration='2',maneuver='3',odom_epoch='cccccccc-cccc-4ccc-8ccc-cccccccccccc',road_revision='4',route_revision='5')
BODY=dict(kind='AVOID',side='LEFT',phase='PASS',merge_segment='lane:next')


class StateWorld(WarmWorld):
    def __init__(self,path):
        super().__init__(path);self.arm()
        before=self.bind
        def bind():
            before();v=copy.deepcopy(self.cache._value)
            for row in v['vms'].values():row['app_state_models']['autonomous']='stateful'
            self.cache.install(v)
        self.bind=bind;self.bind()
        self.runner=VerifiedGatewayRunner(Client(),MockSink(),authority=self.auth,bindings=self.cache,
            vm_ids=(A,B),readiness_profile='warm-drive-1',clock=lambda:self.t)
        self.runner.machine=self.m
        self.source_adapter=ManeuverAdapter();self.target_adapter=ManeuverAdapter()
        self.receiver=StateReceiver(self.target_adapter,B)
        initial=StateReceiver(self.source_adapter,A).rebuild(BODY,current=CONTEXT,road_segments={'lane:next'},
            target_binding=self.cache.current(A,self.t),generation=self.m.generation,state_revision='1')
        self.deliver(A,'app.state.applied',initial)
        self.snapshot=self.produce()

    def message(self,vm,kind,payload):
        identity=Identity(vm_id=vm,app_id='autonomous',boot_id=BOOT,agent_instance_id=self.ids[vm]['autonomous'])
        seq=getattr(self,'message_seq',0)+1;self.message_seq=seq
        return Message(topics.vm_app_events(vm,'autonomous'),build(identity,kind,payload,seq),self.t)

    def deliver(self,vm,kind,payload):
        message=self.message(vm,kind,payload)
        self.runner._enqueue(message);self.runner._drain()
        return message

    def produce(self,revision='1'):
        row=self.cache.current(A,self.t)
        body=dict(profile=PROFILE,execution_id=row['execution_id'],intent_revision=1,lifecycle_generation=1,
                  source_generation=self.m.generation,state_revision=revision,context=CONTEXT,maneuver=BODY)
        return self.deliver(A,'app.state.snapshot',body)

    def apply(self,message=None,current=None):
        message=message or self.snapshot
        value=self.receiver.receive(message,control=self.auth.payload(),source_binding=self.cache.current(A,self.t),
            target_binding=self.cache.current(B,self.t),current=current or CONTEXT,road_segments={'lane:next'})
        return self.deliver(B,'app.state.applied',value)


def test_c11_complete_application_then_new_calculation_is_required(tmp_path):
    w=StateWorld(tmp_path/'authority');request=str(uuid4())
    args=dict(mode='AUTO',target_vm=B,expected_owner=A,expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    w.m.planning.begin(request,**args)
    assert w.m.planning.refresh(request)['phase']=='PREPARING'
    assert w.m.active_vm==A and w.m.output_permitted
    # Road/route revision updates are not blanket state invalidation.
    current={**CONTEXT,'road_revision':'99','route_revision':'100'}
    w.apply(current=current)
    assert w.target_adapter.progress==0
    assert w.target_adapter.calculate(current,{'lane:next'},lambda body,ctx:body['merge_segment'])=='lane:next'
    w.tick(.02)
    row=w.m.planning.refresh(request)
    assert row['phase']=='PREPARED' and row['evidence']['continuity']=='continuous'
    result,_=w.m.planning.commit(request)
    assert result['phase']=='COMMITTED' and not w.m.output_permitted
    w.tick(.2)
    assert w.m.output_permitted and w.m.active_vm==B


def test_c12_accepted_old_owner_copy_remains_usable_but_late_update_is_rejected(tmp_path):
    w=StateWorld(tmp_path/'authority');w.apply()
    identity=w.snapshot.envelope['message_id'];generation=w.m.generation
    w.m._change(__import__('xenova_gateway.machine',fromlist=['Mode']).Mode.MANUAL,A,'test context change',w.t,arm=False)
    assert identity in w.m.states.copies
    # A later application of the already accepted complete copy can be retained.
    applied=w.m.states.applications[B,'autonomous']['payload']
    assert w.deliver(B,'app.state.applied',applied)
    assert w.m.states.evidence(B,'autonomous')['copy_message_id']==identity
    before=len(w.m.states.copies)
    w.deliver(A,'app.state.snapshot',{**w.snapshot.payload,'state_revision':'2','source_generation':generation})
    assert len(w.m.states.copies)==before and w.m.states.rejected>=1


@pytest.mark.parametrize('change',['partial','mixed','frame','instance','retained'])
def test_state_refusal_never_qualifies_a_target_and_normal_application_recovers(tmp_path,change):
    w=StateWorld(tmp_path/'authority');source=w.snapshot
    if change=='partial':
        bad={**source.payload};bad.pop('maneuver')
        with pytest.raises(ValueError):w.receiver.receive(w.message(A,'app.state.snapshot',bad),control=w.auth.payload(),
            source_binding=w.cache.current(A,w.t),target_binding=w.cache.current(B,w.t),current=CONTEXT,road_segments={'lane:next'})
    elif change=='frame':
        with pytest.raises(ValueError):w.apply(current={**CONTEXT,'frame':'2'})
    elif change=='retained':
        with pytest.raises(ValueError):w.receiver.receive(Message(source.topic,source.envelope,w.t,True),control=w.auth.payload(),
            source_binding=w.cache.current(A,w.t),target_binding=w.cache.current(B,w.t),current=CONTEXT,road_segments={'lane:next'})
    else:
        payload=w.receiver.receive(source,control=w.auth.payload(),source_binding=w.cache.current(A,w.t),
            target_binding=w.cache.current(B,w.t),current=CONTEXT,road_segments={'lane:next'})
        payload['execution_id' if change=='instance' else 'copy_message_id']=str(uuid4())
        w.deliver(B,'app.state.applied',payload)
    assert w.m.states.evidence(B,'autonomous') is None
    w.apply();assert w.m.states.evidence(B,'autonomous') is not None


def test_initial_rebuild_is_available_but_not_claimed_as_continuous_transfer(tmp_path):
    w=StateWorld(tmp_path/'authority')
    rebuilt=w.receiver.rebuild(BODY,current=CONTEXT,road_segments={'lane:next'},target_binding=w.cache.current(B,w.t),
                              generation=w.m.generation,state_revision='1')
    w.deliver(B,'app.state.applied',rebuilt);w.tick(.02)
    request=str(uuid4());w.m.planning.begin(request,'AUTO',B,expected_owner=A,expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    assert w.m.planning.refresh(request)['evidence']['continuity']=='stop_then_resume'
    w.m.planning.commit(request);w.tick(.2)
    assert not w.m.output_permitted and w.auth.value['rearm_required']


def test_state_change_after_prepared_invalidates_only_that_preparation(tmp_path):
    w=StateWorld(tmp_path/'authority');w.apply();w.tick(.02);request=str(uuid4())
    w.m.planning.begin(request,'AUTO',B,expected_owner=A,expected_generation=w.m.generation,deadline_ms=int(w.t*1000)+2000)
    assert w.m.planning.refresh(request)['phase']=='PREPARED'
    w.instances={} # unrelated test object cannot change actual accepted identities
    w.ids[B]['autonomous']=str(uuid4());w.bind()
    assert w.m.planning.refresh(request)['phase']=='FAILED'
    assert w.m.active_vm==A and w.m.output_permitted
