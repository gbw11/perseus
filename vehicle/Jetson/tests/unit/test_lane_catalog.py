"""차로 공동 대응·D 원 예약·참조된 상태 복원의 합성 계약 시험.

여기서 만든 head/receipt/LOAD는 명시한 software 값이며 실제 SQL 내구성,
물리 보정·clock·pose prior 승인이나 운행 권한의 근거가 아니다.
"""

from dataclasses import replace
from hashlib import sha256
import struct
import unittest
from uuid import UUID

import test_world_adapter as world_fixture
from test_parking_catalog import publication_context
from xenova_vehicle.mapping.lane_catalog import LaneCatalog, LaneSource, LaneModelCandidates
from xenova_vehicle.mapping.registration import PoseBox, RegistrationProfile
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.storage.contracts import (StorageJob, CatalogHeadSnapshot, CommitReceipt,
    LoadRequest, canonical, json_bytes)
from xenova_vehicle.storage.mapper_state import encode_mapper_state
from xenova_vehicle.storage.records import (CheckpointPayload, EvidenceTable, ContextRecord,
    ProvenanceRecord, PROVENANCE, LoadedCheckpoint)


def uid(n): return '60000000-0000-4000-8000-%012x' % n


FIXED = PoseBox((0., 0.), (0., 0.), (0., 0.))


def owner(context=None):
    return LaneCatalog(publication_context() if context is None else context,
        clock_id=world_fixture.CLOCK, calibration_id=world_fixture.CALIBRATION)


def observed(catalog, sequence=1, *, captured=None, inferred=False, shift=0.):
    captured = (1_000_000 + (sequence-1)*20_000, 1_000_010 + (sequence-1)*20_000) if captured is None else captured
    stamp = FrameStamp('synthetic', 'rgb', sequence, world_fixture.CLOCK, captured, 1_100_000 + (sequence-1)*20_000)
    source = LaneSource(stamp, world_fixture.CALIBRATION, catalog.context.map_epoch,
        catalog.frame_id, catalog.context.odom_epoch, sequence, sequence, True, 'SYNTHETIC')
    road = world_fixture.road(); h = road.hypotheses[0]
    shapes = {name: tuple((x, y+shift) for x, y in getattr(h, name)) for name in
        ('divider', 'left_center', 'right_center', 'left_outer', 'right_outer')}
    h = replace(h, **shapes, inferred=(inferred, inferred))
    return replace(road, status='PARTIAL' if inferred else 'VALID', hypotheses=(h,), source_key=stamp.key), source


def associate(catalog, sequence=1, *, prior=FIXED, crossing=b'\0', **kwargs):
    road, source = observed(catalog, sequence, **kwargs)
    return catalog.observe(road, source, prior, prior_qualified=True, crossing=crossing)


def reserve(catalog, association, *, hwm=1, count=64):
    job = StorageJob(catalog.context, uid(1), 1_100_000, 2_100_000)
    head = CatalogHeadSnapshot(job.job_id, catalog.context.sha256, catalog.context.database_uuid,
        catalog.context.map_epoch, (('LANE', hwm, 1), ('SLOT', 1, 0)), 1_100_000, job.expires_us)
    need = catalog.allocation_need(association, head, job, reservation_nonce=uid(2), now_us=1_100_000, count=count)
    payload = CheckpointPayload.build(job, evidence=EvidenceTable(reservations=(need.reservation,),
        catalog_highwaters=(need.reservation.end_exclusive, 1)), intent='CATALOG_RESERVATION')
    receipt = CommitReceipt(job.context.database_uuid, job.job_id, job.digest(payload),
        'COMMITTED', 1, 1_100_010, job.context.sha256)
    return need, job, payload, receipt


def populated(*, context=None):
    catalog = owner(context); proof = associate(catalog)
    need, job, payload, receipt = reserve(catalog, proof)
    catalog.apply_committed_range(need, job, payload, receipt,
        current_context_sha256=catalog.context.sha256, now_us=1_100_100)
    model = catalog.update(proof, provenance_ids=(101, 102))
    return catalog, model


