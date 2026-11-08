"""실제 D SQLite와 M 결과 형태를 연결하는 좁은 주차 producer 시험이다.

카메라/센서 수락이 아닌 합성 metric 근거다. 실제 transaction을 실행하되 주입한
clock으로 원 job/사본 수명을 검사하며 SQL 지연이나 실차 성능을 주장하지 않는다.
"""

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4

from test_parking import graph, source, CALIBRATION, MAP, FRAME, ODOM, CLOCK
from test_parking_catalog import publication_context
from xenova_vehicle.perception.calibration import BodyModel
from xenova_vehicle.perception.pipeline import PerceptionResult
from xenova_vehicle.perception.parking import ParkingAisle, UNKNOWN, OCCUPIED
from xenova_vehicle.mapping.local_map import EvidenceGrid, GridSpec
from xenova_vehicle.mapping.registration import PoseBox
from xenova_vehicle.runtime.parking_bridge import ParkingBridge, MetricEvidence, ParkingNotReady
from xenova_vehicle.storage.contracts import (StorageContext, StorageJob, CatalogReservation,
    LoadRequest, CommitReceipt, canonical, json_bytes)
from xenova_vehicle.storage.records import EvidenceTable, CheckpointPayload
from xenova_vehicle.storage.repository import PersistenceStore


class Clock:
    now = 1_000_000
    def __call__(self):
        return self.now


