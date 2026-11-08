"""실제 B08 FD→clock→measured Vehicle/STOP window와 EOF의 격리 통합 회귀다.

시간/오차는 아래 caller fixture의 명시 미실측 개발값이다. 원 기존 mapped-observation
unit을 실제 wire 양성으로 재태그하지 않으며 물리/Authority/FREE 수락도 하지 않는다.
"""

import json
import os
import unittest
from uuid import uuid4

from test_simulation_motion_profile import motion_profile


def declared_rig():
    from sim_fd_motion_rig import SimFdMotionRig
    from xenova_gateway.feedback3 import Feedback3Profile
    from xenova_gateway.scene_authority import StationaryPolicy
    from xenova_gateway.frame import StopFrame
    from xenova_vehicle.simulation.steering_sensor import VirtualSteeringSensorProfile
    leaf=VirtualSteeringSensorProfile(sensor_id='isolated-development-servo',quantization_rad=.001,
        angle_error_rad=.0005,timestamp_error_us=100,max_rate_rps=5.)
    motion=motion_profile(steering_sensor_profile=leaf,maximum_clock_error_us=20_000,
        timestamp_error_us=100,drift_ppm=100,max_offset_width_us=100_000)
    stationary=StationaryPolicy(window_us=60_000,max_gap_us=100_000,max_age_us=200_000,
        metres_per_tick=motion.fields['encoder']['metres_per_tick'],max_wheel_mps=.03,max_yaw_rps=.1,
        unobserved_wheel_mps=.001,unobserved_yaw_rps=.001)
    policy=Feedback3Profile(vehicle_config_sha256='a'*64,firmware_sha256='b'*64,
        timestamp_error_us=100,drift_ppm=100,max_offset_width_us=100_000,lease_us=1_000_000,
        probe_timeout_us=200_000,j_cutoff_us=10_000,j_apply_us=100,stationary=stationary,
        phase_budget_us=1_000_000,steering_tolerance_rad=.02,steering_settle_us=60_000)
    host,gateway,device,epoch,vm,execution,app,mission=(str(uuid4()) for _ in range(8))
    witness=dict(vm_id=vm,execution_id=execution,agent_instance_id=app,app_id='autonomous',
        generation=7,gateway_instance=gateway,direction_intent_admitted=True,
        command=dict(generation=7,producer_role='AUTO',command_kind='STOP',calculation_sequence='1',
            plan_revision='1',phase_revision='1',segment_index=0,cusp_index=0,mission_id=mission,
            mission_revision='1',grant_revision='1',driver_session='00000000-0000-0000-0000-000000000000',
            driver_action_seq='0',driver_state_seq='0',next_direction=0))
    return SimFdMotionRig(motion,policy,host_boot=host,gateway_instance=gateway,device_session=device,
        plant_epoch=epoch,initial_pose=(2.1,-.4,1.2),stop_frame=StopFrame(7,0,1,witness=witness),
        command_timeout_s=.5,startup_timeout_s=5.,poll_interval_s=.003,minimum_stationary_samples=3,
        peer_close_timeout_s=1.,owner_cleanup_timeout_s=.5)


@unittest.skipUnless(os.environ.get('XENOVA_TEST_PAIRED_GATEWAY') == '1',
    'NOT_SELECTED: explicit coherent Gateway/common requires XENOVA_TEST_PAIRED_GATEWAY=1')
