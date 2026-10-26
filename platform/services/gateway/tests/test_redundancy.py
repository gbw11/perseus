"""Mode transitions and symmetric failover use real Gateway decisions, no board."""
import pytest
from xenova_gateway.machine import (Mode, Timing, ForwardDrive, ForwardStop, PublishControl, PublishEvent)
from xenova_gateway.redundancy import RedundantControlMachine
from xenova_gateway.config import parse

A="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
BOOT="11111111-1111-4111-8111-111111111111"
RUN="22222222-2222-4222-8222-222222222222"
NEW="33333333-3333-4333-8333-333333333333"

class World:
    def __init__(self, first=A):
        self.m=RedundantControlMachine(vm_ids=(A,B),initial_active_vm=first,
            timing=Timing(ready_hold_s=.15,heartbeat_timeout_s=.10,resume_countdown_s=.12))
        self.t=0.;self.alive={A,B};self.perception=True
        self.actions=self.m.start(self.t)
        for vm in self.alive:assert self.m.accept_session(vm,BOOT,RUN)

    def tick(self, count=1):
        for _ in range(count):
            self.t+=.02
            for vm in self.alive:
                for app in ("manual","autonomous"):
                    state="error" if app=="autonomous" and not self.perception else "standby"
                    self.m.on_heartbeat(app,vm,{"state":state,"uptime_s":self.t},self.t)
                if self.m.active_app:
                    self.m.on_command(self.m.active_app,vm,{"generation":self.m.generation,
                        "v_mps":.1 if vm==A else .2,"steer_rad":0},self.t)
            self.actions=self.m.step(self.t)

@pytest.mark.parametrize("first,other",[(A,B),(B,A)])
def test_continuous_symmetric_rejoin(first,other):
    w=World(first);w.tick(12)
    assert w.m.mode is Mode.AUTO and w.m.active_vm==first
    w.perception=False;w.tick()
    assert w.m.mode is Mode.MANUAL and w.m.active_vm==first
    w.tick(12);w.alive.remove(first);w.tick(7)
    assert w.m.mode is Mode.MANUAL and w.m.active_vm==other
    w.perception=True;w.tick(12)
    assert w.m.mode is Mode.AUTO_READY and w.m.active_vm==other
    w.m.on_resume(w.t,"operator");w.tick()
    assert w.m.counting_down
    w.tick(7)
    assert w.m.mode is Mode.AUTO and w.m.active_vm==other
    assert w.m.accept_session(first,BOOT,NEW)
    assert w.m.node_status(first,w.t)=="unready"
    assert not w.m.accept_session(first,BOOT,RUN)
    w.alive.add(first);w.tick(2)
    assert w.m.node_status(first,w.t)=="unready"
    w.tick(12)
    assert w.m.node_status(first,w.t)=="standby" and w.m.active_vm==other
    w.alive.remove(other);w.tick(7)
    assert w.m.mode is Mode.AUTO and w.m.active_vm==first
    assert any(isinstance(a,ForwardDrive) for a in w.actions)

@pytest.mark.parametrize("first",[A,B])
def test_no_single_heartbeat_auto_resume(first):
    w=World(first);w.tick(12);w.perception=False;w.tick()
    assert w.m.mode is Mode.MANUAL
    w.perception=True;w.tick()
    assert w.m.mode is Mode.MANUAL
    w.tick(12)
    assert w.m.mode is Mode.AUTO_READY
    w.tick(15)
    assert w.m.mode is Mode.AUTO_READY  # operator approval still required
    w.m.on_resume(w.t);w.tick();w.perception=False;w.tick()
    assert w.m.mode is Mode.MANUAL and not w.m.counting_down

def test_unprepared_rejoin_cannot_take_over():
    w=World();w.tick(12);w.alive.remove(A);w.tick(7)
    assert w.m.active_vm==B
    assert w.m.accept_session(A,BOOT,NEW)
    w.alive.add(A);w.tick(2);w.alive.remove(B);w.tick(6)
    assert w.m.mode is Mode.SAFE_STOP
    assert not any(isinstance(a,ForwardDrive) for a in w.actions)

def test_unknown_old_generation_and_expired_candidate_not_output():
    w=World();w.tick(12)
    assert not w.m.on_command("manual",NEW,{"generation":1,"v_mps":1,"steer_rad":0},w.t)
    assert not w.m.on_command("autonomous",A,{"generation":0,"v_mps":1,"steer_rad":0},w.t)
    assert not any(isinstance(a,ForwardDrive) for a in w.m.step(w.t+.11))

def test_failover_requires_new_generation_before_drive():
    w=World();w.tick(12);w.alive.remove(A)
    for _ in range(6):
        w.tick()
        if w.m.active_vm==B:break
    assert w.m.active_vm==B
    assert isinstance(w.actions[0],ForwardStop)
    assert any(isinstance(a,PublishControl) for a in w.actions)
    assert not any(isinstance(a,ForwardDrive) for a in w.actions)
    w.tick()
    assert any(isinstance(a,ForwardDrive) and a.v_mps==.2 for a in w.actions)

def test_heartbeat_without_current_context_is_not_standby():
    w=World();w.tick(12)
    w.m.generation+=1
    assert w.m.node_status(B,w.t)=="unready"

def test_inflight_old_generation_is_liveness_only_during_handover():
    w=World();w.tick(12);w.perception=False;w.tick()
    generation=w.m.generation
    assert w.m.mode is Mode.MANUAL
    for _ in range(9):
        w.t+=.02
        w.m.on_command("autonomous",A,{"generation":generation-1,"v_mps":.5,"steer_rad":0},w.t)
        w.m.on_heartbeat("manual",A,{"state":"standby","uptime_s":w.t},w.t)
        actions=w.m.step(w.t)
        assert w.m.generation==generation and w.m.mode is Mode.MANUAL
        assert not any(isinstance(a,ForwardDrive) for a in actions)
        assert not any(isinstance(a,PublishEvent) and a.type=="drive.fault.detected" for a in actions)
        assert w.m.node_status(A,w.t)=="unready"
    w.tick()
    assert any(isinstance(a,ForwardDrive) for a in w.actions)

def test_generation_change_alone_does_not_report_vm_fault():
    w=World();w.tick(12);w.perception=False;w.tick()
    for _ in range(12):
        w.tick()
        assert not any(isinstance(a,PublishEvent) and a.type=="drive.fault.detected" for a in w.actions)
    w.perception=True
    for _ in range(15):
        w.tick()
        assert not any(isinstance(a,PublishEvent) and a.type=="drive.fault.detected" for a in w.actions)

def test_pair_configuration_and_legacy_default():
    assert parse([],{}).vm_a is None
    config=parse(["--vm-a",A,"--vm-b",B,"--initial-active-vm",B],{})
    assert config.initial_active_vm==B
    for args in (["--vm-a",A],["--vm-a",A,"--vm-b",A],
                 ["--vm-a",A,"--vm-b",B,"--initial-active-vm",NEW]):
        with pytest.raises(SystemExit):parse(args,{})
