"""probe의 원 bytes·채널/패딩 차이·진단 graph 변경 범위를 검사한다.

NumPy/OpenCV reference와 fake SDK의 host 선언만 검사한다. 실제 OAK pixels·보간
동치·모델 정확도는 이 단위 시험의 결과가 아니며 hardware CLI를 따로 실행해야 한다.
"""

from datetime import timedelta
import hashlib
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, call

from xenova_vehicle.simulation import preprocessing_probe as subject
from xenova_vehicle.simulation.capture import _nv12

import test_sim_nn as fixture


def message(payload):
    import numpy as np
    sdk = MagicMock()
    sdk.getData.return_value = payload
    sdk.getSequenceNum.return_value = 7
    sdk.getTimestampDevice.return_value = timedelta(microseconds=7_000_000)
    sdk.getTimestamp.return_value = timedelta(microseconds=7_000_000)
    sdk.getWidth.return_value = sdk.getHeight.return_value = 320
    sdk.getType.return_value = 8
    sdk.getRaw.return_value = NS(fb=NS(width=320, height=320, stride=320,
        p1Offset=0, p2Offset=102400, p3Offset=204800))
    sdk.getFrame.side_effect = lambda *, copy: np.frombuffer(payload, dtype=np.uint8).reshape(3, 320, 320).copy()
    return sdk