def loaded_state(catalog, *, target=None, state=None, records=None):
    """실제 bounded state-page codec와 원 LOAD 객체를 조립하는 합성 저장 fixture다."""
    context = catalog.context
    target = context if target is None else target
    reference, pages = encode_mapper_state(dict(lane_catalog=catalog.state_fields() if state is None else state))
    original = ContextRecord(canonical(dict(kind='METRIC', body=dict(
        profile='explicit-lane-test-source-1', storage_context_sha256=context.sha256))))
    provenance = []
    for identity in catalog.originals():
        raw_key = struct.pack('<16sQQ', UUID(uid(90)).bytes, 1, 1_000_000)
        body = PROVENANCE.pack(bytes.fromhex(original.sha256), 1, 3, 1, 0,
            UUID(context.pi_host_boot).bytes, 1_000_000, 1_000_010, 1,
            0, 0, 0, 0, raw_key+bytes(96), 0, bytes(16))
        provenance.append(ProvenanceRecord(identity, body))
    records = catalog.checkpoint_records() if records is None else records
    highwater = max(65, max((r.stable_id+1 for r in records), default=1))
    evidence = EvidenceTable(tuple(provenance), (original,)+pages, records,
        catalog_highwaters=(highwater, 1))
    original_job = StorageJob(context, uid(20), 1_150_000, 2_150_000)
    payload = CheckpointPayload.build(original_job, evidence=evidence, scene=dict(mapper_state=reference),
        durable_cutoff_pi_us=max((record.pi_upper for record in provenance),default=1_000_010))
    ref = dict(checkpoint_job_id=original_job.job_id, durable_revision='1',
        manifest_sha256=sha256(payload.manifest).hexdigest(), manifest_length=len(payload.manifest), tile_count=0,
        map_epoch=context.map_epoch, frame_epoch=context.frame_epoch, odom_epoch=context.odom_epoch,
        pi_host_boot=context.pi_host_boot, configuration=context.configuration, calibration=context.calibration,
        primitive_sha256=context.primitive_sha256, origin_mapper_instance=context.origin_mapper_instance)
    job = StorageJob(target, uid(21), 1_190_000, 2_190_000)
    request = LoadRequest(job, canonical(ref), canonical(dict(kind='CHECKPOINT_PAGE', cursor=0)))
    heads = CatalogHeadSnapshot(job.job_id, target.sha256, target.database_uuid, target.map_epoch,
        (('LANE', highwater, 1), ('SLOT', 1, 0)), 1_190_000, job.expires_us)
    return LoadedCheckpoint(request, original_job, request.checkpoint_ref, payload, heads, 1_190_010)


