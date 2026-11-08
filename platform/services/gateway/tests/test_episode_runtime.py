"""실제 Gateway actor·원 clock·State admission 회귀. 입력 기하는 typed software leaf다.

C planner/실물 정지·주행 성능의 증거로 이 시험을 계산하지 않는다.
"""
from copy import deepcopy
from uuid import UUID,uuid4

import pytest
from xenova_drive_world import schema,encode,decode,plan_sha256
from xenova_gateway.episode import EpisodeOwner
from test_operator_runtime import world
from b08_fixture import activate,confirm_clock,guest_now,state,accept,proposal,register,ready_episode


def test_initial_none_requires_approved_peer_and_actual_follow_full_state(world):
    w=activate(world);owner=w.o.episodes
    owner.declare(w.peer,0);assert owner.status(w.peer) is None
    owner.declare(w.peer,16);w.r.episode_support=False;assert owner.status(w.peer) is None
    w.r.episode_support=True;assert owner.status(w.peer).commit_state==1
    hidden=state(w,task=True)
    assert not accept(w,hidden) and w.r.drive_link.reasons['STATE_EPISODE_CONTEXT']==1
    assert accept(w,state(w)) and owner.status(w.peer).commit_state==2
    restarted=EpisodeOwner(w.o);restarted.declare(w.peer,16);restarted.sync_mission()
    assert restarted.status(w.peer).state==0 and restarted.registry is None


@pytest.mark.parametrize('gate',['absent','zero','partial'])
def test_initial_follow_state_preserves_optional_direction_gate_contract(world,gate):
    from xenova_gateway.drive_link import state_context
    w=activate(world);body=state(w)
    if gate!='zero':body.ClearField('direction_gate')
    if gate=='partial':
        body.direction_gate.state=0
        with pytest.raises(ValueError,match='required fields missing'):accept(w,body)
        assert w.o.episodes.status(w.peer).commit_state==1
        return
    original=encode(body)
    # FOLLOW에는 gate가 없을 수 있다. context 계산이 원문에 zero gate를 만들지 않는다.
    state_context(body)
    assert body.HasField('direction_gate')==(gate=='zero') and encode(body)==original
    assert accept(w,body) and w.o.episodes.status(w.peer).commit_state==2


def test_selected_new_profile_cannot_downgrade_after_capability_or_binding_loss(world):
    w=world;_,body,_=ready_episode(w);owner=w.o.episodes
    command=dict(calculation_sequence=body.calculation_sequence+1,
        plan_revision=body.path_revision,phase_revision=body.maneuver_revision)
    assert owner.command_allowed(w.peer,command)
    owner.declare(w.peer,0)
    assert owner.required(w.peer) and not owner.supported(w.peer)
    assert not owner.command_allowed(w.peer,command) and owner.status(w.peer) is None
    replacement=(w.peer[0],str(uuid4()),str(uuid4()))
    assert owner.required(replacement) and not owner.command_allowed(replacement,command)
    assert w.w.m.episode_output_evidence_required
    owner.declare(w.peer,31)
    assert not owner.command_allowed(w.peer,command)  # prior own/copy admission was withdrawn
    body.message_id=str(uuid4());body.state_revision+=1;body.calculation_sequence+=1
    assert accept(w,body)
    command['calculation_sequence']=body.calculation_sequence+1
    assert owner.command_allowed(w.peer,command)
    assert owner.registry['snapshot'].core.deadline_pi_us==body.episode.core.deadline_pi_us


def test_registration_receipt_precedes_full_state_and_first_fresh_command(world):
    w=activate(world);confirm_clock(w);owner=w.o.episodes
    request,_=proposal(w);assert register(w,request).result==2  # no first NONE State yet
    assert accept(w,state(w))
    request,body=proposal(w,sequence=2);reply=register(w,request)
    assert reply.result==1 and owner.status(w.peer).commit_state==1
    command=dict(calculation_sequence=4,plan_revision=body.path_revision,phase_revision=body.maneuver_revision)
    assert not owner.command_allowed(w.peer,command)
    body.episode.CopyFrom(reply.snapshot)
    original=body.calculation_sequence;body.calculation_sequence=request.calculation_sequence
    assert not accept(w,body)
    body.calculation_sequence=original;assert accept(w,body)
    command['calculation_sequence']=original;assert not owner.command_allowed(w.peer,command)
    command['calculation_sequence']=original+1;assert owner.command_allowed(w.peer,command)


