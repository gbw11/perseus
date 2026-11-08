"""승인 ToF 자산과 현재 표본을 분리하는 COMPLETE 복원 회귀.

기존 software 보정/센서 fixture와 실제 임시 SQLite 128 tile/4 LOAD를 재사용한다.
원 자료 완성·의미 검사를 다루며 물리 센서 수락, 새 측정, Scene 사용권을 발급하지 않는다.
"""

from dataclasses import FrozenInstanceError, replace
import sys
import unittest

import test_complete_catalog_restore as restore_fixture
import test_mapper_mapping as mapping_fixture
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.runtime.parking_bridge import ParkingBridge
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.scene import SceneError, SceneEvidenceScope
from xenova_vehicle.storage.contracts import canonical, json_bytes


@unittest.skipUnless(sys.platform == 'linux', '기존 실제 SQLite/4 LOAD fixture는 대상 Linux에서 검증한다')
class SceneTofInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = restore_fixture.CompleteCatalogRestoreTests
        cls.addClassCleanup(cls.original.doClassCleanups)
        cls.original.setUpClass()

    @property
    def assets(self):
        return (('front-test-leaf', self.original.fixture.sensor.leaf),)

    def scope(self, **kwargs):
        f = self.original
        return SceneEvidenceScope(f.current_context, f.current_raw, f.calibration, 'e' * 64, **kwargs)

    def test_actual_complete_restores_approved_old_tof_without_any_fresh_incarnation(self):
        f = self.original
        original_payload = f.tof_proof.payload
        original_contexts = tuple(c.payload for c in f.complete.evidence.contexts)
        current = self.scope(approved_tof_assets=self.assets)
        restored = current.with_complete_history(f.complete, now_us=f.now)
        self.assertEqual(len(f.complete.tiles), 128)
        self.assertEqual(len(f.complete.page_receipts), 4)
        self.assertEqual(restored._tof, ())
        self.assertEqual(restored.approved_tof_assets, self.assets)
        with self.assertRaises(SceneError): restored.context_record('TOF', sensor_id=0)
        contexts = {c.sha256: c for c in f.complete.evidence.contexts}
        self.assertEqual(restored.record_sources(f.tof_proof, contexts[f.tof_proof.context_sha256]), (8, 4))
        self.assertEqual(restored.record_sources(f.rgb_proof, contexts[f.rgb_proof.context_sha256]), (1, 2))
        old = next(scope for scope in restored._historical if scope._tof)
        self.assertEqual(old._tof[0][2:], (f.old_tof_instance, 1))
        self.assertEqual(f.tof_proof.pi_lower, 1_100_050)
        self.assertIs(f.tof_proof.payload, original_payload)
        self.assertEqual(tuple(c.payload for c in f.complete.evidence.contexts), original_contexts)
        request, head = restored.complete_restore_head(f.complete, now_us=f.now)
        self.assertIs(request, f.loaded_pages[-1].request)
        self.assertIs(head, f.loaded_pages[-1].heads)
        # 실제 method 등록의 얕은 불변 복제도 원 자산 목록을 유지한다.
        bridge = ParkingBridge(f.current_context, clock_id=f.current_raw.fields['clock_epoch'],
            map_frame_id='odom:' + f.current_context.odom_epoch, body=f.calibration.body,
            method_manifest_sha256='e' * 64, input_kind='LIVE', calibration_profile=f.calibration)
        approved = restored.with_parking_method(bridge.method)
        self.assertIs(approved.approved_tof_assets, restored.approved_tof_assets)
        self.assertEqual(approved._tof, ())

    def test_default_none_derives_only_validated_fresh_inventory_for_existing_callers(self):
        f = self.original
        current = f.scope(f.current_context)
        self.assertEqual(current.approved_tof_assets, self.assets)
        restored = current.with_complete_history(f.complete, now_us=f.now)
        self.assertEqual(restored._tof, current._tof)
        body = json_bytes(restored.context_record('TOF', sensor_id=0).payload, 4096)['body']['tof']
        self.assertEqual((body['sensor_instance'], body['sensor_generation']), current._tof[0][2:])
        self.assertNotEqual(body['sensor_instance'], f.old_tof_instance)
        # calibration에 leaf가 있더라도 fresh source도 명시 inventory도 없으면 도출하지 않는다.
        empty = self.scope()
        self.assertEqual(empty.approved_tof_assets, ())
        with self.assertRaises(SceneError): empty.with_complete_history(f.complete, now_us=f.now)

    def test_explicit_zero_is_not_a_history_wildcard_even_when_current_tof_exists(self):
        f = self.original
        current = self.scope(tof_sources=f.current_scope._tof, approved_tof_assets=())
        self.assertEqual(current.approved_tof_assets, ())
        with self.assertRaisesRegex(SceneError, 'approved asset inventory'):
            current.with_complete_history(f.complete, now_us=f.now)
        with self.assertRaises(SceneError):
            self.scope(approved_tof_assets=(), historical_scopes=(f.old_scope,))

    def test_changed_configured_leaf_or_method_cannot_reuse_original_complete(self):
        leaf = self.assets[0][1]
        different_driver = ('0' if leaf.driver_profile_sha256[0] != '0' else '1') + leaf.driver_profile_sha256[1:]
        for changed in (replace(leaf, sensor_config_revision=leaf.sensor_config_revision + 1),
                        replace(leaf, driver_profile_sha256=different_driver)):
            with self.subTest(leaf=changed.sha256), self.assertRaises(SceneError):
                self.scope(approved_tof_assets=(('front-test-leaf', changed),))
        f = self.original
        changed = SceneEvidenceScope(f.current_context, f.current_raw, f.calibration, 'd' * 64,
                                    approved_tof_assets=self.assets)
        with self.assertRaises(SceneError): changed.with_complete_history(f.complete, now_us=f.now)

    def test_inventory_is_bounded_exact_immutable_and_does_not_approve_unknown_names(self):
        entry = self.assets[0]
        for invalid in (False, list(self.assets), (entry, entry), (entry,) * 3,
                        ((entry[0],),), (('unapproved-leaf-name', entry[1]),)):
            with self.subTest(inventory=invalid), self.assertRaises(SceneError):
                self.scope(approved_tof_assets=invalid)
        current = self.scope(approved_tof_assets=self.assets)
        with self.assertRaises(FrozenInstanceError): current.approved_tof_assets = ()

    def test_configured_unqualified_optional_leaf_does_not_block_rgb_only_scope(self):
        f = self.original
        leaf = replace(self.assets[0][1], first_surface_qualified=False)
        fields = f.calibration.to_dict()
        fields['external_leaves'] = {'front-test-leaf': leaf.sha256}
        calibration = CalibrationProfile.from_dict(fields)
        fields = f.current_raw.fields; fields['calibration_bundle'] = calibration.sha256
        raw = RawBinding(fields)
        publication = json_bytes(f.current_context.source_binding, 2048)
        publication['raw_binding_sha256'] = raw.sha256
        context = replace(f.current_context, source_binding=canonical(publication))
        assets = (('front-test-leaf', leaf),)
        current = SceneEvidenceScope(context, raw, calibration, 'e' * 64, approved_tof_assets=assets)
        self.assertIs(current.approved_tof_assets, assets)
        self.assertFalse(current.approved_tof_assets[0][1].first_surface_qualified)
        self.assertEqual(current._tof, ())
        self.assertEqual(json_bytes(current.context_record('RAW').payload, 4096)['kind'], 'RAW')
        with self.assertRaises(SceneError): current.context_record('TOF', sensor_id=0)
        with self.assertRaises(SceneError):
            SceneEvidenceScope(context, raw, calibration, 'e' * 64, approved_tof_assets=assets,
                tof_sources=(('front-test-leaf', leaf, f.old_tof_instance, 1),))

    def test_actual_mapping_scope_receives_profile_inventory_before_fresh_inputs(self):
        f = mapping_fixture.MapperMappingTests(methodName='runTest')
        self.addCleanup(f.doCleanups); f.setUp()
        scope = f.mapping._make_scope(f.context, now_us=f.now)
        self.assertTrue(f.profile.sensors.tof_sources)
        self.assertEqual(scope.approved_tof_assets, f.profile.sensors.tof_sources)
        self.assertEqual(scope._tof, ())
        with self.assertRaises(SceneError): scope.context_record('TOF', sensor_id=0)
