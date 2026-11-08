"""동일 RGB의 slot 추출 생략과 현재 map occupancy 갱신을 함께 검사한다.

기존 합성 tape/map과 실제 임시 D SQLite 예약 fixture를 재사용한다. NN sentinel은
해석 완료 객체를 소비하지 않는 이 경계만 표시하며 실제 추론·센서·처리율 증거가 아니다.
"""

from dataclasses import replace
import sys
import unittest
from unittest.mock import patch, sentinel
from uuid import uuid4

import test_parking as parking_fixture
import test_parking_bridge as bridge_fixture
from xenova_vehicle.mapping.registration import PoseBox
from xenova_vehicle.perception.parking import OCCUPIED, UNKNOWN
from xenova_vehicle.runtime import parking_bridge as product


@unittest.skipUnless(sys.platform == 'linux', '기존 실제 D SQLite 예약 fixture는 대상 Linux에서 검증한다')
class ParkingGeometryCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bridge_fixture.ParkingBridgeTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def apply(self, result=None, *, metric=True, **changes):
        f = self.fixture
        args = dict(stamp=parking_fixture.source().stamp, capture_prior=f.prior, aisles=f.aisles,
            registration_revision=1, prior_qualified=True, now_us=f.clock(),
            metric_evidence=f.metric() if metric else None)
        args.update(changes)
        return f.bridge.process(f.result() if result is None else result, **args)

    def no_extraction(self):
        return patch.object(product, 'extract_slots', side_effect=AssertionError('duplicate RGB extracted again'))

    def test_same_rgb_late_nn_preserves_geometry_source_ids_proof_and_work_counts(self):
        f = self.fixture; f.allocate()
        original = f.bridge._applied_geometry
        tracks, records, links = f.bridge.catalog.tracks, dict(f.bridge._records), dict(f.bridge._links)
        cursor = f.bridge._next_provenance
        result = replace(f.result(), neural=sentinel.late_nn)
        with self.no_extraction() as extract, \
                patch.object(f.bridge, 'refresh_occupancy', wraps=f.bridge.refresh_occupancy) as refresh:
            update = self.apply(result)
        extract.assert_not_called(); refresh.assert_called_once()
        self.assertIs(update.geometry, original)
        self.assertEqual((update.geometry.hypotheses_checked, update.geometry.pair_count),
                         (original.hypotheses_checked, original.pair_count))
        self.assertIs(update.geometry.source, original.source)
        self.assertEqual(update.geometry.source.stamp, parking_fixture.source().stamp)
        self.assertFalse(update.geometry_updated or update.occupancy_updated)
        self.assertEqual(f.bridge.catalog.tracks, tracks)
        self.assertEqual(f.bridge._records, records); self.assertEqual(f.bridge._links, links)
        self.assertEqual(f.bridge._next_provenance, cursor)

    def test_same_rgb_new_map_hit_still_blocks_clearance_without_new_proof(self):
        f = self.fixture; f.allocate()
        original = f.bridge._applied_geometry; previous = f.bridge.catalog.tracks[0]
        records, cursor = dict(f.bridge._records), f.bridge._next_provenance
        f.grid.add_hits(((.1, .1, .2, .2),), parking_fixture.source().stamp,
            view_id='explicit-synthetic-new-map-hit', origin_xy=(0., -.1))
        f.clock.now = 1_010_000
        result = f.result()
        with self.no_extraction(), patch.object(f.bridge, 'refresh_occupancy',
                wraps=f.bridge.refresh_occupancy) as refresh:
            update = self.apply(result, metric=False)
        refresh.assert_called_once()
        self.assertIs(refresh.call_args.args[0], result.map_snapshot)
        self.assertIs(update.geometry, original)
        self.assertFalse(update.geometry_updated)
        self.assertTrue(update.provenance_wait)
        self.assertEqual(f.bridge._volatile[previous.slot_id].state, OCCUPIED)
        self.assertEqual(f.bridge.catalog.tracks[0].provenance_id, previous.provenance_id)
        self.assertEqual(f.bridge._records, records); self.assertEqual(f.bridge._next_provenance, cursor)

    def test_same_rgb_later_clock_does_not_refresh_original_free_or_geometry_age(self):
        f = self.fixture; f.allocate()
        original = f.bridge._applied_geometry; previous = f.bridge.catalog.tracks[0]
        f.clock.now = 1_600_000
        with self.no_extraction(), patch.object(f.bridge, 'refresh_occupancy',
                wraps=f.bridge.refresh_occupancy) as refresh:
            update = self.apply(metric=False)
        refresh.assert_called_once()
        self.assertIs(update.geometry, original)
        self.assertEqual(update.geometry.source.stamp.captured_us, (900_000, 900_010))
        self.assertEqual(f.bridge._volatile[previous.slot_id].state, UNKNOWN)
        self.assertIs(f.bridge.catalog.tracks[0].occupancy, previous.occupancy)
        self.assertFalse(update.geometry_updated)

    def test_cached_rgb_prior_aisle_and_source_guards_run_before_any_extraction(self):
        f = self.fixture; f.allocate()
        original = f.bridge._applied_geometry
        cases = (
            dict(capture_prior=PoseBox((.01, .01), (0., 0.), (0., 0.))),
            dict(aisles=(replace(f.aisles[0], approach_lane_id=2),)),
            dict(stamp=replace(parking_fixture.source().stamp, clock_id=str(uuid4()))),
        )
        with self.no_extraction(), patch.object(f.bridge, 'refresh_occupancy',
                side_effect=AssertionError('rejected interpretation refreshed occupancy')):
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(ValueError): self.apply(**changes)
            result = f.result()
            for summary, snapshot in (
                    (dict(result.summary, rgb_source_key=('foreign',)), result.map_snapshot),
                    (dict(result.summary, calibration_id='d' * 64), result.map_snapshot),
                    (result.summary, dict(result.map_snapshot, frame_id='foreign-map-frame'))):
                with self.subTest(summary=summary, snapshot_frame=snapshot['frame_id']), self.assertRaises(ValueError):
                    self.apply(replace(result, summary=summary, map_snapshot=snapshot))
            missing = self.apply(replace(result, tape=None))
            self.assertEqual(missing.reasons, ('TAPE_SOURCE_UNAVAILABLE',))
        self.assertIs(f.bridge._applied_geometry, original)

    def test_scene_only_context_keeps_cache_but_geometry_context_change_is_rejected(self):
        f = self.fixture
        f.context = bridge_fixture.publication_context(f.context); f.bridge = f.new_bridge(); f.allocate()
        original = f.bridge._applied_geometry; tracks = f.bridge.catalog.tracks
        newer = bridge_fixture.publication_context(f.context, revision=2,
            scene=dict(admission_id=str(uuid4()), revision='1'))
        self.assertTrue(f.bridge.replace_publication_context(newer, now_us=f.clock()))
        f.context = newer
        with self.no_extraction(): update = self.apply()
        self.assertIs(update.geometry, original)
        self.assertEqual(f.bridge.catalog.tracks, tracks)
        self.assertEqual(original.source.stamp, parking_fixture.source().stamp)
        with self.assertRaises(ValueError):
            f.bridge.replace_publication_context(replace(newer, map_epoch=str(uuid4())), now_us=f.clock())

    def test_new_rgb_source_still_executes_original_extraction_without_relabelling_old_geometry(self):
        f = self.fixture; f.allocate()
        original = f.bridge._applied_geometry
        f.clock.now = 1_010_100
        source = parking_fixture.source(sequence=2, captured=(1_010_000, 1_010_010), available=f.clock())
        with patch.object(product, 'extract_slots', wraps=product.extract_slots) as extract:
            update = self.apply(f.result(source), stamp=source.stamp, metric=False)
        self.assertEqual(extract.call_count, len(f.aisles))
        self.assertEqual(update.geometry.source.stamp, source.stamp)
        self.assertIsNot(update.geometry, original)
        self.assertIs(f.bridge._applied_geometry, original)
        self.assertFalse(update.geometry_updated)
