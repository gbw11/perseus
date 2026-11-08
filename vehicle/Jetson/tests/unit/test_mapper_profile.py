"""명시 local mapper 설정의 타입·원 ID·fail-closed 조립. 실측 승인 fixture가 아니다."""

from dataclasses import asdict, FrozenInstanceError
from hashlib import sha256
import json
import unittest

from xenova_vehicle.runtime.mapper_profile import MapperProfile, MapperProfileError, LocalizationLimits, MAX_BYTES
from xenova_vehicle.perception.calibration import synthetic_calibration, CalibrationProfile
from xenova_vehicle.perception.pipeline import PipelineProfile
from xenova_vehicle.perception.free_space import TargetClass
from xenova_vehicle.perception.parking import ParkingAisle
from xenova_vehicle.perception.tof import TofLeaf


def calibration(*, leaf=None):
    fields=synthetic_calibration().to_dict(); fields['vehicle_bundle_sha256']='a'*64
    if leaf is not None: fields['external_leaves']={'front':leaf.sha256}
    return CalibrationProfile.from_dict(fields)


def profile_fields(calib=None):
    calib=calibration() if calib is None else calib
    # 기존 dataclass의 실험 기본값을 fixture에 전부 명시한다. 배포 기본값 발급은 아니다.
    return dict(schema_version=1,vehicle_configuration='vehicle-config-lab',calibration_profile_sha256=calib.sha256,
        capture_timing=dict(clock_error_us=2,timestamp_error_us=10,readout_error_us=20,max_depth_exposure_us=5000),
        sensors=dict(vehicle_max_age_us=100000,tof_max_age_us=100000,max_speed_mps=.5,max_yaw_rate_rps=2.,
            max_gap_us=100000,capacity=256,tof_sources=[]),pipeline=asdict(PipelineProfile()),
        targets=[asdict(TargetClass(.03,.06,.15,(0.,0.),False))],
        parking_aisles=[asdict(ParkingAisle((0.,0.),(1.,0.),1,.01,0))],
        stopping=dict(max_speed_mps=.5,min_braking_mps2=1.,reaction_latency_us=10000,
            cycle_bound_us=50000,completion_bound_us=30000,body_error_m=.005),motion_qualified=False)


def tof_leaf():
    return TofLeaf(sensor_id=0,sensor_config_revision=1,model='EXPLICIT_TEST_MODEL',driver_profile_sha256='e'*64,
        rotation=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)),translation=(.1,0.,.1),
        zone_bearings=((-0.02,0.02,-0.02,0.02),)*64,distance_convention='AXIAL_Z',
        near_m=.1,far_m=2.,measurement_error_m=.01)


