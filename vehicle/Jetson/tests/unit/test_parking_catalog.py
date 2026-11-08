"""전체 catalog/common-pose 대응과 원 D receipt 소비의 합성 계약 시험이다.

명시한 test ID·head·COMMITTED 객체는 durable SQL 실행의 증거가 아니다.
실제 D transaction/복구는 storage 통합시험이 담당한다. 여기서는 I/O를 하지 않는다.
"""

from dataclasses import replace
import unittest

from test_parking import source, batch, candidate, clearance, CALIBRATION, MAP, FRAME, ODOM, CLOCK
from xenova_vehicle.perception.parking import (SlotGeometryResult, OccupancyEvidence, PARTIAL,
    UNKNOWN, OCCUPIED, CLEAR_REGION, INVALID_DEPTH, HIT, to_map, evaluate_occupancy)
from xenova_vehicle.mapping.parking_catalog import (AssociationProfile, SlotTrack, SlotCatalog,
    associate_slots)
from xenova_vehicle.mapping.registration import PoseBox
from xenova_vehicle.storage.contracts import (StorageContext, StorageJob, CatalogHeadSnapshot,
    CommitReceipt, canonical)
from xenova_vehicle.storage.records import CheckpointPayload, EvidenceTable


DATABASE = '20000000-0000-4000-8000-000000000001'
WORKER = '20000000-0000-4000-8000-000000000002'
ORIGIN = '20000000-0000-4000-8000-000000000003'
HOST = '20000000-0000-4000-8000-000000000004'
JOB = '20000000-0000-4000-8000-000000000005'
NONCE = '20000000-0000-4000-8000-000000000006'
FIXED = PoseBox((0., 0.), (0., 0.), (0., 0.))


def context():
    return StorageContext(DATABASE, WORKER, ORIGIN, MAP, FRAME, ODOM, HOST, 'synthetic-parking',
        CALIBRATION, 'b' * 64, canonical({'kind': 'SYNTHETIC', 'source': 'test-only'}))


def publication_context(original=None, *, revision=1, scene=None):
    """실제 SourcePolicy 수락이 아닌 명시한 합성 Pi origin/PublicationBinding이다."""
    from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin
    original = context() if original is None else original
    binding = dict(service_instance='20000000-0000-4000-8000-000000000090',
        mapper_worker=original.mapper_worker, mapper_instance=original.origin_mapper_instance,
        raw_binding_sha256='a' * 64, map_epoch=original.map_epoch, frame_epoch=original.frame_epoch,
        odom_epoch=original.odom_epoch, configuration=original.configuration, calibration=original.calibration,
        primitive=original.primitive_sha256, scene_admission_ref=scene, source_binding_revision=str(revision))
    origin = original.geometry_origin_fields
    if origin is None:
        origin = PiGeometryOrigin('synthetic-car', original.pi_host_boot,
            '20000000-0000-4000-8000-000000000091', '20000000-0000-4000-8000-000000000092',
            '20000000-0000-4000-8000-000000000093', original.odom_epoch, original.calibration).fields
    return replace(original, source_binding=canonical(binding), geometry_origin=origin)


def shifted(value, x, *, local_id=None):
    def points(original):
        return tuple((p[0] + x, p[1]) for p in original)
    return replace(value, local_id=value.local_id if local_id is None else local_id,
        corners=points(value.corners), partial_points=points(value.partial_points), entry=points(value.entry),
        usable_polygon=points(value.usable_polygon))


def observations(xs, *, sequence=2, captured=(950_000, 950_010)):
    original = source(sequence, captured=captured, available=1_050_000)
    c = batch(original=original).candidates[0]
    values = tuple(shifted(c, x, local_id=i) for i, x in enumerate(xs))
    return SlotGeometryResult(values, original, True, 'VALID', (), len(values), len(values))


def tracks(xs):
    # stable ID는 test oracle의 명시값이다. 생산자는 이 로컬 순번을 발급하지 않는다.
    c = candidate()
    return tuple(SlotTrack(i + 1, 1, 1, to_map(shifted(c, x), (0., 0., 0.),
        position_error_m=0., yaw_error_rad=0.), None, 100 + i) for i, x in enumerate(xs))


