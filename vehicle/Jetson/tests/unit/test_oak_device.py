"""고정 SDK 호출 계약과 원본 프레임 소유를 fake SDK로 검사한다. 실물 시험은 아니다."""

from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from xenova_vehicle.capture_profile import CaptureProfile
from xenova_vehicle.oak import device


def sdk_fixture():
    color, left, right, stereo, rgb_out, depth_out = [MagicMock() for _ in range(6)]
    config = NS(postProcessing=NS(spatialFilter=NS(), temporalFilter=NS(),
                                 speckleFilter=NS(), decimationFilter=NS(),
                                 thresholdFilter=NS(), brightnessFilter=NS()))
    stereo.initialConfig.get.return_value = config
    pipeline = MagicMock()
    pipeline.create.side_effect = [color, left, right, stereo, rgb_out, depth_out]
    pipeline.serializeToJson.return_value = {"nodes": ["fixture"]}
    calibration = MagicMock()
    calibration.eepromToJson.return_value = {"version": 7, "boardName": "fixture"}
    calibration.getStereoRightRectificationRotation.return_value = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    calibration.getBaselineDistance.return_value = 7.5
    calibration.getCameraExtrinsics.return_value = [[1, 0, 0, 0]]
    owner = MagicMock()
    owner.getMxId.return_value = "OAK_TEST_1"
    owner.readCalibration2.return_value = calibration
    owner.readCalibrationRaw.return_value = [1, 2, 3]
    owner.getUsbSpeed.return_value = "SUPER"
    owner.startPipeline.return_value = True
    owner.getConnectedCameraFeatures.return_value = [
        NS(socket=socket, width=width, height=height, sensorName="fixture", name="fixture",
           hasAutofocus=False, configs=[], supportedTypes=[kind])
        for socket, width, height, kind in (("CAM_A", 1920, 1080, "COLOR"),
                                           ("CAM_B", 640, 480, "MONO"),
                                           ("CAM_C", 640, 480, "MONO"))]
    factory = MagicMock(return_value=owner)
    approved = NS(getMxId=lambda: "OAK_TEST_1")
    factory.getAllAvailableDevices.return_value = [approved]
    dai = NS(__version__="2.29.0.0", Pipeline=MagicMock(return_value=pipeline), Device=factory,
             CameraBoardSocket=NS(CAM_A="CAM_A", CAM_B="CAM_B", CAM_C="CAM_C"),
             ColorCameraProperties=NS(SensorResolution=NS(THE_1080_P="1080")),
             MonoCameraProperties=NS(SensorResolution=NS(THE_480_P="480")),
             StereoDepthConfig=NS(AlgorithmControl=NS(DepthAlign=NS(RECTIFIED_RIGHT="RIGHT"),
                                                      DepthUnit=NS(MILLIMETER="MM"))),
             MedianFilter=NS(MEDIAN_OFF="OFF"), CameraExposureOffset=NS(END="END"),
             ImgFrame=NS(Type=NS(NV12="NV12", RAW16="RAW16")),
             Clock=NS(now=lambda: timedelta(microseconds=100)),
             node=NS(ColorCamera=object(), MonoCamera=object(), XLinkOut=object(),
                     StereoDepth=NS(PresetMode=NS(HIGH_DENSITY="HIGH_DENSITY"))))
    return NS(dai=dai, owner=owner, calibration=calibration, pipeline=pipeline,
              stereo=stereo, outputs=(rgb_out, depth_out), approved=approved, config=config)


