"""실제 shared C/Python의 episode hash·wire 경계. Nano 묶음용이며 물리 수락이 아니다."""
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest
from xenova_drive_world import (schema,encode,decode,validate,initial_none_core,initial_none_snapshot,
    episode_core_sha256,episode_source_sha256,plan_sha256,selected_goal_sha256,
    basis_prefix_sha256,budget_request_body)
from test_world import parking_state
from test_input_clock import reply

ID='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
NIL=bytes(16)
ZERO=bytes(32)


@pytest.fixture(scope='module')
def native_probe(tmp_path_factory):
    home=Path(__file__).resolve().parents[1];build=tmp_path_factory.mktemp('episode-wire');binary=build/'episode-probe'
    sources=[home/'world.c',home/'generated/world.pb.c',*(home/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c')),home/'tests/episode_probe.c']
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined',
        '-I'+str(home),'-I'+str(home/'nanopb'),*[str(p) for p in sources],'-lm','-o',str(binary)],
        check=True,capture_output=True,text=True,timeout=30)
    def check(kind,message,*,unchecked=False):
        wire=message.SerializeToString(deterministic=True) if unchecked else encode(message)
        path=build/'input.pb';path.write_bytes(wire)
        result=subprocess.run([str(binary),kind,str(path)],check=True,capture_output=True,text=True,timeout=5)
        assert not result.stderr
        return result.stdout.strip()
    return check


def status():
    return schema.EpisodeStatus(major=1,state=1,registry_epoch=b'r'*16,registry_revision=1,
        accepted_core_sha256=episode_core_sha256(initial_none_core()),episode_id=NIL,episode_revision=0,
        commit_state=1,state_message_id=NIL,state_sha256=ZERO,state_revision=0,plan_revision=0,
        phase_revision=0,calculation_hwm=0,vehicle_hwm=0,valid_until_pi_us=2000000)


def task_state():
    state=parking_state();state.state_features=15;state.reference_revision=9
    task=state.task_reference
    task.home_lane_id=task.from_lane_id=task.selected_lane_id=1;task.changing=False;task.basis_revision=3
    for x in (0,1,2):task.basis_prefix.add(x=x,y=0)
    task.vehicle='rc-test-1';task.mapper_boot=task.mapper_instance=task.clock_epoch=ID
    return state


@pytest.mark.parametrize('kind',['core','source','plan','goal'])
def test_c_and_python_use_the_same_actual_fingerprint_scope(native_probe,kind):
    state=task_state()
    if kind=='core':value=initial_none_core();digest=episode_core_sha256(value)
    elif kind=='source':
        value=schema.EpisodeSourceContext(vehicle='rc-test-1',mapper_boot=ID,mapper_instance=ID,
            map_epoch=ID,frame_epoch=ID,odom_epoch=ID,clock_epoch=ID,configuration='lattice-lab-1',
            calibration='encoder-lab-1',primitive=b'p'*32,reference_id='road',reference_revision=9,
            world_sequence=4,observation_sequence=3)
        digest=episode_source_sha256(value)
    elif kind=='plan':value=state;digest=plan_sha256(value)
    else:value=state.mission;digest=selected_goal_sha256(value)
    assert native_probe(kind,value)==digest.hex()


def test_append_witness_does_not_rekey_plan_but_changed_geometry_or_basis_does(native_probe):
    state=task_state();original=plan_sha256(state)
    state.reference_revision+=1;state.observation_seq+=1;state.vehicle_sequence+=1;state.calculation_sequence+=1
    assert native_probe('plan',state)==plan_sha256(state).hex()==original.hex()
    state.task_reference.basis_revision+=1
    assert native_probe('plan',state)==plan_sha256(state).hex()!=original.hex()
    state=task_state();state.path[0].pose.y+=.01
    assert native_probe('plan',state)==plan_sha256(state).hex()!=original.hex()
    state=task_state();state.frame_epoch='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    assert native_probe('plan',state)==plan_sha256(state).hex()!=original.hex()


def test_initial_none_stamp_is_explicit_and_never_a_success_receipt():
    current=status();snapshot=initial_none_snapshot(current)
    assert snapshot.acceptance.request_nonce==NIL and snapshot.acceptance.request_sha256==ZERO
    assert snapshot.acceptance.accepted_core_sha256==episode_core_sha256(snapshot.core)
    current.state=0
    with pytest.raises(ValueError):initial_none_snapshot(current)
    current=status();current.accepted_core_sha256=b'x'*32
    with pytest.raises(ValueError):initial_none_snapshot(current)


def test_feature_presence_and_exact_core_hash_do_not_accept_silent_none():
    state=task_state();state.episode.CopyFrom(initial_none_snapshot(status()))
    with pytest.raises(ValueError,match='feature'):encode(state)
    state.state_features|=16;assert decode(encode(state),schema.DriveState)==state
    state.episode.acceptance.accepted_core_sha256=b'x'*32
    with pytest.raises(ValueError,match='SHA'):encode(state)


def test_selected_goal_and_basis_hash_are_sensitive_to_the_original_values():
    state=task_state();goal=selected_goal_sha256(state.mission)
    state.mission.goal_revision+=1;assert selected_goal_sha256(state.mission)!=goal
    state=task_state();goal=selected_goal_sha256(state.mission)
    state.mission.selected_slot_polygon[0].x+=.01;assert selected_goal_sha256(state.mission)!=goal
    basis=basis_prefix_sha256(state.task_reference.basis_prefix)
    state.task_reference.basis_prefix[0].y+=.01;assert basis_prefix_sha256(state.task_reference.basis_prefix)!=basis


def test_original_budget_body_is_not_reserialized_before_sha():
    request=schema.MissionBudgetRequest(major=1,request_nonce=b'n'*16,mission_id=b'm'*16,mission_revision=1,
        control_generation=2,expected_grant_revision=3,action=1,slot_id=1,slot_revision=1,goal_revision=1,
        plan_revision=4,plan_sha256=b'p'*32,selected_goal_sha256=b'g'*32,scene_admission_id=NIL,
        scene_revision=0,completed_guest_us=1000000,valid_until_pi_us=1200000)
    # Legal unknown outer data must not be included in the request-body fingerprint.
    link=schema.DriveLink(major=2,budget_request=request);body=encode(request)
    assert budget_request_body(encode(link)+bytes.fromhex('a00601'))==body
    assert sha256(budget_request_body(encode(link))).digest()==sha256(body).digest()


@pytest.mark.parametrize('fault',['none','sequence','scope','scale','undeclared'])
def test_meter_facts_keep_exact_parent_scope_and_declared_support(native_probe,fault):
    link=reply();body=link.clock;body.probe.supported_state_features=16
    body.vehicle.gateway_instance=body.gateway_instance;body.vehicle.pi_session=ID
    facts=body.control_facts;facts.major=1;facts.valid_until_pi_us=6100000
    facts.meter.CopyFrom(schema.PiAbsoluteMeter(meter_epoch=b'm'*16,total_ticks=100,
        metres_per_tick=.0001,gap_revision=0,valid=True,vehicle_sequence=body.vehicle.sequence))
    if fault=='sequence':facts.meter.vehicle_sequence+=1
    if fault=='scope':body.vehicle.host_boot=ID
    if fault=='scale':facts.meter.metres_per_tick=0
    if fault=='undeclared':body.probe.ClearField('supported_state_features')
    if fault=='none':assert native_probe('link',link)=='OK'
    else:
        with pytest.raises(ValueError):validate(link)
        assert native_probe('link',link,unchecked=True)=='REJECT'
