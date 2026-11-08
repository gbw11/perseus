"""실제 actor/journal/clock에 공급하는 typed encoder·app leaf. 실물/실제 C READY 증거는 아니다."""
from collections import deque
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID,uuid4

from xenova_drive_world import schema,encode,decode
from xenova_gateway.absolute_meter import AbsoluteWheelMeter
from xenova_gateway.guest_clock import SentClock
from xenova_gateway.scene_authority import COMMAND3,FEEDBACK3,MatchedStop
from xenova_gateway.drive_link import inbound
from test_verified import A,B,HOST
from test_drive_link import snapshot


def activate(w):
    """기존 모드/issuer는 실제 경로로 준비하고 입력/설치 조건은 software leaf로 명시한다."""
    assert w.apply(w.call('auto.resume'))['result']=='APPLIED'
    w.r.episode_support=True
    manifest=Path(__file__).resolve().parents[4]/'platform/apps/drive/planner/generated/primitive-manifest.json'
    w.r.planner_parameters=json.loads(manifest.read_text())['profile']
    physical=w.sink;current=physical.vehicle
    w.r.drive_link.trust.odom_epoch=current.odom_epoch
    meter=AbsoluteWheelMeter(SimpleNamespace(encoder_bits=64,metres_per_tick=.000001,
        metres_per_tick_upper=.000001,calibration=current.calibration,maximum_sample_age_us=200000),HOST)
    rows=deque(maxlen=128)
    previous=schema.VehicleSample();previous.CopyFrom(current)
    previous.sequence-=1;previous.source_sample-=1;previous.source_us-=20000;previous.pi_sample_us-=20000
    for value in (previous,current):
        rows.append(value)
        meter.record(pi_session=value.pi_session,device_session=value.device_session,odom_epoch=value.odom_epoch,
            sequence=value.sequence,source_sample=value.source_sample,source_us=value.source_us,left=value.left_ticks,
            right=value.right_ticks,pi_sample_us=value.pi_sample_us,clock_error_us=value.clock_error_us)
    physical.odometry=SimpleNamespace(config=SimpleNamespace(metres_per_tick=.000001,
        metres_per_tick_upper=.000001,maximum_sample_age_us=200000),absolute=meter)
    original_bind=w.w.bind
    def bind():
        original_bind();value=deepcopy(w.w.cache._value)
        for row in value['vms'].values():row['app_state_models']['autonomous']='stateful'
        w.w.cache.install(value)
    w.w.bind=bind
    original_view=w.view
    def measured_view():
        value=original_view();value.update(left_ticks=physical.vehicle.left_ticks,right_ticks=physical.vehicle.right_ticks)
        return value
    w.view=measured_view
    def view():
        return dict(feedback_supported=True,ready=True,fault=None,pi_session=physical.pi,
            device_session=physical.session,vehicle_sample=w.view(),accepted=None,applied=None,physical_stop=None,
            absolute_meter=meter.snapshot,clock_valid_until_pi_us=int(w.w.t*1000000)+1000000)
    physical.device_view=view
    def advance(microseconds=20000,left=0,right=0):
        w.w.t+=microseconds/1000000;bind();physical.sample+=1
        value=schema.VehicleSample();value.CopyFrom(physical.vehicle)
        value.sequence=physical.sample;value.source_sample=physical.sample
        value.source_us=value.pi_sample_us=int(w.w.t*1000000);value.left_ticks+=left;value.right_ticks+=right
        physical.actuator_observation(value)
        physical.vehicle=value;rows.append(value)
        meter.record(pi_session=value.pi_session,device_session=value.device_session,odom_epoch=value.odom_epoch,
            sequence=value.sequence,source_sample=value.source_sample,source_us=value.source_us,left=value.left_ticks,
            right=value.right_ticks,pi_sample_us=value.pi_sample_us,clock_error_us=value.clock_error_us)
        w.r.drive_link.tick()
    w.advance_meter=advance;w.record_meter=lambda:advance(0);w.w.bind();w.r.drive_link.tick()
    peer=(A,w.w.executions[A],w.w.ids[A]['autonomous'])
    w.o.episodes.declare(peer,31)
    w.o.episodes.sync_mission();w.peer=peer
    return w


