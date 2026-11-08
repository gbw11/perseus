"""실제 typed grid와 원 source gate로 fixed ROI hazard 변화를 검산한다.

보정·시계·정합은 기존 software fixture다. 실제 센서/scene 사용권 수락이 아니며
certificate_complete 제어값으로 Grid API 동작만 검사한다. OS/SQL/장치를 열지 않는다.
"""

from dataclasses import replace
import unittest

import test_mapper_world as world_fixture
import test_road_basis as basis_fixture
from xenova_vehicle.mapping.local_map import EvidenceGrid, GridSpec, OCCUPIED, FREE
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_hazard import MapperHazard
from xenova_vehicle.runtime.resident_samples import CaptureSample


class MapperHazardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = world_fixture.MapperWorldTests(methodName='runTest')
        self.fixture.setUp()
        self.owner = MapperHazard()

    def frame(self, number=0):
        f = self.fixture
        original = f.draft
        at = f.now + number*1000
        observation = original.frame.observation
        def image(value):
            stamp = replace(value.stamp, sequence=value.stamp.sequence+number,
                captured_us=tuple(v+number*1000 for v in value.stamp.captured_us), available_us=at)
            return replace(value, stamp=stamp)
        observation = replace(observation, rgb=image(observation.rgb), depth=image(observation.depth))
        rgb_new = f.pipeline.rgb_gate.accept(observation.rgb.stamp, observation.rgb.sha256)
        depth_new = f.pipeline.depth_gate.accept(observation.depth.stamp, observation.depth.sha256)
        sample = original.original_job.sample
        rgb = replace(sample.rgb, sequence=observation.rgb.stamp.sequence, received_j_us=at,
            device_us=sample.rgb.device_us+number*1000, sdk_us=sample.rgb.sdk_us+number*1000)
        depth = replace(sample.depth, sequence=observation.depth.stamp.sequence, received_j_us=at,
            device_us=sample.depth.device_us+number*1000, sdk_us=sample.depth.sdk_us+number*1000)
        clocks = {name: dict(bridge_sample=dict(j_before_us=str(at), sdk_us=str(at), j_after_us=str(at)),
            sdk_host_timestamp_j_bracket_us=list(map(str, observation.capture_interval)))
            for name in ('rgb_video', 'depth_right')}
        item = MapperInput(world_fixture.uid(50+number), original.original_job.source_generation,
            CaptureSample(rgb, depth, None, None, clocks), at+100_000,
            context_bytes=original.original_job.context_bytes)
        keys = tuple((key[0], raw.sequence, raw.device_us) for key, raw in zip(original.frame.raw_keys, (rgb, depth)))
        frame = replace(original.frame, observation=observation, observation_sequence=71+number, accepted_us=at,
            raw_keys=keys, pi_interval=original.frame.clock_lease.j_interval_to_pi(observation.capture_interval, at))
        result = replace(original.result, summary=dict(original.result.summary, rgb_updated=rgb_new,
            depth_updated=depth_new, rgb_source_key=observation.rgb.stamp.key, now_us=at),
            map_snapshot=f.grid.snapshot(at, clock_id=f.grid.clock_id))
        return replace(original, original_job=item, frame=frame, result=result), at

    def refresh(self, draft, at):
        grid = draft.pipeline.grid
        return replace(draft, result=replace(draft.result, map_snapshot=grid.snapshot(at, clock_id=grid.clock_id)))

    def test_cold_empty_one_addition_once_and_duplicate_do_not_refresh_or_alias_grid_revision(self):
        first, at = self.frame()
        baseline = self.owner.prepare(first, now_us=at)
        self.assertTrue(baseline.ready)
        self.assertEqual(baseline.revision, 1)
        self.assertFalse(baseline.changed or baseline.revoke_required)
        self.owner.accept(baseline)
        added, at = self.frame(1)
        grid = added.pipeline.grid
        grid.add_hits(((.05, .05, .051, .051),), added.frame.observation.depth.stamp,
            view_id='explicit-software-hit', origin_xy=(0., 0.))
        added = self.refresh(added, at)
        candidate = self.owner.prepare(added, now_us=at)
        self.assertEqual(candidate.revision, 2)
        self.assertTrue(candidate.changed and candidate.revoke_required and candidate.source_advanced)
        self.owner.accept(candidate)
        accepted = self.owner.snapshot
        for _ in range(3):
            grid.add_hits(((.05, .05, .051, .051),), added.frame.observation.depth.stamp,
                view_id='same-original-hit', origin_xy=(0., 0.))
        duplicate = self.owner.prepare(self.refresh(added, at), now_us=at)
        self.assertTrue(duplicate.ready)
        self.assertFalse(duplicate.changed or duplicate.source_advanced or duplicate.revoke_required)
        self.assertIsNone(duplicate.witness)
        self.assertIs(duplicate.snapshot, accepted)
        self.assertGreater(grid.revision, accepted.revision)
        self.assertEqual(duplicate.revision, 2)
        self.owner.accept(duplicate)

    def test_expired_unknown_keeps_raw_occupied_and_only_new_positive_free_removes(self):
        first, at = self.frame()
        grid = first.pipeline.grid
        grid.add_hits(((.05, .05, .051, .051),), first.frame.observation.depth.stamp,
            view_id='explicit-software-hit', origin_xy=(0., 0.))
        initial = self.owner.prepare(self.refresh(first, at), now_us=at)
        self.assertEqual(initial.revision, 2)
        self.owner.accept(initial)
        grid.occupied_ttl_us = 1
        newer, at = self.frame(1)
        self.assertEqual(int(newer.result.map_snapshot['arrays']['state'][7, 2]), 0)
        self.assertEqual(int(grid.state[7, 2]), OCCUPIED)
        expired = self.owner.prepare(newer, now_us=at)
        self.assertEqual(expired.revision, 2)
        self.assertFalse(expired.changed)
        self.owner.accept(expired)
        cleared, at = self.frame(2)
        grid.add_free(((2, 7),), cleared.frame.observation.depth.stamp, view_id='new-independent-software-view',
            origin_xy=(.1, 0.), certificate_complete=True)
        self.assertEqual(int(grid.state[7, 2]), FREE)
        candidate = self.owner.prepare(self.refresh(cleared, at), now_us=at)
        self.assertEqual(candidate.revision, 3)
        self.assertTrue(candidate.changed and candidate.revoke_required)
        self.owner.accept(candidate)
        self.assertFalse(any(self.owner.snapshot.occupied))

    def test_cancel_unsafe_expiry_and_geometry_change_never_reset_baseline_or_restore_scene(self):
        first, at = self.frame()
        self.owner.accept(self.owner.prepare(first, now_us=at))
        original = self.owner.snapshot
        added, at = self.frame(1)
        grid = added.pipeline.grid
        grid.add_hits(((.05, .05, .051, .051),), added.frame.observation.depth.stamp,
            view_id='software-hit', origin_xy=(0., 0.))
        pending = self.owner.prepare(self.refresh(added, at), now_us=at)
        self.assertTrue(self.owner.cancel(pending))
        self.assertEqual(self.owner.snapshot.revision, 2)
        self.assertIs(self.owner.snapshot.occupied, original.occupied)
        self.assertIs(self.owner.snapshot.sources, original.sources)
        # 취소한 후보 grid는 채택하지 않는다. 같은 고정 좌표의 원 EMPTY map에서 다음 관측을 받는다.
        unchanged_grid = EvidenceGrid(grid.spec, frame_id=grid.frame_id, calibration_id=grid.calibration_id,
            clock_id=grid.clock_id, kind=grid.kind)
        unchanged_grid.set_domain(grid.support, grid.permission)
        self.fixture.grid = self.fixture.pipeline.grid = unchanged_grid
        unchanged, at = self.frame(2)
        same = self.owner.prepare(unchanged, now_us=at)
        self.assertTrue(same.ready and same.source_advanced)
        self.assertFalse(same.changed)
        self.assertEqual(same.revision, pending.revision)
        self.owner.accept(same)
        self.assertFalse(any(self.owner.snapshot.occupied))
        again, at = self.frame(3)
        unchanged_grid.add_hits(((.05, .05, .051, .051),), again.frame.observation.depth.stamp,
            view_id='next-actual-software-hit', origin_xy=(0., 0.))
        next_changed = self.owner.prepare(self.refresh(again, at), now_us=at)
        self.assertTrue(next_changed.changed and next_changed.revoke_required)
        self.assertEqual(next_changed.revision, pending.revision+1)
        self.owner.accept(next_changed)
        expired = self.owner.prepare(added, now_us=added.original_job.expires_us)
        self.assertFalse(expired.ready)
        self.assertTrue(expired.revoke_required)
        self.owner.cancel(expired)
        moved = EvidenceGrid(GridSpec(20, 20, .1, (-.1, -.7)), frame_id=grid.frame_id,
            calibration_id=grid.calibration_id, clock_id=grid.clock_id, kind='LIVE')
        self.fixture.pipeline.grid = moved
        changed = self.owner.prepare(self.refresh(added, at), now_us=at)
        self.assertFalse(changed.ready)
        self.assertEqual(changed.reason, 'GEOMETRY_CHANGED')
        self.assertTrue(changed.revoke_required)
        self.assertFalse(self.owner.accept(changed))
        self.assertEqual(self.owner.snapshot.revision, next_changed.revision)

    def test_actual_complete_source_blocks_cold_empty_and_never_treats_restored_unknown_as_clear(self):
        f = self.fixture
        complete, _ = basis_fixture.restore_fixture(f.basis_owner, f.state.lane, f.draft.frame.scope)
        owner = MapperHazard(complete=complete)
        original, at = self.frame()
        rejected = owner.prepare(original, now_us=at)
        self.assertFalse(rejected.ready)
        self.assertEqual(rejected.reason, 'RESTORE_NOT_READY')
        self.assertTrue(rejected.revoke_required)
        self.assertIsNone(rejected.revision)
        owner.cancel(rejected)
        with self.assertRaises(ValueError):
            owner.restore_complete(complete,pipeline=f.pipeline,
                scope=f.draft.frame.scope,now_us=at)
        rejected=owner.prepare(original,now_us=at)
        self.assertFalse(rejected.ready)
        self.assertEqual(rejected.reason,'RESTORE_NOT_READY')
        self.assertFalse(owner.accept(rejected))
        self.assertIsNone(owner.snapshot)


if __name__ == '__main__':
    unittest.main()
