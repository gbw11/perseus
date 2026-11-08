"""Pi Scene 사용권의 VM 전달 형식과 legacy 우회 거절 계약.

codec의 형식 검사는 사용권 승인이 아니다. 현재 권위·source·기한과 실제 경로의
결박은 C/Gateway 통합 시험에서 검사한다. 이 파일은 장치나 broker를 시작하지 않는다.
"""

from pathlib import Path
import struct
import sys
import unittest
from uuid import UUID


HOME=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOME/'world/python'))
sys.path.insert(0,str(HOME/'examples'))
from xenova_drive_world import schema,encode,decode
from test_world_maximum import maximum_world,BASE,BOOT,INSTANCE,REQUEST
from world_fixture import world as legacy_world


def scene_grant():
    """원 full SceneRef와 고정 원 기한을 갖는 명시 합성 ADMITTED 입력."""
    world=maximum_world()
    grant=schema.SceneGrant(major=1,vehicle=world.vehicle,pi_host_boot=BOOT,
        gateway_instance=INSTANCE,source_admission_revision=4,mapper_device='oak-1',
        map_epoch=world.map_epoch,frame_epoch=world.frame_epoch,odom_epoch=world.odom_epoch,
        configuration=world.configuration,calibration=world.calibration,primitive=world.primitive,
        issued_pi_us=BASE,valid_until_pi_us=BASE+300000,lifecycle_state=2,
        request_nonce=UUID(REQUEST).bytes,survey_deadline_pi_us=BASE+200000,
        max_observation_stations=24,purpose=0,frozen_evidence_sha256=b'f'*32,
        frozen_survey_revision=world.scene.survey_revision,frozen_hazard_revision=7,lifecycle_revision=3)
    grant.scene.CopyFrom(world.scene)
    return grant


class SceneLinkContractTests(unittest.TestCase):
    def test_tag10_preserves_existing_link_tags_and_original_grant(self):
        fields=schema.DriveLink.DESCRIPTOR.fields_by_name
        self.assertEqual({name:fields[name].number for name in ('probe','clock','snapshot','accepted',
            'applied','mission_grant','budget_request','budget_reply','scene_grant')},
            dict(probe=2,clock=3,snapshot=4,accepted=5,applied=6,mission_grant=7,
                 budget_request=8,budget_reply=9,scene_grant=10))
        original=encode(scene_grant())
        link=schema.DriveLink(major=2)
        link.scene_grant.CopyFrom(decode(original,schema.SceneGrant))
        payload=encode(link)
        self.assertLessEqual(len(original),1024)
        self.assertLessEqual(len(payload),32768)
        returned=decode(payload,schema.DriveLink)
        self.assertEqual(returned.WhichOneof('payload'),'scene_grant')
        self.assertEqual(encode(returned.scene_grant),original)

    def test_relay_does_not_issue_a_new_revision_or_deadline(self):
        grant=scene_grant(); original=encode(grant)
        for lifecycle in (2,3):
            grant.lifecycle_state=lifecycle
            link=schema.DriveLink(major=2);link.scene_grant.CopyFrom(grant)
            restored=decode(encode(link),schema.DriveLink).scene_grant
            self.assertEqual((restored.issued_pi_us,restored.valid_until_pi_us,
                restored.source_admission_revision,restored.lifecycle_revision),
                (BASE,BASE+300000,4,3))
        self.assertEqual(encode(scene_grant()),original)

    def test_inner_scene_cap_applies_inside_the_link(self):
        link=schema.DriveLink(major=2);link.scene_grant.CopyFrom(scene_grant())
        # Unknown length-delimited field remains part of the bounded inner body.
        link.scene_grant.MergeFromString(b'\xa2\x06\x80\x08'+b'x'*1024)
        with self.assertRaisesRegex(ValueError,'message size cap SceneGrant'):
            encode(link)

    def test_legacy_live_only_roundtrip(self):
        for features in (0,1):
            value=legacy_world();value.required_features=features
            value.cell_flags=bytes([1|8*features])*len(value.occupancy)
            value.age_le32=struct.pack('<I',0)*len(value.occupancy)
            self.assertEqual(decode(encode(value),schema.WorldSnapshot),value)

    def test_legacy_retained_is_rejected_even_with_a_fresh_looking_age(self):
        for features in (0,1):
            for age in (0,3600000):
                with self.subTest(features=features,age=age):
                    value=legacy_world();value.required_features=features
                    value.cell_flags=bytes([3|8*features])*len(value.occupancy)
                    value.age_le32=struct.pack('<I',age)*len(value.occupancy)
                    with self.assertRaisesRegex(ValueError,'legacy world supports live-only'):
                        encode(value)
