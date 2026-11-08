"""실제 signed C 검색/추적기의 소프트웨어 경계다. Gate 주입은 장치 증명이 아니다."""
import ctypes as C
import math
import pytest
from test_lattice import library, Product, fixture, FOUND, STOP

def ports(lib):
    lib.probe_signed_plan.argtypes=[C.c_void_p,*([C.c_float]*6),C.c_int,C.c_int,C.c_uint]
    lib.probe_segments.argtypes=[C.c_void_p,C.POINTER(C.c_int)]
    lib.probe_signed_fixture.argtypes=[C.c_void_p,C.c_uint]
    lib.probe_track_sample.argtypes=[C.c_void_p,C.c_uint,C.c_uint64,C.c_float,C.POINTER(C.c_float)]
    lib.probe_seed_cusp.argtypes=[C.c_void_p]
    lib.probe_gate.argtypes=[C.c_void_p,C.c_uint]

def sample(lib,value,index,stamp=1000000,velocity=0.):
    out=(C.c_float*6)();rc=lib.probe_track_sample(value.handle,index,stamp,velocity,out)
    return rc,list(out)

@pytest.mark.parametrize('gear,goal',[(-1,1.36),(1,2.64)])
def test_actual_signed_search_retains_gear_and_exact_goal_profile(library,gear,goal):
    ports(library);value=Product(library,fixture.world())
    try:
        assert library.probe_signed_plan(value.handle,2.,1.6,0.,goal,1.6,0.,gear,gear,0)==FOUND
        segments=(C.c_int*9)();assert library.probe_segments(value.handle,segments)==1
        assert segments[2]==gear and value.path()[-1][4]==0
        assert library.probe_valid(value.handle)==FOUND
        rc,command=sample(library,value,0)
        assert rc==FOUND and command[0]*gear>0
    finally:value.close()

def test_reverse_body_frame_curvature_has_no_second_servo_sign_flip(library):
    ports(library);value=Product(library,fixture.world())
    try:
        library.probe_signed_fixture(value.handle,1)
        assert library.probe_valid(value.handle)==FOUND
        rc,command=sample(library,value,0)
        assert rc==FOUND and command[0]<0 and command[1]>0
    finally:value.close()

def test_terminal_zero_velocity_does_not_stop_carrot_31cm_before_goal(library):
    ports(library);value=Product(library,fixture.world())
    try:
        library.probe_signed_fixture(value.handle,0)
        # 32*2cm의 경로에서 index16은 종점32cm 앞이며 끝점의 저장 속도는0이다.
        rc,command=sample(library,value,16)
        assert rc==FOUND and command[0]>0
        # 20ms 뒤32cm를 건너뛴 포즈가 xy/yaw만 맞아도 bounded progress를 지워서는 안 된다.
        rc,jumped=sample(library,value,32,1020000)
        assert rc not in (FOUND,STOP) and jumped[5] < .64-1e-4
        # 원 첫 대응을 새로 정한 명시 software plan의 정상 종점은 계속 STOP이다.
        fresh=Product(library,fixture.world())
        try:
            library.probe_signed_fixture(fresh.handle,0)
            rc,arrived=sample(library,fresh,32)
            assert rc==STOP and arrived[5]==pytest.approx(.64,abs=1e-5)
        finally:fresh.close()
    finally:value.close()

@pytest.mark.parametrize('invalid',range(1,9))
def test_cusp_does_not_use_serial_stop_as_calculation_or_skip_full_gate(library,invalid):
    ports(library);value=Product(library,fixture.world())
    try:
        library.probe_signed_fixture(value.handle,2);library.probe_seed_cusp(value.handle)
        rc,command=sample(library,value,32)
        assert rc==STOP and command[2]==1 and command[3]==0 and command[4]==-1
        assert library.probe_gate(value.handle,invalid)==0
        assert library.probe_gate(value.handle,0)==1
        assert library.probe_gate(value.handle,0)==0
        rc,command=sample(library,value,33,1020000)
        assert rc==FOUND and command[0]<0 and command[3]==1
    finally:value.close()

def test_arrived_root_does_not_create_moving_duplicate_path(library):
    ports(library);value=Product(library,fixture.world())
    try:assert library.probe_signed_plan(value.handle,2.,1.6,0.,2.,1.6,0.,1,1,2)==STOP
    finally:value.close()


def test_legacy_engine_does_not_turn_none_signed_state_into_new_direction_permission(library):
    from test_engine import vehicle,send_vehicle,load,exported_state
    from world_codec import schema,encode
    ports(library);world=fixture.world();world.reference_vehicle_pose.x=2.
    source=library.engine_new();target=library.engine_new();value=Product(library,world)
    try:
        for handle in (source,target):
            load(library,handle,world);send_vehicle(library,handle,vehicle());send_vehicle(library,handle,vehicle(2,at=1220000))
        assert library.engine_plan(source,1220000,4096)==0
        command=(C.c_float*4)();assert library.engine_track(source,1220000,command)==0
        forward=exported_state(library,source)
        library.probe_signed_fixture(value.handle,1);assert library.probe_valid(value.handle)==FOUND
        reverse=schema.DriveState();reverse.CopyFrom(forward);del reverse.path[:];del reverse.segments[:]
        for x,y,yaw,steer,speed in value.path():
            reverse.path.add(pose=schema.Pose(x=x,y=y,yaw=yaw),steering_rad=steer,velocity_mps=speed)
        reverse.segments.add(first_index=0,last_index=len(reverse.path)-1,signed_direction=-1)
        reverse.merge_goal.CopyFrom(reverse.path[-1].pose)
        raw=encode(reverse);assert library.engine_apply(target,raw,len(raw),1220000)==9
        raw=encode(forward);assert library.engine_apply(target,raw,len(raw),1220000)==0
        assert library.engine_track(target,1220000,command)==0 and command[0]>0
    finally:library.engine_free(source);library.engine_free(target);value.close()


@pytest.mark.parametrize("variant,direction",[(2,-1),(3,1)],ids=["forward-to-reverse","reverse-to-forward"])
def test_both_cusp_directions_need_one_original_gate_before_segment_progress(library,variant,direction):
    ports(library);value=Product(library,fixture.world())
    try:
        library.probe_signed_fixture(value.handle,variant)
        assert library.probe_valid(value.handle)==FOUND
        library.probe_seed_cusp(value.handle)
        rc,command=sample(library,value,32)
        assert rc==STOP and command[2]==1 and command[4]==direction
        assert library.probe_gate(value.handle,2)==0 # serial STOP provenance is missing
        assert library.probe_gate(value.handle,0)==1 and library.probe_gate(value.handle,0)==0
        rc,command=sample(library,value,33,1020000)
        assert rc==FOUND and command[0]*direction>0 and command[3]==1
    finally:value.close()
