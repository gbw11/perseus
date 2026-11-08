"""실제 catalog/bridge/mailbox를 통한 채택·원 D 사건 순서의 합성 회귀.

head/COMMITTED/main 반납은 이 시험의 명시 이벤트이며 SQL 내구성이나 native
token 반환 증거가 아니다. 인지 입력도 software 선분/배열이며 물리 수락을 만들지 않는다.
"""

from dataclasses import replace
import struct
import unittest
from unittest.mock import patch
from uuid import UUID

import test_parking as parking_fixture
import test_world_adapter as road_fixture
import test_lane_catalog as lane_fixture
from test_mapper_compute import sample as compute_sample
from test_parking_catalog import publication_context
from xenova_drive_world import schema
from xenova_vehicle.mapping.local_map import EvidenceGrid, GridSpec
from xenova_vehicle.mapping.registration import PoseBox
from xenova_vehicle.perception.calibration import BodyModel
from xenova_vehicle.perception.parking import ParkingAisle
from xenova_vehicle.perception.pipeline import PerceptionResult
from xenova_vehicle.runtime.mapper_catalogs import MapperCatalogs, CatalogNotReady
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.parking_bridge import MetricEvidence
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.storage.contracts import (StorageJob, CatalogHeadSnapshot, CommitReceipt,
    ProvenanceRange, canonical)
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import (EvidenceTable, ContextRecord, ProvenanceRecord,
    CheckpointPayload, PROVENANCE)


def uid(n): return '80000000-0000-4000-8000-%012x' % n


FIXED = PoseBox((0., 0.), (0., 0.), (0., 0.))


class MapperCatalogsTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('actual shared map snapshot needs existing NumPy')
        self.context = publication_context()
        self.mailbox = MapperStorageMailbox(self.context.sha256)
        self.owner = self.make_owner()
        self.grid = EvidenceGrid(GridSpec(20, 20, .05, (-.2, -.2)),
            frame_id=self.context.frame_epoch, calibration_id=parking_fixture.CALIBRATION,
            clock_id=parking_fixture.CLOCK, kind='SYNTHETIC')
        support = np.ones((20, 20), dtype=bool)
        self.grid.set_domain(support, support)
        self.sequence = 0; self.job_number = 100

    def make_owner(self, *, crossing=b'\0', roles=(0,), aisles=None):
        aisles = (ParkingAisle((-.1, 0.), (.9, 0.), 1, .001),) if aisles is None else aisles
        return MapperCatalogs(self.context, mailbox=self.mailbox, clock_id=parking_fixture.CLOCK,
            map_frame_id=self.context.frame_epoch, body=BodyModel(), method_manifest_sha256='c'*64,
            input_kind='SYNTHETIC', parking_aisles=aisles,
            aisle_lane_indices=roles, crossing=crossing)

    def frame(self, *, owner=None, ambiguous=False, evidence_missing=False):
        owner = self.owner if owner is None else owner
        self.sequence += 1; sequence = self.sequence; now = 1_000_000+sequence*10_000
        original = parking_fixture.source(sequence, captured=(now-100_000, now-99_990), available=now)
        stamp = original.stamp
        graph = parking_fixture.graph(source_value=original)
        extras = tuple(parking_fixture.strip(i, ((0., y), (1., y)))
            for i, y in ((3, 0.), (4, -.3), (5, -.6)))
        graph = replace(graph, strips=graph.strips+extras)
        road = road_fixture.road(); h = road.hypotheses[0]
        shapes = {name: tuple((x, y-.3) for x, y in getattr(h, name)) for name in
            ('divider', 'left_center', 'right_center', 'left_outer', 'right_outer')}
        h = replace(h, **shapes, strip_ids=((3,), (4,), (5,)))
        road = replace(road, source_key=stamp.key, hypotheses=(h, h) if ambiguous else (h,),
            status='AMBIGUOUS' if ambiguous else 'VALID')
        summary = dict(kind='SYNTHETIC', calibration_id=parking_fixture.CALIBRATION,
            rgb_source_key=stamp.key, rgb_captured_us=stamp.captured_us, now_us=now,
            geometric_model_qualified=True, quarantined=False)
        result = PerceptionResult(summary, graph, road, None, None,
            self.grid.snapshot(now, clock_id=parking_fixture.CLOCK), None)
        sample = compute_sample(sequence)
        rgb = replace(sample.rgb, sdk_us=now-100_000, device_us=now-100_000, received_j_us=now)
        depth = replace(sample.depth, sdk_us=now-100_000, device_us=now-100_000, received_j_us=now)
        clocks = {name: dict(bridge_sample=dict(j_before_us=str(now), sdk_us=str(now), j_after_us=str(now)),
            sdk_host_timestamp_j_bracket_us=[str(now-100_000), str(now-99_990)])
            for name in ('rgb_video', 'depth_right')}
        sample = CaptureSample(rgb, depth, None, None, clocks)
        job = MapperInput(uid(sequence), 'explicit-catalog-test', sample, now+500_000,
            context_bytes=canonical(dict(context_sha256=owner.context.sha256, storage_context=owner.context.fields)))
        context = ContextRecord(canonical(dict(kind='METRIC', body=dict(profile='explicit-collector-test-1',
            storage_context_sha256=owner.context.sha256))))
        identity = 100+sequence; interval = now, now+10
        raw_key = (uid(90), sequence, now-100_000)
        record = ProvenanceRecord(identity, PROVENANCE.pack(bytes.fromhex(context.sha256), 17, 3, 1, 0,
            UUID(owner.context.pi_host_boot).bytes, *interval, sequence, 0, 0, 0, 0,
            struct.pack('<16sQQ', UUID(raw_key[0]).bytes, *raw_key[1:])+bytes(96), 0, bytes(16)))
        evidence = EvidenceTable(() if evidence_missing else (record,), (context,))
        metric = MetricEvidence((stamp.key,), owner.context.pi_host_boot, interval,
            sequence, 17, raw_keys=(raw_key,), parent_ids=(identity,))
        args = dict(context=owner.context, stamp=stamp, capture_prior=FIXED,
            registration_revision=sequence, prior_qualified=True, metric_evidence=metric,
            lane_provenance_ids=(identity, identity), base_evidence=evidence, now_us=now,
            roi_bounds=(-.2, -.2, .8, .8))
        return job, result, args

    def accept_frame(self, **kwargs):
        job, result, args = self.frame(**kwargs)
        draft = self.owner.prepare(job, result, **args)
        self.owner.accept(draft)
        return draft, args['now_us']

    def heads(self, now, *, lane=1, slot=1):
        self.job_number += 1
        job = StorageJob(self.owner.context, uid(self.job_number), now, now+5_000_000)
        head = CatalogHeadSnapshot(job.job_id, job.context.sha256, job.context.database_uuid,
            job.context.map_epoch, (('LANE', lane, 1), ('SLOT', slot, 1)), now, job.expires_us)
        return head, job

    def complete(self, now, *, result=None):
        pending = self.owner.pending
        dispatch = self.mailbox.main_take(current_context_sha256=self.mailbox.context_sha256, now_us=now+1)
        self.assertIs(dispatch.request, pending.request)
        if result is None:
            request = pending.request
            result = CommitReceipt(request.job.context.database_uuid, request.job.job_id, request.digest,
                'COMMITTED', 1, now+2, request.job.context.sha256)
        self.mailbox.main_complete(dispatch, result, completed_us=now+3)
        self.mailbox.main_release(dispatch)
        self.owner.maintenance(now_us=now+4)
        return pending, result

    def ready(self):
        _, now = self.accept_frame()
        head, job = self.heads(now)
        self.owner.maintenance(now_us=now, head=head, head_job=job, reservation_nonce=uid(201))
        self.complete(now)
        _, now = self.accept_frame()
        head, job = self.heads(now, lane=65)
        self.owner.maintenance(now_us=now, head=head, head_job=job, reservation_nonce=uid(202))
        self.complete(now)
        _, now = self.accept_frame()
        self.job_number += 1
        job = StorageJob(self.owner.context, uid(self.job_number), now, now+5_000_000)
        self.owner.maintenance(now_us=now, provenance_job=job, provenance_nonce=uid(203))
        request = self.owner.pending.request
        grant = ProvenanceRange(request.job, request.reservation_nonce, 1000,
            1000+request.block_count, 1000+request.block_count, now+2)
        self.complete(now, result=grant)
        draft, now = self.accept_frame()
        return draft, now

    def test_tentative_geometry_and_discard_never_offer_or_mutate_accepted_catalog(self):
        job, result, args = self.frame()
        before = self.owner._accepted
        draft = self.owner.prepare(job, result, **args)
        self.assertIs(draft.original_job, job)
        self.assertIs(self.owner._accepted, before)
        self.assertIsNone(self.mailbox.main_peek())
        self.assertIsNone(draft.view.lane)
        self.assertIn('WAIT_COMMITTED_LANE_RANGE', draft.view.reasons)
        with self.assertRaises(CatalogNotReady): self.owner.maintenance(now_us=args['now_us'])
        self.owner.discard(draft)
        self.assertIs(self.owner._accepted, before)
        self.assertEqual(self.owner.view.lane_status, 'UNAVAILABLE')
        self.assertIsNone(self.mailbox.main_peek())

    def test_final_accept_only_swaps_prepared_refs_without_mailbox_lock_copy_or_clock(self):
        job, result, args = self.frame(); draft = self.owner.prepare(job, result, **args)
        with patch.object(self.mailbox, 'compute_offer', side_effect=AssertionError('no offer')), \
                patch.object(self.owner, '_owner', side_effect=AssertionError('no reentry')):
            self.owner.accept(draft)
        self.assertIs(self.owner._accepted, draft.state)
        self.assertIsNone(self.mailbox.main_peek())

    def test_actual_ranges_bind_lane_ids_before_aisle_and_keep_provenance_blocks_separate(self):
        draft, now = self.ready()
        self.assertEqual(draft.view.lane.lane_ids, (1, 2))
        publication = draft.view.parking
        self.assertIsNotNone(publication)
        self.assertEqual(publication.total_tracks_in_roi, 1)
        slot = schema.ParkingSlot(); slot.ParseFromString(publication.slots[0])
        self.assertEqual(slot.approach_lane_id, 1)
        self.assertEqual(slot.slot_id, 1)
        self.assertEqual(slot.occupancy_state, 0)  # 누락된 depth/FREE는 빈칸 증거가 아니다.
        state = self.owner._accepted
        self.assertLess(max(state.lane.originals()), 1000)
        self.assertGreaterEqual(state.parking.catalog.tracks[0].provenance_id, 1000)
        self.assertEqual(state.parking.catalog.tracks[0].candidate.source.stamp, state.parking.last.geometry.source.stamp)
        self.assertEqual(state.parking.catalog.tracks[0].candidate.source.stamp.sequence, draft.original_job.sample.rgb.sequence)
        self.assertIsNone(self.mailbox.main_peek())

    def test_unconfigured_crossing_or_unbound_aisle_role_never_selects_default_lane(self):
        for crossing, roles, reason in ((None, (0,), 'CROSSING_POLICY_UNCONFIGURED'),
                (b'\0', None, 'PARKING_LANE_ROLES_UNCONFIGURED')):
            with self.subTest(policy=reason):
                owner = self.make_owner(crossing=crossing, roles=roles)
                job, result, args = self.frame(owner=owner)
                draft = owner.prepare(job, result, **args); owner.accept(draft)
                self.assertIn(reason, draft.view.reasons)
                self.assertIsNone(draft.view.parking)
                self.assertEqual(owner._accepted.parking.catalog.tracks, ())
                self.assertIsNone(self.mailbox.main_peek())

    def test_pending_original_request_identity_survives_new_tentative_frame_and_receipt(self):
        _, now = self.accept_frame(); head, job = self.heads(now)
        self.owner.maintenance(now_us=now, head=head, head_job=job, reservation_nonce=uid(201))
        pending = self.owner.pending
        original = self.mailbox.main_peek().request
        job2, result, args = self.frame(); draft = self.owner.prepare(job2, result, **args)
        need, original_job = draft.state.lane.pending_allocation
        self.assertIs(need, pending.need); self.assertIs(original_job, original.job)
        self.assertIs(self.mailbox.main_peek().request, original)
        self.owner.accept(draft)
        self.assertIs(self.owner.pending.request, original)
        self.complete(now+10)
        self.assertEqual(self.owner._accepted.lane.available_ids, 64)
        self.assertIs(self.owner._accepted.lane.accepted_range[1], original.job)

    def test_false_current_late_old_commit_cleans_transition_without_issuing_old_range(self):
        _, now = self.accept_frame(); head, job = self.heads(now)
        self.owner.maintenance(now_us=now, head=head, head_job=job, reservation_nonce=uid(201))
        dispatch = self.mailbox.main_take(current_context_sha256=self.context.sha256, now_us=now+1)
        request = dispatch.request
        newer = publication_context(self.context, revision=2, scene=dict(admission_id=uid(210), revision='1'))
        self.assertFalse(self.owner.replace_publication_context(newer, now_us=now+2))
        result = CommitReceipt(request.job.context.database_uuid, request.job.job_id, request.digest,
            'COMMITTED', 1, now+3, request.job.context.sha256)
        self.mailbox.main_complete(dispatch, result, completed_us=now+4); self.mailbox.main_release(dispatch)
        self.owner.maintenance(now_us=now+5)
        self.assertEqual(self.owner.context, newer)
        self.assertEqual(self.owner._accepted.lane.available_ids, 0)
        self.assertIsNone(self.owner._accepted.lane.accepted_range)
        delivered = self.owner.take_delivery()
        self.assertFalse(delivered.current); self.assertIs(delivered.result, result)
        self.assertEqual(result.origin_context_sha256, self.context.sha256)

    def test_scene_only_transition_preserves_accepted_ids_cursors_original_ages_and_requests(self):
        draft, now = self.ready(); state = self.owner._accepted
        lane_range = state.lane.accepted_range; slot_range = state.parking.catalog.accepted_range
        lane_ids = draft.view.lane.lane_ids; track = state.parking.catalog.tracks[0]
        provenance = state.parking._provenance; cursor = state.parking._next_provenance
        newer = publication_context(self.context, revision=2, scene=dict(admission_id=uid(211), revision='1'))
        self.assertTrue(self.owner.replace_publication_context(newer, now_us=now+1))
        current = self.owner._accepted
        self.assertIs(current.lane.accepted_range, lane_range)
        self.assertIs(current.parking.catalog.accepted_range, slot_range)
        self.assertIs(current.parking._provenance, provenance)
        self.assertEqual(current.parking._next_provenance, cursor)
        self.assertIs(current.parking.catalog.tracks[0], track)
        self.assertEqual(current.lane.models[0].lane_ids, lane_ids)
        self.assertEqual(track.candidate.source.stamp.captured_us, (940_000, 940_010))
        self.assertIsNone(self.owner.view.lane)
        self.assertEqual(lane_range[1].context.sha256, self.context.sha256)

    def test_current_ambiguous_observation_never_returns_stale_latest_as_current(self):
        old, _ = self.ready()
        draft, _ = self.accept_frame(ambiguous=True)
        self.assertEqual(draft.view.lane_status, 'AMBIGUOUS')
        self.assertIsNone(draft.view.lane); self.assertIsNone(draft.view.parking)
        self.assertIsNotNone(self.owner._accepted.lane.latest)
        self.assertEqual(self.owner._accepted.lane.latest.lane_ids, old.view.lane.lane_ids)

    def test_missing_original_collector_closure_rejects_before_any_offer_or_acceptance(self):
        job, result, args = self.frame(evidence_missing=True)
        before = self.owner._accepted
        with self.assertRaises(CatalogNotReady): self.owner.prepare(job, result, **args)
        self.assertIs(self.owner._accepted, before)
        self.assertIsNone(self.mailbox.main_peek())

    def test_expired_unstarted_mailbox_job_returns_exact_terminal_without_grant(self):
        _, now = self.accept_frame(); head, job = self.heads(now)
        self.owner.maintenance(now_us=now, head=head, head_job=job, reservation_nonce=uid(201))
        request = self.owner.pending.request
        self.assertIsNone(self.mailbox.main_take(current_context_sha256=self.context.sha256,
            now_us=request.job.expires_us))
        self.owner.maintenance(now_us=request.job.expires_us)
        self.assertEqual(self.owner._accepted.lane.available_ids, 0)
        delivery = self.owner.take_delivery()
        self.assertIs(delivery.request, request); self.assertFalse(delivery.current)
        self.assertEqual(delivery.result.reason, 'EXPIRED')

    def test_other_compute_storage_owner_completion_is_not_consumed_by_this_port(self):
        _, now = self.accept_frame()
        external_job = StorageJob(self.context, uid(230), now, now+1000)
        external = CheckpointRequest(external_job, CheckpointPayload.build(external_job, durable_cutoff_pi_us=now))
        self.mailbox.compute_offer(external, now_us=now)
        self.assertEqual(self.owner.maintenance(now_us=now), 'WAIT_OTHER_D_JOB')
        self.assertIsNone(self.owner.pending)
        self.assertIs(self.mailbox.main_peek().request, external)

    def test_checkpoint_is_prepared_only_after_accept_and_keeps_one_original_request_until_offer(self):
        _, now = self.ready(); job = StorageJob(self.owner.context, uid(240), now, now+5_000_000)
        request = self.owner.prepare_checkpoint(job, base_evidence=EvidenceTable(), now_us=now,
            durable_cutoff_pi_us=now+10)
        self.assertIsNone(self.mailbox.main_peek())
        self.owner.maintenance(now_us=now)
        self.assertIs(self.mailbox.main_peek().request, request)
        self.assertIs(self.owner.pending.request, request)
        self.assertIs(self.owner._accepted.parking._pending_checkpoint, request)

    def test_roi_over_eight_preserves_capacity_limited_meta_without_nearest_truncation(self):
        self.ready()
        from test_parking_catalog import shifted
        catalog = self.owner._accepted.parking.catalog; original = catalog.tracks[0]
        # 실제 SQL/발행 이력이 아닌 명시한 합성 과거 inventory다. wrapper의 cap 전달만 본다.
        for i in range(1, 9):
            catalog._tracks[i+1] = replace(original, slot_id=i+1,
                candidate=shifted(original.candidate, i*.5))
        job, result, args = self.frame(); args['roi_bounds'] = (-.2, -.2, 5.2, .8)
        draft = self.owner.prepare(job, result, **args); self.owner.accept(draft)
        publication = draft.view.parking
        self.assertEqual(publication.total_tracks_in_roi, 9)
        self.assertEqual(publication.slots, ())
        meta = schema.ParkingSetMeta(); meta.ParseFromString(publication.meta)
        self.assertFalse(meta.complete); self.assertTrue(meta.capacity_limited)
        self.assertEqual(meta.track_count_in_roi, 9)

    def test_actual_referenced_load_restores_catalog_but_not_unused_range_or_current_candidate(self):
        catalog, original = lane_fixture.populated(context=self.context)
        loaded = lane_fixture.loaded_state(catalog)
        self.owner.restore_loaded(loaded, now_us=1_200_000)
        restored = self.owner._accepted
        self.assertEqual(restored.lane.models[0].lane_ids, original.lane_ids)
        self.assertEqual(restored.lane.models[0].source, original.source)
        self.assertEqual(restored.lane.available_ids, 0)
        self.assertIsNone(restored.lane.accepted_range)
        self.assertIsNone(restored.parking._provenance)
        self.assertEqual(self.owner.view.lane_status, 'WAIT_CURRENT_OBSERVATION')
        self.assertIsNone(self.owner.view.lane); self.assertIsNone(self.owner.view.parking)

    def test_lane_only_checkpoint_header_uses_actual_committed_range_without_fabricated_head(self):
        self.owner = self.make_owner(aisles=(), roles=())
        _, now = self.accept_frame(); head, job = self.heads(now)
        self.owner.maintenance(now_us=now, head=head, head_job=job, reservation_nonce=uid(201))
        self.complete(now)
        self.assertEqual(head.heads[0][1], 1)
        self.assertEqual(self.owner._accepted.parking._head_values, (65, 1))
        _, now = self.accept_frame()
        job = StorageJob(self.owner.context, uid(245), now, now+5_000_000)
        request = self.owner.prepare_checkpoint(job, base_evidence=EvidenceTable(), now_us=now,
            durable_cutoff_pi_us=now+10)
        self.assertEqual(request.payload.evidence.catalog_highwaters, (65, 1))
        self.assertEqual(tuple(r.kind for r in request.payload.evidence.catalog), ('LANE', 'LANE'))
        self.assertIsNone(self.mailbox.main_peek())


if __name__ == '__main__':
    unittest.main()
