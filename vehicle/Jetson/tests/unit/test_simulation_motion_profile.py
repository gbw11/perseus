"""미실측 개발 가정을 명시한 local manifest/neutral/bound 관계의 순수 시험이다."""

from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
import unittest

from xenova_vehicle.perception.calibration import canonical
from xenova_vehicle.simulation.motion_profile import DevelopmentMotionProfile
from xenova_vehicle.simulation.profile import DevelopmentProfile
from xenova_vehicle.simulation.scene import Scene, canonical as scene_canonical, demo_scene


FIXTURE=Path(__file__).resolve().parents[2]/'src/xenova_vehicle/simulation/fixtures/experiment-profile.json'


def assumptions():
    # software 시험용 값이다. 실물 수락값이나 제품 기본값으로 사용하지 않는다.
    return dict(encoder_bits=32,metres_per_tick_upper=.00016,maximum_speed_mps=.5,
        maximum_clock_error_us=1000,maximum_sample_age_us=200_000,sample_period_ms=20,
        position_error_m=.0001,heading_error_rad=.0001,distance_error_ratio=.001,
        heading_error_per_m=.001,vehicle_max_age_us=200_000,max_yaw_rate_rps=2.,
        max_gap_us=100_000,pose_capacity=16,timestamp_error_us=0,drift_ppm=0,
        max_offset_width_us=100,lease_us=1_000_000,lookback_us=500_000)


def motion_profile(development=None,**changes):
    values=assumptions();values.update(changes)
    return DevelopmentMotionProfile(DevelopmentProfile.from_bytes(FIXTURE.read_bytes())
        if development is None else development,**values)


