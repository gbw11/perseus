"""단일 Device·동일 RGB 입력·queue와 모델 적재 수명을 fake SDK에서 검증한다."""

from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum
import json
from types import ModuleType, SimpleNamespace as NS
import struct
import sys
import unittest
from unittest.mock import MagicMock, call, patch

from test_oak_device import sdk_fixture
from test_yolop_asset import fake_sdk_blob, pinned_manifest
from xenova_vehicle.oak import device, yolop_device
from xenova_vehicle.oak.yolop_asset import BLOB_SHA256, BLOB_SIZE, ValidatedYolopAsset


CONTEXT = "1" * 64


@dataclass(frozen=True)
class PacketFixture:
    sequence: int
    device_us: int
    sdk_us: int
    payload: bytes
    golden: dict | None = None


class TensorDataType(IntEnum):
    FP16 = 0


class TensorOrder(IntEnum):
    NCHW = 0x4321


def failed_nn_message(payload=b"\x00\x3c"):
    message = MagicMock()
    message.getSequenceNum.return_value = 19
    message.getTimestampDevice.return_value = timedelta(microseconds=1234)
    message.getTimestamp.return_value = timedelta(microseconds=5678)
    message.getData.return_value = bytearray(payload)
    message.getRaw.return_value = NS(tensors=[NS(name="det_out", dataType=TensorDataType.FP16,
        order=TensorOrder.NCHW, numDimensions=3, dims=[1, 6300, 6], strides=[], offset=0)])
    return message


def yolop_fixture():
    f = sdk_fixture()
    base_nodes = list(f.pipeline.create.side_effect)
    f.color = base_nodes[0]
    f.manip, f.network, f.input_out, f.output_out = [MagicMock() for _ in range(4)]
    f.pipeline.create.side_effect = base_nodes + [f.manip, f.network, f.input_out, f.output_out]
    f.dai.node.ImageManip = object()
    f.dai.node.NeuralNetwork = object()
    f.dai.ImgFrame.Type.BGR888p = "BGR888p"
    f.manifest = pinned_manifest()
    f.blob = fake_sdk_blob(f.manifest)
    f.dai.OpenVINO = NS(Blob=MagicMock(return_value=f.blob),
                        Version=NS(VERSION_2021_4="Version.VERSION_2021_4"))
    f.pipeline.getRequiredOpenVINOVersion.return_value = "Version.VERSION_2021_4"
    f.pipeline.getOpenVINOVersion.return_value = "Version.VERSION_2021_4"
    f.asset = ValidatedYolopAsset(b"verified fixture bytes", f.manifest,
                                 {"asset_id": f.manifest["asset_id"], "blob_sha256": BLOB_SHA256,
                                  "blob_bytes": BLOB_SIZE})
    all_nodes = base_nodes + [f.manip, f.network, f.input_out, f.output_out]
    for index, (node, name) in enumerate(zip(all_nodes,
            ("ColorCamera", "MonoCamera", "MonoCamera", "StereoDepth", "XLinkOut", "XLinkOut",
             "ImageManip", "NeuralNetwork", "XLinkOut", "XLinkOut"), strict=True)):
        node.id = index
        node.getName.return_value = name
        node.getAssetManager.side_effect = AssertionError("asset storage must not be read")
    f.pipeline.getAllNodes.return_value = all_nodes
    f.pipeline.getConnectionMap.return_value = {0: [
        NS(outputId=0, outputName="video", outputGroup="", inputId=6,
           inputName="inputImage", inputGroup="")]}
    f.pipeline.serializeToJson.side_effect = AssertionError("full graph serialization must not run")
    f.profile = NS(device_mxid="OAK_TEST_1", fps=15,
                   asset_dir="approved-release", asset_manifest="approved-manifest")
    return f


