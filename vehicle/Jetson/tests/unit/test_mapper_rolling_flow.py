"""원 software 센서/clock에서 rolling mutation과 실제 SQLite CP/LOAD를 잇는다.

SensorState와 provenance grant/mailbox/SQLite는 기존 연결 fixture를 사용한다.
depth와 tape 출력만 명시 typed 경계 stub이다. 새 GT pose, 실제 카메라 보정,
FREE/scene 사용권, 처리율 또는 COMPLETE warm 설치 수락을 주장하지 않는다.
"""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
import unittest
from unittest.mock import patch

import test_mapper_mapping as mapping_fixtures
from xenova_drive_world import schema
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_profile import MapperProfile
from xenova_vehicle.runtime.source_identity import scene_ref_fields
from xenova_vehicle.storage.contracts import StorageJob, canonical
from xenova_vehicle.storage.mapper_state import decode_mapper_state
from xenova_vehicle.storage.records import CELL, PROVENANCE


@unittest.skipUnless(os.name == 'posix', '실제 SQLite/writer lock은 대상 POSIX에서 검사한다')
class MapperRollingFlowTests(unittest.TestCase):
    def setUp(self):
        # TestCase를 상속하지 않아 기존 시험 메서드를 중복 발견하지 않는다.
        self.f = mapping_fixtures.MapperMappingTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        settings = self.f.profile.to_dict()
        settings['pipeline']['grid'].update(width=16, height=16, origin_xy=[0., 0.])
        settings['rolling_roi'] = dict(margin_cells=4, lookahead_cells=0)
        self.f.profile = MapperProfile(settings)
        self.f.mapping = self.f.owner()

    def pipeline(self):
        return PerceptionPipeline(self.f.fixture.calibration, self.f.profile.pipeline,
                                  rolling_policy=self.f.profile.rolling_roi)

    def frame(self, pipeline, *, sequence=12):
        item, observation = self.f.item()
        if sequence != observation.rgb.stamp.sequence:
            # 다른 software capture key다. 원 clock bracket/Vehicle/pose 시각은 그대로다.
            sample = replace(item.sample, rgb=replace(item.sample.rgb, sequence=sequence),
                             depth=replace(item.sample.depth, sequence=sequence))
            item = MapperInput(mapping_fixtures.uid(), item.source_generation, sample,
                               item.expires_us, context_bytes=item.context_bytes)
            observation = replace(observation,
                rgb=replace(observation.rgb, stamp=replace(observation.rgb.stamp, sequence=sequence)),
                depth=replace(observation.depth, stamp=replace(observation.depth.stamp, sequence=sequence)))
        f = self.f
        draft = f.mapping.prepare_frame(item, pipeline, observation, f.inputs, f.fixture.state, f.now)
        self.assertTrue(draft.ready, draft.reason)
        self.assertIsNotNone(draft.roi_reframe)
        # Y<0인 front hit로 signed global tile 주소를 실제 mutation 경로에 전달한다.
        box = PointBounds((.001, -.051, .1), (.002, -.049, .2), True)
        depth = DepthEvidence(observation.depth.stamp, f.fixture.calibration.sha256,
            'LIVE', (box,), (), 1, 0, 0, 0, 0, 0, True, 'HIT_ONLY')
        graph = TapeGraph((), status='UNKNOWN', complete=False, calibration_qualified=True)
        with patch('xenova_vehicle.perception.depth.analyze_depth', return_value=depth), \
                patch('xenova_vehicle.perception.pipeline.extract_tape', return_value=graph):
            result = pipeline.process(observation, pose=f.inputs.pose, now_us=f.now,
                evidence_observer=draft.observer, roi_reframe=draft.roi_reframe,
                localization_valid_for_us=100_000,
                localization_position_drift_mps=.001, localization_yaw_drift_rps=.001)
        finished = f.mapping.finish_frame(draft, result, now_us=f.now)
        self.assertTrue(finished.ready, finished.reason)
        return finished

    @staticmethod
    def grid_values(grid):
        names = ('state', 'observed_us', 'time_known', 'support', 'permission', 'conflict',
                 'unordered_hit', 'hit_available_us', 'hit_origin')
        return (grid.spec, grid.rolling_window, grid.revision, grid.applied_hits,
                grid.applied_free, tuple((name, getattr(grid, name).tobytes()) for name in names),
                dict(grid.hit_view))

    @staticmethod
    def pool_values(owner):
        # generation은 원 READ lease의 시작/종료만으로도 변하므로 내용과 원 ref를 비교한다.
        refs = owner.pool.current_refs()
        if not refs:
            return refs, ()
        lease = owner.pool.lease(refs, purpose='READ')
        try:
            bodies = tuple((ref, sha256(owner.pool.payload(ref, lease=lease)).hexdigest()) for ref in refs)
        finally:
            owner.pool.release(lease)
        return refs, bodies

    def test_cold_original_grant_signed_proof_adoption_and_actual_sql_checkpoint_load(self):
        f = self.f
        original_grant = f.grant()
        owner = f.mapping._atlas
        draft = self.frame(self.pipeline())
        window = draft.pipeline.grid.rolling_window
        self.assertEqual(window.offset_xy, (-8, -8))
        self.assertIs(draft.collector._atlas_owner, owner)
        self.assertTrue(draft.collector._cells)
        original_cells = dict(draft.collector._cells)
        self.assertTrue(any(col < 0 or row < 0 for col, row in
                            (window.local_to_global(index) for index in original_cells)))
        for index, raw in original_cells.items():
            col, row = window.local_to_global(index)
            self.assertEqual(draft.collector._atlas_proof.read(col, row), raw)
            self.assertEqual(CELL.unpack(raw)[7:10], (*draft.frame.pi_interval, 12))
        self.assertFalse(f.mapping.collector._cells)
        f.mapping.accept(draft)
        collector = f.mapping.collector
        self.assertIs(collector.grant.request, original_grant)
        self.assertEqual(owner.window, window)
        cursor = collector._next
        original_refs = tuple(sorted(item.reference for item in owner.metadata()))
        self.assertTrue(any(x < 0 or y < 0 for x, y, _, _ in original_refs))

        request = f.mapping.queue_checkpoint(StorageJob(f.context, mapping_fixtures.uid(),
            f.now, f.now + 5_000_000), now_us=f.now)
        snapshot = collector._draft_snapshot
        self.assertFalse(snapshot.all_tiles)
        self.assertEqual(snapshot.tile_references, request.payload.tile_references)
        self.assertEqual(tuple(tile.reference for tile in snapshot.full_tiles()), original_refs)
        expected = {tile.reference: tile.payload for tile in request.payload.tiles}
        self.assertEqual(tuple(sorted(expected)), tuple(sorted(original_refs)))
        self.assertGreaterEqual(owner.pool.stats()['owner_pins'], 1)
        self.assertIsNone(f.mailbox.main_peek())
        f.mapping.maintenance(now_us=f.now)
        dispatch = f.mailbox.main_take(current_context_sha256=f.context.sha256, now_us=f.now)
        self.assertIs(dispatch.request, request)
        receipt = f.store.commit(request.job, request.payload, current_context_sha256=f.context.sha256)
        self.assertEqual(receipt.status, 'COMMITTED')
        f.mailbox.main_complete(dispatch, receipt, completed_us=f.now)
        f.mailbox.main_release(dispatch)
        self.assertEqual(f.mapping.maintenance(now_us=f.now), 'HEAD_LOAD_OFFERED')
        self.assertEqual(owner.pool.stats()['dirty_evidence_versions'], 0)
        dispatch = f.mailbox.main_take(current_context_sha256=f.context.sha256, now_us=f.now)
        loaded = f.store.load(dispatch.request, current_context_sha256=f.context.sha256)
        self.assertEqual(loaded.payload.manifest, request.payload.manifest)
        self.assertEqual({tile.reference: tile.payload for tile in loaded.payload.tiles}, expected)
        records = {record.provenance_id: record for record in loaded.payload.evidence.provenance}
        tiles = {(tile.tile_x, tile.tile_y): tile for tile in loaded.payload.tiles}
        for index, raw in original_cells.items():
            col, row = window.local_to_global(index)
            tx, cx = divmod(col, 64)
            ty, cy = divmod(row, 64)
            tile = tiles[(tx, ty)]
            self.assertEqual(tile.payload[(cy * 64 + cx) * CELL.size:(cy * 64 + cx + 1) * CELL.size], raw)
            values = CELL.unpack(raw)
            self.assertEqual(PROVENANCE.unpack(records[values[6]].payload)[6:9], values[7:10])
            self.assertEqual(sha256(tile.payload).hexdigest(), tile.sha256)
        state, _ = decode_mapper_state(json.loads(loaded.payload.manifest)['scene'],
                                       loaded.payload.evidence.contexts)
        self.assertEqual(state['atlas']['roi_origin_cell'], list(window.offset_xy))
        self.assertEqual(state['tile_revision_highwater'], owner.tile_revision_highwater)
        f.mailbox.main_complete(dispatch, loaded, completed_us=f.now)
        f.mailbox.main_release(dispatch)
        f.mapping.maintenance(now_us=f.now)
        self.assertTrue(f.mapping._head_ready)
        self.assertEqual(collector._next, cursor)
        self.assertIs(collector.grant.request, original_grant)

    def test_cancel_preserves_accepted_grid_pool_proof_and_burns_candidate_ids(self):
        f = self.f
        f.grant()
        first = self.frame(self.pipeline())
        f.mapping.accept(first)
        accepted = f.mapping.collector
        owner = f.mapping._atlas
        grid = f.mapping._pipeline.grid
        proof = accepted._atlas_proof
        before_grid = self.grid_values(grid)
        before_pool = self.pool_values(owner)
        before_cells = dict(proof.cells)
        before_bits = dict(proof.unresolved)
        first_free_id = accepted._next
        candidate = self.frame(deepcopy(f.mapping._pipeline), sequence=13)
        burned = candidate.collector._next
        self.assertGreater(burned, first_free_id)
        self.assertIs(f.mapping.collector, accepted)
        f.mapping.cancel(candidate)
        self.assertIs(f.mapping._pipeline.grid, grid)
        self.assertIs(accepted._atlas_proof, proof)
        self.assertEqual(self.grid_values(grid), before_grid)
        self.assertEqual(self.pool_values(owner), before_pool)
        self.assertEqual(dict(proof.cells), before_cells)
        self.assertEqual(dict(proof.unresolved), before_bits)
        self.assertEqual(accepted._next, first_free_id)
        self.assertEqual(f.mapping._cursor_floor, burned)
        retry = self.frame(deepcopy(f.mapping._pipeline), sequence=13)
        new_ids = set(retry.collector._records) - set(accepted._records)
        self.assertTrue(new_ids)
        self.assertGreaterEqual(min(new_ids), burned)
        f.mapping.cancel(retry)

    def test_scene_only_publication_preserves_atlas_lattice_pool_and_original_raw_grant(self):
        f = self.f
        original = f.grant()
        draft = self.frame(self.pipeline())
        f.mapping.accept(draft)
        owner = f.mapping._atlas
        collector = f.mapping.collector
        grid = f.mapping._pipeline.grid
        raw = f.mapping.raw
        proof = collector._atlas_proof
        before_pool = self.pool_values(owner)
        before_grid = self.grid_values(grid)
        before_cells = dict(proof.cells)
        cursor = collector._next
        binding = dict(f.fixture.binding, source_binding_revision='2',
            scene_admission_ref=scene_ref_fields(schema.SceneAdmissionRef(
                admission_id=bytes([5] * 16), admission_revision=1,
                survey_id=bytes([6] * 16), survey_revision=1, policy=1,
                coverage_profile_sha256=bytes([7] * 32), allow_restore_after_mapper_restart=True)))
        changed = replace(f.context, source_binding=canonical(binding))
        f.port.publish(changed)
        f.mapping.maintenance(now_us=f.now + 1)
        self.assertIs(f.mapping._atlas, owner)
        self.assertIs(f.mapping.raw, raw)
        self.assertIs(f.mapping.collector, collector)
        self.assertIs(collector._atlas_proof, proof)
        self.assertIs(collector.grant.request, original)
        self.assertIs(f.mapping._range_request, original)
        self.assertEqual(collector.scope.context, changed)
        self.assertTrue(any(scope.context==f.context for scope in collector.scope._historical))
        self.assertIsNotNone(collector.scope._parking_methods)
        self.assertEqual(collector._next, cursor)
        self.assertEqual(self.grid_values(grid), before_grid)
        self.assertEqual(self.pool_values(owner), before_pool)
        self.assertEqual(dict(proof.cells), before_cells)
        self.assertIsNone(f.mapping.pending)
        self.assertIsNone(f.mailbox.main_peek())
