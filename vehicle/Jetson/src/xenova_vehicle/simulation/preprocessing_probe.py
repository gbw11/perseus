"""실제 SIM NN 입력의 BGR planar/letterbox pixels를 읽는 명시적 진단 probe다.

production graph builder를 재사용하되 nn_input XLinkOut의 metadataOnly만 바꾼 별도
variant다. 수치 차이는 사실로 기록하며 보간 허용값·정확도·READY를 발급하지 않는다.
동기 SDK 호출을 선점할 수 없으므로 실행자는 별도 bounded supervisor를 두어야 한다.
"""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import importlib
from itertools import permutations
import json
import operator
from pathlib import Path
import sys
import tempfile
import time
import traceback
from uuid import uuid4

from ..oak.device import _DeviceLock, _us
from ..oak.sim_nn import HOST_STREAM, HostClockSample, build_sim_pipeline, sim_graph_declaration
from ..oak.yolop_asset import load_yolop_asset
from ..oak.yolop_frames import MAX_NN_BYTES, sdk_nn_packet
from ..yolop_profile import YolopBenchProfile
from .capture import _nv12
from .scene import canonical


PLANAR_BYTES = 3 * 320 * 320
PIXEL_COPY_CAP = 1_048_576
COLLECTOR_COPY_CAP = 4 * 1024**2
INPUT_FORMATS = ('NV12', 'BGR888p')


def j_us():
    return time.monotonic_ns() // 1000