class YolopDeviceTests(unittest.TestCase):
    def start_source(self, f):
        source = yolop_device.YolopOakSource(f.profile, context_key=CONTEXT)
        lock = MagicMock()
        phases = []
        with patch.object(yolop_device, "load_yolop_asset", return_value=f.asset) as loader, \
                patch.object(device.sys, "platform", "linux"), \
                patch.object(device.importlib, "import_module", return_value=f.dai), \
                patch.object(device, "_DeviceLock", return_value=lock):
            receipt = source.start(phases.append)
        loader.assert_called_once_with("approved-release", "approved-manifest")
        return source, lock, phases, receipt

    def test_one_device_one_registration_one_start_and_same_video_fanout(self):
        f = yolop_fixture()
        source, lock, phases, receipt = self.start_source(f)
        self.assertEqual(phases, ["ASSET_CHECK", "DEVICE_BOOT", "PIPELINE_START"])
        f.dai.Device.assert_called_once_with(config=f.pipeline.getDeviceConfig.return_value,
                                             deviceInfo=f.approved)
        f.owner.startPipeline.assert_called_once_with(f.pipeline)
        f.network.setBlob.assert_called_once_with(f.blob)
        f.network.setBlobPath.assert_not_called()
        f.dai.OpenVINO.Blob.assert_called_once_with(list(f.asset.blob_bytes))
        f.network.setNumInferenceThreads.assert_called_once_with(1)
        f.network.input.setQueueSize.assert_called_once_with(1)
        f.network.input.setBlocking.assert_called_once_with(False)
        f.manip.initialConfig.setResizeThumbnail.assert_called_once_with(320, 320, 0, 0, 0)
        f.manip.initialConfig.setFrameType.assert_called_once_with("BGR888p")
        self.assertEqual(f.color.video.link.call_count, 2)
        f.color.video.link.assert_any_call(f.manip.inputImage)
        f.color.preview.link.assert_not_called()
        f.input_out.setMetadataOnly.assert_called_once_with(True)
        f.output_out.setMetadataOnly.assert_called_once_with(False)
        self.assertEqual({item.kwargs["name"]: item.kwargs["maxSize"]
                          for item in f.owner.getOutputQueue.call_args_list},
                         {"rgb_video": 8, "depth_right": 8, "nn_input": 8, "nn_output": 2})
        self.assertEqual(receipt["blob_registration_count"], 1)
        self.assertEqual(receipt["pipeline_start_count"], 1)
        self.assertEqual(receipt["nn_context_key"], CONTEXT)
        self.assertEqual(len(receipt["graph_declaration"]["nodes"]), 10)
        self.assertIs(receipt["graph_declaration"]["asset_storage_included"], False)
        self.assertEqual(receipt["graph_declaration"]["asset"]["blob_sha256"], BLOB_SHA256)
        f.pipeline.serializeToJson.assert_not_called()
        for node in f.pipeline.getAllNodes.return_value:
            node.getAssetManager.assert_not_called()
        self.assertIs(receipt["nn_ready"], False)
        self.assertIs(receipt["nn"]["pixel_equivalence_verified"], False)
        with self.assertRaisesRegex(RuntimeError, "started twice"):
            source.start(phases.append)
        source.close()
        source.close()
        self.assertIsNone(source._asset)
        f.owner.close.assert_called_once()
        lock.close.assert_called_once()

    def test_asset_failure_precedes_sdk_import_or_device_creation(self):
        f = yolop_fixture()
        source = yolop_device.YolopOakSource(f.profile, context_key=CONTEXT)
        phases = []
        with patch.object(yolop_device, "load_yolop_asset", side_effect=ValueError("hash mismatch")), \
                patch.object(device.importlib, "import_module") as importer:
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                source.start(phases.append)
        self.assertEqual(phases, ["ASSET_CHECK"])
        importer.assert_not_called()
        f.dai.Device.assert_not_called()

    def test_incompatible_graph_version_fails_before_device_open(self):
        f = yolop_fixture()
        f.pipeline.getRequiredOpenVINOVersion.return_value = "Version.VERSION_2022_1"
        with self.assertRaisesRegex(ValueError, "OpenVINO versions differ"):
            self.start_source(f)
        f.dai.Device.assert_not_called()
        f.owner.startPipeline.assert_not_called()

    def test_context_is_explicit_and_not_fabricated_by_the_source(self):
        for context in ("", "a" * 63, "A" * 64, True):
            with self.subTest(context=context), self.assertRaises(ValueError):
                yolop_device.YolopOakSource(yolop_fixture().profile, context_key=context)

    def test_poll_is_one_message_per_stream_and_preserves_context(self):
        f = yolop_fixture()
        source = yolop_device.YolopOakSource(f.profile, context_key=CONTEXT)
        queues = {name: MagicMock() for name in source._queue_sizes()}
        source._queues = queues
        raw = NS(payload=b"raw")
        source._frame = MagicMock(return_value=raw)
        nn_input = NS(sequence=1, device_us=100, sdk_us=1000)
        nn_output = PacketFixture(sequence=1, device_us=100, sdk_us=1000, payload=b"owned tensor bytes")
        source._sdk_golden = MagicMock(return_value={"performed": True, "matched": True})
        module = ModuleType("xenova_vehicle.oak.yolop_frames")
        module.sdk_nn_input = MagicMock(return_value=nn_input)
        module.sdk_nn_packet = MagicMock(return_value=nn_output)
        with patch.dict(sys.modules, {module.__name__: module}):
            result = source.poll()
        self.assertEqual(result[:3], [raw, raw, nn_input])
        self.assertEqual(result[3].payload, nn_output.payload)
        self.assertEqual(result[3].golden, {"performed": True, "matched": True})
        source._sdk_golden.assert_called_once()
        for queue in queues.values():
            queue.tryGet.assert_called_once_with()
        for converter in (module.sdk_nn_input, module.sdk_nn_packet):
            self.assertEqual(converter.call_args.args[2], CONTEXT)
        with patch.dict(sys.modules, {module.__name__: module}), \
                self.assertRaisesRegex(ValueError, "non-increasing"):
            source.poll()

    def test_close_failure_retains_host_asset_and_owner(self):
        f = yolop_fixture()
        source, lock, _, _ = self.start_source(f)
        f.owner.close.side_effect = RuntimeError("native failure")
        with self.assertRaisesRegex(RuntimeError, "native failure"):
            source.close()
        self.assertIs(source._asset, f.asset)
        self.assertIs(source._blob, f.blob)
        lock.close.assert_not_called()

    def test_golden_rejects_invalid_layout_before_native_accessor(self):
        source = yolop_device.YolopOakSource(yolop_fixture().profile, context_key=CONTEXT)
        packet, message = MagicMock(), MagicMock()
        module = ModuleType("xenova_vehicle.oak.yolop_frames")
        module.validate_nn_packet = MagicMock(side_effect=ValueError("empty stride"))
        with patch.dict(sys.modules, {module.__name__: module}):
            report = source._sdk_golden(packet, message)
        self.assertIs(report["matched"], False)
        self.assertEqual(report["accessor_calls"], 0)
        message.getLayerFp16.assert_not_called()
        packet.layer_bytes.assert_not_called()

    def test_golden_compares_finite_fp16_values_and_reports_mismatch(self):
        source = yolop_device.YolopOakSource(yolop_fixture().profile, context_key=CONTEXT)
        layers = {"det_out": [0.5, -2.0], "drive_area_seg": [1.0], "lane_line_seg": [0.0]}
        packet = NS(layer_bytes=lambda name: b"".join(struct.pack("<e", item)
                                                      for item in layers[name]))
        message = MagicMock()
        message.getLayerFp16.side_effect = lambda name: list(layers[name])
        module = ModuleType("xenova_vehicle.oak.yolop_frames")
        module.validate_nn_packet = MagicMock(return_value=None)
        with patch.dict(sys.modules, {module.__name__: module}):
            report = source._sdk_golden(packet, message)
            self.assertIs(report["matched"], True)
            self.assertEqual(report["elements"], 4)
            self.assertEqual(report["accessor_calls"], 3)
            self.assertGreaterEqual(report["elapsed_us"], 0)
            message.getLayerFp16.side_effect = lambda name: [float("nan")] * len(layers[name])
            report = source._sdk_golden(packet, message)
        self.assertIs(report["matched"], False)

    def failing_poll(self, source, message, original):
        source._queues = {"nn_output": MagicMock()}
        source._queues["nn_output"].tryGet.return_value = message
        module = ModuleType("xenova_vehicle.oak.yolop_frames")
        module.sdk_nn_input = MagicMock()
        module.sdk_nn_packet = MagicMock(side_effect=original)
        with patch.dict(sys.modules, {module.__name__: module}), \
                self.assertRaises(ValueError) as raised:
            source.poll()
        self.assertIs(raised.exception, original)

    def test_nn_failure_keeps_owned_bytes_and_unmodified_metadata_once(self):
        source = yolop_device.YolopOakSource(yolop_fixture().profile, context_key=CONTEXT)
        message = failed_nn_message()
        original = ValueError("invalid runtime tensor strides")
        self.failing_poll(source, message, original)
        message.getData.return_value[0] = 255
        diagnostic = source.take_failure_diagnostic()
        self.assertEqual(diagnostic["payloads"], {"nn-buffer.bin": b"\x00\x3c"})
        metadata = diagnostic["metadata"]
        self.assertEqual(metadata["sequence"], 19)
        self.assertEqual(metadata["device_timestamp_us"], 1234)
        self.assertEqual(metadata["sdk_timestamp_us"], 5678)
        self.assertEqual(metadata["context_key"], CONTEXT)
        self.assertEqual(metadata["tensors"][0]["strides"]["values"], [])
        self.assertIs(metadata["record_only"], True)
        self.assertIs(metadata["metric_ready"], False)
        self.assertLessEqual(len(json.dumps(metadata, ensure_ascii=True).encode("ascii")), 32 * 1024)
        self.assertIsNone(source.take_failure_diagnostic())
        self.failing_poll(source, message, original)
        self.assertIsNone(source.take_failure_diagnostic())
        message.getData.assert_called_once_with()
        message.getLayerFp16.assert_not_called()

    def test_oversized_failure_payload_is_not_copied(self):
        source = yolop_device.YolopOakSource(yolop_fixture().profile, context_key=CONTEXT)
        message = failed_nn_message(b"x" * (1024**2 + 1))
        self.failing_poll(source, message, ValueError("oversized output"))
        diagnostic = source.take_failure_diagnostic()
        self.assertEqual(diagnostic["payloads"], {})
        self.assertEqual(diagnostic["metadata"]["original_payload_bytes"], 1024**2 + 1)
        self.assertIn("not_copied", diagnostic["metadata"]["payload_rejection"])
        message.getLayerFp16.assert_not_called()

    def test_malformed_failure_metadata_stays_bounded_without_object_repr(self):
        class Opaque:
            def __repr__(self):
                raise AssertionError("opaque SDK values must not be repr'd")

        source = yolop_device.YolopOakSource(yolop_fixture().profile, context_key=CONTEXT)
        message = failed_nn_message()
        bad = NS(name="a" * 100_000, dataType=Opaque(), order=Opaque(), numDimensions=2**100_000,
                 dims=[2**100_000, Opaque(), -1, 0] * 1000, strides=Opaque(), offset=Opaque())
        message.getRaw.return_value = NS(tensors=[bad] * 10)
        message.getTimestampDevice.side_effect = RuntimeError("do not log raw payloads")
        self.failing_poll(source, message, ValueError("invalid metadata"))
        diagnostic = source.take_failure_diagnostic()
        encoded = json.dumps(diagnostic["metadata"], ensure_ascii=True).encode("ascii")
        self.assertLessEqual(len(encoded), 32 * 1024)
        self.assertEqual(len(diagnostic["metadata"]["tensors"]), 3)
        self.assertIs(diagnostic["metadata"]["tensors_truncated"], True)
        self.assertEqual(diagnostic["metadata"]["tensors"][0]["dims"]["length"], 4000)
        self.assertIn("opaque", diagnostic["metadata"]["tensors"][0]["numDimensions"])
        self.assertNotIn(b"do not log raw", encoded)
        message.getLayerFp16.assert_not_called()


if __name__ == "__main__":
    unittest.main()