class MapperProfileTests(unittest.TestCase):
    def test_roundtrip_immutable_typed_config_preserves_distinct_vehicle_and_local_ids(self):
        calib=calibration(); value=profile_fields(calib)
        profile=MapperProfile.from_dict(value)
        profile.check(calib,vehicle_configuration='vehicle-config-lab',vehicle_calibration='a'*64)
        self.assertNotEqual(profile.sha256,profile.vehicle_configuration)
        self.assertNotEqual(profile.calibration_profile_sha256,'a'*64)
        self.assertEqual(MapperProfile.from_bytes(profile.canonical_bytes).canonical_bytes,profile.canonical_bytes)
        value['sensors']['max_speed_mps']=99
        copied=profile.to_dict(); copied['stopping']['max_speed_mps']=99
        self.assertEqual(profile.sensors.max_speed_mps,.5); self.assertEqual(profile.stopping.max_speed_mps,.5)
        self.assertIsInstance(profile.pipeline,PipelineProfile)
        with self.assertRaises(FrozenInstanceError): profile.sensors.capacity=2

    def test_omitted_null_or_partial_timing_never_opens_declared_motion_qualification(self):
        for choice in ('OMIT','NULL','PARTIAL','ALL_NONE'):
            value=profile_fields(); value['motion_qualified']=True
            if choice=='OMIT': del value['capture_timing']
            elif choice=='NULL': value['capture_timing']=None
            elif choice=='PARTIAL': value['capture_timing']['readout_error_us']=None
            else: value['capture_timing']={key:None for key in value['capture_timing']}
            with self.subTest(choice=choice):
                profile=MapperProfile(value)
                self.assertTrue(profile.declared_motion_qualified); self.assertTrue(profile.to_dict()['motion_qualified'])
                self.assertFalse(profile.timing_configured); self.assertFalse(profile.motion_qualified)
        full=profile_fields(); full['motion_qualified']=True
        self.assertTrue(MapperProfile(full).motion_qualified)
        self.assertFalse(MapperProfile(profile_fields()).motion_qualified)

    def test_missing_nested_or_qualification_fields_are_not_filled_from_dataclass_defaults(self):
        paths=(('stopping','body_error_m'),('pipeline','free_ttl_us'),('targets',0,'qualified'),
               ('pipeline','depth','max_patches'),('sensors','capacity'),('capture_timing','clock_error_us'))
        for path in paths:
            value=profile_fields(); target=value
            for key in path[:-1]: target=target[key]
            del target[path[-1]]
            with self.subTest(path=path),self.assertRaises(MapperProfileError): MapperProfile(value)

    def test_original_work_age_shape_and_stopping_bounds_are_enforced(self):
        changes=(('pipeline','free_ttl_us',500001),('pipeline','max_observation_age_us',500001),
                 ('pipeline','max_free_cells',257),('sensors','max_gap_us',1000001),
                 ('sensors','max_speed_mps',True),('stopping','max_speed_mps',.4))
        for group,name,setting in changes:
            value=profile_fields(); value[group][name]=setting
            with self.subTest(group=group,name=name),self.assertRaises(MapperProfileError): MapperProfile(value)
        for subfield,setting in (('stride',2),('max_patches',8193)):
            value=profile_fields(); value['pipeline']['depth'][subfield]=setting
            with self.subTest(depth=subfield),self.assertRaises(MapperProfileError): MapperProfile(value)
        value=profile_fields(); value['pipeline']['grid']['width']=128
        with self.assertRaises(MapperProfileError): MapperProfile(value)

    def test_permission_support_and_target_flags_remain_separate_explicit_inputs(self):
        value=profile_fields(); parsed=MapperProfile(value)
        self.assertEqual(parsed.pipeline.permission_polygon,()); self.assertFalse(parsed.targets[0].qualified)
        value['pipeline']['permission_polygon']=[[-1.,-1.],[1.,-1.],[1.,1.],[-1.,1.]]
        with self.assertRaises(MapperProfileError): MapperProfile(value)
        value['pipeline']['permission_source']='EXPLICIT_INSTALLATION_POLICY'
        self.assertEqual(len(MapperProfile(value).pipeline.permission_polygon),4)
        value['pipeline']['road']['allow_dashed']=1
        with self.assertRaises(MapperProfileError): MapperProfile(value)

    def test_actual_tof_leaf_inventory_is_checked_against_the_calibration_asset(self):
        leaf=tof_leaf(); calib=calibration(leaf=leaf); value=profile_fields(calib)
        value['sensors']['tof_sources']=[dict(external_leaf_name='front',leaf=leaf.to_dict())]
        parsed=MapperProfile(value); parsed.check(calib,vehicle_configuration='vehicle-config-lab',vehicle_calibration='a'*64)
        leaves=parsed.sensors.tof_leaves; self.assertEqual(leaves[0][1].sha256,leaf.sha256)
        self.assertFalse(leaves[0][1].first_surface_qualified)
        leaves.clear(); self.assertEqual(len(parsed.sensors.tof_leaves),1)
        value['sensors']['tof_sources'][0]['leaf']['model']='ANOTHER_SENSOR'
        changed=MapperProfile(value)
        with self.assertRaises(MapperProfileError): changed.check(calib,vehicle_configuration='vehicle-config-lab',vehicle_calibration='a'*64)
        value['sensors']['tof_sources']*=2
        with self.assertRaises(MapperProfileError): MapperProfile(value)

    def test_asset_or_configuration_mismatch_is_not_repaired_with_the_local_profile_sha(self):
        calib=calibration(); parsed=MapperProfile(profile_fields(calib))
        for config,bundle in ((parsed.sha256,'a'*64),('vehicle-config-lab',calib.sha256),('other','a'*64)):
            with self.subTest(config=config,bundle=bundle),self.assertRaises(MapperProfileError):
                parsed.check(calib,vehicle_configuration=config,vehicle_calibration=bundle)

    def test_raw_cap_pretty_json_duplicate_nonfinite_and_encoding_boundaries(self):
        raw=json.dumps(profile_fields(),indent=2).encode('ascii')
        self.assertLess(len(raw),MAX_BYTES)
        self.assertTrue(MapperProfile.from_bytes(raw+b' '*(MAX_BYTES-len(raw))).canonical_bytes)
        for invalid in (raw+b' '*(MAX_BYTES+1-len(raw)),b'\xef\xbb\xbf'+raw,raw.decode().encode('utf-16-le'),
                        b'{"schema_version":1,"schema_version":1}',b'{"x":NaN}',b'{"x":1e999}'):
            with self.subTest(length=len(invalid)),self.assertRaises(MapperProfileError): MapperProfile.from_bytes(invalid)

    def test_native_encoder_is_bounded_and_does_not_call_subclass_hooks(self):
        calls=[]
        class Foreign(dict):
            def items(self): calls.append('items'); raise AssertionError('hook ran')
        for value in (Foreign(),{'x':1<<100000},{'x':'a'*(MAX_BYTES+1)},{'x':[None]*4097}):
            with self.assertRaises(MapperProfileError): MapperProfile.from_dict(value)
        self.assertEqual(calls,[])
        cyclic={}; cyclic['x']=cyclic
        with self.assertRaises(MapperProfileError): MapperProfile(cyclic)

    def test_optional_localization_preserves_legacy_canonical_and_explicit_null(self):
        calib=calibration(); value=profile_fields(calib)
        expected=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
        omitted=MapperProfile(value)
        self.assertIsNone(omitted.localization); self.assertFalse(omitted.localization_configured)
        self.assertNotIn('localization',omitted.to_dict())
        self.assertEqual(omitted.canonical_bytes,expected)
        self.assertEqual(omitted.sha256,sha256(expected).hexdigest())
        value['localization']=None
        explicit=MapperProfile(value)
        expected_null=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
        self.assertIsNone(explicit.localization); self.assertFalse(explicit.localization_configured)
        self.assertIn('localization',explicit.to_dict()); self.assertIsNone(explicit.to_dict()['localization'])
        self.assertEqual(explicit.canonical_bytes,expected_null)
        self.assertNotEqual(explicit.sha256,omitted.sha256)
        self.assertEqual(MapperProfile.from_bytes(explicit.canonical_bytes).canonical_bytes,explicit.canonical_bytes)
        for parsed in (omitted,explicit):
            parsed.check(calib,vehicle_configuration='vehicle-config-lab',vehicle_calibration='a'*64)
            self.assertEqual(parsed.vehicle_configuration,omitted.vehicle_configuration)
            self.assertEqual(parsed.calibration_profile_sha256,omitted.calibration_profile_sha256)
            with self.assertRaises(MapperProfileError):
                parsed.check(calib,vehicle_configuration=parsed.sha256,vehicle_calibration='a'*64)

    def test_explicit_localization_is_immutable_and_does_not_change_motion_policy(self):
        for valid_for in (1,1_000_000):
            value=profile_fields(); value['motion_qualified']=True
            value['localization']=dict(registration_valid_for_us=valid_for,position_drift_mps=0.,yaw_drift_rps=0.)
            parsed=MapperProfile(value)
            self.assertTrue(parsed.localization_configured); self.assertTrue(parsed.motion_qualified)
            self.assertEqual(parsed.localization,LocalizationLimits(valid_for,0.,0.))
            with self.assertRaises(FrozenInstanceError): parsed.localization.position_drift_mps=1.
            value['localization']['position_drift_mps']=1.
            copied=parsed.to_dict(); copied['localization']['yaw_drift_rps']=1.
            self.assertEqual(parsed.localization.position_drift_mps,0.)
            self.assertEqual(parsed.localization.yaw_drift_rps,0.)
            roundtrip=MapperProfile.from_bytes(parsed.canonical_bytes)
            self.assertEqual(roundtrip.localization,parsed.localization)
        # 큰 유한 drift도 설정 문법에서는 허용한다. 실제 geometry 수락은 후속 책임이다.
        value=profile_fields(); value['motion_qualified']=True; value['capture_timing']=None
        value['localization']=dict(registration_valid_for_us=1,position_drift_mps=1e200,yaw_drift_rps=1e200)
        parsed=MapperProfile(value)
        self.assertTrue(parsed.localization_configured); self.assertFalse(parsed.motion_qualified)
        self.assertEqual(parsed.localization.position_drift_mps,1e200)
        del value['localization']
        self.assertFalse(MapperProfile(value).motion_qualified)

    def test_localization_requires_exact_three_fields_and_finite_nonnegative_bounds(self):
        good=dict(registration_valid_for_us=100,position_drift_mps=0.,yaw_drift_rps=0.)
        invalid=[{},[],True,{**good,'unknown':0}]
        invalid.extend({key:value for key,value in good.items() if key!=missing} for missing in good)
        for field,settings in (
                ('registration_valid_for_us',(True,0,-1,1_000_001,1.,None,float('inf'))),
                ('position_drift_mps',(True,-.001,None,float('nan'),float('inf'),-float('inf'))),
                ('yaw_drift_rps',(True,-.001,None,float('nan'),float('inf'),-float('inf')))):
            invalid.extend({**good,field:setting} for setting in settings)
        for row in invalid:
            value=profile_fields(); value['localization']=row
            with self.subTest(localization=row),self.assertRaises(MapperProfileError): MapperProfile(value)
        for row in (dict(registration_valid_for_us=True,position_drift_mps=0.,yaw_drift_rps=0.),
                    dict(registration_valid_for_us=1,position_drift_mps=-1.,yaw_drift_rps=0.),
                    dict(registration_valid_for_us=1,position_drift_mps=0.,yaw_drift_rps=float('nan'))):
            with self.subTest(direct=row),self.assertRaises(MapperProfileError): LocalizationLimits(**row)
