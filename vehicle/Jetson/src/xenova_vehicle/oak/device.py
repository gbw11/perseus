"""DepthAI 2.29 전용 OAK 수신기. A 프로세스만 생성하고 Device 전체를 닫는다.

출력은 NV12와 RECTIFIED_RIGHT RAW16 원본이다. NN·JPEG·주행 권한·빈공간
판정은 없다. 고정 SDK wheel의 stub와 공식 v2 소스에 맞춘 벤치 어댑터다.
"""

from datetime import timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

from ..capture_profile import CaptureProfile
from .frames import RawFrame


def _us(value: timedelta) -> int:
    if not isinstance(value, timedelta):
        raise ValueError("SDK timestamp must be timedelta")
    return (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds


def _json_copy(value: object, limit: int = 1_048_576) -> object:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    if len(encoded) > limit:
        raise ValueError("SDK metadata exceeds capture receipt limit")
    return json.loads(encoded)


class _DeviceLock:
    """같은 사용자의 실제 MXID 소유자를 한 개로 제한한다. lock 파일은 지우지 않는다."""

    def __init__(self, mxid: str):
        self.fd = None
        if sys.platform != "linux":
            raise RuntimeError("OAK capture device ownership requires Linux")
        import fcntl
        self._fcntl = fcntl
        uid = os.getuid()
        root = Path(tempfile.gettempdir()) / f"xenova-oak-{uid}"
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise PermissionError("unsafe OAK owner directory")
        name = hashlib.sha256(mxid.encode("ascii")).hexdigest() + ".lock"
        fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise PermissionError("unsafe OAK owner lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _pipeline(dai, fps: int):
    pipeline = dai.Pipeline()
    color = pipeline.create(dai.node.ColorCamera)
    color.setBoardSocket(dai.CameraBoardSocket.CAM_A)
    color.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
    color.setIspScale(1, 3)
    color.setVideoSize(640, 360)
    color.setFps(fps)
    left = pipeline.create(dai.node.MonoCamera)
    right = pipeline.create(dai.node.MonoCamera)
    for camera, socket in ((left, dai.CameraBoardSocket.CAM_B),
                           (right, dai.CameraBoardSocket.CAM_C)):
        camera.setBoardSocket(socket)
        camera.setResolution(dai.MonoCameraProperties.SensorResolution.THE_480_P)
        camera.setFps(fps)
    stereo = pipeline.create(dai.node.StereoDepth)
    stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
    stereo.setLeftRightCheck(True)
    stereo.setExtendedDisparity(False)
    stereo.setSubpixel(False)
    stereo.setDepthAlign(dai.StereoDepthConfig.AlgorithmControl.DepthAlign.RECTIFIED_RIGHT)
    stereo.setOutputSize(640, 480)
    stereo.setRectification(True)
    stereo.enableDistortionCorrection(True)
    stereo.setMeshStep(16, 16)
    stereo.setAlphaScaling(0.0)
    stereo.setRectifyEdgeFillColor(0)
    stereo.setDisparityToDepthUseSpecTranslation(False)
    stereo.setRectificationUseSpecTranslation(False)
    stereo.setDepthAlignmentUseSpecTranslation(False)
    stereo.initialConfig.setMedianFilter(dai.MedianFilter.MEDIAN_OFF)
    stereo.initialConfig.setConfidenceThreshold(200)
    stereo.initialConfig.setLeftRightCheckThreshold(10)
    stereo.initialConfig.setDepthUnit(dai.StereoDepthConfig.AlgorithmControl.DepthUnit.MILLIMETER)
    config = stereo.initialConfig.get()
    config.postProcessing.spatialFilter.enable = False
    config.postProcessing.spatialFilter.holeFillingRadius = 0
    config.postProcessing.temporalFilter.enable = False
    config.postProcessing.speckleFilter.enable = False
    config.postProcessing.decimationFilter.decimationFactor = 1
    config.postProcessing.bilateralSigmaValue = 0
    config.postProcessing.thresholdFilter.minRange = 0
    config.postProcessing.thresholdFilter.maxRange = 65_535
    config.postProcessing.brightnessFilter.minBrightness = 0
    config.postProcessing.brightnessFilter.maxBrightness = 255
    stereo.initialConfig.set(config)
    left.out.link(stereo.left)
    right.out.link(stereo.right)
    for stream, output in (("rgb_video", color.video), ("depth_right", stereo.depth)):
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName(stream)
        xout.input.setQueueSize(1)
        xout.input.setBlocking(False)
        output.link(xout.input)
    return pipeline


def _features(device, dai) -> list[dict]:
    features = device.getConnectedCameraFeatures()
    by_socket = {feature.socket: feature for feature in features}
    for socket, width, height in ((dai.CameraBoardSocket.CAM_A, 1920, 1080),
                                  (dai.CameraBoardSocket.CAM_B, 640, 480),
                                  (dai.CameraBoardSocket.CAM_C, 640, 480)):
        feature = by_socket.get(socket)
        if feature is None or feature.width < width or feature.height < height:
            raise ValueError(f"required camera/dimensions absent: {socket} {width}x{height}")
    return [{"socket": str(f.socket), "sensor_name": f.sensorName, "name": f.name,
             "width": f.width, "height": f.height, "has_autofocus": f.hasAutofocus,
             "supported_types": [str(t) for t in f.supportedTypes],
             "configs": [{"width": c.width, "height": c.height,
                          "min_fps": c.minFps, "max_fps": c.maxFps,
                          "type": str(c.type)} for c in f.configs]}
            for f in features]


class OakSource:
    """한 graph/session의 SDK owner. 호출자 A의 native hang은 부모가 외부 감시한다."""

    def __init__(self, profile: CaptureProfile):
        self.profile = profile
        self._dai = None
        self._device = None
        self._lock = None
        self._queues = {}
        self._last = {}
        self._started = False

    def start(self, on_stage) -> dict:
        """승인 MXID만 열어 graph를 정확히 한 번 시작하고 선언/장치 receipt를 반환한다."""
        if self._started:
            raise RuntimeError("an OakSource cannot be started twice")
        self._started = True
        if sys.platform != "linux" or sys.byteorder != "little":
            raise RuntimeError("real OAK capture requires little-endian Linux")
        dai = importlib.import_module("depthai")
        self._dai = dai
        if dai.__version__ != "2.29.0.0":
            raise RuntimeError(f"DepthAI 2.29.0.0 required, found {dai.__version__}")
        pipeline = _pipeline(dai, self.profile.fps)
        graph = _json_copy(pipeline.serializeToJson())
        self._lock = _DeviceLock(self.profile.device_mxid)
        on_stage("DEVICE_BOOT")
        found = [info for info in dai.Device.getAllAvailableDevices()
                 if info.getMxId() == self.profile.device_mxid]
        if len(found) != 1:
            raise RuntimeError("approved OAK MXID is not uniquely available")
        self._device = dai.Device(config=pipeline.getDeviceConfig(), deviceInfo=found[0])
        if self._device.getMxId() != self.profile.device_mxid:
            raise RuntimeError("connected OAK MXID differs from approved profile")
        sensors = _features(self._device, dai)
        calibration = self._device.readCalibration2()
        eeprom = _json_copy(calibration.eepromToJson(), 262_144)
        raw = bytes(self._device.readCalibrationRaw())
        if not raw or len(raw) > 65_536:
            raise ValueError("invalid raw EEPROM length")
        on_stage("PIPELINE_START")
        if self._device.startPipeline(pipeline) is not True:
            raise RuntimeError("startPipeline did not confirm success")
        # v2 startPipeline가 만든 기본 blocking queue를 즉시 유한 nonblocking으로 바꾼다.
        self._queues = {name: self._device.getOutputQueue(name=name, maxSize=8, blocking=False)
                        for name in ("rgb_video", "depth_right")}
        queues_configured_j_us = time.monotonic_ns() // 1000
        receipt = {"device_mxid": self._device.getMxId(), "depthai_version": dai.__version__,
                "usb_speed": str(self._device.getUsbSpeed()), "sensors": sensors,
                "sensor_mode_support": "inventory_and_output_shape_only; target_acceptance_pending",
                "calibration": {"eeprom": eeprom, "eeprom_raw_hex": raw.hex(),
                                "eeprom_raw_sha256": hashlib.sha256(raw).hexdigest(),
                                "right_rectification_rotation": calibration.getStereoRightRectificationRotation(),
                                "baseline_cm": calibration.getBaselineDistance(
                                    dai.CameraBoardSocket.CAM_C, dai.CameraBoardSocket.CAM_B, False),
                                "right_to_rgb_extrinsics_cm": calibration.getCameraExtrinsics(
                                    dai.CameraBoardSocket.CAM_C, dai.CameraBoardSocket.CAM_A, False),
                                "active_rectified_intrinsics": None,
                                "metric_calibration_status": "not_evaluated"},
                "graph_declaration": graph, "runtime_config_readback": "not_evaluated",
                "queues": {"host_size": 8, "host_blocking": False,
                           "device_xlink_size": 1, "device_xlink_blocking": False},
                "queues_configured_j_us": str(queues_configured_j_us),
                "nn_requirement": "ABSENT", "jpeg_enabled": False,
                "timestamp_offset": "END", "depth_alignment": "RECTIFIED_RIGHT",
                "depth_unit": "millimeter", "focus_policy": "unlocked_bench_only",
                "capture_ready": False, "metric_ready": False}
        return _json_copy(receipt)

    def sample_clock(self) -> dict:
        """D=SDK steady clock를 J 단조시각 사이에서 읽는다. sync 오차 수락은 별도다."""
        if not self._queues:
            raise RuntimeError("capture queues are not configured")
        before = time.monotonic_ns() // 1000
        sdk = _us(self._dai.Clock.now())
        after = time.monotonic_ns() // 1000
        return {"j_before_us": before, "sdk_us": sdk, "j_after_us": after}

    def poll(self) -> list[RawFrame]:
        """stream당 최대 두 개만 tryGet한다. 파일·broker·다른 worker를 기다리지 않는다."""
        frames = []
        for stream, queue in self._queues.items():
            for _ in range(2):
                message = queue.tryGet()
                if message is None:
                    break
                frames.append(self._frame(stream, message))
        return frames

    def _frame(self, stream, message) -> RawFrame:
        rgb = stream == "rgb_video"
        expected = (640, 360) if rgb else (640, 480)
        expected_type = self._dai.ImgFrame.Type.NV12 if rgb else self._dai.ImgFrame.Type.RAW16
        if ((message.getWidth(), message.getHeight()) != expected
                or message.getType() != expected_type):
            raise ValueError("unexpected OAK frame dimensions/type")
        spec = message.getRaw().fb
        row_bytes = 640 if rgb else 1280
        if (spec.stride not in (0, row_bytes) or spec.p1Offset != 0
                or (rgb and spec.p2Offset not in (0, 640 * 360))
                or (not rgb and spec.p2Offset != 0)
                or spec.p3Offset != 0):
            raise ValueError("unsupported OAK frame stride/plane offsets")
        data = memoryview(message.getData())
        expected_bytes = 640 * 360 * 3 // 2 if rgb else 640 * 480 * 2
        if (data.ndim != 1 or data.itemsize != 1 or not data.c_contiguous
                or data.nbytes != expected_bytes):
            raise ValueError("OAK raw payload is not a contiguous byte plane")
        device_us = _us(message.getTimestampDevice(self._dai.CameraExposureOffset.END))
        sdk_us = _us(message.getTimestamp(self._dai.CameraExposureOffset.END))
        exposure = _us(message.getExposureTime())
        frame = RawFrame(stream, message.getSequenceNum(), device_us, sdk_us,
                         time.monotonic_ns() // 1000, *expected,
                         "NV12" if rgb else "RAW16_LE", bytes(data), exposure if exposure > 0 else None)
        frame.validate()
        current = (frame.sequence, frame.device_us, frame.sdk_us)
        if stream in self._last and any(a <= b for a, b in zip(current, self._last[stream])):
            raise ValueError(f"non-increasing OAK sequence/timestamp in {stream}")
        self._last[stream] = current
        return frame

    def close(self) -> None:
        """전체 Device close가 반환한 뒤에만 lock을 놓는다. hang/예외의 소유 회수는 부모 책임이다."""
        if self._device is not None:
            self._device.close()
            self._device = None
        self._queues.clear()
        if self._lock is not None:
            self._lock.close()
            self._lock = None
