"""음수 tile 경계·창 이동/복귀의 주소 보존 회귀. 관측·시계·이동 승인은 검사하지 않는다."""

import unittest

from xenova_vehicle.mapping.local_map import GridSpec
from xenova_vehicle.mapping.map_lattice import (GridWindow,MapLattice,RollingPolicy,tile_address,
    atlas_state_fields,read_atlas_window)


class MapLatticeTests(unittest.TestCase):
    def test_negative_floor_address_and_exact_signed_tile_edges(self):
        self.assertEqual(tile_address(-1,-1),(-1,-1,4095))
        self.assertEqual(tile_address(-64,-64),(-1,-1,0))
        self.assertEqual(tile_address(-65,64),(-2,1,63))
        for col,row in ((-129,-65),(-64,0),(-1,63),(0,64),(63,127),(64,128)):
            tx,ty,index=tile_address(col,row);y,x=divmod(index,64)
            self.assertEqual((tx*64+x,ty*64+y),(col,row))

    def test_pieces_cover_roi_once_without_crossing_tile_boundaries(self):
        window=GridWindow(MapLattice((.125,-.375),.025),(-1,-1),64,64)
        pieces=window.pieces();self.assertEqual(len(pieces),4)
        cells=set()
        for part in pieces:
            self.assertLessEqual(part.tile_col+part.width,64)
            self.assertLessEqual(part.tile_row+part.height,64)
            for row in range(part.height):
                for col in range(part.width):
                    local=(part.roi_row+row)*64+part.roi_col+col
                    global_cell=(part.tile_x*64+part.tile_col+col,part.tile_y*64+part.tile_row+row)
                    self.assertEqual(window.local_to_global(local),global_cell)
                    self.assertNotIn(local,cells);cells.add(local)
        self.assertEqual(len(cells),4096)

    def test_shift_out_and_back_keeps_lattice_and_original_global_proof_address(self):
        original=GridWindow.from_spec(GridSpec(64,32,.05,(0.,-.8)))
        cell=original.local_to_global(17*64+31);address=tile_address(*cell)
        shifted=original.shifted((-128,64))
        self.assertIs(shifted.lattice,original.lattice)
        self.assertIsNone(shifted.global_to_local(*cell))
        returned=shifted.shifted((0,0))
        self.assertEqual(returned.spec,original.spec)
        self.assertEqual(returned.global_to_local(*cell),17*64+31)
        self.assertEqual(tile_address(*returned.local_to_global(17*64+31)),address)

    def test_absolute_offset_does_not_accumulate_float_origin_drift(self):
        start=GridWindow.from_spec(GridSpec(64,64,.01,(.123,-.456)))
        moved=start
        for offset in range(-200,201):moved=moved.shifted((offset,-offset))
        self.assertEqual(moved.shifted((0,0)).spec.origin_xy,start.spec.origin_xy)
        self.assertEqual(moved.spec.origin_xy,start.shifted((200,-200)).spec.origin_xy)

    def test_invalid_addresses_and_oversized_rolling_roi_reject(self):
        lattice=MapLattice((0.,0.),.05)
        for value in (True,1.0,2**31*64,-2**31*64-1):
            with self.subTest(value=value),self.assertRaises(ValueError):tile_address(value,0)
        for args in (((0,0),65,64),((True,0),64,64),((2**31*64-1,0),2,1)):
            with self.subTest(args=args),self.assertRaises(ValueError):GridWindow(lattice,*args)
        with self.assertRaises(ValueError):GridWindow.from_spec(GridSpec(128,64))

    def test_explicit_policy_keeps_inner_window_and_recenters_both_directions(self):
        window=GridWindow.from_spec(GridSpec(64,64,.125,(0.,0.)))
        policy=RollingPolicy(8,0)
        self.assertIs(policy.window_for(window,(4.,4.),0.),window)
        forward=policy.window_for(window,(9.,4.),0.)
        self.assertEqual(forward.offset_xy,(40,0))
        backward=policy.window_for(forward,(-1.,4.),0.)
        self.assertEqual(backward.offset_xy,(-40,0))
        self.assertIs(backward.lattice,window.lattice)
        self.assertEqual(policy.window_for(backward,(4.,4.),0.).offset_xy,(0,0))

    def test_policy_heading_changes_only_nominal_crop_and_never_original_anchor(self):
        import math
        window=GridWindow.from_spec(GridSpec(64,64,.125,(0.,0.)))
        policy=RollingPolicy(8,8)
        self.assertEqual(policy.window_for(window,(6.,4.),0.).offset_xy,(24,0))
        self.assertIs(policy.window_for(window,(6.,4.),math.pi),window)
        self.assertEqual(policy.sha256,RollingPolicy(8,8).sha256)
        self.assertNotEqual(policy.sha256,RollingPolicy(8,0).sha256)
        with self.assertRaises(ValueError):policy.check_window(GridWindow.from_spec(GridSpec(8,8)))

    def test_versioned_small_metadata_keeps_signed_anchor_and_rejects_rebased_origin_or_policy(self):
        from copy import deepcopy
        policy=RollingPolicy(8,0)
        window=GridWindow.from_spec(GridSpec()).shifted((-64,128))
        state=atlas_state_fields(window,policy)
        self.assertEqual(read_atlas_window(state,window.spec,policy=policy),window)
        self.assertNotIn('tiles',state)
        for key,value in (('roi_origin_cell',[0,0]),('tile_cells',32),('policy_sha256','f'*64),
                ('lattice_origin_xy_m',[1.,2.]),('body','unexpected')):
            bad=deepcopy(state);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):read_atlas_window(bad,window.spec,policy=policy)