def confirm_clock(w):
    now=w.o.now;peer=w.peer;sequence=getattr(w,'clock_sequence',5)+2
    guest=1000000+now-getattr(w,'guest_origin_pi',now)
    value=schema.DriveLink(major=2,probe=schema.ClockProbe(vm=peer[0],execution=peer[1],app_instance=peer[2],
        sequence=sequence,guest_send_us=guest-5000,supported_state_features=31))
    assert w.r.drive_link.receive(A,value,w.w.t-.004)
    with w.io.lock:prepared=w.io.pending.pop((inbound(A),'clock'))
    raw=prepared.stamp(now);w.io.clock_completions.put_nowait(SentClock(peer,w.w.m.generation,raw));w.r.drive_link.tick()
    from hashlib import sha256
    second=schema.DriveLink(major=2,probe=schema.ClockProbe(vm=peer[0],execution=peer[1],app_instance=peer[2],
        sequence=sequence+1,guest_send_us=guest,supported_state_features=31,
        receipt=schema.GuestClockReceipt(probe_sequence=sequence,reply_sha256=sha256(raw).digest(),guest_receive_us=guest-1000)))
    assert w.r.drive_link.receive(A,second,w.w.t)
    w.clock_raw=raw;w.clock_sequence=sequence
    if not hasattr(w,'guest_origin_pi'):w.guest_origin_pi=now


def guest_now(w):return 1000000+w.o.now-w.guest_origin_pi


def mission_execution(w,*,phase=10):
    grant=w.o.missions.grant()
    return schema.MissionExecution(mission_id=grant.mission_id,mission_revision=grant.mission_revision,kind=grant.kind,
        phase=phase,result=0,operator_request_nonce=grant.operator_request_nonce,
        mission_deadline_pi_us=grant.mission_deadline_pi_us,cancel_fence_revision=grant.cancel_fence_revision,
        active_segment=0,scene_admission_id=grant.scene_admission_id,scene_admission_revision=grant.scene_revision,
        strategy=grant.strategy,execution_deadline_pi_us=grant.execution_deadline_pi_us,
        correction_attempts_used=grant.correction_attempts_used,execution_travel_m=grant.execution_travel_m,
        grant_revision=grant.grant_revision,search_travel_m=grant.search_travel_m,search_deadline_pi_us=grant.search_deadline_pi_us)


def state(w,revision=1,calculation=1,*,message_id=None,path_revision=4,phase_revision=1,episode=None,task=False):
    link=snapshot(w.w,w.r.drive_link.trust);body=link.snapshot
    body.state_revision=revision;body.calculation_sequence=calculation;body.vehicle_sequence=w.sink.vehicle.sequence
    body.path_revision=path_revision;body.maneuver_revision=phase_revision;body.message_id=message_id or str(uuid4())
    body.mission.CopyFrom(mission_execution(w));body.episode.CopyFrom(w.o.episodes.registry['snapshot'] if episode is None else episode)
    body.state_features=23
    if task:
        trust=w.r.drive_link.trust;body.state_features|=8;body.world_features=11
        body.task_reference.CopyFrom(schema.TaskReferenceState(home_lane_id=1,from_lane_id=1,selected_lane_id=2,
            changing=True,basis_revision=1,basis_prefix=[schema.Point(x=0,y=1.6),schema.Point(x=2,y=1.6)],
            vehicle=trust.vehicle,mapper_boot=trust.mapper_boot,mapper_instance=trust.mapper_instance,clock_epoch=trust.clock_epoch))
    return body


def accept(w,body):
    link=schema.DriveLink(major=2,snapshot=body)
    return w.r.drive_link.receive(A,decode(encode(link),schema.DriveLink),w.w.t,w.io.prepare_snapshot(link))


