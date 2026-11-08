"""카메라 없는 SIM graph와 원 SYNTHETIC/SDK/J 결합을 fake SDK로 검사한다.

고정 asset loader와 Device/queue만 대체하며 실제 NN tensor 소유/유한 검사는 재사용한다.
영상 pixel equivalence·OAK 실행·물리 노출/보정·주행 수락을 증명하는 시험이 아니다.
"""

from dataclasses import replace
from datetime import timedelta
from contextlib import ExitStack
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from xenova_vehicle.oak import sim_nn
from xenova_vehicle.oak.yolop_asset import BLOB_SHA256, BLOB_SIZE, ValidatedYolopAsset
from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.perception import yolop
from xenova_vehicle.yolop_profile import YolopBenchProfile

from test_yolop_asset import fake_sdk_blob, pinned_manifest
from test_yolop_frames import FakeInput, FakeNN


CONTEXT = '1' * 64
CALIBRATION = '2' * 64


class Clock:
    def __init__(self):
        self.now = 5_000_000

    def __call__(self):
        return self.now


class HostFrame:
    """확인된 ImgFrame setters만 가진 input SDK 대역이다."""
    def setWidth(self, value): self.width = value
    def setHeight(self, value): self.height = value
    def setType(self, value): self.type = value
    def setSequenceNum(self, value): self.sequence = value
    def setTimestamp(self, value): self.sdk = value
    def setTimestampDevice(self, value): self.device = value
    def setData(self, value): self.payload = value.tobytes()


def fixture():
    f = NS(clock=Clock())
    f.oak = YolopBenchProfile('approved-sim-oak', Path('approved-assets'), Path('approved-manifest'))
    f.profile = sim_nn.SimNnProfile(f.oak, 'sim-source', 'simulation:original-clock', CALIBRATION)
    f.manifest = pinned_manifest()
    f.asset = ValidatedYolopAsset(b'verified fake blob bytes', f.manifest,
        dict(asset_id=f.manifest['asset_id'], blob_sha256=BLOB_SHA256, blob_bytes=BLOB_SIZE))
    f.blob = fake_sdk_blob(f.manifest)
    f.nodes = [MagicMock() for _ in range(5)]
    f.host, f.manip, f.network, f.passthrough, f.output = f.nodes
    for index, (node, name) in enumerate(zip(f.nodes,
            ('XLinkIn', 'ImageManip', 'NeuralNetwork', 'XLinkOut', 'XLinkOut'), strict=True)):
        node.id = index
        node.getName.return_value = name
    f.pipeline = MagicMock()
    f.pipeline.create.side_effect = f.nodes
    f.pipeline.getAllNodes.return_value = f.nodes
    f.pipeline.getConnectionMap.return_value = {0: [NS(outputId=left, inputId=right,
        outputName=out, inputName=into) for left, right, out, into in (
        (0, 1, 'out', 'inputImage'), (1, 2, 'out', 'input'),
        (2, 3, 'passthrough', 'input'), (2, 4, 'out', 'input'))]}
    f.pipeline.getRequiredOpenVINOVersion.return_value = 'Version.VERSION_2021_4'
    f.pipeline.getOpenVINOVersion.return_value = 'Version.VERSION_2021_4'
    f.owner = MagicMock()
    f.owner.getMxId.return_value = f.oak.device_mxid
    f.owner.startPipeline.return_value = True
    f.input_queue, f.input_output, f.nn_output = MagicMock(), MagicMock(), MagicMock()
    f.input_output.tryGet.return_value = f.nn_output.tryGet.return_value = None
    f.owner.getInputQueue.return_value = f.input_queue
    f.owner.getOutputQueue.side_effect = lambda *, name, **kwargs: {
        'nn_input': f.input_output, 'nn_output': f.nn_output}[name]
    f.frames = []

    def new_frame():
        value = HostFrame()
        f.frames.append(value)
        return value

    f.dai = NS(__version__='2.29.0.0', Pipeline=MagicMock(return_value=f.pipeline),
        node=NS(**{key: object() for key in ('XLinkIn', 'ImageManip', 'NeuralNetwork', 'XLinkOut',
            'ColorCamera', 'MonoCamera', 'StereoDepth')}),
        OpenVINO=NS(Blob=MagicMock(return_value=f.blob), Version=NS(VERSION_2021_4='Version.VERSION_2021_4')),
        ImgFrame=MagicMock(side_effect=new_frame), Device=MagicMock(return_value=f.owner),
        Clock=NS(now=MagicMock(side_effect=[timedelta(microseconds=70_000_000 + 100 * n) for n in range(20)])))
    f.dai.ImgFrame.Type = NS(NV12=22, BGR888p=8)
    f.info = MagicMock()
    f.info.getMxId.return_value = f.oak.device_mxid
    f.dai.Device.getAllAvailableDevices.return_value = [f.info]
    f.lock = MagicMock()
    return f