class SimFdMotionTests(unittest.TestCase):
    def setUp(self):
        if os.name!='posix':self.skipTest('actual POSIX development-FD contract')

    def test_real_gateway_clock_ack_stationary_vehicle_codec_and_measurement_source(self):
        from xenova_drive_world import schema,decode
        rig=declared_rig()
        try:
            observation=rig.wait_ready()
            vehicle=decode(observation.vehicle_bytes,schema.VehicleSample)
            self.assertEqual((vehicle.host_boot,vehicle.gateway_instance,vehicle.pi_session,vehicle.device_session),
                (rig.host,rig.gateway,observation.pi_session,rig.device_session))
            self.assertEqual(vehicle.odom_epoch,observation.odom_epoch)
            self.assertEqual(vehicle.calibration,rig.profile.sha256)
            self.assertEqual(vehicle.quality,3)
            self.assertTrue(vehicle.HasField('measured_steering_rad'));self.assertGreater(vehicle.steering_error_rad,0.)
            self.assertEqual((vehicle.odom_pose.x,vehicle.odom_pose.y,vehicle.odom_pose.yaw),(0.,0.,0.))
            for actual,expected in zip(rig.plant.renderer_pose(),(2.1,-.4,1.2)):
                self.assertAlmostEqual(actual,expected,places=12)
            self.assertGreaterEqual(len(observation.vehicle_window),3)
            self.assertTrue(observation.clock_ack_verified_by_gateway)
            self.assertLess(observation.observed_pi_us,observation.clock_valid_until_pi_us)
            install=rig.f3.INSTALL3.unpack(observation.install_payload)
            self.assertEqual(install[3],observation.clock_sequence)
            kind,raw=rig.fb.unpack(observation.peer_produced_ack_wire)
            self.assertEqual(kind,rig.f3.CLOCK_ACK)
            ack=rig.f3.CLOCK_ACK3.unpack(raw)
            self.assertEqual(ack[:5],install[:5])
            self.assertEqual(ack[5:7],(1,0));self.assertEqual(ack[-1],install[-1])
            kind,raw=rig.fb.unpack(observation.sensor_wire)
            sensor=rig.f3.SENSOR3.unpack(raw)
            self.assertEqual((sensor[2],sensor[3],sensor[6]),(vehicle.source_sample,vehicle.source_us,3))
            provenance=json.loads(observation.steering_provenance)
            self.assertEqual(provenance['wheel']['original_quality'],5)
            self.assertEqual(provenance['measurement']['source_kind'],'SIMULATED_MEASURED_STEERING')
            self.assertEqual(int(provenance['measurement']['source_sample']),vehicle.source_sample)
            self.assertFalse(observation.fields['authority']);self.assertFalse(observation.fields['free_positive'])
            self.assertTrue(rig.diagnostics()['caller_endpoints_closed'])
            self.assertIsNone(rig.owner.first_failure);self.assertIsNone(rig.peer.failure)
        finally:rig.close()
        self.assertFalse(rig.diagnostics()['owner_thread_alive'])
        self.assertFalse(rig.peer.snapshot()['thread_alive'])
        self.assertEqual(rig.cleanup_warnings,[])

    def test_actual_peer_eof_invalidates_same_gateway_view_and_does_not_reissue_session(self):
        rig=declared_rig()
        try:
            original=rig.wait_ready()
            outcome=rig.force_peer_eof(timeout_s=1.)
            self.assertEqual(outcome['fault'],'DEVICE_LINK_UNAVAILABLE')
            self.assertFalse(outcome['ready']);self.assertIsNone(outcome['vehicle_sample'])
            self.assertIsNone(outcome['physical_stop']);self.assertTrue(outcome['read_fd_detached'])
            self.assertIsNone(rig.current_vehicle())
            view=rig.owner.device_view()
            self.assertEqual(view['pi_session'],original.pi_session)
            self.assertEqual(view['device_session'],original.device_session)
            self.assertEqual(view['clock_epoch'],original.clock_epoch)
            from xenova_drive_world import decode,schema
            self.assertEqual(decode(original.vehicle_bytes,schema.VehicleSample).odom_epoch,original.odom_epoch)
            self.assertTrue(rig.peer.snapshot()['simulation_stopped'])
        finally:rig.close()
        self.assertFalse(rig.diagnostics()['owner_thread_alive'])
        self.assertFalse(rig.peer.snapshot()['thread_alive'])
