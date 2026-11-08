"""개발 plant의 실제 rear-wheel 운동을 유한 encoder/sample로 만든다.

원 plant owner가 운동과 latch를 순서대로 호출한다. GT pose·목표 속도·goal을 받지
않으며 profile/UUID/시계를 발명하지 않는다. feedback3 wire는 호출자가 넘긴 같은
B08 paired codec으로만 만든다. HELLO/install/ACK/APPLIED·lease 승인은 별도 책임이다.
"""

from dataclasses import dataclass, field
import math
import struct
from uuid import UUID


MAX_SEQUENCE = 2**63 - 1


class SimulationFeedbackError(ValueError):
    """원 운동/encoder/sample/paired codec 문맥이 맞지 않는다."""


def _finite(value):
    if type(value) not in (int, float):
        raise SimulationFeedbackError('finite actual plant value required')
    try:
        valid = math.isfinite(value)
    except OverflowError as error:
        raise SimulationFeedbackError('finite actual plant value required') from error
    if not valid:
        raise SimulationFeedbackError('finite actual plant value required')
    return value


def _positive(value):
    if _finite(value) <= 0:
        raise SimulationFeedbackError('explicit positive geometry/uncertainty required')
    return value


def _sequence(value):
    if type(value) is not int or not 1 <= value <= MAX_SEQUENCE:
        raise SimulationFeedbackError('positive original u63 time/sequence required')
    return value


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        raise SimulationFeedbackError('canonical nonzero original UUID required')
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise SimulationFeedbackError('canonical nonzero original UUID required') from error
    if str(parsed) != value or not parsed.int:
        raise SimulationFeedbackError('canonical nonzero original UUID required')
    return value


def _codec(codec):
    # public layout를 복제하지 않는다. primary feedback2로 자동 fallback하지 않는다.
    if (any(not isinstance(getattr(codec, name, None), struct.Struct)
            for name in ('PROBE3', 'CLOCK3', 'SENSOR3'))
            or any(type(getattr(codec, name, None)) is not int for name in ('CLOCK_REPLY', 'SENSOR'))
            or not callable(getattr(codec, 'packet', None))):
        raise SimulationFeedbackError('one coherent paired feedback3 codec must be supplied')
    return codec


@dataclass(frozen=True, slots=True)
class WheelSample:
    """원 device latch 값이다. quality5는 encoder valid+steering estimated다."""

    sample_seq: int
    source_us: int
    left_ticks: int
    right_ticks: int
    steering_mrad: int
    steering_error_mrad: int
    quality: int = field(default=5, init=False)
    physical_qualified: bool = field(default=False, init=False)

    def __post_init__(self):
        _sequence(self.sample_seq)
        _sequence(self.source_us)
        if any(type(v) is not int or not -2**63 <= v < 2**63 for v in (self.left_ticks, self.right_ticks)):
            raise SimulationFeedbackError('signed encoder fields required')
        if (type(self.steering_mrad) is not int or not -1400 <= self.steering_mrad <= 1400
                or type(self.steering_error_mrad) is not int or not 1 <= self.steering_error_mrad <= 65535):
            raise SimulationFeedbackError('estimated steering with positive wire uncertainty required')