class SimPreprocessingProbeTests(unittest.TestCase):
    def reference(self):
        gold = subject.gold_bgr()
        raw = _nv12(gold)
        return gold, raw, subject.reference_from_nv12(raw)

    def test_owned_color_gold_and_original_nv12_reference_keep_bgr_and_black_letterbox(self):
        import numpy as np
        gold, raw, reference = self.reference()
        original = gold.tobytes()
        self.assertEqual(gold.shape, (360, 640, 3))
        self.assertFalse(gold.flags.writeable)
        with self.assertRaises(ValueError):
            gold.setflags(write=True)
        self.assertEqual(len(raw), 345600)
        self.assertEqual(len(reference), 307200)
        planar = np.frombuffer(reference, dtype=np.uint8).reshape(3, 320, 320)
        self.assertFalse(np.any(planar[:, :70]))
        self.assertFalse(np.any(planar[:, 250:]))
        self.assertEqual(int(np.argmax(planar[:, 90, 20])), 0)
        self.assertEqual(int(np.argmax(planar[:, 90, 100])), 2)
        facts = subject.compare_planar(reference, reference)
        self.assertTrue(facts['exact_byte_equal'])
        self.assertEqual(facts['max_abs_error'], 0)
        self.assertEqual(facts['mean_abs_error'], 0.)
        self.assertEqual(facts['padding_top_nonzero'], 0)
        self.assertIsNone(facts['tolerance'])
        self.assertFalse(facts['normalization_applied'])
        self.assertEqual(gold.tobytes(), original)
        self.assertEqual(hashlib.sha256(raw).digest(), hashlib.sha256(_nv12(gold)).digest())
        self.assertIs(subject.host_input_from_nv12(raw, 'NV12'), raw)
        control = subject.host_input_from_nv12(raw, 'BGR888p')
        self.assertEqual(len(control), 691200)
        import cv2
        decoded = cv2.cvtColor(np.frombuffer(raw, dtype=np.uint8).reshape(540, 640), cv2.COLOR_YUV2BGR_NV12)
        self.assertEqual(control, decoded.transpose(2, 0, 1).tobytes())
        self.assertEqual(hashlib.sha256(subject.reference_from_nv12(raw)).digest(), hashlib.sha256(reference).digest())
        with self.assertRaises(ValueError):
            subject.host_input_from_nv12(raw, 'RGB888p')

    def test_channel_swap_is_reported_by_six_permutations_without_repair_or_tolerance(self):
        import numpy as np
        _, _, reference = self.reference()
        planar = np.frombuffer(reference, dtype=np.uint8).reshape(3, 320, 320)
        observed = planar[[2, 1, 0]].tobytes()
        original_observed = observed
        facts = subject.compare_planar(observed, reference)
        self.assertFalse(facts['exact_byte_equal'])
        self.assertGreater(facts['max_abs_error'], 0)
        self.assertEqual(len(facts['channel_permutations']), 6)
        best = min(facts['channel_permutations'], key=lambda row: row['content_mean_abs_error'])
        self.assertEqual(best['observed_indices_for_BGR'], [2, 1, 0])
        self.assertEqual(best['content_mean_abs_error'], 0.)
        self.assertEqual(observed, original_observed)
        self.assertIsNone(facts['tolerance'])

    def test_wrong_padding_and_shape_cannot_be_hidden_by_equal_content_roi(self):
        _, _, reference = self.reference()
        changed = bytearray(reference)
        changed[0] = 17
        facts = subject.compare_planar(bytes(changed), reference)
        self.assertFalse(facts['exact_byte_equal'])
        self.assertEqual(facts['padding_top_nonzero'], 1)
        self.assertEqual(facts['padding_bottom_nonzero'], 0)
        self.assertEqual(facts['content_max_abs_error'], 0)
        self.assertEqual(facts['max_abs_error'], 17)
        self.assertEqual(facts['per_channel'][0]['channel'], 'B')
        for invalid in (reference[:-1], reference+b'\0', bytearray(reference)):
            with self.subTest(kind=type(invalid), size=len(invalid)), self.assertRaises(ValueError):
                subject.compare_planar(invalid, reference)
        with self.assertRaises(ValueError):
            subject.compare_planar(reference, bytes(changed))

    def test_full_passthrough_owns_raw_fb_metadata_and_keeps_bad_layout_visible(self):
        original = bytearray(subject.PLANAR_BYTES)
        sdk = message(original)
        owned = subject.read_passthrough(sdk, 8_000_000)
        original[0] = 255
        self.assertEqual(owned.payload[0], 0)
        self.assertEqual(owned.key, (7, 7_000_000, 7_000_000))
        self.assertTrue(owned.metadata['shape_type_length_ok'])
        self.assertTrue(owned.metadata['sdk_canonical_raw_equal'])
        self.assertEqual(owned.canonical_payload, owned.payload)
        sdk.getFrame.assert_called_once_with(copy=True)
        fields = owned.metadata
        fields['fb']['stride'] = 123
        self.assertEqual(owned.metadata['fb']['stride'], 320)
        sdk.getRaw.return_value.fb.p2Offset = sdk.getRaw.return_value.fb.p3Offset = 869508864
        changed = subject.read_passthrough(sdk, 8_000_001)
        self.assertTrue(changed.metadata['sdk_canonical_raw_equal'])
        self.assertEqual(changed.metadata['fb']['p2Offset'], 869508864)
        self.assertEqual(changed.metadata['fb']['p3Offset'], 869508864)
        self.assertEqual(changed.metadata['fb_offset_semantics'], 'NOT_INTERPRETED_AS_HOST_PAYLOAD_OFFSETS')
        sdk.getWidth.return_value = 319
        self.assertFalse(subject.read_passthrough(sdk, 8_000_002).metadata['shape_type_length_ok'])
        sdk = message(bytearray(subject.PIXEL_COPY_CAP+1))
        with self.assertRaises(ValueError):
            subject.read_passthrough(sdk, 8_000_000)
        sdk.getRaw.assert_not_called()

    def test_sdk_canonical_pixel_mismatch_and_bad_shape_are_preserved_without_offset_inference(self):
        import numpy as np
        sdk = message(bytes(subject.PLANAR_BYTES))
        canonical = np.zeros((3, 320, 320), dtype=np.uint8)
        canonical[2, 100, 100] = 17
        sdk.getFrame.side_effect = None
        sdk.getFrame.return_value = canonical
        owned = subject.read_passthrough(sdk, 8_000_000)
        self.assertFalse(owned.metadata['sdk_canonical_raw_equal'])
        self.assertEqual(owned.payload, bytes(subject.PLANAR_BYTES))
        self.assertEqual(owned.canonical_payload, canonical.tobytes())
        self.assertEqual(owned.metadata['sdk_get_frame_copy']['shape'], [3, 320, 320])
        self.assertEqual(owned.metadata['sdk_get_frame_copy']['strides'], [102400, 320, 1])
        sdk.getFrame.return_value = np.zeros((320, 320, 3), dtype=np.uint8)
        invalid = subject.read_passthrough(sdk, 8_000_001)
        self.assertIsNone(invalid.metadata['sdk_canonical_raw_equal'])
        self.assertIn('sdk_get_frame_error', invalid.metadata)
        self.assertIsNone(invalid.canonical_payload)
        self.assertEqual(invalid.payload, bytes(subject.PLANAR_BYTES))

    def test_diagnostic_variant_changes_only_identified_passthrough_flag_after_original_builder(self):
        f = fixture.fixture()
        f.passthrough.getStreamName.return_value = 'nn_input'
        f.output.getStreamName.return_value = 'nn_output'
        f.passthrough.getMetadataOnly.side_effect = [True, False]
        pipeline, blob, _, receipt = subject.diagnostic_pipeline(f.dai, f.asset)
        self.assertIs(pipeline, f.pipeline)
        self.assertIs(blob, f.blob)
        self.assertEqual(f.pipeline.create.call_count, 5)
        f.network.setBlob.assert_called_once_with(f.blob)
        self.assertEqual(f.passthrough.setMetadataOnly.call_args_list, [call(True), call(False)])
        f.output.setMetadataOnly.assert_called_once_with(False)
        self.assertEqual(receipt['mutation'], dict(node_id=3, stream_name='nn_input',
            property='metadataOnly', before=True, after=False))
        self.assertTrue(receipt['diagnostic_variant_only'])
        self.assertFalse(receipt['production_graph_unchanged_claim'])
        self.assertTrue(receipt['original_host_graph_declaration']['declared_settings']['passthrough_metadata_only'])
        self.assertEqual(receipt['verification'], 'HOST_GETTER_ONLY_NOT_DEVICE_READBACK')
        self.assertEqual(receipt['input_format'], 'NV12')
        self.assertEqual(len(receipt['graph_deltas']), 1)
        baseline_delta = receipt['graph_delta_sha256']
        f = fixture.fixture()
        f.passthrough.getStreamName.return_value = 'nn_input'
        f.output.getStreamName.return_value = 'nn_output'
        f.passthrough.getMetadataOnly.side_effect = [True, False]
        f.host.getStreamName.return_value = subject.HOST_STREAM
        f.host.getMaxDataSize.side_effect = [345600, 691200]
        _, _, _, control = subject.diagnostic_pipeline(f.dai, f.asset, input_format='BGR888p')
        self.assertEqual(f.host.setMaxDataSize.call_args_list, [call(345600), call(691200)])
        self.assertEqual(control['input_format'], 'BGR888p')
        self.assertEqual(control['original_host_graph_declaration']['declared_settings']['input_type'], 'NV12')
        self.assertTrue(control['input_message_type_changed'])
        self.assertEqual(len(control['graph_deltas']), 2)
        self.assertNotEqual(control['graph_delta_sha256'], baseline_delta)
        f.network.setBlob.assert_called_once_with(f.blob)
        with self.assertRaises(ValueError):
            subject.diagnostic_pipeline(f.dai, f.asset, input_format='RGB888p')
        f = fixture.fixture()
        f.passthrough.getStreamName.return_value = 'nn_input'
        f.output.getStreamName.return_value = 'nn_output'
        f.passthrough.getMetadataOnly.side_effect = [True, True]
        with self.assertRaises(ValueError):
            subject.diagnostic_pipeline(f.dai, f.asset)


if __name__ == '__main__':
    unittest.main()
