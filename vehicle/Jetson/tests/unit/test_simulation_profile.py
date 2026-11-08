"""공통 가상차 source/후륜축/미실측 가정과 불변 profile의 경계다.

원 scene은 renderer 입력으로만 검사한다. 차선·주차·목표·실물/출력 준비 수락이나
실제 plant/encoder/MQTT/SDK를 실행하지 않는다. SYNTHETIC 수학 flags와 물리 수락은 별개다.
"""

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import unittest

from xenova_vehicle.simulation.profile import DevelopmentProfile,DevelopmentProfileError,MAX_PROFILE_BYTES
from xenova_vehicle.simulation.scene import Scene,canonical,demo_scene


FIXTURE=Path(__file__).resolve().parents[2]/'src/xenova_vehicle/simulation/fixtures/experiment-profile.json'


def encoded(fields):
    return json.dumps(fields,sort_keys=True,separators=(',', ':'),ensure_ascii=True,allow_nan=False).encode('ascii')


class SimulationProfileTests(unittest.TestCase):
    def setUp(self):
        self.raw=FIXTURE.read_bytes()
        self.profile=DevelopmentProfile.from_bytes(self.raw)

    def test_original_profile_pins_actual_demo_scene_and_exact_software_source_identities(self):
        fields=self.profile.fields
        self.assertEqual(fields['identity'],dict(vehicle='rc-sim-01',configuration='course-sim-1',calibration='course-sim-encoder-1'))
        scene=demo_scene()
        self.assertIsNone(self.profile.check_scene(scene))
        self.assertEqual(fields['source']['scene']['sha256'],sha256(scene.canonical_bytes).hexdigest())
        self.assertEqual(fields['source']['scene']['size_bytes'],len(scene.canonical_bytes))
        self.assertEqual(fields['source']['commit'],'0b56c764eab16a62870eb1ec3c4be97bbc39d6ad')
        self.assertEqual([row['size_bytes'] for row in fields['source']['files']],[7593,6158,16570])
        self.assertIsNone(fields['source']['scene']['derived_from_profile_sha256'])
        self.assertFalse(fields['qualification']['measured'])
        self.assertFalse(fields['qualification']['physical_admission'])
        self.assertNotIn('centerline',fields)
        self.assertNotIn('parking_slots',fields)
        self.assertNotIn('goal',fields)

    def test_rear_axle_body_camera_and_independent_synthetic_calibration_model(self):
        profile=self.profile;fields=profile.fields;v=fields['vehicle'];c=fields['camera']
        self.assertEqual(v['pose_origin'],'REAR_AXLE_FLU')
        self.assertAlmostEqual(v['front_m'],(v['length_m']+v['wheelbase_m'])/2)
        self.assertAlmostEqual(v['rear_m'],(v['length_m']-v['wheelbase_m'])/2)
        self.assertEqual((v['front_m'],v['rear_m']),(.25,.05))
        self.assertEqual(v['track_width_m'],.18)
        self.assertEqual(v['track_width_source'],'new_unmeasured_experiment_value')
        self.assertEqual(v['estimated_steering_error_rad'],.001)
        self.assertAlmostEqual(v['max_steer_rad'],math.atan(v['wheelbase_m']/v['min_turn_radius_m']))
        calibration=profile.calibration()
        self.assertEqual(calibration.kind,'SYNTHETIC')
        self.assertEqual((calibration.body.length,calibration.body.width,calibration.body.wheelbase),(.3,.22,.2))
        self.assertEqual((calibration.body.front,calibration.body.rear),(.25,.05))
        self.assertEqual(calibration.rgb.translation,(.2,0.,.12))
        self.assertEqual(calibration.right.translation,(.2,-.0375,.12))
        self.assertEqual((calibration.rgb.width,calibration.rgb.height),(640,360))
        self.assertEqual((calibration.right.width,calibration.right.height),(640,480))
        self.assertAlmostEqual(calibration.rgb.k[0][0],640/(2*math.tan(c['hfov_rad']/2)))
        self.assertEqual(calibration.support_polygon,((-2.,-3.),(6.,-3.),(6.,3.),(-2.,3.)))
        provenance=calibration.provenance
        self.assertEqual(provenance['development_calibration_model_sha256'],profile.calibration_model_sha256)
        self.assertNotIn('development_profile_sha256',provenance)
        self.assertFalse(provenance['measured']);self.assertFalse(provenance['physical_admission'])
        self.assertIsNone(calibration.vehicle_bundle_sha256)
        self.assertEqual(calibration.sha256,profile.calibration().sha256)

    def test_source_dynamics_rates_and_explicit_steering_uncertainty_do_not_infer_track_width(self):
        fields=self.profile.fields;vehicle=fields['vehicle']
        for key,value in dict(wheel_radius_m=.033,encoder_cpr=1320,motor_tau_s=.12,max_wheel_rpm=450.,
                max_accel_mps2=2.,max_decel_mps2=4.,max_steer_rate_rps=5.).items():
            self.assertEqual(vehicle[key],value)
        self.assertEqual(fields['rates']['physics_hz'],100.)
        self.assertEqual(fields['camera']['fps'],15.)
        for missing in ('track_width_m','track_width_source','estimated_steering_error_rad','steering_error_source'):
            bad=deepcopy(fields);del bad['vehicle'][missing]
            with self.subTest(missing=missing),self.assertRaises(DevelopmentProfileError):
                DevelopmentProfile.from_bytes(encoded(bad))
        for key,value in (('track_width_m',0.),('track_width_m',.23),
                ('track_width_source','width_derived'),('estimated_steering_error_rad',0.),
                ('front_m',.3),('rear_m',.1),('max_steer_rad',.45),('motor_tau_s',.2)):
            bad=deepcopy(fields);bad['vehicle'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(DevelopmentProfileError):
                DevelopmentProfile.from_bytes(encoded(bad))

    def test_owned_canonical_bytes_and_nested_field_copies_are_immutable(self):
        value=self.profile
        expected=encoded(value.fields)
        self.assertEqual(value.canonical_bytes,expected)
        self.assertEqual(value.sha256,sha256(expected).hexdigest())
        first,second=value.fields,value.fields
        self.assertIsNot(first,second)
        self.assertIsNot(first['camera']['rgb_size'],second['camera']['rgb_size'])
        first['camera']['rgb_size'][0]=1
        first['source']['files'].clear()
        first['vehicle']['track_width_m']=.22
        self.assertEqual(value.canonical_bytes,expected)
        with self.assertRaises(AttributeError):value._canonical_bytes=b'{}'
        self.assertEqual(DevelopmentProfile.from_bytes(expected).canonical_bytes,expected)
        self.assertEqual(DevelopmentProfile.from_bytes(expected).calibration_model_sha256,value.calibration_model_sha256)

    def test_exact_fields_missing_unknown_source_pin_and_physical_claim_are_rejected(self):
        original=self.profile.fields
        for path in ((),('identity',),('source',),('source','scene'),('source','files',0),
                ('vehicle',),('camera',),('rates',),('surface',),('qualification',)):
            for operation in ('missing','unknown'):
                fields=deepcopy(original);target=fields
                for name in path:target=target[name]
                if operation=='missing':del target[next(iter(target))]
                else:target['extra']=None
                with self.subTest(path=path,operation=operation),self.assertRaises(DevelopmentProfileError):
                    DevelopmentProfile.from_bytes(encoded(fields))
        changes=((('source','commit'),'f'*40),(('source','files',0,'sha256'),'f'*64),
            (('source','scene','sha256'),'f'*64),(('source','scene','source_sha256'),'f'*64),
            (('qualification','measured'),True),(('qualification','physical_admission'),True))
        for path,value in changes:
            fields=deepcopy(original);target=fields
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(DevelopmentProfileError):
                DevelopmentProfile.from_bytes(encoded(fields))

    def test_duplicate_nonfinite_bool_numbers_and_payload_cap_fail_closed(self):
        fields=self.profile.fields;raw=encoded(fields)
        duplicates=(raw.replace(b'"profile":',b'"profile":"course-development-profile-1","profile":',1),
            raw.replace(b'"length_m":',b'"length_m":0.3,"length_m":',1))
        for invalid in (*duplicates,b'{} trailing',b'\xff',raw.replace(b'"length_m":0.3',b'"length_m":NaN'),
                raw.replace(b'"length_m":0.3',b'"length_m":Infinity'),
                raw.replace(b'"length_m":0.3',b'"length_m":1e999')):
            with self.assertRaises(DevelopmentProfileError):DevelopmentProfile.from_bytes(invalid)
        for path in (('vehicle','length_m'),('vehicle','encoder_cpr'),('camera','fps'),('camera','rgb_size',0),
                ('rates','physics_hz'),('source','files',0,'size_bytes'),('surface','support_polygon',0,0)):
            bad=deepcopy(fields);target=bad
            for key in path[:-1]:target=target[key]
            target[path[-1]]=True
            with self.subTest(path=path),self.assertRaises(DevelopmentProfileError):
                DevelopmentProfile.from_bytes(encoded(bad))
        for key in ('measured','physical_admission'):
            bad=deepcopy(fields);bad['qualification'][key]=0
            with self.assertRaises(DevelopmentProfileError):DevelopmentProfile.from_bytes(encoded(bad))
        padded=raw+b' '*(MAX_PROFILE_BYTES-len(raw))
        self.assertEqual(DevelopmentProfile.from_bytes(padded).sha256,self.profile.sha256)
        for invalid in (padded+b' ',b'',None,bytearray(raw),memoryview(raw)):
            with self.assertRaises(DevelopmentProfileError):DevelopmentProfile.from_bytes(invalid)

    def test_actual_scene_hash_and_layout_body_camera_must_match_without_returning_gt(self):
        with self.assertRaises(DevelopmentProfileError):self.profile.check_scene(demo_scene().fields)
        for key,value in (('source_commit','f'*40),('source_sha256','f'*64)):
            fields=demo_scene().fields;fields[key]=value
            with self.subTest(key=key),self.assertRaises(DevelopmentProfileError):
                self.profile.check_scene(Scene(canonical(fields)))
        for group,key,value in (('vehicle','length_m',.31),('camera','mount_x_m',.21)):
            fields=self.profile.fields
            if group=='vehicle':
                fields[group]['length_m']=value
                fields[group]['front_m']=(value+fields[group]['wheelbase_m'])/2
                fields[group]['rear_m']=(value-fields[group]['wheelbase_m'])/2
            else:fields[group]['ahead_m']=value
            changed=DevelopmentProfile.from_bytes(encoded(fields))
            with self.subTest(group=group,key=key),self.assertRaises(DevelopmentProfileError):
                changed.check_scene(demo_scene())

    def test_explicit_derived_scene_preserves_original_profile_and_calibration_lifetime(self):
        base=self.profile;original=base.canonical_bytes
        fields=demo_scene().fields;fields['track']['layout']['obstacle_seeds']=[]
        derived_scene=Scene(canonical(fields))
        derived=base.for_scene(derived_scene)
        self.assertIs(type(derived),DevelopmentProfile)
        self.assertNotEqual(derived.sha256,base.sha256)
        self.assertEqual(base.canonical_bytes,original)
        self.assertIsNone(derived.check_scene(derived_scene))
        with self.assertRaises(DevelopmentProfileError):base.check_scene(derived_scene)
        with self.assertRaises(DevelopmentProfileError):derived.check_scene(demo_scene())
        provenance=derived.fields['source']['scene']
        self.assertEqual(provenance['derived_from_profile_sha256'],base.sha256)
        self.assertEqual(provenance['base_sha256'],base.fields['source']['scene']['sha256'])
        self.assertEqual(provenance['sha256'],derived_scene.sha256)
        self.assertEqual(derived.calibration_model_sha256,base.calibration_model_sha256)
        self.assertEqual(derived.calibration().sha256,base.calibration().sha256)
        self.assertEqual(derived.fields['vehicle'],base.fields['vehicle'])
        self.assertEqual(derived.fields['camera'],base.fields['camera'])
        self.assertIs(derived.for_scene(derived_scene),derived)
        for group,key,value in (('vehicle','length_m',.31),('camera','height_m',.13)):
            bad=derived_scene.fields;bad['track']['layout'][group][key]=value
            with self.subTest(group=group),self.assertRaises(DevelopmentProfileError):
                base.for_scene(Scene(canonical(bad)))
