"""원 주차칸/경로 예약·내구 reply·보수 거리. Scene/기하는 명시 software leaf다."""
import base64
from copy import deepcopy
from uuid import uuid4,UUID

import pytest
from xenova_drive_world import schema,encode,decode
from xenova_gateway.authority_journal import canonical
from xenova_gateway.mission_authority import validate_record
from xenova_gateway.mission_link import BudgetEnvelope
from test_operator_runtime import world
from test_verified import B
from b08_fixture import parking,parking_state,accept,budget,journal


def reserve(w,body,action=1):
    assert accept(w,body)
    request,envelope=budget(w,body,action=action)
    result,record=w.o.missions.budget(envelope,w.o.now,True)
    assert result.result==1,(result,w.o.missions.distance_valid,w.o.missions.execution)
    journal(w,dict(current_mission=record));assert w.o.missions.publish()
    return request,envelope,result,record


def test_original_lock_and_two_corrections_fit_same_4k_record_with_exact_reply(world):
    w=parking(world);authority=w.o.missions;replies=[];records=[]
    for ordinal,phase in enumerate((2,7,7),1):
        body=parking_state(w,phase,revision=ordinal,calculation=ordinal)
        _,envelope,result,record=reserve(w,body,1 if ordinal==1 else 2)
        replies.append((envelope,encode(result)));records.append(record)
        validate_record(record);assert len(canonical(record))<=4096
        assert base64.b64decode(record['reservations'][-1]['reply'])==encode(result)
    assert records[-1]['corrections']==2 and len(records[-1]['reservations'])==3
    assert len({record['execution_deadline'] for record in records})==1
    assert all(record['execution_meter']==records[0]['execution_meter'] for record in records)
    assert all(record['goal']==records[0]['goal'] for record in records)
    for envelope,original in replies:
        repeated,changed=authority.budget(envelope,w.o.now+999999999,False)
        assert changed is None and encode(repeated)==original


def test_maximum_legal_labels_keep_three_original_replies_within_4k(world):
    w=parking(world,maximum_labels=True);replies=[]
    for ordinal,phase in enumerate((2,7,7),1):
        _,envelope,result,record=reserve(w,parking_state(w,phase,revision=ordinal,calculation=ordinal),
            1 if ordinal==1 else 2)
        validate_record(record);assert len(canonical(record))<=4096
        replies.append((envelope,encode(result)))
    assert len(w.o.missions.current()['reservations'])==3
    for envelope,raw in replies:
        result,changed=w.o.missions.budget(envelope,w.o.now+999999999,False)
        assert changed is None and encode(result)==raw


def test_history_full_does_not_consume_nonce_or_silently_expand_4k(world):
    w=parking(world,maximum_labels=True,maximum_counters=True);accepted=0;rejected=None
    for ordinal,phase in enumerate((2,7,7),1):
        body=parking_state(w,phase,revision=ordinal,calculation=ordinal);assert accept(w,body)
        _,attempt=budget(w,body,action=1 if ordinal==1 else 2);before=canonical(w.o.missions.current())
        result,changed=w.o.missions.budget(attempt,w.o.now,True)
        if changed is None:
            rejected=result
            assert (result.result,result.reason)==(5,10)
            assert canonical(w.o.missions.current())==before
            assert all(row['nonce']!=attempt.request_nonce.hex() for row in w.o.missions.current()['reservations'])
            break
        accepted+=1;assert result.result==1 and len(canonical(changed))<=4096
        journal(w,dict(current_mission=changed));assert w.o.missions.publish()
    assert accepted>=1 and rejected is not None


@pytest.mark.parametrize('mutation',['no_state','phase','plan','goal','slot','moving','expired'])
def test_reservation_checks_actual_full_state_goal_and_original_validity(world,mutation):
    w=parking(world);body=parking_state(w,1 if mutation=='phase' else 2)
    if mutation!='no_state':assert accept(w,body)
    request,envelope=budget(w,body)
    if mutation in ('plan','goal','slot'):
        if mutation=='plan':request.plan_sha256=b'c'*32
        elif mutation=='goal':request.selected_goal_sha256=b'd'*32
        else:request.slot_id+=1
        from hashlib import sha256
        raw=encode(request);envelope=BudgetEnvelope(*w.peer,w.w.m.generation,raw,request.request_nonce,
            sha256(raw).digest(),w.o.now,request.valid_until_pi_us)
    now=request.valid_until_pi_us if mutation=='expired' else w.o.now
    result,record=w.o.missions.budget(envelope,now,mutation!='moving')
    assert result.result==5 and record is None and not w.o.missions.current()['reservations']


