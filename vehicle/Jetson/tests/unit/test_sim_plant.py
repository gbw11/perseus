"""목표와 실제 운동·좌우 wheel·원 dt를 분리하는 개발 plant 회귀다."""

import math
import threading
import unittest

from test_simulation_motion_profile import motion_profile
from xenova_vehicle.simulation.plant import VirtualPlant


PI='10000000-0000-4000-8000-000000000001'
DEVICE='20000000-0000-4000-8000-000000000002'
EPOCH='30000000-0000-4000-8000-000000000003'


class Clock:
    def __init__(self):self.now=3_000_000
    def __call__(self):return self.now
    def advance(self,delta=10_000):self.now+=delta;return self.now


def plant_fixture(pose=(0.,0.,0.)):
    clock=Clock();profile=motion_profile()
    plant=VirtualPlant(profile,initial_pose=pose,epoch=EPOCH,clock=clock)
    plant.bind_sessions(PI,DEVICE)
    return plant,clock


class SimPlantTests(unittest.TestCase):
    def test_motor_and_servo_measurements_lag_target_and_wheels_follow_actual_yaw(self):
        plant,clock=plant_fixture()
        plant.apply_target(.4,.2)
        self.assertEqual(plant.snapshot().velocity_mps,0.)
        self.assertEqual(plant.snapshot().steering_rad,0.)
        state=plant.step(clock.advance())
        self.assertAlmostEqual(state.velocity_mps,.02)
        self.assertAlmostEqual(state.steering_rad,.05)
        self.assertLess(state.velocity_mps,state.target_velocity_mps)
        self.assertLess(state.steering_rad,state.target_steering_rad)
        sample,_=plant.latch()
        self.assertEqual(sample.quality,5);self.assertGreater(sample.steering_error_mrad,0)
        for _ in range(20):plant.step(clock.advance())
        sample,state=plant.latch()
        self.assertGreater(sample.right_ticks,sample.left_ticks)
        self.assertGreater(sample.left_ticks,0)
        self.assertFalse(state.physical_qualified)
        plant.apply_target(.4,.349)
        self.assertAlmostEqual(plant.snapshot().target_steering_rad,math.atan(.2/.55))

    def test_wrap_before_feedback_would_be_wrong_but_actual_unwrapped_increment_is_preserved(self):
        plant,clock=plant_fixture((0.,0.,math.pi-.001))
        plant.apply_target(.4,.3)
        for _ in range(20):plant.step(clock.advance())
        sample,state=plant.latch()
        self.assertLess(state.pose[2],0.)
        self.assertGreater(sample.right_ticks,sample.left_ticks)
        self.assertGreater(sample.left_ticks,0)
        self.assertLess(sample.right_ticks-sample.left_ticks,2000)

    def test_stop_setpoint_does_not_claim_instant_stationary_and_braking_keeps_actual_ticks(self):
        plant,clock=plant_fixture()
        plant.apply_target(.4,0.)
        for _ in range(25):plant.step(clock.advance())
        before,_=plant.latch()
        plant.stop()
        self.assertGreater(plant.snapshot().velocity_mps,0.)
        self.assertEqual(plant.snapshot().target_velocity_mps,0.)
        for _ in range(20):plant.step(clock.advance())
        after,state=plant.latch()
        self.assertEqual(state.velocity_mps,0.)
        self.assertGreater(after.left_ticks,before.left_ticks)
        self.assertGreater(after.sample_seq,before.sample_seq)
        self.assertEqual(after.source_us,clock.now)

    def test_invalid_dt_is_sticky_and_does_not_reset_pose_or_encoder(self):
        for delta in (0,-1,100_001):
            with self.subTest(delta=delta):
                plant,clock=plant_fixture();plant.apply_target(.3,.1)
                plant.step(clock.advance());sample,state=plant.latch()
                ticks=plant.feedback.ticks
                with self.assertRaisesRegex(ValueError,'dt'):plant.step(clock.now+delta)
                failed=plant.snapshot()
                self.assertFalse(failed.valid);self.assertEqual(failed.target_velocity_mps,0.)
                self.assertEqual(failed.pose,state.pose);self.assertEqual(plant.feedback.ticks,ticks)
                with self.assertRaises(ValueError):plant.apply_target(.3,0.)
                with self.assertRaises(ValueError):plant.latch(clock.advance())
                with self.assertRaises(ValueError):plant.renderer_pose()

    def test_renderer_reader_is_allowed_but_foreign_physics_owner_is_rejected(self):
        plant,clock=plant_fixture();plant.step(clock.advance())
        values=[]
        def reader():
            values.append(plant.renderer_pose())
            try:plant.apply_target(.1,0.)
            except RuntimeError:values.append('OWNER_REJECTED')
        thread=threading.Thread(target=reader,daemon=False);thread.start();thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(values,[plant.snapshot().pose,'OWNER_REJECTED'])

    def test_hello_repeat_preserves_original_counter_but_new_session_requires_new_owner(self):
        plant,clock=plant_fixture();producer=plant.feedback
        plant.step(clock.advance());sample,_=plant.latch()
        self.assertIs(plant.bind_sessions(PI,DEVICE),producer)
        self.assertIs(plant.feedback.last_sample,sample)
        ticks=producer.ticks
        with self.assertRaises(ValueError):plant.bind_sessions('40000000-0000-4000-8000-000000000004',DEVICE)
        self.assertFalse(plant.snapshot().valid)
        self.assertEqual(producer.ticks,ticks)
