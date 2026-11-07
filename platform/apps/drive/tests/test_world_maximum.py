"""공용 codec의 최대 cardinality wire 벡터. 실제 주행/권한/모든 scalar의 최적 상한은 아니다.

4096cell/32station/8slot/64reference와512path/3gear를 모두 채운 독립 입력이다.
UUID/SHA/목표/기한의 semantic domain을 지킨다. 실행자가 환경변수로 명시한 새 directory에만
원 wire와 길이/SHA를 보존한다. 기존 golden이나 실제 제어/파일 원본을 변경하지 않는다.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import unittest
import uuid


HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME / 'world/python'))
from xenova_drive_world import schema, encode, decode, validate


U63 = (1 << 63) - 1
BASE = 1 << 62
BOOT = '11111111-1111-4111-8111-111111111111'
INSTANCE = '22222222-2222-4222-8222-222222222222'
MAP = '33333333-3333-4333-8333-333333333333'
FRAME = '44444444-4444-4444-8444-444444444444'
ODOM = '55555555-5555-4555-8555-555555555555'
CLOCK = '66666666-6666-4666-8666-666666666666'
REQUEST = '77777777-7777-4777-8777-777777777777'
MISSION = uuid.UUID(MAP).bytes
SCENE = uuid.UUID(FRAME).bytes
VEHICLE = 'v' * 64
CONFIGURATION = 'c' * 64
CALIBRATION = 'd' * 64
REFERENCE = 'r' * 64
PRIMITIVE = json.loads((HOME / 'planner/generated/primitive-manifest.json').read_text())['asset_sha256']
# 여기의 opaque fingerprint는 크기/동일성 fixture다. S11의 goal/plan 생성 검증은 별도다.
GOAL_SHA = hashlib.sha256(b'maximum-wire-fixture-selected-goal').digest()
PLAN_SHA = hashlib.sha256(b'maximum-wire-fixture-plan').digest()
SLOT_POLYGON = ((.8, .7), (2., .7), (2., 1.5), (.8, 1.5))


def maximum_world():
    """full World15. whole-cell permission은 명시 합성8x8m 지지/통행 영역이다."""
    world = schema.WorldSnapshot(major=1, minor=2, required_features=15, vehicle=VEHICLE,
        mapper_boot=BOOT, mapper_instance=INSTANCE, map_epoch=MAP, frame_epoch=FRAME, odom_epoch=ODOM,
        sequence=U63, configuration=CONFIGURATION, calibration=CALIBRATION, primitive=PRIMITIVE,
        reference_id=REFERENCE, reference_revision=U63, resolution_m=.125, width=64, height=64,
        reference_odom_seq=U63-2, observed_us=BASE, completed_us=BASE+1000, published_us=BASE+2000,
        observation_seq=U63, clock_epoch=CLOCK, complete=True, truncated=False, inflation_policy=0)
    world.origin.CopyFrom(schema.Pose(x=0., y=0., yaw=0.))
    world.reference_vehicle_pose.CopyFrom(schema.Pose(x=1., y=4., yaw=0.))
    for index in range(64):
        x = index * .125
        world.reference.add(x=x, y=4.)
        world.left_boundary.add(x=x, y=8.)
        world.right_boundary.add(x=x, y=0.)
    world.occupancy = bytes((0, 100, 255)[index % 3] for index in range(4096))
    world.cell_flags = bytes(8 if index % 3 == 2 else 11 for index in range(4096))
    world.age_le32 = b''.join(struct.pack('<I', 0xffffffff if index % 3 == 2 else 3_600_000)
                              for index in range(4096))
    world.drivable_permission = b'\xff' * 512
    model = world.lane_model
    model.revision = U63
    model.divider_position_bound_m = .001
    model.divider_heading_bound_rad = .01
    model.crossing = bytes(index % 2 for index in range(31))
    model.divider_state = bytes(index % 2 for index in range(32))
    for index in range(32):
        model.divider.add(x=index * .25, y=4.)
    for identity, adjacent, y in ((101, 102, 4.1875), (102, 101, 3.8125)):
        lane = model.lanes.add(lane_id=identity, adjacent_lane_id=adjacent, travel_direction=1,
            position_bound_m=.001, heading_bound_rad=.01, revision=U63,
            point_state=bytes(index % 2 for index in range(32)))
        for index in range(32):
            lane.centerline.add(x=index * .25, y=y)
    world.localization.CopyFrom(schema.MapLocalization(registration_revision=U63,
        anchor_position_bound_m=.01, anchor_heading_bound_rad=.01, geometry_position_bound_m=.01,
        geometry_heading_bound_rad=.01, observed_mapper_us=BASE, valid_for_us=300_000,
        position_drift_bound_mps=.01, heading_drift_bound_rps=.01, quality=1, timestamp_error_us=1000))
    world.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=SCENE, admission_revision=U63,
        survey_id=uuid.UUID(ODOM).bytes, survey_revision=U63, policy=1,
        coverage_profile_sha256=hashlib.sha256(b'maximum-wire-fixture-coverage').digest(),
        allow_restore_after_mapper_restart=False))
    for index in range(8):
        slot = world.parking_slots.add(slot_id=index+1, geometry_revision=U63, geometry_state=1,
            occupancy_state=2, entry_edge=0, position_bound_m=.01, heading_bound_rad=.01,
            geometry_age_ms=3_600_000, clearance_age_ms=10, evidence_sequence=U63, geometry_complete=True,
            occupancy_reason=0, source_kinds=7, occupancy_revision=U63,
            usable_width_m=1.2 if index == 0 else .6, usable_depth_m=.8, approach_lane_id=102, goal_mask=3)
        x, y = .5 + index * .8, 5.5
        corners = SLOT_POLYGON if index == 0 else ((x, y), (x+.6, y), (x+.6, y+.8), (x, y+.8))
        for px, py in corners:
            slot.corners.add(x=px, y=py)
        gx, gy = (1.79, .9) if index == 0 else (x+.3, y+.4)
        slot.reverse_goal.CopyFrom(schema.Pose(x=gx, y=gy, yaw=-math.atan2(.2, .7)))
        slot.forward_goal.CopyFrom(schema.Pose(x=gx, y=gy, yaw=math.pi))
    world.parking_set.CopyFrom(schema.ParkingSetMeta(revision=U63, complete=True, track_count_in_roi=8,
        capacity_limited=False, evidence_sequence=U63, state=1))
    return world


def maximum_state():
    """3개 typed gear segment의 합계512point. cusp 두 쌍만 같은 SE2 pose다."""
    state = schema.DriveState(major=2, source_vm=BOOT, source_execution=INSTANCE,
        source_app_instance=MAP, source_generation=0xffffffff, state_revision=U63,
        frame_epoch=FRAME, odom_epoch=ODOM, configuration=CONFIGURATION, primitive=PRIMITIVE,
        reference_id=REFERENCE, reference_revision=U63, path_revision=U63, maneuver_revision=U63,
        direction=0, maneuver_phase=0, vehicle_sequence=U63, calculation_sequence=U63,
        tracker_index=339, complete=True, message_id=CLOCK, calibration=CALIBRATION,
        map_epoch=MAP, observation_seq=U63, world_features=15, state_features=7)
    for index in range(170):
        point = state.path.add(steering_rad=0., velocity_mps=0. if index == 169 else .1)
        point.pose.CopyFrom(schema.Pose(x=.1 + index * .01, y=1., yaw=0.))
    for index in range(170):
        t = index / 169
        point = state.path.add(steering_rad=0., velocity_mps=0. if index in (0, 169) else -.1)
        if index == 0:
            point.pose.CopyFrom(state.path[169].pose)
        else:
            point.pose.CopyFrom(schema.Pose(x=1.79-.7*t, y=1.+.1*t*t, yaw=-math.atan2(.2*t, .7)))
    for index in range(172):
        t = index / 171
        point = state.path.add(steering_rad=0., velocity_mps=0. if index in (0, 171) else .1)
        if index == 0:
            point.pose.CopyFrom(state.path[339].pose)
        else:
            point.pose.CopyFrom(schema.Pose(x=1.09+.7*t, y=1.1-.2*t, yaw=-math.atan2(.2, .7)))
    for first, last, direction in ((0, 169, 1), (170, 339, -1), (340, 511, 1)):
        state.segments.add(first_index=first, last_index=last, signed_direction=direction)
    state.merge_goal.CopyFrom(state.path[-1].pose)
    state.mission.CopyFrom(schema.MissionExecution(mission_id=MISSION, mission_revision=U63, kind=2,
        phase=6, result=0, operator_request_nonce=uuid.UUID(REQUEST).bytes, slot_id=1, slot_revision=U63,
        goal_revision=U63, mission_deadline_pi_us=BASE+120_000_000, cancel_fence_revision=0,
        active_segment=1, goal_position_bound_m=.01, goal_heading_bound_rad=.01,
        scene_admission_id=SCENE, scene_admission_revision=U63, strategy=1,
        execution_deadline_pi_us=BASE+30_000_000, correction_attempts_used=2, execution_travel_m=.5,
        grant_revision=U63, search_travel_m=2.5, search_deadline_pi_us=BASE+90_000_000))
    state.mission.goal.CopyFrom(state.merge_goal)
    for x, y in SLOT_POLYGON:
        state.mission.selected_slot_polygon.add(x=x, y=y)
    state.direction_gate.CopyFrom(schema.DirectionGateRef(gate_revision=U63, stop_command_sequence=U63,
        state=4, requested_direction=1))
    return state


def maximum_grant():
    """명시 합성 Authority 응답 fixture. 실제 Authority가 발행한 grant가 아니다."""
    return schema.MissionGrant(major=1, vehicle=VEHICLE, pi_host_boot=uuid.UUID(BOOT).bytes,
        gateway_instance=uuid.UUID(INSTANCE).bytes, owner_vm=uuid.UUID(BOOT).bytes,
        owner_execution=uuid.UUID(INSTANCE).bytes, app_instance=uuid.UUID(MAP).bytes,
        control_generation=0xffffffff, mission_id=MISSION, mission_revision=U63, grant_revision=U63,
        kind=2, strategy=1, operator_request_nonce=uuid.UUID(REQUEST).bytes, issued_pi_us=BASE,
        mission_deadline_pi_us=BASE+120_000_000, cancel_fence_revision=0,
        scene_admission_id=SCENE, scene_revision=U63, configuration=CONFIGURATION,
        calibration=CALIBRATION, primitive=bytes.fromhex(PRIMITIVE), execution_deadline_pi_us=BASE+30_000_000,
        correction_attempts_used=2, execution_travel_m=.5, max_execution_travel_m=5., admitted=True,
        search_deadline_pi_us=BASE+90_000_000, search_travel_m=2.5, max_search_travel_m=10., locked_goal_sha256=GOAL_SHA)


def maximum_messages():
    """결정적 원 메시지를 반환한다. 호출 전에는 메시지 생성/encoder/파일 출판을 하지 않는다."""
    world, state, grant = maximum_world(), maximum_state(), maximum_grant()
    request = schema.MissionBudgetRequest(major=1, request_nonce=uuid.UUID(REQUEST).bytes, mission_id=MISSION,
        mission_revision=U63, control_generation=0xffffffff, expected_grant_revision=U63-1, action=2,
        slot_id=1, slot_revision=U63, goal_revision=U63, plan_revision=U63, plan_sha256=PLAN_SHA,
        selected_goal_sha256=GOAL_SHA, scene_admission_id=SCENE, scene_revision=U63,
        completed_guest_us=BASE, valid_until_pi_us=BASE+100_000)
    reply = schema.MissionBudgetReply(major=1, request_nonce=request.request_nonce,
        request_sha256=hashlib.sha256(encode(request)).digest(), result=1, reason=0)
    reply.grant.CopyFrom(grant)
    result = dict(world=world, state=state, grant=grant, budget_request=request, budget_reply=reply)
    for name, field, value in (('link_state', 'snapshot', state), ('link_grant', 'mission_grant', grant),
                             ('link_budget_request', 'budget_request', request), ('link_budget_reply', 'budget_reply', reply)):
        link = schema.DriveLink(major=2)
        getattr(link, field).CopyFrom(value)
        result[name] = link
    accepted = schema.DriveLink(major=2)
    accepted.accepted.host_boot = BOOT
    accepted.accepted.gateway_instance = INSTANCE
    accepted.accepted.state.CopyFrom(state)
    result['link_accepted'] = accepted
    link = schema.DriveLink(major=2)
    clock = link.clock
    clock.probe.CopyFrom(schema.ClockProbe(vm=BOOT, execution=INSTANCE, app_instance=MAP,
        sequence=U63, guest_send_us=BASE-1000))
    clock.host_boot, clock.gateway_instance = BOOT, INSTANCE
    clock.pi_receive_us, clock.pi_send_us = BASE+1000, BASE+2000
    clock.trust.CopyFrom(schema.WorldTrust(vehicle=VEHICLE, mapper_boot=BOOT, mapper_instance=INSTANCE,
        map_epoch=MAP, frame_epoch=FRAME, odom_epoch=ODOM, configuration=CONFIGURATION,
        calibration=CALIBRATION, primitive=PRIMITIVE, clock_epoch=CLOCK,
        lower_offset_us=-1000, upper_offset_us=1000, expires_pi_us=BASE+1_000_000, max_error_us=2000))
    clock.vehicle.CopyFrom(schema.VehicleSample(major=1, vehicle=VEHICLE, host_boot=BOOT,
        device_session=INSTANCE, odom_epoch=ODOM, sequence=U63, source_sample=U63,
        source_us=BASE, pi_sample_us=BASE+1000, clock_error_us=1000, left_ticks=U63, right_ticks=U63,
        velocity_mps=0., measured_steering_rad=0., estimated_steering_rad=0.,
        position_error_m=.01, heading_error_rad=.01, quality=3, calibration=CALIBRATION,
        steering_error_rad=.01, gateway_instance=INSTANCE, pi_session=FRAME))
    clock.vehicle.odom_pose.CopyFrom(state.path[339].pose)
    clock.current_mission.CopyFrom(grant)
    result['link_clock'] = link
    return result


def encode_maximum_vectors():
    """독립 C decode에도 쓸 수 있는 name→(message type, owned bytes) 사전이다."""
    return {name: (type(message).__name__, encode(message)) for name, message in maximum_messages().items()}


class MaximumWorldWireTests(unittest.TestCase):
    def test_complete_semantic_cardinality_maxima_roundtrip_and_wire_caps(self):
        messages = maximum_messages()
        world, state = messages['world'], messages['state']
        self.assertEqual((world.width, world.height, len(world.occupancy)), (64, 64, 4096))
        self.assertEqual([len(world.reference), len(world.left_boundary), len(world.right_boundary)], [64] * 3)
        self.assertEqual([len(lane.centerline) for lane in world.lane_model.lanes], [32, 32])
        self.assertEqual(len(world.lane_model.divider), 32)
        self.assertEqual((len(world.parking_slots), len(state.path), len(state.segments)), (8, 512, 3))
        for before, after in ((169, 170), (339, 340)):
            self.assertEqual(state.path[before].pose, state.path[after].pose)
            self.assertEqual((state.path[before].velocity_mps, state.path[after].velocity_mps), (0., 0.))
        header = (HOME / 'world/generated/world.pb.h').read_text()
        generated = {name: int(size) for name, size in re.findall(r'^#define xenova_drive_(\w+)_size\s+(\d+)', header, re.M)}
        caps = {'WorldSnapshot': 32768, 'DriveState': 20141, 'DriveLink': 32768,
                'MissionGrant': 1024, 'MissionBudgetRequest': 512, 'MissionBudgetReply': 1536}
        vectors, summary = {}, {}
        for name, message in messages.items():
            with self.subTest(name=name):
                self.assertIs(validate(message), message)
                wire = encode(message)
                self.assertEqual(wire, encode(message))
                self.assertEqual(decode(wire, type(message)), message)
                message_type = message.DESCRIPTOR.name
                self.assertLessEqual(len(wire), caps[message_type])
                self.assertLessEqual(len(wire), generated[message_type])
                vectors[name] = wire
                summary[name] = dict(message_type=message_type, bytes=len(wire),
                    sha256=hashlib.sha256(wire).hexdigest(), generated_max=generated[message_type],
                    semantic_cap=caps[message_type])
        print('MAXIMUM_WIRE_LENGTHS ' + json.dumps(summary, sort_keys=True, separators=(',', ':')))
        destination = os.environ.get('XENOVA_MAXIMUM_VECTOR_DIR')
        if destination:
            directory = Path(destination)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name, wire in vectors.items():
                with (directory / (name + '.pb')).open('xb') as output:
                    output.write(wire)
            record = dict(schema_version=1, scope='SYNTHETIC_MAXIMUM_CARDINALITY_CODEC_VECTORS_NOT_AUTHORITY',
                input_hardware_verified=False, global_wire_cap=32768, vectors=summary,
                world_trust_fixture=dict(now_pi_us=BASE+3000, expires_pi_us=BASE+1_000_000,
                    lower_offset_us=-1000, upper_offset_us=1000, max_error_us=2000),
                limitations=['not an exhaustive supremum over scalar assignments',
                             'not trajectory feasibility, mission admission, or measured timing'])
            with (directory / 'maximum-wire-results.json').open('xb') as output:
                output.write((json.dumps(record, sort_keys=True, indent=2) + '\n').encode('ascii'))


if __name__ == '__main__':
    unittest.main()