def test_original_raw_request_reply_survives_expiry_without_new_registration(world):
    w=world;request,_,reply=ready_episode(w);owner=w.o.episodes
    original=encode(reply);revision=owner.revision
    # Retry is evidence lookup even after current request E. It cannot grant new authority.
    w.advance_meter(120000)
    assert encode(register(w,request))==original and owner.revision==revision
    conflict=schema.EpisodeRequest();conflict.CopyFrom(request);conflict.plan_revision+=1
    changed=register(w,conflict)
    assert (changed.result,changed.reason)==(2,8) and owner.revision==revision
    assert encode(register(w,request))==original


def test_original_birth_and_ingress_travel_are_not_restarted_at_request_arrival(world):
    w=activate(world);confirm_clock(w);assert accept(w,state(w))
    request,body=proposal(w);birth=w.o.episodes._meter()
    w.advance_meter(20000,left=10000,right=-10000)
    w.advance_meter(20000,left=-10000,right=10000)
    request.completed_guest_us=guest_now(w);request.vehicle_sequence=w.sink.vehicle.sequence
    reply=register(w,request);assert reply.result==1
    core=reply.snapshot.core;interval=w.o.clocks.interval(w.peer,w.clock_sequence,request.began_guest_us,w.o.now,w.w.m.generation)
    assert core.started_pi_us==interval[0] and core.deadline_pi_us==interval[0]+40000000
    assert core.started_pi_us<w.o.now and core.origin_vehicle_sequence==birth.vehicle_sequence
    assert core.meter_baseline_total_ticks==birth.total_ticks and reply.snapshot.progress.used_travel_ticks==20000
    assert core.left_baseline_ticks==birth.left_ticks and core.right_baseline_ticks==birth.right_ticks
    assert w.o.episodes.exhausted() is None


@pytest.mark.parametrize('change',['id','plan','frame','basis'])
def test_first_state_must_match_original_registered_geometry(world,change):
    w=activate(world);confirm_clock(w);assert accept(w,state(w))
    request,body=proposal(w);reply=register(w,request);assert reply.result==1
    body.episode.CopyFrom(reply.snapshot)
    if change=='id':body.message_id=str(uuid4())
    elif change=='plan':body.path[0].pose.x+=.01
    elif change=='frame':body.frame_epoch=str(uuid4())
    else:body.task_reference.basis_prefix[1].x+=.01
    if change=='basis':
        # 원 core의 prefix SHA와 모순인 body는 송신 codec에서 이미 거절된다.
        # 이 앞단 거절을 actor 수신 성공/실패로 재해석하거나 검사를 풀지 않는다.
        with pytest.raises(ValueError,match='episode original task/basis/phase'):accept(w,body)
    else:assert not accept(w,body)
    assert w.o.episodes.status(w.peer).commit_state==1


def test_normal_reference_append_preserves_plan_sha_and_receipt_core(world):
    w=activate(world);confirm_clock(w);assert accept(w,state(w))
    request,body=proposal(w);reply=register(w,request);assert reply.result==1
    original=plan_sha256(body);body.episode.CopyFrom(reply.snapshot);body.reference_revision+=1
    assert plan_sha256(body)==original and accept(w,body)
    assert encode(w.o.episodes.registry['snapshot'].core)==encode(reply.snapshot.core)


def test_same_phase_replan_cannot_refund_progress_clock_or_original_budget(world):
    w=world;_,_,first=ready_episode(w);old=schema.EpisodeSnapshot();old.CopyFrom(first.snapshot)
    w.advance_meter(20000)
    request,body=proposal(w,action=2,phase=1,phase_revision=1,sequence=2,plan_revision=6)
    receipt=register(w,request);assert receipt.result==1
    current=receipt.snapshot
    for key in ('started_pi_us','deadline_pi_us','phase_started_pi_us','phase_deadline_pi_us',
                'meter_epoch','meter_baseline_total_ticks','max_travel_m'):
        assert getattr(current.core,key)==getattr(old.core,key)
    assert current.progress.last_progress_pi_us==old.progress.last_progress_pi_us
    assert current.core.phase_goal==old.core.phase_goal and current.core.revision==old.core.revision+1
    # A second transition cannot skip the outstanding full State acknowledgement.
    second,_=proposal(w,action=2,phase=2,phase_revision=2,sequence=3,plan_revision=7)
    assert register(w,second).result==2
    body.episode.CopyFrom(current);assert accept(w,body)


