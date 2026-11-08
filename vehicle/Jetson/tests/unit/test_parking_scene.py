"""실제 Bridge 생성 문맥의 좁은 semantic consumer 회귀.

기존 software Scene/line/sensor fixture와 실제 임시 SQLite 예약을 재사용한다.
등록·source·parent 원문만 검사하며 실물 보정, 현재 FREE 또는 FROZEN 권위를
발급하지 않는다. depth가 섞인 GEOMETRY를 역사 RGB-only로 바꾸지도 않는다.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
import sys
import unittest
from uuid import UUID

import test_scene as scene_fixture
from test_parking import graph
from xenova_vehicle.mapping.local_map import EvidenceGrid, GridSpec
from xenova_vehicle.mapping.registration import PoseBox
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.perception.parking import ParkingAisle, SlotSource
from xenova_vehicle.perception.pipeline import PerceptionResult
from xenova_vehicle.runtime.parking_bridge import MetricEvidence, ParkingBridge
from xenova_vehicle.runtime.parking_scene import (ApprovedParkingMethods, ParkingRecordSources,
    ParkingSceneError, record_sources, validate_parking_method)
from xenova_vehicle.runtime.scene import SceneEvidenceScope
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.storage.contracts import CatalogReservation, StorageJob, canonical, json_bytes
from xenova_vehicle.storage.records import CheckpointPayload, ContextRecord, EvidenceTable, PROVENANCE, ProvenanceRecord


@unittest.skipUnless(sys.platform == 'linux', '실제 기존 D SQLite 예약은 대상 Linux에서 한다')
class ParkingSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.f = scene_fixture.MapperSceneTests(methodName='runTest')
        cls.addClassCleanup(cls.f.doCleanups)
        cls.f.setUp()
        f = cls.f; scope = f.scope; raw = f.raw.fields; context = f.context
        cls.scopes = (scope,)
        cls.frame_id = 'odom:' + context.odom_epoch
        cls.source = SlotSource(FrameStamp(raw['acquisition_worker'], raw['stream_instances']['rgb_video'],
            1, raw['clock_epoch'], (1_010_000, 1_010_002), f.now), f.calibration.sha256,
            context.map_epoch, cls.frame_id, context.odom_epoch, 1, 1, True, 'SYNTHETIC')
        cls.bridge = ParkingBridge(context, clock_id=raw['clock_epoch'], map_frame_id=cls.frame_id,
            body=f.calibration.body, method_manifest_sha256=scope.method_manifest_sha256,
            input_kind='SYNTHETIC', calibration_profile=f.calibration)

        def job(number):
            return StorageJob(context, scene_fixture.uid(number), f.now, f.now + 5_000_000)

        lane = CatalogReservation(context.database_uuid, context.map_epoch, 'LANE', 1, 1, 3,
            scene_fixture.uid(900), context.mapper_worker, context.sha256)
        reservation_job = job(901)
        reservation = CheckpointPayload.build(reservation_job,
            evidence=EvidenceTable(reservations=(lane,), catalog_highwaters=(3, 1)), intent='CATALOG_RESERVATION')
        if f.store.commit(reservation_job, reservation, current_context_sha256=context.sha256).status != 'COMMITTED':
            raise AssertionError('actual fixture lane reservation did not commit')
        provenance_job = job(902)
        request = cls.bridge.prepare_provenance(provenance_job, scene_fixture.uid(903), block_count=128)
        grant = f.store.reserve_provenance(provenance_job, request.reservation_nonce, block_count=128,
            current_context_sha256=context.sha256)
        if not cls.bridge.accept_provenance(request, grant, now_us=f.now):
            raise AssertionError('actual fixture provenance grant was not accepted')
        grid = EvidenceGrid(GridSpec(20, 20, .05, (-.2, -.2)), frame_id=cls.frame_id,
            calibration_id=f.calibration.sha256, clock_id=raw['clock_epoch'], kind='SYNTHETIC')
        domain = np.ones((20, 20), dtype=bool); grid.set_domain(domain, domain)
        grid.add_free(tuple((x, y) for y in range(20) for x in range(20)), cls.source.stamp,
            view_id='explicit-synthetic-certificate', origin_xy=(0., -.1), certificate_complete=True)
        summary = dict(kind='SYNTHETIC', calibration_id=f.calibration.sha256, rgb_source_key=cls.source.stamp.key,
            rgb_captured_us=cls.source.stamp.captured_us, captured_us=cls.source.stamp.captured_us,
            now_us=f.now, geometric_model_qualified=True, quarantined=False)
        result = PerceptionResult(summary, graph(source_value=cls.source), None, None, None,
                                 grid.snapshot(f.now, clock_id=raw['clock_epoch']), None)
        metric = MetricEvidence((cls.source.stamp.key,), context.pi_host_boot, (1_010_000, 1_010_002), 1, 3,
            raw_keys=((raw['stream_instances']['rgb_video'], 1, 1_010_000),
                      (raw['stream_instances']['depth_right'], 1, 1_010_001)))
        args = dict(stamp=cls.source.stamp, capture_prior=PoseBox((0., 0.), (0., 0.), (0., 0.)),
            aisles=(ParkingAisle((-.1, 0.), (.9, 0.), 1, .001, approach_lane_id=1),),
            registration_revision=1, prior_qualified=True, now_us=f.now, metric_evidence=metric)
        cls.bridge.process(result, **args)
        range_job = job(904)
        head = f.store.catalog_heads(range_job, current_context_sha256=context.sha256)
        allocation = cls.bridge.prepare_range(head, range_job, scene_fixture.uid(905), now_us=f.now)
        receipt = f.store.commit(allocation.job, allocation.payload, current_context_sha256=context.sha256)
        if not cls.bridge.accept_range(allocation, receipt, now_us=f.now):
            raise AssertionError('actual fixture slot range was not accepted')
        if not cls.bridge.process(result, **args).geometry_updated:
            raise AssertionError('actual Bridge fixture did not produce geometry')
        cls.checkpoint = cls.bridge.build_checkpoint(job(906), now_us=f.now, durable_cutoff_pi_us=1_010_002)
        cls.evidence = cls.checkpoint.payload.evidence
        cls.approved = validate_parking_method(cls.bridge.method, cls.scopes)
        contexts = {item.sha256: item for item in cls.evidence.contexts}
        cls.direct = next(row for row in cls.evidence.provenance
                          if contexts[row.context_sha256] == cls.bridge.method and PROVENANCE.unpack(row.payload)[2] == 3)
        cls.aggregate = next(row for row in cls.evidence.provenance
            if json_bytes(contexts[row.context_sha256].payload, 4096)['body']['profile'] == 'parking-originals-1')
        cls.originals = contexts[cls.aggregate.context_sha256]

    def consume(self, record=None, context=None, evidence=None, *, scopes=None, approved=None):
        return record_sources(self.aggregate if record is None else record,
            self.originals if context is None else context, evidence=self.evidence if evidence is None else evidence,
            scopes=self.scopes if scopes is None else scopes,
            approved_methods=self.approved if approved is None else approved)

    def direct_method(self, value):
        context = ContextRecord(canonical(value))
        data = list(PROVENANCE.unpack(self.direct.payload)); data[0] = bytes.fromhex(context.sha256)
        record = ProvenanceRecord(self.direct.provenance_id, PROVENANCE.pack(*data))
        return record, context, EvidenceTable((record,), (context,))

    def originals_value(self, value):
        context = ContextRecord(canonical(value))
        rows = []
        for row in self.evidence.provenance:
            if row.context_sha256 == self.originals.sha256:
                data = list(PROVENANCE.unpack(row.payload)); data[0] = bytes.fromhex(context.sha256)
                row = ProvenanceRecord(row.provenance_id, PROVENANCE.pack(*data))
            rows.append(row)
        evidence = replace(self.evidence, provenance=tuple(rows),
            contexts=tuple(context if item == self.originals else item for item in self.evidence.contexts))
        record = next(row for row in rows if row.provenance_id == self.aggregate.provenance_id)
        return record, context, evidence

    def test_actual_bridge_method_originals_and_no_tof_masks_are_preserved(self):
        self.assertIs(self.approved.actual_method, self.bridge.method)
        direct = self.consume(self.direct, self.bridge.method)
        self.assertEqual((direct.actual_mask, direct.wire_mask), (3, 3))
        self.assertIs(direct.scope, self.f.scope)
        # depth bit는 유지되어 caller HISTORY의 RGB-only 조건에 맞지 않는다.
        self.assertTrue(direct.actual_mask & 2)
        aggregate = self.consume()
        self.assertEqual((aggregate.actual_mask, aggregate.wire_mask), (0, 0))
        self.assertIs(aggregate.method, self.bridge.method)
        self.assertIs(aggregate.scope, self.f.scope)
        with self.assertRaises(FrozenInstanceError):
            self.approved.prototype = b'changed'
        with self.assertRaises(ParkingSceneError):
            ApprovedParkingMethods()
        with self.assertRaises(ParkingSceneError):
            ParkingRecordSources()

    def test_exact_method_assets_full_context_body_and_policy_are_required(self):
        for change in ('unknown', 'context', 'calibration', 'body', 'geometry', 'association', 'frame'):
            with self.subTest(change=change):
                value = json_bytes(self.bridge.method.payload, 4096); body = value['body']
                if change == 'unknown': body['extra'] = 1
                elif change == 'context': body['storage_context_sha256'] = '9' * 64
                elif change == 'calibration': body['calibration_profile_sha256'] = '9' * 64
                elif change == 'body': body['body']['width'] += .001
                elif change == 'geometry': body['geometry_profile']['max_line_residual_m'] += .001
                elif change == 'association': body['association_profile']['residual_m'] += .001
                else: body['map_frame_id'] = 'odom:' + scene_fixture.uid(999)
                record, context, evidence = self.direct_method(value)
                with self.assertRaises(ParkingSceneError):
                    self.consume(record, context, evidence)
        for value in ((), (object(),)):
            with self.assertRaises(ParkingSceneError):
                validate_parking_method(self.bridge.method, value)

    def test_scene_only_history_uses_exact_known_old_context_and_same_approved_policy(self):
        fields = json_bytes(self.f.context.source_binding, 2048); fields['source_binding_revision'] = '2'
        context = replace(self.f.context, source_binding=canonical(fields))
        scope = SceneEvidenceScope(context, self.f.raw, self.f.calibration, self.f.scope.method_manifest_sha256,
                                   historical_scopes=(self.f.scope,))
        value = json_bytes(self.bridge.method.payload, 4096); value['body']['storage_context_sha256'] = context.sha256
        method = ContextRecord(canonical(value))
        approved = validate_parking_method(method, (scope, self.f.scope))
        result = self.consume(scopes=(scope, self.f.scope), approved=approved)
        self.assertIs(result.scope, self.f.scope)
        self.assertIs(result.method, self.bridge.method)
        with self.assertRaises(ParkingSceneError):
            self.consume(scopes=(scope,), approved=approved)

    def test_original_method_dependency_and_exact_entry_grammar_cannot_be_inferred(self):
        missing = replace(self.evidence, contexts=tuple(item for item in self.evidence.contexts if item != self.bridge.method))
        with self.assertRaises(ParkingSceneError):
            self.consume(evidence=missing)
        for change in ('asset_edge', 'storage', 'entry', 'catalog_bool', 'id_bool'):
            with self.subTest(change=change):
                value = json_bytes(self.originals.payload, 4096); body = value['body']
                if change == 'asset_edge': body['method_context_sha256'] = self.f.scope.method_manifest_sha256
                elif change == 'storage': body['storage_context_sha256'] = '9' * 64
                elif change == 'entry': body['entries'][0]['extra'] = 1
                elif change == 'catalog_bool': body['catalog_revision'] = True
                else: body['entries'][0]['provenance_id'] = True
                record, context, evidence = self.originals_value(value)
                with self.assertRaises(ParkingSceneError):
                    self.consume(record, context, evidence)

    def test_source_sha_and_typed_camera_origin_are_both_checked(self):
        value = json_bytes(self.originals.payload, 4096)
        value['body']['sources'][0]['sha256'] = '9' * 64
        record, context, evidence = self.originals_value(value)
        with self.assertRaises(ParkingSceneError):
            self.consume(record, context, evidence)
        # raw UUID/seq가 같아도 parent의 A/full RawBinding이 다르면 빌려 쓸 수 없다.
        fields = self.f.raw.fields
        fields['acquisition_worker'] = scene_fixture.uid(996)
        other_raw = RawBinding(fields)
        binding = json_bytes(self.f.context.source_binding, 2048)
        binding['raw_binding_sha256'] = other_raw.sha256
        other_context = replace(self.f.context, source_binding=canonical(binding))
        other_scope = SceneEvidenceScope(other_context, other_raw, self.f.calibration,
                                         self.f.scope.method_manifest_sha256)
        method_fields = json_bytes(self.bridge.method.payload, 4096)
        method_fields['body']['storage_context_sha256'] = other_context.sha256
        other_method = ContextRecord(canonical(method_fields))
        data = list(PROVENANCE.unpack(self.direct.payload)); data[0] = bytes.fromhex(other_method.sha256)
        other_parent = ProvenanceRecord(self.direct.provenance_id, PROVENANCE.pack(*data))
        evidence = replace(self.evidence,
            provenance=tuple(other_parent if row == self.direct else row for row in self.evidence.provenance),
            contexts=(*self.evidence.contexts, other_method))
        with self.assertRaises(ParkingSceneError):
            self.consume(evidence=evidence, scopes=(*self.scopes, other_scope))
        value = json_bytes(self.originals.payload, 4096)
        wrong = replace(self.source, stamp=replace(self.source.stamp, stream_id=scene_fixture.uid(998)))
        old = self.source.sha256
        for row in value['body']['sources']:
            if row['sha256'] == old: row.update(sha256=wrong.sha256, fields=asdict(wrong))
        for entry in value['body']['entries']:
            if entry['geometry_source'] == old: entry['geometry_source'] = wrong.sha256
            if entry['occupancy'] is not None and entry['occupancy']['source'] == old:
                entry['occupancy']['source'] = wrong.sha256
        record, context, evidence = self.originals_value(value)
        with self.assertRaises(ParkingSceneError):
            self.consume(record, context, evidence)

    def test_aggregate_parent_union_interval_sequence_and_closure_are_exact(self):
        for field, value in ((1, 1), (6, self.aggregate.pi_lower + 1),
                             (7, self.aggregate.pi_upper + 1), (8, self.aggregate.observation_sequence + 1)):
            with self.subTest(field=field):
                data = list(PROVENANCE.unpack(self.aggregate.payload)); data[field] = value
                record = ProvenanceRecord(self.aggregate.provenance_id, PROVENANCE.pack(*data))
                evidence = replace(self.evidence, provenance=tuple(record if row == self.aggregate else row
                    for row in self.evidence.provenance))
                with self.assertRaises(ParkingSceneError):
                    self.consume(record, evidence=evidence)
        missing = replace(self.evidence, provenance=tuple(row for row in self.evidence.provenance
            if row.provenance_id != self.aggregate.parents[0]))
        with self.assertRaises(ParkingSceneError):
            self.consume(evidence=missing)

    def test_generic_metric_profile_and_foreign_pi_raw_inventory_are_not_parking_sources(self):
        value = json_bytes(self.bridge.method.payload, 4096); value['body']['profile'] = 'generic-metric'
        record, context, evidence = self.direct_method(value)
        with self.assertRaises(ParkingSceneError):
            self.consume(record, context, evidence)
        data = list(PROVENANCE.unpack(self.direct.payload)); raw = bytearray(data[-3])
        raw[:16] = UUID(scene_fixture.uid(997)).bytes; data[-3] = bytes(raw)
        record = ProvenanceRecord(self.direct.provenance_id, PROVENANCE.pack(*data))
        evidence = EvidenceTable((record,), (self.bridge.method,))
        with self.assertRaises(ParkingSceneError):
            self.consume(record, self.bridge.method, evidence)
