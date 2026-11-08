"""보존된 실제 원본을 공통 입력으로 옮긴다. 재생을 LIVE로, bridge를 노출로 위장하지 않는다.

P0 descriptor의 capture interval(null-only)은 변경하지 않는다. 수락한 시간 오차와
원 노출이 모두 있을 때만 별도의 파생 capture 구간을 만든다. depth bridge가 없는
이전 S03 표본은 depth의 시간 미수락 상태를 그대로 유지한다.
"""

from dataclasses import dataclass

from .inputs import FrameStamp, ImageFrame, PerceptionInput


@dataclass(frozen=True)
class CaptureTiming:
    """명시적으로 수락한 오차(us). 하나라도 미정이면 metric 시간 구간을 만들지 않는다."""

    clock_error_us: int | None = None
    timestamp_error_us: int | None = None
    readout_error_us: int | None = None
    max_depth_exposure_us: int | None = None

    def __post_init__(self):
        for value in (self.clock_error_us,self.timestamp_error_us,self.readout_error_us,self.max_depth_exposure_us):
            if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000):
                raise ValueError('time error must be an explicit nonnegative microsecond bound')


def _image(frame, *, context, calibration, clock=None, timing=None):
    frame.validate()
    captured = None
    if timing is not None and type(timing) is not CaptureTiming:
        raise ValueError('expected CaptureTiming')
    if timing is not None and clock is not None:
        from .geometry import capture_interval
        errors = timing.clock_error_us,timing.timestamp_error_us,timing.readout_error_us
        exposure = frame.exposure_us
        if exposure is None and frame.stream == 'depth_right':
            exposure = timing.max_depth_exposure_us
        end = clock.get('sdk_host_timestamp_j_bracket_us')
        if end is not None and exposure is not None and all(v is not None for v in errors):
            captured = capture_interval(tuple(map(int,end)),exposure,clock_error_us=errors[0],
                timestamp_error_us=errors[1],readout_error_us=errors[2])
    stamp = FrameStamp(context['acquisition_worker'],context['stream_instances'][frame.stream],
        frame.sequence,context['clock_epoch'],captured,frame.received_j_us)
    return ImageFrame(stamp,frame.width,frame.height,frame.encoding,frame.payload,frame.exposure_us)


def from_capture_sample(sample, context, calibration, *, kind='LIVE', timing=None, nn_candidates=None, include_nn=True):
    """현재 P0의 owned CaptureSample. 기존 binding/원 association은 호출자가 먼저 검사한다."""
    from ..runtime.resident_samples import CaptureSample
    if type(sample) is not CaptureSample or kind not in ('LIVE','REPLAY'):
        raise ValueError('expected a physical owned CaptureSample and explicit adapter kind')
    if calibration.kind == 'SYNTHETIC':
        raise ValueError('a synthetic calibration cannot qualify a physical camera')
    rgb = _image(sample.rgb,context=context,calibration=calibration,clock=sample.clocks.get('rgb_video'),timing=timing)
    depth = _image(sample.depth,context=context,calibration=calibration,clock=sample.clocks.get('depth_right'),timing=timing)
    nn_key = None
    if sample.nn_output is not None and include_nn:
        original,packet = sample.nn_input,sample.nn_output
        if original is None or not (original.sequence==packet.sequence==sample.rgb.sequence
                and original.sdk_us==packet.sdk_us==sample.rgb.sdk_us
                and original.device_us==packet.device_us==sample.rgb.device_us):
            raise ValueError('NN has no exact original RGB parent')
        if nn_candidates is None:
            from .yolop import decode_yolop
            nn_candidates = decode_yolop(packet)
        if nn_candidates.packet is not packet:
            raise ValueError('provided NN candidates differ from the original packet')
        nn_key = (context['acquisition_worker'],context['stream_instances'].get('nn_output0',context['stream_instances'].get('nn_output')),
                  context['clock_epoch'],packet.sequence,packet.device_us)
    elif nn_candidates is not None:
        raise ValueError('NN candidates supplied for a capture without NN')
    return PerceptionInput(kind,calibration.sha256,rgb,depth,nn_candidates,nn_key)


def from_diagnostic_sample(sample, calibration, *, timing=None):
    """검사된 S03 표본의 기존 missing-depth-bridge 한계를 그대로 보존한다."""
    from ..recording.yolop_contract import YolopDiagnosticSample
    from ..runtime.resident_samples import CaptureSample
    if type(sample) is not YolopDiagnosticSample:
        raise ValueError('expected an already validated YOLOP diagnostic sample')
    clocks = {'rgb_video':sample.metadata['capture_clock']}
    owned = CaptureSample(sample.rgb,sample.depth,sample.nn_input,sample.nn_output,clocks)
    return from_capture_sample(owned,sample.metadata['context'],calibration,kind='REPLAY',timing=timing)
