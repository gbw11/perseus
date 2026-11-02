"""Planned handoff preserves the existing explicit AUTO-resume boundary.

Real Authority, actor queue and warm state machine; execution/guest observations
use the existing WarmWorld fixture. No VM or device is controlled here.
"""
from uuid import uuid4
import pytest
from xenova_gateway.authority import AuthorityError
from xenova_gateway.machine import Mode
from xenova_gateway.planning import context
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.sink import MockSink
from test_verified import A,B,Client
from test_warm import WarmWorld


def manual_ready(path,first=A):
    world=WarmWorld(path,first=first,mode='MANUAL')
    world.perception=False;world.arm()
    world.perception=True;world.tick(6.5)
    assert world.m.mode is Mode.AUTO_READY and world.m.output_permitted
    return world


@pytest.mark.parametrize('first,target',[(A,A),(A,B),(B,B),(B,A)])
def test_new_planned_auto_cannot_skip_explicit_resume(tmp_path,first,target):
    world=manual_ready(tmp_path/'authority',first)
    generation=world.m.generation;request=str(uuid4())
    with pytest.raises(AuthorityError,match='resume/countdown'):
        world.m.planning.begin(request,mode='AUTO',target_vm=target,expected_owner=first,
            expected_generation=generation,deadline_ms=int(world.t*1000)+2000)
    assert world.auth.transition(request) is None
    assert world.m.generation==generation and world.m.active_vm==first
    assert world.m.mode is Mode.AUTO_READY and world.m.output_permitted


@pytest.mark.parametrize('phase',['PREPARING','PREPARED'])
def test_preexisting_preparation_cannot_skip_resume_at_commit(tmp_path,phase):
    world=manual_ready(tmp_path/'authority');generation=world.m.generation;request=str(uuid4())
    # A durable row from an earlier producer is not a new authorization.
    spec=dict(mode='AUTO',target_vm=B,owner=A,generation=generation,
        deadline_ms=int(world.t*1000)+2000,app='autonomous',
        target=context(world.cache.current(B,world.t),'autonomous'),
        source=context(world.cache.current(A,world.t),'manual'),
        source_app='manual',state_model='stateless')
    world.auth.begin_transition(request,spec,int(world.t*1000))
    if phase=='PREPARED':world.auth.finish_preparation(request,phase,evidence={'kind':'stateless_qualification'})
    with pytest.raises(AuthorityError,match='not prepared'):
        world.m.planning.commit(request)
    assert world.m.generation==generation and world.m.mode is Mode.AUTO_READY
    assert world.m.active_vm==A and world.m.output_permitted
    assert world.auth.transition(request)['spec']==spec


@pytest.mark.parametrize('first,other',[(A,B),(B,A)])
def test_explicit_resume_countdown_then_same_mode_handoff_succeeds(tmp_path,first,other):
    world=manual_ready(tmp_path/'authority',first);generation=world.m.generation
    assert world.resume();world.tick(2.9)
    assert world.m.mode is Mode.AUTO_READY and world.m.generation==generation
    world.tick(.4)
    assert world.m.mode is Mode.AUTO and world.m.output_permitted
    assert world.m.generation==generation+1
    world.tick(3.2);request=str(uuid4())
    arguments=dict(mode='AUTO',target_vm=other,expected_owner=first,
        expected_generation=world.m.generation,deadline_ms=int(world.t*1000)+2000)
    world.m.planning.begin(request,**arguments)
    assert world.m.planning.refresh(request)['phase']=='PREPARED'
    committed,_=world.m.planning.commit(request)
    world.tick(.2)
    assert world.m.active_vm==other and world.m.output_permitted
    assert world.m.planning.commit(request)==(committed,[])


def test_real_actor_reports_rejection_without_disabling_manual_owner(tmp_path):
    world=manual_ready(tmp_path/'authority');generation=world.m.generation;request=str(uuid4())
    runner=VerifiedGatewayRunner(Client(),MockSink(),authority=world.auth,bindings=world.cache,
        vm_ids=(A,B),clock=lambda:world.t,readiness_profile='warm-drive-1')
    runner.machine=world.m
    reply=runner.submit_transition('prepare',request,mode='AUTO',target_vm=B,
        expected_owner=A,expected_generation=generation,deadline_ms=int(world.t*1000)+2000)
    assert reply.empty();runner._drain()
    result=reply.get_nowait()
    assert result['ok'] is False and 'resume/countdown' in result['error']
    assert world.auth.transition(request) is None and world.m.generation==generation
    assert world.m.output_permitted and world.m.mode is Mode.AUTO_READY
    assert runner.serial_accepted==runner.serial_failed==0
