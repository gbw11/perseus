"""불변 LIVE64 사본과 실제 grid의 주소·원 값·후보 격리를 검산한다.

직접 채운 RAW 배열은 codec 경계의 software 값이다. hit/FREE 시험의 certificate
인자도 제어값이며 실제 ray coverage·보정·주행 허가를 증명하지 않는다.
"""

from copy import deepcopy
from hashlib import sha256
import math
import struct
import unittest

from xenova_vehicle.mapping.local_map import EvidenceGrid, GridSpec, GridMutation, OCCUPIED, FREE
from xenova_vehicle.mapping.map_lattice import MapLattice, GridWindow, tile_address
from xenova_vehicle.mapping.rolling_grid import (LIVE64, LIVE_TILE_BYTES, ViewKey, view_key,
    configure_grid, pack_live_tiles, reframe_grid)
from xenova_vehicle.perception.inputs import FrameStamp


FIELDS = ('state', 'support', 'permission', 'observed_us', 'time_known', 'conflict',
    'hit_origin', 'unordered_hit', 'hit_available_us')


class RollingGridTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('실제 RAW 배열 codec에는 기존 NumPy가 필요함')
        self.np = np

    def grid(self, *, offset=(0, 0), size=3, seeded=True):
        window = GridWindow(MapLattice((0., 0.), .125), offset, size, size)
        grid = EvidenceGrid(window.spec, frame_id='odom:original', calibration_id='a'*64,
            clock_id='original-J', kind='LIVE')
        if seeded:
            np = self.np
            values = np.arange(size*size, dtype=np.uint64).reshape(size, size)
            grid.state[:] = values % 3
            grid.support[:] = values % 2 == 0
            grid.permission[:] = values % 3 != 0
            grid.time_known[:] = values % 2 == 1
            grid.conflict[:] = values % 3 == 1
            grid.unordered_hit[:] = values % 4 == 0
            grid.observed_us[:] = np.uint64(2**53+101)+values
            grid.hit_available_us[:] = np.uint64(2**53+501)+values
            grid.hit_origin[:, :, 0] = values*.125
            grid.hit_origin[:, :, 1] = -.25-values*.125
            grid.hit_view = {0: 'original/raw/view-A', size*size-1: 'original/raw/view-B'}
            grid.revision, grid.applied_hits, grid.applied_free = 23, 41, 17
        configure_grid(grid, window)
        return grid, window

    def same_raw(self, left, right):
        for name in FIELDS:
            with self.subTest(field=name):
                self.assertEqual(getattr(left, name).dtype, getattr(right, name).dtype)
                self.assertEqual(getattr(left, name).tobytes(), getattr(right, name).tobytes())
        self.assertEqual(left.hit_view, right.hit_view)
        self.assertTrue(all(type(key) is ViewKey for key in left.hit_view.values()))

    def test_negative_tiles_roundtrip_preserves_exact_live64_fields_age_and_counters(self):
        grid, window = self.grid(offset=(-65, -1))
        before = deepcopy(grid)
        pool = dict(pack_live_tiles(grid, {}))
        self.assertEqual(len(pool), 4)
        self.assertEqual(tile_address(-1, -1), (-1, -1, 4095))
        self.assertEqual(tile_address(-64, -64), (-1, -1, 0))
        self.assertEqual(tile_address(-65, -65), (-2, -2, 4095))
        flags, observed, available, x, y, key = LIVE64.unpack_from(pool[(-2, -1)], 4095*64)
        self.assertEqual(LIVE64.size, 64)
        self.assertEqual((flags, observed, available, x, y), (4+64+128, 2**53+101, 2**53+501, 0., -.25))
        self.assertEqual(key, sha256(b'original/raw/view-A').digest()[:31])
        age = before.snapshot(2**53+3101, clock_id='original-J')['arrays']['age_ms'].tobytes()
        reframe_grid(grid, window.shifted((130, 70)), pool)
        self.assertFalse(bool(self.np.any(grid.state)))
        self.assertFalse(bool(self.np.any(grid.time_known)))
        self.assertFalse(bool(self.np.any(grid.observed_us)))
        reframe_grid(grid, window, pool)
        self.same_raw(grid, before)
        self.assertEqual(grid.snapshot(2**53+3101, clock_id='original-J')['arrays']['age_ms'].tobytes(), age)
        self.assertEqual((grid.revision, grid.applied_hits, grid.applied_free), (23, 41, 17))
        self.assertEqual(grid.spec.origin_xy, window.lattice.origin(window.offset_xy))

    def test_current_overlap_overrides_stale_pool_including_view_absence_and_new_cells_are_unknown(self):
        grid, window = self.grid(size=2)
        grid.state[:] = OCCUPIED
        grid.hit_view[1] = view_key('old-pool-view')
        pool = dict(pack_live_tiles(grid, {}))
        grid.state[0, 1] = FREE
        grid.observed_us[0, 1] = 2**53+777
        grid.hit_view.pop(1)
        reframe_grid(grid, window.shifted((1, 0)), pool)
        self.assertEqual(int(grid.state[0, 0]), FREE)
        self.assertEqual(int(grid.observed_us[0, 0]), 2**53+777)
        self.assertNotIn(0, grid.hit_view)
        self.assertTrue(bool(self.np.all(grid.state[:, 1] == 0)))
        self.assertTrue(bool(self.np.all(grid.observed_us[:, 1] == 0)))

    def test_candidate_cancel_does_not_modify_accepted_arrays_external_pool_or_old_body(self):
        accepted, window = self.grid()
        before = deepcopy(accepted)
        pool = dict(pack_live_tiles(accepted, {}))
        old_body = pool[(0, 0)]
        candidate = deepcopy(accepted)
        self.assertIs(candidate.hit_view[0], accepted.hit_view[0])
        reframe_grid(candidate, window.shifted((64, 0)), pool)
        candidate.state[0, 0] = OCCUPIED
        candidate.hit_view[0] = view_key('candidate-only')
        changed = dict(pack_live_tiles(candidate, pool))
        self.assertIn((1, 0), changed)
        self.assertIs(pool[(0, 0)], old_body)
        self.assertNotIn((1, 0), pool)
        self.assertFalse(any(value is pool for value in vars(candidate).values()))
        self.same_raw(accepted, before)
        self.assertEqual(pack_live_tiles(accepted, pool)[0][1], old_body)

    def test_malformed_tile_rejected_before_reframe_and_pool_pack_cannot_mutate_original(self):
        grid, window = self.grid()
        bad_state = bytearray(LIVE_TILE_BYTES); bad_state[0] = 3
        bad_origin = bytearray(LIVE_TILE_BYTES); struct.pack_into('<d', bad_origin, 17, math.nan)
        bad_view = bytearray(LIVE_TILE_BYTES); bad_view[33] = 1
        malformed = (b'', bytes(LIVE_TILE_BYTES-1), bytearray(LIVE_TILE_BYTES), None,
            bytes(bad_state), bytes(bad_origin), bytes(bad_view))
        arrays = tuple(getattr(grid, name) for name in FIELDS)
        original_views = grid.hit_view
        for body in malformed:
            with self.subTest(kind=type(body).__name__, length=None if body is None else len(body)):
                with self.assertRaises(ValueError): reframe_grid(grid, window.shifted((64, 0)), {(1, 0): body})
                with self.assertRaises(ValueError): pack_live_tiles(grid, {(0, 0): body})
                self.assertEqual(grid.rolling_window, window)
                self.assertIs(grid.hit_view, original_views)
                self.assertTrue(all(getattr(grid, name) is original for name, original in zip(FIELDS, arrays)))

    def test_mutation_captures_original_offset_and_view_key_is_not_rehashed_after_restore(self):
        grid, window = self.grid(offset=(-1, -1), size=4, seeded=False)
        domain = self.np.ones((4, 4), dtype=bool); grid.set_domain(domain, domain)
        stamp = FrameStamp('original-A', 'original-depth', 1, 'original-J', (1000, 1000), 1005)
        mutations = []
        grid.add_hits(((-.07, -.07, -.06, -.06),), stamp, view_id='original-capture-A',
            origin_xy=(0., 0.), observer=mutations.append)
        self.assertEqual(mutations[-1].window_offset_xy, (-1, -1))
        key = grid.hit_view[0]
        self.assertIs(view_key(key), key)
        pool = dict(pack_live_tiles(grid, {}))
        reframe_grid(grid, window.shifted((64, 64)), pool)
        reframe_grid(grid, window, pool)
        self.assertIs(type(grid.hit_view[0]), ViewKey)
        self.assertEqual(grid.hit_view[0], key)
        newer = FrameStamp('original-A', 'original-depth', 2, 'original-J', (1100, 1100), 1105)
        self.assertEqual(grid.add_free(((0, 0),), newer, view_id='original-capture-A',
            origin_xy=(.1, 0.), certificate_complete=True), 0)
        self.assertEqual(int(grid.state[0, 0]), OCCUPIED)
        self.assertEqual(grid.add_free(((0, 0),), newer, view_id='different-capture-B', origin_xy=(.1, 0.),
            certificate_complete=True, observer=mutations.append), 1)
        self.assertEqual(mutations[-1].window_offset_xy, (-1, -1))
        reframe_grid(grid, window.shifted((0, 0)), pool)
        self.assertEqual(mutations[0].window_offset_xy, (-1, -1))
        self.assertEqual(mutations[-1].window_offset_xy, (-1, -1))
        old_constructor = GridMutation('HIT', stamp, (1, 1), b'\1', b'\1')
        self.assertIsNone(old_constructor.window_offset_xy)

    def test_fixed_grid_view_policy_and_existing_five_argument_mutation_stay_unchanged(self):
        grid = EvidenceGrid(GridSpec(2, 2, .125, (0., 0.)), frame_id='fixed', calibration_id='a'*64,
            clock_id='original-J', kind='LIVE')
        domain = self.np.ones((2, 2), dtype=bool); grid.set_domain(domain, domain)
        stamp = FrameStamp('A', 'depth', 1, 'original-J', (1000, 1000), 1005)
        deltas = []
        grid.add_hits(((.03, .03, .04, .04),), stamp, view_id='raw-fixed-view', origin_xy=(0., 0.), observer=deltas.append)
        self.assertIs(type(grid.hit_view[0]), str)
        self.assertEqual(grid.add_free(((0, 0),), stamp, view_id='raw-fixed-view', origin_xy=(.1, 0.),
            certificate_complete=True, observer=deltas.append), 1)
        self.assertTrue(all(delta.window_offset_xy is None for delta in deltas))
        self.assertIsNone(grid.rolling_window)


if __name__ == '__main__':
    unittest.main()