def prepared(catalog, value, *, hwm=1, count=4, now=1_000_000):
    job = StorageJob(catalog.context, JOB, 1_000_000, 2_000_000)
    head = CatalogHeadSnapshot(job.job_id, catalog.context.sha256, DATABASE, MAP,
        (('LANE', 1, 0), ('SLOT', hwm, 0)), 1_000_000, job.expires_us)
    association = catalog.associate(value, FIXED, prior_qualified=True)
    need = catalog.allocation_need(value, association, head, job, reservation_nonce=NONCE, now_us=now, count=count)
    payload = CheckpointPayload.build(job, evidence=EvidenceTable(reservations=(need.reservation,),
        catalog_highwaters=(1, need.reservation.end_exclusive)), intent='CATALOG_RESERVATION')
    receipt = CommitReceipt(DATABASE, job.job_id, job.digest(payload), 'COMMITTED', 1, 1_000_010, catalog.context.sha256)
    return association, need, job, payload, receipt


def committed_catalog():
    catalog = SlotCatalog(context(), clock_id=CLOCK); value = batch()
    association, need, job, payload, receipt = prepared(catalog, value)
    catalog.apply_committed_range(need, job, payload, receipt, current_context_sha256=catalog.context.sha256, now_us=1_000_100)
    catalog.update(value, association, provenance_ids={0: 101})
    return catalog