class LaneCatalogTests(unittest.TestCase):
    def test_actual_cleanup_closes_only_original_pending_need_without_issuing_ids(self):
        catalog=owner();proof=associate(catalog)
        need,job,payload,receipt=reserve(catalog,proof)
        with self.assertRaises(ValueError):catalog.cancel_allocation(replace(need))
        self.assertIs(catalog.pending_allocation[0],need)
        catalog.cancel_allocation(need)
        self.assertIsNone(catalog.pending_allocation)
        self.assertEqual(catalog.available_ids,0)
        self.assertEqual(catalog.revision,0)
        self.assertIsNone(catalog.latest)
        with self.assertRaises(ValueError):
            catalog.apply_committed_range(need,job,payload,receipt,
                current_context_sha256=catalog.context.sha256,now_us=1_100_100)

    def test_new_model_waits_for_original_committed_lane_range_then_exposes_actual_ids(self):
        catalog = owner(); proof = associate(catalog)
        self.assertEqual(proof.status, 'NEW')
        self.assertIsNone(catalog.update(proof, provenance_ids=(101, 102)))
        need, job, payload, receipt = reserve(catalog, proof)
        self.assertEqual(need.reservation.kind, 'LANE')
        self.assertEqual(catalog.available_ids, 0)
        wrong = replace(receipt, job_digest='e'*64)
        with self.assertRaises(ValueError):
            catalog.apply_committed_range(need, job, payload, wrong,
                current_context_sha256=catalog.context.sha256, now_us=1_100_100)
        self.assertTrue(catalog.apply_committed_range(need, job, payload, receipt,
            current_context_sha256=catalog.context.sha256, now_us=1_100_100))
        model = catalog.update(proof, provenance_ids=(101, 102))
        self.assertEqual(model.lane_ids, (1, 2)); self.assertEqual(model.lane_revisions, (1, 1))
        self.assertEqual(model.model_revision, 1); self.assertEqual(model.provenance_ids, (101, 102))
        self.assertEqual(catalog.available_ids, 62)
        self.assertFalse(catalog.apply_committed_range(need, job, payload, receipt,
            current_context_sha256=catalog.context.sha256, now_us=1_100_100))

    def test_fresh_common_geometry_keeps_ids_and_old_source_objects_are_not_retimed(self):
        catalog, original = populated()
        proof = associate(catalog, 2, prior=PoseBox((.05, .05), (0., 0.), (0., 0.)))
        self.assertEqual(proof.status, 'UNIQUE'); self.assertEqual(proof.checked_lanes, 2)
        updated = catalog.update(proof, provenance_ids=(103, 104))
        self.assertEqual(updated.lane_ids, original.lane_ids)
        self.assertEqual(updated.lane_revisions, (2, 2))
        self.assertAlmostEqual(updated.road.hypotheses[0].divider[0][0], .05)
        self.assertEqual(original.source.stamp.captured_us, (1_000_000, 1_000_010))
        self.assertEqual(original.age_us(1_200_000, world_fixture.CLOCK), 200_000)
        self.assertEqual(associate(catalog, 2).status, 'DUPLICATE')
        self.assertEqual(catalog.revision, 2)

    def test_partial_geometry_and_declared_crossing_remain_independent_of_free_or_paint_kind(self):
        catalog = owner(); proof = associate(catalog, inferred=True, crossing=b'\1')
        need, job, payload, receipt = reserve(catalog, proof)
        catalog.apply_committed_range(need, job, payload, receipt,
            current_context_sha256=catalog.context.sha256, now_us=1_100_100)
        model = catalog.update(proof, provenance_ids=(101, 102))
        self.assertEqual(model.road.status, 'PARTIAL')
        self.assertEqual(model.road.hypotheses[0].inferred, (True, True))
        self.assertEqual(model.crossing, b'\1')
        forbidden = replace(model, crossing=b'\0')
        self.assertNotEqual(forbidden.records()[0].payload, model.records()[0].payload)
        wire = world_fixture.lane_model_from_road(model.road, lane_ids=model.lane_ids,
            model_revision=model.model_revision, lane_revisions=model.lane_revisions, crossing=model.crossing)
        self.assertEqual(tuple(v.lane_id for v in wire.lanes), model.lane_ids)
        self.assertEqual(wire.divider_state, b'\1\1'); self.assertEqual(wire.crossing, b'\1')

    def test_incomplete_or_ambiguous_road_and_unqualified_prior_never_issue_ids(self):
        catalog = owner(); road, source = observed(catalog)
        for value, qualified in ((replace(road, complete=False), True),
                (replace(road, status='AMBIGUOUS', hypotheses=road.hypotheses*2), True),
                (road, False)):
            with self.subTest(status=value.status, prior=qualified):
                proof = catalog.observe(value, source, FIXED, prior_qualified=qualified, crossing=b'\0')
                self.assertEqual(proof.status, 'UNKNOWN')
                self.assertIsNone(catalog.update(proof, provenance_ids=(101, 102)))
        self.assertEqual(catalog.revision, 0)

    def test_lack_of_common_fov_never_proves_new_lane_birth(self):
        catalog, _ = populated()
        proof = associate(catalog, 2, prior=PoseBox((5., 5.), (0., 0.), (0., 0.)))
        self.assertEqual(proof.status, 'UNKNOWN'); self.assertEqual(proof.checked_lanes, 2)
        self.assertIn('COMMON_COVERAGE_OR_WORK_UNRESOLVED', proof.reasons)
        self.assertIsNone(catalog.update(proof, provenance_ids=(103, 104)))
        self.assertEqual(catalog.available_ids, 62)

    def test_joint_pose_envelope_and_work_cap_do_not_become_unique_by_best_score(self):
        catalog, _ = populated(); road, source = observed(catalog, 2)
        wide = PoseBox((-.2, .2), (0., 0.), (0., 0.))
        proof = catalog.observe(road, source, wide, prior_qualified=True, crossing=b'\0')
        self.assertEqual(proof.status, 'UNKNOWN')
        capped = catalog.observe(road, source, wide, prior_qualified=True, crossing=b'\0',
            profile=RegistrationProfile(max_cells=1))
        self.assertEqual(capped.status, 'UNKNOWN')
        self.assertIsNone(catalog.update(capped, provenance_ids=(103, 104)))

    def test_scene_only_transition_keeps_accepted_range_cursor_ids_and_original_provenance(self):
        catalog, original = populated(); old = catalog.context; accepted = catalog.accepted_range
        newer = publication_context(old, revision=2, scene=dict(admission_id=uid(70), revision='1'))
        self.assertTrue(catalog.replace_publication_context(newer, now_us=1_150_000))
        self.assertIs(catalog.latest, original); self.assertIs(catalog.accepted_range, accepted)
        self.assertEqual(catalog.available_ids, 62)
        self.assertEqual(original.original_context_sha256, old.sha256)
        proof = associate(catalog, 2)
        model = catalog.update(proof, provenance_ids=(103, 104))
        self.assertEqual(model.lane_ids, original.lane_ids)
        self.assertEqual(model.original_context_sha256, newer.sha256)
        with self.assertRaises(ValueError): catalog.replace_publication_context(old, now_us=1_150_001)

    def test_pending_old_range_requires_terminal_and_cannot_be_first_accepted_after_transition(self):
        catalog = owner(); proof = associate(catalog)
        need, job, payload, receipt = reserve(catalog, proof)
        newer = publication_context(catalog.context, revision=2, scene=dict(admission_id=uid(70), revision='1'))
        self.assertFalse(catalog.replace_publication_context(newer, now_us=1_150_000))
        with self.assertRaises(ValueError):
            catalog.apply_committed_range(need, job, payload, receipt,
                current_context_sha256=catalog.context.sha256, now_us=1_150_001)
        catalog.retire_allocation(need, job, payload, receipt)
        self.assertTrue(catalog.replace_publication_context(newer, now_us=1_150_002))
        self.assertEqual(catalog.available_ids, 0)
        self.assertIsNone(catalog.accepted_range)

    def test_fixed32_exhaustion_and_expired_commit_do_not_issue_or_wrap_ids(self):
        catalog = owner(); proof = associate(catalog)
        with self.assertRaises(ValueError): reserve(catalog, proof, hwm=(1 << 32)-1, count=2)
        self.assertEqual(catalog.available_ids, 0)
        need, job, payload, receipt = reserve(catalog, proof)
        with self.assertRaises(ValueError):
            catalog.apply_committed_range(need, job, payload, receipt,
                current_context_sha256=catalog.context.sha256, now_us=job.expires_us)
        self.assertEqual(catalog.available_ids, 0)
        self.assertIsNone(catalog.latest)

    def test_state_and_local_descriptor_preserve_full_geometry_without_claiming_digest_only_restore(self):
        catalog, model = populated(); state = catalog.state_fields()
        typed = LaneModelCandidates.from_fields(json_bytes(canonical(state['models'][0]), 65536))
        self.assertEqual(typed, model)
        self.assertTrue(all(len(r.encode()) == 256 and len(r.payload) <= 224 for r in model.records()))
        target = owner(); loaded = loaded_state(catalog)
        target.restore(loaded, now_us=1_200_000)
        self.assertEqual(target.checkpoint_records(), catalog.checkpoint_records())
        self.assertEqual(target.state_fields(), state)
        self.assertEqual(target.available_ids, 0); self.assertIsNone(target.latest)
        old = associate(target, 2)
        self.assertEqual(old.status, 'UNKNOWN')
        fresh = associate(target, 12, captured=(1_210_000, 1_210_010))
        self.assertEqual(fresh.status, 'UNIQUE')
        self.assertEqual(target.update(fresh, provenance_ids=(103, 104)).lane_ids, model.lane_ids)

    def test_missing_state_geometry_or_changed_crossing_rejects_restore_atomically(self):
        catalog, model = populated()
        for mutation in ('missing', 'crossing'):
            state = catalog.state_fields()
            if mutation == 'missing': state['models'] = []
            else: state['models'][0]['crossing'] = '01'
            target = owner()
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError): target.restore(loaded_state(catalog, state=state), now_us=1_200_000)
                self.assertEqual(target.revision, 0); self.assertEqual(target.checkpoint_records(), ())

    def test_all_catalog_models_are_checked_and_repeated_geometry_stays_ambiguous(self):
        catalog, model = populated()
        # 합성 내구 원문을 추가한다. 생산 allocator의 로컬 ID 발급 경로가 아니다.
        for index in range(1, 9):
            duplicate = replace(model, lane_ids=(2*index+1, 2*index+2), provenance_ids=(101+2*index, 102+2*index))
            catalog._models[duplicate.lane_ids] = duplicate
        target = owner(); target.restore(loaded_state(catalog), now_us=1_200_000)
        proof = associate(target, 12, captured=(1_210_000, 1_210_010))
        self.assertEqual(proof.status, 'AMBIGUOUS')
        self.assertEqual(proof.checked_lanes, 18); self.assertEqual(len(proof.matches), 9)
        self.assertIsNone(target.update(proof, provenance_ids=(201, 202)))

    def test_new_mapper_restore_can_update_issued_ids_but_cannot_reuse_old_unused_range(self):
        catalog, original = populated(); old = catalog.context
        binding = json_bytes(old.source_binding, 2048)
        binding.update(mapper_worker=uid(80), mapper_instance=uid(81), source_binding_revision='2')
        new = replace(old, mapper_worker=uid(80), origin_mapper_instance=uid(81), source_binding=canonical(binding))
        with self.assertRaises(ValueError): catalog.replace_publication_context(new, now_us=1_150_000)
        target = owner(new); target.restore(loaded_state(catalog, target=new), now_us=1_200_000)
        self.assertEqual(target.available_ids, 0); self.assertIsNone(target.accepted_range)
        fresh = associate(target, 12, captured=(1_210_000, 1_210_010))
        self.assertEqual(fresh.status, 'UNIQUE')
        self.assertEqual(target.update(fresh, provenance_ids=(201, 202)).lane_ids, original.lane_ids)


if __name__ == '__main__':
    unittest.main()
