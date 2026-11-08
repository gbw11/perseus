"""실제 shared codec에서 dense common-station 표현의 보수성을 검사한다.

도로·오차는 명시 software 기하다. 원 Road/catalog/ID/시각을 바꾸지 않고 wire만
간략화하며, C 연산 경계의 Python 대조는 네이티브 C 실행 수락을 대신하지 않는다.
"""

from dataclasses import asdict,replace
import math
import struct
import unittest

from xenova_drive_world import schema,encode,validate
from xenova_vehicle.perception.road import LaneHypothesis,RoadResult
from xenova_vehicle.transport.world_adapter import WorldBuildError,lane_model_from_road


def f32(value):return struct.unpack('<f',struct.pack('<f',value))[0]


def ceil32(value):
    result=f32(value)
    if result<value:
        result=struct.unpack('<f',struct.pack('<I',struct.unpack('<I',struct.pack('<f',result))[0]+1))[0]
    return result


def road(*,count=24,span=1.,error=.03,sagitta=0.,offset=(0.,0.),stations=None,points=None,inferred=None):
    stations=tuple(i*span/(count-1) for i in range(count)) if stations is None else stations
    divider=(tuple((offset[0]+s,offset[1]+4*sagitta*(s/span)*(1-s/span)) for s in stations)
             if points is None else points)
    count=len(divider)
    outer_left=tuple((x,y+.3) for x,y in divider);outer_right=tuple((x,y-.3) for x,y in divider)
    left=tuple(((p[0]+q[0])/2,(p[1]+q[1])/2) for p,q in zip(divider,outer_left))
    right=tuple(((p[0]+q[0])/2,(p[1]+q[1])/2) for p,q in zip(divider,outer_right))
    h=LaneHypothesis(0,((0,),(1,),(2,)),stations,divider,outer_left,outer_right,left,right,
        (False,)*count if inferred is None else inferred,((.25,.35),(.25,.35)),error,.002)
    return RoadResult('VALID',(h,),True,(),('software','rgb','original-clock',9),True)


def build(original,crossing=None,**changes):
    args=dict(lane_ids=(101,102),model_revision=17,lane_revisions=(21,31),
        crossing=bytes(len(original.hypotheses[0].station_s)-1) if crossing is None else crossing)
    args.update(changes)
    return lane_model_from_road(original,**args)


def kept_indices(original,model):
    points=original.hypotheses[0].divider
    return tuple(next(i for i,p in enumerate(points) if (f32(p[0]),f32(p[1]))==(v.x,v.y)) for v in model.divider)