def frame_message(stream, seq=1, stamp=1000):
    rgb = stream == "rgb_video"
    payload = bytearray(640 * 360 * 3 // 2 if rgb else 640 * 480 * 2)
    frame = MagicMock()
    frame.getWidth.return_value = 640
    frame.getHeight.return_value = 360 if rgb else 480
    frame.getType.return_value = "NV12" if rgb else "RAW16"
    frame.getRaw.return_value = NS(fb=NS(stride=640 if rgb else 1280, p1Offset=0,
                                       p2Offset=640 * 360 if rgb else 0, p3Offset=0))
    frame.getData.return_value = payload
    frame.getSequenceNum.return_value = seq
    frame.getTimestampDevice.return_value = timedelta(microseconds=stamp)
    frame.getTimestamp.return_value = timedelta(microseconds=stamp + 100_000)
    frame.getExposureTime.return_value = timedelta(microseconds=500)
    return frame, payload


class OakDeviceTests(unittest.TestCase):
    def start_source(self, fixture):
        source = device.OakSource(CaptureProfile("OAK_TEST_1"))
        lock = MagicMock()
        stages = []
        with patch.object(device.sys, "platform", "linux"), \
                patch.object(device.importlib, "import_module", return_value=fixture.dai), \
                patch.object(device, "_DeviceLock", return_value=lock):
            receipt = source.start(stages.append)
        return source, lock, stages, receipt

    def test_one_non_autostart_device_one_pipeline_native_right_and_bounded_queues(self):
        f = sdk_fixture()
        source, lock, stages, receipt = self.start_source(f)
        f.dai.Device.assert_called_once_with(config=f.pipeline.getDeviceConfig.return_value,
                                             deviceInfo=f.approved)
        f.owner.startPipeline.assert_called_once_with(f.pipeline)
        self.assertEqual(stages, ["DEVICE_BOOT", "PIPELINE_START"])
        f.stereo.setDepthAlign.assert_called_once_with("RIGHT")
        f.stereo.initialConfig.setDepthUnit.assert_called_once_with("MM")
        f.stereo.setSubpixel.assert_called_once_with(False)
        f.stereo.setExtendedDisparity.assert_called_once_with(False)
        f.stereo.setDisparityToDepthUseSpecTranslation.assert_called_once_with(False)
        self.assertFalse(f.config.postProcessing.temporalFilter.enable)
        self.assertFalse(f.config.postProcessing.spatialFilter.enable)
        for output in f.outputs:
            output.input.setQueueSize.assert_called_once_with(1)
            output.input.setBlocking.assert_called_once_with(False)
        self.assertEqual(f.owner.getOutputQueue.call_count, 2)
        for call in f.owner.getOutputQueue.call_args_list:
            self.assertEqual(call.kwargs["maxSize"], 8)
            self.assertIs(call.kwargs["blocking"], False)
        self.assertEqual(receipt["usb_speed"], "SUPER")
        self.assertEqual(receipt["runtime_config_readback"], "not_evaluated")
        self.assertIsNone(receipt["calibration"]["active_rectified_intrinsics"])
        f.calibration.getBaselineDistance.assert_called_once_with("CAM_C", "CAM_B", False)
        with self.assertRaises(RuntimeError):
            source.start(stages.append)
        source.close()
        source.close()
        f.owner.close.assert_called_once()
        lock.close.assert_called_once()

    def test_unapproved_mxid_does_not_open_device(self):
        f = sdk_fixture()
        f.dai.Device.getAllAvailableDevices.return_value = [NS(getMxId=lambda: "OTHER")]
        source = device.OakSource(CaptureProfile("OAK_TEST_1"))
        lock = MagicMock()
        with patch.object(device.sys, "platform", "linux"), \
                patch.object(device.importlib, "import_module", return_value=f.dai), \
                patch.object(device, "_DeviceLock", return_value=lock):
            with self.assertRaisesRegex(RuntimeError, "not uniquely available"):
                source.start(lambda stage: None)
        f.dai.Device.assert_not_called()
        source.close()
        lock.close.assert_called_once()

    def test_close_failure_does_not_release_device_ownership(self):
        f = sdk_fixture()
        source, lock, _, _ = self.start_source(f)
        f.owner.close.side_effect = RuntimeError("native close failure")
        with self.assertRaisesRegex(RuntimeError, "native close"):
            source.close()
        lock.close.assert_not_called()
        self.assertIs(source._device, f.owner)

    def test_wrong_sdk_version_fails_before_device_open(self):
        f = sdk_fixture()
        f.dai.__version__ = "3.0.0"
        with patch.object(device.sys, "platform", "linux"), \
                patch.object(device.importlib, "import_module", return_value=f.dai):
            with self.assertRaisesRegex(RuntimeError, "2.29.0.0 required"):
                device.OakSource(CaptureProfile("OAK_TEST_1")).start(lambda stage: None)
        f.dai.Device.assert_not_called()

    def test_missing_native_depth_sensor_is_not_silently_resized(self):
        f = sdk_fixture()
        f.owner.getConnectedCameraFeatures.return_value[2].height = 400
        with self.assertRaisesRegex(ValueError, "dimensions absent"):
            self.start_source(f)
        f.owner.startPipeline.assert_not_called()

    def test_actual_source_rejects_non_linux_before_sdk_import(self):
        with patch.object(device.sys, "platform", "win32"), \
                patch.object(device.importlib, "import_module") as importer:
            with self.assertRaisesRegex(RuntimeError, "Linux"):
                device.OakSource(CaptureProfile("OAK_TEST_1")).start(lambda stage: None)
        importer.assert_not_called()

    def test_poll_is_bounded_and_copies_sdk_bytes(self):
        f = sdk_fixture()
        source = device.OakSource(CaptureProfile("OAK_TEST_1"))
        source._dai = f.dai
        rgb = [frame_message("rgb_video", i, i * 1000) for i in range(1, 4)]
        depth = [frame_message("depth_right", i, i * 1000) for i in range(1, 4)]
        queues = [MagicMock(), MagicMock()]
        queues[0].tryGet.side_effect = [item[0] for item in rgb]
        queues[1].tryGet.side_effect = [item[0] for item in depth]
        source._queues = dict(zip(("rgb_video", "depth_right"), queues))
        frames = source.poll()
        self.assertEqual(len(frames), 4)
        self.assertEqual([queue.tryGet.call_count for queue in queues], [2, 2])
        rgb[0][1][0] = 255
        self.assertEqual(frames[0].payload[0], 0)
        self.assertEqual(frames[0].device_us, 1000)
        self.assertEqual(frames[0].sdk_us, 101000)
        self.assertEqual(frames[0].exposure_us, 500)
        rgb[0][0].getTimestamp.assert_called_once_with("END")
        self.assertNotEqual(frames[0].stream, frames[2].stream)

    def test_bad_shape_stride_length_or_repeated_timestamp_is_rejected(self):
        source = device.OakSource(CaptureProfile("OAK_TEST_1"))
        source._dai = sdk_fixture().dai
        for kind in ("width", "type", "stride", "length"):
            message, _ = frame_message("depth_right")
            if kind == "width":
                message.getWidth.return_value = 320
            elif kind == "type":
                message.getType.return_value = "NV12"
            elif kind == "stride":
                message.getRaw.return_value.fb.stride = 1300
            else:
                message.getData.return_value = bytearray(4)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                source._frame("depth_right", message)
        message, _ = frame_message("depth_right")
        source._frame("depth_right", message)
        with self.assertRaisesRegex(ValueError, "non-increasing"):
            source._frame("depth_right", message)


@unittest.skipUnless(sys.platform == "linux", "real flock ownership requires Linux")
class LinuxDeviceLockTests(unittest.TestCase):
    def test_same_mxid_owner_is_exclusive_and_lock_file_survives_release(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(device.tempfile, "gettempdir", return_value=directory):
            first = device._DeviceLock("LOCK_TEST_1")
            second = None
            try:
                root = Path(directory) / f"xenova-oak-{os.getuid()}"
                files = list(root.glob("*.lock"))
                self.assertEqual(len(files), 1)
                lock_path = files[0]
                inode = lock_path.stat().st_ino
                with self.assertRaises(BlockingIOError):
                    device._DeviceLock("LOCK_TEST_1")
                first.close()
                self.assertTrue(lock_path.exists())
                second = device._DeviceLock("LOCK_TEST_1")
                self.assertEqual(lock_path.stat().st_ino, inode)
                second.close()
                self.assertTrue(lock_path.exists())
                self.assertEqual(lock_path.stat().st_ino, inode)
            finally:
                first.close()
                if second is not None:
                    second.close()

    def test_owner_directory_symlink_or_loose_permissions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(device.tempfile, "gettempdir", return_value=directory):
            target = Path(directory) / "private-target"
            target.mkdir(mode=0o700)
            root = Path(directory) / f"xenova-oak-{os.getuid()}"
            root.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(PermissionError, "unsafe OAK owner directory"):
                device._DeviceLock("LOCK_TEST_1")
            self.assertEqual(list(target.iterdir()), [])
            root.unlink()
            root.mkdir(mode=0o700)
            root.chmod(0o755)
            with self.assertRaisesRegex(PermissionError, "unsafe OAK owner directory"):
                device._DeviceLock("LOCK_TEST_1")
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
