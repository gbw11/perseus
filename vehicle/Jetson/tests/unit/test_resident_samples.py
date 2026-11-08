"""상주 LIVE 어댑터의 원 source/bridge·owned 수명과 NN 연결 경계를 검증한다.

합성 bytes 시험이며 실제 OAK/추론률/주행 준비를 입증하지 않는다.
"""

from dataclasses import replace
from collections import deque
import unittest
from unittest.mock import Mock, patch

from raw_binding_fixtures import binding_fields
from raw_lease_fixtures import identifier, scope
from raw_slot_fixtures import descriptor
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.yolop_frames import NNInputStamp, NNPacket, RuntimeTensor
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.raw_leases import FilledInfo, WriteKey
from xenova_vehicle.runtime.raw_slot import ExpectedSlot, decode_owned
from xenova_vehicle.runtime.resident_samples import (
    CaptureAssembler, CaptureSample, decode_sample_slot, encode_sample, p0_stream_ids)
from xenova_vehicle.runtime import resident_workers


def sample(*, nn=True):
    binding = RawBinding(binding_fields())
    original = descriptor()
    tensors = tuple(RuntimeTensor(t['name'], t['data_type_code'], t['order_code'], len(t['dims']),
                    tuple(t['dims']), tuple(t['strides_bytes']), t['offset']) for t in original['nn']['layers'])
    rgb = RawFrame('rgb_video', 10, 50_000, 1100, 1105, 640, 360, 'NV12', bytes(345600), 30006)
    depth = RawFrame('depth_right', 71, 49_900, 1103, 1107, 640, 480, 'RAW16_LE', bytes(614400), 30005)
    input_stamp = NNInputStamp('nn_input', 10, 50_000, 1100, 1104, 320, 320, 'BGR888p', binding.sha256)
    output = NNPacket('nn_output', 10, 50_000, 1100, 1110, bytes(894848), tensors, binding.sha256)
    items = (rgb, depth, input_stamp, output) if nn else (rgb, depth)
    bridge = dict(j_before_us='1000', sdk_us='1000', j_after_us='1002')
    clocks = {item.stream: dict(bridge_sample=dict(bridge),
        sdk_host_timestamp_j_bracket_us=[str(item.sdk_us - 1), str(item.sdk_us + 3)]) for item in items}
    return binding, CaptureSample(rgb, depth, input_stamp if nn else None, output if nn else None, clocks)