class RearWheelFeedback:
    """실제 중앙 이동과 wrap 전 signed yaw 증분에서 좌우 wheel path를 누적한다.

    dl=distance-track*yaw/2, dr=distance+track*yaw/2다. fractional tick 잔여를
    보존하며 signed32/64 counter만 wrap한다. latch 간 half-range 이상 이동은 불명으로
    닫는다. 실제 reset/gap은 invalidate로 알리고 같은 객체를 자동 재초기화하지 않는다.
    """

    def __init__(self, *, metres_per_tick, track_width_m, encoder_bits, steering_error_rad,
                 pi_session, device_session, initial_left_ticks=0, initial_right_ticks=0):
        self._metres_per_tick = _positive(metres_per_tick)
        self._track_width_m = _positive(track_width_m)
        self._steering_error_rad = _positive(steering_error_rad)
        if type(encoder_bits) is not int or encoder_bits not in (32, 64):
            raise SimulationFeedbackError('explicit encoder_bits32 or64 required')
        self._encoder_bits = encoder_bits
        self._pi_session, self._device_session = _uuid(pi_session), _uuid(device_session)
        self._pi, self._device = UUID(pi_session).bytes, UUID(device_session).bytes
        self._half = 1 << (encoder_bits - 1)
        if any(type(v) is not int or not -self._half <= v < self._half
                for v in (initial_left_ticks, initial_right_ticks)):
            raise SimulationFeedbackError('initial encoder fields outside selected signed width')
        self._ticks = (initial_left_ticks, initial_right_ticks)
        self._fraction = (0., 0.)
        self._unlatched = (0, 0)
        self._steering = None
        self._sequence = 0
        self._last_source = None
        self._last_sample = None
        self._last_probe = None
        self._valid = True

    @property
    def metres_per_tick(self):
        return self._metres_per_tick

    @property
    def track_width_m(self):
        return self._track_width_m

    @property
    def encoder_bits(self):
        return self._encoder_bits

    @property
    def steering_error_rad(self):
        return self._steering_error_rad

    @property
    def pi_session(self):
        return self._pi_session

    @property
    def device_session(self):
        return self._device_session

    @property
    def ticks(self):
        return self._ticks

    @property
    def valid(self):
        return self._valid

    @property
    def last_sample(self):
        return self._last_sample

    def invalidate(self):
        self._valid = False
        self._last_sample = self._last_probe = None

    def _current(self):
        if not self._valid:
            raise SimulationFeedbackError('actual wheel continuity is invalid; no automatic reset')

    def advance_motion(self, distance_m, yaw_delta_rad, steering_rad):
        """같은 plant step의 실제 이동·wrap 전 yaw·실제 servo각만 받는다."""
        self._current()
        try:
            distance, yaw, steering = map(_finite, (distance_m, yaw_delta_rad, steering_rad))
            if abs(steering) > 1.4 or distance == 0 and yaw != 0:
                raise SimulationFeedbackError('actual Ackermann motion/steering outside codec domain')
            paths = (distance - self.track_width_m * yaw / 2, distance + self.track_width_m * yaw / 2)
            deltas = tuple(path / self.metres_per_tick for path in paths)
            # float 잔여에 정수 tick 해상도가 있고 wire 차분도 유일한 범위만 사용한다.
            if any(not math.isfinite(d) or abs(d) >= min(self._half, 2**52) for d in deltas):
                raise SimulationFeedbackError('motion exceeds unambiguous representable tick range')
            totals = tuple(d + f for d, f in zip(deltas, self._fraction))
            steps = tuple(math.floor(value) for value in totals)
            unlatched = tuple(a + b for a, b in zip(self._unlatched, steps))
            if any(abs(v) >= self._half for v in unlatched):
                raise SimulationFeedbackError('unlatched encoder change exceeds half range')
            ticks = tuple((v + d + self._half) % (2 * self._half) - self._half
                for v, d in zip(self._ticks, steps))
            fractions = tuple(value - step for value, step in zip(totals, steps))
        except (ValueError, OverflowError) as error:
            self.invalidate()
            raise SimulationFeedbackError('actual wheel motion invalidated continuity') from error
        self._ticks, self._fraction, self._unlatched = ticks, fractions, unlatched
        self._steering = steering

    def latch(self, source_us):
        """원 device clock에서 새 관측을 latch한다. 같은 시각·표본을 도착으로 갱신하지 않는다."""
        self._current()
        _sequence(source_us)
        if self._steering is None or self._last_source is not None and source_us <= self._last_source:
            raise SimulationFeedbackError('new actual steering observation/device time required')
        _sequence(self._sequence + 1)
        steering = round(self._steering * 1000)
        error = math.ceil((self.steering_error_rad + abs(self._steering - steering / 1000)) * 1000)
        sample = WheelSample(self._sequence + 1, source_us, *self._ticks, steering, error)
        self._sequence, self._last_source, self._last_sample = sample.sample_seq, source_us, sample
        self._unlatched = (0, 0)
        return sample

    def _sample(self, sample):
        self._current()
        if type(sample) is not WheelSample or sample is not self._last_sample:
            raise SimulationFeedbackError('original current producer latch required')

    def clock_reply(self, codec, probe_payload, sample, *, received_device_us, sent_device_us):
        """원 PROBE3와 실제 D2/latch/D3만 직렬화한다. clock install/승인을 만들지 않는다."""
        self._current()
        codec = _codec(codec)
        if type(probe_payload) is not bytes:
            raise SimulationFeedbackError('original immutable PROBE3 payload required')
        try:
            pi, device, probe_seq, p1 = codec.PROBE3.unpack(probe_payload)
        except struct.error as error:
            raise SimulationFeedbackError('original PROBE3 payload differs') from error
        if (pi, device) != (self._pi, self._device):
            raise SimulationFeedbackError('original Pi/device probe identity differs')
        _sequence(probe_seq)
        _sequence(p1)
        _sequence(received_device_us)
        _sequence(sent_device_us)
        if type(sample) is not WheelSample:
            raise SimulationFeedbackError('original producer latch required')
        key = (probe_payload, sample, received_device_us, sent_device_us)
        if self._last_probe is not None:
            old_seq, old_p1, old_key, wire = self._last_probe
            if probe_seq == old_seq:
                if key == old_key and sample is old_key[1]:
                    return wire
                raise SimulationFeedbackError('original probe reply cannot be retimed')
            if probe_seq <= old_seq or p1 <= old_p1 or sample.sample_seq <= old_key[1].sample_seq:
                raise SimulationFeedbackError('new probe requires a new actual latch')
        self._sample(sample)
        if not received_device_us <= sample.source_us <= sent_device_us:
            raise SimulationFeedbackError('original D2/latch/D3 order differs')
        payload = codec.CLOCK3.pack(self._pi, self._device, probe_seq, p1, received_device_us,
            sample.source_us, sent_device_us, sample.sample_seq, sample.left_ticks, sample.right_ticks,
            sample.quality, sample.steering_mrad, sample.steering_error_mrad)
        wire = codec.packet(codec.CLOCK_REPLY, payload)
        if type(wire) is not bytes:
            raise SimulationFeedbackError('paired packet codec must return immutable bytes')
        self._last_probe = (probe_seq, p1, key, wire)
        return wire

    def sensor_wire(self, codec, sample, *, clock_epoch, clock_sequence):
        """caller의 실제 install epoch/seq를 사용한다. 그 lease의 현재성은 caller 책임이다."""
        self._sample(sample)
        codec = _codec(codec)
        _uuid(clock_epoch)
        _sequence(clock_sequence)
        payload = codec.SENSOR3.pack(self._pi, self._device, sample.sample_seq, sample.source_us,
            sample.left_ticks, sample.right_ticks, sample.quality, sample.steering_mrad,
            sample.steering_error_mrad, UUID(clock_epoch).bytes, clock_sequence)
        wire = codec.packet(codec.SENSOR, payload)
        if type(wire) is not bytes:
            raise SimulationFeedbackError('paired packet codec must return immutable bytes')
        return wire
