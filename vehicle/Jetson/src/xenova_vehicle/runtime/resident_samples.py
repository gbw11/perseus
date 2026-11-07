"""상주 A의 원 관측을 P0에 넣고 M의 owned P0를 SDK 없는 값으로 복원한다.

NN stream 별칭만 경계에서 바꾼다. 원 seq/device/SDK/J·노출·bridge는 보존하며
depth는 RGB 정렬로 바꾸지 않는다. 이 값은 metric/world/출력 허가가 아니다.
"""

from collections import OrderedDict, deque
from dataclasses import dataclass

from ..oak.frames import RawFrame
from ..oak.observations import ClockEvidence, ObservationTracker
from ..oak.yolop_association import YolopAssociation
from ..oak.yolop_frames import NNInputStamp, NNPacket, RuntimeTensor
from .raw_slot import METADATA_BYTES, OwnedSlot, P0Context, encode_owned


@dataclass(frozen=True)
class CaptureSample:
    rgb: RawFrame
    depth: RawFrame
    nn_input: NNInputStamp | None
    nn_output: NNPacket | None
    clocks: dict

    @property
    def key(self):
        return self.rgb.sequence, self.rgb.device_us, self.nn_output is not None

    def fresh(self, now_us, max_age_us=500_000):
        return all(clock.get('sdk_host_timestamp_j_bracket_us') is not None
                   and 0 <= now_us - int(clock['sdk_host_timestamp_j_bracket_us'][0]) <= max_age_us
                   for clock in self.clocks.values())


def _stamp(item, stream_id, clock, *, nn=False):
    return dict(source_key=[stream_id, str(item.sequence), str(item.device_us)], sdk_us=str(item.sdk_us),
                received_j_us=str(item.received_j_us), bridge_sample=clock['bridge_sample'],
                sdk_host_j_interval_us=clock['sdk_host_timestamp_j_bracket_us'],
                exposure_us=None if nn else item.exposure_us, capture_j_interval_us=None)


def encode_sample(sample, write_key, binding):
    """실제 grant key와 전체 binding의 hash에 원 payload를 결박한다. 아직 copy/post 사실은 아니다."""
    streams = binding.fields['stream_instances']
    configured = 'nn_input0' in streams
    if write_key.scope.raw_binding_sha256 != binding.sha256:
        raise ValueError('write grant differs from accepted raw binding')
    if (sample.nn_input is None) != (sample.nn_output is None):
        raise ValueError('NN presence needs both original input and output')
    sample.rgb.validate()
    sample.depth.validate()
    values = {'rgb_video': _stamp(sample.rgb, streams['rgb_video'], sample.clocks['rgb_video']),
              'depth_right': _stamp(sample.depth, streams['depth_right'], sample.clocks['depth_right'])}
    planes = {}
    body = bytearray()
    for name, payload, dtype, encoding, shape, strides in (
        ('rgb_video', sample.rgb.payload, 'u1', 'NV12', [540, 640], [640, 1]),
        ('depth_right', sample.depth.payload, '<u2', 'RAW16_LE', [480, 640], [1280, 2])):
        body.extend(bytes((-len(body)) % 64))
        planes[name] = dict(offset=METADATA_BYTES + len(body), length=len(payload), dtype=dtype,
                            encoding=encoding, shape=shape, strides_bytes=strides)
        body.extend(payload)
    nn = None
    if sample.nn_output is not None:
        packet, original_input = sample.nn_output, sample.nn_input
        if packet.context_key != binding.sha256 or original_input.context_key != binding.sha256:
            raise ValueError('NN source context differs from accepted raw binding')
        packet.validate()
        original_input.validate()
        values['nn_input0'] = _stamp(original_input, streams['nn_input0'], sample.clocks['nn_input'], nn=True)
        values['nn_output0'] = _stamp(packet, streams['nn_output0'], sample.clocks['nn_output'], nn=True)
        body.extend(bytes((-len(body)) % 64))
        planes['nn_output0'] = dict(offset=METADATA_BYTES + len(body), length=len(packet.payload), dtype='u1',
                                    encoding='NN_STORAGE', shape=[len(packet.payload)], strides_bytes=[1])
        body.extend(packet.payload)
        nn = dict(rgb_parent_key=values['rgb_video']['source_key'], input_key=values['nn_input0']['source_key'],
                  output_key=values['nn_output0']['source_key'], layers=[
                      dict(name=t.name, data_type_code=t.data_type_code, order_code=t.order_code,
                           dims=list(t.dims), strides_bytes=list(t.strides), offset=t.offset, length=t.byte_length)
                      for t in packet.tensors])
    descriptor = dict(version=1, profile='p0-yolop-320-v1', raw_binding_sha256=binding.sha256,
                      streams=values, planes=planes, nn=nn)
    return encode_owned(P0Context(write_key, streams, configured), descriptor, bytes(body))