class ParkingAssociationTests(unittest.TestCase):
    def test_scene_context_transition_preserves_original_accepted_range_cursor_tracks_and_ages(self):
        old = publication_context(); catalog = SlotCatalog(old, clock_id=CLOCK); value = batch()
        proof, need, job, payload, receipt = prepared(catalog, value)
        catalog.apply_committed_range(need, job, payload, receipt, current_context_sha256=old.sha256, now_us=1_000_100)
        catalog.update(value, proof, provenance_ids={0: 101})
        track = catalog.tracks[0]; accepted = catalog.accepted_range
        unused, revision = catalog.available_ids, catalog.revision
        newer = publication_context(old, revision=2, scene=dict(admission_id=NONCE, revision='1'))
        self.assertTrue(catalog.replace_publication_context(newer, now_us=1_100_000))
        self.assertIs(catalog.tracks[0], track); self.assertEqual(catalog.revision, revision)
        self.assertEqual(catalog.available_ids, unused); self.assertIs(catalog.accepted_range, accepted)
        self.assertIs(accepted.job, job); self.assertIs(accepted.receipt, receipt)
        self.assertEqual(accepted.need.reservation.context_sha256, old.sha256)
        self.assertNotEqual(accepted.need.reservation.context_sha256, newer.sha256)
        self.assertEqual(catalog.tracks[0].geometry_age_ms(1_100_000, CLOCK), 200)
        second = observations((1.,))
        self.assertTrue(catalog.update(second, catalog.associate(second, FIXED, prior_qualified=True), provenance_ids={0: 102}))
        self.assertEqual(tuple(t.slot_id for t in catalog.tracks), (1, 2))
        self.assertEqual(catalog.available_ids, unused - 1)

    def test_pending_old_range_waits_actual_terminal_then_old_unaccepted_receipt_stays_historical(self):
        old = publication_context(); catalog = SlotCatalog(old, clock_id=CLOCK)
        _, need, job, payload, receipt = prepared(catalog, batch())
        newer = publication_context(old, revision=2, scene=dict(admission_id=NONCE, revision='1'))
        self.assertFalse(catalog.replace_publication_context(newer, now_us=1_100_000))
        self.assertIs(catalog.context, old); self.assertIsNone(catalog.accepted_range)
        with self.assertRaises(ValueError):
            catalog.apply_committed_range(need, job, payload, receipt,
                current_context_sha256=old.sha256, now_us=1_100_000)
        self.assertEqual(catalog.available_ids, 0)
        catalog.retire_allocation(need, job, payload, receipt)
        self.assertTrue(catalog.replace_publication_context(newer, now_us=1_100_001))
        with self.assertRaises(ValueError):
            catalog.apply_committed_range(need, job, payload, receipt,
                current_context_sha256=newer.sha256, now_us=1_100_002)
        self.assertEqual(catalog.available_ids, 0)

    def test_rebind_rejects_new_mapper_real_origin_raw_change_or_old_revision_without_mutation(self):
        from xenova_vehicle.storage.contracts import json_bytes
        old = publication_context(); catalog = SlotCatalog(old, clock_id=CLOCK)
        newer = publication_context(old, revision=2, scene=dict(admission_id=NONCE, revision='1'))
        mapper = replace(newer, mapper_worker='20000000-0000-4000-8000-000000000098')
        origin = dict(newer.geometry_origin_fields, pi_session='20000000-0000-4000-8000-000000000097')
        raw_binding = json_bytes(newer.source_binding, 2048); raw_binding['raw_binding_sha256'] = 'e' * 64
        for changed in (mapper, replace(newer, geometry_origin=origin), replace(newer, source_binding=canonical(raw_binding)),
                replace(newer, geometry_origin=None)):
            with self.assertRaises(ValueError): catalog.replace_publication_context(changed, now_us=1_100_000)
            self.assertIs(catalog.context, old)
        self.assertTrue(catalog.replace_publication_context(newer, now_us=1_100_000))
        with self.assertRaises(ValueError): catalog.replace_publication_context(old, now_us=1_100_001)
        self.assertIs(catalog.context, newer)

    def test_unique_joint_assignment_preserves_explicit_existing_ids(self):
        value = observations((0., 1.))
        result = associate_slots(value, tracks((0., 1.)), FIXED, prior_qualified=True)
        self.assertEqual(result.status, 'UNIQUE')
        self.assertTrue(result.complete)
        self.assertEqual(result.assignments[0].pairs, ((0, 1), (1, 2)))
        self.assertEqual(result.catalog_checked, 2)

    def test_all_nine_catalog_bays_survive_before_cap_without_nearest_eight(self):
        value = observations(tuple(float(i) for i in range(8)))
        result = associate_slots(value, tracks(tuple(float(i) for i in range(9))),
            PoseBox((0., 1.), (0., 0.), (0., 0.)), prior_qualified=True)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)
        self.assertEqual(result.catalog_checked, 9)
        self.assertIn('SURVIVING_CATALOG_CAP', result.reasons)
        self.assertEqual({n for _, choices in result.candidate_ids for n in choices}, set(range(1, 10)))

    def test_repeated_pitch_retains_two_complete_assignments(self):
        result = associate_slots(observations((0.,)), tracks((0., 1.)),
            PoseBox((0., 1.), (0., 0.), (0., 0.)), prior_qualified=True)
        self.assertEqual(result.status, 'AMBIGUOUS')
        self.assertTrue(result.complete)
        self.assertEqual({a.pairs for a in result.assignments}, {((0, 1),), ((0, 2),)})

    def test_individually_possible_pairs_without_one_common_pose_are_not_unique(self):
        result = associate_slots(observations((0., 1.)), tracks((.05, 1.25)),
            PoseBox((0., .30), (0., 0.), (0., 0.)), prior_qualified=True)
        self.assertEqual(result.status, 'INCONSISTENT')
        self.assertTrue(result.complete)
        self.assertEqual(result.assignments, ())

    def test_pose_work_cap_keeps_unexamined_cells_and_cannot_claim_unique(self):
        result = associate_slots(observations((0.,)), tracks((0.,)),
            PoseBox((-.1, .1), (0., 0.), (0., 0.)), prior_qualified=True,
            profile=AssociationProfile(max_pose_cells=1))
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)
        self.assertIn('ASSOCIATION_WORK_CAP', result.reasons)

    def test_catalog_gate_work_cap_keeps_unchecked_ids_possible(self):
        result = associate_slots(observations((0.,)), tracks((0., 1.)), FIXED, prior_qualified=True,
            profile=AssociationProfile(max_distance_tests=3))
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(result.complete)
        self.assertEqual(result.candidate_ids, ((0, (1, 2)),))
        self.assertEqual(result.distance_tests, 0)

    def test_partial_missing_clock_and_more_than_eight_observations_are_not_truncated(self):
        complete = observations((0.,))
        partial = replace(complete, candidates=(replace(complete.candidates[0], geometry_state=PARTIAL),))
        for value in (partial, observations((0.,), captured=None), observations(tuple(float(i) for i in range(9)))):
            result = associate_slots(value, (), FIXED, prior_qualified=True)
            self.assertEqual(result.status, 'UNKNOWN')
            self.assertFalse(result.complete)
            self.assertEqual(result.births, ())
        self.assertEqual(associate_slots(complete, (), FIXED, prior_qualified=False).status, 'UNKNOWN')

    def test_changed_geometry_near_existing_bay_is_not_a_new_birth(self):
        original = observations((0.,)); c = original.candidates[0]
        deeper = replace(c, corners=(c.corners[0], c.corners[1], (.4, 1.1), (0., 1.1)),
            depth_interval_m=(1.09, 1.11))
        result = associate_slots(replace(original, candidates=(deeper,)), tracks((0.,)), FIXED, prior_qualified=True)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertIn('EXISTING_VERSUS_NEW_UNRESOLVED', result.reasons)
        self.assertEqual(result.births, ())