@unittest.skipUnless(sys.platform == 'linux', '실제 기존 D SQLite owner는 Linux에서 검증함')
class ParkingBridgeTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('실제 공통 map snapshot을 만들 NumPy가 없음')
        from xenova_vehicle.runtime.resident_storage import _Database, StorageProfile
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.db = _Database(StorageProfile(str(Path(self.directory.name) / 'parking.sqlite3'), 'CREATE_IF_ABSENT'),
            time.monotonic_ns() // 1000 + 5_000_000)
        self.addCleanup(self.db.close); self.clock = Clock()
        self.context = StorageContext(self.db.metadata['database_uuid'], str(uuid4()), str(uuid4()), MAP,
            FRAME, ODOM, str(uuid4()), 'synthetic-parking', CALIBRATION, 'b' * 64,
            canonical(dict(profile='explicit-synthetic-parking')))
        self.store = PersistenceStore(self.db.connection, self.context.database_uuid,
            expires_us=6_000_000, clock_us=self.clock)
        # approach lane reference도 test의 D 예약에서 나온다. 임의 운영 lane ID를 발급하지 않는다.
        lane = CatalogReservation(self.context.database_uuid, MAP, 'LANE', 1, 1, 3, str(uuid4()),
            self.context.mapper_worker, self.context.sha256)
        job = self.job()
        payload = CheckpointPayload.build(job, evidence=EvidenceTable(reservations=(lane,), catalog_highwaters=(3, 1)),
            intent='CATALOG_RESERVATION')
        self.assertEqual(self.store.commit(job, payload, current_context_sha256=self.context.sha256).status, 'COMMITTED')
        self.bridge = self.new_bridge()
        self.grid = EvidenceGrid(GridSpec(20, 20, .05, (-.2, -.2)), frame_id=FRAME,
            calibration_id=CALIBRATION, clock_id=CLOCK, kind='SYNTHETIC')
        domain = np.ones((20, 20), dtype=bool); self.grid.set_domain(domain, domain)
        self.grid.add_free(tuple((x, y) for y in range(20) for x in range(20)), source().stamp,
            view_id='explicit-synthetic-certificate', origin_xy=(0., -.1), certificate_complete=True)
        self.aisles = (ParkingAisle((-.1, 0.), (.9, 0.), 1, .001, approach_lane_id=1),)
        self.prior = PoseBox((0., 0.), (0., 0.), (0., 0.))

    def new_bridge(self, **kwargs):
        return ParkingBridge(self.context, clock_id=CLOCK, map_frame_id=FRAME, body=BodyModel(),
            method_manifest_sha256='c' * 64, input_kind=kwargs.get('input_kind', 'SYNTHETIC'))

    def job(self):
        return StorageJob(self.context, str(uuid4()), self.clock(), self.clock() + 5_000_000)

    def assert_original_method_closure(self, evidence):
        contexts = {c.sha256: c for c in evidence.contexts}
        for context in evidence.contexts:
            fields = json_bytes(context.payload, 4096)
            if fields['body'].get('profile') != 'parking-originals-1':
                continue
            originals = fields['body']
            self.assertIn(originals['method_context_sha256'], contexts)
            method = json_bytes(contexts[originals['method_context_sha256']].payload, 4096)
            self.assertEqual(method['kind'], 'METRIC')
            self.assertEqual(method['body']['profile'], 'parking-method-1')
            self.assertEqual(method['body']['storage_context_sha256'], originals['storage_context_sha256'])
        self.assertLessEqual(len(contexts), 64)
        return contexts

    def result(self, original=None):
        original = source() if original is None else original
        stamp = original.stamp
        summary = dict(kind='SYNTHETIC', calibration_id=CALIBRATION, rgb_source_key=stamp.key,
            rgb_captured_us=stamp.captured_us, captured_us=stamp.captured_us, now_us=self.clock(),
            geometric_model_qualified=True, quarantined=False)
        return PerceptionResult(summary, graph(source_value=original), None, None, None,
            self.grid.snapshot(self.clock(), clock_id=CLOCK), None)

    def metric(self, sequence=1):
        return MetricEvidence((source().stamp.key,), self.context.pi_host_boot, (100, 200), sequence,
            3, raw_keys=((self.context.mapper_worker, sequence, 900_000),))

    def process(self, metric=True):
        return self.bridge.process(self.result(), stamp=source().stamp, capture_prior=self.prior,
            aisles=self.aisles, registration_revision=1, prior_qualified=True, now_us=self.clock(),
            metric_evidence=self.metric() if metric else None)

    def allocate(self):
        job = self.job(); request = self.bridge.prepare_provenance(job, str(uuid4()), block_count=128)
        result = self.store.reserve_provenance(job, request.reservation_nonce, block_count=128,
            current_context_sha256=self.context.sha256)
        self.assertTrue(self.bridge.accept_provenance(request, result, now_us=self.clock()))
        self.process()
        range_job = self.job(); head = self.store.catalog_heads(range_job, current_context_sha256=self.context.sha256)
        request = self.bridge.prepare_range(head, range_job, str(uuid4()), now_us=self.clock())
        receipt = self.store.commit(request.job, request.payload, current_context_sha256=self.context.sha256)
        self.assertTrue(self.bridge.accept_range(request, receipt, now_us=self.clock()))
        update = self.process()
        self.assertTrue(update.geometry_updated)
        return update

    def test_allocation_wait_and_duplicate_source_have_no_invented_id_or_new_vote(self):
        waiting = self.process(metric=False)
        self.assertTrue(waiting.allocation_wait)
        self.assertEqual(self.bridge.catalog.tracks, ())
        self.allocate(); track = self.bridge.catalog.tracks[0]; revision = self.bridge.catalog.revision
        repeated = self.process()
        self.assertFalse(repeated.geometry_updated)
        self.assertEqual(self.bridge.catalog.tracks[0].candidate.source.stamp, track.candidate.source.stamp)
        self.assertEqual(self.bridge.catalog.revision, revision)

    def test_same_m_scene_transition_preserves_accepted_ids_provenance_and_ages_and_first_maps_unused_id(self):
        self.context = publication_context(self.context); self.bridge = self.new_bridge(); self.allocate()
        old_context = self.context; old_method = self.bridge.method
        old_job = self.job(); original_cp = self.bridge.build_checkpoint(old_job, now_us=self.clock(), durable_cutoff_pi_us=200)
        self.assert_original_method_closure(original_cp.payload.evidence)
        old_receipt = self.store.commit(old_job, original_cp.payload, current_context_sha256=old_context.sha256)
        self.assertTrue(self.bridge.accept_checkpoint(original_cp, old_receipt, now_us=self.clock()))
        track = self.bridge.catalog.tracks[0]; accepted = self.bridge.catalog.accepted_range
        provenance, cursor = self.bridge._provenance, self.bridge._next_provenance
        unused = self.bridge.catalog.available_ids
        newer = publication_context(old_context, revision=2, scene=dict(admission_id=str(uuid4()), revision='1'))
        self.assertTrue(self.bridge.replace_publication_context(newer, now_us=self.clock()))
        self.assertIs(self.bridge._contexts[old_method.sha256], old_method)
        self.assertIs(self.bridge.catalog.tracks[0], track)
        self.assertIs(self.bridge.catalog.accepted_range, accepted)
        self.assertIs(accepted.job.context, old_context)
        self.assertIs(self.bridge._provenance, provenance); self.assertEqual(self.bridge._next_provenance, cursor)
        self.assertEqual(self.bridge.catalog.available_ids, unused)
        self.assertIs(self.bridge.last_commit, old_receipt)
        self.assertEqual(old_receipt.origin_context_sha256, old_context.sha256)
        self.assertEqual(self.bridge.catalog.tracks[0].candidate.source.stamp, source().stamp)
        self.assertEqual(self.bridge.catalog.tracks[0].occupancy.observed_lower_us, track.occupancy.observed_lower_us)
        with self.assertRaises(ParkingNotReady):
            self.bridge.build_checkpoint(StorageJob(newer, str(uuid4()), self.clock(), self.clock() + 5_000_000),
                now_us=self.clock(), durable_cutoff_pi_us=200)
        self.context = newer
        new_head_job = self.job()
        head = self.store.catalog_heads(new_head_job, current_context_sha256=newer.sha256)
        # current 조회 cache를 비워도 원 수락한 HWM의 단조 하한을 낮추지 않는다.
        lower = replace(head, heads=(head.heads[0], ('SLOT', head.heads[1][1] - 1, head.heads[1][2])))
        with self.assertRaises(ValueError): self.bridge.observe_heads(lower, new_head_job, now_us=self.clock())
        self.bridge.observe_heads(head, new_head_job, now_us=self.clock())
        original = source(2, captured=(950_000, 950_010))
        metric = MetricEvidence((original.stamp.key,), newer.pi_host_boot, (50_100, 50_200), 2,
            3, raw_keys=((newer.mapper_worker, 2, 950_000),))
        pose = PoseBox((1., 1.), (0., 0.), (0., 0.))
        self.assertTrue(self.bridge.process(self.result(original), stamp=original.stamp, capture_prior=pose,
            aisles=self.aisles, registration_revision=1, prior_qualified=True,
            now_us=self.clock(), metric_evidence=metric).geometry_updated)
        self.assertEqual(tuple(t.slot_id for t in self.bridge.catalog.tracks), (1, 2))
        self.assertEqual(self.bridge.catalog.available_ids, unused - 1)
        # scene-only 전환 뒤 pruning도 과거 originals의 실제 method 원문을 유지한다.
        self.bridge._prune()
        self.assertIs(self.bridge._contexts[old_method.sha256], old_method)
        cp = self.bridge.build_checkpoint(self.job(), now_us=self.clock(), durable_cutoff_pi_us=50_200)
        self.assert_original_method_closure(cp.payload.evidence)
        self.assertIn(old_method.payload, {c.payload for c in cp.payload.evidence.contexts})
        self.assertIn(self.bridge.method.payload, {c.payload for c in cp.payload.evidence.contexts})
        receipt = self.store.commit(cp.job, cp.payload, current_context_sha256=newer.sha256)
        self.assertEqual(receipt.status, 'COMMITTED')
        self.assertTrue(self.bridge.accept_checkpoint(cp, receipt, now_us=self.clock()))
        reference = self.store.latest_complete(expires_us=self.clock() + 5_000_000)
        loaded = self.store.load(LoadRequest(self.job(), canonical(reference),
            canonical(dict(kind='CHECKPOINT_PAGE', cursor=0))), current_context_sha256=newer.sha256)
        loaded_contexts = self.assert_original_method_closure(loaded.payload.evidence)
        self.assertEqual(loaded_contexts[old_method.sha256].payload, old_method.payload)
        self.assertEqual(loaded_contexts[self.bridge.method.sha256].payload, self.bridge.method.payload)
        row = self.db.connection.execute('SELECT context_sha256,job_id FROM catalog_ranges WHERE kind=? AND start_id=?', ('SLOT', 1)).fetchone()
        self.assertEqual(row, (old_context.sha256, accepted.job.job_id))
        self.assertEqual(self.db.connection.execute('SELECT COUNT(DISTINCT stable_id) FROM catalog_versions WHERE kind=?', ('SLOT',)).fetchone()[0], 2)

    def test_scene_transition_waits_original_checkpoint_result_without_forgetting_or_retiming_it(self):
        self.context = publication_context(self.context); self.bridge = self.new_bridge(); self.allocate()
        old = self.context; method = self.bridge.method
        request = self.bridge.build_checkpoint(self.job(), now_us=self.clock(), durable_cutoff_pi_us=200)
        newer = publication_context(old, revision=2, scene=dict(admission_id=str(uuid4()), revision='1'))
        self.assertFalse(self.bridge.replace_publication_context(newer, now_us=self.clock()))
        self.assertEqual(self.bridge.context_transition_wait_reason, 'PENDING_ORIGINAL_D_JOB')
        self.assertIs(self.bridge.context, old); self.assertIs(self.bridge.method, method)
        self.assertIs(self.bridge._pending_checkpoint, request)
        receipt = self.store.commit(request.job, request.payload, current_context_sha256=old.sha256)
        self.assertFalse(self.bridge.accept_checkpoint(request, receipt, now_us=self.clock()))
        self.assertIsNone(self.bridge.last_commit)
        self.assertTrue(self.bridge.replace_publication_context(newer, now_us=self.clock()))
        self.assertEqual(request.job.context, old); self.assertEqual(receipt.origin_context_sha256, old.sha256)
        self.assertIsNone(self.bridge.context_transition_wait_reason)

    def test_pending_provenance_is_not_promoted_on_scene_transition_and_new_range_never_reuses_old_numbers(self):
        self.context = publication_context(self.context); self.bridge = self.new_bridge()
        old = self.context; request = self.bridge.prepare_provenance(self.job(), str(uuid4()), block_count=4)
        original = self.store.reserve_provenance(request.job, request.reservation_nonce, block_count=4,
            current_context_sha256=old.sha256)
        newer = publication_context(old, revision=2, scene=dict(admission_id=str(uuid4()), revision='1'))
        self.assertFalse(self.bridge.replace_publication_context(newer, now_us=self.clock()))
        # 실제 D의 원 결과도 전환 요청 뒤에는 대기 정리/재사용 금지 하한만 제공한다.
        self.assertFalse(self.bridge.accept_provenance(request, original, now_us=self.clock()))
        self.assertTrue(self.bridge.replace_publication_context(newer, now_us=self.clock()))
        with self.assertRaises(ValueError): self.bridge.accept_provenance(request, original, now_us=self.clock())
        self.assertIsNone(self.bridge._provenance)
        self.context = newer; next_request = self.bridge.prepare_provenance(self.job(), str(uuid4()), block_count=4)
        reserved = self.store.reserve_provenance(next_request.job, next_request.reservation_nonce, block_count=4,
            current_context_sha256=newer.sha256)
        self.assertGreaterEqual(reserved.start_inclusive, original.end_exclusive)
        self.assertTrue(self.bridge.accept_provenance(next_request, reserved, now_us=self.clock()))

    def test_real_checkpoint_load_reconstructs_original_source_times_and_does_not_revive_free(self):
        self.allocate(); original = self.bridge.catalog.tracks[0]
        job = self.job(); request = self.bridge.build_checkpoint(job, now_us=self.clock(), durable_cutoff_pi_us=200)
        self.assertIs(self.bridge.build_checkpoint(self.job(), now_us=self.clock(), durable_cutoff_pi_us=200), request)
        self.assertTrue(all(len(c.payload) <= 4096 for c in request.payload.evidence.contexts))
        self.assertTrue(any(p.parents for p in request.payload.evidence.provenance))
        contexts = self.assert_original_method_closure(request.payload.evidence)
        self.assertEqual(contexts[self.bridge.method.sha256].payload, self.bridge.method.payload)
        self.assertNotIn('c' * 64, contexts)  # method_manifest_sha256는 asset이며 context edge가 아니다.
        receipt = self.store.commit(job, request.payload, current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status, 'COMMITTED')
        self.assertTrue(self.bridge.accept_checkpoint(request, receipt, now_us=self.clock()))
        reference = self.store.latest_complete(expires_us=self.clock() + 5_000_000)
        load_job = self.job(); load = LoadRequest(load_job, canonical(reference), canonical(dict(kind='CHECKPOINT_PAGE', cursor=0)))
        result = self.store.load(load, current_context_sha256=self.context.sha256)
        loaded_contexts = self.assert_original_method_closure(result.payload.evidence)
        self.assertEqual(loaded_contexts[self.bridge.method.sha256].payload, self.bridge.method.payload)
        restored = self.new_bridge(); restored.restore(result, now_us=self.clock())
        track = restored.catalog.tracks[0]
        self.assertEqual(track.slot_id, original.slot_id)
        self.assertEqual(track.candidate.source, original.candidate.source)
        self.assertEqual(track.occupancy.observed_lower_us, original.occupancy.observed_lower_us)
        self.assertEqual(track.geometry_age_ms(self.clock(), CLOCK), 100)
        self.assertEqual(track.occupancy_at(self.clock(), CLOCK).state, UNKNOWN)
        self.assertIsNone(restored.catalog.durable_hwm)
        # 같은 칸의 새 실제 source를 기존 immutable catalog key에 덮어쓰지 않는다.
        newer = source(2, captured=(950_000, 950_010))
        metric = MetricEvidence((newer.stamp.key,), self.context.pi_host_boot, (50_100, 50_200), 2,
            3, raw_keys=((self.context.mapper_worker, 2, 950_000),))
        self.assertTrue(self.bridge.process(self.result(newer), stamp=newer.stamp, capture_prior=self.prior,
            aisles=self.aisles, registration_revision=1, prior_qualified=True, now_us=self.clock(),
            metric_evidence=metric).geometry_updated)
        next_request = self.bridge.build_checkpoint(self.job(), now_us=self.clock(), durable_cutoff_pi_us=50_200)
        self.assertEqual(self.store.commit(next_request.job, next_request.payload,
            current_context_sha256=self.context.sha256).status, 'COMMITTED')
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM catalog_versions WHERE kind=? AND stable_id=?',
            ('SLOT', original.slot_id)).fetchone()[0], 2)

    def test_new_map_hit_without_fresh_provenance_blocks_old_clearance_immediately(self):
        self.allocate(); previous = self.bridge.catalog.tracks[0]
        self.grid.add_hits(((.1, .1, .2, .2),), source().stamp, view_id='synthetic-new-hit', origin_xy=(0., -.1))
        self.clock.now = 1_010_000
        update = self.process(metric=False)
        self.assertFalse(update.geometry_updated)
        self.assertTrue(update.provenance_wait)
        self.assertEqual(self.bridge.catalog.tracks[0].provenance_id, previous.provenance_id)
        self.assertEqual(self.bridge._volatile[previous.slot_id].state, OCCUPIED)

    def test_publication_uses_common_schema_and_never_mints_selection_or_scene(self):
        try:
            from xenova_drive_world import schema
        except ImportError:
            self.skipTest('공용 protobuf package가 연결되지 않음')
        self.allocate()
        publication = self.bridge.publication_candidates(now_us=self.clock(), roi_bounds=(-.2, -.2, .8, .8))
        self.assertFalse(publication.live_authority)
        meta = schema.ParkingSetMeta.FromString(publication.meta)
        self.assertTrue(meta.complete)
        self.assertEqual(meta.track_count_in_roi, 1)
        value = schema.ParkingSlot.FromString(publication.slots[0])
        self.assertEqual(value.slot_id, self.bridge.catalog.tracks[0].slot_id)
        self.assertEqual(value.goal_mask, 3)
        self.assertEqual(value.geometry_age_ms, 100)
        self.assertEqual(value.geometry_state, 1)
        self.assertEqual(value.approach_lane_id, 1)

    def test_original_context_and_synthetic_domains_are_not_relabelled(self):
        with self.assertRaises(ValueError):
            self.new_bridge(input_kind='LIVE')
        replay = self.new_bridge(input_kind='REPLAY')
        with self.assertRaises(ValueError):
            replay.process(self.result(), stamp=source().stamp, capture_prior=self.prior,
                aisles=self.aisles, registration_revision=1, prior_qualified=True, now_us=self.clock())
        with self.assertRaises(ValueError):
            self.bridge.process(self.result(), stamp=replace(source().stamp, clock_id=str(uuid4())),
                capture_prior=self.prior, aisles=self.aisles, registration_revision=1, prior_qualified=True, now_us=self.clock())

    def test_more_than_eight_committed_id_tracks_are_reported_partial_without_nearest_subset(self):
        try:
            from xenova_drive_world import schema
        except ImportError:
            self.skipTest('공용 protobuf package가 연결되지 않음')
        self.allocate()
        # 독립적인 합성 capture-pose 입력이다. 실차 운동/주행속도 시험이 아니다.
        for number in range(2, 10):
            original = source(number, captured=(900_000 + 1000 * number, 900_010 + 1000 * number))
            pose = PoseBox((float(number - 1),) * 2, (0., 0.), (0., 0.))
            delta = original.stamp.captured_us[0] - 900_000
            metric = MetricEvidence((original.stamp.key,), self.context.pi_host_boot, (100 + delta, 200 + delta), number,
                3, raw_keys=((self.context.mapper_worker, number, original.stamp.captured_us[0]),))
            update = self.bridge.process(self.result(original), stamp=original.stamp, capture_prior=pose,
                aisles=self.aisles, registration_revision=1, prior_qualified=True, now_us=self.clock(), metric_evidence=metric)
            self.assertTrue(update.geometry_updated)
        publication = self.bridge.publication_candidates(now_us=self.clock(), roi_bounds=(-.2, -.2, 10., .8))
        meta = schema.ParkingSetMeta.FromString(publication.meta)
        self.assertEqual(publication.total_tracks_in_roi, 9)
        self.assertEqual(publication.slots, ())
        self.assertEqual(meta.track_count_in_roi, 9)
        self.assertTrue(meta.capacity_limited)
        self.assertFalse(meta.complete)

    def test_failed_and_late_original_range_results_are_cleanup_not_new_issuance(self):
        self.process(metric=False)
        job = self.job(); head = self.store.catalog_heads(job, current_context_sha256=self.context.sha256)
        request = self.bridge.prepare_range(head, job, str(uuid4()), now_us=self.clock())
        receipt = self.store.commit(job, request.payload, current_context_sha256=self.context.sha256)
        self.clock.now = job.expires_us
        self.assertFalse(self.bridge.accept_range(request, receipt, now_us=self.clock()))
        self.assertEqual(self.bridge.catalog.available_ids, 0)
        self.assertEqual(self.bridge.catalog.tracks, ())
        self.assertFalse(self.bridge.accept_range(request, receipt, now_us=self.clock()))


if __name__ == '__main__':
    unittest.main()
