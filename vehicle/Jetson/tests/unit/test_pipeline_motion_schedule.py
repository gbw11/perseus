"""원 full-capture tube→필요 셀 선택→완료시각 재검사 경계를 검산한다.

영상 추출만 고정 typed 증거로 대체하고 motion/certificate/scheduler/grid는 실제 구현을
사용한다. Vehicle·clock fixture의 보정과 운동 가정은 실물 성능 수락이 아니다.
"""

from dataclasses import replace
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from xenova_vehicle.mapping.free_schedule import FreeScheduler,stopping_region
from xenova_vehicle.mapping.local_map import FREE,GridSpec
from xenova_vehicle.mapping.odometry import PoseEstimate,PoseSample
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.free_space import TargetClass
from xenova_vehicle.perception.inputs import FrameStamp,ImageFrame,PerceptionInput
from xenova_vehicle.perception.motion import widen_motion_rays
from xenova_vehicle.perception.pipeline import PerceptionPipeline,PipelineProfile

import test_depth_evidence as depth_fixture


class PipelineMotionScheduleTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.calibration=depth_fixture.profile(3)
        self.profile=PipelineProfile(grid=GridSpec(2,1,.01,(-.01,-.005)),max_free_cells=1,
            max_free_tiles=64,max_free_ray_tests=256,permission_polygon=self.calibration.support_polygon,
            permission_source='EXPLICIT_SOFTWARE_FIXTURE')
        self.targets=(TargetClass(.5,.5,.5,qualified=True),)
        self.needed=np.array([[False,True]],dtype=np.bool_)
        self.graph=SimpleNamespace(status='UNKNOWN',strips=(),reasons=(),work={})
        self.road=SimpleNamespace(status='UNKNOWN',hypotheses=(),reasons=(),semantic_scored=0)

    def observation(self,sequence=1):
        at=sequence*100_000
        def stamp(stream):return FrameStamp('synthetic',stream,sequence,'sim-clock',(at,at+40_000),at+45_000)
        return PerceptionInput('SYNTHETIC',self.calibration.sha256,
            ImageFrame(stamp('rgb'),3,3,'BGR8',bytes(27)),
            ImageFrame(stamp('right'),3,3,'RAW16_LE',bytes(18)))

    def pose(self,sequence=1):
        at=sequence*100_000
        sample=PoseSample('odom','sim-clock',self.calibration.sha256,at+20_000,
            0.,0.,0.,.00001,.00001,'SYNTHETIC_ODOMETRY')
        return PoseEstimate(sample,(at,at+40_000),(at-10_000,at+50_000))

    def process(self,core,observation,**kwargs):
        def depth(_array,_camera,_plane,*,stamp,**_):
            rays=tuple(replace(ray,source_key=stamp.key) for ray in depth_fixture.witness_rays())
            return DepthEvidence(stamp,self.calibration.sha256,'SYNTHETIC',(),rays,9,9,0,0,0,0,True,'FLOOR')
        with patch('xenova_vehicle.perception.depth.analyze_depth',side_effect=depth), \
                patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=self.graph), \
                patch('xenova_vehicle.perception.pipeline.infer_road',return_value=self.road):
            return core.process(observation,**kwargs)

    def test_actual_motion_certificate_targets_needed_cells_first_and_keeps_original_age(self):
        core=PerceptionPipeline(self.calibration,self.profile,targets=self.targets)
        original=self.observation()
        value=self.process(core,original,now_us=150_000,completed_clock=lambda:160_000,
            pose=self.pose(),motion_qualified=True,needed_mask=self.needed,
            cycle_bound_us=10_000,completion_bound_us=20_000)
        self.assertEqual(value.summary['free_temporal_status'],'FULL_CAPTURE_MOTION_RAY_TUBE')
        self.assertEqual(value.summary['free_status'],'QUALIFIED_CELL_ATTEMPTS')
        self.assertEqual(value.summary['free_certificates'][0]['cell'],[1,0])
        self.assertEqual(value.summary['free_certificates'][0]['status'],'CLEAR_REGION')
        self.assertEqual(value.summary['free_cells_applied'],1)
        self.assertEqual(value.map_snapshot['arrays']['state'][0,1],FREE)
        self.assertEqual(int(core.grid.observed_us[0,1]),100_000)
        self.assertEqual(int(value.map_snapshot['arrays']['age_ms'][0,1]),60)
        self.assertIs(value.depth.stamp,original.depth.stamp)
        self.assertEqual(value.depth.patches,depth_fixture.witness_rays())
        self.assertLessEqual(value.summary['free_ray_tests'],self.profile.max_free_ray_tests)
        self.assertTrue(value.summary['free_schedule']['after_all_needed_live_fresh'])
        self.assertFalse(value.summary['free_schedule']['renewal_proven'])
        self.assertFalse(value.summary['world_valid'] or value.summary['live_authority'])
        second=self.process(core,self.observation(2),now_us=250_000,completed_clock=lambda:260_000,
            pose=self.pose(2),motion_qualified=True,needed_mask=~self.needed,
            cycle_bound_us=10_000,completion_bound_us=20_000)
        self.assertEqual(second.summary['free_certificates'][0]['cell'],[0,0])
        self.assertEqual(int(core.grid.observed_us[0,0]),200_000)
        self.assertEqual(int(core.grid.observed_us[0,1]),100_000)

    def test_missing_motion_full_pose_needed_or_timing_never_uses_cell_padding_as_free(self):
        base=dict(now_us=150_000,completed_clock=lambda:160_000,pose=self.pose(),motion_qualified=True,
            needed_mask=self.needed,cycle_bound_us=10_000,completion_bound_us=20_000)
        cases=(
            ('MOTION_UNQUALIFIED',dict(motion_qualified=False)),
            ('FULL_CAPTURE_POSE_UNAVAILABLE',dict(pose=None)),
            ('MOTION_RAY_TUBE_UNAVAILABLE',dict(pose=replace(self.pose(),bracket_us=(110_000,150_000)))),
            ('NEEDED_REGION_UNKNOWN',dict(needed_mask=None)),
            ('SCHEDULING_BOUNDS_UNKNOWN',dict(cycle_bound_us=None)),
            ('ACTUAL_COMPLETION_CLOCK_REQUIRED',dict(completed_clock=None)),
        )
        for status,changed in cases:
            with self.subTest(missing=status):
                value=self.process(PerceptionPipeline(self.calibration,self.profile,targets=self.targets),
                    self.observation(),**dict(base,**changed))
                self.assertEqual(value.summary['free_status'],status)
                self.assertEqual(value.summary['free_cells_applied'],0)
                self.assertEqual(value.summary['map_counts']['free'],0)
                self.assertFalse(value.summary['free_certificates'])

    def test_real_completion_after_budget_or_source_expiry_discards_new_free_before_apply(self):
        for completed,bound,status in ((170_001,20_000,'ACTUAL_COMPLETION_BOUND_EXCEEDED'),
                (600_001,1_000_000,'OBSERVATION_EXPIRED_DURING_COMPUTE')):
            with self.subTest(completed=completed):
                core=PerceptionPipeline(self.calibration,self.profile,targets=self.targets)
                value=self.process(core,self.observation(),now_us=150_000,completed_clock=lambda:completed,
                    pose=self.pose(),motion_qualified=True,needed_mask=self.needed,
                    cycle_bound_us=10_000,completion_bound_us=bound)
                self.assertEqual(value.summary['free_certificates'][0]['status'],'CLEAR_REGION')
                self.assertEqual(value.summary['free_cells_applied'],0)
                self.assertEqual(value.summary['map_counts']['free'],0)
                self.assertFalse(core.grid.time_known.any())
                self.assertIn(status,(value.summary['free_schedule']['status'],value.summary['free_temporal_status']))
                self.assertEqual(value.summary['depth_source_key'],('synthetic','right','sim-clock',1))

    def test_exhausted_shared_ray_budget_does_not_permanently_skip_unattempted_needed_cells(self):
        import numpy as np
        core=PerceptionPipeline(self.calibration,replace(self.profile,max_free_cells=2,max_free_ray_tests=1),targets=self.targets)
        attempted=[]
        for sequence in (1,2):
            at=sequence*100_000
            value=self.process(core,self.observation(sequence),now_us=at+50_000,completed_clock=lambda:at+60_000,
                pose=self.pose(sequence),motion_qualified=True,needed_mask=np.ones((1,2),dtype=bool),
                cycle_bound_us=10_000,completion_bound_us=20_000)
            self.assertEqual(value.summary['free_ray_tests'],1)
            self.assertEqual(len(value.summary['free_certificates']),1)
            self.assertEqual(len(value.summary['free_schedule']['cells']),2)
            self.assertEqual(value.summary['free_cells_applied'],0)
            attempted.append(value.summary['free_certificates'][0]['cell'])
        self.assertEqual(attempted,[[0,0],[1,0]])

    def test_actual_registration_narrowing_does_not_replace_raw_full_capture_motion_hull(self):
        import test_alignment_flow as alignment_fixture
        fixture=alignment_fixture.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        core=PerceptionPipeline(fixture.calibration,fixture.profile)
        fixture.process(core,1,fixture.graph(alignment_fixture.CORNER_GT,0.),fixture.pose(1))
        original=fixture.observation(2)
        observation=replace(original,
            rgb=replace(original.rgb,stamp=replace(original.rgb.stamp,captured_us=(180_000,200_000))),
            depth=replace(original.depth,stamp=replace(original.depth.stamp,captured_us=(180_000,200_000))))
        raw=replace(fixture.pose(2,.2,error=.16),captured_us=observation.capture_interval,bracket_us=(170_000,210_000))
        graph=fixture.graph(alignment_fixture.CORNER_GT,.1)
        expanded=[]
        def motion(*args,**kwargs):
            result=widen_motion_rays(*args,**kwargs);expanded.append((args[3],result));return result
        def depth(_array,_camera,_plane,*,stamp,**_):
            rays=tuple(replace(ray,source_key=stamp.key) for ray in depth_fixture.witness_rays())
            return DepthEvidence(stamp,fixture.calibration.sha256,'SYNTHETIC',(),rays,9,9,0,0,0,0,True,'FLOOR')
        with patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph), \
                patch('xenova_vehicle.perception.depth.analyze_depth',side_effect=depth), \
                patch('xenova_vehicle.perception.motion.widen_motion_rays',side_effect=motion):
            result=core.process(observation,pose=raw,motion_qualified=True,completed_clock=lambda:205_000)
        self.assertEqual(result.registration.status,'BOUNDED')
        self.assertLess(result.registration.position_error_m,raw.sample.position_error_m)
        self.assertAlmostEqual(result.summary['mapped_pose'][0],.1,delta=.03)
        self.assertEqual(len(expanded),1)
        self.assertIs(expanded[0][0],raw)
        self.assertLessEqual(expanded[0][1][0].origin_lower[0],-.16)
        self.assertGreaterEqual(expanded[0][1][0].origin_upper[0],.16)

    def test_stopping_region_uses_actual_admitted_vehicle_lease_and_whole_cells_without_capacity_promise(self):
        import numpy as np
        from xenova_drive_world import encode
        import test_sensor_samples as sensor_fixture
        fixture=sensor_fixture.SensorSampleTests(methodName='runTest');fixture.setUp()
        vehicle=fixture.owner.accept_vehicle(encode(fixture.vehicle()),fixture.lease,fixture.now)
        pose=PoseEstimate(vehicle.pose_sample,vehicle.j_interval,vehicle.j_interval)
        spec=GridSpec(64,64,.05,(-1.6,-1.6))
        options=dict(admission=fixture.owner,frame_id='odom:'+pose.sample.epoch,now_us=fixture.now,
            max_speed_mps=.5,min_braking_mps2=1.,reaction_latency_us=100_000,
            cycle_bound_us=50_000,completion_bound_us=20_000,body_error_m=.01)
        region=stopping_region(spec,vehicle,pose,fixture.calibration,**options)
        body=fixture.calibration.body
        age=fixture.now-min(vehicle.j_interval[0],pose.captured_us[0])
        nominal=(math.hypot(max(body.front,body.rear),body.width/2)+.01+pose.sample.position_error_m
            +.5*(age+170_000)/1e6+.5**2/2)
        self.assertGreaterEqual(region.radius_m,nominal)
        self.assertTrue(region.complete and region.valid_at(fixture.now))
        self.assertEqual(region.vehicle_key,vehicle.source_key)
        self.assertEqual(region.source_epochs,(fixture.source['map_epoch'],fixture.source['frame_epoch'],fixture.source['odom_epoch']))
        with self.assertRaises(ValueError):region.mask.setflags(write=True)
        for row in range(spec.height):
            for col in range(spec.width):
                x0,y0,x1,y1=spec.bounds(col,row);a,b,c,d=region.bounds_xy
                if x0<=c and x1>=a and y0<=d and y1>=b:self.assertTrue(region.mask[row,col])
        context=('LIVE',fixture.calibration.sha256,pose.sample.clock_id,options['frame_id'])
        plan=FreeScheduler(spec,context_key=context).plan(context_key=context,needed_mask=region.mask,
            support=np.ones(region.mask.shape,dtype=bool),permission=np.ones(region.mask.shape,dtype=bool),
            state=np.zeros(region.mask.shape,dtype=np.uint8),observed_us=np.zeros(region.mask.shape,dtype=np.uint64),
            time_known=np.zeros(region.mask.shape,dtype=bool),now_us=fixture.now,capture_lower_us=pose.captured_us[0],
            max_cells=8,max_ray_tests=256,cycle_bound_us=50_000,completion_bound_us=20_000)
        self.assertEqual(plan.status,'BUDGET_INSUFFICIENT')
        self.assertFalse(plan.necessary_capacity_ok or plan.renewal_proven)
        small=stopping_region(GridSpec(2,2,.05,(-.05,-.05)),vehicle,pose,fixture.calibration,**options)
        self.assertFalse(small.complete)
        # 실제 physical 입력 소비에서도 원 region의 clipping/pose 결박을 무시하지 않는다.
        cal=fixture.calibration
        def stamp(stream):return FrameStamp('explicit-physical-fixture',stream,1,pose.sample.clock_id,pose.captured_us,fixture.now)
        observation=PerceptionInput('LIVE',cal.sha256,
            ImageFrame(stamp('rgb'),cal.rgb.width,cal.rgb.height,'BGR8',bytes(cal.rgb.width*cal.rgb.height*3)),
            ImageFrame(stamp('right'),cal.right.width,cal.right.height,'RAW16_LE',bytes(cal.right.width*cal.right.height*2)))
        rays=tuple(replace(ray,source_key=observation.depth.stamp.key) for ray in depth_fixture.witness_rays())
        depth=DepthEvidence(observation.depth.stamp,cal.sha256,'LIVE',(),rays,9,9,0,0,0,0,True,'FLOOR')
        for required,current_pose,status in ((small,pose,'NEEDED_REGION_OUTSIDE_GRID'),
                (region,replace(pose,sample=replace(pose.sample,x=.1)),'NEEDED_POSE_CHANGED')):
            profile=PipelineProfile(grid=required.spec,max_free_cells=1,max_free_ray_tests=1,
                permission_polygon=cal.support_polygon,permission_source='EXPLICIT_SOFTWARE_FIXTURE')
            with patch('xenova_vehicle.perception.depth.analyze_depth',return_value=depth), \
                    patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=self.graph), \
                    patch('xenova_vehicle.perception.pipeline.infer_road',return_value=self.road):
                result=PerceptionPipeline(cal,profile,targets=self.targets).process(observation,pose=current_pose,
                    now_us=fixture.now,completed_clock=lambda:fixture.now+1,motion_qualified=True,needed_mask=required)
            self.assertEqual(result.summary['free_schedule']['status'],status)
            self.assertEqual(result.summary['free_cells_applied'],0)
            self.assertFalse(result.summary['free_certificates'])
        with self.assertRaises(ValueError):
            stopping_region(spec,vehicle,pose,fixture.calibration,**dict(options,max_speed_mps=.4))
        with self.assertRaises(ValueError):
            stopping_region(spec,vehicle,replace(pose,sample=replace(pose.sample,epoch='other')),fixture.calibration,**options)
        fixture.owner.accept_vehicle(encode(fixture.vehicle(2,1_110_000,.002)),fixture.lease,fixture.now)
        self.assertFalse(region.valid_at(fixture.now))