def observation(sequence=7, *, kind='SYNTHETIC', source='sim-source', clock='simulation:original-clock',
                calibration=CALIBRATION, width=640, height=360, encoding='NV12'):
    # 가상 scalar77은 실제 J5백만/SDK7천만과 의도적으로 다르다.
    stamp = FrameStamp(source, 'rgb', sequence, clock, (77, 78), 80)
    size = width * height * 3 if encoding == 'BGR8' else width * height * 3 // 2
    frame = ImageFrame(stamp, width, height, encoding, bytes(size))
    return PerceptionInput(kind, calibration, frame)


def output_message(submission, *, passthrough=False, key=None):
    value = FakeInput() if passthrough else FakeNN()
    sequence, device_us, sdk_us = submission.key if key is None else key
    value.getSequenceNum = lambda: sequence
    value.getTimestampDevice = lambda: timedelta(microseconds=device_us)
    value.getTimestamp = lambda: timedelta(microseconds=sdk_us)
    return value


class SimNnTests(unittest.TestCase):
    def start(self, f=None):
        f = fixture() if f is None else f
        patches = (patch.object(sim_nn, 'load_yolop_asset', return_value=f.asset),
            patch.object(sim_nn.importlib, 'import_module', return_value=f.dai),
            patch.object(sim_nn, '_DeviceLock', return_value=f.lock),
            patch.object(sim_nn.sys, 'platform', 'linux'), patch.object(sim_nn.sys, 'byteorder', 'little'))
        with ExitStack() as stack:
            for entry in patches:
                stack.enter_context(entry)
            f.source = sim_nn.SimNnSource(f.profile, context_key=CONTEXT, clock=f.clock)
            f.stages = []
            f.receipt = f.source.start(f.stages.append, startup_deadline_us=f.clock.now + 90_000_000)
        return f

    def capture(self, f, value=None, **changes):
        return f.source.capture(observation() if value is None else value,
            captured_j_us=changes.get('captured_j_us', (4_999_900, 4_999_950)),
            expires_j_us=changes.get('expires_j_us', 5_100_000))

    def complete(self, f, original=None):
        ticket = self.capture(f, original)
        f.input_output.tryGet.return_value = output_message(ticket, passthrough=True)
        f.nn_output.tryGet.return_value = output_message(ticket)
        result, = f.source.poll()
        return result

    def test_dedicated_host_graph_has_no_camera_one_verified_blob_and_bounded_queues(self):
        f = self.start()
        self.assertEqual(f.stages, ['ASSET_CHECK', 'DEVICE_BOOT', 'PIPELINE_START'])
        created = [row.args[0] for row in f.pipeline.create.call_args_list]
        self.assertEqual(created, [f.dai.node.XLinkIn, f.dai.node.ImageManip,
            f.dai.node.NeuralNetwork, f.dai.node.XLinkOut, f.dai.node.XLinkOut])
        f.network.setBlob.assert_called_once_with(f.blob)
        f.dai.OpenVINO.Blob.assert_called_once_with(list(f.asset.blob_bytes))
        f.dai.Device.assert_called_once_with(config=f.pipeline.getDeviceConfig.return_value, deviceInfo=f.info)
        f.owner.readCalibration2.assert_not_called()
        f.owner.getConnectedCameraFeatures.assert_not_called()
        f.host.setMaxDataSize.assert_called_once_with(sim_nn.INPUT_BYTES)
        f.host.setNumFrames.assert_called_once_with(2)
        f.host.out.link.assert_called_once_with(f.manip.inputImage)
        f.manip.initialConfig.setResizeThumbnail.assert_called_once_with(320, 320, 0, 0, 0)
        f.manip.setNumFramesPool.assert_called_once_with(2)
        f.network.setNumInferenceThreads.assert_called_once_with(1)
        f.network.setNumPoolFrames.assert_called_once_with(2)
        f.input_queue.setMaxSize.assert_called_once_with(1)
        f.input_queue.setBlocking.assert_called_once_with(False)
        for node in (f.manip.inputImage, f.network.input, f.passthrough.input, f.output.input):
            node.setQueueSize.assert_called_once_with(1)
            node.setBlocking.assert_called_once_with(False)
        self.assertEqual(f.owner.getOutputQueue.call_count, 2)
        f.pipeline.serializeToJson.assert_not_called()
        self.assertEqual((f.receipt['input_kind'], f.receipt['graph_declaration']['camera_nodes']), ('SYNTHETIC', 0))
        self.assertEqual(f.receipt['blob_registration_count'], 1)
        self.assertFalse(f.receipt['live_authority'])
        with self.assertRaises(RuntimeError):
            f.source.start(f.stages.append, startup_deadline_us=99_000_000)
        f.network.setBlob.assert_called_once_with(f.blob)

    def test_original_virtual_capture_j_and_actual_sdk_enqueue_are_separate_owned_ticket(self):
        f = self.start()
        original = observation()
        ticket = self.capture(f, original)
        self.assertIs(ticket.observation, original)
        self.assertEqual(ticket.captured_j_us, (4_999_900, 4_999_950))
        self.assertEqual(ticket.expires_j_us, 5_100_000)
        self.assertEqual(ticket.key, (7, 70_000_000, 70_000_000))
        self.assertEqual(ticket.clock_sample, sim_nn.HostClockSample(5_000_000, 70_000_000, 5_000_000))
        self.assertEqual(original.rgb.stamp.captured_us, (77, 78))
        self.assertEqual(ticket.ticket, (original.rgb.stamp.key, ticket.key, hashlib.sha256(original.rgb.payload).hexdigest()))
        self.assertEqual(ticket.payload_sha256, original.rgb.sha256)
        f.input_queue.send.assert_called_once_with(f.frames[0])
        frame = f.frames[0]
        self.assertEqual((frame.width, frame.height, frame.type, frame.sequence), (640, 360, 22, 7))
        self.assertEqual((frame.sdk, frame.device), (timedelta(seconds=70), timedelta(seconds=70)))
        self.assertEqual(frame.payload, original.rgb.payload)
        self.assertEqual(len(frame.payload), sim_nn.INPUT_BYTES)
        with self.assertRaises(AttributeError):
            ticket.expires_j_us = 9_000_000

    def test_exact_full_key_pairs_late_output_without_retiming_or_retaining_sdk_views(self):
        f = self.start()
        ticket = self.capture(f)
        wrong = output_message(ticket, key=(7, 70_000_001, 70_000_000))
        wrong.getData = MagicMock(side_effect=AssertionError('unmatched SDK bytes must not be copied'))
        f.nn_output.tryGet.return_value = wrong
        self.assertEqual(f.source.poll(), ())
        wrong.getData.assert_not_called()
        f.input_output.tryGet.return_value = output_message(ticket, passthrough=True)
        f.nn_output.tryGet.return_value = None
        self.assertEqual(f.source.poll(), ())
        f.input_output.tryGet.return_value = None
        nn = output_message(ticket)
        original = bytes(nn.data)
        f.nn_output.tryGet.return_value = nn
        f.clock.now += 300
        result, = f.source.poll()
        nn.data[0] = 255
        self.assertEqual(result.nn_output.payload, original)
        self.assertIs(result.submission, ticket)
        self.assertEqual(result.nn_input.received_j_us, 5_000_000)
        self.assertEqual(result.nn_output.received_j_us, 5_000_300)
        self.assertEqual(result.submission.expires_j_us, 5_100_000)
        self.assertEqual(result.submission.observation.kind, 'SYNTHETIC')
        self.assertEqual(f.source.counts, dict(expired=0, unmatched=1, duplicate=0, completed=1))
        with self.assertRaises(ValueError):
            replace(result, nn_input=replace(result.nn_input, sdk_us=ticket.key[2] + 1))
        f.nn_output.tryGet.return_value = None
        self.assertEqual(f.source.poll(), ())

    def test_single_pending_and_expiry_do_not_refresh_capture_or_accept_old_outputs(self):
        f = self.start()
        original = self.capture(f)
        calls = f.dai.Clock.now.call_count
        self.assertIsNone(self.capture(f, observation(8)))
        self.assertEqual(f.dai.Clock.now.call_count, calls)
        f.input_queue.send.assert_called_once()
        f.clock.now = original.expires_j_us
        f.input_output.tryGet.return_value = output_message(original, passthrough=True)
        f.nn_output.tryGet.return_value = output_message(original)
        self.assertEqual(f.source.poll(), ())
        self.assertEqual(f.source.counts['expired'], 1)
        self.assertEqual(original.expires_j_us, 5_100_000)
        with self.assertRaises(ValueError):
            self.capture(f, observation(8))
        current = self.capture(f, observation(8), captured_j_us=(5_099_900, 5_099_950), expires_j_us=5_200_000)
        self.assertEqual(current.key[0], 8)
        self.assertEqual(f.source.poll(), ())
        self.assertIs(f.source._pending, current)
        self.assertGreaterEqual(f.source.counts['unmatched'], 4)

    def test_wrong_kind_source_profile_time_shape_and_nonincreasing_keys_are_rejected(self):
        for value in (observation(kind='LIVE'), observation(kind='REPLAY'), observation(source='foreign'),
                observation(clock='J'), observation(calibration='3' * 64), observation(width=638),
                observation(encoding='BGR8'), observation(sequence=2**63)):
            with self.subTest(kind=value.kind, stamp=value.rgb.stamp.key, width=value.rgb.width), self.assertRaises(ValueError):
                self.capture(self.start(), value)
        f = self.start()
        for changes in (dict(captured_j_us=(5_000_001, 5_000_002)),
                dict(captured_j_us=(5_000_000, 4_999_900)), dict(captured_j_us=[4_999_900, 4_999_950]),
                dict(expires_j_us=5_000_000), dict(expires_j_us=True), dict(expires_j_us=11_000_000)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.capture(f, **changes)
        f.input_queue.send.assert_not_called()
        ticket = self.capture(f)
        f.input_output.tryGet.return_value = output_message(ticket, passthrough=True)
        f.nn_output.tryGet.return_value = output_message(ticket)
        self.assertEqual(len(f.source.poll()), 1)
        with self.assertRaises(ValueError):
            self.capture(f)
        with self.assertRaises(ValueError):
            sim_nn.SimNnSource(f.oak, context_key=CONTEXT)
        f.output.getName.return_value = 'ColorCamera'
        with self.assertRaises(ValueError):
            sim_nn.sim_graph_declaration(f.pipeline, f.asset)

    def test_actual_nn_payload_bounds_and_finite_validation_remain_mandatory(self):
        for payload in (bytearray(1_048_577), bytearray(b'\x00\x7c' + bytes(894_798))):
            with self.subTest(size=len(payload)):
                f = self.start()
                ticket = self.capture(f)
                nn = output_message(ticket)
                nn.data = payload
                f.nn_output.tryGet.return_value = nn
                with self.assertRaises(ValueError):
                    f.source.poll()
                self.assertEqual(f.source.counts['completed'], 0)

    def test_asset_startup_deadline_and_sdk_version_fail_before_opening_device(self):
        f = fixture()
        source = sim_nn.SimNnSource(f.profile, context_key=CONTEXT, clock=f.clock)
        with patch.object(sim_nn, 'load_yolop_asset', side_effect=ValueError('asset mismatch')), \
                patch.object(sim_nn.importlib, 'import_module') as importer:
            with self.assertRaises(ValueError):
                source.start(lambda stage: None, startup_deadline_us=6_000_000)
            importer.assert_not_called()
        source = sim_nn.SimNnSource(f.profile, context_key=CONTEXT, clock=f.clock)
        with patch.object(sim_nn, 'load_yolop_asset') as loader:
            with self.assertRaises(TimeoutError):
                source.start(lambda stage: None, startup_deadline_us=f.clock.now)
            loader.assert_not_called()
        f = fixture()
        f.dai.__version__ = '3.0.0'
        with self.assertRaises(RuntimeError):
            self.start(f)
        f.dai.Device.assert_not_called()

    def test_send_uncertainty_and_device_close_failure_retain_original_owner_and_deadline(self):
        f = self.start()
        f.input_queue.send.side_effect = RuntimeError('send outcome unknown')
        with self.assertRaises(RuntimeError):
            self.capture(f)
        original = f.source._pending
        self.assertIsNotNone(original)
        with self.assertRaises(RuntimeError):
            self.capture(f, observation(8))
        with self.assertRaises(RuntimeError):
            f.source.poll()
        f.owner.close.side_effect = OSError('device still owned')
        with self.assertRaises(OSError):
            f.source.close(deadline_us=6_000_000)
        f.lock.close.assert_not_called()
        self.assertIs(f.source._pending, original)
        f.clock.now = 6_000_000
        with self.assertRaises(TimeoutError):
            f.source.close(deadline_us=8_000_000)
        self.assertEqual(f.owner.close.call_count, 1)
        f.lock.close.assert_not_called()
        good = self.start()
        good.source.close(deadline_us=7_000_000)
        good.source.close(deadline_us=9_000_000)
        good.owner.close.assert_called_once()
        good.lock.close.assert_called_once()
        with self.assertRaises(RuntimeError):
            self.capture(good)

    def test_adapter_reuses_actual_decoder_and_preserves_original_parent_depth_clock_and_kind(self):
        f = self.start()
        original = observation()
        depth_stamp = replace(original.rgb.stamp, stream_id='right', captured_us=(76, 79))
        depth = ImageFrame(depth_stamp, 640, 480, 'RAW16_LE', b'\xe8\x03' * (640 * 480))
        original = replace(original, depth=depth)
        paired = self.complete(f, original)
        f.clock.now += 300
        joined = paired.to_perception_input(original, clock=f.clock)
        self.assertIs(joined.rgb, original.rgb)
        self.assertIs(joined.depth, depth)
        self.assertIs(joined.rgb.stamp, original.rgb.stamp)
        self.assertIs(joined.depth.stamp, depth_stamp)
        self.assertEqual((joined.kind, joined.calibration_id), ('SYNTHETIC', original.calibration_id))
        self.assertEqual(joined.capture_interval, (76, 79))
        self.assertIs(joined.nn.packet, paired.nn_output)
        self.assertEqual(joined.nn.preprocessing_id, yolop.PREPROCESSING_ID)
        self.assertEqual(joined.nn.lane.source.shape, (360, 640))
        self.assertEqual(joined.nn_key, ('sim-source', 'rgb', 'simulation:original-clock', 7,
            70_000_000, 70_000_000, CONTEXT, paired.submission.payload_sha256))
        self.assertTrue(all(type(value) is int or type(value) is str and len(value) <= 128
            for value in joined.nn_key))
        self.assertFalse(joined.live_authority)
        self.assertIsNone(original.nn)
        self.assertIsNone(original.nn_key)
        self.assertEqual(paired.submission.expires_j_us, 5_100_000)
        # 값이 같은 사본도 원 capture ticket의 parent 객체를 대신하지 못한다.
        with self.assertRaises(ValueError):
            paired.to_perception_input(replace(original), clock=f.clock)
        # frozen 경계를 우회한 변조까지 SHA 재검사가 발견하는지의 방어 시험이다.
        object.__setattr__(paired.submission, 'payload_sha256', '0' * 64)
        with self.assertRaises(ValueError):
            paired.to_perception_input(original, clock=f.clock)

    def test_adapter_rejects_expiry_before_and_during_real_decode_without_reissuing_original_e(self):
        f = self.start()
        paired = self.complete(f)
        original = paired.submission.observation
        f.clock.now = paired.submission.expires_j_us
        with patch.object(yolop, 'decode_yolop', side_effect=AssertionError('expired input must not decode')) as decoder:
            with self.assertRaises(TimeoutError):
                paired.to_perception_input(original, clock=f.clock)
            decoder.assert_not_called()
        f = self.start()
        paired = self.complete(f)
        original = paired.submission.observation
        actual_decoder = yolop.decode_yolop
        decoded = []

        def finish_at_expiry(packet):
            candidates = actual_decoder(packet)
            decoded.append(candidates)
            f.clock.now = paired.submission.expires_j_us
            return candidates

        with patch.object(yolop, 'decode_yolop', side_effect=finish_at_expiry):
            with self.assertRaises(TimeoutError):
                paired.to_perception_input(original, clock=f.clock)
        self.assertIs(decoded[0].packet, paired.nn_output)
        self.assertIsNone(original.nn)
        self.assertEqual((paired.submission.captured_j_us, paired.submission.expires_j_us),
            ((4_999_900, 4_999_950), 5_100_000))


if __name__ == '__main__':
    unittest.main()