def proposal(w,*,action=1,phase=1,phase_revision=1,sequence=1,plan_revision=5):
    """C 후보 대신 명시 typed 기하를 준다. 실제 등록/clock/hash/State 검사는 제품이다."""
    from hashlib import sha256
    from xenova_drive_world import episode_source_sha256,basis_prefix_sha256,plan_sha256
    owner=w.o.episodes;old=owner.registry['snapshot'];trust=w.r.drive_link.trust;meter=owner._meter()
    prior=owner.registry['state'];watermark=1 if prior is None else prior['calculation']
    revision=2 if prior is None else prior['revision']+1
    body=state(w,revision=revision,calculation=watermark+1,path_revision=plan_revision,
        phase_revision=phase_revision,task=True)
    source=schema.EpisodeSourceContext(vehicle=trust.vehicle,mapper_boot=trust.mapper_boot,
        mapper_instance=trust.mapper_instance,map_epoch=trust.map_epoch,frame_epoch=trust.frame_epoch,
        odom_epoch=meter.odom_epoch,clock_epoch=trust.clock_epoch,configuration=trust.configuration,
        calibration=trust.calibration,primitive=bytes.fromhex(trust.primitive),reference_id=body.reference_id,
        reference_revision=body.reference_revision,world_sequence=1,observation_sequence=body.observation_seq)
    if action==1:
        core=schema.EpisodeCore(state=2,episode_id=uuid4().bytes,revision=1,phase=phase,
            phase_revision=phase_revision,home_lane_id=1,target_lane_id=2,policy=1,
            blocker=[schema.Point(x=x,y=y) for x,y in ((.4,.2),(.6,.2),(.6,.4),(.4,.4))],
            blocker_position_bound_m=.01,blocker_world_sequence=1,blocker_observation_sequence=1,
            birth_snapshot_id=uuid4().bytes,basis_revision=1,basis_prefix_count=2,
            basis_prefix_sha256=basis_prefix_sha256(body.task_reference.basis_prefix),
            source_geometry_sha256=episode_source_sha256(source),origin_vehicle_sequence=meter.vehicle_sequence,
            started_pi_us=0,deadline_pi_us=0,phase_started_pi_us=0,phase_deadline_pi_us=0,
            left_baseline_ticks=0,right_baseline_ticks=0,meter_epoch=UUID(meter.meter_epoch).bytes,
            meter_baseline_total_ticks=0,max_travel_m=6,progress_mode=1,
            phase_goal=schema.Pose(x=1,y=1.6,yaw=0),stop_wait=False,stop_wait_started_pi_us=0)
        progress=schema.EpisodeProgress(used_travel_ticks=0,last_progress_pi_us=0,
            vehicle_sequence=meter.vehicle_sequence,station_lower_m=0,remaining_upper_m=1,revision=1)
    else:
        core=schema.EpisodeCore();core.CopyFrom(old.core)
        core.revision+=1;core.phase=phase;core.phase_revision=phase_revision
        core.state=1 if action==3 else 2
        progress=schema.EpisodeProgress();progress.CopyFrom(old.progress);progress.revision+=1
    current=w.o.missions.current();guest=guest_now(w)
    request=schema.EpisodeRequest(major=1,request_nonce=uuid4().bytes,request_sequence=sequence,action=action,
        vm=w.peer[0],execution=w.peer[1],app_instance=w.peer[2],control_generation=w.w.m.generation,
        mission_id=UUID(current['id']).bytes,mission_revision=current['revision'],
        expected_grant_revision=current['grant_revision'],cancel_fence_revision=current['cancel_fence'],
        scene_admission_id=bytes(16) if current['scene_id'] is None else UUID(current['scene_id']).bytes,
        scene_revision=current['scene_revision'],source=source,candidate=core,progress=progress,
        completed_guest_us=guest,began_guest_us=guest if action==1 else 0,phase_started_guest_us=guest,
        valid_until_pi_us=w.o.now+100000,clock_probe_sequence=w.clock_sequence,
        clock_reply_sha256=sha256(w.clock_raw).digest(),vehicle_sequence=meter.vehicle_sequence,
        expected_registry_epoch=owner.epoch,expected_registry_revision=owner.revision,
        expected_episode_id=old.core.episode_id,expected_episode_revision=old.core.revision,
        phase_state_message_id=UUID(body.message_id).bytes,plan_revision=body.path_revision,
        phase_revision=body.maneuver_revision,calculation_sequence=watermark,plan_sha256=plan_sha256(body))
    return request,body


def register(w,request):
    from xenova_drive_world import episode_request_body
    link=schema.DriveLink(major=2,episode_request=request);wire=encode(link)
    assert w.r.drive_link.receive(w.peer[0],decode(wire,schema.DriveLink),w.w.t,episode_request_body(wire))
    return decode(w.o.episodes.last_reply,schema.EpisodeReceipt)


def ready_episode(w):
    activate(w);confirm_clock(w)
    assert accept(w,state(w))
    request,body=proposal(w);reply=register(w,request);assert reply.result==1
    body.episode.CopyFrom(reply.snapshot);assert accept(w,body)
    return request,body,reply


