"""Real input -> core planner -> tracker -> full state application, no fake path."""
import ctypes as C
import math
import struct
from uuid import UUID
import pytest
from test_lattice import library,fixture,encode,obstacle
from world_codec import schema,decode
from test_world import modern_world as _modern_world

def modern_world():
    value=_modern_world();value.reference_vehicle_pose.y=1.2
    return value

def exported_state(lib,engine):
    out=C.create_string_buffer(32768);n=lib.engine_state(engine,out,len(out));assert n>0
    return decode(out.raw[:n],schema.DriveState)


def lane_grant(*,revision=3):
    """합성 Pi issuer receipt. 실제 Authority/장치 승인이나 운영 설정은 아니다."""
    link=schema.DriveLink(major=2)
    link.mission_grant.CopyFrom(schema.MissionGrant(major=1,vehicle='rc-test-1',
        pi_host_boot=UUID(fixture.BOOT).bytes,gateway_instance=bytes.fromhex('b1'*16),
        owner_vm=UUID('77777777-7777-4777-8777-777777777777').bytes,
        owner_execution=UUID('88888888-8888-4888-8888-888888888888').bytes,
        app_instance=UUID('99999999-9999-4999-8999-999999999999').bytes,control_generation=1,
        mission_id=bytes.fromhex('a1'*16),mission_revision=2,grant_revision=revision,kind=1,strategy=0,
        operator_request_nonce=bytes.fromhex('c1'*16),issued_pi_us=1000000,mission_deadline_pi_us=0,
        cancel_fence_revision=4,scene_admission_id=bytes(16),scene_revision=0,configuration='lattice-lab-1',
        calibration='encoder-lab-1',primitive=bytes.fromhex(fixture.PRIMITIVE),execution_deadline_pi_us=0,
        correction_attempts_used=0,execution_travel_m=0,max_execution_travel_m=0,admitted=True,
        search_deadline_pi_us=0,search_travel_m=0,max_search_travel_m=0))
    return link




def vehicle(seq=1,*,x=0.,at=1200000):
    return schema.VehicleSample(major=1,vehicle='rc-test-1',host_boot=fixture.BOOT,
        device_session=fixture.INSTANCE,odom_epoch=fixture.ODOM,sequence=seq,source_sample=seq,
        source_us=at-100000,pi_sample_us=at,clock_error_us=500,left_ticks=seq,right_ticks=seq,
        odom_pose=schema.Pose(x=x,y=0,yaw=0),velocity_mps=0.,estimated_steering_rad=0.,
        position_error_m=.005,heading_error_rad=.01,steering_error_rad=.001,quality=3,calibration='encoder-lab-1')


def send_vehicle(lib,engine,v):
    data=encode(v);assert lib.engine_vehicle(engine,data,len(data))==0


def load(lib,engine,world,at=1200000):
    data=encode(world);assert lib.engine_world(engine,data,len(data),at)==0


