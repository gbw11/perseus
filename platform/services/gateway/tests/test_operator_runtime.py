"""실제 actor/Authority fsync/임무 원장. app·physical 입력은 명시 typed 시험 fixture다.

실제 serial/clock/STOP 생산자는 test_feedback3_adapter의 독립 PTY 흐름으로 검증한다.
이 fixture의 READY/정지 표본을 실물·C planner 실행 증거로 계산하지 않는다.
"""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from xenova_drive_world import encode,decode,schema
from xenova_gateway.authority import AuthorityError
from xenova_gateway.drive_link import DriveLinkIO,DriveLinkProvider
from xenova_gateway.frame import StopFrame
from xenova_gateway.guest_clock import GuestClockPolicy
from xenova_gateway.manual_source import ManualProfile
from xenova_gateway.machine import Mode
from xenova_gateway.scene_authority import MatchedStop,COMMAND3,FEEDBACK3,StationaryPolicy
from xenova_gateway.sink import MockSink
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from test_verified import A,B,HOST,BOOT,Client
from test_warm import WarmWorld,report
from test_drive_link import trust

pytestmark=pytest.mark.skipif(not hasattr(os,'O_DIRECTORY'),reason='actual POSIX Authority fsync')


class TypedPhysical(MockSink):
    def __init__(self,world):
        super().__init__();self.world=world;self.device=self;self.sequence=0;self.raw=None;self.sample=0
        self.pi=str(uuid4());self.session=str(uuid4());self.gateway=None
        self.steering_available=True
        self.stationary_policy=StationaryPolicy(10000,30000,200000,.000001,.01,.01,0.,0.)
        self.odometry=SimpleNamespace(config=SimpleNamespace(metres_per_tick=.000001))
    def write(self,frame,at):
        count=super().write(frame,at)
        if isinstance(frame,StopFrame):
            self.sequence+=1;self.frame=frame
        return count
    def measure(self):
        at=int(self.world.t*1e6);self.sample+=2;values=[]
        for n,stamp in ((self.sample-1,at-20000),(self.sample,at)):
            value=schema.VehicleSample(major=1,vehicle='rc-test-1',host_boot=HOST,device_session=self.session,
                odom_epoch=self.session,sequence=n,source_sample=n,source_us=stamp,pi_sample_us=stamp,clock_error_us=0,
                left_ticks=0,right_ticks=0,odom_pose=schema.Pose(x=0,y=0,yaw=0),velocity_mps=0,
                position_error_m=0,heading_error_rad=0,quality=3,calibration='encoder-lab-1',
                gateway_instance=self.gateway,pi_session=self.pi)
            self.actuator_observation(value);values.append(value)
        self.vehicle=values[-1]
        if not self.sequence:return
        f=self.frame
        command=COMMAND3.pack(UUID(self.pi).bytes,UUID(self.session).bytes,self.sequence,bytes(16),0,0,0,0,f.generation,f.counter,2,0,0,0,1,0)
        feedback=FEEDBACK3.pack(UUID(self.pi).bytes,UUID(self.session).bytes,self.sequence,f.generation,f.counter,2,2,
            self.sample,self.sample,at,4,0,0,1,bytes(16),0,0,0,0)
        self.raw=MatchedStop(command,feedback,tuple(encode(v) for v in values))
    def actuator_observation(self,value):
        """새 plant 표본의 선언된 centered servo 추정. 실물 측정·장치 APPLIED 증거는 아니다."""
        value.ClearField('measured_steering_rad')
        if self.steering_available:
            value.estimated_steering_rad=0.;value.steering_error_rad=.001
        else:
            value.ClearField('estimated_steering_rad');value.ClearField('steering_error_rad')
    @property
    def matched_stop(self):return self.raw
    def device_view(self):
        return dict(feedback_supported=True,ready=True,fault=None,pi_session=self.pi,device_session=self.session,
            vehicle_sample=None,accepted=None,applied=None,physical_stop=None)


