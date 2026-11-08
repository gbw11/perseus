"""주차 U형·잘린 끝점·whole-cell 근거·후륜축 seed의 합성 반례다.

영상 추출/실차 보정/실제 빈칸 수락 시험이 아니다. 기대 좌표는 직접 정의한
미터 단위 선분이며 GT를 운영 입력으로 가져오지 않는다.
"""

from dataclasses import replace
import math
import struct
import unittest

from xenova_vehicle.perception.calibration import BodyModel
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.tape import TapeGraph, TapeJunction, TapeStrip
from xenova_vehicle.perception.parking import (ParkingAisle, ParkingProfile, SlotSource, ClearanceMap,
    PARTIAL, VALID, AMBIGUOUS, UNKNOWN, OCCUPIED, CLEAR_REGION, extract_slots, goal_candidates,
    to_map, evaluate_occupancy)
from xenova_vehicle.mapping.local_map import GridSpec


CALIBRATION = 'a' * 64
MAP = '10000000-0000-4000-8000-000000000001'
FRAME = '10000000-0000-4000-8000-000000000002'
ODOM = '10000000-0000-4000-8000-000000000003'
CLOCK = '10000000-0000-4000-8000-000000000004'


def source(sequence=1, *, captured=(900_000, 900_010), available=1_000_000):
    return SlotSource(FrameStamp('synthetic-camera', 'rgb', sequence, CLOCK, captured, available),
        CALIBRATION, MAP, FRAME, ODOM, 1, 1, True, input_kind='SYNTHETIC')


def strip(number, points, *, start='physical_junction', end='physical_junction', error=.001):
    return TapeStrip(number, tuple(tuple(float(v) for v in p) for p in points), (.02, .03), error, start, end)


def graph(*, source_value=None, end='physical_junction', start='physical_junction', back=True):
    original = source() if source_value is None else source_value
    values = [strip(0, ((0., 0.), (0., .6)), start=start, end=end),
              strip(1, ((.4, 0.), (.4, .6)), start=start, end=end)]
    if back:
        values.append(strip(2, ((0., .6), (.4, .6))))
    return TapeGraph(tuple(values), source_key=original.stamp.key, calibration_qualified=True)


def batch(*, original=None, graph_value=None, profile=ParkingProfile()):
    original = source() if original is None else original
    return extract_slots(graph(source_value=original) if graph_value is None else graph_value,
        ParkingAisle((-.1, 0.), (.9, 0.), 1, .001), source=original, body=BodyModel(), profile=profile)


def candidate():
    return batch().candidates[0]


def clearance(*, state=1, age_ms=0, support=1, permission=1, conflict=0, revision=1):
    spec = GridSpec(20, 20, .05, (-.2, -.2)); size = spec.width * spec.height
    return ClearanceMap((CALIBRATION, MAP, FRAME, ODOM, CLOCK), spec, 1_000_000, revision,
        bytes([state]) * size, bytes([support]) * size, bytes([permission]) * size,
        bytes([conflict]) * size, struct.pack('<I', age_ms) * size, 1)


