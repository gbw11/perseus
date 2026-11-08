"""정합 outer-set·원 age·중복 관측과 odom 경계의 독립 반례."""

from dataclasses import replace
import math
import unittest

from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.mapping.registration import (PointFeature, SegmentFeature, FrozenReference,
    PoseBox, RegistrationProfile, register)
from xenova_vehicle.mapping.odometry import PoseSample, PoseBuffer
from xenova_vehicle.mapping.local_map import SourceGate, EvidenceGrid, GridSpec, UNKNOWN, FREE, OCCUPIED


CAL = 'a'*64


def stamp(sequence=1, time=100_000):
    return FrameStamp('source','right',sequence,'clock',(time,time),time+100)


def reference():
    return FrozenReference(7,CAL,(
        SegmentFeature((0.,0.),(1.,0.),.001,'h','old'),
        SegmentFeature((0.,0.),(0.,1.),.001,'v','old')))


def features():
    return (PointFeature((.2,0.),.001,'h','new',('h',)),
            PointFeature((.8,0.),.001,'h','new',('h',)),
            PointFeature((0.,.2),.001,'v','new',('v',)),
            PointFeature((0.,.8),.001,'v','new',('v',)))


class RegistrationTests(unittest.TestCase):
    def test_corner_is_bounded_and_wrong_prior_is_inconsistent(self):
        prior = PoseBox((-.02,.02),(-.02,.02),(-.01,.01))
        result = register(features(),reference(),prior,calibration_id=CAL)
        self.assertEqual(result.status,'BOUNDED')
        self.assertLessEqual(math.hypot(*result.representative[:2]),result.position_error_m)
        wrong = PoseBox((2.,2.1),(2.,2.1),(-.01,.01))
        self.assertEqual(register(features(),reference(),wrong,calibration_id=CAL).status,'INCONSISTENT')

    def test_parallel_ridge_is_not_a_unique_pose(self):
        ref = FrozenReference(1,CAL,(SegmentFeature((-3.,0.),(3.,0.),0.,'line','old'),))
        obs = (PointFeature((.2,0.),0.,'line','new'), PointFeature((.8,0.),0.,'line','new'))
        prior = PoseBox((-.5,.5),(-.01,.01),(-.005,.005))
        result = register(obs,ref,prior,RegistrationProfile(cell_steps=(.1,.02,.01)),calibration_id=CAL)
        self.assertEqual(result.status,'AMBIGUOUS_OR_UNOBSERVABLE')
        for x in (-.5,-.2,0.,.2,.5):
            self.assertTrue(any(c.x[0]<=x<=c.x[1] and c.y[0]<=0<=c.y[1] and c.yaw[0]<=0<=c.yaw[1]
                                for c in result.feasible))

    def test_repeated_corner_alias_and_budget_preserve_possible_cells(self):
        ref = FrozenReference(2,CAL,reference().segments+(
            SegmentFeature((1.,0.),(2.,0.),.001,'h','old'),
            SegmentFeature((1.,0.),(1.,1.),.001,'v','old')))
        prior = PoseBox((-.02,1.02),(-.01,.01),(-.005,.005))
        result = register(features(),ref,prior,RegistrationProfile(cell_steps=(.04,.02,.01)),calibration_id=CAL)
        self.assertNotEqual(result.status,'BOUNDED')
        for x in (0.,1.):
            self.assertTrue(any(c.x[0]<=x<=c.x[1] and c.y[0]<=0<=c.y[1] for c in result.feasible))
        limited = register(features(),ref,prior,RegistrationProfile(max_cells=1),calibration_id=CAL)
        self.assertEqual(limited.status,'UNKNOWN_BUDGET')
        self.assertFalse(limited.complete)
        self.assertTrue(any(c.x[0]<=1<=c.x[1] for c in limited.feasible))

    def test_same_capture_cannot_certify_itself_and_outliers_count_components(self):
        with self.assertRaises(ValueError):
            register(tuple(replace(p,capture_id='old') for p in features()),reference(),
                     PoseBox((0.,0.),(0.,0.),(0.,0.)),calibration_id=CAL)
        obs = features()+(PointFeature((3.,4.),0.,'bad','new',('absent',)),)
        prior = PoseBox((0.,0.),(0.,0.),(0.,0.))
        self.assertEqual(register(obs,reference(),prior,calibration_id=CAL).status,'INCONSISTENT')
        allowed = register(obs,reference(),prior,RegistrationProfile(outlier_components=1),calibration_id=CAL)
        self.assertEqual(allowed.status,'BOUNDED')