class ResidentSamplesTests(unittest.TestCase):
    def encode(self, original, binding):
        key = WriteKey(replace(scope(), raw_binding_sha256=binding.sha256), 0, 1, identifier(400))
        encoded = encode_sample(original, key, binding)
        expected = ExpectedSlot(encoded.context, FilledInfo(key, encoded.payload_length,
                                                          encoded.descriptor_sha256, 2000))
        return encoded, decode_owned(encoded.raw, expected)

    def test_owned_roundtrip_preserves_original_sources_bridge_and_runtime_layout(self):
        binding, original = sample()
        encoded, owned = self.encode(original, binding)
        restored = decode_sample_slot(owned, binding)
        self.assertEqual(restored.rgb, original.rgb)
        self.assertEqual(restored.depth, original.depth)
        self.assertEqual(restored.nn_input, original.nn_input)
        self.assertEqual(restored.nn_output.payload, original.nn_output.payload)
        self.assertEqual(restored.nn_output.tensors, original.nn_output.tensors)
        self.assertEqual(restored.clocks, original.clocks)
        self.assertEqual(restored.depth.sequence, 71)
        self.assertEqual(restored.rgb.sequence, 10)
        self.assertEqual(encoded.context.stream_ids['nn_input0'], binding.fields['stream_instances']['nn_input0'])
        self.assertNotIn('nn_input', owned.descriptor()['streams'])

    def test_separate_jpeg_binding_keeps_full_hash_but_has_no_p0_plane(self):
        binding, original = sample()
        fields = binding.fields
        fields['stream_instances']['jpeg'] = identifier(9000)
        binding = RawBinding(fields)
        original = replace(original, nn_input=replace(original.nn_input, context_key=binding.sha256),
            nn_output=replace(original.nn_output, context_key=binding.sha256))
        encoded, owned = self.encode(original, binding)
        restored = decode_sample_slot(owned, binding)
        self.assertEqual(encoded.context.write_key.scope.raw_binding_sha256, binding.sha256)
        self.assertEqual(set(encoded.context.stream_ids), {'rgb_video', 'depth_right', 'nn_input0', 'nn_output0'})
        self.assertNotIn('jpeg', owned.descriptor()['streams'])
        self.assertNotIn('jpeg', owned.descriptor()['planes'])
        self.assertEqual(restored.rgb, original.rgb)
        self.assertEqual(restored.depth, original.depth)
        self.assertEqual(restored.nn_output.payload, original.nn_output.payload)
        self.assertEqual(restored.nn_output.context_key, binding.sha256)

    def test_p0_subset_is_owned_and_requires_the_validated_full_binding(self):
        binding, _ = sample()
        streams = p0_stream_ids(binding)
        streams['rgb_video'] = identifier(9001)
        self.assertNotEqual(p0_stream_ids(binding)['rgb_video'], streams['rgb_video'])
        with self.assertRaises(ValueError):
            p0_stream_ids(binding.fields)

    def test_configured_nn_can_be_absent_during_capture_warmup(self):
        binding, original = sample(nn=False)
        encoded, owned = self.encode(original, binding)
        self.assertTrue(encoded.context.nn_configured)
        restored = decode_sample_slot(owned, binding)
        self.assertIsNone(restored.nn_input)
        self.assertIsNone(restored.nn_output)
        self.assertEqual(set(owned.descriptor()['streams']), {'rgb_video', 'depth_right'})
        self.assertEqual(len(owned.payload_bytes()), 960000)

    def test_full_binding_context_mismatch_is_rejected(self):
        binding, original = sample()
        other_fields = binding.fields
        other_fields['pipeline_instance'] = identifier(3000)
        other = RawBinding(other_fields)
        key = WriteKey(replace(scope(), raw_binding_sha256=other.sha256), 0, 1, identifier(400))
        with self.assertRaises(ValueError):
            encode_sample(original, key, binding)
        _, owned = self.encode(original, binding)
        with self.assertRaises(ValueError):
            decode_sample_slot(owned, other)

    def test_partial_nn_and_changed_nn_context_are_rejected(self):
        binding, original = sample()
        with self.assertRaises(ValueError):
            self.encode(replace(original, nn_input=None), binding)
        changed = replace(original.nn_input, context_key='e' * 64)
        with self.assertRaises(ValueError):
            self.encode(replace(original, nn_input=changed), binding)

    def test_unknown_bridge_remains_unknown_and_does_not_become_fresh(self):
        binding, original = sample(nn=False)
        clocks = {name: dict(bridge_sample=None, sdk_host_timestamp_j_bracket_us=None)
                  for name in original.clocks}
        original = replace(original, clocks=clocks)
        _, owned = self.encode(original, binding)
        restored = decode_sample_slot(owned, binding)
        self.assertEqual(restored.clocks, clocks)
        self.assertFalse(restored.fresh(1200))

    def test_freshness_uses_original_lower_bound_including_exact_half_second(self):
        _, original = sample(nn=False)
        self.assertFalse(original.fresh(1098))
        self.assertTrue(original.fresh(1099 + 500000))
        self.assertFalse(original.fresh(1099 + 500001))

    def test_assembler_combines_exact_nn_parent_and_independent_depth(self):
        binding, original = sample()
        assembler = CaptureAssembler(binding, 10000, 1000)
        assembler.bridge.update(dict(j_before_us=1000, sdk_us=1000, j_after_us=1002))
        self.assertEqual(assembler.observe(original.rgb), [])
        pairs = assembler.observe(original.depth)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].depth.sequence, 71)
        self.assertIsNone(pairs[0].nn_output)
        assembler.observe(original.nn_input)
        assembler.observe(original.nn_output)
        assembled = assembler.latest(1200)
        self.assertIs(assembled.rgb, original.rgb)
        self.assertIs(assembled.nn_input, original.nn_input)
        self.assertIs(assembled.nn_output, original.nn_output)
        self.assertIs(assembled.depth, original.depth)
        # P0 원 stamp는 bridge/SDK→J interval을 보존한다. 추가 미수락 진단은 별도다.
        for stream, expected in original.clocks.items():
            self.assertEqual(assembled.clocks[stream]['bridge_sample'], expected['bridge_sample'])
            self.assertEqual(assembled.clocks[stream]['sdk_host_timestamp_j_bracket_us'],
                             expected['sdk_host_timestamp_j_bracket_us'])
            self.assertIsNone(assembled.clocks[stream]['oak_sync_error_bound_us'])
            self.assertIsNone(assembled.clocks[stream]['readout_error_bound_us'])
            self.assertFalse(assembled.clocks[stream]['metric_time_qualified'])
        self.assertIsNone(assembler.latest(600000))

    def test_prequeue_source_never_counts_as_new_pair(self):
        binding, original = sample(nn=False)
        assembler = CaptureAssembler(binding, 10000, 1100)
        assembler.bridge.update(dict(j_before_us=1000, sdk_us=1000, j_after_us=1002))
        self.assertEqual(assembler.observe(original.rgb), [])
        self.assertEqual(assembler.observe(original.depth), [])
        self.assertIsNone(assembler.latest(1200))
        self.assertEqual(assembler.tracker.candidate_pairs, 0)

    def test_new_rgb_depth_is_not_held_by_old_nn_and_late_nn_is_still_published(self):
        binding,original=sample()
        assembler=CaptureAssembler(binding,10000,1000)
        assembler.bridge.update(dict(j_before_us=1000,sdk_us=1000,j_after_us=1002))
        for item in (original.rgb,original.depth,original.nn_input,original.nn_output):
            assembler.observe(item)
        first=assembler.latest(1200)
        self.assertIsNotNone(first.nn_output)
        assembler.mark_published(first)
        self.assertIsNone(assembler.latest(1200))
        def advance(item):
            return replace(item,sequence=item.sequence+1,device_us=item.device_us+10_000,
                           sdk_us=item.sdk_us+10_000,received_j_us=item.received_j_us+10_000)
        rgb,depth,nn_input,nn_output=map(advance,(original.rgb,original.depth,original.nn_input,original.nn_output))
        assembler.observe(rgb); assembler.observe(depth)
        raw=assembler.latest(11_200)
        self.assertIs(raw.rgb,rgb)
        self.assertIsNone(raw.nn_output)
        assembler.mark_published(raw)
        self.assertIsNone(assembler.latest(11_200))
        assembler.observe(nn_input); assembler.observe(nn_output)
        late=assembler.latest(11_200)
        self.assertIs(late.rgb,rgb)
        self.assertIs(late.nn_output,nn_output)
        assembler.mark_published(late)
        self.assertIsNone(assembler.latest(11_200))


