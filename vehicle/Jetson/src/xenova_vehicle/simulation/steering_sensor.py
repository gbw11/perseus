"""실제 plant 현재 servo각을 읽는 별도 개발 가상 센서와 feedback3 결합이다.

명령 목표·GT pose·goal을 받지 않는다. measured bit는 명시 가상 센서의 원 관측에만
쓰며 실물 measured steering을 주장하지 않는다. 기존 estimated WheelSample은 그대로
보존하고 새 Measurement와 같은 sample/time일 때만 별도 quality3 값을 직렬화한다.
"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import struct
from uuid import UUID

from ..perception.calibration import canonical
from .feedback import MAX_SEQUENCE, WheelSample, _codec


def _finite(value):
    if type(value) not in (int,float):raise ValueError('finite actual steering value required')
    try:valid=math.isfinite(value)
    except OverflowError as error:raise ValueError('finite actual steering value required') from error
    if not valid:raise ValueError('finite actual steering value required')
    return value


def _uint(value,*,maximum=MAX_SEQUENCE,positive=True):
    if type(value) is not int or not int(positive)<=value<=maximum:
        raise ValueError('original bounded sample/time integer required')
    return value


def _uuid(value):
    if type(value) is not str or str(UUID(value))!=value or not UUID(value).int:
        raise ValueError('original canonical nonzero steering source UUID required')
    return value


@dataclass(frozen=True,slots=True)
class VirtualSteeringSensorProfile:
    """명시 가상 센서의 local leaf다. 모든 오차/시간/변화율은 caller가 선언한다."""

    sensor_id: str
    quantization_rad: float
    angle_error_rad: float
    timestamp_error_us: int
    max_rate_rps: float

    def __post_init__(self):
        if (type(self.sensor_id) is not str or not 1<=len(self.sensor_id)<=64
                or any(not 33<=ord(c)<=126 for c in self.sensor_id)):
            raise ValueError('explicit bounded virtual steering sensor identity required')
        if (_finite(self.quantization_rad)<=0 or _finite(self.angle_error_rad)<0
                or _finite(self.max_rate_rps)<=0):
            raise ValueError('explicit quantization/error/rate model required')
        _uint(self.timestamp_error_us,maximum=1_000_000,positive=False)

    @property
    def fields(self):
        return dict(profile='simulated-steering-sensor-1',sensor_id=self.sensor_id,
            source_kind='SIMULATED_MEASURED_STEERING',quantization_rad=self.quantization_rad,
            angle_error_rad=self.angle_error_rad,timestamp_error_us=self.timestamp_error_us,
            max_rate_rps=self.max_rate_rps,quantization_rule='ROUND_TIES_TO_EVEN',
            wire_rule='NEAREST_MRAD_ERROR_OUTWARD_CEIL',steering_quality_bit=2,physical_qualified=False)

    @property
    def canonical_bytes(self):return canonical(self.fields)

    @property
    def sha256(self):return sha256(self.canonical_bytes).hexdigest()

    @property
    def source_kind(self):return 'SIMULATED_MEASURED_STEERING'

    @property
    def physical_qualified(self):return False


@dataclass(frozen=True,slots=True,init=False)
class SteeringMeasurement:
    """원 actual servo와 그 양자화/오차를 보존한다. source 시각을 다시 읽지 않는다."""

    profile: VirtualSteeringSensorProfile
    source_sample: int
    source_us: int
    actual_steering_rad: float
    steering_rad: float
    steering_mrad: int
    steering_error_mrad: int
    _provenance: bytes = field(repr=False)

    @property
    def source_kind(self):return 'SIMULATED_MEASURED_STEERING'

    @property
    def physical_qualified(self):return False

    @property
    def provenance_bytes(self):return self._provenance

    @property
    def fields(self):return json.loads(self._provenance)


@dataclass(frozen=True,slots=True,init=False)
class MeasuredFeedbackSample:
    """원 wheel과 독립 센서 측정을 같은 latch에 결합한 값이다. 원 wheel tag는 바꾸지 않는다."""

    wheel_sample: WheelSample
    measurement: SteeringMeasurement
    _owner: object = field(repr=False,compare=False)
    _provenance: bytes = field(repr=False)

    @property
    def sample_seq(self):return self.wheel_sample.sample_seq

    @property
    def source_sample(self):return self.sample_seq

    @property
    def source_us(self):return self.wheel_sample.source_us

    @property
    def left_ticks(self):return self.wheel_sample.left_ticks

    @property
    def right_ticks(self):return self.wheel_sample.right_ticks

    @property
    def steering_mrad(self):return self.measurement.steering_mrad

    @property
    def steering_error_mrad(self):return self.measurement.steering_error_mrad

    @property
    def quality(self):return 3

    @property
    def source_kind(self):return self.measurement.source_kind

    @property
    def physical_qualified(self):return False

    @property
    def provenance_bytes(self):return self._provenance

    def clock_reply(self,codec,probe_payload,*,received_device_us,sent_device_us):
        return self._owner._clock_reply(self,codec,probe_payload,received_device_us,sent_device_us)

    def sensor_wire(self,codec,*,pi_session,device_session,clock_epoch,clock_sequence):
        return self._owner._sensor_wire(self,codec,pi_session,device_session,clock_epoch,clock_sequence)


class VirtualSteeringSensor:
    """한 producer의 latest measurement/composition/reply 각1개만 가진다. clock 승인은 하지 않는다."""

    def __init__(self,profile,*,pi_session,device_session):
        if type(profile) is not VirtualSteeringSensorProfile:raise ValueError('exact virtual steering profile required')
        self._profile=profile
        self._pi_session,self._device_session=_uuid(pi_session),_uuid(device_session)
        self._pi,self._device=UUID(pi_session).bytes,UUID(device_session).bytes
        self._measurement=self._combined=self._last_probe=None

    @property
    def profile(self):return self._profile

    def sample(self,actual_steering_rad,*,source_sample,source_us):
        """같은 plant lock의 실제 현재각과 원 latch를 받는다. 목표각 인자는 없다."""
        actual=_finite(actual_steering_rad);_uint(source_sample);_uint(source_us)
        if abs(actual)>1.4:raise ValueError('actual servo exceeds feedback3 steering domain')
        old=self._measurement
        if old is not None:
            if (source_sample,source_us)==(old.source_sample,old.source_us):
                if actual==old.actual_steering_rad:return old
                raise ValueError('same original steering latch changed actual angle')
            if source_sample<=old.source_sample or source_us<=old.source_us:
                raise ValueError('original steering source sample/time regressed')
        p=self.profile;quotient=actual/p.quantization_rad
        if not math.isfinite(quotient) or abs(quotient)>=2**52:
            raise ValueError('virtual quantization exceeds representable sample domain')
        quantized=round(quotient)*p.quantization_rad
        mrad=round(quantized*1000)
        if not -1400<=mrad<=1400:raise ValueError('quantized steering exceeds wire domain')
        try:
            guard=4*math.fsum((math.ulp(actual),math.ulp(quantized),math.ulp(p.quantization_rad)))
            quantization=math.nextafter(p.quantization_rad/2+guard,math.inf)
            time_error=math.nextafter(p.max_rate_rps*p.timestamp_error_us/1_000_000,math.inf)
            wire_rounding=math.nextafter(abs(quantized-mrad/1000)+guard,math.inf)
            bound=math.nextafter(math.fsum((p.angle_error_rad,quantization,time_error,wire_rounding)),math.inf)
            scaled=math.nextafter(bound*1000,math.inf)
            if not math.isfinite(scaled):raise ValueError('virtual steering bound is not finite')
            error=max(1,math.ceil(scaled))
        except OverflowError as error:
            raise ValueError('virtual steering bound cannot be represented') from error
        if error>65535:raise ValueError('virtual steering uncertainty exceeds wire domain')
        evidence=dict(profile='simulated-steering-measurement-1',source_kind='SIMULATED_MEASURED_STEERING',
            sensor_profile_sha256=p.sha256,pi_session=self._pi_session,device_session=self._device_session,
            source_sample=str(source_sample),source_us=str(source_us),actual_steering_rad=actual,
            quantized_steering_rad=quantized,steering_mrad=mrad,steering_error_mrad=error,
            error_model=dict(angle_error_rad=p.angle_error_rad,quantization_bound_rad=quantization,
                timestamp_error_us=p.timestamp_error_us,max_rate_rps=p.max_rate_rps,
                time_error_rad=time_error,wire_rounding_error_rad=wire_rounding,roundoff_guard_rad=guard),
            physical_qualified=False)
        result=object.__new__(SteeringMeasurement)
        for name,value in dict(profile=p,source_sample=source_sample,source_us=source_us,
                actual_steering_rad=actual,steering_rad=quantized,steering_mrad=mrad,
                steering_error_mrad=error,_provenance=canonical(evidence)).items():
            object.__setattr__(result,name,value)
        self._measurement=result
        return result

    def combine(self,wheel_sample,measurement):
        """다른 시각/센서의 값으로 measured bit를 만들지 않는다."""
        if (type(wheel_sample) is not WheelSample or type(measurement) is not SteeringMeasurement
                or measurement is not self._measurement
                or (wheel_sample.sample_seq,wheel_sample.source_us)!=(measurement.source_sample,measurement.source_us)):
            raise ValueError('same original wheel sample and actual steering measurement required')
        if self._combined is not None and self._combined.measurement is measurement:
            if self._combined.wheel_sample is wheel_sample:return self._combined
            raise ValueError('original measurement is already bound to another wheel object')
        result=object.__new__(MeasuredFeedbackSample)
        provenance=canonical(dict(profile='simulated-measured-feedback-1',quality=3,
            wheel=dict(source_sample=str(wheel_sample.sample_seq),source_us=str(wheel_sample.source_us),
                left_ticks=wheel_sample.left_ticks,right_ticks=wheel_sample.right_ticks,
                original_quality=wheel_sample.quality,estimated_steering_mrad=wheel_sample.steering_mrad,
                estimated_steering_error_mrad=wheel_sample.steering_error_mrad),
            measurement=measurement.fields,physical_qualified=False))
        for name,value in dict(wheel_sample=wheel_sample,measurement=measurement,_owner=self,_provenance=provenance).items():
            object.__setattr__(result,name,value)
        self._combined=result
        return result

    def _current(self,value):
        if (type(value) is not MeasuredFeedbackSample or value is not self._combined
                or value.measurement is not self._measurement):
            raise ValueError('original current measured composition required')

    def _clock_reply(self,value,codec,probe_payload,d2,d3):
        codec=_codec(codec)
        if type(probe_payload) is not bytes:raise ValueError('original immutable PROBE3 required')
        try:pi,device,seq,p1=codec.PROBE3.unpack(probe_payload)
        except struct.error as error:raise ValueError('original PROBE3 shape differs') from error
        if (pi,device)!=(self._pi,self._device):raise ValueError('original steering probe source differs')
        for number in (seq,p1,d2,d3):_uint(number)
        key=(probe_payload,value,d2,d3)
        if self._last_probe is not None:
            old_seq,old_p1,old_key,wire=self._last_probe
            if seq==old_seq:
                if key==old_key and value is old_key[1]:return wire
                raise ValueError('original measured clock reply cannot be retimed')
            if seq<=old_seq or p1<=old_p1 or value.sample_seq<=old_key[1].sample_seq:
                raise ValueError('new original probe requires a new measured latch')
        self._current(value)
        if not d2<=value.source_us<=d3:raise ValueError('original D2/measurement/D3 order differs')
        payload=codec.CLOCK3.pack(self._pi,self._device,seq,p1,d2,value.source_us,d3,value.sample_seq,
            value.left_ticks,value.right_ticks,value.quality,value.steering_mrad,value.steering_error_mrad)
        wire=codec.packet(codec.CLOCK_REPLY,payload)
        if type(wire) is not bytes:raise ValueError('paired codec must return immutable bytes')
        self._last_probe=(seq,p1,key,wire)
        return wire

    def _sensor_wire(self,value,codec,pi_session,device_session,clock_epoch,clock_sequence):
        self._current(value);codec=_codec(codec)
        if (_uuid(pi_session),_uuid(device_session))!=(self._pi_session,self._device_session):
            raise ValueError('original measured sensor source differs')
        _uuid(clock_epoch);_uint(clock_sequence)
        payload=codec.SENSOR3.pack(self._pi,self._device,value.sample_seq,value.source_us,
            value.left_ticks,value.right_ticks,value.quality,value.steering_mrad,value.steering_error_mrad,
            UUID(clock_epoch).bytes,clock_sequence)
        wire=codec.packet(codec.SENSOR,payload)
        if type(wire) is not bytes:raise ValueError('paired codec must return immutable bytes')
        return wire
