"""Measured limits use controlled fixtures; no real vehicle values are asserted."""
import json
import os
from dataclasses import replace

import pytest
from xenova_gateway.config import parse
from xenova_gateway.frame import DriveFrame,StopFrame,decode,encode
from xenova_gateway.machine import valid_command,ForwardDrive
from xenova_gateway.output_guard import VehicleLimits,LimitedSink
from xenova_gateway.sink import MockSink,SinkError


def limits(**changes):
    return replace(VehicleLimits(-.5,.5,-.3,.3,2.,3.),**changes)


def test_manual_activation_requires_original_session_stop_and_finite_completion(tmp_path):
    from uuid import uuid4
    from xenova_gateway.verified_runner import VerifiedGatewayRunner
    from xenova_gateway.output_owner import SubmissionReceipt
    from xenova_gateway.machine import Mode
    from test_warm import WarmWorld
    from test_verified import A,B,Client
    pi,device=str(uuid4()),str(uuid4())
    class Device(MockSink):
        def device_view(self):return dict(feedback_supported=True,ready=True,fault=None,pi_session=pi,device_session=device)
        def write(self,frame,t):
            count=super().write(frame,t);r=SubmissionReceipt(count,frame)
            r.metadata=dict(command_sequence=len(self.frames),pi_session=pi,device_session=device);return r
    w=WarmWorld(tmp_path/'authority',mode='MANUAL');w.perception=False;w.arm()
    w.m._change(Mode.MANUAL,A,'test_new_control',w.t,arm=True);w.tick(.1)
    sink=Device();runner=VerifiedGatewayRunner(Client(),sink,authority=w.auth,bindings=w.cache,
        vm_ids=(A,B),clock=lambda:w.t,readiness_profile='warm-drive-1');runner.machine=w.m;runner.drive_link=object()
    action=ForwardDrive('manual',w.m.generation,.1,0.,source={'app_id':'manual'})
    assert not runner._manual_can_move(action,sink.device_view(),w.t)
    original=runner._manual_activation['deadline_ms'];sequence=runner._manual_activation['first_stop']
    event=dict(command_generation=action.generation,command_kind='STOP',status='APPLIED',physical_stop=True,
        pi_session=pi,device_session=device,command_sequence=sequence,observed_monotonic=w.t)
    for changed in ({'physical_stop':None},{'device_session':str(uuid4())},{'command_generation':action.generation-1},
                    {'observed_monotonic':original/1000+.01}):
        runner._manual_observe([{**event,**changed}]);assert not runner._manual_activation['ready']
    runner._manual_observe([event]);assert runner._manual_can_move(action,sink.device_view(),w.t)
    w.m._change(Mode.MANUAL,A,'another_control',w.t,arm=True)
    newer=ForwardDrive('manual',w.m.generation,.1,0.,source={'app_id':'manual'})
    w.t+=.03;assert not runner._manual_can_move(newer,sink.device_view(),w.t)
    original=runner._manual_activation['deadline_ms'];w.t=original/1000+.01
    assert not runner._manual_can_move(newer,sink.device_view(),w.t)
    assert w.m.failure=='PHYSICAL_STOP_UNCONFIRMED' and not w.m.output_permitted
    assert runner._manual_activation['deadline_ms']==original and isinstance(sink.frames[-1][1],StopFrame)


def test_no_invented_serial_limits_and_explicit_schema(tmp_path):
    with pytest.raises(SystemExit):parse(['--sink','serial'],env={})
    path=tmp_path/'limits.json'
    pair=['--vm-a','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','--vm-b','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']
    assert parse(['--sink','serial','--vehicle-limits',str(path),*pair],env={}).vehicle_limits==str(path)
    path.write_text(json.dumps({'schema_version':'1.0-drive-output-limits',**vars(limits())}));path.chmod(0o600)
    assert VehicleLimits.load(path,uid=os.geteuid())==limits()
    path.chmod(0o644)
    with pytest.raises(ValueError):VehicleLimits.load(path,uid=os.geteuid())
    for change in ({'max_v_mps':float('nan')},{'max_v_mps':10**1000},{'max_accel_mps2':0},{'min_v_mps':.1},{'max_v_mps':40}):
        with pytest.raises(ValueError):limits(**change)


def test_range_rejection_latches_but_stop_is_always_submitted():
    sink=MockSink();guard=LimitedSink(sink,limits(),0.)
    with pytest.raises(SinkError,match='RANGE'):guard.write(DriveFrame(1,1,.6,0),.1)
    assert not sink.frames
    with pytest.raises(SinkError):guard.write(DriveFrame(1,2,0,0),.2)
    guard.write(StopFrame(1,3,1),.2)
    assert len(sink.frames)==1 and isinstance(sink.frames[0][1],StopFrame)


