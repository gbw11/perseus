"""개발 Vehicle wire→원 clock/admission→full-capture motion 경로를 검사한다.

기본 선택은 명시 software Vehicle/returned-trust leaf를 실제 공용 codec과 소비자에 넣는다.
마지막 클래스만 전체 B08 Gateway/common을 주입하며 Device↔Pi mapped interval은 별도
software 입력이다. 실제 feedback3 install/ACK·카메라·실물 FREE·주행 수락 증거가 아니다.
"""

from base64 import b64encode
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib
import unittest
from uuid import UUID

from raw_binding_fixtures import binding_fields
from test_simulation_motion_profile import motion_profile
from xenova_drive_world import encode, decode, schema
from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.runtime.mapper_sensor_state import MapperSensorState
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.simulation.feedback import RearWheelFeedback
from xenova_vehicle.simulation.motion import DevelopmentMotion
from xenova_vehicle.storage.contracts import StorageContext, canonical
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin
from xenova_vehicle.transport.sensor_samples import SensorContext, SampleRejected


def identifier(number):return str(UUID(int=number))


class MotionFixture:
    """정지 GT가 아닌 명시 software mapped Vehicle/clock provenance 입력이다."""
    def __init__(self,*,profile=None,odom_epoch=None):
        self.profile=motion_profile() if profile is None else profile
        self.now=1_050_000
        self.source=dict(vehicle='rc-sim-01',device='sim-nn',mapper_boot=identifier(10),
            mapper_instance=identifier(11),clock_epoch=identifier(12),map_epoch=identifier(13),
            frame_epoch=identifier(14),odom_epoch=identifier(15) if odom_epoch is None else odom_epoch,
            configuration='course-sim-1',calibration=self.profile.sha256,primitive='b'*64)
        self.origin=dict(pi_host_boot=identifier(16),gateway_instance=identifier(17),
            pi_session=identifier(18),device_session=identifier(19))
        self.mapper_worker=identifier(20)
        fields=binding_fields();fields.update(clock_epoch=self.source['clock_epoch'],
            calibration_bundle=self.profile.bound_calibration.sha256)
        self.raw=RawBinding(fields)
        self.sensor_context=SensorContext.for_development(self.profile,self.source,**self.origin)
        self.policy=self.profile.clock_policy(device=self.source['device'],primitive=self.source['primitive'])
        self.binding=dict(service_instance=self.raw.fields['service_instance'],mapper_worker=self.mapper_worker,
            mapper_instance=self.source['mapper_instance'],raw_binding_sha256=self.raw.sha256,
            **{key:self.source[key] for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')},
            scene_admission_ref=None,source_binding_revision='1')
        geometry=PiGeometryOrigin(vehicle=self.source['vehicle'],**self.origin,
            odom_epoch=self.source['odom_epoch'],calibration=self.source['calibration'])
        self.context=StorageContext(identifier(21),self.mapper_worker,self.source['mapper_instance'],
            self.source['map_epoch'],self.source['frame_epoch'],self.source['odom_epoch'],self.origin['pi_host_boot'],
            self.source['configuration'],self.source['calibration'],self.source['primitive'],canonical(self.binding),
            geometry_origin=geometry.fields)
        trust=schema.WorldTrust(**{key:value for key,value in self.source.items() if key!='device'},
            lower_offset_us=99990,upper_offset_us=100010,expires_pi_us=2_000_000,max_error_us=100)
        probe=schema.MapperClockProbe(major=1,vehicle=self.source['vehicle'],device=self.source['device'],
            pi_host_boot=self.origin['pi_host_boot'],pi_client_instance=identifier(22),probe_seq=4,nonce=b'n'*16,
            pi_send_us=1_100_000,admission_revision=7,expected_mapper_boot=self.source['mapper_boot'],
            expected_mapper_instance=self.source['mapper_instance'],expected_clock_epoch=self.source['clock_epoch'])
        probe.mapper_trust.CopyFrom(trust)
        self.probe=encode(probe)
        self.vehicles=(self.vehicle(1,1_090_000,0.),self.vehicle(2,1_110_000,.002))

    def vehicle(self,seq,pi_us,x):
        value=schema.VehicleSample(major=1,vehicle=self.source['vehicle'],host_boot=self.origin['pi_host_boot'],
            gateway_instance=self.origin['gateway_instance'],pi_session=self.origin['pi_session'],
            device_session=self.origin['device_session'],odom_epoch=self.source['odom_epoch'],sequence=seq,
            source_sample=seq+10,source_us=370000+seq*20000,pi_sample_us=pi_us,clock_error_us=10,
            left_ticks=seq*13,right_ticks=seq*13,velocity_mps=.1,position_error_m=.0001,
            heading_error_rad=.0001,quality=3,calibration=self.profile.sha256)
        value.odom_pose.CopyFrom(schema.Pose(x=x,y=0.,yaw=0.))
        return encode(value)

    def observation(self):
        source='sim-wall:'+self.raw.fields['capture_epoch'];clock=self.sensor_context.pose_clock_id
        rgb=FrameStamp(source,'rgb_video',7,clock,(999_800,1_000_100),1_020_000)
        depth=FrameStamp(source,'depth_right',7,clock,(999_900,1_000_200),1_020_000)
        return PerceptionInput('SYNTHETIC',self.profile.bound_calibration.sha256,
            ImageFrame(rgb,640,360,'NV12',bytes(640*360*3//2),0),
            ImageFrame(depth,640,480,'RAW16_LE',bytes(640*480*2),0))

    def snapshot(self,*,probe=None,vehicles=None):
        probe=self.probe if probe is None else probe;key=sha256(probe).hexdigest()
        def row(raw,at):return dict(raw=b64encode(raw).decode('ascii'),received_j_us=str(at),clock_probe_sha256=key)
        return dict(version=1,publication=deepcopy(self.binding),source=dict(self.source),origin=dict(self.origin),
            storage_context=self.context.fields,context_sha256=self.context.sha256,active_probe_sha256=key,
            probes={key:row(probe,1_010_000)},vehicles=[row(raw,1_015_000+i)
                for i,raw in enumerate(self.vehicles if vehicles is None else vehicles)],tof={},scene=None)

    def owner(self):
        return DevelopmentMotion(self.profile,self.raw,self.policy,sensor_context=self.sensor_context,
            mapper_worker=self.mapper_worker,mapper_instance=self.source['mapper_instance'])

    def read(self,owner,*,snapshot=None,observation=None,now=None,expires=1_499_800):
        return owner.read(canonical(self.snapshot() if snapshot is None else snapshot),
            self.observation() if observation is None else observation,
            now_us=self.now if now is None else now,expires_j_us=expires)


class DevelopmentMotionTests(unittest.TestCase):
    def test_default_production_rejection_and_explicit_development_binding_are_separate(self):
        f=MotionFixture()
        with self.assertRaises(SampleRejected):SensorContext(f.profile.bound_calibration,f.source,**f.origin)
        with self.assertRaises(SampleRejected):SensorContext.for_development(f.profile.neutral_calibration,f.source,**f.origin)
        self.assertEqual(f.sensor_context.domain,'DEVELOPMENT_SYNTHETIC')
        self.assertEqual(f.sensor_context.pose_clock_id,'jetson-monotonic:'+f.source['clock_epoch'])
        args=f.profile.sensor_arguments;pose=f.profile.pose_arguments
        production=MapperSensorState(f.profile.bound_calibration,f.raw,f.policy,**args,
            max_gap_us=pose['max_gap_us'],capacity=pose['capacity'],mapper_worker=f.mapper_worker,
            mapper_instance=f.source['mapper_instance'])
        stamp=replace(f.observation().rgb.stamp,captured_us=f.observation().capture_interval)
        with self.assertRaises(SampleRejected):production.read(canonical(f.snapshot()),stamp,now_us=f.now)

    def test_full_capture_uses_original_wire_admissions_and_nonoracle_motion_tube(self):
        f=MotionFixture();owner=f.owner();observation=f.observation();result=f.read(owner,observation=observation)
        self.assertIsNotNone(result.pose);self.assertEqual(result.pose.captured_us,(999_800,1_000_200))
        self.assertEqual(result.pose.bracket_us,(990_000,1_010_000))
        self.assertEqual(result.pose.sample.origin,'SYNTHETIC_ODOMETRY')
        self.assertEqual(result.pose.sample.clock_id,observation.rgb.stamp.clock_id)
        self.assertEqual(result.pose.sample.calibration_id,f.profile.bound_calibration.sha256)
        self.assertEqual(result.reasons,());self.assertEqual(result.tof,())
        bracket=owner.original_pose_bracket(result.pose)
        self.assertEqual(tuple(row.evidence.wire for row in bracket.originals),f.vehicles)
        self.assertEqual(tuple(row.received_j_us for row in bracket.originals),(1_015_000,1_015_001))
        self.assertIs(owner.validate_vehicle_admission(bracket.originals[1]),result.vehicle)
        self.assertEqual(result.vehicle.anchor.calibration_bundle_sha256,f.profile.sha256)
        self.assertNotEqual(result.vehicle.anchor.calibration_bundle_sha256,observation.calibration_id)
        self.assertGreater(result.pose.sample.position_error_m,.0001)
        from xenova_vehicle.perception.free_space import RayBundle
        from xenova_vehicle.perception.motion import widen_motion_rays
        ray=RayBundle((.2,-.0375,.12),(.2,-.0375,.12),(.6,0.,-.8),(.6,0.,-.8),
            .2,1.,.01,3.,True,observation.depth.stamp.key,(1.,1.))
        widened=widen_motion_rays((ray,),observation.depth.stamp,f.profile.bound_calibration,result.pose,
            input_kind='SYNTHETIC',motion_qualified=True,capture_interval=observation.capture_interval)
        self.assertTrue(bool(widened.qualified[0]))
        self.assertLess(widened[0].origin_lower[0],ray.origin_lower[0])
        self.assertGreater(widened[0].origin_upper[0],ray.origin_upper[0])

    def test_duplicate_and_compatible_clock_renewal_preserve_original_buffer_interval_and_hwm(self):
        f=MotionFixture();owner=f.owner();first=f.read(owner);buffer=owner.sensor_state.poses
        old=first.vehicle;original=owner.original_vehicle_admission(old)
        probe=decode(f.probe,schema.MapperClockProbe);probe.probe_seq+=1;probe.pi_send_us=1_170_000
        probe.mapper_trust.expires_pi_us=2_050_000
        renewed=f.snapshot();new_raw=encode(probe);key=sha256(new_raw).hexdigest()
        renewed['probes'][key]=dict(raw=b64encode(new_raw).decode('ascii'),received_j_us='1075000',clock_probe_sha256=key)
        renewed['active_probe_sha256']=key
        result=f.read(owner,snapshot=renewed,now=1_080_000)
        self.assertIs(owner.sensor_state.poses,buffer)
        self.assertIs(result.vehicle,old)
        self.assertEqual(result.vehicle.j_interval,old.j_interval)
        self.assertIs(owner.original_vehicle_admission(old),original)
        self.assertEqual(result.delivery_metadata,())
        self.assertEqual(len(buffer._samples),2)

    def test_absent_or_expired_clock_old_vehicle_and_unbracketed_capture_never_make_pose(self):
        f=MotionFixture();probe=decode(f.probe,schema.MapperClockProbe);probe.ClearField('mapper_trust')
        no_clock=f.read(f.owner(),snapshot=f.snapshot(probe=encode(probe)))
        self.assertIsNone(no_clock.pose);self.assertIn('PI_J_CLOCK_UNAVAILABLE',no_clock.reasons)
        expired_probe=decode(f.probe,schema.MapperClockProbe);expired_probe.mapper_trust.expires_pi_us=1_150_000
        expired=f.read(f.owner(),snapshot=f.snapshot(probe=encode(expired_probe)))
        self.assertIsNone(expired.pose)
        old=f.read(f.owner(),now=1_300_000)
        self.assertIsNone(old.pose)
        only_one=f.read(f.owner(),snapshot=f.snapshot(vehicles=f.vehicles[:1]))
        self.assertIsNone(only_one.pose);self.assertIn('FULL_CAPTURE_POSE_UNAVAILABLE',only_one.reasons)
        with self.assertRaises(ValueError):f.read(f.owner(),now=1_499_800)
        with self.assertRaises(ValueError):f.read(f.owner(),expires=1_499_801)

    def test_changed_source_raw_origin_clock_or_neutral_observation_is_rejected(self):
        f=MotionFixture();original=f.observation()
        for observation in (replace(original,calibration_id=f.profile.neutral_calibration.sha256),
                replace(original,kind='REPLAY'),replace(original,rgb=replace(original.rgb,
                    stamp=replace(original.rgb.stamp,source_id='sim-wall:'+identifier(90))),depth=None),
                replace(original,rgb=replace(original.rgb,stamp=replace(original.rgb.stamp,
                    clock_id=f.source['clock_epoch'])),depth=None)):
            with self.subTest(kind=observation.kind),self.assertRaises(ValueError):f.read(f.owner(),observation=observation)
        for change in ('origin','source','raw'):
            snapshot=f.snapshot()
            if change=='origin':snapshot['origin']['pi_session']=identifier(91)
            elif change=='source':snapshot['source']['mapper_instance']=identifier(92)
            else:snapshot['publication']['raw_binding_sha256']='c'*64
            with self.subTest(change=change),self.assertRaises(ValueError):f.read(f.owner(),snapshot=snapshot)
        zero=replace(original,rgb=replace(original.rgb,stamp=replace(original.rgb.stamp,captured_us=(1_000_000,1_000_000))),depth=None)
        with self.assertRaises(ValueError):f.read(f.owner(),observation=zero)

    def test_contradictory_odometry_quarantines_pose_without_inventing_stationary_motion(self):
        f=MotionFixture();owner=f.owner();bad=f.snapshot(vehicles=(f.vehicles[0],f.vehicle(2,1_110_000,10.)))
        result=f.read(owner,snapshot=bad)
        self.assertIsNone(result.pose);self.assertIsNone(result.vehicle)
        self.assertIn('ODOMETRY_MOTION_CONTRADICTION',result.reasons)
        again=f.read(owner)
        self.assertIsNone(again.pose);self.assertIsNone(again.vehicle)


class PairedGatewayMotionTests(unittest.TestCase):
    """전체 coherent B08 Gateway/common 선택 전용. 실제 Device clock ACK는 별도다."""
    def test_actual_wheel_codec_and_gateway_odometry_feed_original_vehicle_and_capture_admission(self):
        odometry_module=importlib.import_module('xenova_gateway.odometry')
        feedback=importlib.import_module('xenova_gateway.feedback3')
        framing=importlib.import_module('xenova_gateway.feedback')
        common=importlib.import_module('xenova_drive_world')
        profile=motion_profile()
        odom=odometry_module.VehicleOdometry(odometry_module.OdometryConfig(**profile.odometry_arguments),identifier(16))
        odom.device_session(identifier(19))
        f=MotionFixture(profile=profile,odom_epoch=odom.epoch);owner=f.owner();encoder=profile.fields['encoder']
        wheels=RearWheelFeedback(metres_per_tick=encoder['metres_per_tick'],track_width_m=encoder['track_width_m'],
            encoder_bits=encoder['encoder_bits'],steering_error_rad=encoder['steering_error_rad'],
            pi_session=f.origin['pi_session'],device_session=f.origin['device_session'])
        rows=[]
        for index,stamp in enumerate((370_000,390_000,410_000)):
            wheels.advance_motion(0. if index==0 else .002,0.,0.)
            sample=wheels.latch(stamp)
            wire=wheels.sensor_wire(feedback,sample,clock_epoch=identifier(80),clock_sequence=1)
            _,payload=framing.unpack(wire)
            pi,device,seq,source_us,left,right,quality,steer,error,_,_=feedback.SENSOR3.unpack(payload)
            # 명시 software Device→Pi 구간이다. 실제 install/ACK 통과로 세지 않는다.
            odom.observe_mapped(str(UUID(bytes=pi)),str(UUID(bytes=device)),source_us,seq,left,right,
                quality,steer,error,stamp+699990,stamp+700010)
            raw=owner.vehicle_from_gateway(odom,odometry_module=odometry_module,common=common,now_pi_us=stamp+700020)
            if raw is not None:rows.append(raw)
        self.assertEqual(len(rows),2)
        result=f.read(owner,snapshot=f.snapshot(vehicles=tuple(rows)))
        self.assertIsNotNone(result.pose)
        bracket=owner.original_pose_bracket(result.pose)
        self.assertEqual(tuple(row.evidence.wire for row in bracket.originals),tuple(rows))
        self.assertEqual(result.pose.sample.origin,'SYNTHETIC_ODOMETRY')
        self.assertEqual(result.pose.captured_us,f.observation().capture_interval)
        self.assertEqual(result.vehicle.anchor.odom_epoch,odom.epoch)
        self.assertGreater(result.pose.sample.x,0.)
        self.assertLess(result.pose.sample.x,.005)

    def test_gateway_port_preserves_origin_and_does_not_renew_expired_latest(self):
        odometry_module=importlib.import_module('xenova_gateway.odometry')
        common=importlib.import_module('xenova_drive_world');profile=motion_profile()
        odom=odometry_module.VehicleOdometry(odometry_module.OdometryConfig(**profile.odometry_arguments),identifier(16))
        odom.device_session(identifier(19));f=MotionFixture(profile=profile,odom_epoch=odom.epoch);owner=f.owner()
        self.assertIsNone(owner.vehicle_from_gateway(odom,odometry_module=odometry_module,common=common,now_pi_us=1_100_000))
        for seq,source_us,pi_us,ticks in ((1,390000,1090000,0),(2,410000,1110000,13)):
            odom.observe_mapped(f.origin['pi_session'],f.origin['device_session'],source_us,seq,ticks,ticks,5,0,1,pi_us-10,pi_us+10)
        original=owner.vehicle_from_gateway(odom,odometry_module=odometry_module,common=common,now_pi_us=1_110_020)
        self.assertEqual(decode(original,schema.VehicleSample).pi_sample_us,1_110_000)
        self.assertIsNone(owner.vehicle_from_gateway(odom,odometry_module=odometry_module,common=common,now_pi_us=1_400_000))
        odom.device_session(identifier(91))
        with self.assertRaises(ValueError):owner.vehicle_from_gateway(odom,odometry_module=odometry_module,common=common,now_pi_us=1_400_000)


if __name__=='__main__':unittest.main()