def decode_sample_slot(owned, binding):
    """token/view 반환 뒤에도 유효한 owned frame/NN을 만든다. REPLAY 전용 decoder를 가장하지 않는다."""
    if type(owned) is not OwnedSlot or owned.expected.context.write_key.scope.raw_binding_sha256 != binding.sha256:
        raise ValueError('owned slot differs from current raw binding')
    descriptor, body = owned.descriptor(), owned.payload_bytes()
    streams, planes = descriptor['streams'], descriptor['planes']
    clocks = {}

    def fields(name, alias=None):
        value = streams[name]
        clocks[alias or name] = dict(bridge_sample=value['bridge_sample'],
                                    sdk_host_timestamp_j_bracket_us=value['sdk_host_j_interval_us'])
        return (int(value['source_key'][1]), int(value['source_key'][2]),
                int(value['sdk_us']), int(value['received_j_us']))

    def payload(name):
        plane = planes[name]
        start = plane['offset'] - METADATA_BYTES
        return body[start:start + plane['length']]

    rgb = RawFrame('rgb_video', *fields('rgb_video'), 640, 360, 'NV12', payload('rgb_video'),
                   streams['rgb_video']['exposure_us'])
    depth = RawFrame('depth_right', *fields('depth_right'), 640, 480, 'RAW16_LE', payload('depth_right'),
                     streams['depth_right']['exposure_us'])
    rgb.validate()
    depth.validate()
    original_input = packet = None
    if descriptor['nn'] is not None:
        original_input = NNInputStamp('nn_input', *fields('nn_input0', 'nn_input'), 320, 320,
                                      'BGR888p', binding.sha256)
        tensors = tuple(RuntimeTensor(t['name'], t['data_type_code'], t['order_code'], len(t['dims']),
                                      tuple(t['dims']), tuple(t['strides_bytes']), t['offset'])
                        for t in descriptor['nn']['layers'])
        packet = NNPacket('nn_output', *fields('nn_output0', 'nn_output'), payload('nn_output0'),
                          tensors, binding.sha256)
    return CaptureSample(rgb, depth, original_input, packet, clocks)


class CaptureAssembler:
    """8개 ring에서 RGB/depth 후보와 RGB/input/output 삼중 연결을 유지한다. 재전송은 새 source가 아니다."""

    def __init__(self, binding, sync_tolerance_us, queue_sdk_barrier):
        self.binding = binding
        self.tolerance = sync_tolerance_us
        self.barrier = queue_sdk_barrier
        self.bridge = ClockEvidence()
        self.tracker = ObservationTracker(sync_tolerance_us)
        self.association = YolopAssociation(binding.sha256)
        self.raw = {'rgb_video': deque(maxlen=8), 'depth_right': deque(maxlen=8)}
        self.clocks = OrderedDict()
        self.latest_capture = self.latest_nn = None

    @staticmethod
    def _key(item):
        return item.stream, item.sequence, item.device_us, item.sdk_us

    def _sample(self, rgb, depth, original_input=None, packet=None):
        items = (rgb, depth) if packet is None else (rgb, depth, original_input, packet)
        return CaptureSample(rgb, depth, original_input, packet,
                             {item.stream: self.clocks[self._key(item)] for item in items})

    def observe(self, item):
        if item.sdk_us <= self.barrier:
            return []
        clock = self.bridge.describe(item)
        if not 0 <= item.received_j_us - int(clock['sdk_host_timestamp_j_bracket_us'][0]) <= 500_000:
            return []
        self.clocks[self._key(item)] = clock
        while len(self.clocks) > 48:
            self.clocks.popitem(last=False)
        pairs = []
        if isinstance(item, RawFrame):
            self.raw[item.stream].append(item)
            candidate = self.tracker.observe(item)
            if candidate is not None:
                other = next((frame for frame in self.raw[candidate['other_stream']]
                              if frame.sequence == int(candidate['other_sequence'])
                              and frame.device_us == int(candidate['other_device_timestamp_us'])), None)
                if other is not None:
                    rgb, depth = (item, other) if item.stream == 'rgb_video' else (other, item)
                    self.latest_capture = self._sample(rgb, depth)
                    pairs.append(self.latest_capture)
        for match in self.association.observe(item, context_key=self.binding.sha256):
            depth = min(self.raw['depth_right'], key=lambda frame: abs(frame.sdk_us - match.rgb.sdk_us), default=None)
            if depth is not None and abs(depth.sdk_us - match.rgb.sdk_us) <= self.tolerance:
                self.latest_nn = self._sample(match.rgb, depth, match.input, match.packet)
        return pairs

    def latest(self, now_us):
        self.association.expire(now_us)
        if self.latest_nn is not None and self.latest_nn.fresh(now_us):
            return self.latest_nn
        return self.latest_capture if self.latest_capture is not None and self.latest_capture.fresh(now_us) else None
