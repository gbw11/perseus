"""최신 참조 포트와 실제 Processor의 미준비·취소 발행 경계를 검산한다.

포트의 작은 bytes는 송신 대기 제어값이며 World 수락 양성이 아니다. World 기하/codec
양성은 test_mapper_world가 맡는다. 여기서 GT나 임의 lane/basis를 Processor에 넣지 않는다.
"""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import unittest
from unittest.mock import patch

import test_mapper_processor_mapping as processor_fixture
from test_parking_catalog import publication_context
from xenova_vehicle.runtime.mapper_compute import MapperCancelled
from xenova_vehicle.runtime.mapper_mapping import MappingContextPort
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_publication import MapperPublicationPort, PublicationReport
from xenova_vehicle.storage.contracts import canonical, json_bytes


def report(context, *, kind='WORLD_CANDIDATE', raw=b'original-control-fixture', issued=100, expires=200):
    return PublicationReport(context, kind, raw, issued, expires, sha256(context.source_binding).hexdigest(),
        grant_sha256='a'*64 if kind == 'SURVEY_SUMMARY' else None)


class MapperPublicationPortTests(unittest.TestCase):
    def setUp(self):
        self.context = publication_context()
        self.context_port = MappingContextPort()
        self.context_port.publish(self.context)
        self.port = MapperPublicationPort(self.context_port)

    def test_latest_per_kind_keeps_original_deadline_and_exact_ack_identity(self):
        old = report(self.context)
        new = report(self.context, raw=b'new-original-bytes', issued=110, expires=180)
        survey = report(self.context, kind='SURVEY_SUMMARY')
        self.assertTrue(self.port.offer(old))
        self.assertTrue(self.port.offer(survey))
        self.assertTrue(self.port.offer(new))
        self.assertIs(self.port.peek('WORLD_CANDIDATE', 120), new)
        self.assertIs(self.port.peek('SURVEY_SUMMARY', 120), survey)
        self.assertFalse(self.port.acknowledge(old))
        self.assertFalse(self.port.acknowledge(replace(new)))
        self.assertIs(self.port.peek('WORLD_CANDIDATE', 179), new)
        self.assertIsNone(self.port.peek('WORLD_CANDIDATE', 180))
        self.assertFalse(self.port.acknowledge(new))
        self.assertIs(self.port.peek('SURVEY_SUMMARY', 180), survey)
        self.assertTrue(self.port.acknowledge(survey))
        self.assertIsNone(self.port.peek('SURVEY_SUMMARY', 180))
        self.assertEqual((old.issued_us, old.expires_us), (100, 200))

    def test_source_identity_replacement_fences_old_report_while_equal_context_is_coalesced(self):
        original = report(self.context)
        self.assertTrue(self.port.offer(original))
        self.context_port.publish(replace(self.context))
        self.assertIs(self.context_port.read(), self.context)
        self.assertIs(self.port.peek('WORLD_CANDIDATE', 120), original)
        binding = json_bytes(self.context.source_binding, 2048)
        binding['source_binding_revision'] = '2'
        newer = replace(self.context, source_binding=canonical(binding))
        self.context_port.publish(newer)
        self.assertIsNone(self.port.peek('WORLD_CANDIDATE', 120))
        self.assertFalse(self.port.offer(original))
        self.assertIsNone(self.port.peek('WORLD_CANDIDATE', 120))
        self.assertTrue(self.port.offer(report(newer)))

    def test_original_bounds_frozen_bytes_and_publication_hash_are_checked_before_offer(self):
        for kind, cap in (('WORLD_CANDIDATE', 32768), ('SURVEY_SUMMARY', 512)):
            bounded = report(self.context, kind=kind, raw=b'x'*cap)
            self.assertIs(bounded.context, self.context)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                report(self.context, kind=kind, raw=b'x'*(cap+1))
            with self.assertRaises(ValueError): report(self.context, kind=kind, raw=bytearray(b'x'))
            with self.assertRaises(FrozenInstanceError): bounded.expires_us = 999
        for change in (dict(issued_us=True), dict(expires_us=100), dict(publication_sha256='b'*64),
                       dict(kind='UNKNOWN'), dict(raw=b''), dict(expires_us=2**63),dict(expires_us=5_000_101)):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                replace(report(self.context), **change)
        with self.assertRaises(ValueError):replace(report(self.context,kind='SURVEY_SUMMARY'),grant_sha256=None)

    def test_swap_and_ack_reuse_only_existing_reentrant_lock_without_hash_or_encode(self):
        original = report(self.context)
        self.assertIs(self.port.adoption_lock, self.context_port.adoption_lock)
        with self.context_port.adoption_lock, patch('xenova_vehicle.runtime.mapper_publication.sha256',
                side_effect=AssertionError('no hash in final adoption')):
            self.assertTrue(self.port.offer(original))
            self.assertIs(self.port.peek('WORLD_CANDIDATE', 150), original)
            self.assertTrue(self.port.acknowledge(original))
        self.assertIsNone(self.port.peek('WORLD_CANDIDATE', 150))

    def test_admission_fence_discards_original_heads_and_rejects_late_compute_offer(self):
        world=report(self.context);survey=report(self.context,kind='SURVEY_SUMMARY')
        self.assertTrue(self.port.offer(world));self.assertTrue(self.port.offer(survey))
        self.assertFalse(self.port.fence_admission(0))
        self.assertTrue(self.port.fence_admission(1))
        for kind in ('WORLD_CANDIDATE','SURVEY_SUMMARY'):self.assertIsNone(self.port.peek(kind,150))
        self.assertFalse(self.port.offer(world));self.assertFalse(self.port.offer(survey,admission_revision=0))
        self.assertEqual((world.issued_us,world.expires_us),(100,200))
        self.assertEqual((survey.issued_us,survey.expires_us),(100,200))
        # 작은 opaque bytes의 port 수명만 검사한다. 실제 관측/World 수락 증거가 아니다.
        current=report(self.context,raw=b'current-original-control',issued=120,expires=190)
        self.assertTrue(self.port.offer(current,admission_revision=1))
        self.assertEqual(self.port.discard_pending('WORLD_CANDIDATE'),(current,))
        self.assertEqual(self.port.discard_pending(),())
        self.assertFalse(self.port.acknowledge(current))
        with self.assertRaises(ValueError):self.port.fence_admission(0)
        with self.assertRaises(ValueError):self.port.offer(world,admission_revision=True)


class MapperPublicationProcessorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = processor_fixture.MapperProcessorMappingTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def enable(self):
        owner = self.fixture
        f = owner.fixture
        port = MapperPublicationPort(owner.port)
        owner.processor = ResidentMapperProcessor(f.calibration_bytes, f.raw, clock=lambda: f.now,
            mapper_profile_bytes=owner.config.canonical_bytes, source_policy=f.source_policy,
            clock_policy=f.fixture.policy, mapper_worker=f.fixture.mapper_worker,
            mapper_instance=f.fixture.mapper_instance, mapping_context_port=owner.port,
            mapping_mailbox=owner.mailbox, publication_port=port)
        return owner.processor, port

    def test_default_off_and_actual_unready_geometry_never_offer_world_or_advance_sequence(self):
        owner = self.fixture
        owner.grant()
        item, summary = owner.geometry()
        self.assertNotIn('world_candidate_ready', summary)
        self.assertIsNone(owner.processor._staged[8])
        owner.processor.accepted(item)
        self.assertEqual(owner.processor._world_sequence, 0)
        processor, port = self.enable()
        owner.grant()
        item, summary = owner.geometry()
        self.assertFalse(summary['world_candidate_ready'])
        self.assertIn(summary['world_candidate_reason'], ('WORLD_NOT_READY', 'MAPPING_NOT_READY'))
        self.assertIsNone(processor._staged[8])
        processor.accepted(item)
        self.assertEqual(processor._world_sequence, 0)
        self.assertIsNone(port.peek('WORLD_CANDIDATE', owner.fixture.now))

    def test_epoch_cancel_discards_staged_report_without_offering_or_spending_sequence(self):
        owner = self.fixture
        processor, port = self.enable()
        owner.grant()
        # 원 후보 보관/취소만 검산하는 불투명 제어값이다. World 기하 양성이 아니다.
        candidate = report(owner.context, issued=owner.fixture.now, expires=owner.fixture.now+100)
        with patch.object(processor, '_world_report', return_value=(candidate, 'READY')):
            item, summary = owner.geometry()
        self.assertTrue(summary['world_candidate_ready'])
        self.assertIs(processor._staged[8], candidate)
        binding = json_bytes(owner.context.source_binding, 2048)
        binding['source_binding_revision'] = '2'
        owner.port.publish(replace(owner.context, source_binding=canonical(binding)))
        with owner.port.adoption_lock, self.assertRaisesRegex(MapperCancelled, 'PUBLICATION_CHANGED'):
            processor.accepted(item)
        processor.cancelled(item, 'PUBLICATION_CHANGED')
        self.assertIsNone(processor._staged)
        self.assertEqual(processor._world_sequence, 0)
        self.assertIsNone(port.peek('WORLD_CANDIDATE', owner.fixture.now))

    def test_exception_after_candidate_preparation_does_not_publish_unaccepted_report(self):
        owner = self.fixture
        processor, port = self.enable()
        owner.grant()
        candidate = report(owner.context, issued=owner.fixture.now, expires=owner.fixture.now+100)
        prepared = []
        def prepare(_sensors,_frame_state=None):
            prepared.append(candidate)
            return candidate, 'READY'
        def checkpoint(*_):
            if prepared: raise RuntimeError('EXPLICIT_PUBLICATION_FAILURE')
        with patch.object(processor, '_world_report', side_effect=prepare):
            with self.assertRaisesRegex(RuntimeError, 'EXPLICIT_PUBLICATION_FAILURE'):
                processor.geometry(owner.fixture.item(), None, checkpoint)
        self.assertEqual(prepared, [candidate])
        self.assertIsNone(processor._staged)
        self.assertIsNone(processor._mapping_draft)
        self.assertEqual(processor._world_sequence, 0)
        self.assertIsNone(port.peek('WORLD_CANDIDATE', owner.fixture.now))


if __name__ == '__main__':
    unittest.main()
