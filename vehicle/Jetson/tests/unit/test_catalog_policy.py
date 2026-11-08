"""설치 역할 설정과 원 관측의 결박을 검사한다. 도로 기하/물리 수락은 별도다."""

from dataclasses import replace,FrozenInstanceError
import json
import unittest

from test_mapper_profile import profile_fields
from test_world_adapter import road
from xenova_vehicle.perception.inputs import FrameStamp
from xenova_vehicle.runtime.mapper_profile import MapperProfile,MapperProfileError,CatalogPolicy


class CatalogPolicyTests(unittest.TestCase):
    def test_omission_keeps_original_canonical_and_null_does_not_enable_policy(self):
        fields=profile_fields()
        raw=json.dumps(fields,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
        self.assertEqual(MapperProfile(fields).canonical_bytes,raw)
        self.assertIsNone(MapperProfile(fields).catalog_policy)
        fields['catalog_policy']=None
        parsed=MapperProfile(fields)
        self.assertIsNone(parsed.catalog_policy)
        self.assertIn('catalog_policy',parsed.to_dict())

    def test_roles_are_immutable_and_match_declared_aisles_without_preassigned_ids(self):
        fields=profile_fields();fields['catalog_policy']=dict(crossing_allowed=True,aisle_lane_indices=[1])
        parsed=MapperProfile(fields)
        self.assertEqual(parsed.catalog_policy,CatalogPolicy(True,(1,)))
        fields['catalog_policy']['aisle_lane_indices'][0]=0
        self.assertEqual(parsed.catalog_policy.aisle_lane_indices,(1,))
        with self.assertRaises(FrozenInstanceError):parsed.catalog_policy.crossing_allowed=False
        self.assertEqual(MapperProfile.from_bytes(parsed.canonical_bytes).catalog_policy,parsed.catalog_policy)
        for roles in ([],[0,1],[True],[2]):
            fields['catalog_policy']['aisle_lane_indices']=roles
            with self.subTest(roles=roles),self.assertRaises(MapperProfileError):MapperProfile(fields)
        fields['catalog_policy']['aisle_lane_indices']=[0]
        fields['parking_aisles'][0]['approach_lane_id']=1
        with self.assertRaises(MapperProfileError):MapperProfile(fields)

    def test_exact_policy_fields_and_builtin_bool_are_required(self):
        for policy in ({},{'crossing_allowed':True},{'crossing_allowed':1,'aisle_lane_indices':[0]},
                {'crossing_allowed':True,'aisle_lane_indices':[0],'extra':0},[]):
            fields=profile_fields();fields['catalog_policy']=policy
            with self.subTest(policy=policy),self.assertRaises(MapperProfileError):MapperProfile(fields)

    def test_crossing_uses_only_original_frame_and_never_fills_missing_geometry(self):
        stamp=FrameStamp('worker','rgb',1,'clock',(1,2),3)
        original=replace(road(),source_key=stamp.key)
        count=len(original.hypotheses[0].divider)-1
        self.assertEqual(CatalogPolicy(True,()).crossing(original,stamp),bytes([1])*count)
        self.assertEqual(CatalogPolicy(False,()).crossing(original,stamp),bytes(count))
        self.assertEqual(CatalogPolicy(True,()).crossing(replace(original,hypotheses=()),stamp),b'')
        with self.assertRaises(MapperProfileError):CatalogPolicy(True,()).crossing(original,replace(stamp,sequence=2))
