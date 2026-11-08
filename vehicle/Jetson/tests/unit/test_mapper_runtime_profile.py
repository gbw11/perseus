"""실제 mapper 설정 producer/consumer와 후보 채택 경계의 연결 시험.

원 source software fixture와 zero NV12/RIGHT bytes를 실제 SensorState/인지 코어로
보낸다. LIVE 표시는 fixture의 명시 SOFTWARE_TEST_ONLY profile이며 물리 보정/시계
승인이 아니다. 카메라·spawn·MQTT·service는 열지 않고 그 제어 입구만 모조한다.
"""

from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_mapper_sensor_state as sensor_fixtures
import test_mapper_profile as profile_fixtures
from xenova_drive_world import encode
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.calibration import canonical
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.runtime import resident_runtime, resident_workers
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_profile import MapperProfile
from xenova_vehicle.runtime.raw_binding import RawBindingProfile
from xenova_vehicle.runtime.resident_profile import runtime_artifacts
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.source_identity import SourcePolicy
from xenova_vehicle.yolop_profile import YolopBenchProfile
from xenova_vehicle.mapping.free_schedule import stopping_region


def uid(number): return '00000000-0000-4000-8000-'+f'{number:012x}'
def checkpoint(*_): return None


class MapperRuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        # import한 모듈의 suite를 실행하지 않고 실제 wire/clock 값 builder만 재사용한다.
        self.fixture=sensor_fixtures.MapperSensorStateTests(
            'test_full_capture_uses_original_vehicle_anchor_interval_and_callback_metadata')
        self.fixture.setUp(); f=self.fixture
        self.calibration=f.calibration; self.raw=f.raw; self.now=f.now
        self.calibration_bytes=canonical(self.calibration.to_dict())
        value=profile_fixtures.profile_fields(self.calibration)
        value['vehicle_configuration']=f.fixture.source['configuration']; value['motion_qualified']=True
        value['sensors'].update(vehicle_max_age_us=200000,tof_max_age_us=200000,max_gap_us=100000,capacity=16)
        value['sensors']['tof_sources']=[dict(external_leaf_name=name,leaf=leaf.to_dict()) for name,leaf in f.leaves.values()]
        # 원 크기640x360/640x480의 실제 decoder를 유지하고 빈 영상의 birdview만 작게 한다.
        value['pipeline']['grid'].update(width=4,height=4,resolution_m=.1,origin_xy=(-.2,-.2))
        value['pipeline']['tape'].update(x_min_m=.15,x_max_m=.25,y_min_m=-.05,y_max_m=.05,cell_m=.05)
        value['pipeline']['depth'].update(max_patches=0,max_hit_boxes=0)
        self.fields=value; self.config=MapperProfile(value)
        src=f.fixture.source
        self.source_policy=SourcePolicy(src['vehicle'],src['configuration'],src['calibration'],src['primitive'],self.calibration.sha256)
        self.yolop=YolopBenchProfile('FAKE',Path('assets'),Path('manifest.json'))
        _,self.sensor_owner=resident_workers.mapper_sensor_owner(SimpleNamespace(
            mapper_profile_bytes=self.config.canonical_bytes,perception_profile_bytes=self.calibration_bytes,
            source_policy=self.source_policy,clock_policy=f.policy,
            peer=SimpleNamespace(worker_instance=f.mapper_worker),mapper_instance=f.mapper_instance),self.raw)

    def processor(self, *, config=True, source_policy=None):
        return ResidentMapperProcessor(self.calibration_bytes,self.raw,clock=lambda:self.now,
            mapper_profile_bytes=self.config.canonical_bytes if config else None,
            source_policy=self.source_policy if source_policy is None else source_policy,
            clock_policy=self.fixture.policy,mapper_worker=self.fixture.mapper_worker,mapper_instance=self.fixture.mapper_instance)

    def item(self, number=1, *, snapshot=None, capture_timing='PROFILE'):
        shift=(number-1)*2000
        rgb=RawFrame('rgb_video',number,800050+shift,900050+shift,self.now,640,360,'NV12',
            bytes(640*360)+bytes([128])*(640*360//2),100)
        depth=RawFrame('depth_right',number+10,800100+shift,900100+shift,self.now,640,480,'RAW16_LE',bytes(640*480*2),None)
        clocks={name:dict(bridge_sample=dict(j_before_us=str(self.now),sdk_us=str(self.now-100000),j_after_us=str(self.now)),
            sdk_host_timestamp_j_bracket_us=[str(end),str(end)])
            for name,end in (('rgb_video',1000050+shift),('depth_right',1000100+shift))}
        sample=CaptureSample(rgb,depth,None,None,clocks)
        item=MapperInput(uid(number),self.raw.sha256,sample,self.now+150000,
            context_bytes=canonical(self.fixture.snapshot() if snapshot is None else snapshot))
        self.sensor_owner.ingest(item.context_bytes,now_us=self.now)
        return resident_workers.attach_sensor_snapshot(item,self.sensor_owner,
            self.config.capture_timing if capture_timing=='PROFILE' else capture_timing,now_us=self.now)

    def test_cli_passes_original_mapper_file_to_runtime_without_implicitly_enabling_diagnostics(self):
        from xenova_vehicle import cli
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); mapper=root/'mapper.json'; calibration=root/'calibration.json'
            original=json.dumps(self.fields,indent=2).encode('ascii'); mapper.write_bytes(original); calibration.write_bytes(self.calibration_bytes)
            with patch('xenova_vehicle.yolop_profile.load_yolop_profile',return_value=self.yolop),\
                    patch('xenova_vehicle.transport.jio_profile.JetsonJioProfile.load',return_value=SimpleNamespace(source_policy=self.source_policy)),\
                    patch('xenova_vehicle.runtime.resident_runtime.run_runtime',return_value=dict(exit_code=0)) as run,redirect_stdout(io.StringIO()):
                code=cli.main(['runtime','--profile',str(root/'capture.json'),'--database',str(root/'map.db'),
                    '--state-dir',str(root/'state'),'--output',str(root/'output'),'--jio-profile',str(root/'private-jio.json'),
                    '--perception-profile',str(calibration),'--mapper-profile',str(mapper),'--json'])
            self.assertEqual(code,0); self.assertEqual(run.call_args.kwargs['mapper_profile_bytes'],original)
            self.assertEqual(run.call_args.kwargs['perception_profile_bytes'],self.calibration_bytes)
            self.assertNotIn('diagnostics',run.call_args.kwargs)

    def test_prepare_publishes_canonical_pin_and_checks_actual_vehicle_policy_without_id_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            def runtime(name,policy):
                value=resident_runtime._Runtime.__new__(resident_runtime._Runtime)
                value.output=Path(directory)/name; value.output_owned=False; value.profile=self.yolop
                value.http_address=None
                value.perception_profile_bytes=self.calibration_bytes
                value.mapper_profile_bytes=json.dumps(self.fields,indent=2).encode('ascii')
                value.jio_profile=SimpleNamespace(source_policy=policy)
                return value
            actual=runtime('valid',self.source_policy)._prepare()
            self.assertEqual(actual['manifests']['mapper-profile.json'],self.config.canonical_bytes)
            self.assertEqual(actual['mapper_profile_sha256'],self.config.sha256)
            self.assertEqual(actual['published']['mapper-profile.json']['sha256'],self.config.sha256)
            self.assertNotEqual(actual['mapper_profile_sha256'],self.source_policy.configuration)
            self.assertEqual(actual['calibration_bundle'],self.calibration.sha256)
            self.assertNotEqual(actual['calibration_bundle'],self.source_policy.calibration)
            for index,policy in enumerate((replace(self.source_policy,configuration='wrong-config'),
                    replace(self.source_policy,calibration='f'*64),replace(self.source_policy,calibration_profile_sha256='f'*64))):
                with self.subTest(index=index),patch('xenova_vehicle.runtime.resident_runtime.publish_runtime_artifacts') as publish:
                    with self.assertRaises(ValueError): runtime('bad-'+str(index),policy)._prepare()
                    publish.assert_not_called()

    def test_actor_rejects_mapper_pin_mismatch_before_opening_transport_or_sending_hello(self):
        artifacts=runtime_artifacts(self.yolop,self.calibration_bytes,self.config.canonical_bytes)
        refs=RawBindingProfile('FAKE',self.raw.fields['clock_epoch'],artifacts['model_id'],artifacts['parser_id'],
            artifacts['graph_profile_sha256'],artifacts['format_table_sha256'],artifacts['calibration_bundle'],True,False)
        peer=LifecyclePeer(uid(80),uid(81),uid(82),'M',artifacts['release_sha256'])
        boot=SimpleNamespace(profile=self.yolop,peer=peer,raw_profile=refs,perception_profile_bytes=self.calibration_bytes,
            mapper_profile_bytes=self.config.canonical_bytes,mapper_profile_sha256='f'*64)
        with patch.object(resident_workers,'ResidentLink') as transport:
            with self.assertRaisesRegex(ValueError,'mapper configuration'): resident_workers._Actor(object(),boot)
            transport.assert_not_called()

    def test_profile_none_keeps_actual_old_frame_local_path_and_does_not_sample_diagnostic_clock(self):
        processor=self.processor(config=False); item=self.item()
        with patch('xenova_vehicle.perception.geometry.perf_counter_ns',side_effect=AssertionError('diagnostics auto-enabled')):
            result=json.loads(processor.geometry(item,None,checkpoint))
        self.assertIsNone(processor._config); self.assertFalse(hasattr(processor,'_accepted_sensors'))
        self.assertEqual(processor._accepted.pipeline.targets,())
        self.assertIsNone(result['latest']['captured_us']); self.assertNotIn('mapper_profile_sha256',result['latest'])
        self.assertNotIn('diagnostic_timings',result['latest']); self.assertFalse(result['latest']['world_valid'])
        processor.cancelled(item,'TEST_COMPLETE')

    def test_actual_source_sensor_state_and_shared_core_receive_full_hull_motion_and_stopping_limits(self):
        processor=self.processor(); item=self.item(); actual_process=PerceptionPipeline.process; seen=[]
        def observe(core,observation,**kwargs):
            seen.append((core,observation,kwargs)); return actual_process(core,observation,**kwargs)
        with patch.object(PerceptionPipeline,'process',new=observe),\
                patch('xenova_vehicle.mapping.free_schedule.stopping_region',side_effect=stopping_region) as stop,\
                patch('xenova_vehicle.perception.geometry.perf_counter_ns',side_effect=AssertionError('diagnostics auto-enabled')):
            payload=processor.geometry(item,None,checkpoint)
        core,observation,kwargs=seen[0]; pose=kwargs['pose']
        # END1000050/1000100, RGB 노출100, depth 상한5000, 오차2+10+20의 독립 계산이다.
        self.assertEqual(observation.capture_interval,(995068,1000132))
        self.assertEqual(pose.captured_us,(995068,1000132)); self.assertNotEqual(pose.captured_us,observation.rgb.stamp.captured_us)
        self.assertEqual(pose.bracket_us,(990000,1010000)); self.assertEqual(pose.sample.origin,'ODOMETRY')
        self.assertIs(kwargs['motion_qualified'],False)
        self.assertEqual(core.profile,self.config.pipeline); self.assertEqual(core.targets,self.config.targets)
        self.assertEqual(stop.call_count,1)
        for name in ('max_speed_mps','min_braking_mps2','reaction_latency_us','cycle_bound_us','completion_bound_us','body_error_m'):
            self.assertEqual(stop.call_args.kwargs[name],getattr(self.config.stopping,name))
        self.assertIs(stop.call_args.args[2],pose)
        summary=json.loads(payload)['latest']
        self.assertEqual(summary['mapper_profile_sha256'],self.config.sha256)
        self.assertEqual(summary['source_vehicle_sequence'],2); self.assertEqual(summary['tof_inputs'],2)
        self.assertFalse(summary['localization_configured']);self.assertEqual(summary['free_cells_applied'],0)
        self.assertFalse(summary['world_valid']); self.assertNotIn('diagnostic_timings',summary)
        processor.accepted(item)
        self.assertIs(processor._accepted.pipeline,core); self.assertEqual(self.sensor_owner.sensors.latest_vehicle.anchor.sequence,2)

    def test_explicit_localization_limits_reach_real_core_without_creating_an_initial_anchor(self):
        # 명시 software bound 전달 시험이다. 설치용 drift/validity 수락값이 아니다.
        limits=dict(registration_valid_for_us=500000,position_drift_mps=.01,yaw_drift_rps=.02)
        self.fields['localization']=limits;self.config=MapperProfile(self.fields)
        processor=self.processor();item=self.item();seen=[];actual=PerceptionPipeline.process
        def observe(core,observation,**kwargs):
            seen.append(kwargs);return actual(core,observation,**kwargs)
        with patch.object(PerceptionPipeline,'process',new=observe):
            summary=json.loads(processor.geometry(item,None,checkpoint))['latest']
        self.assertTrue(seen[0]['motion_qualified']);self.assertTrue(summary['localization_configured'])
        self.assertEqual(seen[0]['localization_valid_for_us'],limits['registration_valid_for_us'])
        self.assertEqual(seen[0]['localization_position_drift_mps'],limits['position_drift_mps'])
        self.assertEqual(seen[0]['localization_yaw_drift_rps'],limits['yaw_drift_rps'])
        self.assertFalse(summary['localization_qualified']);self.assertIsNone(summary['alignment_metadata'])
        self.assertEqual(summary['free_cells_applied'],0);self.assertFalse(summary['world_valid'])
        processor.cancelled(item,'TEST_COMPLETE')

    def test_lost_clock_uses_separate_diagnostic_core_and_preserves_accepted_metric_map(self):
        import numpy as np
        processor=self.processor(); first=self.item(); processor.geometry(first,None,checkpoint); processor.accepted(first)
        before=processor._accepted.pipeline; old=before.grid.snapshot(self.now,clock_id=before.grid.clock_id)
        source=self.fixture.snapshot(); source['probes']={}; source['active_probe_sha256']=None
        item=self.item(2,snapshot=source)
        processor.admission_fenced(self.sensor_owner.fence_revision,'SOFTWARE_CLOCK_LOSS')
        summary=json.loads(processor.geometry(item,None,checkpoint))['latest']
        self.assertTrue(summary['preserved_metric_map']); self.assertIn('PI_J_CLOCK_UNAVAILABLE',summary['metric_input_reasons'])
        processor.accepted(item)
        after=processor._accepted.pipeline; current=after.grid.snapshot(self.now,clock_id=after.grid.clock_id)
        self.assertEqual((current['frame_id'],current['revision']),(old['frame_id'],old['revision']))
        self.assertEqual(after._last_rgb_key,before._last_rgb_key)
        for key,value in old['arrays'].items(): np.testing.assert_array_equal(current['arrays'][key],value)
        self.assertIsNot(processor._diagnostic.pipeline,after)
        self.assertNotEqual(processor._diagnostic.pipeline.grid.frame_id,after.grid.frame_id)

    def test_cancelled_geometry_preserves_fresh_main_sensor_highwater_and_accepted_core(self):
        processor=self.processor(); first=self.item(); processor.geometry(first,None,checkpoint); processor.accepted(first)
        accepted,sensors=processor._accepted,self.sensor_owner
        snapshot=self.fixture.snapshot(); third=encode(self.fixture.fixture.vehicle(3,1_120_000,.003))
        snapshot['vehicles'].append(self.fixture.row(third,received=1_025_000))
        item=self.item(2,snapshot=snapshot); processor.geometry(item,None,checkpoint)
        self.assertIs(processor._accepted,accepted); self.assertIs(self.sensor_owner,sensors)
        self.assertEqual(sensors.sensors.latest_vehicle.anchor.sequence,3)
        self.assertEqual(processor._staged[2].sensors.latest_vehicle.anchor.sequence,3)
        processor.cancelled(item,'ORIGINAL_INPUT_EXPIRED')
        self.assertIs(processor._accepted,accepted); self.assertIs(self.sensor_owner,sensors)
        self.assertIsNone(processor._staged); self.assertEqual(sensors.sensors.latest_vehicle.anchor.sequence,3)
        self.assertIn(sha256(third).hexdigest(),sensors._seen_vehicle)

    def test_missing_capture_timing_remains_diagnostic_without_a_fabricated_zero_pose(self):
        value=deepcopy(self.fields); value['capture_timing']=None; config=MapperProfile(value)
        processor=ResidentMapperProcessor(self.calibration_bytes,self.raw,clock=lambda:self.now,
            mapper_profile_bytes=config.canonical_bytes,source_policy=self.source_policy,clock_policy=self.fixture.policy,
            mapper_worker=self.fixture.mapper_worker,mapper_instance=self.fixture.mapper_instance)
        result=json.loads(processor.geometry(self.item(capture_timing=None),None,checkpoint))['latest']
        self.assertIsNone(result['captured_us']); self.assertIsNone(result['mapped_pose'])
        self.assertIn('FULL_CAPTURE_POSE_UNAVAILABLE',result['metric_input_reasons'])
        self.assertIsNone(processor._staged[1].pipeline.grid); self.assertIsNotNone(processor._staged[3].pipeline.grid)
        self.assertFalse(result['world_valid']); self.assertFalse(result['preserved_metric_map'])

    def test_processor_checks_source_policy_local_calibration_pin_as_a_separate_identity(self):
        with self.assertRaises(ValueError):
            self.processor(source_policy=replace(self.source_policy,calibration_profile_sha256='f'*64))
