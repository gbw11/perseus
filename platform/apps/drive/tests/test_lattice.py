"""Real search/tracker with independent SAT geometry and bicycle dynamics."""
import ctypes as C
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import pytest

HOME=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOME/'world/python'))
from world_codec import encode
spec=importlib.util.spec_from_file_location('lattice_world_fixture',HOME/'examples/world_fixture.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
CFG=json.loads((HOME/'profiles/lattice-lab-1.json').read_text())
FOUND=1;STOP=2

@pytest.fixture(scope='module')
def library(tmp_path_factory):
    output=tmp_path_factory.mktemp('lattice-product')/'lattice.so'
    world=HOME/'world';planner=HOME/'planner'
    sources=[world/'world.c',world/'generated/world.pb.c',*(world/'nanopb'/v for v in ('pb_common.c','pb_decode.c','pb_encode.c')),
             planner/'lattice.c',planner/'engine.c',planner/'generated/primitives.c',HOME/'tests/lattice_bridge.c']
    cmd=['cc','-std=c11','-D_POSIX_C_SOURCE=200809L','-DTEST_PRIMITIVE_SHA="'+fixture.PRIMITIVE+'"','-O2','-g','-Wall','-Wextra','-Werror',
         '-fsanitize=undefined','-shared','-fPIC',*[f'-I{p}' for p in (world,world/'generated',world/'nanopb',planner)],
         *map(str,sources),'-lm','-o',str(output)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    lib=C.CDLL(str(output))
    lib.probe_new.argtypes=[C.c_char_p,C.c_size_t];lib.probe_new.restype=C.c_void_p
    lib.probe_free.argtypes=[C.c_void_p]
    lib.probe_decoded.argtypes=[C.c_void_p]
    lib.probe_plan.argtypes=[C.c_void_p,*([C.c_float]*7),C.c_uint,C.c_uint,C.c_uint64,C.c_uint,C.c_float]
    lib.probe_count.argtypes=[C.c_void_p];lib.probe_count.restype=C.c_uint
    lib.probe_direction.argtypes=[C.c_void_p]
    lib.probe_point.argtypes=[C.c_void_p,C.c_uint,C.POINTER(C.c_float)]
    lib.probe_track.argtypes=[C.c_void_p,*([C.c_float]*6),C.POINTER(C.c_float)]
    lib.probe_metrics.argtypes=[C.c_void_p,C.POINTER(C.c_uint64)]
    lib.probe_update.argtypes=[C.c_void_p,C.c_char_p,C.c_size_t]
    lib.probe_valid.argtypes=[C.c_void_p]
    lib.engine_new.restype=C.c_void_p
    lib.engine_free.argtypes=[C.c_void_p]
    lib.engine_world.argtypes=[C.c_void_p,C.c_char_p,C.c_size_t,C.c_uint64]
    lib.engine_vehicle.argtypes=[C.c_void_p,C.c_char_p,C.c_size_t]
    lib.engine_plan.argtypes=[C.c_void_p,C.c_uint64,C.c_uint]
    lib.engine_track.argtypes=[C.c_void_p,C.c_uint64,C.POINTER(C.c_float)]
    lib.engine_apply.argtypes=[C.c_void_p,C.c_char_p,C.c_size_t,C.c_uint64]
    lib.engine_state.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t]
    return lib

class Product:
    def __init__(self,lib,world):
        self.lib=lib;self.world=world;wire=encode(world);self.handle=lib.probe_new(wire,len(wire))
        assert self.handle and lib.probe_decoded(self.handle)==0
    def close(self):self.lib.probe_free(self.handle);self.handle=None
    def plan(self,start=(.3,1.6,0,0),goal=(4.8,1.6,0),**limits):
        return self.lib.probe_plan(self.handle,*start,*goal,limits.get('nodes',0),limits.get('collisions',0),
            limits.get('cancel_after',0),limits.get('budget_ms',10000),limits.get('width',0))
    def path(self):
        data=[]
        for k in range(self.lib.probe_count(self.handle)):
            point=(C.c_float*5)();assert self.lib.probe_point(self.handle,k,point)==0;data.append(list(point))
        return data
    def track(self,pose,v,steer,dt=.02):
        out=(C.c_float*8)();rc=self.lib.probe_track(self.handle,*pose,v,steer,dt,out);return rc,list(out)
    def metrics(self):
        out=(C.c_uint64*11)();self.lib.probe_metrics(self.handle,out);return list(out)
    def update(self,world):
        wire=encode(world);assert self.lib.probe_update(self.handle,wire,len(wire))==0;self.world=world

def obstacle(world,x0,x1,y0,y1,*,unknown=False):
    values=bytearray(world.occupancy)
    for y in range(y0,y1):
        for x in range(x0,x1):values[y*world.width+x]=255 if unknown else 100
    world.occupancy=bytes(values)

def independent_footprint(world,point,half_width=None):
    x,y,yaw=point[:3];c=math.cos(yaw);s=math.sin(yaw)
    length=(CFG['front']+CFG['rear'])/2+CFG['margin']
    width=(half_width or CFG['half_width'])+CFG['margin'];shift=(CFG['front']-CFG['rear'])/2
    cx=x+shift*c;cy=y+shift*s
    # Independent separating-axis projections, using the actual obstacle
    # square vertices instead of the product's grid-range/SAT shortcut.
    body=[(cx+c*u-s*v,cy+s*u+c*v) for u,v in ((-length,-width),(length,-width),(length,width),(-length,width))]
    r=world.resolution_m
    for iy in range(world.height):
        for ix in range(world.width):
            if world.occupancy[iy*world.width+ix]<70 and world.cell_flags[iy*world.width+ix]&1:continue
            square=[(ix*r,iy*r),((ix+1)*r,iy*r),((ix+1)*r,(iy+1)*r),(ix*r,(iy+1)*r)]
            axes=((1,0),(0,1),(c,s),(-s,c))
            for ax,ay in axes:
                a=[px*ax+py*ay for px,py in body];b=[px*ax+py*ay for px,py in square]
                if max(a)<min(b) or max(b)<min(a):break
            else:return False
    return True

@pytest.mark.parametrize('side',[-1,0,1])
def test_actual_search_straight_left_right_bypass_and_rejoin(library,side):
    world=fixture.world()
    if side:
        # Block one side of a central obstruction to require the other; the
        # planner sees occupancy only, never this scenario label.
        obstacle(world,19,24,13,19)
        obstacle(world,16,27,19 if side<0 else 2,30 if side<0 else 13)
    product=Product(library,world)
    try:
        rc=product.plan();path=product.path();metrics=product.metrics()
        assert rc==FOUND,(rc,metrics)
        assert len(path)>50 and metrics[0]>1 and metrics[1]>5
        assert math.dist(path[-1][:2],(4.8,1.6))<=CFG['goal_distance']+.001
        assert abs(path[-1][2])<=CFG['goal_heading']+.001
        if side:
            assert library.probe_direction(product.handle)==side
            offsets=[p[1]-1.6 for p in path]
            assert (max(offsets) if side>0 else min(offsets))*side>.35
            assert abs(path[-1][1]-1.6)<.12
        for k,p in enumerate(path):
            assert independent_footprint(world,p)
            assert abs(p[3])<=CFG['max_steer']+.0001
            if k:
                ds=math.dist(p[:2],path[k-1][:2]);dt=ds/CFG['primitive_speed']
                assert abs(p[3]-path[k-1][3])<=CFG['steer_rate']*dt+.001
        assert library.probe_valid(product.handle)==FOUND
    finally:product.close()

def test_current_heading_and_non_nominal_curvature_are_connected(library):
    product=Product(library,fixture.world())
    try:
        rc=product.plan(start=(.5,1.45,.15,.13))
        assert rc==FOUND,(rc,product.metrics())
        path=product.path()
        assert path[0][2]==pytest.approx(.15) and path[0][3]==pytest.approx(.13)
        assert abs(path[1][3]-.13)<.1
    finally:product.close()


def test_unrelated_obstacle_does_not_turn_a_follow_correction_into_bypass(library):
    world=fixture.world();obstacle(world,10,12,27,29)
    product=Product(library,world)
    try:
        assert product.plan(start=(.3,1.45,0,0))==FOUND
        assert library.probe_direction(product.handle)==0
        assert library.probe_valid(product.handle)==FOUND
    finally:product.close()


def test_terminal_heading_errors_share_one_total_allowance(library):
    product=Product(library,fixture.world())
    try:
        goal=(4.8,1.6,.12)
        assert product.plan(goal=goal)==FOUND
        end=product.path()[-1]
        # Individually the actual heading is within goal_heading, but its
        # deviation from the planned terminal state must also be accounted for.
        sign=1 if end[2]>=goal[2] else -1
        yaw=goal[2]-sign*.20
        total=abs(end[2]-goal[2])+abs(yaw-end[2])+.01
        assert abs(yaw-goal[2])<CFG['goal_heading']<total
        rc,cmd=product.track((goal[0],goal[1],yaw),0,end[3])
        assert rc!=STOP and cmd[0]==0
    finally:product.close()


@pytest.mark.parametrize('gx',[4.8,5.53])
@pytest.mark.parametrize('start',[(4.5282,1.5311,.0051,0.),(4.2807,1.5343,-.063,0.)])
def test_rebuild_near_existing_merge_goal_finds_a_real_short_connection(library,gx,start):
    product=Product(library,fixture.world())
    try:
        assert product.plan(start=start,goal=(gx,1.6,0))==FOUND,product.metrics()
        path=product.path();assert len(path)>=2
        assert math.dist(path[-1][:2],(gx,1.6))<=CFG['goal_distance']
        assert abs(path[-1][2])<=CFG['goal_heading']
        assert all(independent_footprint(product.world,p) for p in path)
        rc,command=product.track(start[:3],0,0)
        assert rc==FOUND and command[0]>0
    finally:product.close()


def test_already_reached_goal_is_a_checked_stationary_path(library):
    product=Product(library,fixture.world())
    try:
        assert product.plan(start=(4.8,1.6,.01,.13),goal=(4.8,1.6,0))==FOUND
        assert library.probe_valid(product.handle)==FOUND
        rc,command=product.track((4.8,1.6,.01),0,.13)
        assert rc==STOP and command[0]==0
    finally:product.close()


def test_curved_reference_and_road_corridor_use_actual_geometry(library):
    world=fixture.world();radius=12.
    for group,offset in ((world.reference,0),(world.left_boundary,.65),(world.right_boundary,-.65)):
        for point in group:
            theta=math.asin(point.x/radius);x=radius*math.sin(theta);y=13-radius*math.cos(theta)
            point.x=x-offset*math.sin(theta);point.y=y+offset*math.cos(theta)
    start=(.4,13-math.sqrt(radius*radius-.4**2),math.asin(.4/radius),math.atan(.25/radius))
    goal=(4.7,13-math.sqrt(radius*radius-4.7**2),math.asin(4.7/radius))
    product=Product(library,world)
    try:
        assert product.plan(start=start,goal=goal)==FOUND,product.metrics()
        path=product.path()
        assert path[-1][1]-path[0][1]>.7 and path[-1][2]>.2
        assert math.dist(path[-1][:2],goal[:2])<CFG['goal_distance']+.001
        assert library.probe_valid(product.handle)==FOUND
    finally:product.close()


@pytest.mark.parametrize('side',[-1,1])
@pytest.mark.parametrize('priority',[False,True])
def test_tight_curve_reaches_goal_with_continuous_nonzero_steering(library,side,priority):
    world=fixture.world(height=64);radius=1.2;cx=cy=2.7;half_road=.4
    for group in (world.reference,world.left_boundary,world.right_boundary):del group[:]
    road_left=[];road_right=[]
    for k in range(61):
        angle=-.5+k*.05;x=cx+radius*math.sin(angle);y=cy-side*radius*math.cos(angle)
        nx,ny=-side*math.sin(angle),math.cos(angle)
        world.reference.add(x=x,y=y)
        hard=.9 if priority else half_road
        world.left_boundary.add(x=x+nx*hard,y=y+ny*hard)
        world.right_boundary.add(x=x-nx*hard,y=y-ny*hard)
        road_left.append(fixture.schema.Point(x=x+nx*half_road,y=y+ny*half_road))
        road_right.append(fixture.schema.Point(x=x-nx*half_road,y=y-ny*half_road))
    if priority:
        from xenova_drive_world import mark_road
        mark_road(world,road_left,road_right)
    steering=side*math.atan(CFG['wheelbase']/radius)
    start=(cx,cy-side*radius,0,steering);angle=1.8
    goal=(cx+radius*math.sin(angle),cy-side*radius*math.cos(angle),side*angle)
    product=Product(library,world)
    try:
        assert product.plan(start=start,goal=goal)==FOUND,product.metrics()
        path=product.path()
        assert library.probe_direction(product.handle)==0
        assert math.dist(path[-1][:2],goal[:2])<=CFG['goal_distance']
        assert abs(math.remainder(path[-1][2]-goal[2],2*math.pi))<=CFG['goal_heading']
        assert path[-1][3]*side>.1 # The curved road must not require a straight tail.
        assert library.probe_valid(product.handle)==FOUND
        for k,p in enumerate(path):
            # Independent annulus geometry, including all four actual body corners.
            cs,sn=math.cos(p[2]),math.sin(p[2])
            for u in (-CFG['rear']-CFG['margin'],CFG['front']+CFG['margin']):
                for v in (-CFG['half_width']-CFG['margin'],CFG['half_width']+CFG['margin']):
                    r=math.hypot(p[0]+cs*u-sn*v-cx,p[1]+sn*u+cs*v-cy)
                    assert radius-half_road-.002<=r<=radius+half_road+.002
            if k:
                dt=math.dist(p[:2],path[k-1][:2])/CFG['primitive_speed']
                assert abs(p[3]-path[k-1][3])<=CFG['steer_rate']*dt+.001
        rc,command=product.track(start[:3],0,steering)
        assert rc==FOUND and command[0]>0
    finally:product.close()


@pytest.mark.parametrize('half_road',[.36,1.0])
def test_road_priority_uses_shoulder_only_for_blocked_narrow_road_and_returns(library,half_road):
    from xenova_drive_world import mark_road
    world=fixture.world();obstacle(world,19,24,13,19)
    mark_road(world,[fixture.schema.Point(x=p.x,y=1.6+half_road) for p in world.reference],
                    [fixture.schema.Point(x=p.x,y=1.6-half_road) for p in world.reference])
    product=Product(library,world)
    try:
        assert product.plan(budget_ms=2000)==FOUND,product.metrics()
        path=product.path()
        corners=[]
        for point in path:
            assert independent_footprint(world,point)
            x,y,yaw=point[:3]
            corners.append(max(abs(y+math.sin(yaw)*u+math.cos(yaw)*v-1.6)
                for u in (-CFG['rear'],CFG['front']) for v in (-CFG['half_width'],CFG['half_width'])))
        assert (max(corners)>half_road)==(half_road<.5)
        assert corners[-1]<half_road
        assert product.metrics()[3]<=CFG['collision_limit'] and product.metrics()[4]<=CFG['node_limit']
        blocked_world=fixture.world(sequence=2);mark_road(blocked_world,list(world.left_boundary),list(world.right_boundary))
        blocked_world.occupancy=bytes([255])*len(blocked_world.occupancy)
        product.update(blocked_world)
        assert library.probe_valid(product.handle)==10
        assert product.track((.3,1.6,0),0,0)[0]==10
    finally:product.close()


def test_grid_origin_rotation_does_not_change_collision_frame(library):
    world=fixture.world();obstacle(world,19,24,13,19)
    angle=.4;tx=-2.;ty=3.;cs=math.cos(angle);sn=math.sin(angle)
    def transform(x,y):return tx+cs*x-sn*y,ty+sn*x+cs*y
    world.origin.x=tx;world.origin.y=ty;world.origin.yaw=angle
    for group in (world.reference,world.left_boundary,world.right_boundary):
        for p in group:p.x,p.y=transform(p.x,p.y)
    world.reference_vehicle_pose.x,world.reference_vehicle_pose.y=transform(.3,1.6)
    world.reference_vehicle_pose.yaw=angle
    product=Product(library,world)
    try:
        assert product.plan(start=(*transform(.3,1.6),angle,0),goal=(*transform(4.8,1.6),angle))==FOUND,product.metrics()
        assert library.probe_valid(product.handle)==FOUND
    finally:product.close()

@pytest.mark.parametrize('limit,value,expected', [('nodes',1,4),('collisions',1,5),('cancel_after',5,7),('budget_ms',0,6)])
def test_bounded_exits_then_same_product_can_plan_normally(library,limit,value,expected):
    product=Product(library,fixture.world())
    try:
        assert product.plan(**{limit:value})==expected
        # New explicit job after completion, not a deadline extension/retry
        # inside the old job. Reset test resource policy, not an owned thread.
    finally:product.close()
    product=Product(library,fixture.world())
    try:assert product.plan()==FOUND
    finally:product.close()

def test_unknown_and_narrow_passages_never_certify_a_center_point(library):
    world=fixture.world();obstacle(world,16,25,0,world.height,unknown=True)
    product=Product(library,world)
    try:assert product.plan() in (3,4,5,10)
    finally:product.close()
    world=fixture.world()
    obstacle(world,16,25,0,15);obstacle(world,16,25,17,world.height)
    product=Product(library,world)
    try:assert product.plan() in (3,4,5,10)
    finally:product.close()

def test_real_tracker_closed_loop_moves_and_returns_to_reference(library):
    world=fixture.world();obstacle(world,19,24,13,19)
    product=Product(library,world)
    try:
        assert product.plan()==FOUND
        x,y,yaw=.3,1.6,0.;v=steer=0.;deviation=0
        for step in range(2200):
            rc,cmd=product.track((x,y,yaw),v,steer)
            if rc==STOP:break
            assert rc==FOUND,(rc,step,(x,y,yaw),cmd)
            velocity,delta=cmd[:2]
            assert abs(delta-steer)<=CFG['steer_rate']*.02+.0001
            assert velocity<=CFG['max_velocity']+.0001
            assert cmd[5]==pytest.approx(2*cmd[3]/(cmd[2]**2+cmd[3]**2),rel=1e-4,abs=1e-5)
            # Independent bounded ideal actuator model driven only by the
            # returned command. No path coordinates are assigned to the car.
            v+=max(-CFG['max_brake']*.02,min(CFG['max_accel']*.02,velocity-v))
            steer+=max(-CFG['steer_rate']*.02,min(CFG['steer_rate']*.02,delta-steer))
            turn=v/CFG['wheelbase']*math.tan(steer)*.02
            x+=v*.02*math.cos(yaw+turn/2);y+=v*.02*math.sin(yaw+turn/2);yaw=math.remainder(yaw+turn,2*math.pi)
            deviation=max(deviation,abs(y-1.6))
            if step%5==0:assert independent_footprint(world,(x,y,yaw))
        else:pytest.fail(f'tracker never reached its actual goal: {(x,y,yaw,v,steer)}')
        assert x>4.5 and abs(y-1.6)<.15 and deviation>.35
        assert abs(yaw)<=CFG['goal_heading']+.001
        assert product.metrics()[10]>0
    finally:product.close()

def test_changed_obstacle_invalidates_path_and_final_command(library):
    product=Product(library,fixture.world())
    try:
        assert product.plan()==FOUND
        world=fixture.world(sequence=2);obstacle(world,5,10,12,20)
        product.update(world)
        assert library.probe_valid(product.handle)==10
        rc,cmd=product.track((.3,1.6,0),.4,0)
        assert rc==10 and cmd[0]==0 and cmd[7]
    finally:product.close()
