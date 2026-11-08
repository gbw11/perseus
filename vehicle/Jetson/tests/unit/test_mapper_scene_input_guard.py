"""새 원 Scene delivery와 최종 compute 채택 사이의 참조 경주를 검사한다.

첫 사례는 기존 software sensor/profile의 실제 Processor·core·coordinator를 쓴다.
둘째의 opaque bytes는 포트 참조 검산 전용이며 Scene/World 승인이나 실물 증거가 아니다.
"""

from copy import deepcopy
from hashlib import sha256
import json
import unittest

import test_mapper_resident_scene as scene_fixture
from test_parking_catalog import publication_context
from xenova_drive_world import schema
from xenova_vehicle.runtime.mapper_compute import MapperCancelled
from xenova_vehicle.runtime.mapper_mapping import MappingContextPort
from xenova_vehicle.runtime.mapper_publication import MapperPublicationPort, PublicationReport


def checkpoint(*_):
    return None


class MapperSceneInputGuardTests(unittest.TestCase):
    def test_actual_negative_row_between_scene_prepare_and_adoption_preserves_accepted_map(self):
        f = scene_fixture.MapperResidentSceneTests(methodName='runTest')
        self.addCleanup(f.doCleanups)
        f.setUp()
        original_grant = f.establish_original_grant()
        processor = f.processor
        core, before = f.map_snapshot()
        original_context = f.base.port.read()
        original_row = f.base.port.read_scene()
        original_coordinator_grant = processor._scene.scene.grant_bytes
        source = f.runtime.fixture.snapshot()
        source['scene'] = deepcopy(f.scene_row)
        item = f.runtime.item(2, snapshot=source)
        summary = json.loads(processor.geometry(item, None, checkpoint))['latest']
        self.assertTrue(summary['mapping_ready'], summary.get('mapping_reason'))
        state = processor._staged[9]
        self.assertIsNotNone(state.scene)
        self.assertIs(state.scene, processor._scene._draft)
        self.assertIs(state.scene_row, original_row)
        negative = schema.SceneGrant()
        negative.CopyFrom(original_grant)
        negative.lifecycle_state = 3
        negative.lifecycle_revision += 1
        f.publish_grant(negative)
        self.assertIs(f.base.port.read(), original_context)
        self.assertIsNot(f.base.port.read_scene(), original_row)
        # main은 원 row만 게시한다. coordinator의 상태는 compute가 소비하기 전까지 그대로다.
        self.assertIs(processor._scene.scene.grant_bytes, original_coordinator_grant)
        with f.base.port.adoption_lock, self.assertRaisesRegex(MapperCancelled, 'SCENE_INPUT_CHANGED'):
            processor.accepted_at(item, f.runtime.now)
        f.assert_preserved_map(core, before)
        processor.cancelled(item, 'SCENE_INPUT_CHANGED')
        self.assertIsNone(processor._staged)
        self.assertIsNone(processor._scene._draft)
        processor.maintenance()
        self.assertFalse(processor._scene.scene.eligible)
        f.assert_preserved_map(core, before)

    def test_port_rejects_old_or_equal_foreign_expected_row_and_accepts_current_reference(self):
        context = publication_context()
        inputs = MappingContextPort()
        inputs.publish(context)
        port = MapperPublicationPort(inputs)
        old_row = b'original-opaque-scene-delivery-1'
        current_row = b'original-opaque-scene-delivery-2'
        inputs.publish_scene(old_row)
        report = PublicationReport(context, 'SURVEY_SUMMARY', b'opaque-reference-only', 100, 200,
            sha256(context.source_binding).hexdigest(), 'a'*64)
        inputs.publish_scene(current_row)
        self.assertFalse(port.offer(report, scene_row=old_row))
        foreign_equal = bytes(bytearray(current_row))
        self.assertEqual(foreign_equal, current_row)
        self.assertIsNot(foreign_equal, current_row)
        self.assertFalse(port.offer(report, scene_row=foreign_equal))
        self.assertIsNone(port.peek('SURVEY_SUMMARY', 150))
        self.assertTrue(port.offer(report, scene_row=current_row))
        self.assertIs(port.peek('SURVEY_SUMMARY', 150), report)
        self.assertEqual((report.issued_us, report.expires_us), (100, 200))


if __name__ == '__main__':
    unittest.main()