class ResidentControlDraftTests(unittest.TestCase):
    """busy는 원 완료 사실/기한을 갱신하지 않으며 STOP은 새 수락을 막는다."""

    def actor(self):
        actor = object.__new__(resident_workers._Actor)
        actor.link = Mock()
        actor.stop = None
        actor.deferred = deque()
        actor.drain_sent = Mock()
        actor._receive = Mock(return_value=[])
        return actor

    def test_busy_retries_keep_original_completed_time_expiry_and_request(self):
        actor = self.actor()
        accepted = object()
        deferred = object()
        actor.link.send.side_effect = [None, None, accepted]
        actor._receive.side_effect = [[deferred], []]
        with patch.object(resident_workers, 'j_us', return_value=150), patch.object(resident_workers.time, 'sleep'):
            result = actor.send_required('SLOT', {'op': 'RELEASE'}, domain='RAW', revision=1,
                request_id=identifier(600), issued_us=100, expires_us=200)
        self.assertIs(result, accepted)
        self.assertEqual(len(actor.link.send.call_args_list), 3)
        for call in actor.link.send.call_args_list:
            self.assertEqual(call.kwargs, dict(domain='RAW', revision=1, request_id=identifier(600),
                                              issued_us=100, expires_us=200))
        self.assertEqual(list(actor.deferred), [deferred])

    def test_stop_during_busy_aborts_without_a_new_packet_or_new_deadline(self):
        actor = self.actor()
        actor.link.send.return_value = None

        def stop_arrives():
            actor.stop = object()
            return []

        actor._receive.side_effect = stop_arrives
        with patch.object(resident_workers, 'j_us', return_value=150), patch.object(resident_workers.time, 'sleep'):
            with self.assertRaises(resident_workers._StopWork):
                actor.send_required('SLOT', {'op': 'FILLED'}, issued_us=100, expires_us=200,
                                    request_id=identifier(600))
        self.assertEqual(actor.link.send.call_count, 1)
        self.assertEqual(actor.link.send.call_args.kwargs['issued_us'], 100)
        self.assertEqual(actor.link.send.call_args.kwargs['expires_us'], 200)


if __name__ == '__main__':
    unittest.main()
