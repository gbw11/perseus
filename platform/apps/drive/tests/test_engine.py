"""Real input -> core planner -> tracker -> full state application, no fake path."""
import ctypes as C
from test_lattice import library,fixture,encode,obstacle
from world_codec import schema,decode


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
