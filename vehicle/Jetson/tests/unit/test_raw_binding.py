"""RawBinding의 값 문법·canonical 소유권·독립 P0 기대값을 검사한다.

합성 자산 참조를 사용하며 실제 파일·장치·보정 승인이나 BIND 전달을 시험하지
않는다. 숫자 폭과 native 입력 거절은 공개 값 constructor의 책임으로 검증한다.
"""

from copy import deepcopy
import hashlib
import itertools
import json
import unittest
from unittest.mock import patch

from xenova_vehicle.runtime import raw_binding as binding
from raw_binding_fixtures import binding_fields, identifier, profile


IDENTITIES = ('service_instance', 'acquisition_worker', 'pipeline_instance',
              'capture_epoch', 'sdk_bridge_epoch', 'clock_epoch')
REFS = ('device_id', 'clock_epoch', 'model_id', 'parser_id', 'graph_profile_sha256',
        'format_table_sha256', 'calibration_bundle')
HASHES = ('model_id', 'parser_id', 'graph_profile_sha256', 'format_table_sha256')
ASSET_REFS = (*HASHES, 'calibration_bundle')


class RawBindingTests(unittest.TestCase):
    def make_profile(self, original=None, **changes):
        original = profile() if original is None else original
        values = {name: getattr(original, name) for name in (*REFS, 'nn_enabled', 'jpeg_enabled')}
        values.update(changes)
        return binding.RawBindingProfile(**values)

    def rejected(self, fields):
        with self.assertRaises(binding.RawBindingError):
            binding.RawBinding(fields)

    def test_exact_thirteen_fields_have_no_added_version(self):
        fields = binding_fields()
        self.assertEqual(set(fields), {*IDENTITIES, 'stream_instances', 'device_id',
                                       'model_id', 'parser_id', 'graph_profile_sha256',
                                       'format_table_sha256', 'calibration_bundle'})
        self.assertEqual(len(fields), 13)
        self.assertEqual(binding.RawBinding(fields).fields, fields)
        for name in fields:
            with self.subTest(missing=name):
                changed = deepcopy(fields)
                del changed[name]
                self.rejected(changed)
        self.rejected({**fields, 'version': 1})

    def test_canonical_bytes_and_sha_use_the_entire_owned_body(self):
        fields = binding_fields(nn_enabled=True, jpeg_enabled=True)
        fields['calibration_bundle'] = 'f' * 64
        value = binding.RawBinding(fields)
        independent = json.dumps(fields, sort_keys=True, ensure_ascii=True,
                                 separators=(',', ':'), allow_nan=False).encode('ascii')
        self.assertIs(type(value.canonical_bytes), bytes)
        self.assertEqual(value.canonical_bytes, independent)
        self.assertEqual(value.sha256, hashlib.sha256(independent).hexdigest())
        self.assertNotIn(b'\n', value.canonical_bytes)
        self.assertNotIn(b' ', value.canonical_bytes)
        reversed_fields = dict(reversed(tuple(fields.items())))
        reversed_fields['stream_instances'] = dict(reversed(tuple(fields['stream_instances'].items())))
        other = binding.RawBinding(reversed_fields)
        self.assertEqual(other.canonical_bytes, value.canonical_bytes)
        self.assertEqual(other.sha256, value.sha256)

    def test_valid_maximum_width_body_is_1121_bytes(self):
        fields = binding_fields(nn_enabled=True, jpeg_enabled=True)
        fields['device_id'] = 'Z' * 64
        fields['calibration_bundle'] = 'f' * 64
        value = binding.RawBinding(fields)
        self.assertEqual(len(value.canonical_bytes), 1121)
        self.assertEqual(len(fields['stream_instances']), 5)
        self.assertEqual(len(set(fields['stream_instances'].values())), 5)
        approved = self.make_profile(profile(nn_enabled=True, jpeg_enabled=True),
                                     device_id='Z' * 64, calibration_bundle='f' * 64)
        self.assertIsNone(approved.check(value))

    def test_input_and_public_field_mutation_cannot_change_owned_value(self):
        fields = binding_fields()
        original = deepcopy(fields)
        value = binding.RawBinding(fields)
        canonical, digest = value.canonical_bytes, value.sha256
        fields['device_id'] = 'changed'
        fields['stream_instances']['rgb_video'] = identifier(999)
        first = value.fields
        second = value.fields
        self.assertIsNot(first, second)
        self.assertIsNot(first['stream_instances'], second['stream_instances'])
        first['device_id'] = 'another'
        first['stream_instances'].clear()
        self.assertEqual(value.fields, original)
        self.assertEqual(value.canonical_bytes, canonical)
        self.assertEqual(value.sha256, digest)
        with self.assertRaises(AttributeError):
            value.sha256 = '0' * 64

    def test_each_canonical_identity_uuid_is_required(self):
        base = binding_fields()
        for name in IDENTITIES:
            for wrong in (None, '', identifier(201).upper(), identifier(201).replace('-', ''),
                          '{' + identifier(201) + '}', 'not-a-uuid'):
                with self.subTest(name=name, wrong=wrong):
                    fields = deepcopy(base)
                    fields[name] = wrong
                    self.rejected(fields)

    def test_six_identity_fields_may_alias_even_a_stream_uuid(self):
        fields = binding_fields(nn_enabled=True, jpeg_enabled=True)
        shared = fields['stream_instances']['rgb_video']
        for name in IDENTITIES:
            fields[name] = shared
        value = binding.RawBinding(fields)
        self.assertTrue(all(value.fields[name] == shared for name in IDENTITIES))
        self.assertEqual(len(set(value.fields['stream_instances'].values())), 5)

    def test_all_nn_and_jpeg_presence_combinations_are_valid(self):
        for nn_enabled, jpeg_enabled in itertools.product((False, True), repeat=2):
            with self.subTest(nn_enabled=nn_enabled, jpeg_enabled=jpeg_enabled):
                fields = binding_fields(nn_enabled=nn_enabled, jpeg_enabled=jpeg_enabled)
                value = binding.RawBinding(fields)
                approved = profile(nn_enabled=nn_enabled, jpeg_enabled=jpeg_enabled)
                self.assertIsNone(approved.check(value))
                self.assertIs(type(approved.stream_names), tuple)
                self.assertEqual(set(approved.stream_names), set(fields['stream_instances']))
                self.assertEqual(len(fields['stream_instances']), 2 + 2 * nn_enabled + jpeg_enabled)
                self.assertIn('rgb_video', fields['stream_instances'])
                self.assertIn('depth_right', fields['stream_instances'])
                self.assertEqual(fields['model_id'] is not None, nn_enabled)
                self.assertEqual(fields['parser_id'] is not None, nn_enabled)

    def test_required_rgb_and_depth_remain_in_no_video_profile(self):
        fields = binding_fields(nn_enabled=False, jpeg_enabled=False)
        self.assertEqual(set(fields['stream_instances']), {'rgb_video', 'depth_right'})
        for name in ('rgb_video', 'depth_right'):
            with self.subTest(name=name):
                changed = deepcopy(fields)
                del changed['stream_instances'][name]
                self.rejected(changed)

    def test_nn_stream_pair_cannot_be_partial(self):
        for missing in ('nn_input0', 'nn_output0'):
            with self.subTest(missing=missing):
                fields = binding_fields()
                del fields['stream_instances'][missing]
                self.rejected(fields)

    def test_model_parser_pair_and_stream_presence_must_agree(self):
        for nn_enabled in (False, True):
            base = binding_fields(nn_enabled=nn_enabled)
            for name in ('model_id', 'parser_id'):
                with self.subTest(nn_enabled=nn_enabled, name=name):
                    fields = deepcopy(base)
                    fields[name] = None if nn_enabled else 'f' * 64
                    self.rejected(fields)
            fields = deepcopy(base)
            fields['model_id'] = fields['parser_id'] = None if nn_enabled else 'f' * 64
            self.rejected(fields)

    def test_stream_uuid_alias_is_rejected_but_other_identity_alias_is_not(self):
        base = binding_fields(nn_enabled=True, jpeg_enabled=True)
        for name in ('depth_right', 'nn_input0', 'nn_output0', 'jpeg'):
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields['stream_instances'][name] = fields['stream_instances']['rgb_video']
                self.rejected(fields)

    def test_inactive_null_unknown_and_imu_streams_are_not_supported(self):
        base = binding_fields(nn_enabled=False, jpeg_enabled=False)
        for name, wrong in (('jpeg', None), ('nn_input0', None), ('nn_output0', None),
                            ('imu', identifier(999)), ('imu_accel', identifier(999)),
                            ('unknown', identifier(999))):
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields['stream_instances'][name] = wrong
                self.rejected(fields)

    def test_each_stream_uuid_has_exact_canonical_form(self):
        base = binding_fields(nn_enabled=True, jpeg_enabled=True)
        for name in base['stream_instances']:
            for wrong in (None, '01234567-89AB-CDEF-8123-456789ABCDEF',
                          '0123456789abcdef8123456789abcdef'):
                with self.subTest(name=name, wrong=wrong):
                    fields = deepcopy(base)
                    fields['stream_instances'][name] = wrong
                    self.rejected(fields)

    def test_sha_fields_require_lowercase_hex64_and_calibration_is_nullable(self):
        base = binding_fields()
        for name in (*HASHES, 'calibration_bundle'):
            for wrong in ('F' * 64, 'g' * 64, 'a' * 63, 'a' * 65, '', 0):
                with self.subTest(name=name, wrong=wrong):
                    fields = deepcopy(base)
                    fields[name] = wrong
                    self.rejected(fields)
        for calibration in (None, '0' * 64, 'f' * 64):
            with self.subTest(calibration=calibration):
                fields = deepcopy(base)
                fields['calibration_bundle'] = calibration
                self.assertEqual(binding.RawBinding(fields).fields['calibration_bundle'], calibration)

    def test_graph_and_format_references_are_required_even_without_nn(self):
        base = binding_fields(nn_enabled=False)
        for name in ('graph_profile_sha256', 'format_table_sha256'):
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields[name] = None
                self.rejected(fields)

    def test_device_id_preserves_case_and_allowed_symbol_width(self):
        base = binding_fields()
        for device in ('A', 'a', '0', '_', '-', 'Ab_C-123', 'Z' * 64):
            with self.subTest(device=device):
                fields = deepcopy(base)
                fields['device_id'] = device
                self.assertEqual(binding.RawBinding(fields).fields['device_id'], device)
        upper = deepcopy(base)
        lower = deepcopy(base)
        upper['device_id'], lower['device_id'] = 'ABCDEF', 'abcdef'
        self.assertNotEqual(binding.RawBinding(upper).sha256, binding.RawBinding(lower).sha256)

    def test_device_id_rejects_empty_oversize_whitespace_path_and_non_ascii(self):
        base = binding_fields()
        for device in ('', 'a' * 65, 'a b', ' a', 'a\n', 'a/b', 'a\\b', 'a:b', 'a.b', '\uac00'):
            with self.subTest(device=device):
                fields = deepcopy(base)
                fields['device_id'] = device
                self.rejected(fields)

    def test_numeric_bool_float_bytes_and_list_are_not_native_scalar_coercions(self):
        base = binding_fields()
        for wrong in (0, 1, True, False, 1.0, b'abc', [], ()):
            with self.subTest(type_name=type(wrong).__name__):
                fields = deepcopy(base)
                fields['device_id'] = wrong
                self.rejected(fields)
        for wrong in ([], (), None, b'{}', '{}'):
            with self.subTest(root_type=type(wrong).__name__):
                self.rejected(wrong)

    def test_numeric_keys_are_rejected_without_stringifying_indices(self):
        for wrong in (0, 1, True):
            with self.subTest(wrong=wrong):
                fields = binding_fields()
                fields[wrong] = fields.pop('device_id')
                self.rejected(fields)
                fields = binding_fields()
                streams = fields['stream_instances']
                streams[wrong] = streams.pop('rgb_video')
                self.rejected(fields)

    def test_depth_node_string_and_cycle_invalid_inputs_reject_before_serialization(self):
        nested = binding_fields()
        nested['stream_instances']['rgb_video'] = {'deeper': identifier(999)}
        many = binding_fields()
        many['stream_instances'] = {str(i): identifier(999) for i in range(65)}
        long_value = binding_fields()
        long_value['device_id'] = 'a' * 65
        long_key = binding_fields()
        long_key['a' * 65] = long_key.pop('device_id')
        cyclic = binding_fields()
        cyclic['stream_instances']['rgb_video'] = cyclic
        # 정상 schema는 최대37 nodes다. 64를 넘기는 입력을 정상 경계 양성으로 꾸미지 않는다.
        for name, fields in (('depth', nested), ('nodes', many), ('string', long_value),
                             ('key', long_key), ('cycle', cyclic)):
            with self.subTest(name=name):
                with patch('json.dumps', side_effect=AssertionError('serialize before native rejection')):
                    with patch('copy.deepcopy', side_effect=AssertionError('unbounded deepcopy')):
                        self.rejected(fields)

    def test_custom_metaclass_and_container_hooks_are_not_invoked(self):
        calls = []

        def forbidden(name):
            calls.append(name)
            raise AssertionError('custom callback invoked: ' + name)

        class TrapMeta(type):
            def __eq__(cls, other):
                return forbidden('metaclass equality')

            def __hash__(cls):
                return forbidden('metaclass hash')

        class Trap(metaclass=TrapMeta):
            def __iter__(self):
                return forbidden('iterator')

            def __index__(self):
                return forbidden('numeric index')

            def __str__(self):
                return forbidden('string conversion')

            def __repr__(self):
                return forbidden('representation')

            def __deepcopy__(self, memo):
                return forbidden('deepcopy')

        class TrapDict(dict, metaclass=TrapMeta):
            def items(self):
                return forbidden('dict items')

            def __iter__(self):
                return forbidden('dict iterator')

            def __len__(self):
                return forbidden('dict length')

        class TrapStr(str, metaclass=TrapMeta):
            def __len__(self):
                return forbidden('string length')

            def __str__(self):
                return forbidden('subclass conversion')

        objects = (Trap(), TrapDict(binding_fields()), TrapStr('approved'))
        for index, hostile in enumerate(objects):
            with self.subTest(case=index):
                self.rejected(hostile)
                fields = binding_fields()
                fields['device_id'] = hostile
                self.rejected(fields)
                fields = binding_fields()
                fields['stream_instances'] = hostile
                self.rejected(fields)
        self.assertEqual(calls, [])

    def test_profile_checks_independently_supplied_refs(self):
        approved = profile(calibration='e' * 64)
        base = binding_fields()
        base['calibration_bundle'] = 'e' * 64
        self.assertIsNone(approved.check(binding.RawBinding(base)))
        for name in REFS:
            with self.subTest(name=name):
                fields = deepcopy(base)
                fields[name] = ('OTHER_DEVICE' if name == 'device_id' else
                                identifier(999) if name == 'clock_epoch' else 'f' * 64)
                candidate = binding.RawBinding(fields)
                with self.assertRaises(binding.RawBindingError):
                    approved.check(candidate)
                self.assertEqual(getattr(approved, name), base[name])

    def test_profile_device_case_is_exact_even_when_both_values_are_well_typed(self):
        fields = binding_fields()
        fields['device_id'] = 'abcdef'
        approved = self.make_profile(device_id='ABCDEF')
        with self.assertRaises(binding.RawBindingError):
            approved.check(binding.RawBinding(fields))

    def test_profile_nn_and_jpeg_choices_do_not_follow_candidate_presence(self):
        for expected_nn, expected_jpeg in itertools.product((False, True), repeat=2):
            approved = profile(nn_enabled=expected_nn, jpeg_enabled=expected_jpeg)
            for actual_nn, actual_jpeg in itertools.product((False, True), repeat=2):
                with self.subTest(expected_nn=expected_nn, expected_jpeg=expected_jpeg,
                                  actual_nn=actual_nn, actual_jpeg=actual_jpeg):
                    candidate = binding.RawBinding(binding_fields(nn_enabled=actual_nn,
                                                                 jpeg_enabled=actual_jpeg))
                    if (expected_nn, expected_jpeg) == (actual_nn, actual_jpeg):
                        self.assertIsNone(approved.check(candidate))
                    else:
                        with self.assertRaises(binding.RawBindingError):
                            approved.check(candidate)

    def test_profile_calibration_unknown_is_an_exact_reference_choice(self):
        for approved_calibration in (None, 'f' * 64):
            approved = self.make_profile(calibration_bundle=approved_calibration)
            for supplied in (None, 'f' * 64):
                with self.subTest(approved=approved_calibration, supplied=supplied):
                    fields = binding_fields()
                    fields['calibration_bundle'] = supplied
                    candidate = binding.RawBinding(fields)
                    if supplied == approved_calibration:
                        self.assertIsNone(approved.check(candidate))
                    else:
                        with self.assertRaises(binding.RawBindingError):
                            approved.check(candidate)

    def test_profile_refs_are_fresh_copies_and_scalars_are_frozen(self):
        approved = profile()
        original = {name: getattr(approved, name) for name in ASSET_REFS}
        first, second = approved.refs, approved.refs
        self.assertEqual(first, original)
        self.assertIsNot(first, second)
        first.clear()
        self.assertEqual(approved.refs, original)
        for name in (*REFS, 'nn_enabled', 'jpeg_enabled'):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    setattr(approved, name, None)

    def test_profile_bool_choices_are_exact_booleans(self):
        for name in ('nn_enabled', 'jpeg_enabled'):
            for wrong in (0, 1, None, 'true'):
                with self.subTest(name=name, wrong=wrong):
                    with self.assertRaises(binding.RawBindingError):
                        self.make_profile(**{name: wrong})

    def test_profile_uses_the_same_device_clock_and_hash_grammar(self):
        for name, wrong in (('device_id', 'a b'), ('device_id', 'a' * 65),
                            ('clock_epoch', identifier(204).upper()),
                            ('model_id', 'g' * 64), ('parser_id', 'F' * 64),
                            ('graph_profile_sha256', None), ('format_table_sha256', 'f' * 63),
                            ('calibration_bundle', True)):
            with self.subTest(name=name, wrong=wrong):
                with self.assertRaises(binding.RawBindingError):
                    self.make_profile(**{name: wrong})

    def test_profile_model_parser_null_pair_follows_its_own_nn_choice(self):
        for original in (profile(nn_enabled=False), profile(nn_enabled=True)):
            for name in ('model_id', 'parser_id'):
                with self.subTest(nn_enabled=original.nn_enabled, name=name):
                    wrong = None if original.nn_enabled else 'f' * 64
                    with self.assertRaises(binding.RawBindingError):
                        self.make_profile(original, **{name: wrong})

    def test_profile_check_requires_a_binding_value_not_a_mutable_fields_dict(self):
        with self.assertRaises(binding.RawBindingError):
            profile().check(binding_fields())

    def test_profile_check_rejects_binding_subclass_before_custom_field_getter(self):
        calls = []

        class DerivedBinding(binding.RawBinding):
            @property
            def fields(self):
                calls.append('subclass fields getter')
                raise AssertionError('custom getter must not run')

        candidate = DerivedBinding(binding_fields())
        with self.assertRaises(binding.RawBindingError):
            profile().check(candidate)
        self.assertEqual(calls, [])

    def test_profile_scalar_type_rejection_does_not_call_numeric_or_metaclass_hooks(self):
        calls = []

        class TrapMeta(type):
            def __eq__(cls, other):
                calls.append('metaclass equality')
                raise AssertionError('custom type equality must not run')

            def __hash__(cls):
                calls.append('metaclass hash')
                raise AssertionError('custom type hash must not run')

        class Trap(metaclass=TrapMeta):
            def __index__(self):
                calls.append('numeric index')
                raise AssertionError('numeric coercion must not run')

            def __str__(self):
                calls.append('string conversion')
                raise AssertionError('string coercion must not run')

        for name in (*REFS, 'nn_enabled', 'jpeg_enabled'):
            with self.subTest(name=name):
                with self.assertRaises(binding.RawBindingError):
                    self.make_profile(**{name: Trap()})
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