class OperatorWorld:
    def __init__(self,path):
        self.w=WarmWorld(path,mode='MANUAL');self.w.auth.update(manual_hold=True);self.w.arm()
        self.sink=TypedPhysical(self.w);self.client=Client();self.client.identity=SimpleNamespace(agent_instance_id=str(uuid4()))
        self.r=VerifiedGatewayRunner(self.client,self.sink,authority=self.w.auth,bindings=self.w.cache,vm_ids=(A,B),
            clock=lambda:self.w.t,readiness_profile='warm-drive-1');self.r.machine=self.w.m
        self.io=DriveLinkIO('127.0.0.1',1883,None,None,(A,B),clock=lambda:self.w.t)
        self.r.drive_link=DriveLinkProvider(self.r,self.io,trust(self.w),self.client.identity.agent_instance_id)
        self.sink.gateway=self.r.drive_link.instance
        self.r.manual_profile=ManualProfile(.1,.08,.3);self.r.guest_clock_policy=GuestClockPolicy(1000,100,1000000,20000,1000000)
        self.r.configure_operator();self.o=self.r.operator;self.w.m.manual_registry=self.o.sources
        self.w.m.durable_write_available=self.r._durable_available
        self.pump(lambda:self.o.ready)
    def view(self):
        # Bare protobuf numeric fields must remain native integers at this internal port.
        v=self.sink.vehicle
        value=dict(vehicle=v.vehicle,host_boot=v.host_boot,device_session=v.device_session,odom_epoch=v.odom_epoch,
            sequence=v.sequence,source_sample=v.source_sample,source_us=v.source_us,pi_sample_us=v.pi_sample_us,clock_error_us=0,
            left_ticks=0,right_ticks=0,odom_pose=dict(x=0,y=0,yaw=0),velocity_mps=0,position_error_m=0,heading_error_rad=0,quality=3,
            calibration=v.calibration,pi_session=v.pi_session)
        for key in ('measured_steering_rad','estimated_steering_rad','steering_error_rad'):
            if v.HasField(key):value[key]=getattr(v,key)
        return value
    def tick(self):
        self.w.t+=.002;self.w.bind();self.sink.measure()
        self.sink.device_view=lambda:dict(feedback_supported=True,ready=True,fault=None,pi_session=self.sink.pi,
            device_session=self.sink.session,vehicle_sample=self.view(),accepted=None,applied=None,physical_stop=None)
        self.o.tick()
    def pump(self,predicate):
        end=time.monotonic()+3
        while not predicate():
            assert time.monotonic()<end,'actual actor journal did not settle'
            self.tick();time.sleep(.001)
    def apps(self):
        m=self.w.m;self.w.t+=.02;self.w.bind();m.note_delivery(m.generation,'acknowledged',self.w.t)
        for vm in (A,B):
            for app in ('manual','autonomous'):
                assert m.accept_session(vm,BOOT,self.w.ids[vm][app],self.w.t,app=app)
                key=vm,app;self.w.count[key]=self.w.count.get(key,0)+1
                active=vm==m.active_vm and app==m.active_app
                if active:self.w.progress[key]=self.w.progress.get(key,0)+1
                m.on_lifecycle(app,vm,report(m.generation,self.w.count[key],self.w.progress.get(key,0),
                    'EXECUTING' if active else 'WAITING','READY' if active else 'QUALIFIED'),self.w.t)
                m.on_command(app,vm,dict(generation=m.generation,v_mps=0.,steer_rad=0.),self.w.t,dict(message_id=str(uuid4())))
        self.r._emit(m.step(self.w.t))
    def apply(self,call):
        response=self.send(call);assert response['ok']
        end=time.monotonic()+5
        while not self.o._known(call['request_id'])['terminal']:
            assert time.monotonic()<end
            self.tick();self.apps();time.sleep(.001)
        return self.o._known(call['request_id'])
    def call(self,action,args=None):
        self.w.t+=.002;issued=int(self.w.t*1000)
        return dict(version=2,action=action,request_id=f'g1:{HOST}:{issued}:{uuid4()}',host_boot_id=HOST,
            gateway_instance_id=self.o.instance,expected_generation=self.w.m.generation,expected_owner=self.w.m.active_vm,
            deadline_pi_ms=issued+10000,args=args or {})
    def send(self,call):
        result=self.o.submit(call);self.pump(lambda:not result.empty());return result.get_nowait()
    def close(self):self.r.authority_worker().close()


