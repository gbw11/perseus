"""DepthAI2.29의 host-frame 전용 SIM YOLOP owner다. 카메라 node를 만들지 않는다.

원 SYNTHETIC 관측/가상 clock와 실제 SDK/J 표본을 별도로 보존한다. host가 넣은
device timestamp는 HOST_SIM metadata이며 실제 OAK 노출/장치 시계 측정이 아니다.
native 호출의 hang을 이 동기 owner가 선점하지 못하므로 부모의 원 기한 감시가 필요하다.
근거: 고정2.29 stub의 XLinkIn/DataInputQueue 및 v2.29.0.0 ImgFrameBindings.cpp.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import importlib
import sys
import time

from ..perception.inputs import PerceptionInput
from ..yolop_profile import YolopBenchProfile
from .device import _DeviceLock, _json_copy, _us
from .yolop_asset import ASSET_ID, load_yolop_asset, validated_sdk_blob
from .yolop_frames import NNInputStamp, NNPacket, sdk_nn_input, sdk_nn_packet


PROFILE_ID = 'oak-host-sim-yolop-v1'
HOST_STREAM = 'host_sim_rgb'
INPUT_BYTES = 640 * 360 * 3 // 2
COPY_CAP_BYTES = 4 * 1024**2
U64_MAX = 2**64 - 1


def j_us():
    return time.monotonic_ns() // 1000


def _uint(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ValueError('SIM time/sequence must be u64')
    return value


def _label(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError('SIM source/clock must be bounded printable ASCII')


def _sha(value):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('SIM context/calibration requires canonical SHA256')


@dataclass(frozen=True)
class SimNnProfile:
    """기존 검증 asset/Device 자원 설정을 SIM 선언으로 감싼다. 물리 profile 자체는 받지 않는다."""
    oak: YolopBenchProfile
    source_id: str
    clock_id: str
    calibration_id: str

    def __post_init__(self):
        if type(self.oak) is not YolopBenchProfile:
            raise ValueError('SIM requires the exact approved YOLOP asset/device settings')
        _label(self.source_id)
        _label(self.clock_id)
        _sha(self.calibration_id)

    def to_dict(self):
        return dict(profile_id=PROFILE_ID, input_kind='SYNTHETIC', camera_nodes=0,
            source_id=self.source_id, clock_id=self.clock_id, calibration_id=self.calibration_id,
            approved_asset_device_settings=self.oak.to_dict())


@dataclass(frozen=True)
class HostClockSample:
    """실제 J 두 번 사이에서 읽은 SDK Clock.now다. 가상 scalar 시각으로 채우지 않는다."""
    j_before_us: int
    sdk_us: int
    j_after_us: int

    def __post_init__(self):
        for value in (self.j_before_us, self.sdk_us, self.j_after_us):
            _uint(value)
        if self.j_before_us > self.j_after_us:
            raise ValueError('nonmonotonic actual SIM host clock sample')


@dataclass(frozen=True)
class SimNnSubmission:
    """원 관측과 원 capture J/E를 보유한다. NN 수신 시 이 시각을 새로 만들지 않는다."""
    observation: PerceptionInput
    captured_j_us: tuple[int, int]
    expires_j_us: int
    clock_sample: HostClockSample
    context_key: str
    payload_sha256: str = field(init=False)

    def __post_init__(self):
        if type(self.observation) is not PerceptionInput or self.observation.kind != 'SYNTHETIC':
            raise ValueError('explicit original SYNTHETIC observation required')
        if type(self.clock_sample) is not HostClockSample:
            raise ValueError('actual SDK/J sample required')
        _sha(self.context_key)
        if type(self.captured_j_us) is not tuple or len(self.captured_j_us) != 2:
            raise ValueError('original capture J interval required')
        lower, upper = map(_uint, self.captured_j_us)
        _uint(self.expires_j_us)
        if not lower <= upper <= self.clock_sample.j_before_us <= self.clock_sample.j_after_us < self.expires_j_us:
            raise ValueError('capture/sample/expiry order is invalid')
        object.__setattr__(self, 'payload_sha256', hashlib.sha256(self.observation.rgb.payload).hexdigest())

    @property
    def key(self):
        # 두 timestamp 필드는 host가 설정한 동일 SDK 표본이며 장치 측정이 아니다.
        return self.observation.rgb.stamp.sequence, self.clock_sample.sdk_us, self.clock_sample.sdk_us

    @property
    def ticket(self):
        """원 frame key와 별도 enqueue key다. 물리 RGB/NN timestamp 동일성으로 위장하지 않는다."""
        return self.observation.rgb.stamp.key, self.key, self.payload_sha256


@dataclass(frozen=True)
class SimNnResult:
    submission: SimNnSubmission
    nn_input: NNInputStamp
    nn_output: NNPacket

    def __post_init__(self):
        if (type(self.submission) is not SimNnSubmission or type(self.nn_input) is not NNInputStamp
                or type(self.nn_output) is not NNPacket):
            raise ValueError('owned SIM submission/passthrough/output required')
        for packet in (self.nn_input, self.nn_output):
            if ((packet.sequence, packet.device_us, packet.sdk_us) != self.submission.key
                    or packet.context_key != self.submission.context_key
                    or not self.submission.clock_sample.j_after_us <= packet.received_j_us < self.submission.expires_j_us):
                raise ValueError('SIM output changed original full input key/context/lifetime')

    def to_perception_input(self, original_observation, *, clock=j_us):
        """원 SYNTHETIC parent에 기존 YOLOP 후보만 붙인다. 물리 stamp adapter는 쓰지 않는다.

        original_observation은 capture ticket의 실제 동일객체여야 한다. clock은 decode 전후
        실제 J를 읽는 port이며 원 E를 대체하는 now/expiry 상수나 새 capture 시각을 만들지 않는다.
        NN key는 원 labels와 별도 host enqueue metadata의 최대8개 scalar다.
        """
        self.__post_init__()
        submission = self.submission
        original = submission.observation
        if (original_observation is not original or type(original) is not PerceptionInput
                or original.kind != 'SYNTHETIC' or original.nn is not None or original.nn_key is not None):
            raise ValueError('SIM adapter requires the identical unprocessed original observation')
        began = _uint(clock())
        latest = max(submission.clock_sample.j_after_us,
            self.nn_input.received_j_us, self.nn_output.received_j_us)
        if began < latest:
            raise ValueError('SIM adapter time precedes original NN availability')
        if began >= submission.expires_j_us:
            raise TimeoutError('original SIM input expired before decode')
        if hashlib.sha256(original.rgb.payload).hexdigest() != submission.payload_sha256:
            raise ValueError('SIM original RGB bytes differ from the capture ticket SHA256')
        self.nn_input.validate()
        self.nn_output.validate()
        stamp = original.rgb.stamp
        key = (stamp.source_id, stamp.stream_id, stamp.clock_id, stamp.sequence,
            self.nn_output.device_us, self.nn_output.sdk_us, submission.context_key, submission.payload_sha256)
        from ..perception.yolop import decode_yolop
        candidates = decode_yolop(self.nn_output)
        if candidates.packet is not self.nn_output:
            raise ValueError('SIM decoded candidates changed original NN packet identity')
        result = PerceptionInput(original.kind, original.calibration_id,
            original.rgb, original.depth, candidates, key)
        completed = _uint(clock())
        if completed < began:
            raise ValueError('nonmonotonic actual SIM decode clock')
        if completed >= submission.expires_j_us:
            raise TimeoutError('original SIM input expired during decode')
        return result


def build_sim_pipeline(dai, asset):
    """실제 SDK graph와 한 번 등록한 blob/metadata를 반환한다. 카메라 graph 확장은 받지 않는다."""
    blob, metadata = validated_sdk_blob(dai, asset)
    pipeline = dai.Pipeline()
    pipeline.setOpenVINOVersion(dai.OpenVINO.Version.VERSION_2021_4)
    host = pipeline.create(dai.node.XLinkIn)
    host.setStreamName(HOST_STREAM)
    host.setMaxDataSize(INPUT_BYTES)
    host.setNumFrames(2)
    manip = pipeline.create(dai.node.ImageManip)
    manip.initialConfig.setResizeThumbnail(320, 320, 0, 0, 0)
    manip.initialConfig.setFrameType(dai.ImgFrame.Type.BGR888p)
    manip.setMaxOutputFrameSize(320 * 320 * 3)
    manip.setNumFramesPool(2)
    manip.setWaitForConfigInput(False)
    manip.inputImage.setQueueSize(1)
    manip.inputImage.setBlocking(False)
    network = pipeline.create(dai.node.NeuralNetwork)
    network.setBlob(blob)
    network.setNumInferenceThreads(1)
    network.setNumPoolFrames(2)
    network.input.setQueueSize(1)
    network.input.setBlocking(False)
    host.out.link(manip.inputImage)
    manip.out.link(network.input)
    for name, output, metadata_only in (('nn_input', network.passthrough, True), ('nn_output', network.out, False)):
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName(name)
        xout.setMetadataOnly(metadata_only)
        xout.input.setQueueSize(1)
        xout.input.setBlocking(False)
        output.link(xout.input)
    if (str(pipeline.getRequiredOpenVINOVersion()) != asset.manifest['sdk_blob_metadata']['host_graph_required_version']
            or pipeline.getOpenVINOVersion() != dai.OpenVINO.Version.VERSION_2021_4):
        raise ValueError('SIM graph/blob OpenVINO mismatch')
    return pipeline, blob, metadata


def sim_graph_declaration(pipeline, asset):
    """전체 graph/asset 직렬화 없이 실제 node/연결과 별도 SIM 설정을 명시한다."""
    native = pipeline.getAllNodes()
    if len(native) != 5:
        raise ValueError('host-only SIM graph must contain exactly5 nodes')
    nodes = []
    for node in native:
        if type(node.id) is not int or not 0 <= node.id < 2**63:
            raise ValueError('invalid SIM graph node ID')
        name = node.getName()
        if type(name) is not str or name not in ('XLinkIn', 'ImageManip', 'NeuralNetwork', 'XLinkOut'):
            raise ValueError('physical/unknown node cannot enter host-only SIM graph')
        nodes.append(dict(id=node.id, name=name))
    ids = {node['id'] for node in nodes}
    if len(ids) != 5 or Counter(node['name'] for node in nodes) != Counter(XLinkIn=1, ImageManip=1, NeuralNetwork=1, XLinkOut=2):
        raise ValueError('invalid SIM node inventory')
    connections = []
    groups = pipeline.getConnectionMap()
    if len(groups) > 5:
        raise ValueError('SIM connection-group budget exceeded')
    for group in groups.values():
        if len(group) + len(connections) > 4:
            raise ValueError('SIM connection budget exceeded')
        for link in group:
            if (type(link.outputId) is not int or type(link.inputId) is not int
                    or link.outputId not in ids or link.inputId not in ids):
                raise ValueError('foreign SIM graph connection')
            if any(type(name) is not str or len(name) > 128 for name in (link.outputName, link.inputName)):
                raise ValueError('invalid SIM graph connection name')
            connections.append(dict(output_id=link.outputId, input_id=link.inputId,
                output_name=link.outputName, input_name=link.inputName))
    if len(connections) != 4:
        raise ValueError('SIM graph must have exactly4 connections')
    return _json_copy(dict(profile_id=PROFILE_ID, input_kind='SYNTHETIC', camera_nodes=0,
        scope='host_SIM_node_connection_declaration', device_config_readback=False,
        nodes=nodes, connections=connections, asset_storage_included=False,
        asset={key: asset.receipt[key] for key in ('asset_id', 'blob_sha256', 'blob_bytes')},
        declared_settings=dict(input_stream=HOST_STREAM, input_size_wh=[640, 360], input_type='NV12',
            input_max_bytes=INPUT_BYTES, host_input_queue_size=1, host_input_blocking=False,
            nn_size_wh=[320, 320], content_size_wh=[320, 180], padding_ltrb=[0, 70, 0, 70],
            padding_bgr=[0, 0, 0], nn_type='BGR888p', inference_threads=1, pool_frames=2,
            host_output_sizes={'nn_input': 8, 'nn_output': 2}, device_queue_size=1,
            device_queue_blocking=False, passthrough_metadata_only=True,
            pixel_equivalence_verified=False,
            device_timestamp_basis='HOST_SIM_SDK_sample_not_device_measurement')))


class SimNnSource:
    """한 Device/graph, 원 입력 하나와 출력 두 개만 보유하는 동기 SIM owner다.

    capture의 captured_j_us는 renderer가 별도로 기록한 실제 원 J 구간이어야 한다.
    observation의 가상 FrameStamp를 수정하거나 그 scalar를 J/SDK로 복사하지 않는다.
    """
    def __init__(self, profile, *, context_key, clock=j_us):
        if type(profile) is not SimNnProfile:
            raise ValueError('dedicated SIM NN profile required; physical profile is unsupported')
        _sha(context_key)
        self.profile, self.context_key, self.clock = profile, context_key, clock
        self._dai = self._device = self._lock = self._asset = self._blob = self._input_queue = None
        self._queues = {}
        self._started = self._closed = False
        self._pending = self._input = self._output = self._last = self._send_failure = None
        self._close_deadline = None
        self._counts = Counter(expired=0, unmatched=0, duplicate=0, completed=0)

    @property
    def counts(self):
        return dict(self._counts)

    def _check(self, deadline):
        if _uint(self.clock()) >= deadline:
            raise TimeoutError('original SIM operation deadline expired')

    def start(self, on_stage, *, startup_deadline_us):
        if self._started or self._closed:
            raise RuntimeError('SIM NN source is single-start')
        self._started = True
        begun = _uint(self.clock())
        deadline = min(_uint(startup_deadline_us), begun + int(self.profile.oak.startup_timeout_s * 1_000_000))
        self._check(deadline)
        on_stage('ASSET_CHECK')
        self._asset = load_yolop_asset(self.profile.oak.asset_dir, self.profile.oak.asset_manifest)
        self._check(deadline)
        if sys.platform != 'linux' or sys.byteorder != 'little':
            raise RuntimeError('SIM OAK NN requires little-endian Linux')
        self._dai = dai = importlib.import_module('depthai')
        if dai.__version__ != '2.29.0.0':
            raise RuntimeError('SIM NN requires DepthAI2.29.0.0')
        pipeline, self._blob, metadata = build_sim_pipeline(dai, self._asset)
        graph = sim_graph_declaration(pipeline, self._asset)
        self._check(deadline)
        self._lock = _DeviceLock(self.profile.oak.device_mxid)
        on_stage('DEVICE_BOOT')
        self._check(deadline)
        found = [item for item in dai.Device.getAllAvailableDevices() if item.getMxId() == self.profile.oak.device_mxid]
        if len(found) != 1:
            raise RuntimeError('approved SIM NN MXID is not uniquely available')
        self._device = dai.Device(config=pipeline.getDeviceConfig(), deviceInfo=found[0])
        if self._device.getMxId() != self.profile.oak.device_mxid:
            raise RuntimeError('SIM NN Device differs from approved MXID')
        on_stage('PIPELINE_START')
        self._check(deadline)
        if self._device.startPipeline(pipeline) is not True:
            raise RuntimeError('SIM startPipeline was not confirmed')
        self._input_queue = self._device.getInputQueue(HOST_STREAM)
        self._input_queue.setMaxSize(1)
        self._input_queue.setBlocking(False)
        self._queues = {name: self._device.getOutputQueue(name=name, maxSize=size, blocking=False)
            for name, size in (('nn_input', 8), ('nn_output', 2))}
        self._check(deadline)
        return _json_copy(dict(profile=self.profile.to_dict(), graph_declaration=graph,
            device_mxid=self._device.getMxId(), depthai_version=dai.__version__, model_id=ASSET_ID,
            model_asset=self._asset.receipt, sdk_blob_metadata=metadata, blob_registration_count=1,
            pipeline_start_count=1, input_kind='SYNTHETIC', nn_context_key=self.context_key,
            nn_ready=False, metric_ready=False, live_authority=False,
            model_residency_evidence='host_verified_asset_and_graph_start_only',
            startup_deadline_us=str(deadline)))

    def capture(self, observation, *, captured_j_us, expires_j_us):
        """원 NV12를 한 번 SDK 소유 버퍼에 복사한다. busy는 입력/clock을 새로 만들지 않는다."""
        if self._closed or not self._queues or self._send_failure is not None:
            raise RuntimeError('SIM NN source is not available')
        if type(observation) is not PerceptionInput or observation.kind != 'SYNTHETIC' or observation.nn is not None:
            raise ValueError('unprocessed original SYNTHETIC input required')
        rgb, p = observation.rgb, self.profile
        if ((rgb.width, rgb.height, rgb.encoding, len(rgb.payload)) != (640, 360, 'NV12', INPUT_BYTES)
                or (rgb.stamp.source_id, rgb.stamp.clock_id, observation.calibration_id)
                != (p.source_id, p.clock_id, p.calibration_id)):
            raise ValueError('SIM original source/calibration/owned NV12 profile mismatch')
        if rgb.stamp.sequence >= 2**63:
            raise ValueError('HOST_SIM SDK sequence must fit signed int64')
        now = _uint(self.clock())
        self._expire(now)
        if self._pending is not None:
            return None
        sample = HostClockSample(now, _us(self._dai.Clock.now()), _uint(self.clock()))
        submission = SimNnSubmission(observation, captured_j_us, expires_j_us, sample, self.context_key)
        if expires_j_us - captured_j_us[0] > int(p.oak.nn_stall_timeout_s * 1_000_000):
            raise ValueError('SIM original NN lifetime exceeds approved timeout')
        current = observation.rgb.stamp.sequence, sample.sdk_us
        if self._last is not None and any(a <= b for a, b in zip(current, self._last)):
            raise ValueError('non-increasing original SIM sequence/SDK timestamp')
        import numpy as np
        frame = self._dai.ImgFrame()
        frame.setWidth(640)
        frame.setHeight(360)
        frame.setType(self._dai.ImgFrame.Type.NV12)
        frame.setSequenceNum(current[0])
        stamp = timedelta(microseconds=sample.sdk_us)
        frame.setTimestamp(stamp)
        frame.setTimestampDevice(stamp)
        frame.setData(np.frombuffer(rgb.payload, dtype=np.uint8))
        self._check(expires_j_us)
        self._pending, self._last = submission, current
        try:
            self._input_queue.send(frame)
        except Exception as error:
            # 전송 불명을 새 nonce/key의 재시도로 숨기지 않는다. 원 pending/owner를 유지한다.
            self._send_failure = type(error).__name__
            raise
        return submission

    def _expire(self, now):
        if self._pending is not None and now >= self._pending.expires_j_us:
            self._pending = self._input = self._output = None
            self._counts['expired'] += 1

    def poll(self):
        """stream별1개, 최대1MiB NN copy다. 원 key 전부 일치한 owned 결과만 반환한다."""
        if self._closed or not self._queues or self._send_failure is not None:
            raise RuntimeError('SIM NN source is not available')
        self._expire(_uint(self.clock()))
        copied = 0
        for stream, queue in self._queues.items():
            message = queue.tryGet()
            if message is None:
                continue
            pending = self._pending
            if pending is None:
                self._counts['unmatched'] += 1
                continue
            key = message.getSequenceNum(), _us(message.getTimestampDevice()), _us(message.getTimestamp())
            if key != pending.key:
                self._counts['unmatched'] += 1
                continue
            if (self._input if stream == 'nn_input' else self._output) is not None:
                self._counts['duplicate'] += 1
                continue
            received = _uint(self.clock())
            packet = (sdk_nn_input if stream == 'nn_input' else sdk_nn_packet)(message, received, self.context_key)
            copied += len(getattr(packet, 'payload', b''))
            if copied > COPY_CAP_BYTES:
                raise ValueError('SIM collector copy budget exceeded')
            if stream == 'nn_input':
                self._input = packet
            else:
                self._output = packet
            self._expire(_uint(self.clock()))
        if self._pending is None or self._input is None or self._output is None:
            return ()
        result = SimNnResult(self._pending, self._input, self._output)
        self._expire(_uint(self.clock()))
        if self._pending is None:
            return ()
        self._pending = self._input = self._output = None
        self._counts['completed'] += 1
        return (result,)

    def close(self, *, deadline_us):
        """실제 Device close 성공 뒤 lock을 놓는다. 첫 원 close 기한은 retry로 연장하지 않는다."""
        if self._closed:
            return
        now = _uint(self.clock())
        cap = min(_uint(deadline_us), now + int(self.profile.oak.close_timeout_s * 1_000_000))
        self._close_deadline = cap if self._close_deadline is None else min(cap, self._close_deadline)
        self._check(self._close_deadline)
        if self._device is not None:
            self._device.close()
            self._device = None
        self._queues.clear()
        self._input_queue = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None
        self._pending = self._input = self._output = self._asset = self._blob = None
        self._closed = True
        self._check(self._close_deadline)