class ParkingCatalogTests(unittest.TestCase):
    def test_new_candidate_waits_for_exact_committed_range_and_duplicate_does_not_reset_it(self):
        catalog = SlotCatalog(context(), clock_id=CLOCK); value = batch()
        association, need, job, payload, receipt = prepared(catalog, value)
        self.assertFalse(catalog.update(value, association, provenance_ids={0: 101}))
        self.assertEqual(catalog.tracks, ())
        self.assertTrue(catalog.apply_committed_range(need, job, payload, receipt,
            current_context_sha256=catalog.context.sha256, now_us=1_000_100))
        self.assertTrue(catalog.update(value, association, provenance_ids={0: 101}))
        self.assertEqual(catalog.tracks[0].slot_id, 1)
        self.assertFalse(catalog.apply_committed_range(need, job, payload, receipt,
            current_context_sha256=catalog.context.sha256, now_us=1_000_200))
        later = observations((2.,)); matching = catalog.associate(later, FIXED, prior_qualified=True)
        self.assertEqual(matching.status, 'NEW')
        self.assertTrue(catalog.update(later, matching, provenance_ids={0: 102}))
        self.assertEqual(tuple(t.slot_id for t in catalog.tracks), (1, 2))

    def test_historical_expired_wrong_context_and_unknown_receipts_cannot_supply_ids(self):
        for fault in ('expired', 'context', 'unknown', 'payload'):
            catalog = SlotCatalog(context(), clock_id=CLOCK); value = batch()
            _, need, job, payload, receipt = prepared(catalog, value)
            now = 2_000_000 if fault == 'expired' else 1_000_100
            current = 'c' * 64 if fault == 'context' else catalog.context.sha256
            if fault == 'unknown':
                receipt = replace(receipt, status='UNKNOWN', durable_revision=None, committed_j_us=None)
            if fault == 'payload':
                payload = CheckpointPayload.build(job, evidence=EvidenceTable(reservations=(replace(need.reservation,
                    end_exclusive=need.reservation.end_exclusive + 1),), catalog_highwaters=(1, 6)), intent='CATALOG_RESERVATION')
            with self.assertRaises(ValueError):
                catalog.apply_committed_range(need, job, payload, receipt, current_context_sha256=current, now_us=now)
            self.assertIsNone(catalog.durable_hwm)
            self.assertEqual(catalog.tracks, ())

    def test_source_duplicate_is_no_new_geometry_or_id_and_conflicting_bytes_are_rejected(self):
        catalog = committed_catalog(); value = batch()
        matching = catalog.associate(value, FIXED, prior_qualified=True)
        original = catalog.tracks; revision = catalog.revision
        self.assertFalse(catalog.update(value, matching, provenance_ids={0: 101}))
        changed = replace(value, candidates=(shifted(value.candidates[0], .001),))
        changed_match = catalog.associate(changed, FIXED, prior_qualified=True)
        with self.assertRaises(ValueError):
            catalog.update(changed, changed_match, provenance_ids={0: 103})
        self.assertEqual(catalog.tracks, original)
        self.assertEqual(catalog.revision, revision)

    def test_geometry_and_occupancy_revisions_are_independent_and_missing_depth_keeps_hit(self):
        catalog = committed_catalog(); value = observations((0.,))
        matching = catalog.associate(value, FIXED, prior_qualified=True)
        hit = OccupancyEvidence(OCCUPIED, HIT, value.source, 2, None, (1 << 32) - 1, 1,
            10, 1, 0, value.candidates[0].usable_polygon, 1_050_000)
        self.assertTrue(catalog.update(value, matching, provenance_ids={0: 102}, occupancy=((0, hit),)))
        updated = catalog.tracks[0]
        self.assertEqual(updated.geometry_revision, 2)
        self.assertEqual(updated.occupancy_revision, 2)
        later = observations((0.,), sequence=3, captured=(980_000, 980_010))
        self.assertTrue(catalog.update(later, catalog.associate(later, FIXED, prior_qualified=True), provenance_ids={0: 103}))
        self.assertEqual(catalog.tracks[0].occupancy_state, OCCUPIED)
        self.assertEqual(catalog.tracks[0].occupancy.source.stamp.key, value.source.stamp.key)
        self.assertEqual(catalog.tracks[0].geometry_revision, 3)
        self.assertEqual(catalog.tracks[0].occupancy_revision, 2)

    def test_original_capture_time_reversal_and_old_catalog_proof_are_not_applied(self):
        catalog = committed_catalog(); value = observations((0.,), captured=(800_000, 800_010))
        proof = catalog.associate(value, FIXED, prior_qualified=True)
        with self.assertRaises(ValueError):
            catalog.update(value, proof, provenance_ids={0: 102})
        newer = observations((0.,)); proof = catalog.associate(newer, FIXED, prior_qualified=True)
        with self.assertRaises(ValueError):
            catalog.update(newer, replace(proof, catalog_revision=0), provenance_ids={0: 102})
        self.assertEqual(catalog.revision, 1)

    def test_original_clearance_age_expires_and_restore_does_not_authorize_retained_free(self):
        catalog = committed_catalog(); value = observations((0.,))
        c = to_map(value.candidates[0], (0., 0., 0.), position_error_m=0., yaw_error_rad=0.)
        free = evaluate_occupancy(c, clearance(), now_us=1_050_000)
        self.assertEqual(free.state, CLEAR_REGION)
        catalog.update(value, catalog.associate(value, FIXED, prior_qualified=True), provenance_ids={0: 102}, occupancy=((0, free),))
        track = catalog.tracks[0]
        self.assertEqual(track.occupancy_at(1_100_000, CLOCK).state, CLEAR_REGION)
        self.assertEqual(track.occupancy_at(1_500_000, CLOCK).state, UNKNOWN)
        self.assertEqual(track.occupancy.observed_lower_us, 999_001)
        self.assertEqual(track.geometry_age_ms(1_100_000, CLOCK), 150)
        restored = SlotCatalog(context(), clock_id=CLOCK)
        restored.restore(catalog.checkpoint_records(), {102: (track.candidate.source, free)},
            catalog_revision=catalog.revision, restored_at_us=1_060_000)
        self.assertEqual(restored.tracks[0].occupancy_state, UNKNOWN)
        self.assertEqual(restored.tracks[0].occupancy_at(1_100_000, CLOCK).state, UNKNOWN)
        self.assertIs(restored.tracks[0].occupancy, free)
        geometry_only = observations((0.,), sequence=3, captured=(980_000, 980_010))
        restored.update(geometry_only, restored.associate(geometry_only, FIXED, prior_qualified=True), provenance_ids={0: 103})
        self.assertTrue(restored.tracks[0].restored)
        self.assertEqual(restored.tracks[0].occupancy_at(1_100_000, CLOCK).state, UNKNOWN)
        later = observations((0.,), sequence=4, captured=(1_020_000, 1_020_010))
        mapped = to_map(later.candidates[0], (0., 0., 0.), position_error_m=0., yaw_error_rad=0.)
        unrelated_revision = evaluate_occupancy(mapped, replace(clearance(), revision=2), now_us=1_100_000)
        restored.update(later, restored.associate(later, FIXED, prior_qualified=True),
            provenance_ids={0: 104}, occupancy=((0, unrelated_revision),))
        self.assertTrue(restored.tracks[0].restored)
        self.assertEqual(restored.tracks[0].occupancy_at(1_100_000, CLOCK).state, UNKNOWN)

    def test_restored_geometry_keeps_its_old_clock_without_losing_id_or_inventing_fresh_age(self):
        original = committed_catalog(); record = original.checkpoint_records()[0]; track = original.tracks[0]
        new_clock = '30000000-0000-4000-8000-000000000001'
        restored = SlotCatalog(context(), clock_id=new_clock)
        restored.restore((record,), {101: (track.candidate.source, None)}, catalog_revision=1, restored_at_us=100_000)
        self.assertEqual(restored.tracks[0].geometry_age_ms(200_000, new_clock), (1 << 32) - 1)
        value = observations((0.,)); new_source = replace(value.source,
            stamp=replace(value.source.stamp, clock_id=new_clock, captured_us=(150_000, 150_010), available_us=200_000))
        value = replace(value, source=new_source, candidates=(replace(value.candidates[0], source=new_source),))
        proof = restored.associate(value, FIXED, prior_qualified=True)
        self.assertEqual(proof.status, 'UNIQUE')
        self.assertTrue(restored.update(value, proof, provenance_ids={0: 102}))
        self.assertEqual(restored.tracks[0].slot_id, 1)
        self.assertEqual(restored.tracks[0].candidate.source.stamp.clock_id, new_clock)

    def test_fixed32_exhaustion_rejects_new_range_without_wrap_or_id_zero(self):
        catalog = SlotCatalog(context(), clock_id=CLOCK)
        with self.assertRaises(ValueError):
            prepared(catalog, batch(), hwm=1 << 32)
        self.assertIsNone(catalog.durable_hwm)

    def test_checkpoint_record_restores_stable_id_and_source_but_not_old_unused_range(self):
        catalog = committed_catalog(); records = catalog.checkpoint_records(); original = catalog.tracks[0]
        self.assertEqual(len(records[0].encode()), 256)
        self.assertEqual(len(records[0].payload), 220)
        restored = SlotCatalog(context(), clock_id=CLOCK)
        restored.restore(records, {101: (original.candidate.source, original.occupancy)},
            catalog_revision=catalog.revision, restored_at_us=1_050_000)
        self.assertEqual(restored.tracks[0].slot_id, original.slot_id)
        self.assertEqual(restored.tracks[0].candidate.source, original.candidate.source)
        self.assertEqual(restored.checkpoint_records()[0].payload, records[0].payload)
        self.assertTrue(restored.tracks[0].restored)
        self.assertIsNone(restored.durable_hwm)
        old = batch()
        self.assertFalse(restored.update(old, restored.associate(old, FIXED, prior_qualified=True), provenance_ids={0: 101}))
        newer = observations((2.,))
        self.assertFalse(restored.update(newer, restored.associate(newer, FIXED, prior_qualified=True), provenance_ids={0: 102}))

    def test_corrupt_or_missing_provenance_restore_is_atomic(self):
        catalog = committed_catalog(); record = catalog.checkpoint_records()[0]
        original = catalog.tracks[0]; payload = bytearray(record.payload); payload[-1] ^= 1
        for records, originals in (((record,), {}), ((replace(record, payload=bytes(payload)),),
                                                  {101: (original.candidate.source, None)})):
            restored = SlotCatalog(context(), clock_id=CLOCK)
            with self.assertRaises(ValueError):
                restored.restore(records, originals, catalog_revision=1, restored_at_us=1_050_000)
            self.assertEqual(restored.tracks, ())
            self.assertEqual(restored.revision, 0)


if __name__ == '__main__':
    unittest.main()
