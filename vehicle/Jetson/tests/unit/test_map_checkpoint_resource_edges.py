"""원 frame cache와 durable tile 표현의 자원 경계를 검산한다.

기존 software capture/정합 및 실제 SQLite CP fixture를 사용한다. 추가 부모는 같은
fixture가 수락한 원 PI_ODOM 행이며, 실제 세 센서 표본 취득이나 장기 loop 수락은 아니다.
rolling 시험은 실제 CP tile의 reference4tuple 표현만 사용하며 archive roots를 발명하지 않는다.
"""

from dataclasses import replace
from hashlib import sha256
import os
import unittest

from xenova_vehicle.runtime.map_checkpoint import MapCheckpointNotReady
from xenova_vehicle.storage.records import PROVENANCE, TileVersion

import test_alignment_anchor_provenance as anchor_fixture


@unittest.skipUnless(os.name == 'posix', '원 실제 SQLite fixture는 대상 POSIX에서 검사한다')
class MapCheckpointResourceEdgesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = anchor_fixture.AlignmentAnchorProvenanceTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.collector = self.fixture.collector

    def three_parent_frame(self):
        result, pose, frame = self.fixture.next_result()
        self.assertEqual(len(frame.parents), 1)
        earlier = sorted((record for record in self.collector._records.values()
            if PROVENANCE.unpack(record.payload)[2] == 0 and record.source_mask == 16
            and record.provenance_id != frame.parents[0].provenance_id),
            key=lambda record: record.provenance_id)
        self.assertGreaterEqual(len(earlier), 2)
        parents = frame.parents + tuple(earlier[:2])
        contexts = {context.sha256: context for context in frame.contexts}
        for record in parents:
            contexts[record.context_sha256] = self.collector._contexts[record.context_sha256]
        return result, pose, replace(frame, parents=parents, contexts=tuple(contexts.values()))

    def test_three_parent_repeated_callbacks_allocate_one_group_and_preserve_originals(self):
        collector = self.collector
        result, pose, frame = self.three_parent_frame()
        parent_bytes = {record.provenance_id: record.payload for record in frame.parents}
        pin = collector._propagation_anchor_record
        anchor = collector._records[pin[1]]
        cursor = collector._next
        before_groups = {key for key, record in collector._records.items()
            if PROVENANCE.unpack(record.payload)[2] == 4}
        collector.observe_result(frame, result, self.fixture.pipeline, raw_pose=pose)
        groups = {key for key, record in collector._records.items()
            if PROVENANCE.unpack(record.payload)[2] == 4} - before_groups
        self.assertEqual(len(groups), 1)
        self.assertEqual(collector._records[next(iter(groups))].parents,
            tuple(record.provenance_id for record in frame.parents))
        # 새 fan-in/RGB/REG/GEOM 이후 같은 원 frame query는 ID를 더 쓰지 않는다.
        self.assertEqual(collector._next - cursor, 4)
        roots = collector._frame_roots(frame, self.fixture.pipeline, pose)
        cursor, records = collector._next, dict(collector._records)
        contexts = {key: value.payload for key, value in collector._contexts.items()}
        for _ in range(4):
            collector.observe_result(frame, result, self.fixture.pipeline, raw_pose=pose)
            self.assertIs(collector._frame_roots(frame, self.fixture.pipeline, pose), roots)
        self.assertEqual(collector._next, cursor)
        self.assertEqual(collector._records, records)
        self.assertEqual({key: value.payload for key, value in collector._contexts.items()}, contexts)
        self.assertEqual({key: collector._records[key].payload for key in parent_bytes}, parent_bytes)
        self.assertEqual(collector._propagation_anchor_record, pin)
        self.assertIs(collector._records[pin[1]], anchor)
        self.assertEqual(sha256(anchor.payload).hexdigest(), pin[2])

    def test_cache_hit_still_rejects_original_parent_conflict_and_changed_anchor_pin(self):
        collector = self.collector
        result, pose, frame = self.three_parent_frame()
        collector.observe_result(frame, result, self.fixture.pipeline, raw_pose=pose)
        cursor, pin = collector._next, collector._propagation_anchor_record
        original = frame.parents[0]
        fields = list(PROVENANCE.unpack(original.payload))
        fields[8] += 1
        collector._records[original.provenance_id] = replace(original, payload=PROVENANCE.pack(*fields))
        with self.assertRaisesRegex(MapCheckpointNotReady, 'original frame parent conflict'):
            collector._frame_roots(frame, self.fixture.pipeline, pose)
        self.assertEqual(collector._next, cursor)
        collector._records[original.provenance_id] = original
        collector._propagation_anchor_record = (pin[0], pin[1], '0' * 64)
        with self.assertRaisesRegex(MapCheckpointNotReady, 'registration pin changed'):
            collector._frame_roots(frame, self.fixture.pipeline, pose)
        self.assertEqual(collector._next, cursor)
        collector._propagation_anchor_record = pin
        self.assertTrue(collector._frame_roots(frame, self.fixture.pipeline, pose))
        self.assertEqual(collector._next, cursor)

    def test_durable_reference_tuple_closes_without_dropping_original_proof(self):
        collector = self.collector
        tile = next(iter(collector._durable_tiles.values()))
        self.assertIs(type(tile), TileVersion)
        reference = tile.reference
        self.assertEqual(len(reference), 4)
        # rolling COMMITTED가 보유하는 실제4tuple 표현이다. tile 본문/roots를 새로 읽지 않는다.
        collector._durable_tiles = {reference[:2]: reference}
        records, contexts = collector._records, collector._contexts
        original_bytes = {key: record.payload for key, record in records.items()}
        cursor, pin = collector._next, collector._propagation_anchor_record
        with self.assertRaisesRegex(MapCheckpointNotReady, 'durable tile provenance roots unavailable'):
            collector._trim()
        self.assertIs(collector._records, records)
        self.assertIs(collector._contexts, contexts)
        self.assertEqual({key: record.payload for key, record in collector._records.items()}, original_bytes)
        self.assertEqual(collector._durable_tiles, {reference[:2]: reference})
        self.assertEqual(collector._next, cursor)
        self.assertEqual(collector._propagation_anchor_record, pin)

    def test_nonrolling_tile_keeps_original_parent_closure_during_trim(self):
        collector = self.collector
        tiles = tuple(collector._durable_tiles.values())
        self.assertTrue(tiles)
        self.assertTrue(all(type(tile) is TileVersion for tile in tiles))
        roots = {identity for tile in tiles for identity in tile.provenance_ids}
        self.assertTrue(roots)
        before = dict(collector._records)
        required = self.fixture.closure(before, roots)
        cursor, pin = collector._next, collector._propagation_anchor_record
        collector._trim()
        self.assertTrue(required <= set(collector._records))
        for identity in required:
            self.assertIs(collector._records[identity], before[identity])
        self.assertEqual(collector._next, cursor)
        self.assertEqual(collector._propagation_anchor_record, pin)
        self.assertEqual(tuple(collector._durable_tiles.values()), tiles)


if __name__ == '__main__':
    unittest.main()