def journal(w,changes):
    """동일 actor의 실제 기존 worker/Authority ACK까지 기다린다. 차량 시각은 재발급하지 않는다."""
    import time
    result=[]
    assert w.o._proposal('b08-fixture:'+str(uuid4()),changes,'b08-fixture',w.o.now+1000000,
        lambda ok,unknown:result.append((ok,unknown)))
    deadline=time.monotonic()+3
    while not result:
        assert time.monotonic()<deadline,'actual actor journal ACK did not settle'
        w.o.tick();time.sleep(.001)
    assert result==[(True,False)]


def parking(w,*,maximum_labels=False,maximum_counters=False):
    """Scene의 물리 승인만 명시 typed precondition. 임무 발급과 저장은 실제 issuer다."""
    activate(w)
    if maximum_labels:
        # A legal-size software profile, not an installed physical calibration claim.
        trust=w.r.drive_link.trust;trust.vehicle='v'*64;trust.configuration='c'*64;trust.calibration='a'*64
        w.sink.vehicle.vehicle=trust.vehicle;w.sink.vehicle.calibration=trust.calibration
        w.sink.odometry.absolute.config.calibration=trust.calibration
        w.advance_meter();w.advance_meter()  # New scale/calibration epoch, then actual continuous pair.
    ref=SimpleNamespace(admission_id=uuid4().bytes,admission_revision=(1<<63)-2 if maximum_counters else 1)
    w.r.scene_runtime=SimpleNamespace(owner=None,motion_grant=lambda now:SimpleNamespace(scene=ref),
        mission_allowed=lambda grant,now:grant.scene_admission_id==ref.admission_id)
    call=w.call('parking.start',dict(strategy='REVERSE'))
    record=w.o.missions.start(call,2,admitted=True)
    if maximum_counters:
        # Codec의 U63 경계에 있는 기존 issuer record라는 명시 시험 전제다.
        # 실제 fresh start가 이 counters를 발급했다거나 임의 padding이 있다고 주장하지 않는다.
        record.update(revision=(1<<63)-2,grant_revision=(1<<63)-4,cancel_fence=(1<<63)-2)
    journal(w,dict(current_mission=record))
    assert w.o.missions.publish();confirm_clock(w)
    w.o.missions.observe_vehicle(w.view(),.000001,w.o.now)
    return w


def parking_state(w,phase=2,*,revision=1,calculation=1):
    body=state(w,revision=revision,calculation=calculation)
    body.mission.CopyFrom(mission_execution(w,phase=phase))
    body.mission.slot_id=1;body.mission.slot_revision=1;body.mission.goal_revision=1
    body.mission.goal.CopyFrom(schema.Pose(x=1,y=.5,yaw=1.57079632679))
    body.mission.selected_slot_polygon.extend(schema.Point(x=x,y=y) for x,y in
        ((.7,.1),(1.3,.1),(1.3,.9),(.7,.9)))
    body.mission.goal_position_bound_m=.01;body.mission.goal_heading_bound_rad=.01
    return body


def budget(w,body,*,action=1,nonce=None,raw_suffix=b''):
    from hashlib import sha256
    from xenova_drive_world import plan_sha256,selected_goal_sha256
    from xenova_gateway.mission_link import BudgetEnvelope
    grant=w.o.missions.grant()
    request=schema.MissionBudgetRequest(major=1,request_nonce=nonce or uuid4().bytes,
        mission_id=grant.mission_id,mission_revision=grant.mission_revision,
        control_generation=grant.control_generation,expected_grant_revision=grant.grant_revision,
        action=action,slot_id=body.mission.slot_id,slot_revision=body.mission.slot_revision,
        goal_revision=body.mission.goal_revision,plan_revision=body.path_revision,
        plan_sha256=plan_sha256(body),selected_goal_sha256=selected_goal_sha256(body.mission),
        scene_admission_id=grant.scene_admission_id,scene_revision=grant.scene_revision,
        completed_guest_us=guest_now(w),valid_until_pi_us=w.o.now+100000)
    raw=encode(request)+raw_suffix
    envelope=BudgetEnvelope(*w.peer,w.w.m.generation,raw,request.request_nonce,sha256(raw).digest(),
        w.o.now,request.valid_until_pi_us)
    return request,envelope
