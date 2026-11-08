"""원 negative grant의 lease 독립 fence와 새로운 restore 보존 회귀.

JIO/clock은 기존 software 왕복, Scene은 실제 임시 SQLite commit fixture를 쓴다.
실제 MQTT/실차 승인은 없으며 original key/bytes/만료와 현재 actor 연결만 검사한다.
"""

from dataclasses import replace
import sys
import unittest
from unittest.mock import patch

import test_jetson_jio as jio_fixture
import test_scene_source_ports as source_port_fixtures
import test_scene as scene_fixture
import test_mapper_resident_scene as processor_fixture
from xenova_drive_world import decode, encode, schema
from xenova_vehicle.runtime.scene import SceneError
from xenova_vehicle.runtime.source_identity import known_scene_negative
from xenova_vehicle.transport.jetson_jio import PREFIX


def negative(grant, state=3):
    value = schema.SceneGrant(); value.CopyFrom(grant)
    value.lifecycle_state = state; value.lifecycle_revision += 1
    return value


def restore(grant):
    value = schema.SceneGrant(); value.CopyFrom(grant)
    value.lifecycle_state = 1; value.lifecycle_revision += 2
    value.request_nonce = bytes([81] * 16); value.purpose = 1
    value.restore_checkpoint_sha256 = bytes([82] * 32)
    return value


class SceneNegativeJioTests(unittest.TestCase):
    def owner(self):
        return source_port_fixtures.JioSurveyPortTests(methodName='runTest').owner()

    def match(self, raw, known):
        return known_scene_negative(raw, known, source=jio_fixture.source(),
            pi_host_boot=jio_fixture.uid(1), gateway_instance=jio_fixture.uid(4))

    def test_only_lifecycle_fields_can_change_and_same_revision_requires_original_bytes(self):
        _, _, original = self.owner(); known = encode(original)
        event = negative(original); raw = encode(event)
        self.assertTrue(self.match(raw, known))
        self.assertTrue(self.match(encode(negative(original, 4)), known))
        self.assertTrue(self.match(raw, raw))
        self.assertFalse(self.match(known, known))
        conflict = schema.SceneGrant(); conflict.CopyFrom(event); conflict.lifecycle_state = 4
        self.assertFalse(self.match(encode(conflict), raw))
        self.assertFalse(self.match(raw, None))
        for field in ('nonce', 'scene', 'admission', 'issued', 'expiry', 'profile', 'origin', 'purpose'):
            with self.subTest(field=field):
                changed = schema.SceneGrant(); changed.CopyFrom(event)
                if field == 'nonce': changed.request_nonce = bytes([90] * 16)
                elif field == 'scene': changed.scene.admission_revision += 1
                elif field == 'admission': changed.source_admission_revision += 1
                elif field == 'issued': changed.issued_pi_us += 1
                elif field == 'expiry': changed.valid_until_pi_us += 1
                elif field == 'profile': changed.scene.coverage_profile_sha256 = bytes([91] * 32)
                elif field == 'origin': changed.pi_host_boot = jio_fixture.uid(92)
                else:
                    changed.purpose = 1; changed.restore_checkpoint_sha256 = bytes([93] * 32)
                self.assertFalse(self.match(encode(changed), known))
        older = schema.SceneGrant(); older.CopyFrom(event); older.lifecycle_revision -= 1
        self.assertFalse(self.match(encode(older), raw))

    def test_actual_jio_forwards_known_original_negative_after_new_revision_without_clock(self):
        clock, owner, original = self.owner(); token = owner._token
        previous = owner.current_scene
        probe = decode(jio_fixture.returned_probe(), schema.MapperClockProbe)
        probe.probe_seq += 1; probe.admission_revision += 1; probe.nonce = bytes([80] * 16)
        probe.ClearField('mapper_trust')
        jio_fixture.deliver(owner, f'{PREFIX}/mapper/oak-1/clock/request.pb', probe)
        owner.take_inputs()
        self.assertIsNone(owner.current_lease)
        self.assertIs(owner.current_scene, previous)
        self.assertGreater(owner._pi_revision, original.source_admission_revision)
        clock.now += 5_000_000
        event = negative(original); raw = encode(event)
        topic = f'{PREFIX}/mapper/oak-1/scene.pb'
        jio_fixture.deliver(owner, topic, raw, qos=1)
        rows = owner.take_inputs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].raw, raw)
        self.assertIsNone(rows[0].lease)
        self.assertEqual(rows[0].received_j_us, clock.now)
        self.assertIs(owner._token, token)
        current = owner.current_scene
        jio_fixture.deliver(owner, topic, raw, qos=1)
        self.assertEqual(owner.take_inputs(), ())
        self.assertIs(owner.current_scene, current)
        # 원 positive는 새 승인/clock 없이 다시 살리지 않는다.
        jio_fixture.deliver(owner, topic, original, qos=1)
        self.assertEqual(owner.take_inputs(), ())
        self.assertIs(owner.current_scene, current)

    def test_final_lock_rechecks_actual_head_when_new_restore_arrives_during_validation(self):
        _, owner, original = self.owner(); token = owner._token
        newer = restore(original); event = negative(original); topic = f'{PREFIX}/mapper/oak-1/scene.pb'
        actual_helper = known_scene_negative
        def interleave(raw, known, **kwargs):
            matched = actual_helper(raw, known, **kwargs)
            jio_fixture.deliver(owner, topic, newer, qos=1)
            return matched
        with patch('xenova_vehicle.runtime.source_identity.known_scene_negative', side_effect=interleave):
            jio_fixture.deliver(owner, topic, event, qos=1)
        self.assertEqual(owner.current_scene.raw, encode(newer))
        self.assertEqual(tuple(row.raw for row in owner.take_inputs()), (encode(newer),))
        self.assertIs(owner._token, token)