def test_meaningful_progress_uses_original_samples_without_registration_per_pose(world):
    from xenova_gateway.drive_link import state_context
    w=world;_,body,_=ready_episode(w);owner=w.o.episodes;registry_revision=owner.revision
    original_context=state_context(body)
    original=owner.registry['snapshot'].progress.last_progress_pi_us
    for ordinal,station in ((1,.01),(2,.02)):
        w.advance_meter(20000,left=10000,right=10000)
        body.message_id=str(uuid4());body.state_revision+=1;body.calculation_sequence+=1
        body.vehicle_sequence=w.sink.vehicle.sequence;body.episode.CopyFrom(owner.registry['snapshot'])
        progress=body.episode.progress;progress.used_travel_ticks=ordinal*10000
        progress.vehicle_sequence=body.vehicle_sequence;progress.revision+=1;progress.station_lower_m=station
        if ordinal==2:progress.last_progress_pi_us=w.o.now
        assert accept(w,body)
        assert state_context(body)==original_context
    assert owner.revision==registry_revision and owner.registry['snapshot'].progress.last_progress_pi_us>original
    old=schema.EpisodeSnapshot();old.CopyFrom(owner.registry['snapshot'])
    body.message_id=str(uuid4());body.state_revision+=1;body.calculation_sequence+=1
    body.episode.progress.last_progress_pi_us+=1  # same pose/Vehicle is no new progress
    assert not accept(w,body) and owner.registry['snapshot']==old


@pytest.mark.parametrize('fault,reason',[('distance','EPISODE_TRAVEL'),('gap','EPISODE_METER_GAP'),
    ('progress','EPISODE_PROGRESS'),('phase','EPISODE_PHASE_DEADLINE'),('whole','EPISODE_DEADLINE')])
def test_original_caps_do_not_depend_on_pose_endpoint_or_latest_actor_delta(world,fault,reason):
    w=world;ready_episode(w);owner=w.o.episodes
    if fault=='distance':w.advance_meter(20000,left=6000000,right=-6000000)
    elif fault=='gap':w.advance_meter(250000)
    else:
        # Time alone is the independent fault under examination. Preserve valid input
        # continuity explicitly rather than replacing a gap with a zero-distance anchor.
        target=(owner.registry['snapshot'].progress.last_progress_pi_us or owner.registry['snapshot'].core.phase_started_pi_us)+2000000 if fault=='progress' else (
            owner.registry['snapshot'].core.phase_deadline_pi_us if fault=='phase' else owner.registry['snapshot'].core.deadline_pi_us)
        while w.o.now<target:w.advance_meter(min(100000,target-w.o.now))
    assert owner.exhausted()==reason
    owner.tick();assert owner.failed and w.o.hold
    current=owner.registry['snapshot'];assert current.core.episode_id!=bytes(16)
    owner.sync_mission();assert owner.registry['snapshot']==current


def test_owner_rebind_keeps_original_core_and_cannot_create_none(world):
    w=world;_,_,receipt=ready_episode(w);owner=w.o.episodes;current=deepcopy(w.o.missions.current())
    rebound=w.o.missions.rebind(current,w.w.m.generation+1,w.peer[0])
    # This unit boundary applies an issuer record as a fixture, not a live handover proof.
    w.w.auth.value['current_mission']=rebound;owner.sync_mission()
    assert encode(owner.registry['snapshot'].core)==encode(receipt.snapshot.core)
    assert owner.registry['snapshot'].acceptance.origin_generation==receipt.snapshot.acceptance.origin_generation
    assert owner.registry['snapshot'].core.deadline_pi_us==receipt.snapshot.core.deadline_pi_us


def test_finish_retains_terminal_core_and_next_begin_requires_closed_predecessor(world):
    w=world;_,_,first=ready_episode(w);owner=w.o.episodes
    for phase in (2,3,4,5):
        request,body=proposal(w,action=3 if phase==5 else 2,phase=phase,
            phase_revision=phase,sequence=phase,plan_revision=phase+4)
        receipt=register(w,request);assert receipt.result==1
        body.episode.CopyFrom(receipt.snapshot);assert accept(w,body)
        assert receipt.snapshot.core.deadline_pi_us==first.snapshot.core.deadline_pi_us
    assert owner.terminal is not None and owner.registry['snapshot'].core.state==1
    terminal=encode(owner.terminal['snapshot']);closed=owner.registry['snapshot'].core.episode_id
    follow=state(w,revision=20,calculation=20,path_revision=20)
    assert accept(w,follow)  # normal FOLLOW can have a new current path and phase
    assert encode(owner.terminal['snapshot'])==terminal
    request,body=proposal(w,sequence=6,plan_revision=21)
    assert request.expected_episode_id==closed and request.expected_episode_revision==5
    receipt=register(w,request);assert receipt.result==1
    assert receipt.snapshot.core.episode_id!=closed and owner.registry['snapshot'].core.revision==1
    body.episode.CopyFrom(receipt.snapshot);assert accept(w,body)
    assert owner.output_deadline()==receipt.snapshot.core.phase_deadline_pi_us
