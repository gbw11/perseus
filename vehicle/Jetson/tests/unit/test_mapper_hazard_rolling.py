"""실제 rolling grid/proof DTO의 전역 hazard 기억과 원 관측 결박을 검산한다.

원 clock/grid는 기존 software fixture이며 CELL/provenance/range는 명시한 합성 값이다.
현재 ROI 제어·캡슐의 의미 검사를 시험하며 실제 센서/SQL/scene 권한을 승인하지 않는다.
"""

from copy import deepcopy
from dataclasses import replace
import struct
import unittest
from uuid import UUID

import test_mapper_hazard as fixed_fixture
from xenova_vehicle.mapping.local_map import OCCUPIED
from xenova_vehicle.mapping.map_lattice import GridWindow
from xenova_vehicle.mapping.rolling_grid import configure_grid, pack_live_tiles, reframe_grid
from xenova_vehicle.runtime.atlas_proof import AtlasProofWindow
from xenova_vehicle.runtime.map_checkpoint import MapCheckpointCollector
from xenova_vehicle.runtime.mapper_hazard import MapperHazard
from xenova_vehicle.storage.contracts import StorageJob, ProvenanceRange
from xenova_vehicle.storage.records import CELL, PROVENANCE, ProvenanceRecord


def uid(n): return '40000000-0000-4000-8000-%012x' % n


