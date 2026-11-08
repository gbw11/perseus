"""main의 원 admission과 Processor 후보 채택을 분리하는 software 회귀다.

기존 명시 wire/clock/profile과 실제 인지·mapping 경로를 사용한다. outbox의 작은
opaque bytes는 참조 철회 시험이며 World 수락, 실물 시계·보정·FREE 승인이 아니다.
"""

from hashlib import sha256
import json
import unittest

import test_mapper_processor_mapping as mapping_fixture
import test_mapper_resident_scene as scene_fixture
import test_mapper_runtime_profile as runtime_fixture
from xenova_drive_world import decode, encode, schema
from xenova_vehicle.runtime.mapper_compute import MapperInput, MapperCancelled
from xenova_vehicle.runtime.mapper_publication import PublicationReport


def checkpoint(*_):
    return None


class MapperAdmissionProcessorTests(unittest.TestCase):
    def fixture(self, kind=runtime_fixture.MapperRuntimeProfileTests):
        result = kind(methodName='runTest')
        self.addCleanup(result.doCleanups)
        result.setUp()
        return result

    @staticmethod
    def without_snapshot(item):
        return MapperInput(item.job_id, item.source_generation, item.sample, item.expires_us,
            parser_cap_us=item.parser_cap_us, context_bytes=item.context_bytes)

    def test_missing_snapshot_keeps_diagnostic_separate_from_accepted_metric_map(self):
        f = self.fixture()
        processor = f.processor()
        original = processor._accepted
        item = self.without_snapshot(f.item())
        self.assertIsNone(item.sensor_snapshot)
        self.assertFalse(processor.admission_fenced(0, 'INITIAL_ADMISSION'))
        summary = json.loads(processor.geometry(item, None, checkpoint))['latest']
        self.assertIn('SENSOR_SNAPSHOT_UNAVAILABLE', summary['metric_input_reasons'])
        self.assertFalse(summary['metric_ready'] or summary['world_valid'] or summary['live_authority'])
        self.assertIsNone(processor._latest_source_inputs)
        self.assertIsNone(processor._staged[2])
        self.assertFalse(processor._staged[6])
        self.assertIsNotNone(processor._staged[3].pipeline.grid)
        self.assertFalse(hasattr(processor, '_accepted_sensors'))
        processor.accepted_at(item, f.now)
        self.assertIs(processor._accepted, original)
        self.assertIsNone(original.pipeline.grid)
        self.assertEqual(processor._diagnostic.processed, 1)

    def test_cancelled_mapping_keeps_original_main_admission_records_and_hwm(self):
        f = self.fixture(mapping_fixture.MapperProcessorMappingTests)
        f.grant()
        item = f.fixture.item()
        snapshot = item.sensor_snapshot
        self.assertIsNotNone(snapshot)
        owner = f.fixture.sensor_owner
        originals = owner.vehicle_admissions
        latest = owner.sensors.latest_vehicle
        accepted = f.processor._accepted
        summary = json.loads(f.processor.geometry(item, None, checkpoint))['latest']
        self.assertTrue(summary['mapping_ready'], summary.get('mapping_reason'))
        self.assertIs(f.processor._staged[2], snapshot)
        f.processor.cancelled(item, 'SOURCE_EXPIRED')
        self.assertIs(f.processor._accepted, accepted)
        self.assertIsNone(accepted.pipeline.grid)
        self.assertIs(owner.sensors.latest_vehicle, latest)
        self.assertTrue(all(a is b for a, b in zip(owner.vehicle_admissions, originals, strict=True)))
        self.assertTrue(all(any(row is known for known in owner.vehicle_admissions)
            for row in snapshot.vehicle_admissions))
        self.assertIsNone(f.processor._mapping_draft)
        self.assertIsNone(f.processor._staged)

    def test_original_lease_expiry_before_compute_is_not_renewed_by_the_frame_budget(self):
        f = self.fixture()
        value = f.fixture.snapshot()
        probe = decode(f.fixture.probe_raw, schema.MapperClockProbe)
        # 명시 software returned trust다. 원 frame E보다 먼저 끝나는 lease를 최초 입력한다.
        probe.mapper_trust.expires_pi_us = 1_170_000
        wire = encode(probe)
        key = sha256(wire).hexdigest()
        value['probes'][key] = f.fixture.row(wire, key=key, received=f.now-1)
        value['active_probe_sha256'] = key
        item = f.item(snapshot=value)
        self.assertIsNotNone(item.sensor_snapshot)
        lease = item.sensor_snapshot.metric_inputs.clock_lease
        self.assertIsNotNone(lease)
        expiry = item.expires_us
        f.now += 20_000
        self.assertLess(f.now, expiry)
        with self.assertRaises(ValueError):
            lease.pi_interval_to_j((lease.probe_pi_send_us, lease.probe_pi_send_us), f.now)
        processor = f.processor()
        original = processor._accepted
        summary = json.loads(processor.geometry(item, None, checkpoint))['latest']
        self.assertIn('PI_J_CLOCK_UNAVAILABLE', summary['metric_input_reasons'])
        self.assertFalse(processor._staged[6])
        self.assertIsNone(processor._latest_source_inputs.clock_lease)
        self.assertEqual(item.expires_us, expiry)
        self.assertEqual(item.sensor_snapshot.metric_inputs.clock_lease.raw_probe_bytes, wire)
        processor.accepted_at(item, f.now)
        self.assertIs(processor._accepted, original)

    def test_main_fence_blocks_old_offers_and_compute_hook_revokes_scene_without_resetting_map(self):
        f = self.fixture(scene_fixture.MapperResidentSceneTests)
        f.establish_original_grant()
        processor = f.processor
        core, before = f.map_snapshot()
        frame_sequence = processor._frame_sequence
        owner = f.runtime.sensor_owner
        originals = owner.vehicle_admissions
        context = f.base.context
        publication = sha256(context.source_binding).hexdigest()
        reports = tuple(PublicationReport(context, kind, b'opaque-reference-only', f.runtime.now,
            f.runtime.now+100_000, publication, 'a'*64 if kind == 'SURVEY_SUMMARY' else None)
            for kind in ('WORLD_CANDIDATE', 'SURVEY_SUMMARY'))
        for report in reports:
            self.assertTrue(f.publications.offer(report, admission_revision=0))
        self.assertTrue(f.publications.fence_admission(1))
        for report in reports:
            self.assertFalse(f.publications.offer(report, admission_revision=processor._admission_revision))
        self.assertTrue(processor.admission_fenced(1, 'CLOCK_UNAVAILABLE'))
        self.assertEqual(processor._admission_revision, 1)
        self.assertIsNone(processor._latest_source_inputs)
        self.assertFalse(processor._scene.scene.eligible)
        self.assertEqual(processor._scene.scene.blocked_reason, 'SOURCE')
        self.assertIsNone(processor._scene_summary_sent)
        for kind in ('WORLD_CANDIDATE', 'SURVEY_SUMMARY'):
            self.assertIsNone(f.publications.peek(kind, f.runtime.now))
        f.assert_preserved_map(core, before)
        self.assertEqual(processor._frame_sequence, frame_sequence)
        self.assertTrue(all(a is b for a, b in zip(owner.vehicle_admissions, originals, strict=True)))
        self.assertFalse(processor.admission_fenced(1, 'DUPLICATE'))
        with self.assertRaises(ValueError):
            processor.admission_fenced(0, 'OLD')
        old_item = f.runtime.item(2)
        with self.assertRaisesRegex(MapperCancelled, 'ADMISSION_FENCE'):
            processor.geometry(old_item, None, checkpoint)
        self.assertIs(processor._accepted.pipeline, core)


if __name__ == '__main__':
    unittest.main()