@pytest.fixture
def world(tmp_path):
    w=OperatorWorld(tmp_path/'authority')
    try:yield w
    finally:w.close()


def test_global_stop_original_key_durable_result_replay_and_conflict(world):
    w=world;call=w.call('stop');reply=w.send(call);assert reply['ok']
    w.pump(lambda:w.o._known(call['request_id'])['terminal'])
    row=deepcopy(w.o._known(call['request_id']));assert row['result'] in ('HOLD_CONFIRMED','STOP_UNCONFIRMED')
    assert w.o.hold and not w.w.m.output_permitted and w.sink.frames
    original=w.w.auth.path.read_bytes();generation=w.w.m.generation
    assert w.send(call)['data']['original']==row['original']
    assert w.w.auth.path.read_bytes()==original and w.w.m.generation==generation
    conflict=deepcopy(call);conflict['action']='parking.cancel'
    assert w.send(conflict)['error']['code']=='IDEMPOTENCY_CONFLICT'
    assert w.o.worker is w.r.authority_worker()


def test_busy_actual_fsync_does_not_delay_ram_stop_or_query(world,monkeypatch):
    w=world;entered=threading.Event();release=threading.Event();save=w.w.auth.save
    def blocked(value):
        entered.set();assert release.wait(2);return save(value)
    monkeypatch.setattr(w.w.auth,'save',blocked)
    call=w.call('stop');pending=w.o.submit(call);w.tick();assert entered.wait(1)
    try:
        start=time.monotonic();query=w.o.submit(dict(version=2,action='status'));w.tick()
        assert time.monotonic()-start<.2 and query.get_nowait()['ok']
        assert w.o.hold and w.o.worker.busy and isinstance(w.sink.frames[-1][1],StopFrame)
        assert pending.empty()
    finally:release.set()
    w.pump(lambda:not pending.empty());assert pending.get_nowait()['ok']


def test_unconsumed_original_control_ack_cannot_apply_old_generation_readiness(world):
    """실제 저장은 끝났지만 actor callback 전인 창에서 old READY를 섞지 않는다."""
    w=world;m=w.w.m;original_generation=m.generation;done=[]
    fields=w.w.auth.issue_fields(Mode.MANUAL,m.active_vm,'test-journal-result-window',w.w.t,1.,
        arm=True,manual=dict(manual_session=w.o.sources.sid,manual_policy='NEUTRAL_THEN_ACTION'))
    m.output_permitted=False;m._authorized=False
    def committed(ok,unknown):
        done.append((ok,unknown))
        if ok:
            m._authorized=True
            w.r._emit(m._adopt_control(Mode.MANUAL,m.active_vm,'test-journal-result-window',w.w.t))
    assert w.o._proposal('test-journal-result-window',fields,'control',w.o.now+1_000_000,committed)
    end=time.monotonic()+2
    while w.o.worker.busy or w.o.worker._results.empty():
        assert time.monotonic()<end,'actual journal ACK was not published'
        time.sleep(.001)
    assert not done and w.w.auth.value['generation']==original_generation+1
    before=w.w.auth.path.read_bytes()
    assert w.w.auth.value['phase']=='PENDING' and not w.r._durable_available()
    w.apps()  # 실제 old-generation lifecycle/delivery 입력과 machine.step.
    assert m.generation==original_generation and not m.output_permitted
    assert w.w.auth.path.read_bytes()==before and w.w.auth.value['phase']=='PENDING'
    w.tick()  # actor가 원 ACK를 소비하고 이미 저장된 control을 adopt한다.
    assert done==[(True,False)] and m.generation==original_generation+1
    assert m.phase=='PENDING' and not m.output_permitted
    w.apps()
    assert m.phase=='APPLIED' and m.output_permitted and not w.w.auth.value['rearm_required']