class SourceAndMapTests(unittest.TestCase):
    def test_duplicate_and_late_nn_do_not_renew_geometry_source(self):
        gate = SourceGate()
        self.assertTrue(gate.accept(stamp(),'b'*64))
        self.assertFalse(gate.accept(replace(stamp(),available_us=999_999),'b'*64))
        with self.assertRaises(ValueError):
            gate.accept(stamp(),'c'*64)
        self.assertTrue(gate.accept(stamp(2,200_000),'c'*64))
        self.assertFalse(gate.accept(stamp(),'b'*64))

    def test_age_expiry_hit_priority_and_independent_view_clear(self):
        import numpy as np
        grid = EvidenceGrid(GridSpec(2,2,.1,(0.,0.)),frame_id='map',calibration_id=CAL,clock_id='clock',kind='SYNTHETIC')
        grid.set_domain(np.ones((2,2),dtype=np.bool_),np.ones((2,2),dtype=np.bool_))
        self.assertEqual(grid.add_free(((0,0),),stamp(),view_id='right',origin_xy=(0.,0.),certificate_complete=True),1)
        fresh = grid.snapshot(110_000,clock_id='clock')
        self.assertEqual(int(fresh['arrays']['age_ms'][0,0]),10)
        self.assertEqual(int(grid.snapshot(700_000,clock_id='clock')['arrays']['state'][0,0]),UNKNOWN)
        grid.add_hits(((.02,.02,.04,.04),),stamp(2,200_000),view_id='right',origin_xy=(0.,0.))
        self.assertEqual(grid.add_free(((0,0),),stamp(3,300_000),view_id='right',origin_xy=(0.,0.),certificate_complete=True),0)
        self.assertEqual(int(grid.snapshot(310_000,clock_id='clock')['arrays']['state'][0,0]),OCCUPIED)
        self.assertEqual(int(grid.snapshot(6_000_000,clock_id='clock')['arrays']['state'][0,0]),UNKNOWN)
        self.assertEqual(int(grid.state[0,0]),OCCUPIED)
        self.assertEqual(grid.add_free(((0,0),),stamp(4,400_000),view_id='right',origin_xy=(.05,0.),certificate_complete=True),1)
        self.assertEqual(int(grid.state[0,0]),FREE)

    def test_small_hit_touches_full_cells_and_permission_is_not_free(self):
        import numpy as np
        grid = EvidenceGrid(GridSpec(2,2,.1,(0.,0.)),frame_id='frame',calibration_id=CAL,clock_id='clock',kind='LIVE')
        grid.set_domain(np.ones((2,2),dtype=np.bool_),np.ones((2,2),dtype=np.bool_))
        self.assertEqual(grid.snapshot(100_000,clock_id='clock')['counts']['free'],0)
        self.assertEqual(set(grid.touched((.1,.1,.1,.1))),{(0,0),(0,1),(1,0),(1,1)})

    def test_late_other_stream_hit_cannot_replace_the_latest_view_barrier(self):
        grid = EvidenceGrid(GridSpec(1,1,.1,(0.,0.)),frame_id='map',calibration_id=CAL,clock_id='clock',kind='SYNTHETIC')
        grid.support[:]=grid.permission[:]=True
        box = ((.02,.02,.04,.04),)
        grid.add_hits(box,stamp(2,200_000),view_id='A',origin_xy=(0.,0.))
        old = replace(stamp(1,100_000),stream_id='other')
        grid.add_hits(box,old,view_id='B',origin_xy=(.1,0.))
        self.assertEqual(grid.hit_view[0],'A')
        self.assertEqual(int(grid.observed_us[0,0]),200_000)
        self.assertEqual(grid.add_free(((0,0),),stamp(3,300_000),view_id='A',origin_xy=(0.,0.),certificate_complete=True),0)

    def test_unknown_time_hit_does_not_inherit_free_time_or_accept_ordered_clear(self):
        grid = EvidenceGrid(GridSpec(1,1,.1,(0.,0.)),frame_id='map',calibration_id=CAL,clock_id='clock',kind='LIVE')
        grid.support[:]=grid.permission[:]=True
        grid.add_free(((0,0),),stamp(),view_id='A',origin_xy=(0.,0.),certificate_complete=True)
        unknown = replace(stamp(2,200_000),captured_us=None)
        grid.add_hits(((.02,.02,.04,.04),),unknown,view_id='B',origin_xy=(0.,0.))
        self.assertFalse(grid.time_known[0,0])
        self.assertTrue(grid.unordered_hit[0,0])
        self.assertEqual(grid.add_free(((0,0),),stamp(3,300_000),view_id='A',origin_xy=(.1,0.),certificate_complete=True),0)


class OdometryTests(unittest.TestCase):
    def sample(self,at,x=0.,epoch='odom',origin='ODOMETRY'):
        return PoseSample(epoch,'clock',CAL,at,x,0.,0.,.001,.001,origin)

    def test_interpolation_bound_gap_epoch_and_no_extrapolation(self):
        buf = PoseBuffer(max_speed_mps=1.)
        buf.add(self.sample(0))
        buf.add(self.sample(100_000,.1))
        query = stamp(1,50_000)
        pose = buf.at(query,CAL)
        self.assertAlmostEqual(pose.sample.x,.05)
        self.assertGreaterEqual(pose.sample.position_error_m,.05)
        self.assertIsNone(buf.at(stamp(2,200_000),CAL))
        self.assertIsNone(buf.at(replace(query,captured_us=None),CAL))
        buf.add(self.sample(200_000,.2,epoch='new'))
        self.assertIsNone(buf.at(query,CAL))

    def test_oracle_is_opt_in_and_clock_mismatch_is_not_stationary(self):
        buf = PoseBuffer()
        buf.add(self.sample(100_000,origin='ORACLE_SIMULATION'))
        self.assertIsNone(buf.at(stamp(),CAL))
        self.assertEqual(buf.at(stamp(),CAL,allow_oracle=True).sample.origin,'ORACLE_SIMULATION')
        self.assertIsNone(buf.at(replace(stamp(),clock_id='different'),CAL,allow_oracle=True))


if __name__ == '__main__':
    unittest.main()