def test_rate_uses_accepted_quantized_targets_and_capped_gap():
    sink=MockSink();guard=LimitedSink(sink,limits(),0.)
    guard.write(DriveFrame(1,1,.04,.06),.02)
    guard.write(DriveFrame(1,2,.08,.12),.04)
    before=guard.previous
    with pytest.raises(SinkError,match='RATE'):guard.write(DriveFrame(2,3,.5,.12),100.)
    assert guard.previous==before and len(sink.frames)==2


def test_quantization_cannot_exceed_measured_bounds():
    guard=LimitedSink(MockSink(),limits(max_v_mps=.1006),0.)
    with pytest.raises(SinkError,match='QUANTIZED'):guard.write(DriveFrame(1,1,.1006,0),.1)


def test_burst_preview_defers_exact_target_without_changing_final_guard():
    sink=MockSink();guard=LimitedSink(sink,limits(),0.)
    guard.write(DriveFrame(1,1,.04,.06),.02);before=guard.previous
    target=DriveFrame(1,2,.08,.12);at=guard.drive_ready_at(target)
    assert at==pytest.approx(.04) and guard.previous==before and len(sink.frames)==1
    guard.write(target,at)
    assert guard.previous[:2]==(.08,.12)
    assert guard.drive_ready_at(DriveFrame(1,3,.5,.12))==float('inf')


def test_partial_submission_never_advances_baseline_and_stop_is_attempted():
    class Partial(MockSink):
        def write(self,frame,t):return 1 if isinstance(frame,DriveFrame) else super().write(frame,t)
    sink=Partial();guard=LimitedSink(sink,limits(),0.)
    with pytest.raises(SinkError,match='SUBMISSION'):guard.write(DriveFrame(1,1,.04,0),.02)
    assert guard.previous==(0.,0.,0.)
    guard.write(StopFrame(1,2,1),.03)
    with pytest.raises(SinkError):guard.write(DriveFrame(1,3,0,0),.04)


def test_clock_failure_does_not_block_stop_or_allow_future_drive():
    sink=MockSink();guard=LimitedSink(sink,limits(),1.)
    guard.write(StopFrame(1,1,1),float('nan'))
    assert len(sink.frames)==1
    with pytest.raises(SinkError,match='CLOCK'):guard.write(DriveFrame(1,2,0,0),1.1)


def test_extreme_finite_command_cannot_crash_encoder_or_skip_limit_guard():
    assert decode(encode(DriveFrame(1,1,1e308,-1e308))).v_mps==32.767
    assert valid_command({'generation':1,'v_mps':10**1000,'steer_rad':0}) is None
    guard=LimitedSink(MockSink(),limits(),0.)
    with pytest.raises(SinkError,match='RANGE'):guard.write(DriveFrame(1,1,1e308,0),.1)


def test_actual_verified_runner_fails_and_attempts_stop_on_same_turn(tmp_path,monkeypatch):
    from test_verified import World,Client,A,B,message
    from xenova_gateway.verified_runner import VerifiedGatewayRunner
    w=World(tmp_path/'authority');client=Client();sink=MockSink()
    guard=LimitedSink(sink,limits(),w.t)
    runner=VerifiedGatewayRunner(client,guard,authority=w.auth,bindings=w.cache,vm_ids=(A,B),clock=lambda:w.t)
    runner.subscribe();runner.begin();runner.machine=w.m;w.arm()
    normal=message(w,A,'autonomous','drive.command',{'generation':w.m.generation,'v_mps':.1,'steer_rad':0})
    runner._enqueue(normal);runner.run_once(0)
    assert any(isinstance(frame,DriveFrame) and frame.v_mps==.1 for _,frame,_ in sink.frames)
    assert runner.submissions[-1]['source']['message_id']==normal.envelope['message_id']
    w.tick(.02)
    original=w.auth.update
    def persist_failure(**fields):
        if fields.get('phase')=='FAILED':
            assert not w.m.output_permitted and isinstance(sink.frames[-1][1],StopFrame)
        return original(**fields)
    monkeypatch.setattr(w.auth,'update',persist_failure)
    received=message(w,A,'autonomous','drive.command',{'generation':w.m.generation,'v_mps':.6,'steer_rad':0})
    runner._enqueue(received);runner.run_once(0)
    assert not w.m.output_permitted and w.m.failure=='OUTPUT_PATH_FAILED'
    assert [frame.v_mps for _,frame,_ in sink.frames if isinstance(frame,DriveFrame)]==[.1]
    assert isinstance(sink.frames[-1][1],StopFrame)
    failed=next(row for row in runner.submissions if row['serial_result']=='failed')
    assert failed['failure_reason']=='OUTPUT_RANGE_EXCEEDED'
    assert failed['source']['message_id']==received.envelope['message_id'] and failed['device_applied'] is None
