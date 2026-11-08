"""실제 C의 선택 차로/경계/원 station prefix 소비다.

기하·차량 배치는 명시한 software fixture이며 센서·임무/주차 완료나 물리 성능 수락이
아니다. legacy 최근접 reference 시험을 새 LaneModel 허가로 재사용하지 않는다.
"""

import ctypes as C
import math
from test_lattice import library, Product, obstacle, encode, CFG
from test_engine import modern_world, vehicle, send_vehicle, load, exported_state, lane_grant
from world_codec import schema


def ports(lib):
    lib.probe_task_lane.argtypes = [C.c_void_p, *([C.c_float]*4)]
    lib.probe_task_lane.restype = C.c_uint32
    lib.probe_task_contains.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32, C.c_int, *([C.c_float]*4)]
    lib.probe_task_goal.argtypes = [C.c_void_p, C.c_uint32, *([C.c_float]*3), C.POINTER(C.c_float)]
    lib.probe_basis_accept.argtypes = [C.c_void_p]
    lib.probe_basis_count.argtypes = [C.c_void_p]
    lib.probe_basis_fault.argtypes = [C.c_void_p, C.c_uint]
    lib.probe_parking_approach.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32]
    lib.engine_task_state.argtypes = [C.c_void_p, C.POINTER(C.c_uint32)]


def engine(lib, world):
    ports(lib); handle = lib.engine_new()
    load(lib, handle, world); send_vehicle(lib, handle, vehicle()); send_vehicle(lib, handle, vehicle(2, at=1220000))
    grant = encode(lane_grant())
    assert lib.engine_grant(handle, grant, len(grant), 1220000) == 0
    return handle, grant


def task_state(lib, handle):
    result = (C.c_uint32*5)(); assert lib.engine_task_state(handle, result) == 0
    return tuple(result)


def test_follow_goal_and_actual_tracker_use_lane_center_without_mutating_station_basis(library):
    world = modern_world(); before = encode(world); handle, grant = engine(library, world)
    try:
        assert library.engine_plan(handle, 1220000, 4096) == 0
        state = exported_state(library, handle)
        assert abs(state.path[-1].pose.y-1.2) < 1e-5
        assert abs(state.merge_goal.y-1.2) < 1e-5
        assert task_state(library, handle)[:4] == (1, 1, 1, 0)
        for point in state.path:
            assert .8 < point.pose.y-.125 and point.pose.y+.125 < 1.6
        command = (C.c_float*4)()
        assert library.engine_track_grant(handle, grant, len(grant), 1220000, command) == 0
        assert command[0] > 0 and abs(command[1]) < .01
        assert encode(world) == before and all(p.y == C.c_float(1.6).value for p in world.reference)
    finally: library.engine_free(handle)


def test_lane_identity_uses_whole_footprint_and_is_independent_of_model_array_order(library):
    ports(library); world = modern_world(); value = Product(library, world)
    try:
        assert library.probe_task_lane(value.handle, .5, 1.2, 0, .01) == 1
        assert library.probe_task_lane(value.handle, .5, 2.0, 0, .01) == 2
        assert library.probe_task_lane(value.handle, .5, 1.6, 0, .01) == 0
        assert library.probe_task_lane(value.handle, .5, 1.45, 0, .04) == 0
        lanes = [schema.LaneGeometry(), schema.LaneGeometry()]
        for copy, original in zip(lanes, world.lane_model.lanes): copy.CopyFrom(original)
        del world.lane_model.lanes[:]
        world.lane_model.lanes.add().CopyFrom(lanes[1]); world.lane_model.lanes.add().CopyFrom(lanes[0])
        value.update(world)
        assert library.probe_task_lane(value.handle, .5, 1.2, 0, .01) == 1
    finally: value.close()


def test_original_divider_placement_and_uncertain_lane_cannot_start_modern_auto(library):
    world = modern_world(); world.reference_vehicle_pose.y = 1.6
    handle, _ = engine(library, world)
    try:
        assert library.engine_plan(handle, 1220000, 4096) == 5
    finally: library.engine_free(handle)