class ParkingGeometryTests(unittest.TestCase):
    def test_observed_u_has_four_corners_and_original_frame_source(self):
        result = batch()
        self.assertTrue(result.complete)
        self.assertEqual(result.status, 'VALID')
        self.assertEqual(len(result.candidates), 1)
        value = result.candidates[0]
        self.assertEqual(value.geometry_state, VALID)
        self.assertEqual(value.corners, ((0., 0.), (.4, 0.), (.4, .6), (0., .6)))
        self.assertEqual(value.source, source())
        self.assertEqual(value.coordinates, 'BASE')
        self.assertFalse(result.metric_ready)
        self.assertFalse(result.live_authority)

    def test_fov_or_occluded_back_is_partial_even_with_a_white_cross_line(self):
        for kind in ('FOV_clip', 'occluded', 'predicted'):
            result = batch(graph_value=graph(end=kind))
            self.assertEqual(result.candidates[0].geometry_state, PARTIAL)
            self.assertEqual(result.candidates[0].back_evidence, 'missing_back')
            self.assertEqual(goal_candidates(result.candidates[0], body=BodyModel()), ())

    def test_fov_entry_at_the_policy_line_is_not_an_observed_aisle_connection(self):
        result = batch(graph_value=graph(start='FOV_clip'))
        self.assertEqual(result.candidates[0].geometry_state, PARTIAL)
        self.assertIn('ENTRY_OR_GEOMETRY_UNRESOLVED', result.candidates[0].reasons)

    def test_two_real_paint_ends_can_bound_the_depth_but_open_occluded_paint_cannot(self):
        observed = batch(graph_value=graph(end='observed_paint_end', back=False))
        self.assertEqual(observed.candidates[0].geometry_state, VALID)
        self.assertEqual(observed.candidates[0].back_evidence, 'observed_endpoints')
        missing = batch(graph_value=graph(end='occluded', back=False))
        self.assertEqual(missing.candidates[0].geometry_state, PARTIAL)
        self.assertFalse(missing.candidates[0].geometry_complete)

    def test_shared_middle_divider_keeps_two_bays_and_rejects_merged_pair(self):
        original = source()
        values = tuple(strip(i, ((x, 0.), (x, .6))) for i, x in enumerate((0., .35, .70)))
        result = batch(graph_value=TapeGraph(values, source_key=original.stamp.key, calibration_qualified=True))
        self.assertEqual(len(result.candidates), 2)
        self.assertTrue(all(c.geometry_state == VALID for c in result.candidates))
        self.assertTrue(all(c.width_interval_m[1] < .4 for c in result.candidates))

    def test_two_observed_back_roles_remain_ambiguous_without_best_score_selection(self):
        original = source()
        values = graph().strips + (strip(3, ((0., .5), (.4, .5))),)
        junctions = (TapeJunction(0, (0., .5), 3, 'physical_junction'),
                     TapeJunction(1, (.4, .5), 3, 'physical_junction'))
        result = batch(graph_value=TapeGraph(values, junctions, source_key=original.stamp.key, calibration_qualified=True))
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertEqual(len(result.candidates), 2)
        self.assertTrue(all(c.geometry_state == AMBIGUOUS for c in result.candidates))

    def test_hypothesis_cap_does_not_qualify_the_checked_prefix_as_complete(self):
        result = batch(profile=ParkingProfile(hypothesis_cap=1))
        self.assertFalse(result.complete)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertIn('SLOT_HYPOTHESIS_CAP', result.reasons)

    def test_source_mismatch_and_unqualified_calibration_are_not_hidden(self):
        with self.assertRaises(ValueError):
            batch(original=source(2), graph_value=graph())
        unqualified = batch(graph_value=replace(graph(), calibration_qualified=False))
        self.assertTrue(all(not g.metric_qualified for g in goal_candidates(unqualified.candidates[0], body=BodyModel())))

    def test_reverse_and_forward_seeds_use_rear_axle_overhang_and_are_not_feasibility(self):
        seeds = goal_candidates(candidate(), body=BodyModel())
        self.assertEqual(tuple(s.direction for s in seeds), ('REVERSE', 'FORWARD'))
        self.assertAlmostEqual(seeds[0].pose[1] - seeds[1].pose[1], .20)
        self.assertAlmostEqual(seeds[0].pose[2], -math.pi / 2)
        self.assertAlmostEqual(seeds[1].pose[2], math.pi / 2)
        self.assertTrue(all(s.d_interval_m[0] <= s.pose[1] <= s.d_interval_m[1] + .03 for s in seeds))
        self.assertTrue(all(not s.feasible for s in seeds))
        self.assertEqual(goal_candidates(candidate(), body=BodyModel(width=.6)), ())

    def test_map_transform_is_explicit_once_and_includes_the_capture_pose_bound(self):
        original = candidate()
        mapped = to_map(original, (1., 2., math.pi / 2), position_error_m=.01, yaw_error_rad=.01)
        self.assertEqual(mapped.corners[0], (1., 2.))
        self.assertGreater(mapped.position_bound_m, original.position_bound_m + .01)
        self.assertIs(mapped.source, original.source)
        with self.assertRaises(ValueError):
            to_map(mapped, (0., 0., 0.), position_error_m=0., yaw_error_rad=0.)
        with self.assertRaises(ValueError):
            evaluate_occupancy(original, clearance(), now_us=1_000_000)


