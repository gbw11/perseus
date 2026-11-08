"""명시 survey 설치 설정은 원 sensor/age/cap을 넓히거나 사용권을 발급하지 않는다."""

from dataclasses import asdict,FrozenInstanceError
import unittest

from test_mapper_profile import profile_fields
from xenova_vehicle.runtime.mapper_profile import MapperProfile,MapperProfileError
from xenova_vehicle.runtime.scene import SurveyProfile


def survey():
    return asdict(SurveyProfile('d'*64,3,4096,5000,.1,.01,.01,20000,.05,.05,500000,'LIVE'))


class MapperSurveyProfileTests(unittest.TestCase):
    def test_omission_null_and_explicit_frozen_policy_keep_canonical_distinct(self):
        fields=profile_fields();old=MapperProfile(fields)
        self.assertIsNone(old.survey);self.assertNotIn('survey',old.to_dict())
        fields['survey']=None
        self.assertIsNone(MapperProfile(fields).survey);self.assertNotEqual(MapperProfile(fields).sha256,old.sha256)
        fields['survey']=survey();configured=MapperProfile(fields)
        self.assertEqual(configured.survey,SurveyProfile(**fields['survey']))
        self.assertEqual(MapperProfile.from_bytes(configured.canonical_bytes).canonical_bytes,configured.canonical_bytes)
        with self.assertRaises(FrozenInstanceError):configured.survey.required_sensor_mask=1
        fields['survey']['required_sensor_mask']=1
        self.assertEqual(configured.survey.required_sensor_mask,3)
        self.assertEqual(configured.motion_qualified,old.motion_qualified)

    def test_runtime_survey_rejects_absent_sensors_nonlive_or_expanded_original_bounds(self):
        for key,value in (('required_sensor_mask',4),('required_sensor_mask',8),('input_kind','SYNTHETIC'),
                ('observation_max_age_us',500001),('max_pose_gap_us',100001),
                ('station_window_us',1000001),('atlas_cell_capacity',128*4096+1)):
            fields=profile_fields();fields['survey']={**survey(),key:value}
            with self.subTest(key=key,value=value),self.assertRaises(MapperProfileError):MapperProfile(fields)

    def test_all_survey_fields_are_explicit_and_builtin_types_are_required(self):
        for key in survey():
            fields=profile_fields();fields['survey']=survey();del fields['survey'][key]
            with self.subTest(missing=key),self.assertRaises(MapperProfileError):MapperProfile(fields)
        for value in ({},[],{**survey(),'required_sensor_mask':True},{**survey(),'unknown':1}):
            fields=profile_fields();fields['survey']=value
            with self.subTest(value=value),self.assertRaises(MapperProfileError):MapperProfile(fields)