def test_old_reservation_reply_after_owner_rebind_is_evidence_not_current_permission(world):
    w=parking(world);body=parking_state(w);_,envelope,reply,record=reserve(w,body)
    original=encode(reply);rebound=w.o.missions.rebind(record,w.w.m.generation+1,B)
    # Authority replacement is a typed unit premise; no live A/B transition is claimed here.
    w.w.auth.value['current_mission']=rebound
    result,change=w.o.missions.budget(envelope,w.o.now+999999999,False)
    assert change is None and encode(result)==original
    assert result.grant.control_generation==record['generation']
    assert result.grant.owner_vm==UUID(record['owner']).bytes
    assert w.o.missions.current()['grant_revision']!=result.grant.grant_revision
    assert rebound['execution_deadline']==record['execution_deadline'] and rebound['execution_meter']==record['execution_meter']


def test_parking_distance_uses_all_wheel_samples_and_gap_is_unknown_not_zero(world):
    w=parking(world);authority=w.o.missions;birth=deepcopy(authority.current()['search_meter'])
    w.advance_meter(20000,left=100000,right=-100000);w.advance_meter(20000,left=-100000,right=100000)
    authority.observe_vehicle(w.view(),.000001,w.o.now)
    assert authority.used_distance()==pytest.approx(.2) and authority.current()['search_meter']==birth
    w.advance_meter(250000);authority.observe_vehicle(w.view(),.000001,w.o.now)
    assert authority.used_distance() is None and authority.exhausted(w.o.now)=='PARKING_METER_UNAVAILABLE'


def test_locked_execution_uses_original_30s_instead_of_completed_search_cutoff(world):
    w=parking(world);authority=w.o.missions;current=deepcopy(authority.current())
    now=w.o.now;current.update(issued=now-89000000,deadline=now+31000000,
        search_deadline=now+1000000,execution_deadline=now+30000000,goal='a'*64,
        phase='EXECUTE',execution_meter=deepcopy(current['search_meter']))
    # Issuer-clock boundary is explicit; no new lock/E is minted by this assertion.
    w.w.auth.value['current_mission']=current
    assert authority.output_deadline()==now+30000000
    assert authority.exhausted(now+1000000) is None
    assert authority.exhausted(now+30000000)=='EXECUTION_DEADLINE'


def test_mutable_travel_does_not_create_new_phase_but_goal_change_does(world):
    from xenova_gateway.drive_link import state_context
    w=parking(world);body=parking_state(w);original=state_context(body)
    body.mission.search_travel_m=.1;body.mission.execution_travel_m=.2
    assert state_context(body)==original
    body.mission.goal.x+=.01
    assert state_context(body)!=original


@pytest.mark.parametrize('stationary',[False,True])
def test_parked_terminal_requires_current_original_stop_fact_and_durable_hold(world,monkeypatch,stationary):
    w=parking(world);_,_,_,record=reserve(w,parking_state(w));body=parking_state(w,0,revision=2,calculation=2)
    body.mission.result=1;body.direction_gate.stop_command_sequence=41
    # Physical stationary facts are typed here; actual producer is tested separately in PTY.
    facts=SimpleStop(w,body,stationary)
    monkeypatch.setattr(w.r,'control_facts',lambda:facts)
    admitted=accept(w,body)
    assert admitted is stationary
    if not stationary:
        assert w.o.missions.terminal_pending is None and w.o.missions.current()['admitted']
        return
    assert w.o.hold and w.o.missions.current()['admitted']
    pending=deepcopy(w.o.missions.terminal_pending)
    assert pending['phase']=='HOLD' and pending['result']=='PARKED' and not pending['admitted']
    # Existing actor journal persists termination; no new daemon/DB is involved.
    import time
    end=time.monotonic()+3
    while w.o.missions.terminal_pending is not None:
        assert time.monotonic()<end;w.o.tick();time.sleep(.001)
    final=w.o.missions.current()
    assert final['phase']=='HOLD' and final['result']=='PARKED' and not final['admitted']
    assert final['execution_deadline']==record['execution_deadline'] and final['execution_meter']==record['execution_meter']


def SimpleStop(w,body,stationary):
    """Only a MissionAuthority input leaf; this function does not claim measured STOP."""
    facts=schema.DriveControlFacts()
    facts.stop.state=3 if stationary else 2;facts.stop.command_sequence=41
    facts.stop.valid_until_pi_us=w.o.now+100000;facts.stop.control_generation=w.w.m.generation
    facts.stop.producer_role=1;facts.stop.source_vm=UUID(w.peer[0]).bytes
    facts.stop.source_execution=UUID(w.peer[1]).bytes;facts.stop.source_app_instance=UUID(w.peer[2]).bytes
    facts.stop.mission_id=body.mission.mission_id;facts.stop.mission_revision=body.mission.mission_revision
    facts.stop.grant_revision=body.mission.grant_revision;facts.stop.plan_revision=body.path_revision
    facts.stop.phase_revision=body.maneuver_revision
    return facts
