"""불변 scalar Interval 기준과 최적화한 ray 하나의 증명을 대조한다."""

import math
import random
import unittest

from xenova_vehicle.perception.free_space import RayBundle
from xenova_vehicle.perception.geometry import Interval
from xenova_vehicle.perception.ray_witness import witness


def scalar_reference(patch,lower,upper,guard):
    """최적화 전 Interval 경로. 새 arithmetic helper를 재사용하지 않는다."""
    origin=tuple(Interval(a,b) for a,b in zip(patch.origin_lower,patch.origin_upper))
    direction=tuple(Interval(a,b) for a,b in zip(patch.direction_lower,patch.direction_upper))
    axes=sorted(range(3),key=lambda i:-min(abs(direction[i].lo),abs(direction[i].hi)))
    for axis in axes:
        if direction[axis].lo<=0<=direction[axis].hi:continue
        cut=(lower[axis]+upper[axis])/2
        try:rho=(Interval(cut,cut)-origin[axis])/direction[axis]
        except ValueError:continue
        if rho.lo<=max(0.,patch.near_m)+guard or rho.hi>=min(patch.range_lower_m,patch.far_m)-guard:continue
        intersects=True
        for i in range(3):
            value=Interval(cut,cut) if i==axis else origin[i]+rho*direction[i]
            if value.lo<=lower[i]+guard or value.hi>=upper[i]-guard:
                intersects=False;break
        if intersects:return True
    return False


class RayWitnessTests(unittest.TestCase):
    def test_positive_boundary_near_and_first_surface_match_scalar(self):
        ray=RayBundle((0.,0.,1.),(0.,0.,1.),(0.,0.,-1.),(0.,0.,-1.),1.,1.,.01,3.,True)
        for lower,upper,expected in (((-.1,-.1,.3),(.1,.1,.7),True),
                ((0.,-.1,.3),(.1,.1,.7),False),
                ((-.1,-.1,-.3),(.1,.1,-.1),False),
                ((-.1,-.1,.999),(.1,.1,1.),False)):
            self.assertEqual(witness(ray,lower,upper,1e-9),expected)
            self.assertEqual(witness(ray,lower,upper,1e-9),scalar_reference(ray,lower,upper,1e-9))

    def test_original_interval_results_match_for_uncertain_origins_directions_and_boxes(self):
        rng=random.Random(1708)
        positive=negative=0
        for _ in range(240):
            raw=(rng.uniform(-1,1),rng.uniform(-1,1),rng.uniform(-1,-.1))
            length=math.sqrt(sum(v*v for v in raw));direction=tuple(v/length for v in raw)
            error=rng.choice((0.,.001,.01));origin=(rng.uniform(-.1,.1),rng.uniform(-.1,.1),rng.uniform(.1,1.))
            origin_error=rng.choice((0.,.001,.002))
            measured=rng.uniform(.2,3.)
            ray=RayBundle(tuple(v-origin_error for v in origin),tuple(v+origin_error for v in origin),
                tuple(max(-1.,v-error) for v in direction),tuple(min(1.,v+error) for v in direction),
                measured,measured+.001,.01,8.,True)
            distance=measured*rng.uniform(.1,1.2)
            center=tuple(o+distance*d+rng.uniform(-.05,.05) for o,d in zip(origin,direction))
            radius=rng.uniform(.015,.2)
            lower,upper=tuple(v-radius for v in center),tuple(v+radius for v in center)
            expected=scalar_reference(ray,lower,upper,1e-9)
            self.assertEqual(witness(ray,lower,upper,1e-9),expected)
            positive+=int(expected);negative+=int(not expected)
        self.assertGreater(positive,20);self.assertGreater(negative,20)

    def test_zero_denominators_and_unbounded_axis_arithmetic_are_not_witnesses(self):
        ray=RayBundle((0.,0.,0.),(0.,0.,0.),(1.,1e-320,0.),(1.,1e-320,0.),1.,1.,0.,2.,True)
        for lower,upper in (((2.,2.,2.),(3.,3.,3.)),((1e308,1e308,1e308),(1.1e308,1.1e308,1.1e308))):
            self.assertFalse(witness(ray,lower,upper,1e-9))
            self.assertEqual(witness(ray,lower,upper,1e-9),scalar_reference(ray,lower,upper,1e-9))