class MapperHazardRollingTests(unittest.TestCase):
    def setUp(self):
        self.base = fixed_fixture.MapperHazardTests(methodName='runTest')
        self.base.setUp()
        self.fixture = self.base.fixture
        self.grid = self.fixture.grid
        self.window = GridWindow.from_spec(self.grid.spec)
        configure_grid(self.grid, self.window)
        self.owner = MapperHazard()
        self.pool = {}

    def frame(self, n=0): return self.base.frame(n)

    def refresh(self, draft, at): return self.base.refresh(draft, at)

    def hit(self, draft):
        x0, y0, x1, y1 = self.grid.spec.bounds(0, 0)
        x, y = (x0+x1)/2, (y0+y1)/2
        self.grid.add_hits(((x, y, x, y),), draft.frame.observation.depth.stamp,
            view_id='original-hit-'+str(draft.frame.observation_sequence), origin_xy=(0., 0.))

    def move(self, offset):
        self.pool.update(pack_live_tiles(self.grid, self.pool))
        reframe_grid(self.grid, self.window.shifted(offset), self.pool)

    def begin_hit(self):
        draft, at = self.frame()
        self.hit(draft)
        candidate = self.owner.prepare(self.refresh(draft, at), now_us=at)
        self.assertTrue(candidate.ready)
        self.assertEqual(candidate.revision, 2)
        self.owner.accept(candidate)
        return candidate

    def proof(self, draft, at, *, sequence=None):
        """현재 주소의 실제 typed helper와 원 record bytes다. SQL grant가 아닌 합성 range다."""
        context = draft.context
        job = StorageJob(context, uid(1), at-10, at+100_000)
        grant = ProvenanceRange(job, uid(2), 1000, 1128, 1128, at-5)
        collector = MapCheckpointCollector(draft.frame.scope, grant, now_us=at)
        collector._atlas_proof = AtlasProofWindow(self.grid.rolling_window, lambda *_: None)
        frame = draft.frame
        sequence = frame.observation_sequence if sequence is None else sequence
        current = frame.scope.context_record('METRIC')
        stream, source_sequence, device_us = frame.raw_keys[1]
        raw = struct.pack('<16sQQ', UUID(stream).bytes, source_sequence, device_us)
        record = ProvenanceRecord(1000, PROVENANCE.pack(bytes.fromhex(current.sha256), 2, 2, 1, 0,
            UUID(context.pi_host_boot).bytes, *frame.pi_interval, sequence, 0, 0, 0, 0,
            raw+bytes(96), 0, bytes(16)))
        collector._records[record.provenance_id] = record
        collector._contexts[current.sha256] = current
        gx, gy = self.grid.rolling_window.local_to_global(0)
        collector._atlas_proof.write(gx, gy, CELL.pack(0, 1, 2, 0., 1., .01,
            record.provenance_id, *frame.pi_interval, sequence, self.grid.revision, 0))
        return replace(draft, collector=collector)

    def test_negative_shift_return_keeps_offscreen_bits_and_does_not_issue_hazard_or_source_progress(self):
        original = self.begin_hit()
        body = original.snapshot.global_occupied[0][1]
        original_sources = self.owner.snapshot.sources
        for offset in ((-65, -1), (-64, -64), (-1, -1), (0, 0)):
            self.move(offset)
            duplicate, at = self.frame()
            candidate = self.owner.prepare(duplicate, now_us=at)
            self.assertTrue(candidate.ready)
            self.assertFalse(candidate.changed or candidate.source_advanced or candidate.revoke_required)
            self.assertIsNone(candidate.witness)
            self.assertEqual(candidate.revision, 2)
            self.assertEqual(candidate.snapshot.window.offset_xy, offset)
            self.assertIs(dict(candidate.snapshot.global_occupied)[(0, 0)], body)
            self.assertEqual(candidate.snapshot.sources, original_sources)
            self.owner.accept(candidate)
        self.assertTrue(any(self.owner.snapshot.occupied))
        self.assertEqual(int(self.grid.state[0, 0]), OCCUPIED)

    def test_off_roi_new_hit_and_raw_unknown_preserve_all_previous_global_occupied(self):
        initial = self.begin_hit()
        old_body = dict(initial.snapshot.global_occupied)[(0, 0)]
        self.move((-65, -1))
        current, at = self.frame(1)
        self.hit(current)
        changed = self.owner.prepare(self.refresh(current, at), now_us=at)
        self.assertTrue(changed.ready and changed.changed)
        self.assertEqual(changed.revision, 3)
        self.assertEqual(set(dict(changed.snapshot.global_occupied)), {(0, 0), (-2, -1)})
        self.assertIs(dict(changed.snapshot.global_occupied)[(0, 0)], old_body)
        self.owner.accept(changed)
        self.grid.occupied_ttl_us = 1
        self.grid.state[0, 0] = 0
        unknown, at = self.frame(2)
        kept = self.owner.prepare(unknown, now_us=at)
        self.assertTrue(kept.ready)
        self.assertFalse(kept.changed)
        self.assertEqual(kept.revision, 3)
        self.assertTrue(any(kept.snapshot.occupied))
        self.assertEqual(kept.snapshot.global_occupied, changed.snapshot.global_occupied)
        self.owner.accept(kept)

    def test_current_free_needs_original_proof_sequence_even_when_record_pi_and_raw_j_match(self):
        self.begin_hit()
        for n, mode in ((1, 'missing'), (2, 'old-sequence'), (3, 'current')):
            draft, at = self.frame(n)
            self.grid.add_free(((0, 0),), draft.frame.observation.depth.stamp,
                view_id='new-free-view-'+str(n), origin_xy=(.1, 0.), certificate_complete=True)
            draft = self.refresh(draft, at)
            if mode != 'missing':
                draft = self.proof(draft, at, sequence=draft.frame.observation_sequence-1 if mode == 'old-sequence' else None)
            candidate = self.owner.prepare(draft, now_us=at)
            self.assertTrue(candidate.ready)
            if mode == 'current':
                self.assertTrue(candidate.changed)
                self.assertEqual(candidate.revision, 3)
                self.assertFalse(any(candidate.snapshot.occupied))
                self.assertEqual(candidate.snapshot.global_occupied, ())
            else:
                self.assertFalse(candidate.changed)
                self.assertEqual(candidate.revision, 2)
                self.assertTrue(any(candidate.snapshot.occupied))
            self.owner.accept(candidate)

    def test_cancelled_global_hit_burns_floor_without_changing_original_bits_or_hwm(self):
        self.begin_hit()
        original = self.owner.snapshot
        self.move((64, 0))
        draft, at = self.frame(1)
        before_hit = deepcopy(self.grid)
        self.hit(draft)
        pending = self.owner.prepare(self.refresh(draft, at), now_us=at)
        self.assertEqual(pending.revision, 3)
        self.owner.cancel(pending)
        self.assertEqual(self.owner.snapshot.revision, 3)
        self.assertIs(self.owner.snapshot.global_occupied, original.global_occupied)
        self.assertIs(self.owner.snapshot.sources, original.sources)
        # 취소한 grid mutation은 채택하지 않는다. ROI 이동 자체는 다음 실제 관측에서 유지할 수 있다.
        self.fixture.grid = self.fixture.pipeline.grid = self.grid = before_hit
        unchanged, at = self.frame(2)
        stable = self.owner.prepare(unchanged, now_us=at)
        self.assertTrue(stable.ready)
        self.assertFalse(stable.changed)
        self.assertEqual(stable.revision, 3)
        self.owner.accept(stable)
        again, at = self.frame(3)
        self.hit(again)
        changed = self.owner.prepare(self.refresh(again, at), now_us=at)
        self.assertEqual(changed.revision, 4)
        self.assertTrue(changed.changed and changed.revoke_required)
        self.owner.accept(changed)

    def test_global_occupied_cap_rejects_129th_tile_without_mutating_old_body_refs(self):
        self.begin_hit()
        original = self.owner.snapshot
        body = original.global_occupied[0][1]
        at_cap = tuple(((i, 0), body) for i in range(-127, 1))
        # 예산 한계만 검사하는 명시 private 주입이다. 실제 복원/관측 승인으로 쓰지 않는다.
        self.owner._accepted = replace(original, global_occupied=at_cap)
        self.move((64, 0))
        draft, at = self.frame(1)
        self.hit(draft)
        rejected = self.owner.prepare(self.refresh(draft, at), now_us=at)
        self.assertFalse(rejected.ready)
        self.assertTrue(rejected.revoke_required)
        self.assertEqual(rejected.revision, 2)
        self.assertIs(self.owner.snapshot.global_occupied, at_cap)
        self.assertTrue(all(value is body for _, value in self.owner.snapshot.global_occupied))
        self.owner.cancel(rejected)
        with self.assertRaises(ValueError):
            replace(original, global_occupied=at_cap+(((1, 0), body),))


if __name__ == '__main__':
    unittest.main()
