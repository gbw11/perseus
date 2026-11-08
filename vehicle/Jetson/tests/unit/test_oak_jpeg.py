"""JPEG leaf/graph SDK fake 경계다. 실제 Device·이미지 decoder 수락은 별도다.

jpeg_bytes는 독립 baseline JPEG 문법으로 만든 회색 codec 입력이다. 새 packet의
시각·영상 framing·소유권을 검사하며 capture나 NN/metric 권위를 만들지 않는다.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import gc
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch
import weakref

import test_yolop_device as fixtures
from xenova_vehicle.oak import device, jpeg, yolop_device


def jpeg_bytes(width=640, height=360, *, extra_bytes=0):
    """DC0/EOB의 표준 grayscale baseline이다. cv2·SDK·fixture 파일을 읽지 않는다.

    8x8 zero-DCT block마다 Huffman 00 두 bit를 넣어 회색128을 표현한다. 마지막
    entropy padding은1이며 extra_bytes는 최대 길이 COM segment로만 추가한다.
    """
    def segment(marker, body):
        return bytes((0xff, marker)) + struct.pack('>H', len(body) + 2) + body
    comments = []
    remaining = extra_bytes
    if type(remaining) is not int or remaining < 0 or 0 < remaining < 4:
        raise ValueError('whole bounded COM segments required')
    while remaining:
        count = min(65537, remaining)
        if 0 < remaining - count < 4:
            count -= 4 - (remaining - count)
        comments.append(segment(0xfe, bytes(count - 4)))
        remaining -= count
    table = b'\x01' + bytes(15) + b'\0'
    blocks = ((width + 7) // 8) * ((height + 7) // 8)
    whole, tail = divmod(2 * blocks, 8)
    entropy = bytes(whole) + (bytes(((1 << (8-tail)) - 1,)) if tail else b'')
    return (b'\xff\xd8' + b''.join(comments)
        + segment(0xdb, b'\0' + bytes([1]) * 64)
        + segment(0xc0, b'\x08' + struct.pack('>HH', height, width) + b'\x01\x01\x11\0')
        + segment(0xc4, b'\0' + table + b'\x10' + table)
        + segment(0xda, b'\x01\x01\0\0\x3f\0') + entropy + b'\xff\xd9')


def jpeg_message(payload=None, *, sequence=7, device_us=1234, sdk_us=101234):
    message = MagicMock()
    message.getType.return_value = 'BITSTREAM'
    message.getSequenceNum.return_value = sequence
    message.getTimestampDevice.return_value = timedelta(microseconds=device_us)
    message.getTimestamp.return_value = timedelta(microseconds=sdk_us)
    message.getData.return_value = bytearray(jpeg_bytes()) if payload is None else payload
    # 압축 stream의 dimensions는 검증한 원 SOF에서 얻는다. 미확인 SDK metadata를 가정하지 않는다.
    message.getWidth.side_effect = AssertionError('BITSTREAM width metadata must not be inferred')
    message.getHeight.side_effect = AssertionError('BITSTREAM height metadata must not be inferred')
    return message


def jpeg_fixture():
    f = fixtures.yolop_fixture()
    nodes = list(f.pipeline.create.side_effect)
    f.encoder, f.jpeg_out = MagicMock(), MagicMock()
    for node, index, name in ((f.encoder, 10, 'VideoEncoder'), (f.jpeg_out, 11, 'XLinkOut')):
        node.id = index
        node.getName.return_value = name
        node.getAssetManager.side_effect = AssertionError('asset storage must not be read')
    f.pipeline.create.side_effect = nodes + [f.encoder, f.jpeg_out]
    f.pipeline.getAllNodes.return_value += [f.encoder, f.jpeg_out]
    f.pipeline.getConnectionMap.return_value[0] += [
        NS(outputId=0, outputName='video', outputGroup='', inputId=10, inputName='input', inputGroup=''),
        NS(outputId=10, outputName='bitstream', outputGroup='', inputId=11, inputName='input', inputGroup='')]
    f.dai.node.VideoEncoder = object()
    f.dai.VideoEncoderProperties = NS(Profile=NS(MJPEG='MJPEG'))
    f.dai.ImgFrame.Type.BITSTREAM = 'BITSTREAM'
    return f


class OakJpegTests(unittest.TestCase):
    def start(self, f, **options):
        source = yolop_device.YolopOakSource(f.profile, context_key=fixtures.CONTEXT, **options)
        lock = MagicMock()
        with patch.object(yolop_device, 'load_yolop_asset', return_value=f.asset), \
                patch.object(device.sys, 'platform', 'linux'), \
                patch.object(device.importlib, 'import_module', return_value=f.dai), \
                patch.object(device, '_DeviceLock', return_value=lock):
            receipt = source.start(lambda _: None)
        self.addCleanup(source.close)
        return source, receipt

    def test_opt_in_adds_only_encoder_and_xlink_to_same_video_and_one_device(self):
        f = jpeg_fixture()
        source, receipt = self.start(f, jpeg_enabled=True)
        f.dai.Device.assert_called_once()
        f.owner.startPipeline.assert_called_once_with(f.pipeline)
        f.network.setBlob.assert_called_once_with(f.blob)
        self.assertEqual(source._blob_registration_count, 1)
        self.assertEqual(f.pipeline.create.call_count, 12)
        self.assertEqual(f.color.video.link.call_count, 3)
        f.color.video.link.assert_any_call(f.encoder.input)
        f.color.video.link.assert_any_call(f.manip.inputImage)
        f.encoder.setDefaultProfilePreset.assert_called_once_with(15, 'MJPEG')
        f.encoder.setNumFramesPool.assert_called_once_with(2)
        f.encoder.setMaxOutputFrameSize.assert_called_once_with(jpeg.MAX_JPEG_BYTES)
        f.encoder.input.setQueueSize.assert_called_once_with(1)
        f.encoder.input.setBlocking.assert_called_once_with(False)
        f.encoder.bitstream.link.assert_called_once_with(f.jpeg_out.input)
        f.jpeg_out.setStreamName.assert_called_once_with('jpeg')
        f.jpeg_out.setMetadataOnly.assert_called_once_with(False)
        f.jpeg_out.input.setQueueSize.assert_called_once_with(1)
        f.jpeg_out.input.setBlocking.assert_called_once_with(False)
        f.owner.getOutputQueue.assert_any_call(name='jpeg', maxSize=1, blocking=False)
        self.assertTrue(receipt['jpeg_enabled'])
        self.assertEqual(receipt['graph_declaration']['declared_settings']['jpeg']['profile'], 'MJPEG')
        self.assertEqual(receipt['graph_declaration']['declared_settings']['jpeg']['size_wh'], [640, 360])
        self.assertEqual(len(receipt['graph_declaration']['nodes']), 12)
        self.assertEqual(source.jpeg_drop_count, 0)
        f.pipeline.serializeToJson.assert_not_called()

    def test_default_graph_receipt_and_four_queues_remain_without_jpeg(self):
        f = fixtures.yolop_fixture()
        source, receipt = self.start(f)
        self.assertFalse(source.jpeg_enabled)
        self.assertFalse(receipt['jpeg_enabled'])
        self.assertNotIn('jpeg', receipt['graph_declaration']['declared_settings'])
        self.assertEqual(f.pipeline.create.call_count, 10)
        self.assertEqual(f.color.video.link.call_count, 2)
        self.assertEqual(source._queue_sizes(), {'rgb_video': 8, 'depth_right': 8, 'nn_input': 8, 'nn_output': 2})
        for option in (1, 0, None, 'true'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                yolop_device.YolopOakSource(f.profile, context_key=fixtures.CONTEXT, jpeg_enabled=option)

    def test_packet_owns_once_copied_bytes_and_original_metadata_without_sdk_reference(self):
        original = bytearray(jpeg_bytes())
        message = jpeg_message(original)
        reference = weakref.ref(message)
        message.getTimestampDevice.side_effect = lambda offset=None: timedelta(microseconds=1234 if offset == 'END' else 734)
        message.getTimestamp.side_effect = lambda offset=None: timedelta(microseconds=101234 if offset == 'END' else 100734)
        packet = jpeg.sdk_jpeg(message, 200000, bitstream_type='BITSTREAM', exposure_offset='END')
        self.assertEqual((packet.stream, packet.sequence, packet.device_us, packet.sdk_us, packet.received_j_us),
            ('jpeg', 7, 1234, 101234, 200000))
        self.assertEqual((packet.width, packet.height), (640, 360))
        self.assertEqual(len(packet.payload), 1036)
        self.assertEqual(packet.payload, bytes(original))
        original[0] = 0
        self.assertEqual(packet.payload[:2], b'\xff\xd8')
        message.getData.assert_called_once_with()
        message.getTimestampDevice.assert_called_once_with('END')
        message.getTimestamp.assert_called_once_with('END')
        message.getWidth.assert_not_called()
        message.getHeight.assert_not_called()
        del message
        gc.collect()
        self.assertIsNone(reference())
        with self.assertRaises(FrozenInstanceError):
            packet.sequence = 8
        self.assertFalse(hasattr(packet, 'exposure_us'))
        self.assertFalse(hasattr(packet, 'capture_interval'))

    def test_packet_256kib_boundary_and_marker_dimensions_are_checked(self):
        raw = jpeg_bytes()
        maximum = jpeg_bytes(extra_bytes=jpeg.MAX_JPEG_BYTES-len(raw))
        packet = jpeg.JpegPacket('jpeg', 1, 2, 3, 4, 640, 360, maximum)
        self.assertEqual(len(packet.payload), jpeg.MAX_JPEG_BYTES)
        bad_payloads = (b'\xff\xd8\xff\xd9', raw[:-1], raw + b'\0',
            b'\xff\xd8\xff\xfe\x00\x01' + raw[2:], jpeg_bytes(638, 360), maximum + b'\0')
        for payload in bad_payloads:
            with self.subTest(size=len(payload)), self.assertRaises(ValueError):
                replace(packet, payload=payload)
        for fields in ({'stream': 'rgb_video'}, {'sequence': True}, {'device_us': -1},
                {'sdk_us': 2**64}, {'received_j_us': 1.}, {'width': True}, {'payload': bytearray(raw)}):
            with self.subTest(fields=tuple(fields)), self.assertRaises(ValueError):
                replace(packet, **fields)

    def test_sdk_buffer_shape_type_timestamp_and_remaining_copy_budget_fail_closed(self):
        raw = jpeg_bytes()
        for payload in (memoryview(bytearray(16)).cast('H'), memoryview(bytearray(16))[::2],
                memoryview(bytearray(16)).cast('B', shape=(4, 4)), bytearray(jpeg.MAX_JPEG_BYTES+1)):
            with self.subTest(payload=type(payload).__name__), self.assertRaises(ValueError):
                jpeg.sdk_jpeg(jpeg_message(payload), 200000, bitstream_type='BITSTREAM', exposure_offset='END')
        message = jpeg_message()
        with self.assertRaises(ValueError):
            jpeg.sdk_jpeg(message, 200000, bitstream_type='BITSTREAM', exposure_offset='END', copy_budget_bytes=len(raw)-1)
        message.getTimestamp.return_value = 123
        with self.assertRaises(ValueError):
            jpeg.sdk_jpeg(message, 200000, bitstream_type='BITSTREAM', exposure_offset='END')
        message = jpeg_message(sequence=True)
        with self.assertRaises(ValueError):
            jpeg.sdk_jpeg(message, 200000, bitstream_type='BITSTREAM', exposure_offset='END')
        message = jpeg_message()
        message.getType.return_value = 'NV12'
        with self.assertRaises(ValueError):
            jpeg.sdk_jpeg(message, 200000, bitstream_type='BITSTREAM', exposure_offset='END')

    def test_poll_drops_only_jpeg_and_retains_raw_nn_hwm_then_recovers(self):
        f = jpeg_fixture()
        source = yolop_device.YolopOakSource(f.profile, context_key=fixtures.CONTEXT, jpeg_enabled=True)
        source._dai = f.dai
        source._queues = {name: MagicMock() for name in source._queue_sizes()}
        for queue in source._queues.values():
            queue.tryGet.return_value = None
        rgb, depth = NS(payload=b'rgb'), NS(payload=b'depth')
        source._queues['rgb_video'].tryGet.return_value = object()
        source._queues['depth_right'].tryGet.return_value = object()
        source._frame = MagicMock(side_effect=lambda stream, _: rgb if stream == 'rgb_video' else depth)
        original_hwm = {'nn_input': (10, 20, 30), 'nn_output': (10, 20, 30)}
        source._last = dict(original_hwm)
        queue = source._queues['jpeg']
        for message in (jpeg_message(bytearray(jpeg.MAX_JPEG_BYTES+1)), jpeg_message(b'not-jpeg')):
            queue.tryGet.return_value = message
            self.assertEqual(source.poll(), [rgb, depth])
            self.assertEqual(source._last, original_hwm)
            self.assertIsNone(source._nn_failure)
            self.assertFalse(source._golden_done)
        queue.tryGet.return_value = jpeg_message(sequence=1, device_us=100, sdk_us=1100)
        packet = source.poll()[-1]
        self.assertIs(type(packet), jpeg.JpegPacket)
        queue.tryGet.return_value = jpeg_message(sequence=1, device_us=100, sdk_us=1100)
        self.assertEqual(source.poll(), [rgb, depth])
        queue.tryGet.return_value = jpeg_message(sequence=2, device_us=100, sdk_us=1101)
        self.assertEqual(source.poll(), [rgb, depth])
        queue.tryGet.return_value = jpeg_message(sequence=2, device_us=101, sdk_us=1101)
        self.assertEqual(source.poll()[-1].sequence, 2)
        queue.tryGet.side_effect = RuntimeError('JPEG queue failure')
        self.assertEqual(source.poll(), [rgb, depth])
        self.assertEqual(source.jpeg_drop_count, 5)
        self.assertEqual(source._last, original_hwm)
        self.assertIsNone(source._nn_failure)
        self.assertFalse(source._golden_done)

    def test_jpeg_keeps_existing_compact_node_and_connection_caps(self):
        f = jpeg_fixture()
        f.pipeline.getAllNodes.return_value += [MagicMock() for _ in range(5)]
        with self.assertRaisesRegex(ValueError, 'node bound'):
            yolop_device.compact_yolop_graph(f.pipeline, f.asset.receipt, 15, jpeg_enabled=True)
        f = jpeg_fixture()
        group = f.pipeline.getConnectionMap.return_value[0]
        f.pipeline.getConnectionMap.return_value = {0: group * 11}
        with self.assertRaisesRegex(ValueError, 'connection bound'):
            yolop_device.compact_yolop_graph(f.pipeline, f.asset.receipt, 15, jpeg_enabled=True)


if __name__ == '__main__':
    unittest.main()
