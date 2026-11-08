"""실제 SQLite 128 tile/4 LOAD와 새 M의 역사 catalog 복원 연결이다.

기하·센서·보정 자격은 명시한 software fixture다. 실제 SQL/페이지 codec를 쓰지만
카메라 물리 수락, 새 scene/clock, 빈 공간 사용권이나 처리율을 증명하지 않는다.
"""

from dataclasses import replace
import math
import struct
import sys
import unittest
from uuid import UUID, uuid4

import test_map_checkpoint as map_fixture
import test_parking as parking_fixture
import test_world_adapter as road_fixture
from xenova_vehicle.mapping.lane_catalog import LaneCatalog, LaneSource
from xenova_vehicle.mapping.registration import PoseBox
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.parking import (SlotSource, ClearanceMap, evaluate_occupancy,
    CLEAR_REGION)
from xenova_vehicle.runtime.map_checkpoint import CheckpointPages, SourceComplete
from xenova_vehicle.runtime.mapper_catalogs import MapperCatalogs
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.parking_bridge import ParkingBridge
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.scene import SceneEvidenceScope, SceneError
from xenova_vehicle.runtime.storage_messages import make_load_request
from xenova_vehicle.storage.contracts import StorageContext, StorageJob, canonical, json_bytes
from xenova_vehicle.storage.mapper_state import encode_mapper_state, decode_mapper_state
from xenova_vehicle.storage.records import (CELL, PROVENANCE, CheckpointPayload, EvidenceTable,
    ProvenanceRecord, TileVersion)


def uid(): return str(uuid4())


def _corrupted_complete(original, **changes):
    """음성 시험 전용 변조다. 양성 자료는 실제 CheckpointPages가 만들었다."""
    result = object.__new__(SourceComplete)
    for name in original.__dataclass_fields__:
        object.__setattr__(result, name, changes.get(name, getattr(original, name)))
    return result