def gold_bgr():
    """BGR color bands·4px checker·비대칭 edge marks. renderer/GT mask는 사용하지 않는다."""
    import numpy as np
    image = np.empty((360, 640, 3), dtype=np.uint8)
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        (255, 0, 255), (0, 255, 255), (128, 128, 128), (0, 0, 0))
    for index, color in enumerate(colors):
        image[:, index*80:(index+1)*80] = color
    yy, xx = np.indices((128, 192))
    checker = (((xx//4 + yy//4) % 2)*255).astype(np.uint8)
    image[112:240, 224:416] = checker[..., None]
    image[0:6, 0:6] = (0, 0, 255)
    image[0:10, -10:] = (0, 255, 0)
    image[-14:, 0:14] = (255, 0, 0)
    image[-18:, -18:] = (255, 255, 255)
    image[179:181] = (255, 255, 255)
    image[:, 319:321] = (0, 0, 0)
    return np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(360, 640, 3)


def _decoded_nv12(raw):
    import cv2
    import numpy as np
    if type(raw) is not bytes or len(raw) != 640*360*3//2:
        raise ValueError('original owned640x360 NV12 required')
    return cv2.cvtColor(np.frombuffer(raw, dtype=np.uint8).reshape(540, 640), cv2.COLOR_YUV2BGR_NV12)


def host_input_from_nv12(raw, input_format):
    """BGR control도 같은 원 NV12를 decode한 pixels를 쓴다. 색 범위 rescale은 하지 않는다."""
    if type(input_format) is not str or input_format not in INPUT_FORMATS:
        raise ValueError('explicit diagnostic NV12 or BGR888p input format required')
    if input_format == 'NV12':
        if type(raw) is not bytes or len(raw) != 345600:
            raise ValueError('original640x360 NV12 bytes required')
        return raw
    return _decoded_nv12(raw).transpose(2, 0, 1).tobytes(order='C')


def reference_from_nv12(raw):
    """두 입력 control의 공통 reference다. 원 NV12 decode→linear resize→상하70 black pad다."""
    import cv2
    import numpy as np
    decoded = _decoded_nv12(raw)
    content = cv2.resize(decoded, (320, 180), interpolation=cv2.INTER_LINEAR)
    reference = np.zeros((320, 320, 3), dtype=np.uint8)
    reference[70:250] = content
    return reference.transpose(2, 0, 1).tobytes(order='C')


def compare_planar(observed, reference):
    """허용오차 없이 exact equality와 원 차이 집계를 반환한다. 채널 재배열을 적용하지 않는다."""
    import numpy as np
    if any(type(raw) is not bytes or len(raw) != PLANAR_BYTES for raw in (observed, reference)):
        raise ValueError('both original planar buffers must be exactly3x320x320 uint8')
    actual = np.frombuffer(observed, dtype=np.uint8).reshape(3, 320, 320)
    expected = np.frombuffer(reference, dtype=np.uint8).reshape(3, 320, 320)
    if np.any(expected[:, :70]) or np.any(expected[:, 250:]):
        raise ValueError('declared host reference must contain exact black top/bottom padding')
    delta = np.abs(actual.astype(np.int16)-expected.astype(np.int16))
    content = delta[:, 70:250]
    channel_errors = [dict(channel=name, max_abs_error=int(delta[index].max()),
        mean_abs_error=float(delta[index].mean()), content_mean_abs_error=float(content[index].mean()))
        for index, name in enumerate(('B', 'G', 'R'))]
    orders = []
    for order in permutations(range(3)):
        changed = np.abs(actual[list(order)].astype(np.int16)-expected.astype(np.int16))
        orders.append(dict(observed_indices_for_BGR=list(order), mean_abs_error=float(changed.mean()),
            content_mean_abs_error=float(changed[:, 70:250].mean())))
    return dict(exact_byte_equal=observed == reference, shape_chw=[3, 320, 320], dtype='uint8',
        max_abs_error=int(delta.max()), mean_abs_error=float(delta.mean()), per_channel=channel_errors,
        content_roi_ltrb=[0, 70, 320, 250], content_max_abs_error=int(content.max()),
        content_mean_abs_error=float(content.mean()),
        padding_top_nonzero=int(np.count_nonzero(actual[:, :70])),
        padding_bottom_nonzero=int(np.count_nonzero(actual[:, 250:])), channel_permutations=orders,
        permutation_semantics='diagnostic observed-index reorder only; not applied to output',
        tolerance=None, interpolation_reference='OpenCV INTER_LINEAR',
        normalization_applied=False, physical_acceptance=False, model_accuracy_evaluated=False)


def diagnostic_pipeline(dai, asset, *, input_format='NV12'):
    """원 builder 뒤 full-pixel flag와 명시 BGR control의 host buffer cap만 바꾼다."""
    if type(input_format) is not str or input_format not in INPUT_FORMATS:
        raise ValueError('unsupported diagnostic input format')
    pipeline, blob, metadata = build_sim_pipeline(dai, asset)
    original = sim_graph_declaration(pipeline, asset)
    matches = [node for node in pipeline.getAllNodes()
        if node.getName() == 'XLinkOut' and node.getStreamName() == 'nn_input']
    if len(matches) != 1:
        raise ValueError('exact one original nn_input XLinkOut required')
    node, = matches
    before = node.getMetadataOnly()
    if before is not True:
        raise ValueError('original SIM passthrough metadataOnly declaration differs')
    node.setMetadataOnly(False)
    after = node.getMetadataOnly()
    if after is not False:
        raise ValueError('diagnostic passthrough full-pixel flag was not confirmed by host getter')
    mutation = dict(node_id=node.id, stream_name='nn_input', property='metadataOnly', before=before, after=after)
    deltas = [mutation]
    if input_format == 'BGR888p':
        hosts = [item for item in pipeline.getAllNodes()
            if item.getName() == 'XLinkIn' and item.getStreamName() == HOST_STREAM]
        if len(hosts) != 1:
            raise ValueError('exact one original HOST_SIM input stream required')
        host, = hosts
        previous_size = host.getMaxDataSize()
        if previous_size != 345600:
            raise ValueError('original NV12 host input cap differs')
        host.setMaxDataSize(691200)
        if host.getMaxDataSize() != 691200:
            raise ValueError('diagnostic BGR host input cap was not confirmed by host getter')
        deltas.append(dict(node_id=host.id, stream_name=HOST_STREAM,
            property='maxDataSize', before=previous_size, after=691200))
    declaration = dict(original=original, deltas=deltas, host_input_format=input_format)
    return pipeline, blob, metadata, dict(diagnostic_variant_only=True,
        original_host_graph_declaration=original,
        mutation=mutation, graph_deltas=deltas, input_format=input_format,
        input_message_type_changed=input_format != 'NV12',
        graph_delta_sha256=hashlib.sha256(canonical(deltas)).hexdigest(),
        diagnostic_declaration_sha256=hashlib.sha256(canonical(declaration)).hexdigest(),
        verification='HOST_GETTER_ONLY_NOT_DEVICE_READBACK', production_graph_unchanged_claim=False)


@dataclass(frozen=True)
class PassthroughPixels:
    """SDK 수명에서 분리한 원 buffer와 bounded metadata. shape 이상도 원문 그대로 보유한다."""
    payload: bytes
    metadata_bytes: bytes
    canonical_payload: bytes | None = None

    def __post_init__(self):
        if (type(self.payload) is not bytes or len(self.payload) > PIXEL_COPY_CAP
                or type(self.metadata_bytes) is not bytes or len(self.metadata_bytes) > 8192
                or self.canonical_payload is not None and (type(self.canonical_payload) is not bytes
                    or len(self.canonical_payload) > PIXEL_COPY_CAP)):
            raise ValueError('bounded owned passthrough bytes/metadata required')

    @property
    def metadata(self):
        return json.loads(self.metadata_bytes)

    @property
    def key(self):
        fields = self.metadata
        return fields['sequence'], fields['device_us'], fields['sdk_us']


def _int(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('original SDK pixel metadata must be uint64')
    return value


def read_passthrough(message, received_j_us):
    """원 buffer와 SDK getFrame(copy=True)를 비교한다. fb offsets를 host index로 추정하지 않는다."""
    with memoryview(message.getData()) as view:
        if view.ndim != 1 or view.itemsize != 1 or view.format != 'B' or not view.c_contiguous or view.nbytes > PIXEL_COPY_CAP:
            raise ValueError('passthrough original buffer exceeds contiguous uint8/1MiB bound')
        payload = view.tobytes()
    raw = message.getRaw()
    frame_type = message.getType()
    code = operator.index(frame_type)
    fields = dict(stream='nn_input', sequence=_int(message.getSequenceNum()),
        device_us=_int(_us(message.getTimestampDevice())), sdk_us=_int(_us(message.getTimestamp())),
        received_j_us=_int(received_j_us), width=_int(message.getWidth()), height=_int(message.getHeight()),
        type_code=_int(code), type_name=str(frame_type), payload_length=len(payload),
        fb={name: _int(getattr(raw.fb, name)) for name in ('width', 'height', 'stride', 'p1Offset', 'p2Offset', 'p3Offset')})
    fields['shape_type_length_ok'] = (fields['width'], fields['height'], code, len(payload)) == (320, 320, 8, PLANAR_BYTES)
    fields['fb_offset_semantics'] = 'NOT_INTERPRETED_AS_HOST_PAYLOAD_OFFSETS'
    fields['buffer_interpretation'] = 'SDK2.29 getFrame(copy=True) BGR888p CHW compared with original getData bytes'
    fields['sdk_canonical_raw_equal'] = None
    canonical_payload = None
    if fields['shape_type_length_ok']:
        import numpy as np
        try:
            copied = message.getFrame(copy=True)
            if type(copied) is not np.ndarray:
                raise ValueError('SDK canonical frame did not return an ndarray')
            fields['sdk_get_frame_copy'] = dict(shape=list(copied.shape), dtype=copied.dtype.str,
                strides=list(copied.strides), nbytes=copied.nbytes, c_contiguous=bool(copied.flags.c_contiguous))
            if (copied.shape != (3, 320, 320) or copied.dtype != np.uint8
                    or copied.strides != (102400, 320, 1) or copied.nbytes != PLANAR_BYTES
                    or not copied.flags.c_contiguous):
                raise ValueError('SDK canonical CHW shape/dtype/strides differ')
            canonical_payload = copied.tobytes(order='C')
            fields['sdk_canonical_raw_equal'] = canonical_payload == payload
            fields['sdk_get_frame_copy']['sha256'] = hashlib.sha256(canonical_payload).hexdigest()
        except Exception as error:
            fields['sdk_get_frame_error'] = dict(type=type(error).__name__, message=str(error)[:256])
    return PassthroughPixels(payload, canonical(fields), canonical_payload)


def _write(directory, name, raw):
    with (directory/name).open('xb') as stream:
        stream.write(raw)
    return dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def _json(directory, name, value):
    raw = canonical(value)
    if len(raw) > 262_144:
        raise ValueError('pixel probe JSON exceeds256KiB')
    return _write(directory, name, raw)


def _png(directory, name, image):
    import cv2
    ok, encoded = cv2.imencode('.png', image)
    if not ok or encoded.nbytes > 2*1024**2:
        raise ValueError('pixel probe PNG exceeds bounded image encoding')
    return _write(directory, name, encoded.tobytes())


def _artifacts(directory, original_bgr, nv12, reference, pixels, nn_packet, *, host_input=None, input_format='NV12'):
    """원 payload만 저장한다. 저장 실패는 바깥 Device close 경로를 건너뛰지 않는다."""
    import cv2
    import numpy as np
    files = []
    if original_bgr is not None:
        files.append(_write(directory, 'gold-original.bgr8', original_bgr.tobytes()))
        files.append(_png(directory, 'gold-original.png', original_bgr))
    if nv12 is not None:
        files.append(_write(directory, 'gold-input.nv12', nv12))
        decoded = cv2.cvtColor(np.frombuffer(nv12, dtype=np.uint8).reshape(540, 640), cv2.COLOR_YUV2BGR_NV12)
        files.append(_png(directory, 'gold-nv12-decoded.png', decoded))
    if host_input is not None and input_format == 'BGR888p':
        files.append(_write(directory, 'host-input.bgr888p', host_input))
    if reference is not None:
        files.append(_write(directory, 'reference.bgr888p', reference))
        files.append(_png(directory, 'reference.png', np.frombuffer(reference, dtype=np.uint8).reshape(3, 320, 320).transpose(1, 2, 0)))
    if pixels is not None:
        files.append(_write(directory, 'observed.bgr888p', pixels.payload))
        files.append(_json(directory, 'observed-metadata.json', pixels.metadata))
        if pixels.canonical_payload is not None:
            files.append(_write(directory, 'sdk-get-frame-copy.bgr888p', pixels.canonical_payload))
            files.append(_png(directory, 'sdk-get-frame-copy.png',
                np.frombuffer(pixels.canonical_payload, dtype=np.uint8).reshape(3, 320, 320).transpose(1, 2, 0)))
        if pixels.metadata['shape_type_length_ok']:
            files.append(_png(directory, 'observed.png', np.frombuffer(pixels.payload, dtype=np.uint8).reshape(3, 320, 320).transpose(1, 2, 0)))
    if nn_packet is not None:
        files.append(_write(directory, 'nn-buffer.bin', nn_packet.payload))
        files.append(_json(directory, 'nn-packet.json', nn_packet.descriptor()))
    return files


def run_preprocessing_probe(profile, output_root, *, input_format='NV12'):
    """유일 원 gold frame을 실제 Device에 한 번만 전송한다. 자동 retry/재시각화는 없다.

    startup≤90s, capture부터NN≤5s, close≤15s 원 기한을 고정한다. SDK native hang은
    이 함수가 끊지 못하므로 명시 CLI의 바깥 supervisor가 별도로 감시해야 한다.
    """
    if type(profile) is not YolopBenchProfile:
        raise ValueError('explicit approved YOLOP device/asset profile required')
    if type(input_format) is not str or input_format not in INPUT_FORMATS:
        raise ValueError('explicit diagnostic input format required')
    root = Path(output_root).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='s23-preprocessing-', dir=root))
    report = dict(profile='sim-nn-pixel-golden-diagnostic-1', status='FAILED', exit_code=1,
        files=[], source_kind='SYNTHETIC', diagnostic_variant_only=True, send_count=0,
        input_format=input_format, range_conversion_cause='UNCONFIRMED_NO_PINNED_FIRMWARE_MATRIX_SOURCE',
        send_count_semantics='attempts_no_retry_not_NN_completion',
        source_id='host-sim-gold:'+str(uuid4()), clock_id='jetson-monotonic:'+str(uuid4()),
        live_authority=False, physical_acceptance=False, model_accuracy_evaluated=False,
        supervisor_required=True, nn_packet_cap_bytes=MAX_NN_BYTES, collector_copy_cap_bytes=COLLECTOR_COPY_CAP)
    device = lock = None
    pixels = nn_packet = original_bgr = nv12 = reference = host_input = None
    try:
        if sys.platform != 'linux' or sys.byteorder != 'little':
            raise RuntimeError('actual pixel probe requires little-endian Linux')
        started = j_us()
        startup_e = started+min(90_000_000, int(profile.startup_timeout_s*1_000_000))
        report['startup_deadline_j_us'] = startup_e

        def current(deadline):
            if j_us() >= deadline:
                raise TimeoutError('original pixel probe deadline expired')

        asset = load_yolop_asset(profile.asset_dir, profile.asset_manifest)
        current(startup_e)
        dai = importlib.import_module('depthai')
        if dai.__version__ != '2.29.0.0':
            raise ValueError('pixel probe requires DepthAI2.29.0.0')
        pipeline, blob, metadata, graph = diagnostic_pipeline(dai, asset, input_format=input_format)
        report['files'].append(_json(directory, 'graph-receipt.json', graph))
        report['files'].append(_json(directory, 'asset-receipt.json', dict(asset=asset.receipt,
            sdk_blob_metadata=metadata, input_contract=asset.manifest['input'],
            preprocessing_profile=asset.manifest['preprocessing_profile'],
            host_normalization_added=False, blob_registration_count=1)))
        current(startup_e)
        lock = _DeviceLock(profile.device_mxid)
        found = [item for item in dai.Device.getAllAvailableDevices() if item.getMxId() == profile.device_mxid]
        if len(found) != 1:
            raise RuntimeError('approved probe MXID is not uniquely available')
        device = dai.Device(config=pipeline.getDeviceConfig(), deviceInfo=found[0])
        current(startup_e)
        if device.getMxId() != profile.device_mxid or device.startPipeline(pipeline) is not True:
            raise RuntimeError('probe Device identity/start was not confirmed')
        input_queue = device.getInputQueue(HOST_STREAM)
        input_queue.setMaxSize(1)
        input_queue.setBlocking(False)
        queues = {name: device.getOutputQueue(name=name, maxSize=size, blocking=False)
            for name, size in (('nn_input', 8), ('nn_output', 2))}
        current(startup_e)
        report['device_mxid'] = device.getMxId()
        report['depthai_version'] = dai.__version__
        report['pipeline_start_count'] = 1
        # library 준비는 픽셀 capture 전에 한다. 기존 pixels를 새 시각으로 발급하지 않는다.
        import numpy as np
        import cv2  # capture 전에 해당 기존 library를 준비한다.
        before = j_us()
        original_bgr = gold_bgr()
        nv12 = _nv12(original_bgr)
        after = j_us()
        nn_e = before+min(5_000_000, int(profile.nn_stall_timeout_s*1_000_000))
        host_input = host_input_from_nv12(nv12, input_format)
        reference = reference_from_nv12(nv12)
        sdk_before = j_us()
        sdk_sample = HostClockSample(sdk_before, _us(dai.Clock.now()), j_us())
        expected_key = (1, sdk_sample.sdk_us, sdk_sample.sdk_us)
        context = hashlib.sha256(canonical(dict(source_id=report['source_id'], key=expected_key,
            payload_sha256=hashlib.sha256(host_input).hexdigest()))).hexdigest()
        report['input'] = dict(sequence=1, host_capture_j_us=[before, after], expires_j_us=nn_e,
            enqueue_clock=as_clock_fields(sdk_sample), expected_key=expected_key, context_key=context,
            base_nv12_sha256=hashlib.sha256(nv12).hexdigest(),
            reference_sha256=hashlib.sha256(reference).hexdigest(),
            payload_sha256=hashlib.sha256(host_input).hexdigest(), input_payload_bytes=len(host_input),
            input_stage='ORIGINAL_NV12' if input_format == 'NV12' else 'HOST_DECODE_SAME_NV12_TO_BGR_PLANAR',
            device_timestamp_basis='HOST_SIM_METADATA_NOT_REAL_DEVICE_MEASUREMENT')
        frame = dai.ImgFrame()
        frame.setWidth(640)
        frame.setHeight(360)
        frame.setType(dai.ImgFrame.Type.NV12 if input_format == 'NV12' else dai.ImgFrame.Type.BGR888p)
        frame.setSequenceNum(1)
        frame.setTimestamp(timedelta(microseconds=sdk_sample.sdk_us))
        frame.setTimestampDevice(timedelta(microseconds=sdk_sample.sdk_us))
        frame.setData(np.frombuffer(host_input, dtype=np.uint8))
        current(nn_e)
        report['send_attempted_j_us'] = j_us()
        report['send_count'] = 1
        input_queue.send(frame)
        report['send_returned_j_us'] = j_us()
        report['host_queue_send_returned'] = True
        while j_us() < nn_e and (pixels is None or nn_packet is None):
            for stream, queue in queues.items():
                if (stream == 'nn_input' and pixels is not None) or (stream == 'nn_output' and nn_packet is not None):
                    continue
                message = queue.tryGet()
                if message is None:
                    continue
                received = j_us()
                if stream == 'nn_input':
                    pixels = read_passthrough(message, received)
                else:
                    nn_packet = sdk_nn_packet(message, received, context)
                if (0 if pixels is None else len(pixels.payload)+len(pixels.canonical_payload or b''))+(0 if nn_packet is None else len(nn_packet.payload)) > COLLECTOR_COPY_CAP:
                    raise ValueError('probe original collector copy cap exceeded')
            if pixels is None or nn_packet is None:
                time.sleep(.002)
        paired_j = j_us()
        report['pair_completed_j_us'] = paired_j
        report['expired'] = paired_j >= nn_e
        if pixels is None or nn_packet is None:
            raise TimeoutError('original single input has no complete actual passthrough/NN pair')
        nn_key = nn_packet.sequence, nn_packet.device_us, nn_packet.sdk_us
        report['full_key_match'] = pixels.key == nn_key == expected_key
        report['shape_type_length_ok'] = pixels.metadata['shape_type_length_ok']
        report['fb_offset_semantics'] = pixels.metadata['fb_offset_semantics']
        report['sdk_canonical_raw_equal'] = pixels.metadata['sdk_canonical_raw_equal']
        if pixels.metadata['shape_type_length_ok']:
            report['comparison'] = compare_planar(pixels.payload, reference)
        exact = report.get('comparison', {}).get('exact_byte_equal', False)
        good = report['full_key_match'] and report['shape_type_length_ok'] and report['sdk_canonical_raw_equal'] is True and not report['expired']
        report.update(status='EXACT_MATCH' if good and exact else 'PIXEL_DIFFERENCE' if good else 'FAILED',
            exit_code=0 if good and exact else 2 if good else 1)
        del blob
    except Exception as error:
        report['error'] = dict(type=type(error).__name__, message=str(error)[:512])
        trace = traceback.format_exc().encode('utf-8')
        report['files'].append(_write(directory, 'error.txt', trace[:16384]))
        report['error_trace_truncated'] = len(trace) > 16384
    finally:
        # 결과/오류가 나도 원 gold/reference/받은 SDK bytes를 먼저 보존한다.
        try:
            report['files'].extend(_artifacts(directory, original_bgr, nv12, reference, pixels, nn_packet,
                host_input=host_input, input_format=input_format))
        except Exception as error:
            report.update(status='FAILED', exit_code=1,
                artifact_error=dict(type=type(error).__name__, message=str(error)[:512]))
        close_started = j_us()
        close_e = close_started+min(15_000_000, int(profile.close_timeout_s*1_000_000))
        report['close_started_j_us'], report['close_deadline_j_us'] = close_started, close_e
        try:
            if device is not None:
                device.close()
            if lock is not None:
                lock.close()
            report['close_returned_before_e'] = j_us() < close_e
            if not report['close_returned_before_e']:
                raise TimeoutError('original probe close deadline exceeded')
        except Exception as error:
            report.update(status='FAILED', exit_code=1,
                close_error=dict(type=type(error).__name__, message=str(error)[:512]))
        _json(directory, 'result.json', report)
    return directory, report


def as_clock_fields(sample):
    return dict(j_before_us=sample.j_before_us, sdk_us=sample.sdk_us, j_after_us=sample.j_after_us)