class ParkingOccupancyTests(unittest.TestCase):
    def mapped(self):
        return to_map(candidate(), (0., 0., 0.), position_error_m=0., yaw_error_rad=0.)

    def test_original_grid_snapshot_is_copied_without_resetting_its_ages_or_source(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('NumPy가 없어 실제 map snapshot 경계를 실행할 수 없음')
        from xenova_vehicle.mapping.local_map import EvidenceGrid
        grid = EvidenceGrid(GridSpec(20, 20, .05, (-.2, -.2)), frame_id=FRAME,
            calibration_id=CALIBRATION, clock_id=CLOCK, kind='SYNTHETIC')
        domain = np.ones((20, 20), dtype=bool); grid.set_domain(domain, domain)
        # map 저장기의 positive-free 입력 계약을 합성으로 주입한다. 실제 ray 수락 시험이 아니다.
        grid.add_free(tuple((x, y) for y in range(20) for x in range(20)), source().stamp,
            view_id='synthetic-certificate', origin_xy=(0., -.1), certificate_complete=True)
        snapshot = grid.snapshot(1_000_000, clock_id=CLOCK)
        copied = ClearanceMap.from_snapshot(snapshot, snapshot_us=1_000_000, map_epoch=MAP,
            odom_epoch=ODOM, source_kinds=1)
        grid.state[8, 8] = 2
        self.assertEqual(evaluate_occupancy(self.mapped(), copied, now_us=1_000_000).state, CLEAR_REGION)
        self.assertEqual(struct.unpack_from('<I', copied.age_le32, 0)[0], 100)
        self.assertEqual(copied.source_domain, source().domain)
        with self.assertRaises(ValueError):
            ClearanceMap.from_snapshot(snapshot, snapshot_us=1_000_000, map_epoch=MAP, odom_epoch=ODOM, source_kinds=4)

    def test_every_required_whole_cell_needs_fresh_free_support_and_permission(self):
        value = self.mapped(); valid = evaluate_occupancy(value, clearance(), now_us=1_000_000)
        self.assertEqual(valid.state, CLEAR_REGION)
        self.assertGreater(valid.checked_cells, 0)
        self.assertEqual(valid.clearance_age_ms, 1)
        self.assertEqual(valid.observed_lower_us, 999_001)
        self.assertFalse(valid.feasible)
        for wrong in (clearance(state=0), clearance(permission=0), clearance(support=0), clearance(conflict=1)):
            self.assertEqual(evaluate_occupancy(value, wrong, now_us=1_000_000).state, UNKNOWN)

    def test_one_unknown_hole_blocks_clear_and_hit_anywhere_in_bay_wins(self):
        evidence = clearance(); states = bytearray(evidence.state)
        states[8 * 20 + 8] = 0
        self.assertEqual(evaluate_occupancy(self.mapped(), replace(evidence, state=bytes(states)), now_us=1_000_000).state, UNKNOWN)
        states[14 * 20 + 10] = 2
        required = ((.1, .1), (.2, .1), (.2, .2), (.1, .2))
        occupied = evaluate_occupancy(self.mapped(), replace(evidence, state=bytes(states)), now_us=1_000_000, required_region=required)
        self.assertEqual(occupied.state, OCCUPIED)
        self.assertGreater(occupied.hit_cells, 0)

    def test_cell_age_is_original_and_stale_republication_does_not_become_clear(self):
        evidence = clearance(age_ms=100)
        before = evaluate_occupancy(self.mapped(), evidence, now_us=1_100_000)
        after = evaluate_occupancy(self.mapped(), evidence, now_us=1_400_000)
        self.assertEqual(before.state, CLEAR_REGION)
        self.assertEqual(before.observed_lower_us, 899_001)
        self.assertEqual(after.state, UNKNOWN)
        self.assertEqual(evidence.snapshot_us, 1_000_000)

    def test_partial_unknown_capture_and_domain_change_do_not_create_empty_bays(self):
        partial = batch(graph_value=graph(end='occluded')).candidates[0]
        partial = to_map(partial, (0., 0., 0.), position_error_m=0., yaw_error_rad=0.)
        self.assertEqual(evaluate_occupancy(partial, clearance(), now_us=1_000_000).state, UNKNOWN)
        original = source(captured=None)
        value = batch(original=original).candidates[0]
        value = to_map(value, (0., 0., 0.), position_error_m=0., yaw_error_rad=0.)
        self.assertEqual(evaluate_occupancy(value, clearance(), now_us=1_000_000).state, UNKNOWN)
        with self.assertRaises(ValueError):
            evaluate_occupancy(self.mapped(), replace(clearance(), source_domain=('b' * 64, MAP, FRAME, ODOM, CLOCK)), now_us=1_000_000)


if __name__ == '__main__':
    unittest.main()
