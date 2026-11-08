"""Python producer -> real nanopb product decoder; independent pose expectations."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

HOME=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOME/'world/python'))
from world_codec import schema,encode,decode
spec=importlib.util.spec_from_file_location('owned_world_fixture',HOME/'examples/world_fixture.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)

@pytest.fixture(scope='module')
def probe(tmp_path_factory):
    build=tmp_path_factory.mktemp('world-native');binary=build/'world-probe'
    world=HOME/'world'
    sources=[world/'world.c',world/'generated/world.pb.c',*(world/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c')),HOME/'tests/world_probe.c']
    args=['cc','-std=c11','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-DTEST_PRIMITIVE_SHA="'+fixture.PRIMITIVE+'"',
        '-I'+str(world),'-I'+str(world/'nanopb'),'-I'+str(world/'generated'),*[str(p) for p in sources],'-lm','-o',str(binary)]
    subprocess.run(args,check=True,capture_output=True,text=True,timeout=30)
    return binary,build

def product(probe,data,kind=None):
    binary,build=probe;path=build/'input.bin';path.write_bytes(data)
    result=subprocess.run([str(binary),str(path),*([kind] if kind else [])],check=True,capture_output=True,text=True,timeout=5)
    return json.loads(result.stdout)

def test_normal_and_maximum_python_to_nanopb_and_pool_pose(probe):
    for height in (32,64):
        value=fixture.world(height=height)
        wire=encode(value)
        assert decode(wire)==value
        result=product(probe,wire)
        assert result['result']=='OK' and result['bytes']<32768
        assert result['pool_size']<120000 and result['odom_size']<80000

def test_frozen_protoc_golden_to_python_and_nanopb(probe):
    vector=json.loads((HOME.parents[2]/'docs/contracts/drive-world/vectors/world-4x4.json').read_text())
    wire=bytes.fromhex(vector['wire_hex'])
    import hashlib
    assert hashlib.sha256(wire).hexdigest()==vector['sha256']
    message=decode(wire)
    assert (message.width,message.height,message.sequence)==(4,4,2)
    assert message.occupancy==bytes(16) and message.cell_flags==bytes([3])*16
    assert message.age_le32==b'\x80\xee\x36\x00'*16
    assert encode(message)==wire
    # 보존한 protoc golden은 당시 primitive revision이다. 새 생성기 산출물의
    # 호환성을 임의 허용하지 않고 현재 asset 참조만 바꾼 정상 입력과 짝지어 확인한다.
    assert product(probe,wire)['result']==('OK' if message.primitive==fixture.PRIMITIVE else 'CONFIGURATION')
    message.primitive=fixture.PRIMITIVE
    assert product(probe,encode(message))['result']=='OK'
    with pytest.raises(ValueError):decode(wire+b'\x08\x01')
    assert product(probe,wire+b'\x08\x01')['result']=='DECODE'


@pytest.mark.parametrize('field,value,reason',[
    ('major',2,'VERSION'),('required_features',2,'VERSION'),('complete',False,'ARRAY'),
    ('truncated',True,'ARRAY'),('width',65,'ARRAY'),('occupancy',b'','ARRAY'),
    ('mapper_boot',fixture.INSTANCE,'SOURCE'),('frame_epoch',fixture.INSTANCE,'EPOCH'),
    ('configuration','wrong','CONFIGURATION'),('resolution_m',float('nan'),'GEOMETRY'),
    ('clock_epoch',fixture.INSTANCE,'TIME'),('published_us',4000000,'TIME')])
def test_semantic_errors_do_not_become_default_values(probe,field,value,reason):
    message=fixture.world();setattr(message,field,value)
    assert product(probe,message.SerializeToString())['result']==reason

def test_required_presence_partial_duplicate_unknown_and_oversize(probe):
    message=fixture.world();message.ClearField('height')
    assert product(probe,message.SerializePartialToString())['result']=='DECODE'
    normal=encode(fixture.world())
    assert product(probe,normal[:-7])['result']=='DECODE'
    assert product(probe,normal+b'\x08\x01')['result']=='DECODE'
    assert product(probe,b'x'*32769)['result']=='TOO_LARGE'
    # Compatible optional unknown field is ignored; mandatory semantics must
    # declare required_features and fail above.
    assert product(probe,normal+b'\xf8\x07\x01')['result']=='OK'
    # An embedded NUL must not make C's strcmp see only a trusted prefix.
    value=fixture.world();value.vehicle='rc-test-1\x00extra'
    assert product(probe,value.SerializeToString())['result']=='DECODE'

def test_producer_rejects_nonfinite_and_missing_arrays():
    value=fixture.world();value.reference[0].x=float('inf')
    with pytest.raises(ValueError):encode(value)
    value=fixture.world();value.cell_flags=b''
    with pytest.raises(ValueError):encode(value)


def test_preferred_road_feature_is_explicit_and_never_supplies_free_cells(probe):
    from xenova_drive_world import mark_road
    value=fixture.world()
    original=(value.occupancy,value.age_le32,bytes(value.cell_flags))
    left=[schema.Point(x=p.x,y=1.96) for p in value.reference]
    right=[schema.Point(x=p.x,y=1.24) for p in value.reference]
    mark_road(value,left,right)
    assert value.required_features==1 and value.minor==1
    assert value.occupancy==original[0] and value.age_le32==original[1]
    assert all((a&7)==b for a,b in zip(value.cell_flags,original[2]))
    assert product(probe,encode(value))['result']=='OK'
    value.required_features=0
    with pytest.raises(ValueError,match='cell encoding'):encode(value)
    assert product(probe,value.SerializeToString())['result']=='ARRAY'
    value.required_features=1;value.cell_flags=bytes([3])*len(value.cell_flags)
    with pytest.raises(ValueError,match='preferred road'):encode(value)
    assert product(probe,value.SerializeToString())['result']=='ARRAY'


def test_road_shape_rejects_crossing_or_wrong_side_then_accepts_recovery(probe):
    value=fixture.world();value.left_boundary[3].y=-.2
    assert product(probe,encode(value))['result']=='GEOMETRY'
    value=fixture.world();value.left_boundary[3].CopyFrom(value.left_boundary[2])
    assert product(probe,encode(value))['result']=='GEOMETRY'
    assert product(probe,encode(fixture.world()))['result']=='OK'


def modern_world():
    """독립적인 두 lane/whole-cell permission/원 localization lease 양성 입력."""
    from xenova_drive_world import mark_road
    value=fixture.world()
    mark_road(value,[schema.Point(x=p.x,y=2.4) for p in value.reference],
                    [schema.Point(x=p.x,y=.8) for p in value.reference])
    value.minor=2;value.required_features=11
    count=value.width*value.height
    value.cell_flags=bytes([9])*count;value.age_le32=struct.pack('<I',0)*count
    value.drivable_permission=bytes([255])*(count//8)
    model=value.lane_model
    model.revision=1;model.divider_position_bound_m=.001;model.divider_heading_bound_rad=.001
    for x in (0.,2.,4.,6.):model.divider.add(x=x,y=1.6)
    model.crossing=bytes([1,1,1]);model.divider_state=bytes(4)
    for identifier,y in ((1,1.2),(2,2.0)):
        lane=model.lanes.add(lane_id=identifier,travel_direction=1,position_bound_m=.001,
            heading_bound_rad=.001,revision=1,adjacent_lane_id=3-identifier,point_state=bytes(4))
        for x in (0.,2.,4.,6.):lane.centerline.add(x=x,y=y)
    value.localization.CopyFrom(schema.MapLocalization(registration_revision=1,
        anchor_position_bound_m=.001,anchor_heading_bound_rad=.001,
        geometry_position_bound_m=.001,geometry_heading_bound_rad=.001,
        observed_mapper_us=value.observed_us,valid_for_us=1000000,
        position_drift_bound_mps=.001,heading_drift_bound_rps=.001,quality=1,timestamp_error_us=10))
    return value



def parking_world(*,width=64,height=32):
    """고정 fixture다. 두 lane/slot 후보를 수락 권위로 승격하지 않는다."""
    value=fixture.world(width=width,height=height)
    value.minor=2;value.required_features=15
    n=width*height
    value.cell_flags=bytes([9])*n
    value.drivable_permission=bytes([255])*(n//8)+(bytes([(1<<(n%8))-1]) if n%8 else b'')
    model=value.lane_model
    model.revision=1;model.divider_position_bound_m=.001;model.divider_heading_bound_rad=.01
    model.crossing=b'\x01\x00';model.divider_state=bytes(3)
    for x in (0.,1.,2.):model.divider.add(x=x,y=1.6)
    for lane_id,adjacent,y in ((1,2,2.),(2,1,1.2)):
        lane=model.lanes.add(lane_id=lane_id,adjacent_lane_id=adjacent,travel_direction=1,
            position_bound_m=.001,heading_bound_rad=.01,revision=1,point_state=bytes(3))
        for x in (0.,1.,2.):lane.centerline.add(x=x,y=y)
    value.localization.CopyFrom(schema.MapLocalization(registration_revision=1,
        anchor_position_bound_m=.01,anchor_heading_bound_rad=.01,
        geometry_position_bound_m=.01,geometry_heading_bound_rad=.01,
        observed_mapper_us=1000000,valid_for_us=300000,position_drift_bound_mps=.01,
        heading_drift_bound_rps=.01,quality=1,timestamp_error_us=1000))
    slot=value.parking_slots.add(slot_id=1,geometry_revision=1,geometry_state=1,
        occupancy_state=0,entry_edge=0,position_bound_m=.01,heading_bound_rad=.01,
        geometry_age_ms=0,clearance_age_ms=0,evidence_sequence=1,geometry_complete=True,
        occupancy_reason=0,source_kinds=1,occupancy_revision=1,usable_width_m=.6,
        usable_depth_m=.6,approach_lane_id=1,goal_mask=1)
    for x,y in ((3.,2.4),(3.6,2.4),(3.6,3.),(3.,3.)):slot.corners.add(x=x,y=y)
    slot.reverse_goal.CopyFrom(schema.Pose(x=3.3,y=2.7,yaw=0.))
    value.parking_set.CopyFrom(schema.ParkingSetMeta(revision=1,complete=True,
        track_count_in_roi=1,capacity_limited=False,evidence_sequence=1,state=1))
    return value


def parking_state():
    value=schema.DriveState(major=2,source_vm=fixture.BOOT,source_execution=fixture.INSTANCE,
        source_app_instance=fixture.MAP,source_generation=1,state_revision=1,
        frame_epoch=fixture.FRAME,odom_epoch=fixture.ODOM,configuration='lattice-lab-1',
        primitive=fixture.PRIMITIVE,reference_id='straight-lab-road',reference_revision=1,
        path_revision=1,maneuver_revision=1,direction=0,maneuver_phase=0,
        vehicle_sequence=1,calculation_sequence=1,tracker_index=0,complete=True,
        message_id=fixture.CLOCK,calibration='encoder-lab-1',map_epoch=fixture.MAP,
        observation_seq=1,world_features=15,state_features=7)
    value.merge_goal.CopyFrom(schema.Pose(x=.3,y=.95,yaw=.1))
    for x,y,yaw,v in ((.2,1.,0.,.1),(.4,1.,0.,0.),(.4,1.,0.,0.),(.3,.95,.1,-.1)):
        point=value.path.add(steering_rad=0.,velocity_mps=v)
        point.pose.CopyFrom(schema.Pose(x=x,y=y,yaw=yaw))
    value.segments.add(first_index=0,last_index=1,signed_direction=1)
    value.segments.add(first_index=2,last_index=3,signed_direction=-1)
    value.mission.CopyFrom(schema.MissionExecution(mission_id=bytes([1])*16,mission_revision=1,
        kind=2,phase=6,result=0,operator_request_nonce=bytes([2])*16,slot_id=1,slot_revision=1,
        goal_revision=1,mission_deadline_pi_us=121000000,cancel_fence_revision=0,active_segment=1,
        goal_position_bound_m=.01,goal_heading_bound_rad=.01,scene_admission_id=bytes(16),
        scene_admission_revision=0,strategy=1,execution_deadline_pi_us=31000000,
        correction_attempts_used=0,execution_travel_m=.2,grant_revision=1,
        search_travel_m=.5,search_deadline_pi_us=91000000))
    value.mission.goal.CopyFrom(value.merge_goal)
    for x,y in ((.2,.7),(.8,.7),(.8,1.3),(.2,1.3)):value.mission.selected_slot_polygon.add(x=x,y=y)
    value.direction_gate.CopyFrom(schema.DirectionGateRef(gate_revision=1,
        stop_command_sequence=42,state=1,requested_direction=-1))
    return value


def lane_grant():
    return schema.MissionGrant(major=1,vehicle='rc-test-1',pi_host_boot=bytes([1])*16,
        gateway_instance=bytes([2])*16,owner_vm=bytes([3])*16,owner_execution=bytes([4])*16,
        app_instance=bytes([5])*16,control_generation=1,mission_id=bytes([6])*16,mission_revision=1,
        grant_revision=1,kind=1,strategy=0,operator_request_nonce=bytes([7])*16,issued_pi_us=1000000,
        mission_deadline_pi_us=0,cancel_fence_revision=0,scene_admission_id=bytes(16),scene_revision=0,
        configuration='lattice-lab-1',calibration='encoder-lab-1',primitive=bytes.fromhex(fixture.PRIMITIVE),
        execution_deadline_pi_us=0,correction_attempts_used=0,execution_travel_m=0.,
        max_execution_travel_m=0.,admitted=True,search_deadline_pi_us=0,
        search_travel_m=0.,max_search_travel_m=0.)


def test_new_world_roundtrip_and_permission_unused_bits(probe):
    for width,height in ((64,64),(3,3)):
        value=parking_world(width=width,height=height)
        wire=encode(value)
        assert len(wire)<=32768 and decode(wire)==value
        assert product(probe,wire)['result']=='OK'
    value.drivable_permission=value.drivable_permission[:-1]+b'\xff'
    with pytest.raises(ValueError):encode(value)
    assert product(probe,value.SerializeToString())['result']!='OK'


def test_world_age_includes_capture_halfwidth_once_and_keeps_original_publication(probe):
    value=parking_world(width=3,height=3)
    value.sequence=2
    value.observed_us=1000000;value.completed_us=1100000;value.published_us=1100000
    value.localization.observed_mapper_us=1000000
    value.localization.timestamp_error_us=100000
    wire=encode(value)
    assert decode(wire)==value
    result=product(probe,wire,'world-time')
    assert result==dict(result='TIME',decoded='OK',age=250000,deadline=1100000,
        at_publication='OK',before_publication='TIME')


@pytest.mark.parametrize('change',('stations','adjacency','divider-side','goal-mask','convexity',
                                  'partial-clear','duplicate-slot','capacity','missing-localization'))
def test_new_world_semantic_counterexamples_match_python_and_c(probe,change):
    value=parking_world()
    if change=='stations':del value.lane_model.lanes[0].centerline[-1]
    elif change=='adjacency':value.lane_model.lanes[0].adjacent_lane_id=99
    elif change=='divider-side':value.lane_model.lanes[1].centerline[1].y=2.
    elif change=='goal-mask':value.parking_slots[0].goal_mask=0
    elif change=='convexity':value.parking_slots[0].corners[2].CopyFrom(value.parking_slots[0].corners[0])
    elif change=='partial-clear':value.parking_slots[0].geometry_state=0;value.parking_slots[0].occupancy_state=2
    elif change=='duplicate-slot':value.parking_slots.add().CopyFrom(value.parking_slots[0])
    elif change=='capacity':value.parking_set.capacity_limited=True
    else:value.ClearField('localization')
    with pytest.raises(ValueError):encode(value)
    assert product(probe,value.SerializeToString())['result']!='OK'


def test_drive2_signed_segments_and_cusp_roundtrip(probe):
    value=parking_state()
    wire=encode(value)
    assert decode(wire,schema.DriveState)==value
    assert product(probe,wire,'state')['result']=='OK'
    for field,number in (('major',1),('state_features',3)):
        invalid=schema.DriveState();invalid.CopyFrom(value);setattr(invalid,field,number)
        with pytest.raises(ValueError):encode(invalid)
        assert product(probe,invalid.SerializeToString(),'state')['result']=='VERSION'
    for change in ('cusp-velocity','partition','velocity-sign','goal-presence'):
        invalid=schema.DriveState();invalid.CopyFrom(value)
        if change=='cusp-velocity':invalid.path[1].velocity_mps=.1
        elif change=='partition':invalid.segments[1].first_index=1
        elif change=='velocity-sign':invalid.path[3].velocity_mps=.1
        else:invalid.mission.ClearField('goal')
        with pytest.raises(ValueError):encode(invalid)
        assert product(probe,invalid.SerializeToString(),'state')['result']!='OK'


def test_link2_lane_grant_sentinel_binary_lengths_and_budget_replay(probe):
    link=schema.DriveLink(major=2);link.mission_grant.CopyFrom(lane_grant())
    wire=encode(link)
    assert decode(wire,schema.DriveLink)==link and product(probe,wire,'link')['result']=='OK'
    for field,value in (('mission_id',bytes(15)),('primitive',bytes(31)),('mission_deadline_pi_us',1)):
        invalid=schema.DriveLink();invalid.CopyFrom(link);setattr(invalid.mission_grant,field,value)
        with pytest.raises(ValueError):encode(invalid)
        assert product(probe,invalid.SerializeToString(),'link')['result']!='OK'
    reply=schema.DriveLink(major=2)
    reply.budget_reply.CopyFrom(schema.MissionBudgetReply(major=1,request_nonce=bytes([1])*16,
        request_sha256=bytes([2])*32,result=2,reason=0))
    assert product(probe,encode(reply),'link')['result']=='OK'
    reply.budget_reply.grant.CopyFrom(lane_grant())
    with pytest.raises(ValueError):encode(reply)
    assert product(probe,reply.SerializeToString(),'link')['result']!='OK'
    request=schema.DriveLink(major=2)
    request.budget_request.CopyFrom(schema.MissionBudgetRequest(major=1,request_nonce=bytes([1])*16,
        mission_id=bytes([2])*16,mission_revision=1,control_generation=1,expected_grant_revision=1,
        action=1,slot_id=1,slot_revision=1,goal_revision=1,plan_revision=1,
        plan_sha256=bytes([3])*32,selected_goal_sha256=bytes([4])*32,
        scene_admission_id=bytes(16),scene_revision=0,completed_guest_us=1000000,valid_until_pi_us=1100000))
    assert product(probe,encode(request),'link')['result']=='OK'


def test_link2_clock_keeps_vehicle_major1_and_nested_current_mission(probe):
    link=schema.DriveLink(major=2)
    reply=link.clock
    reply.probe.CopyFrom(schema.ClockProbe(vm=fixture.BOOT,execution=fixture.INSTANCE,
        app_instance=fixture.MAP,sequence=1,guest_send_us=1000000))
    reply.host_boot=fixture.BOOT;reply.gateway_instance=fixture.INSTANCE
    reply.pi_receive_us=1001000;reply.pi_send_us=1002000
    reply.current_mission.CopyFrom(lane_grant())
    reply.vehicle.CopyFrom(schema.VehicleSample(major=1,vehicle='rc-test-1',host_boot=fixture.BOOT,
        device_session=fixture.INSTANCE,odom_epoch=fixture.ODOM,sequence=1,source_sample=1,
        source_us=1000000,pi_sample_us=1001000,clock_error_us=1000,left_ticks=0,right_ticks=0,
        velocity_mps=0.,position_error_m=.01,heading_error_rad=.01,quality=3,calibration='encoder-lab-1'))
    reply.vehicle.odom_pose.CopyFrom(schema.Pose(x=0.,y=0.,yaw=0.))
    assert decode(encode(link),schema.DriveLink)==link
    assert product(probe,encode(link),'link')['result']=='OK'
    reply.vehicle.major=2
    with pytest.raises(ValueError):encode(link)
    assert product(probe,link.SerializeToString(),'link')['result']=='VERSION'


def test_new_repeated_counts_and_uint64_do_not_truncate_before_validation(probe):
    value=parking_world()
    value.lane_model.lanes.add().CopyFrom(value.lane_model.lanes[0])
    with pytest.raises(ValueError):encode(value)
    assert product(probe,value.SerializeToString())['result']=='DECODE'
    value=parking_world();value.sequence=1<<63
    with pytest.raises(ValueError):encode(value)
    with pytest.raises(ValueError):decode(value.SerializeToString())
    assert product(probe,value.SerializeToString())['result']=='DECODE'


def test_scene_and_tof_standalone_shapes_do_not_imply_admission():
    scene=schema.SceneAdmissionRef(admission_id=bytes([1])*16,admission_revision=1,
        survey_id=bytes([2])*16,survey_revision=1,policy=1,coverage_profile_sha256=bytes([3])*32,
        allow_restore_after_mapper_restart=False)
    grant=schema.SceneGrant(major=1,vehicle='rc-test-1',pi_host_boot=fixture.BOOT,
        gateway_instance=fixture.INSTANCE,source_admission_revision=1,mapper_device='jetson',
        map_epoch=fixture.MAP,frame_epoch=fixture.FRAME,odom_epoch=fixture.ODOM,
        configuration='lattice-lab-1',calibration='encoder-lab-1',primitive=fixture.PRIMITIVE,
        issued_pi_us=1000000,valid_until_pi_us=2000000,lifecycle_state=0,request_nonce=bytes([4])*16,
        survey_deadline_pi_us=1500000,max_observation_stations=24,purpose=0,
        frozen_survey_revision=0,frozen_hazard_revision=0,lifecycle_revision=1)
    grant.scene.CopyFrom(scene)
    assert decode(encode(grant),schema.SceneGrant)==grant
    grant.scene.policy=0
    with pytest.raises(ValueError):encode(grant)
    grant.scene.policy=1;grant.frozen_hazard_revision=1
    with pytest.raises(ValueError):encode(grant)
    value=schema.TofObservation(major=1,vehicle='rc-test-1',host_boot=fixture.BOOT,
        gateway_instance=fixture.INSTANCE,pi_session=fixture.MAP,device_session=fixture.FRAME,
        sensor_id=0,source_sample=1,source_us=1000000,pi_sample_us=1001000,
        clock_error_us=1000,sensor_time_error_us=1000,calibration='0'*64,sensor_config_revision=1,
        rows=8,cols=8,distance_mm_le=b'\xe8\x03'*64,status=bytes([1])*64,quality=bytes([1])*64,
        frame_status=3,source_crc32=0,clock_epoch=fixture.CLOCK,clock_seq=1,complete=True,
        sensor_instance=bytes([5])*16,sensor_generation=1)
    assert decode(encode(value),schema.TofObservation)==value
    value.sensor_instance=bytes(15)
    with pytest.raises(ValueError):encode(value)
    with pytest.raises(ValueError):decode(value.SerializeToString(),schema.TofObservation)
