"""명시 rolling 정책과 구형 canonical bytes의 호환을 검사한다. 운행 자격은 만들지 않는다."""

from copy import deepcopy
import json
import unittest

from test_mapper_profile import profile_fields
from xenova_vehicle.runtime.mapper_profile import MapperProfile,MapperProfileError


class MapperRollingProfileTests(unittest.TestCase):
    def test_omitted_legacy_policy_keeps_exact_original_canonical_field_set(self):
        fields=profile_fields();fields.pop('rolling_roi',None)
        old=MapperProfile(fields)
        self.assertIsNone(old.rolling_roi)
        self.assertNotIn('rolling_roi',json.loads(old.canonical_bytes))
        self.assertEqual(MapperProfile.from_bytes(old.canonical_bytes).canonical_bytes,old.canonical_bytes)
        self.assertEqual(MapperProfile.from_bytes(old.canonical_bytes).sha256,old.sha256)

    def test_explicit_policy_is_immutable_and_null_does_not_enable_it(self):
        fields=profile_fields();fields['rolling_roi']=dict(margin_cells=4,lookahead_cells=0)
        chosen=MapperProfile(fields);raw=chosen.canonical_bytes
        fields['rolling_roi']['margin_cells']=5
        self.assertEqual(chosen.rolling_roi.margin_cells,4)
        self.assertEqual(chosen.canonical_bytes,raw)
        self.assertEqual(MapperProfile.from_bytes(raw).rolling_roi,chosen.rolling_roi)
        fields['rolling_roi']=None
        self.assertIsNone(MapperProfile(fields).rolling_roi)

    def test_all_fields_types_and_window_bounds_are_explicit(self):
        base=profile_fields()
        invalid=({},dict(margin_cells=4),dict(margin_cells=True,lookahead_cells=0),
            dict(margin_cells=0,lookahead_cells=0),dict(margin_cells=32,lookahead_cells=0),
            dict(margin_cells=4,lookahead_cells=-1),dict(margin_cells=4,lookahead_cells=33),
            dict(margin_cells=4,lookahead_cells=0,enabled=True))
        for value in invalid:
            fields=deepcopy(base);fields['rolling_roi']=value
            with self.subTest(value=value),self.assertRaises(MapperProfileError):MapperProfile(fields)
        fields=deepcopy(base);fields['pipeline']['grid']['width']=8
        fields['rolling_roi']=dict(margin_cells=4,lookahead_cells=0)
        with self.assertRaises(MapperProfileError):MapperProfile(fields)