class WorldAdapterStationBoundsTests(unittest.TestCase):
    def assert_c_heading(self,model):
        for lane in model.lanes:
            outer=[(f32(f32(2*p.x)-d.x),f32(f32(2*p.y)-d.y)) for p,d in zip(lane.centerline,model.divider)]
            threshold=f32(2*f32(f32(2*lane.position_bound_m)+model.divider_position_bound_m))
            for a,b in zip(outer,outer[1:]):
                length=f32(math.hypot(f32(b[0]-a[0]),f32(b[1]-a[1])))
                self.assertGreater(length,threshold)

    def assert_original_covered(self,original,model):
        h=original.hypotheses[0];indices=kept_indices(original,model)
        self.assertEqual((indices[0],indices[-1]),(0,len(h.station_s)-1))
        sources=h.divider,h.left_center,h.right_center,h.left_outer,h.right_outer
        divider=tuple((p.x,p.y) for p in model.divider)
        centers=[tuple((p.x,p.y) for p in lane.centerline) for lane in model.lanes]
        outers=[tuple((f32(f32(2*c[0])-d[0]),f32(f32(2*c[1])-d[1])) for c,d in zip(center,divider)) for center in centers]
        wires=(divider,*centers,*outers)
        bounds=(model.divider_position_bound_m,*(lane.position_bound_m for lane in model.lanes))
        outer_bounds=tuple(2*bound+bounds[0] for bound in bounds[1:])
        for q,(a,b) in enumerate(zip(indices,indices[1:])):
            for side,(source,wire,bound) in enumerate(zip(sources,wires,(*bounds,*outer_bounds))):
                for k in range(a,b+1):
                    t=(h.station_s[k]-h.station_s[a])/(h.station_s[b]-h.station_s[a])
                    nominal=tuple((1-t)*wire[q][j]+t*wire[q+1][j] for j in (0,1))
                    self.assertLessEqual(h.error_m+math.dist(source[k],nominal),bound,
                        (side,a,b,k,h.error_m,bound))
        self.assert_c_heading(model)

    def test_valid_original_two_station_encoding_is_unchanged(self):
        original=road(count=2,error=.001);h=original.hypotheses[0]
        expected=schema.LaneModel(revision=17,crossing=b'\x01',divider_state=b'\0\0',
            divider_position_bound_m=ceil32(h.error_m),divider_heading_bound_rad=ceil32(h.yaw_error_rad))
        for x,y in h.divider:expected.divider.add(x=x,y=y)
        for index,points in enumerate((h.left_center,h.right_center)):
            lane=expected.lanes.add(lane_id=(101,102)[index],adjacent_lane_id=(102,101)[index],revision=(21,31)[index],
                travel_direction=1,position_bound_m=ceil32(h.error_m+abs(f32(.15)-.15)),
                heading_bound_rad=ceil32(h.yaw_error_rad),point_state=b'\0\0')
            for x,y in points:lane.centerline.add(x=x,y=y)
        expected.divider_position_bound_m=expected.lanes[0].position_bound_m
        self.assertEqual(encode(build(original,b'\x01')),encode(expected))

    def test_dense_straight_thirty_and_forty_mm_bounds_keep_full_source_span(self):
        for count,error in ((24,.03),(24,.04),(32,.04)):
            with self.subTest(count=count,error=error):
                original=road(count=count,error=error);before=asdict(original)
                model=build(original)
                self.assertLess(len(model.divider),count);self.assertGreaterEqual(len(model.divider),2)
                self.assertEqual([lane.lane_id for lane in model.lanes],[101,102])
                self.assertEqual([lane.revision for lane in model.lanes],[21,31]);self.assertEqual(model.revision,17)
                self.assertGreaterEqual(model.divider_position_bound_m,error)
                self.assertTrue(all(lane.position_bound_m>=error for lane in model.lanes))
                self.assert_original_covered(original,model);validate(model)
                self.assertEqual(asdict(original),before)

    def test_curved_sagitta_and_all_original_outer_points_remain_inside_bounds(self):
        original=road(span=1.8,error=.03,sagitta=.08);model=build(original)
        self.assertLess(len(model.divider),24)
        self.assertGreater(model.divider_position_bound_m,original.hypotheses[0].error_m)
        self.assertGreater(model.divider_heading_bound_rad,original.hypotheses[0].yaw_error_rad)
        self.assertTrue(all(lane.heading_bound_rad>=original.hypotheses[0].yaw_error_rad for lane in model.lanes))
        self.assert_original_covered(original,model)

    def test_original_backtracking_self_crossing_or_wrong_side_is_not_repaired(self):
        for points in (((0.,0.),(.8,0.),(.4,0.),(1.,0.)),
                ((0.,0.),(1.,.2),(0.,.2),(1.,0.)),
                ((0.,0.),(1.,0.),(1.,1.),(0.,1.),(0.,0.))):
            with self.subTest(points=points),self.assertRaises(WorldBuildError):
                build(road(count=len(points),stations=tuple(range(len(points))),points=points,error=.04))
        original=road();h=original.hypotheses[0]
        malformed=replace(original,hypotheses=(replace(h,left_center=h.right_center,left_outer=h.right_outer),))
        with self.assertRaises(WorldBuildError):build(malformed)

    def test_short_full_span_is_rejected_instead_of_discarding_an_unrepresentable_tail(self):
        original=road(span=.12,error=.04);before=asdict(original)
        with self.assertRaisesRegex(WorldBuildError,'unbounded reconstructed lane heading'):build(original)
        self.assertEqual(asdict(original),before)

    def test_nonuniform_common_s_keeps_last_small_tail_and_source_correspondence(self):
        stations=tuple((i/23)**2 for i in range(23))+(1.00001,)
        original=road(stations=stations,error=.04,sagitta=.02)
        model=build(original)
        self.assertEqual((model.divider[-1].x,model.divider[-1].y),tuple(map(f32,original.hypotheses[0].divider[-1])))
        self.assert_original_covered(original,model)

    def test_crossing_and_inferred_information_survives_omitted_stations(self):
        flags=tuple(i==11 for i in range(24));original=road(error=.04,inferred=flags)
        crossing=bytes(int(i!=11) for i in range(23));model=build(original,crossing)
        indices=kept_indices(original,model)
        self.assertNotIn(11,indices)
        for q,(a,b) in enumerate(zip(indices,indices[1:])):
            self.assertEqual(model.crossing[q],int(all(crossing[a:b])))
            if any(flags[a:b+1]):
                for states in (model.divider_state,*(lane.point_state for lane in model.lanes)):
                    self.assertEqual(states[q:q+2],b'\x01\x01')
        self.assert_original_covered(original,model)

    def test_float32_offset_and_reconstructed_outer_have_no_heading_boundary_waiver(self):
        original=road(error=.04,offset=(2048.03,-1024.05));model=build(original)
        h=original.hypotheses[0]
        quantization=max(math.dist(p,tuple(map(f32,p))) for points in (h.divider,h.left_center,h.right_center) for p in points)
        bound=ceil32(h.error_m+quantization)
        self.assertGreaterEqual(model.divider_position_bound_m,bound)
        self.assertTrue(all(lane.position_bound_m>=bound for lane in model.lanes))
        self.assert_original_covered(original,model)

    def test_dense_representation_does_not_repair_external_ids_or_midpoint_contract(self):
        original=road()
        for change in (dict(lane_ids=(0,102)),dict(lane_ids=(101,101)),dict(lane_ids=(101,1<<32)),
                dict(model_revision=0),dict(lane_revisions=(True,31)),dict(crossing=b'\0')):
            with self.subTest(change=change),self.assertRaises(WorldBuildError):build(original,**change)
        h=original.hypotheses[0]
        bad=replace(h,left_center=tuple((x,y+.01) for x,y in h.left_center))
        with self.assertRaises(WorldBuildError):build(replace(original,hypotheses=(bad,)))

    def test_direct_float32_outer_rounding_and_c_coordinate_domain_remain_bounded(self):
        dy=8191.89990234375;left_y=8192.0498046875;right_y=8191.75
        for end_delta in (0.,.00048828125):
            with self.subTest(end_delta=end_delta):
                original=road(count=2,error=0.,inferred=(False,True));h=original.hypotheses[0]
                divider=((0.,dy),(1.,dy+end_delta))
                left=((0.,left_y),(1.,left_y));right=((0.,right_y),(1.,right_y))
                outer_left=tuple((2*c[0]-d[0],2*c[1]-d[1]) for c,d in zip(left,divider))
                outer_right=tuple((2*c[0]-d[0],2*c[1]-d[1]) for c,d in zip(right,divider))
                original=replace(original,hypotheses=(replace(h,divider=divider,left_center=left,right_center=right,
                    left_outer=outer_left,right_outer=outer_right,yaw_error_rad=0.),))
                model=build(original,b'\x01')
                self.assertEqual(len(model.divider),2)
                self.assertGreaterEqual(2*model.lanes[0].position_bound_m+model.divider_position_bound_m,.00048828125)
                self.assertEqual(model.divider_state,b'\0\x01')
                self.assertTrue(all(lane.point_state==b'\0\x01' for lane in model.lanes))
                self.assertEqual(model.crossing,b'\x01')
                if end_delta:self.assertGreater(model.lanes[0].heading_bound_rad,0.)
                self.assert_original_covered(original,model)
        for offset in ((999999.99,0.),(1_000_000.,0.),(0.,1_000_000.)):
            with self.subTest(offset=offset),self.assertRaisesRegex(WorldBuildError,'C domain'):
                build(road(count=2,span=.5,error=0.,offset=offset))