@unittest.skipUnless(sys.platform == 'linux', '원 Scene SQLite commit은 대상 Linux에서 검사한다')
class SceneNegativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = scene_fixture.MapperSceneTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        value, _, _ = self.f.committed()
        summary = decode(self.f.mapper._frozen.summary, schema.SurveySummary)
        value.lifecycle_state = 2; value.lifecycle_revision += 1
        value.frozen_evidence_sha256 = summary.evidence_sha256
        value.frozen_survey_revision = summary.survey_revision; value.frozen_hazard_revision = summary.hazard_revision
        self.assertTrue(self.f.mapper.receive_grant(encode(value), self.f.clock, now_j_us=self.f.now + 20))
        self.assertTrue(self.f.mapper.eligible)
        self.original = value

    def test_known_negative_without_lease_revokes_once_and_keeps_original_event_bytes(self):
        f = self.f; old_lease = f.mapper._lease; event = encode(negative(self.original))
        self.assertTrue(f.mapper.receive_grant(event, None, now_j_us=f.now + 30))
        self.assertFalse(f.mapper.eligible)
        self.assertEqual(f.mapper.blocked_reason, 'REVOKED')
        self.assertEqual(f.mapper.grant_bytes, event)
        self.assertIs(f.mapper._lease, old_lease)
        summary = f.mapper._summary
        self.assertTrue(f.mapper.receive_grant(event, None, now_j_us=f.now + 40))
        self.assertEqual(f.mapper._summary, summary)

    def test_old_nonce_negative_cannot_cancel_newer_actual_restore_with_no_supplied_lease(self):
        f = self.f; newer = restore(self.original)
        self.assertTrue(f.mapper.receive_grant(encode(newer), f.clock, now_j_us=f.now + 30))
        original_start = f.mapper._validation_start_j
        old = negative(self.original); old.lifecycle_revision = newer.lifecycle_revision + 1
        with patch.object(f.mapper, 'revoke', wraps=f.mapper.revoke) as revoke:
            self.assertFalse(f.mapper.receive_grant(encode(old), None, now_j_us=f.now + 40))
            revoke.assert_not_called()
        self.assertEqual(f.mapper.grant_bytes, encode(newer))
        self.assertEqual(f.mapper._validation_start_j, original_start)
        self.assertEqual(f.mapper.blocked_reason, 'FREEZE_PENDING')

    def test_renewed_semantic_source_revision_invalidates_old_positive_in_now(self):
        f = self.f
        probe = decode(f.clock.raw_probe_bytes, schema.MapperClockProbe)
        probe.admission_revision += 1
        lease = replace(f.clock, source_revision=f.clock.source_revision + 1, raw_probe_bytes=encode(probe))
        f.mapper.renew_clock(lease, now_j_us=f.now + 30)
        with self.assertRaisesRegex(SceneError, 'source admission revision'):
            f.mapper._now(f.now + 40)
        self.assertFalse(f.mapper.eligible)
        self.assertEqual(f.mapper.blocked_reason, 'SOURCE')


@unittest.skipUnless(sys.platform == 'linux', '실제 Processor/Mapping SQLite 경계는 대상 Linux에서 검사한다')
class SceneNegativeProcessorTests(unittest.TestCase):
    def setUp(self):
        self.f = processor_fixture.MapperResidentSceneTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.original = self.f.establish_original_grant()

    def test_actual_scene_port_negative_runs_before_missing_latest_clock_without_resetting_map(self):
        f = self.f; processor = f.processor
        collector = processor._mapping.collector; pipeline = processor._accepted.pipeline
        event = negative(self.original)
        f.publish_grant(event)
        processor._latest_source_inputs = None
        processor._maintain_scene()
        self.assertEqual(processor._scene.scene.grant_bytes, encode(event))
        self.assertEqual(processor._scene.scene.blocked_reason, 'REVOKED')
        self.assertFalse(processor._scene.scene.eligible)
        self.assertIs(processor._mapping.collector, collector)
        self.assertIs(processor._accepted.pipeline, pipeline)

    def test_stale_negative_preserves_new_restore_but_current_original_lease_expiry_still_fences(self):
        f = self.f; processor = f.processor
        newer = restore(self.original)
        f.publish_grant(newer); processor._maintain_scene()
        self.assertEqual(processor._scene.scene.grant_bytes, encode(newer))
        original_start = processor._scene.scene._validation_start_j
        old = negative(self.original); old.lifecycle_revision = newer.lifecycle_revision + 1
        f.publish_grant(old)
        processor._latest_source_inputs = None
        processor._maintain_scene()
        self.assertEqual(processor._scene_status, 'STALE_SCENE_NEGATIVE')
        self.assertEqual(processor._scene.scene.grant_bytes, encode(newer))
        self.assertEqual(processor._scene.scene._validation_start_j, original_start)
        lease = processor._scene.scene._lease
        trust = decode(lease.trust_bytes, schema.WorldTrust)
        f.runtime.now = min(newer.valid_until_pi_us, lease.expires_pi_us) - trust.upper_offset_us
        processor._maintain_scene()
        self.assertEqual(processor._scene_status, 'WAIT_SCENE_CLOCK')
        self.assertFalse(processor._scene.scene.eligible)
        self.assertEqual(processor._scene.scene.blocked_reason, 'CLOCK')