def test_full_product_state_is_rechecked_and_calculated_before_adoption(library):
    a=library.engine_new();b=library.engine_new();world=fixture.world()
    obstacle(world,19,24,13,19)
    try:
        for e in (a,b):
            load(library,e,world);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(a,1220000,4096)==0
        command=(C.c_float*4)();assert library.engine_track(a,1220000,command)==0
        assert command[0]>0 # Real selected tracker completion, not a readiness timer.
        output=C.create_string_buffer(32768);length=library.engine_state(a,output,len(output))
        assert length>0
        state=decode(output.raw[:length],schema.DriveState)
        assert len(state.path)>50 and state.calculation_sequence==1
        assert library.engine_apply(b,output.raw,length,1220000)==0
        assert library.engine_track(b,1220000,command)==0 and command[0]>0
        state.path[len(state.path)//2].pose.y=99
        bad=encode(state)
        assert library.engine_apply(b,bad,len(bad),1220000)==9
        # Rejected incoming state does not overwrite the validated path.
        assert library.engine_track(b,1220000,command)==0 and command[0]>0
    finally:library.engine_free(a);library.engine_free(b)


def test_missing_exact_anchor_expiry_then_real_fresh_input_recovery(library):
    e=library.engine_new();world=fixture.world();command=(C.c_float*4)()
    try:
        load(library,e,world);send_vehicle(library,e,vehicle(2))
        assert library.engine_plan(e,1200000,4096)==5
        # The missing exact historical sample cannot be replaced by latest.
        library.engine_free(e);e=library.engine_new()
        load(library,e,world);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        assert library.engine_track(e,1700000,command)==7 and command[0]==0
        world.sequence=2;world.observation_seq=2;world.observed_us=1600000
        world.completed_us=1601000;world.published_us=1602000;world.reference_odom_seq=3
        send_vehicle(library,e,vehicle(3,at=1700000));load(library,e,world,at=1705000)
        assert library.engine_track(e,1705000,command)==0 and command[0]>0
    finally:library.engine_free(e)


def test_refreshed_clock_upper_bound_preserves_original_observation_deadline(library):
    e=library.engine_new();w=fixture.world();out=(C.c_float*4)()
    try:
        send_vehicle(library,e,vehicle(at=1100000));send_vehicle(library,e,vehicle(2,at=1190000))
        load(library,e,w,at=1220000)
        assert library.engine_plan(e,1220000,4096)==0
        # Both valid clock intervals bound the same Pi clock. A tighter upper
        # bound may be lower than the previous receive estimate; the original
        # observation at Pi 1,100,000us still expires at 1,400,000us.
        assert library.engine_track(e,1215000,out)==0 and out[0]>0
        send_vehicle(library,e,vehicle(3,at=1400001))
        assert library.engine_track(e,1400001,out)==7 and out[0]==0
    finally:library.engine_free(e)


def test_map_before_anchor_keeps_valid_current_then_adopts_and_recovers(library):
    e=library.engine_new();w=fixture.world();out=(C.c_float*4)()
    try:
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        newer=fixture.world();newer.sequence=2;newer.observation_seq=2;newer.reference_odom_seq=3
        newer.occupancy=bytes([100])*len(newer.occupancy)
        load(library,e,newer,at=1230000)
        # Completed map arrived through another broker before its exact anchor.
        # It neither invents the anchor nor replaces the previous usable map.
        assert library.engine_track(e,1230000,out)==0 and out[0]>0
        send_vehicle(library,e,vehicle(3,at=1240000))
        assert library.engine_track(e,1240000,out)==6 and out[0]==0
        w.sequence=3;w.observation_seq=3;w.reference_odom_seq=4
        send_vehicle(library,e,vehicle(4,at=1260000));load(library,e,w,at=1260000)
        assert library.engine_plan(e,1260000,4096)==0
        assert library.engine_track(e,1260000,out)==0 and out[0]>0
    finally:library.engine_free(e)


def test_fresh_instance_near_road_end_rebuilds_with_swept_endpoint_reserve(library):
    e=library.engine_new();w=fixture.world()
    w.reference_vehicle_pose.CopyFrom(schema.Pose(x=4.5282,y=1.5311,yaw=.0051))
    try:
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        command=(C.c_float*4)();assert library.engine_track(e,1220000,command)==0 and command[0]>0
        out=C.create_string_buffer(32768);n=library.engine_state(e,out,len(out));assert n>0
        state=decode(out.raw[:n],schema.DriveState)
        # Independent fixture geometry: road ends x=6; corner radius includes
        # 0.010m half-step sweep. No planner output makes this expected value.
        import math
        assert abs(state.merge_goal.x-(6-math.hypot(.415,.215)))<.0001
    finally:library.engine_free(e)


@pytest.mark.parametrize('rotation',[0.,.4])
def test_curved_reference_goal_respects_rotated_grid_and_swept_end_reserve(library,rotation):
    e=library.engine_new();w=fixture.world(height=64);radius=2.
    cs,sn=math.cos(rotation),math.sin(rotation)
    w.origin.CopyFrom(schema.Pose(x=2,y=-1,yaw=rotation))
    def point(x,y):return {'x':2+cs*x-sn*y,'y':-1+sn*x+cs*y}
    for group in (w.reference,w.left_boundary,w.right_boundary):del group[:]
    for k in range(39):
        angle=-.2+k*.08;x=1.1+radius*math.sin(angle);y=3.2+radius*(1-math.cos(angle))
        nx,ny=-math.sin(angle),math.cos(angle)
        w.reference.add(**point(x,y));w.left_boundary.add(**point(x+nx*.7,y+ny*.7))
        w.right_boundary.add(**point(x-nx*.7,y-ny*.7))
    w.reference_vehicle_pose.CopyFrom(schema.Pose(**point(1.1,3.2),yaw=rotation))
    try:
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        out=(C.c_float*4)();assert library.engine_track(e,1220000,out)==0 and out[0]>0
        state=exported_state(library,e)
        reserve=math.hypot(.415,.215)
        local_goal_y=-(state.merge_goal.x-2)*sn+(state.merge_goal.y+1)*cs
        assert 5.8<local_goal_y<=6.4-reserve+1e-5
        for sample in state.path:
            c,s=math.cos(sample.pose.yaw),math.sin(sample.pose.yaw)
            for u in (-.125,.325):
                for v in (-.125,.125):
                    x=sample.pose.x+c*u-s*v-2;y=sample.pose.y+s*u+c*v+1
                    assert 0<=x*cs+y*sn<6.4 and 0<=-x*sn+y*cs<6.4
    finally:library.engine_free(e)


def exported_state(lib,engine):
    out=C.create_string_buffer(32768);n=lib.engine_state(engine,out,len(out));assert n>0
    return decode(out.raw[:n],schema.DriveState)


def test_blocked_automatic_terminal_advances_then_plans_past_obstacle(library):
    e=library.engine_new();w=fixture.world(height=64);out=(C.c_float*4)()
    # The original 4.5m target is x=4.8, inside this actual occupied box.
    obstacle(w,46,51,12,20)
    try:
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        first=exported_state(library,e)
        assert .6<first.path[-1].pose.x<4.4
        assert library.engine_track(e,1220000,out)==0 and out[0]>0
        # A later rolling observation extends beyond the same obstacle. The
        # automatic local target must not remain pinned before it forever.
        rolling_road(library,e,3,1300000,2.,1.6,0,blocks=((36,41,12,20),))
        assert library.engine_plan(e,1300000,4096)==0
        advanced=exported_state(library,e)
        assert advanced.path[-1].pose.x>5.8 and advanced.direction!=0
        assert library.engine_track(e,1300000,out)==0 and out[0]>0
    finally:library.engine_free(e)


def test_no_forward_terminal_stops_then_fresh_clear_map_recovers(library):
    e=library.engine_new();w=fixture.world(height=64);out=(C.c_float*4)()
    # The measured start remains free; every forward terminal's inflated front
    # intersects this wall. Retargeting cannot convert that into a usable path.
    obstacle(w,7,64,2,30)
    try:
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==6
        assert library.engine_track(e,1220000,out)!=0 and out[0]==0
        rolling_road(library,e,3,1300000,.3,1.6,0)
        assert library.engine_plan(e,1300000,4096)==0
        assert library.engine_track(e,1300000,out)==0 and out[0]>0
    finally:library.engine_free(e)


def rolling_road(lib,engine,sequence,at,x,y,yaw,steer=0,reference_revision=1,blocks=()):
    # Unit observations explicitly move the fixture. Circuit replay uses an
    # independently integrated vehicle instead of this state-placement helper.
    v=vehicle(sequence,at=at);v.odom_pose.CopyFrom(schema.Pose(x=x-.3,y=y-1.6,yaw=yaw))
    v.estimated_steering_rad=steer;v.velocity_mps=.4;send_vehicle(lib,engine,v)
    w=fixture.world(height=64);w.sequence=sequence;w.observation_seq=sequence;w.reference_odom_seq=sequence
    w.reference_revision=reference_revision
    w.observed_us=at-110000;w.completed_us=at-109000;w.published_us=at-108000
    w.origin.x=x-1.;w.reference_vehicle_pose.CopyFrom(schema.Pose(x=x,y=y,yaw=yaw))
    for group in (w.reference,w.left_boundary,w.right_boundary):del group[:]
    for k in range(7):
        px=x-1.+k
        w.reference.add(x=px,y=1.6);w.left_boundary.add(x=px,y=3.);w.right_boundary.add(x=px,y=.2)
    for bounds in blocks:obstacle(w,*bounds)
    load(lib,engine,w,at=at)


@pytest.mark.parametrize('bypass',[False,True])
def test_consumed_local_path_advances_without_losing_real_bypass_intent(library,bypass):
    e=library.engine_new();w=fixture.world(height=64);out=(C.c_float*4)()
    if bypass:obstacle(w,19,24,13,19)
    else:w.reference_vehicle_pose.y=1.45
    try:
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        assert library.engine_track(e,1220000,out)==0 and out[0]>0
        original=exported_state(library,e)
        assert (original.direction!=0)==bypass
        # Same observed failure: inside XY tolerance, but heading still differs.
        rolling_road(library,e,3,1300000,original.merge_goal.x,1.6,.278)
        assert library.engine_plan(e,1300000,4096)==0
        assert library.engine_track(e,1300000,out)==0 and out[0]>0
        advanced=exported_state(library,e)
        assert advanced.path[-1].pose.x>original.merge_goal.x+2
        assert advanced.path_revision>original.path_revision
        assert library.engine_track(e,1300000,out)==0 and out[0]>0
        if bypass:
            assert advanced.direction==original.direction
            assert advanced.merge_goal==original.merge_goal
            assert advanced.maneuver_revision==original.maneuver_revision
            # A fresh Standby gets the persistent merge intent and the actual
            # forward execution path, rather than driving back to that intent.
            standby=library.engine_new()
            try:
                rolling_road(library,standby,3,1300000,original.merge_goal.x,1.6,.278)
                wire=encode(advanced)
                assert library.engine_apply(standby,wire,len(wire),1300000)==0
                assert library.engine_track(standby,1300000,out)==0 and out[0]>0
                assert exported_state(library,standby).merge_goal==original.merge_goal
            finally:library.engine_free(standby)
            # Heading alone inside the limit is insufficient when observation
            # uncertainty pushes its total beyond the original allowance.
            rolling_road(library,e,4,1400000,original.merge_goal.x+.2,1.6,.215)
            assert library.engine_plan(e,1400000,4096)==0
            assert exported_state(library,e).direction==original.direction
            rolling_road(library,e,5,1500000,original.merge_goal.x+.3,1.6,0)
            assert library.engine_plan(e,1500000,4096)==0
            settled=exported_state(library,e)
            assert settled.direction==0 and settled.maneuver_revision>original.maneuver_revision
        else:assert advanced.direction==0
    finally:library.engine_free(e)


def test_merge_passage_survives_handoff_and_clipped_reference(library):
    source=library.engine_new();target=library.engine_new();legacy=library.engine_new();out=(C.c_float*4)()
    world=fixture.world(height=64);obstacle(world,19,24,13,19)
    try:
        load(library,source,world);send_vehicle(library,source,vehicle());send_vehicle(library,source,vehicle(2,at=1220000))
        assert library.engine_plan(source,1220000,4096)==0
        assert library.engine_track(source,1220000,out)==0
        original=exported_state(library,source);assert original.direction
        x=original.merge_goal.x
        # Confirm actual passage while the milestone still belongs to this road.
        # The heading remains unaligned, so passage must not complete rejoining.
        rolling_road(library,source,3,1300000,x+.2,1.6,.278)
        assert library.engine_plan(source,1300000,4096)==0
        assert library.engine_track(source,1300000,out)==0 and out[0]>0
        state=exported_state(library,source)
        assert state.merge_passed_reference_revision==1 and state.direction==original.direction
        assert state.merge_goal==original.merge_goal and state.maneuver_revision==original.maneuver_revision
        # Exercise the common binary envelope used by the real Gateway relay.
        link=schema.DriveLink(major=2);link.accepted.host_boot=fixture.BOOT
        link.accepted.gateway_instance=fixture.INSTANCE;link.accepted.state.CopyFrom(state)
        accepted=decode(encode(link),schema.DriveLink).accepted.state
        for engine,payload in ((target,accepted),(legacy,decode(encode(state),schema.DriveState))):
            if engine==legacy:payload.ClearField('merge_passed_reference_revision')
            rolling_road(library,engine,3,1300000,x+.2,1.6,.278)
            wire=encode(payload)
            assert library.engine_apply(engine,wire,len(wire),1300000)==0
            assert library.engine_track(engine,1300000,out)==0 and out[0]>0
        assert exported_state(library,target).merge_passed_reference_revision==1
        assert not exported_state(library,legacy).HasField('merge_passed_reference_revision')
        bad=schema.DriveState();bad.CopyFrom(accepted);bad.merge_passed_reference_revision=0
        raw=bad.SerializeToString()
        assert library.engine_apply(target,raw,len(raw),1300000)==106
        assert library.engine_track(target,1300000,out)==0 and out[0]>0
        assert exported_state(library,target).merge_passed_reference_revision==1
        duplicate=encode(accepted)+b'\xd8\x01\x01'
        with pytest.raises(ValueError,match='duplicate'):decode(duplicate,schema.DriveState)
        assert library.engine_apply(target,duplicate,len(duplicate),1300000)==103
        # Move only after adoption, keeping the incoming full path inside the
        # recipient's current map at adoption. Then the old milestone is clipped.
        for engine in (target,legacy):
            rolling_road(library,engine,4,1400000,x+1.8,1.6,.278)
            assert library.engine_plan(engine,1400000,4096)==0
            assert library.engine_track(engine,1400000,out)==0 and out[0]>0
        assert exported_state(library,target).merge_passed_reference_revision==1
        assert not exported_state(library,legacy).HasField('merge_passed_reference_revision')
        # Neither disappearance of the old point nor nearest projection onto a
        # far road endpoint may be used as new passage evidence.
        for engine in (target,legacy):
            rolling_road(library,engine,5,1500000,x+2.,1.6,0)
            assert library.engine_plan(engine,1500000,4096)==0
            assert library.engine_track(engine,1500000,out)==0 and out[0]>0
        settled=exported_state(library,target);unconfirmed=exported_state(library,legacy)
        assert settled.direction==0 and not settled.HasField('merge_passed_reference_revision')
        assert settled.maneuver_revision>state.maneuver_revision
        assert unconfirmed.direction==state.direction
        assert unconfirmed.path[-1].pose.x>x+4 # No return to the clipped milestone.
    finally:
        for engine in (source,target,legacy):library.engine_free(engine)


def test_passed_merge_survives_real_direction_change_and_binary_handoff(library):
    source=library.engine_new();target=library.engine_new();out=(C.c_float*4)()
    world=fixture.world(height=64);obstacle(world,19,24,13,19)
    try:
        load(library,source,world);send_vehicle(library,source,vehicle());send_vehicle(library,source,vehicle(2,at=1220000))
        assert library.engine_plan(source,1220000,4096)==0
        assert library.engine_track(source,1220000,out)==0
        original=exported_state(library,source);assert original.direction
        x=original.merge_goal.x
        rolling_road(library,source,3,1300000,x+.2,1.6,.278)
        assert library.engine_plan(source,1300000,4096)==0
        assert library.engine_track(source,1300000,out)==0 and out[0]>0
        passed=exported_state(library,source);assert passed.merge_passed_reference_revision==1
        # A real new occupied corridor forces the opposite bypass. The old
        # milestone is behind this rolling reference, not a newly chosen goal.
        blocked_side=(23,34,19,30) if original.direction>0 else (23,34,2,13)
        heading=-.278*original.direction
        rolling_road(library,source,4,1400000,x+1.8,1.6,heading,
                     blocks=((26,31,13,19),blocked_side))
        assert library.engine_plan(source,1400000,4096)==0
        assert library.engine_track(source,1400000,out)==0 and out[0]>0
        changed=exported_state(library,source)
        assert changed.direction==-original.direction
        assert changed.merge_goal==passed.merge_goal
        assert changed.maneuver_revision>passed.maneuver_revision
        assert changed.merge_passed_reference_revision==1
        link=schema.DriveLink(major=2);link.accepted.host_boot=fixture.BOOT
        link.accepted.gateway_instance=fixture.INSTANCE;link.accepted.state.CopyFrom(changed)
        accepted=decode(encode(link),schema.DriveLink).accepted.state
        rolling_road(library,target,4,1400000,x+1.8,1.6,heading,
                     blocks=((26,31,13,19),blocked_side))
        wire=encode(accepted);assert library.engine_apply(target,wire,len(wire),1400000)==0
        assert library.engine_track(target,1400000,out)==0 and out[0]>0
        adopted=exported_state(library,target)
        assert adopted.merge_passed_reference_revision==1 and adopted.direction==changed.direction
        assert adopted.merge_goal==changed.merge_goal
        # Once actual current road/heading/steering guards hold, both the
        # original and the recipient close the same maneuver without returning
        # to the clipped milestone. Direction change is not itself completion.
        for engine in (source,target):
            rolling_road(library,engine,5,1500000,x+3.6,1.6,0)
            assert library.engine_plan(engine,1500000,4096)==0
            assert library.engine_track(engine,1500000,out)==0 and out[0]>0
            settled=exported_state(library,engine)
            assert settled.direction==0 and not settled.HasField('merge_passed_reference_revision')
            assert settled.maneuver_revision>changed.maneuver_revision
    finally:
        library.engine_free(source);library.engine_free(target)


def test_road_policy_binary_handoff_and_policy_mismatch_preserve_current_execution(library):
    from xenova_drive_world import mark_road
    source=library.engine_new();target=library.engine_new();out=(C.c_float*4)()
    w=fixture.world()
    mark_road(w,[schema.Point(x=p.x,y=1.96) for p in w.reference],
                [schema.Point(x=p.x,y=1.24) for p in w.reference])
    try:
        for engine in (source,target):
            load(library,engine,w);send_vehicle(library,engine,vehicle());send_vehicle(library,engine,vehicle(2,at=1220000))
        assert library.engine_plan(source,1220000,4096)==0
        assert library.engine_track(source,1220000,out)==0 and out[0]>0
        state=exported_state(library,source);assert state.world_features==1
        link=schema.DriveLink(major=2);link.accepted.host_boot=fixture.BOOT
        link.accepted.gateway_instance=fixture.INSTANCE;link.accepted.state.CopyFrom(state)
        accepted=decode(encode(link),schema.DriveLink).accepted.state
        assert accepted.world_features==1
        wire=encode(accepted);assert library.engine_apply(target,wire,len(wire),1220000)==0
        assert library.engine_track(target,1220000,out)==0 and out[0]>0
        legacy=schema.DriveState();legacy.CopyFrom(accepted);legacy.ClearField('world_features')
        wire=encode(legacy);assert library.engine_apply(target,wire,len(wire),1220000)==14
        assert library.engine_track(target,1220000,out)==0 and out[0]>0
        unsupported=schema.DriveState();unsupported.CopyFrom(state);unsupported.world_features=2
        with pytest.raises(ValueError,match='world features'):encode(unsupported)
        raw=unsupported.SerializeToString();assert library.engine_apply(target,raw,len(raw),1220000)==104
        assert exported_state(library,target).world_features==1
    finally:library.engine_free(source);library.engine_free(target)


def test_invalid_merge_passage_rejected_and_new_reference_does_not_inherit_it(library):
    e=library.engine_new();target=library.engine_new();out=(C.c_float*4)()
    world=fixture.world(height=64);obstacle(world,19,24,13,19)
    try:
        load(library,e,world);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_plan(e,1220000,4096)==0
        assert library.engine_track(e,1220000,out)==0
        state=exported_state(library,e);x=state.merge_goal.x
        rolling_road(library,e,3,1300000,x+.2,1.6,.278)
        assert library.engine_plan(e,1300000,4096)==0
        assert library.engine_track(e,1300000,out)==0
        state=exported_state(library,e);assert state.merge_passed_reference_revision==1
        rolling_road(library,target,3,1300000,x+.2,1.6,.278,reference_revision=2)
        for bad in (0,2):
            changed=schema.DriveState();changed.CopyFrom(state);changed.merge_passed_reference_revision=bad
            with pytest.raises(ValueError,match='merge passage'):encode(changed)
            raw=changed.SerializeToString() # Also verify C's raw-input boundary.
            assert library.engine_apply(target,raw,len(raw),1300000)==106
        cleared=schema.DriveState();cleared.CopyFrom(state);cleared.direction=0
        with pytest.raises(ValueError,match='merge passage'):encode(cleared)
        raw=cleared.SerializeToString();assert library.engine_apply(target,raw,len(raw),1300000)==106
        wire=encode(state)
        assert library.engine_apply(target,wire,len(wire),1300000)==0
        assert library.engine_track(target,1300000,out)==0 and out[0]>0
        assert not exported_state(library,target).HasField('merge_passed_reference_revision')
        rolling_road(library,target,4,1400000,x+2.,1.6,0,reference_revision=2)
        assert library.engine_plan(target,1400000,4096)==0
        assert library.engine_track(target,1400000,out)==0 and out[0]>0
        assert exported_state(library,target).direction==state.direction
    finally:library.engine_free(e);library.engine_free(target)

def test_real_owner_memory_search_failure_then_normal_progress(library):
    library.engine_memory.argtypes=[C.c_void_p,C.c_uint,C.POINTER(C.c_uint64)]
    library.engine_force_node_limit.argtypes=[C.c_void_p,C.c_uint64]
    e=library.engine_new()
    def memory(region):
        data=(C.c_uint64*6)()
        assert library.engine_memory(e,region,data)==0
        reserved,capacity,used,peak,failures,busy=data
        assert 0<=used<=peak<=capacity<=reserved
        return list(data)
    try:
        assert all(memory(i)[2]==0 for i in range(4))
        w=fixture.world();obstacle(w,19,24,13,19)
        load(library,e,w);send_vehicle(library,e,vehicle());send_vehicle(library,e,vehicle(2,at=1220000))
        assert library.engine_force_node_limit(e,1220000)==6
        failed=memory(2);assert failed[4]==1 and failed[2]>0
        # A new real observation requests another bounded plan; no fake READY.
        w.sequence=2;w.observation_seq=2
        load(library,e,w)
        assert library.engine_plan(e,1220000,4096)==0
        out=(C.c_float*4)()
        assert library.engine_track(e,1220000,out)==0 and out[0]>0
        assert all(memory(i)[2]>0 for i in range(4))
        assert memory(2)[4]==failed[4] and memory(2)[3]>=failed[3]
    finally:
        library.engine_free(e)


def test_state2_metadata_survives_actual_apply_and_next_export_without_minting_authority(library):
    source=library.engine_new();target=library.engine_new();world=modern_world()
    grant=lane_grant();grant_wire=encode(grant)
    try:
        for engine in (source,target):
            load(library,engine,world);send_vehicle(library,engine,vehicle());send_vehicle(library,engine,vehicle(2,at=1220000))
        assert library.engine_grant(source,grant_wire,len(grant_wire),1220000)==0
        assert library.engine_plan(source,1220000,4096)==0
        command=(C.c_float*4)()
        assert library.engine_track_grant(source,grant_wire,len(grant_wire),1220000,command)==0
        state=exported_state(library,source)
        assert state.major==2 and state.state_features==15 and state.mission.kind==1
        assert state.HasField("task_reference") and state.task_reference.selected_lane_id==1
        assert state.mission.phase==10 and state.mission.grant_revision==3
        assert state.mission.mission_id==grant.mission_grant.mission_id
        assert len(state.segments)==1 and state.segments[0].signed_direction==1
        # 합성 gate 진술의 보존만 확인한다. 실제 stationary/servo 사건을 만들지 않는다.
        state.direction_gate.CopyFrom(schema.DirectionGateRef(gate_revision=5,stop_command_sequence=9,
            state=2,requested_direction=1))
        wire=encode(state)
        assert library.engine_apply(target,wire,len(wire),1220000)==0
        assert library.engine_track_grant(target,grant_wire,len(grant_wire),1220000,command)==0
        copied=exported_state(library,target)
        assert copied.mission==state.mission
        assert copied.segments==state.segments
        assert copied.direction_gate==state.direction_gate
        changed=lane_grant(revision=4);changed_wire=encode(changed)
        assert library.engine_track_grant(target,changed_wire,len(changed_wire),1220000,command)==9
        assert command[0]==0
        assert exported_state(library,target).mission==state.mission
    finally:library.engine_free(source);library.engine_free(target)


def test_new_world_whole_cell_permission_is_consumed_by_the_actual_collision_planner(library):
    for allowed in (True,False):
        engine=library.engine_new();world=modern_world()
        if not allowed:world.drivable_permission=bytes(len(world.drivable_permission))
        try:
            send_vehicle(library,engine,vehicle());send_vehicle(library,engine,vehicle(2,at=1220000))
            load(library,engine,world)
            result=library.engine_plan(engine,1220000,4096)
            assert (result==0)==allowed
        finally:library.engine_free(engine)


def test_localization_lease_expires_without_renewing_to_world_or_vehicle_deadline(library):
    engine=library.engine_new();world=modern_world();world.localization.valid_for_us=200000
    grant_wire=encode(lane_grant())
    try:
        send_vehicle(library,engine,vehicle());send_vehicle(library,engine,vehicle(2,at=1220000))
        load(library,engine,world)
        assert library.engine_grant(engine,grant_wire,len(grant_wire),1220000)==0
        assert library.engine_plan(engine,1220000,4096)==0
        command=(C.c_float*4)();assert library.engine_track_grant(engine,grant_wire,len(grant_wire),1220000,command)==0
        send_vehicle(library,engine,vehicle(3,at=1320000))
        assert library.engine_track_grant(engine,grant_wire,len(grant_wire),1320000,command)==7
        assert command[0]==0
    finally:library.engine_free(engine)


def test_legacy_auto_profiles_keep_none_candidate_while_lane_profile_requires_original_grant(library):
    from xenova_drive_world import mark_road
    for feature in (0,1,11):
        engine=library.engine_new();world=modern_world() if feature==11 else fixture.world()
        if feature==1:
            mark_road(world,[schema.Point(x=p.x,y=1.96) for p in world.reference],
                            [schema.Point(x=p.x,y=1.24) for p in world.reference])
        try:
            send_vehicle(library,engine,vehicle());send_vehicle(library,engine,vehicle(2,at=1220000))
            load(library,engine,world)
            assert library.engine_plan(engine,1220000,4096)==0
            command=(C.c_float*4)();result=library.engine_track(engine,1220000,command)
            if feature==11:
                assert result==9 and command[0]==0
            else:
                assert result==0 and command[0]>0
                state=exported_state(library,engine)
                assert state.major==2 and state.state_features==7 and state.mission.kind==0
                assert state.mission.mission_id==bytes(16) and state.world_features==feature
        finally:library.engine_free(engine)