class DevelopmentMotionProfileTests(unittest.TestCase):
    def test_omitted_or_none_virtual_steering_keeps_the_original_estimated_manifest(self):
        original=motion_profile();expected=original.canonical_bytes;none=motion_profile(steering_sensor_profile=None)
        self.assertEqual(none.canonical_bytes,expected)
        self.assertEqual(none.sha256,original.sha256)
        self.assertEqual(none.bound_calibration.sha256,original.bound_calibration.sha256)
        self.assertIsNone(original.steering_sensor_profile)
        self.assertEqual(original.fields['profile'],'course-development-motion-1')
        self.assertNotIn('steering_sensor',original.fields)
        self.assertEqual(original.fields['encoder']['steering_error_rad'],.001)

    def test_explicit_virtual_sensor_changes_profile_leaf_and_bundle_without_changing_neutral_or_label(self):
        from xenova_vehicle.simulation.steering_sensor import VirtualSteeringSensorProfile
        leaf=VirtualSteeringSensorProfile('virtual-current-servo-1',.0001,.0002,1,5.)
        original=motion_profile();measured=motion_profile(steering_sensor_profile=leaf)
        self.assertIs(measured.steering_sensor_profile,leaf)
        self.assertEqual(measured.fields['profile'],'course-development-motion-2')
        self.assertEqual(measured.fields['steering_sensor'],dict(leaf_sha256=leaf.sha256,
            profile=leaf.fields,combined_wire_quality=3))
        self.assertNotEqual(measured.sha256,original.sha256)
        self.assertNotEqual(measured.bound_calibration.sha256,original.bound_calibration.sha256)
        self.assertEqual(measured.neutral_calibration.sha256,original.neutral_calibration.sha256)
        self.assertEqual(measured.calibration_model_sha256,original.calibration_model_sha256)
        self.assertEqual(measured.fields['identity'],original.fields['identity'])
        self.assertEqual(measured.fields['encoder']['steering_error_rad'],.001)
        self.assertEqual(measured.odometry_arguments['calibration'],measured.sha256)
        self.assertFalse(measured.fields['qualification']['measured'])
        self.assertFalse(measured.fields['qualification']['physical_admission'])
        scene=demo_scene();fields=scene.fields;fields['track']['layout']['obstacle_seeds']=[]
        derived=measured.development_profile.for_scene(Scene(scene_canonical(fields)))
        self.assertEqual(motion_profile(derived,steering_sensor_profile=leaf).canonical_bytes,measured.canonical_bytes)
        for invalid in (False,object(),VirtualSteeringSensorProfile('too-slow',.0001,.0002,1,4.)):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):motion_profile(steering_sensor_profile=invalid)

    def test_manifest_has_exact_local_shape_and_noncyclic_neutral_bound_relation(self):
        profile=motion_profile();fields=profile.fields;neutral=profile.neutral_calibration
        before=neutral.to_dict();bound=profile.bound_calibration
        self.assertEqual(set(fields),{'profile','neutral_calibration_sha256','calibration_model_sha256',
            'identity','encoder','odometry','sensor','clock','qualification'})
        self.assertEqual(profile.canonical_bytes,canonical(fields))
        self.assertEqual(profile.sha256,sha256(profile.canonical_bytes).hexdigest())
        self.assertEqual(fields['neutral_calibration_sha256'],neutral.sha256)
        self.assertEqual(fields['calibration_model_sha256'],profile.development_profile.calibration_model_sha256)
        self.assertIsNone(neutral.vehicle_bundle_sha256)
        expected=neutral.to_dict();expected['vehicle_bundle_sha256']=profile.sha256
        self.assertEqual(bound.to_dict(),expected)
        self.assertEqual(neutral.to_dict(),before)
        self.assertEqual(bound.kind,'SYNTHETIC')
        self.assertNotEqual(profile.sha256,bound.sha256)
        self.assertNotEqual(neutral.sha256,bound.sha256)
        self.assertEqual(fields['qualification'],dict(input_kind='SYNTHETIC',measured=False,physical_admission=False))
        self.assertEqual(fields['identity']['calibration'],'course-sim-encoder-1')
        self.assertLessEqual(len(profile.canonical_bytes),4096)

    def test_every_new_assumption_is_required_and_native_boundaries_are_checked(self):
        development=DevelopmentProfile.from_bytes(FIXTURE.read_bytes())
        for missing in assumptions():
            args=assumptions();del args[missing]
            with self.subTest(missing=missing),self.assertRaises(TypeError):
                DevelopmentMotionProfile(development,**args)
        for key,value in (('encoder_bits',True),('encoder_bits',16),('metres_per_tick_upper',.00001),
                ('position_error_m',0.),('heading_error_rad',float('nan')),('maximum_speed_mps',True),
                ('vehicle_max_age_us',1_000_001),('pose_capacity',257),('max_gap_us',0),
                ('timestamp_error_us',-1),('drift_ppm',1_000_000),('lease_us',1_000_001),
                ('lookback_us',199_999)):
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):motion_profile(**{key:value})

    def test_scene_only_derivation_keeps_original_neutral_model_and_bundle(self):
        original=DevelopmentProfile.from_bytes(FIXTURE.read_bytes())
        scene=demo_scene();value=scene.fields
        value['track']['layout']['obstacle_seeds']=[]
        derived=original.for_scene(Scene(scene_canonical(value)))
        first,second=motion_profile(original),motion_profile(derived)
        self.assertNotEqual(original.sha256,derived.sha256)
        self.assertEqual(first.canonical_bytes,second.canonical_bytes)
        self.assertEqual(first.bound_calibration.sha256,second.bound_calibration.sha256)
        self.assertIsNone(first.check_development_profile(derived))
        changed=original.fields;changed['camera']['height_m']+=.01
        foreign=DevelopmentProfile.from_bytes(canonical(changed))
        with self.assertRaises(ValueError):first.check_development_profile(foreign)

    def test_field_copies_config_arguments_and_clock_policy_preserve_explicit_values(self):
        profile=motion_profile();fields=profile.fields
        fields['encoder']['track_width_m']=1.;fields['identity']['calibration']='foreign'
        self.assertEqual(profile.fields['encoder']['track_width_m'],.18)
        self.assertEqual(profile.fields['identity']['calibration'],'course-sim-encoder-1')
        args=profile.odometry_arguments
        self.assertEqual(args['vehicle'],'rc-sim-01');self.assertEqual(args['calibration'],profile.sha256)
        self.assertEqual(args['encoder_bits'],32);self.assertEqual(args['track_width_m'],.18)
        self.assertEqual(args['wheelbase_m'],profile.bound_calibration.body.wheelbase)
        self.assertEqual(args['position_error_m'],.0001)
        self.assertEqual(profile.pose_arguments['max_gap_us'],100_000)
        policy=profile.clock_policy(device='sim-nn',primitive='b'*64)
        self.assertEqual(policy.calibration,profile.sha256)
        self.assertEqual((policy.timestamp_error_us,policy.drift_ppm,policy.lease_us,policy.lookback_us),
                         (0,0,1_000_000,500_000))
        with self.assertRaises(FrozenInstanceError):profile._bytes=b'{}'


if __name__=='__main__':unittest.main()