def test_changed_lane_search_uses_adjacent_center_and_continues_after_explicit_new_observation(library):
    world = modern_world(); obstacle(world, 20, 25, 9, 15)
    handle, grant = engine(library, world)
    try:
        assert library.engine_plan(handle, 1220000, 4096) == 0
        initial = exported_state(library, handle)
        assert task_state(library, handle)[:4] == (1, 1, 2, 1)
        assert initial.merge_goal.y == 2.0
        # 검색 종점은 기존 profile의 goal_distance 안에서 수락된다. 중심 goal 자체와
        # 실제 곡률 경로의 허용 종점을 같은 정확 좌표로 강제하지 않는다.
        endpoint=initial.path[-1].pose
        assert math.hypot(endpoint.x-initial.merge_goal.x,endpoint.y-initial.merge_goal.y) <= CFG['goal_distance']
        assert abs(endpoint.yaw-initial.merge_goal.yaw) <= CFG['goal_heading']
        command = (C.c_float*4)()
        assert library.engine_track_grant(handle, grant, len(grant), 1220000, command) == 0 and command[0] > 0
        # 별도 명시 MAP/odom 배치 관측이다. 위 command의 물리 실행을 주장하지 않는다.
        observed = vehicle(3, at=1300000); observed.odom_pose.x = 3.2; observed.odom_pose.y = .8
        send_vehicle(library, handle, observed)
        world.sequence = world.observation_seq = 3; world.reference_odom_seq = 3
        world.reference_vehicle_pose.CopyFrom(schema.Pose(x=3.5, y=2., yaw=0))
        world.observed_us = 1190000; world.completed_us = 1191000; world.published_us = 1192000
        world.localization.observed_mapper_us = world.observed_us
        load(library, handle, world, at=1300000)
        assert library.engine_plan(handle, 1300000, 4096) == 0
        assert task_state(library, handle)[:4] == (1, 2, 2, 0)
        assert library.engine_track_grant(handle, grant, len(grant), 1300000, command) == 0 and command[0] > 0
        assert world.reference[0].y == C.c_float(1.6).value
    finally: library.engine_free(handle)


def test_forbidden_crossing_and_fresh_permission_remain_independent_constraints(library):
    ports(library); world = modern_world(); value = Product(library, world)
    try:
        assert library.probe_task_contains(value.handle, 1, 2, 1, 1., 1.6, 0, .01) == 1
        world.lane_model.crossing = bytes(3); value.update(world)
        assert library.probe_task_contains(value.handle, 1, 2, 1, 1., 1.6, 0, .01) == 0
        assert library.probe_task_contains(value.handle, 1, 1, 0, 1., 1.6, 0, .01) == 0
        obstacle(world, 20, 25, 9, 15); handle, _ = engine(library, world)
        try: assert library.engine_plan(handle, 1220000, 4096) != 0
        finally: library.engine_free(handle)
        world.lane_model.crossing = bytes([1])*3; world.drivable_permission = bytes(len(world.drivable_permission))
        handle, _ = engine(library, world)
        try: assert library.engine_plan(handle, 1220000, 4096) != 0
        finally: library.engine_free(handle)
    finally: value.close()


def test_open_basis_accepts_only_exact_append_and_keeps_original_prefix(library):
    ports(library); world = modern_world(); value = Product(library, world)
    try:
        assert library.probe_basis_accept(value.handle) == 1
        original = [p.SerializeToString(deterministic=True) for p in world.reference]
        world.reference.add(x=6.1, y=1.6); world.reference_revision += 1
        world.left_boundary.add(x=6.1, y=2.4); world.right_boundary.add(x=6.1, y=.8)
        value.update(world)
        assert library.probe_basis_accept(value.handle) == 1
        assert library.probe_basis_count(value.handle) == 8
        assert original == [p.SerializeToString(deterministic=True) for p in world.reference[:-1]]
        world.lane_model.revision += 1
        for lane in world.lane_model.lanes: lane.revision += 1
        value.update(world)
        assert library.probe_basis_accept(value.handle) == 1
        world.reference[0].y += .01; value.update(world)
        assert library.probe_basis_accept(value.handle) == 0
        assert library.probe_basis_count(value.handle) == 8
    finally: value.close()