def test_actual_follow_issuer_same_authority_cancel_preserves_budget_context(world):
    w=world;call=w.call('auto.resume');record=w.o.missions.start(call,1,admitted=True)
    done=[]
    assert w.o._proposal(call['request_id'],{'current_mission':record},'test-explicit-issuer-precondition',
        call['deadline_pi_ms']*1000,lambda ok,unknown:done.append(ok))
    w.pump(lambda:bool(done));assert done==[True]
    grant=w.o.missions.grant();assert grant.admitted and grant.kind==1 and grant.mission_id==UUID(record['id']).bytes
    assert w.o.missions.publish()
    raw=w.r.drive_link.missions.current_grant(A);assert raw==encode(grant)
    cancel=w.call('parking.cancel');assert w.send(cancel)['ok']
    w.pump(lambda:w.o._known(cancel['request_id'])['terminal'])
    current=w.o.missions.current();assert current['id']==record['id'] and current['phase']=='HOLD'
    assert not current['admitted'] and current['deadline']==record['deadline']


def test_parking_issuer_original_search_90s_survives_grant_and_owner_rebind(world,monkeypatch):
    """Scene 자격은 typed 사전조건이며 이 검사는 실제 Scene 승인/주행을 주장하지 않는다."""
    w=world;ref=schema.SceneAdmissionRef(admission_id=uuid4().bytes,admission_revision=7)
    scene_precondition=SimpleNamespace(motion_grant=lambda now:SimpleNamespace(scene=ref))
    monkeypatch.setattr(w.r,'scene_runtime',scene_precondition)
    call=w.call('parking.start',dict(strategy='REVERSE'))
    record=w.o.missions.start(call,2)
    issued=int(call['request_id'].split(':')[2])*1000
    assert record['deadline']==issued+120_000_000
    assert record['search_deadline']==min(record['deadline'],issued+90_000_000)
    original=w.o.missions.grant(record)
    assert original.mission_deadline_pi_us==record['deadline']
    assert original.search_deadline_pi_us==issued+90_000_000
    w.w.t+=.1;w.w.bind()
    rebound=w.o.missions.rebind(record,w.w.m.generation+1,B)
    grant=w.o.missions.grant(rebound)
    assert rebound['owner']==B and rebound['generation']==w.w.m.generation+1
    assert grant.owner_vm==UUID(B).bytes
    assert (rebound['issued'],grant.mission_deadline_pi_us,grant.search_deadline_pi_us)==(
        issued,original.mission_deadline_pi_us,original.search_deadline_pi_us)
    assert record['owner']==w.w.m.active_vm and record['search_deadline']==issued+90_000_000


def test_actual_mode_manual_then_follow_resume_and_original_cancel(world):
    w=world;manual=w.call('mode.manual');row=w.apply(manual)
    assert row['result']=='APPLIED' and w.w.auth.value['manual_hold'] is True
    assert w.w.m.mode is Mode.MANUAL and w.o.sources.kind=='PULSE' and not w.o.hold
    neutral=w.o.sources.status();assert neutral['action_seq']=='0'
    resume=w.call('auto.resume');accepted=w.send(resume);assert accepted['ok']
    # Explicit cancellation fixes this original countdown, not a later resume.
    end=time.monotonic()+5
    while w.o._effects.get(resume['request_id'],{}).get('countdown') is None:
        assert time.monotonic()<end;w.tick();w.apps();time.sleep(.001)
    cancel=w.call('auto.cancel');assert w.send(cancel)['ok']
    w.pump(lambda:w.o._known(cancel['request_id'])['terminal'])
    assert w.o._known(cancel['request_id'])['result']=='CANCELLED'
    w.pump(lambda:w.o._known(resume['request_id'])['terminal'])
    assert w.o._known(resume['request_id'])['result']=='FAILED' and not w.o.hold
    assert w.o.sources.kind=='PULSE' and w.w.m.mode is Mode.MANUAL

    fresh=w.call('auto.resume');completed=w.apply(fresh)
    assert completed['result']=='APPLIED' and w.w.m.mode is Mode.AUTO and not w.o.hold
    mission=w.o.missions.current();grant=w.o.missions.grant()
    assert mission['origin']==fresh['request_id'] and grant.admitted and grant.control_generation==w.w.m.generation
    assert mission['phase']=='FOLLOW' and mission['nonce']==fresh['request_id'].split(':')[-1]