@unittest.skipUnless(sys.platform == 'linux', '실제 기존 D SQLite owner는 Linux에서 검증함')
class CompleteCatalogRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f = map_fixture.MapCheckpointTests(methodName='runTest')
        cls.addClassCleanup(f.doCleanups); f.setUp(); cls.fixture = f
        cls.now = now = f.sensor.now
        # 승인값은 software 가정이며 leaf 자체는 기존 센서 fixture 원 객체다.
        fields = f.calibration.to_dict()
        fields['external_leaves'] = {'front-test-leaf': f.sensor.leaf.sha256}
        cls.calibration = CalibrationProfile.from_dict(fields)
        raw = f.raw.fields; raw['calibration_bundle'] = cls.calibration.sha256
        cls.old_raw = RawBinding(raw)
        publication = json_bytes(f.context.source_binding, 2048)
        publication['raw_binding_sha256'] = cls.old_raw.sha256
        cls.old_context = replace(f.context, source_binding=canonical(publication))
        cls.old_tof_instance = str(UUID(bytes=f.sensor.instance))
        cls.old_scope = SceneEvidenceScope(cls.old_context, cls.old_raw, cls.calibration, 'e'*64,
            tof_sources=(('front-test-leaf', f.sensor.leaf, cls.old_tof_instance, 1),))
        def job(context=cls.old_context): return StorageJob(context, uid(), now, now+5_000_000)
        cls.job = staticmethod(job)
        def proof(identity, kind, mask, context, instance):
            raw_key = struct.pack('<16sQQ', UUID(instance).bytes, 1, 400000)
            return ProvenanceRecord(identity, PROVENANCE.pack(bytes.fromhex(context.sha256), mask, kind, 1, 0,
                UUID(cls.old_context.pi_host_boot).bytes, 1_100_050, 1_100_050, 1,
                0, 0, 0, 0, raw_key+bytes(96), 0, bytes(16)))
        rgb_context = cls.old_scope.context_record('METRIC')
        tof_context = cls.old_scope.context_record('TOF', sensor_id=0)
        cls.rgb_proof = proof(f.grant.start_inclusive, 3, 1, rgb_context,
            cls.old_raw.fields['stream_instances']['rgb_video'])
        cls.tof_proof = proof(f.grant.start_inclusive+1, 0, 8, tof_context, cls.old_tof_instance)
        cls.source_evidence = EvidenceTable((cls.rgb_proof, cls.tof_proof), (rgb_context, tof_context))
        stamp = FrameStamp(cls.old_raw.fields['acquisition_worker'], cls.old_raw.fields['stream_instances']['rgb_video'],
            1, cls.old_raw.fields['clock_epoch'], (1_000_000, 1_000_010), now)
        cls.stamp = stamp
        lane = LaneCatalog(cls.old_context, clock_id=stamp.clock_id, calibration_id=cls.calibration.sha256)
        road = replace(road_fixture.road(), source_key=stamp.key)
        source = LaneSource(stamp, cls.calibration.sha256, cls.old_context.map_epoch,
            cls.old_context.frame_epoch, cls.old_context.odom_epoch, 1, 1, True, 'LIVE')
        association = lane.observe(road, source, PoseBox((0., 0.), (0., 0.), (0., 0.)),
            prior_qualified=True, crossing=b'\0')
        lane_job = job(); head = f.store.catalog_heads(lane_job, current_context_sha256=cls.old_context.sha256)
        need = lane.allocation_need(association, head, lane_job, reservation_nonce=uid(), now_us=now)
        payload = CheckpointPayload.build(lane_job, evidence=EvidenceTable(reservations=(need.reservation,),
            catalog_highwaters=(need.reservation.end_exclusive, 1)), intent='CATALOG_RESERVATION')
        receipt = f.store.commit(lane_job, payload, current_context_sha256=cls.old_context.sha256)
        lane.apply_committed_range(need, lane_job, payload, receipt,
            current_context_sha256=cls.old_context.sha256, now_us=now)
        cls.old_lane = lane; cls.model = lane.update(association, provenance_ids=(cls.rgb_proof.provenance_id,)*2)
        bridge = cls.bridge(cls.old_context, stamp.clock_id)
        slot_source = SlotSource(stamp, cls.calibration.sha256, cls.old_context.map_epoch,
            cls.old_context.frame_epoch, cls.old_context.odom_epoch, 1, 1, True, 'LIVE')
        batch = parking_fixture.batch(original=slot_source)
        batch = replace(batch, candidates=tuple(replace(c, approach_lane_id=cls.model.lane_ids[0]) for c in batch.candidates))
        association = bridge.catalog.associate(batch, PoseBox((0., 0.), (0., 0.), (0., 0.)), prior_qualified=True)
        slot_job = job(); head = f.store.catalog_heads(slot_job, current_context_sha256=cls.old_context.sha256)
        need = bridge.catalog.allocation_need(batch, association, head, slot_job, reservation_nonce=uid(), now_us=now)
        payload = CheckpointPayload.build(slot_job, evidence=EvidenceTable(reservations=(need.reservation,),
            catalog_highwaters=(65, need.reservation.end_exclusive)), intent='CATALOG_RESERVATION')
        receipt = f.store.commit(slot_job, payload, current_context_sha256=cls.old_context.sha256)
        bridge.catalog.apply_committed_range(need, slot_job, payload, receipt,
            current_context_sha256=cls.old_context.sha256, now_us=now)
        clearance = parking_fixture.clearance(age_ms=100)
        clearance = replace(clearance, source_domain=slot_source.domain, snapshot_us=now)
        pose=association.assignments[0].pose_envelope
        occupancy = tuple((c.local_id, evaluate_occupancy(parking_fixture.to_map(c,pose.center,
            position_error_m=math.hypot(*pose.widths[:2])/2,yaw_error_rad=pose.widths[2]/2),
            clearance, now_us=now, clock_id=stamp.clock_id)) for c in batch.candidates)
        bridge.catalog.update(batch, association, provenance_ids={c.local_id: cls.rgb_proof.provenance_id for c in batch.candidates},
            occupancy=occupancy)
        cls.original_occupancy = bridge.catalog.tracks[0].occupancy
        cls.original_slot_source = bridge.catalog.tracks[0].candidate.source
        reserve_job = job(); request = bridge.prepare_provenance(reserve_job, uid(), block_count=64)
        grant = f.store.reserve_provenance(reserve_job, request.reservation_nonce, block_count=64,
            current_context_sha256=cls.old_context.sha256)
        bridge.accept_provenance(request, grant, now_us=now)
        head_job = job(); bridge.observe_heads(f.store.catalog_heads(head_job,
            current_context_sha256=cls.old_context.sha256), head_job, now_us=now)
        state_ref, pages = encode_mapper_state({'lane_catalog': lane.state_fields()})
        base = EvidenceTable(cls.source_evidence.provenance, cls.source_evidence.contexts+pages,
            lane.checkpoint_records(), catalog_highwaters=(65, 65))
        unknown = CELL.pack(255, 0, 0, 0., 0., 0., 0, 0, 0, 0, 0, 0)
        hit = CELL.pack(100, 1, 8, .1, .2, .01, cls.tof_proof.provenance_id,
            1_100_050, 1_100_050, 1, 1, 0)
        tiles = tuple(TileVersion(cls.old_context.map_epoch, i, 0, 1,
            (hit if i == 0 else unknown)+unknown*4095) for i in range(128))
        # 앞96개의 원 tile version을 저장한 뒤 최종128개 manifest를 한 번 출판한다.
        for end in (32, 64, 96):
            original = job(); payload = CheckpointPayload.build(original, tiles[end-32:end], cls.source_evidence,
                tile_references=tuple(t.reference for t in tiles[:end]), durable_cutoff_pi_us=1_100_050)
            f.assertEqual(f.store.commit(original, payload, current_context_sha256=cls.old_context.sha256).status, 'COMMITTED')
        final = bridge.build_checkpoint(job(), now_us=now, tiles=tiles[96:],
            tile_references=tuple(t.reference for t in tiles), base_evidence=base,
            durable_cutoff_pi_us=1_100_050, scene={'mapper_state': state_ref})
        f.assertEqual(canonical(decode_mapper_state(json_bytes(final.payload.manifest, 16384)['scene'], final.payload.evidence.contexts)[0]),
            canonical({'lane_catalog': lane.state_fields()}))
        f.assertEqual(f.store.commit(final.job, final.payload, current_context_sha256=cls.old_context.sha256).status, 'COMMITTED')
        cls.reference = canonical(f.store.latest_complete(expires_us=now+5_000_000))
        raw = cls.old_raw.fields
        for name in ('service_instance', 'acquisition_worker', 'pipeline_instance', 'capture_epoch', 'sdk_bridge_epoch', 'clock_epoch'):
            raw[name] = uid()
        raw['stream_instances'] = {name: uid() for name in raw['stream_instances']}
        cls.current_raw = RawBinding(raw)
        publication = json_bytes(cls.old_context.source_binding, 2048)
        publication.update(service_instance=raw['service_instance'], mapper_worker=uid(), mapper_instance=uid(),
            raw_binding_sha256=cls.current_raw.sha256, source_binding_revision='1')
        cls.current_context = replace(cls.old_context, mapper_worker=publication['mapper_worker'],
            origin_mapper_instance=publication['mapper_instance'], source_binding=canonical(publication))
        cls.current_scope = cls.scope(cls.current_context)
        binding = dict(service_instance=raw['service_instance'], storage_worker=uid(), storage_profile_sha256='d'*64,
            database_uuid=cls.current_context.database_uuid, db_schema_version=1)
        pages = None; loaded_pages = []
        for cursor in (0, 32, 64, 96):
            request = make_load_request(binding, mapper_worker=cls.current_context.mapper_worker,
                origin_mapper_instance=cls.current_context.origin_mapper_instance, job_id=uid(), issued_us=now,
                expires_us=now+5_000_000, checkpoint_ref=json_bytes(cls.reference, 2048),
                selection={'kind': 'CHECKPOINT_PAGE', 'cursor': cursor}, purpose='BOOTSTRAP_RESTORE')
            if pages is None: pages = CheckpointPages(cls.reference, request.job.context)
            loaded = f.store.load(request, current_context_sha256=request.job.context.sha256)
            loaded_pages.append(loaded); complete = pages.accept(loaded, now_us=now)
        cls.complete = complete; cls.loaded_pages = tuple(loaded_pages)

    @classmethod
    def scope(cls, context, *, tof=True, method='e'*64, raw=None):
        return SceneEvidenceScope(context, cls.current_raw if raw is None else raw, cls.calibration, method,
            tof_sources=(('front-test-leaf', cls.fixture.sensor.leaf, '90000000-0000-4000-8000-000000000099', 2),) if tof else ())

    @classmethod
    def bridge(cls, context, clock):
        return ParkingBridge(context, clock_id=clock, map_frame_id=context.frame_epoch,
            body=cls.calibration.body, method_manifest_sha256='e'*64, input_kind='LIVE', calibration_profile=cls.calibration)

    def test_actual_four_page_head_and_old_rgb_tof_sources_survive_new_registered_m(self):
        self.assertEqual(len(self.complete.tiles), 128); self.assertEqual(len(self.complete.page_receipts), 4)
        scope = self.current_scope.with_complete_history(self.complete, now_us=self.now)
        request, head = scope.complete_restore_head(self.complete, now_us=self.now)
        self.assertIs(request, self.loaded_pages[-1].request); self.assertIs(head, self.loaded_pages[-1].heads)
        self.assertNotEqual(head.context_sha256, self.current_context.sha256)
        self.assertEqual(head.context_sha256, request.job.context.sha256)
        self.assertNotEqual(self.current_raw.sha256, self.old_raw.sha256)
        contexts = {c.sha256: c for c in self.complete.evidence.contexts}
        self.assertEqual(scope.record_sources(self.rgb_proof, contexts[self.rgb_proof.context_sha256]), (1, 2))
        self.assertEqual(scope.record_sources(self.tof_proof, contexts[self.tof_proof.context_sha256]), (8, 4))
        self.assertEqual(self.tof_proof.pi_lower, 1_100_050)
        history = [old for old in scope._historical if old._tof]
        self.assertEqual(history[0]._tof[0][2:], (self.old_tof_instance, 1))
        self.assertEqual(scope._tof[0][3], 2)
        self.assertEqual(len(scope._historical), 2)
        self.assertTrue(any(json_bytes(c.payload, 4096)['body'].get('profile')=='mapper-state-page-1'
            for c in self.complete.evidence.contexts))
        self.assertTrue(all(json_bytes(old.context_record('RAW').payload, 4096)['body']['profile']=='scene-source-context-1'
            for old in scope._historical))

    def test_issued_catalogs_restore_without_old_unused_ranges_freshness_or_current_target(self):
        owner = MapperCatalogs(self.current_context, mailbox=MapperStorageMailbox(self.current_context.sha256),
            clock_id=self.current_raw.fields['clock_epoch'], map_frame_id=self.current_context.frame_epoch,
            body=self.calibration.body, method_manifest_sha256='e'*64, input_kind='LIVE', calibration_profile=self.calibration)
        scope = owner.restore_complete(self.complete, current_scope=self.current_scope, now_us=self.now)
        self.assertIs(scope.context, self.current_context)
        self.assertEqual(owner._accepted.lane.models[0].lane_ids, self.model.lane_ids)
        self.assertEqual(owner._accepted.lane.models[0].source, self.model.source)
        track = owner._accepted.parking.catalog.tracks[0]
        self.assertEqual(track.slot_id, 1); self.assertTrue(track.restored)
        self.assertEqual(track.candidate.source, self.original_slot_source)
        self.assertEqual(track.occupancy, self.original_occupancy)
        self.assertEqual(self.original_occupancy.state, CLEAR_REGION)
        self.assertEqual(owner._accepted.lane.available_ids, 0)
        self.assertEqual(owner._accepted.parking.catalog.available_ids, 0)
        self.assertIsNone(owner._accepted.parking._provenance)
        self.assertIsNone(owner._head); self.assertIsNone(owner._accepted.lane.latest)
        self.assertEqual(owner.view.lane_status, 'WAIT_CURRENT_OBSERVATION')
        candidates = owner._accepted.parking.publication_candidates(now_us=self.now, roi_bounds=(-1., -1., 2., 2.))
        from xenova_drive_world import schema
        meta = schema.ParkingSetMeta(); meta.ParseFromString(candidates.meta)
        self.assertFalse(meta.complete)

    def test_current_map_full_context_path_still_uses_its_original_loaded_object(self):
        request = map_fixture.LoadRequest(self.job(), self.reference, canonical({'kind': 'CHECKPOINT_PAGE', 'cursor': 0}))
        loaded = self.fixture.store.load(request, current_context_sha256=self.old_context.sha256)
        lane = LaneCatalog(self.old_context, clock_id=self.stamp.clock_id, calibration_id=self.calibration.sha256)
        lane.restore(loaded, now_us=self.now)
        self.assertEqual(lane.models[0].lane_ids, self.model.lane_ids)
        bridge = self.bridge(self.old_context, self.stamp.clock_id); bridge.restore(loaded, now_us=self.now)
        self.assertEqual(bridge.catalog.tracks[0].candidate.source, self.original_slot_source)

    def test_missing_page_or_last_original_expiry_cannot_become_complete_restore(self):
        with self.assertRaises(SceneError):
            self.current_scope.complete_restore_head(_corrupted_complete(self.complete,
                page_receipts=self.complete.page_receipts[:-1]), now_us=self.now)
        with self.assertRaises(SceneError):
            self.current_scope.complete_restore_head(self.complete, now_us=self.loaded_pages[-1].request.job.expires_us)

    def test_last_request_registered_mapper_and_actual_pi_origin_must_match_current_scope(self):
        for changes in ({'mapper_worker': uid()}, {'origin_mapper_instance': uid()}):
            context = replace(self.current_context, **changes)
            publication = json_bytes(context.source_binding, 2048)
            publication.update(mapper_worker=context.mapper_worker, mapper_instance=context.origin_mapper_instance)
            context = replace(context, source_binding=canonical(publication))
            with self.subTest(changes=tuple(changes)), self.assertRaises(SceneError):
                self.scope(context).complete_restore_head(self.complete, now_us=self.now)
        origin = self.current_context.geometry_origin_fields; origin['gateway_instance'] = uid()
        context = replace(self.current_context, geometry_origin=origin)
        with self.assertRaises(SceneError): self.scope(context).complete_restore_head(self.complete, now_us=self.now)

    def test_missing_approved_tof_or_changed_method_raw_asset_stays_not_ready(self):
        with self.assertRaises(SceneError): self.scope(self.current_context, tof=False).with_complete_history(self.complete, now_us=self.now)
        with self.assertRaises(SceneError): self.scope(self.current_context, method='d'*64).with_complete_history(self.complete, now_us=self.now)
        fields = self.current_raw.fields; fields['graph_profile_sha256'] = 'a'*64
        raw = RawBinding(fields); publication = json_bytes(self.current_context.source_binding, 2048)
        publication['raw_binding_sha256'] = raw.sha256
        context = replace(self.current_context, source_binding=canonical(publication))
        with self.assertRaises(SceneError): self.scope(context, raw=raw).with_complete_history(self.complete, now_us=self.now)