def test_actual_reference_append_does_not_reissue_mission_or_reset_current_path(library):
    world = modern_world(); handle, grant = engine(library, world)
    try:
        assert library.engine_plan(handle, 1220000, 4096) == 0
        command = (C.c_float*4)()
        assert library.engine_track_grant(handle, grant, len(grant), 1220000, command) == 0
        original = exported_state(library, handle)
        world.reference.add(x=6.1, y=1.6); world.reference_revision = 2
        world.left_boundary.add(x=6.1, y=2.4); world.right_boundary.add(x=6.1, y=.8)
        world.sequence = world.observation_seq = 2
        load(library, handle, world, at=1230000)
        assert library.engine_plan(handle, 1230000, 4096) == 0
        assert library.engine_track_grant(handle, grant, len(grant), 1230000, command) == 0
        current = exported_state(library, handle)
        assert current.path_revision == original.path_revision
        assert current.maneuver_revision == original.maneuver_revision
        assert current.mission == original.mission
        assert current.merge_goal == original.merge_goal
        assert task_state(library, handle)[:4] == (1, 1, 1, 0)
    finally: library.engine_free(handle)


def test_grid_roi_can_move_but_closed_overcap_or_changed_source_cannot_restart_basis(library):
    ports(library); world = modern_world(); value = Product(library, world)
    try:
        assert library.probe_basis_accept(value.handle) == 1
        world.origin.x = .01; value.update(world)
        assert library.probe_basis_accept(value.handle) == 1
        assert library.probe_basis_count(value.handle) == 7
        world.origin.x = 0.; value.update(world)
        # 이 둘은 wire 수락 사실이 아닌, helper의 bounded 값 거절 주입이다.
        assert library.probe_basis_fault(value.handle, 1) == 0
        assert library.probe_basis_fault(value.handle, 2) == 0
        assert library.probe_basis_fault(value.handle, 3) == 0
    finally: value.close()


def test_short_unblocked_lane_goal_does_not_require_bypass_settling_length(library):
    world = modern_world(); world.reference_vehicle_pose.x = 5.2
    handle, grant = engine(library, world)
    try:
        assert library.engine_plan(handle, 1220000, 4096) == 0
        state = exported_state(library, handle)
        assert .30 < state.path[-1].pose.x-5.2 < .40
        command = (C.c_float*4)()
        assert library.engine_track_grant(handle, grant, len(grant), 1220000, command) == 0
        assert command[0] > 0
    finally: library.engine_free(handle)


def test_selected_parking_approach_lane_is_required_without_authorizing_other_apron_space(library):
    from test_world import parking_world
    ports(library); value = Product(library, parking_world())
    try:
        assert library.probe_parking_approach(value.handle, 1, 1) == 1
        assert library.probe_parking_approach(value.handle, 2, 1) == 0
        assert library.probe_parking_approach(value.handle, 1, 0) == 0
        assert library.probe_parking_approach(value.handle, 1, 9) == 0
        # 해당 slot이 있다는 사실도 FOLLOW 차로 밖 차체를 허용하지 않는다.
        assert library.probe_task_contains(value.handle, 1, 1, 0, 3.3, 2.7, 0, .01) == 0
    finally: value.close()


def test_actual_warm_adoption_requires_same_task_basis_and_rejects_old_modern_body(library):
    source,grant=engine(library,modern_world());target,_=engine(library,modern_world())
    try:
        assert library.engine_plan(source,1220000,4096)==0
        command=(C.c_float*4)()
        assert library.engine_track_grant(source,grant,len(grant),1220000,command)==0
        state=exported_state(library,source)
        assert state.state_features==15 and state.task_reference.home_lane_id==1
        wire=encode(state);assert library.engine_apply(target,wire,len(wire),1220000)==0
        assert library.engine_track_grant(target,grant,len(grant),1220000,command)==0 and command[0]>0
        # codec은 구조상 base7을 읽지만 이 actual selected-lane profile은 body 없는 사본을 채택하지 않는다.
        old=schema.DriveState();old.CopyFrom(state);old.ClearField("task_reference");old.state_features=7
        raw=encode(old);assert library.engine_apply(target,raw,len(raw),1220000)==9
        assert library.engine_track_grant(target,grant,len(grant),1220000,command)==0 and command[0]>0
        for field in ("vehicle","mapper_boot","mapper_instance","clock_epoch"):
            changed=schema.DriveState();changed.CopyFrom(state)
            setattr(changed.task_reference,field,"eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee" if field!="vehicle" else "foreign-car")
            raw=encode(changed);assert library.engine_apply(target,raw,len(raw),1220000)==9
    finally:library.engine_free(source);library.engine_free(target)
