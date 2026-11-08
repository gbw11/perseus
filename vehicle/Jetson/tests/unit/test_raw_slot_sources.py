"""P0 소유 slot의 원 출처·시각·NN 결합을 합성 사건으로 검사한다.

등록 표와 원 FILLED를 외부 기대값으로 고정한다. SDK·센서·clock·실제 메모리
관측은 생성하지 않으며 codec 통과를 freshness나 NN 준비로 해석하지 않는다.
"""

from copy import deepcopy
import itertools
import unittest

from xenova_vehicle.runtime import raw_slot as codec
from raw_slot_fixtures import bundle, context, expected, payload, raw_record


MAX = 2**64 - 1
NN_REFERENCES = {'rgb_video': 'rgb_parent_key', 'nn_input0': 'input_key', 'nn_output0': 'output_key'}


class RawSlotSourceTests(unittest.TestCase):
    def valid_record(self, ctx, desc, *, completed=2000):
        body = payload(desc)
        encoding = codec.encode_owned(ctx, desc, body)
        self.assertEqual(encoding.descriptor(), desc)
        decoded = codec.decode_owned(encoding.raw, expected(encoding, completed_us=completed))
        self.assertEqual(decoded.descriptor(), desc)
        independent_raw, original = raw_record(ctx, desc, body, completed_us=completed)
        independent = codec.decode_owned(independent_raw, original)
        self.assertEqual(independent.descriptor(), desc)
        return encoding, decoded

    def invalid_record(self, ctx, desc, *, completed=2000):
        body = payload(desc)
        for make in (codec.encode_owned, codec.SlotEncoding):
            with self.subTest(api=make.__name__):
                with self.assertRaises(codec.SlotCodecError):
                    make(ctx, desc, body)
        # 제품 encoder를 우회하여 올바른 CRC/SHA를 가진 schema 음성을 만든다.
        raw, original = raw_record(ctx, desc, body, completed_us=completed)
        for read in (codec.decode_owned, codec.OwnedSlot):
            with self.subTest(api=read.__name__):
                with self.assertRaises(codec.SlotCodecError):
                    read(raw, original)

    def changed_key(self, desc, stream, index, value, *, update_reference=True):
        key = list(desc['streams'][stream]['source_key'])
        key[index] = value
        desc['streams'][stream]['source_key'] = key
        if update_reference and desc['nn'] is not None and stream in NN_REFERENCES:
            desc['nn'][NN_REFERENCES[stream]] = list(key)

    def bridge(self, stamp, *, frame_sdk, before, anchor, after, interval):
        stamp['sdk_us'] = str(frame_sdk)
        stamp['bridge_sample'] = dict(j_before_us=str(before), sdk_us=str(anchor), j_after_us=str(after))
        stamp['sdk_host_j_interval_us'] = [str(value) for value in interval]

    def test_registered_stream_table_is_external_to_descriptor(self):
        ctx, base, _ = bundle()
        original_table = dict(ctx.stream_ids)
        for stream in base['streams']:
            with self.subTest(stream=stream):
                desc = deepcopy(base)
                self.changed_key(desc, stream, 0, '10000000-0000-4000-8000-0000000000ff')
                self.invalid_record(ctx, desc)
                self.assertEqual(dict(ctx.stream_ids), original_table)

    def test_swapped_registered_uuid_scopes_are_not_aliases(self):
        ctx, base, _ = bundle()
        for left, right in (('rgb_video', 'depth_right'), ('nn_input0', 'nn_output0')):
            with self.subTest(left=left, right=right):
                desc = deepcopy(base)
                self.changed_key(desc, left, 0, ctx.stream_ids[right])
                self.changed_key(desc, right, 0, ctx.stream_ids[left])
                self.invalid_record(ctx, desc)

    def test_context_requires_exact_configured_table_and_unique_uuids(self):
        ctx = context()
        for configured, table in (
                (True, {name: ctx.stream_ids[name] for name in ('rgb_video', 'depth_right')}),
                (False, dict(ctx.stream_ids)),
                (True, {**dict(ctx.stream_ids), 'nn_input0': ctx.stream_ids['rgb_video']})):
            with self.subTest(configured=configured, table=table):
                with self.assertRaises(codec.SlotCodecError):
                    codec.P0Context(ctx.write_key, table, configured)

    def test_nn_three_full_key_references_match_their_own_stamps(self):
        ctx, base, _ = bundle()
        for stream, reference in NN_REFERENCES.items():
            for component in range(3):
                with self.subTest(stream=stream, component=component):
                    desc = deepcopy(base)
                    wrong = list(desc['nn'][reference])
                    wrong[component] = ('10000000-0000-4000-8000-0000000000ff' if component == 0
                                        else str(int(wrong[component]) + 1))
                    desc['nn'][reference] = wrong
                    self.invalid_record(ctx, desc)

    def test_self_consistent_nn_keys_still_require_rgb_sequence_and_device(self):
        ctx, base, _ = bundle()
        for stream in NN_REFERENCES:
            for component in (1, 2):
                with self.subTest(stream=stream, component=component):
                    desc = deepcopy(base)
                    self.changed_key(desc, stream, component,
                                     str(int(desc['streams'][stream]['source_key'][component]) + 1))
                    self.invalid_record(ctx, desc)

    def test_nn_sdk_stamp_equality_is_independent_of_full_key_equality(self):
        ctx, base, _ = bundle()
        for stream in NN_REFERENCES:
            with self.subTest(stream=stream):
                desc = deepcopy(base)
                desc['streams'][stream]['sdk_us'] = '1901'
                if stream == 'rgb_video':
                    desc['streams'][stream]['sdk_host_j_interval_us'] = ['900', '904']
                self.invalid_record(ctx, desc)

    def test_depth_has_independent_sequence_device_sdk_and_unknown_bridge(self):
        ctx, desc, _ = bundle()
        depth = desc['streams']['depth_right']
        self.assertNotEqual(depth['source_key'][1:], desc['streams']['rgb_video']['source_key'][1:])
        self.assertNotEqual(depth['sdk_us'], desc['streams']['rgb_video']['sdk_us'])
        _, result = self.valid_record(ctx, desc)
        saved = result.descriptor()['streams']['depth_right']
        self.assertIsNone(saved['bridge_sample'])
        self.assertIsNone(saved['sdk_host_j_interval_us'])
        self.assertIsNone(saved['capture_j_interval_us'])

    def test_nn_presence_and_external_configuration_matrix(self):
        for configured, present in itertools.product((False, True), repeat=2):
            with self.subTest(configured=configured, present=present):
                ctx, desc, _ = bundle(nn_present=present, nn_configured=configured)
                before = dict(ctx.stream_ids)
                if present and not configured:
                    self.invalid_record(ctx, desc)
                else:
                    self.valid_record(ctx, desc)
                self.assertEqual(dict(ctx.stream_ids), before)
                self.assertEqual(len(ctx.stream_ids), 4 if configured else 2)

    def test_every_partial_nn_presence_combination_is_rejected(self):
        ctx, base, _ = bundle()
        for nn, input_stamp, output_stamp, plane in itertools.product((False, True), repeat=4):
            if len({nn, input_stamp, output_stamp, plane}) == 1:
                continue
            with self.subTest(nn=nn, input_stamp=input_stamp, output_stamp=output_stamp, plane=plane):
                desc = deepcopy(base)
                if not nn:
                    desc['nn'] = None
                if not input_stamp:
                    del desc['streams']['nn_input0']
                if not output_stamp:
                    del desc['streams']['nn_output0']
                if not plane:
                    del desc['planes']['nn_output0']
                self.invalid_record(ctx, desc)

    def test_cross_stream_arrival_reversal_and_exact_input_stamps_are_valid(self):
        ctx, desc, _ = bundle()
        self.assertLess(int(desc['streams']['nn_output0']['received_j_us']),
                        int(desc['streams']['rgb_video']['received_j_us']))
        for stream, received in (('nn_input0', '1501'), ('nn_output0', '1500'),
                                 ('rgb_video', '1502'), ('depth_right', '1499')):
            desc['streams'][stream]['received_j_us'] = received
        _, result = self.valid_record(ctx, desc, completed=1502)
        self.assertEqual(result.descriptor(), desc)

    def test_nn_output_received_time_does_not_replace_original_sdk_stamp(self):
        ctx, desc, _ = bundle()
        desc['streams']['nn_output0']['received_j_us'] = '1999'
        _, result = self.valid_record(ctx, desc)
        saved = result.descriptor()['streams']['nn_output0']
        self.assertEqual(saved['sdk_us'], '1900')
        self.assertEqual(saved['source_key'], desc['nn']['output_key'])
        self.assertEqual(saved['received_j_us'], '1999')
        self.assertIsNone(saved['capture_j_interval_us'])

    def test_source_u64_sequence_and_device_endpoints_are_not_slot_generation(self):
        for number in ('0', str(MAX)):
            with self.subTest(number=number):
                ctx, desc, _ = bundle(nn_present=False)
                self.changed_key(desc, 'rgb_video', 1, number)
                self.changed_key(desc, 'rgb_video', 2, number)
                self.valid_record(ctx, desc)
                self.assertEqual(ctx.write_key.generation, 1)

    def test_unknown_bridge_is_preserved_without_clock_or_interval_fill(self):
        ctx, desc, _ = bundle()
        for stamp in desc['streams'].values():
            stamp['bridge_sample'] = None
            stamp['sdk_host_j_interval_us'] = None
        _, result = self.valid_record(ctx, desc)
        for stamp in result.descriptor()['streams'].values():
            self.assertIsNone(stamp['bridge_sample'])
            self.assertIsNone(stamp['sdk_host_j_interval_us'])
            self.assertIsNone(stamp['capture_j_interval_us'])

    def test_bridge_and_derived_interval_must_be_present_or_unknown_together(self):
        ctx, base, _ = bundle()
        for stream in base['streams']:
            for missing in ('bridge_sample', 'sdk_host_j_interval_us'):
                with self.subTest(stream=stream, missing=missing):
                    desc = deepcopy(base)
                    stamp = desc['streams'][stream]
                    stamp['bridge_sample'] = dict(j_before_us='1000', sdk_us='2000', j_after_us='1002')
                    stamp['sdk_host_j_interval_us'] = ['899', '903']
                    stamp[missing] = None
                    self.invalid_record(ctx, desc)

    def test_bridge_can_be_recorded_for_each_stream_without_nn_exposure(self):
        ctx, desc, _ = bundle()
        for stream in ('nn_input0', 'nn_output0'):
            self.bridge(desc['streams'][stream], frame_sdk=1900, before=1000,
                        anchor=2000, after=1002, interval=(899, 903))
        self.bridge(desc['streams']['depth_right'], frame_sdk=1850, before=1000,
                    anchor=2000, after=1002, interval=(849, 853))
        self.valid_record(ctx, desc)

    def test_bridge_quantization_requires_both_exact_one_microsecond_margins(self):
        ctx, base, _ = bundle(nn_present=False)
        self.valid_record(ctx, base)
        for interval in ((900, 903), (899, 902), (898, 903), (899, 904), (900, 902)):
            with self.subTest(interval=interval):
                desc = deepcopy(base)
                desc['streams']['rgb_video']['sdk_host_j_interval_us'] = [str(v) for v in interval]
                self.invalid_record(ctx, desc)

    def test_bridge_clock_domains_are_not_directly_ordered(self):
        ctx, desc, _ = bundle(nn_present=False)
        sample = desc['streams']['rgb_video']['bridge_sample']
        self.assertGreater(int(sample['sdk_us']), int(sample['j_after_us']))
        self.valid_record(ctx, desc)

    def test_bridge_age_inclusive_zero_and_one_second_boundaries(self):
        for age in (-1, 0, 1_000_000, 1_000_001):
            with self.subTest(age=age):
                ctx, desc, _ = bundle(nn_present=False)
                received = 1002 + age
                desc['streams']['rgb_video']['received_j_us'] = str(received)
                if 0 <= age <= 1_000_000:
                    self.valid_record(ctx, desc, completed=max(2000, received))
                else:
                    self.invalid_record(ctx, desc, completed=max(2000, received))

    def test_zero_width_bridge_is_valid_but_reversed_bracket_is_not(self):
        ctx, desc, _ = bundle(nn_present=False)
        self.bridge(desc['streams']['rgb_video'], frame_sdk=1900, before=1002,
                    anchor=2000, after=1002, interval=(901, 903))
        self.valid_record(ctx, desc)
        self.bridge(desc['streams']['rgb_video'], frame_sdk=1900, before=1003,
                    anchor=2000, after=1002, interval=(902, 903))
        self.invalid_record(ctx, desc)

    def test_intermediate_signed_arithmetic_may_exceed_u64_if_final_interval_fits(self):
        ctx, desc, _ = bundle(nn_present=False)
        # 산술 스트레스 입력이다. 실제 카메라 chronology를 재현한 양성이 아니다.
        self.bridge(desc['streams']['rgb_video'], frame_sdk=MAX, before=1000,
                    anchor=MAX, after=1002, interval=(999, 1003))
        self.valid_record(ctx, desc)

    def test_final_interval_zero_is_valid_but_negative_lower_cannot_be_clamped(self):
        ctx, desc, _ = bundle(nn_present=False)
        self.bridge(desc['streams']['rgb_video'], frame_sdk=10, before=91,
                    anchor=100, after=93, interval=(0, 4))
        self.valid_record(ctx, desc)
        desc['streams']['rgb_video']['bridge_sample']['j_before_us'] = '90'
        self.invalid_record(ctx, desc)

    def test_final_interval_max_is_valid_but_overflow_upper_cannot_be_clamped(self):
        ctx, desc, _ = bundle(nn_present=False)
        # 최종 정수 표현 경계만 검사하며 물리 source 시간 자격을 만들지 않는다.
        self.bridge(desc['streams']['rgb_video'], frame_sdk=MAX, before=99,
                    anchor=100, after=99, interval=(MAX - 2, MAX))
        self.valid_record(ctx, desc)
        desc['streams']['rgb_video']['bridge_sample']['j_after_us'] = '100'
        self.invalid_record(ctx, desc)

    def test_encoder_has_no_completion_but_decoder_checks_each_original_arrival(self):
        ctx, base, _ = bundle()
        for stream in base['streams']:
            for received, valid in ((2000, True), (2001, False)):
                with self.subTest(stream=stream, received=received):
                    desc = deepcopy(base)
                    desc['streams'][stream]['received_j_us'] = str(received)
                    encoding = codec.encode_owned(ctx, desc, payload(desc))
                    self.assertEqual(encoding.descriptor(), desc)
                    original = expected(encoding, completed_us=2000)
                    for read in (codec.decode_owned, codec.OwnedSlot):
                        with self.subTest(api=read.__name__):
                            if valid:
                                self.assertEqual(read(encoding.raw, original).descriptor(), desc)
                            else:
                                with self.assertRaises(codec.SlotCodecError):
                                    read(encoding.raw, original)

    def test_original_filled_completion_is_not_replaced_by_latest_arrival(self):
        ctx, desc, _ = bundle()
        encoding = codec.encode_owned(ctx, desc, payload(desc))
        original = expected(encoding, completed_us=1509)
        self.assertEqual(original.original_filled_info.completed_us, 1509)
        with self.assertRaises(codec.SlotCodecError):
            codec.decode_owned(encoding.raw, original)
        self.assertEqual(original.original_filled_info.completed_us, 1509)
        self.assertEqual(codec.decode_owned(encoding.raw, expected(encoding, completed_us=1510)).descriptor(), desc)

    def test_measured_bridge_after_is_bounded_by_original_completion(self):
        ctx, desc, _ = bundle(nn_present=False)
        self.bridge(desc['streams']['rgb_video'], frame_sdk=1900, before=2000,
                    anchor=1900, after=2001, interval=(1999, 2002))
        desc['streams']['rgb_video']['received_j_us'] = '2001'
        encoding = codec.encode_owned(ctx, desc, payload(desc))
        with self.assertRaises(codec.SlotCodecError):
            codec.decode_owned(encoding.raw, expected(encoding, completed_us=2000))
        self.assertEqual(codec.decode_owned(encoding.raw, expected(encoding, completed_us=2001)).descriptor(), desc)

    def test_derived_interval_upper_is_not_an_actual_post_completion_measurement(self):
        ctx, desc, _ = bundle(nn_present=False)
        self.bridge(desc['streams']['rgb_video'], frame_sdk=1900, before=2000,
                    anchor=1900, after=2000, interval=(1999, 2001))
        desc['streams']['rgb_video']['received_j_us'] = '2000'
        self.valid_record(ctx, desc, completed=2000)

    def test_capture_interval_is_null_only_for_every_stream(self):
        ctx, base, _ = bundle()
        for stream in base['streams']:
            with self.subTest(stream=stream):
                desc = deepcopy(base)
                desc['streams'][stream]['capture_j_interval_us'] = ['899', '903']
                self.invalid_record(ctx, desc)

    def test_nn_exposure_is_null_only_including_numeric_zero(self):
        ctx, base, _ = bundle()
        for stream in ('nn_input0', 'nn_output0'):
            for exposure in (0, 1, 1_000_000):
                with self.subTest(stream=stream, exposure=exposure):
                    desc = deepcopy(base)
                    desc['streams'][stream]['exposure_us'] = exposure
                    self.invalid_record(ctx, desc)

    def test_rgb_depth_exposure_preserves_null_zero_and_upper_bound(self):
        for exposure in (None, 0, 1_000_000):
            with self.subTest(exposure=exposure):
                ctx, desc, _ = bundle(nn_present=False)
                for stream in ('rgb_video', 'depth_right'):
                    desc['streams'][stream]['exposure_us'] = exposure
                _, decoded = self.valid_record(ctx, desc)
                for stamp in decoded.descriptor()['streams'].values():
                    self.assertEqual(stamp['exposure_us'], exposure)
                    self.assertIsNone(stamp['capture_j_interval_us'])

    def test_rgb_depth_exposure_rejects_values_outside_declared_bounds(self):
        ctx, base, _ = bundle(nn_present=False)
        for stream in ('rgb_video', 'depth_right'):
            for exposure in (-1, 1_000_001):
                with self.subTest(stream=stream, exposure=exposure):
                    desc = deepcopy(base)
                    desc['streams'][stream]['exposure_us'] = exposure
                    self.invalid_record(ctx, desc)


if __name__ == '__main__':
    unittest.main()
