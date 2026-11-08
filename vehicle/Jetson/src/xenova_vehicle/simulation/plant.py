"""실제 wall dt로 진행하는 개발용 rear-axle bicycle plant다.

원 simulator0b56의 모터 지연·가감속·서보 속도·midpoint 적분을 사용한다.
장애물 GT로 가짜 근거리 센서를 만들지 않는다. pose는 renderer/evaluation 전용이며
odom은 실제 이동에서 생성한 RearWheelFeedback latch만 사용한다. 실측 plant가 아니다.
"""

from dataclasses import dataclass
import math
import threading
import time
from uuid import UUID

from .feedback import RearWheelFeedback, SimulationFeedbackError
from .motion_profile import DevelopmentMotionProfile


def j_us():
    return time.monotonic_ns() // 1000


def _time(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError('original plant clock must be positive u63 microseconds')
    return value


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite builtin plant value required')
    return value


def _clamp(value, bound):
    return max(-bound, min(bound, value))


@dataclass(frozen=True, slots=True)
class PlantSnapshot:
    """단일 lock의 원 상태. pose/목표값은 renderer/evaluation 외 센서 입력이 아니다."""

    epoch: str
    observed_us: int
    pose: tuple
    velocity_mps: float
    steering_rad: float
    target_velocity_mps: float
    target_steering_rad: float
    valid: bool
    invalid_reason: str | None
    physical_qualified: bool = False


class VirtualPlant:
    """단일 물리 owner와 잠긴 renderer reader를 위한 명시 개발 plant다.

    첫 mutation thread에 결박한다. stop()의 target-zero는 다른 thread에서도 안전하게
    요청할 수 있으나 속도0/새 센서 표본을 뜻하지 않는다. clock gap/역전 뒤 자동 reset은 없다.
    """

    def __init__(self, motion_profile, *, initial_pose, epoch, clock=j_us):
        if type(motion_profile) is not DevelopmentMotionProfile:
            raise ValueError('explicit DevelopmentMotionProfile required')
        if type(initial_pose) is not tuple or len(initial_pose) != 3:
            raise ValueError('renderer-only original rear-axle pose required')
        self._pose = tuple(_finite(v) for v in initial_pose)
        if type(epoch) is not str or str(UUID(epoch)) != epoch or not UUID(epoch).int:
            raise ValueError('original plant epoch required')
        if not callable(clock):
            raise ValueError('actual monotonic clock callable required')
        self.profile, self.epoch, self.clock = motion_profile, epoch, clock
        self._vehicle = motion_profile.development_profile.fields['vehicle']
        self._encoder = motion_profile.fields['encoder']
        self._max_gap_us = motion_profile.fields['sensor']['max_gap_us']
        self._maximum_speed = motion_profile.fields['odometry']['maximum_speed_mps']
        self._last_us = _time(clock())
        self._wheel_rad_s = self._steering = self._target_velocity = self._target_steering = 0.
        self._owner = None
        self._lock = threading.Lock()
        self._valid = True
        self._reason = None
        self._feedback = None

    def _owned(self):
        current = threading.get_ident()
        if self._owner is None:
            self._owner = current
        if current != self._owner:
            raise RuntimeError('plant mutations belong to the original physics owner')

    def _current(self):
        if not self._valid:
            raise ValueError('plant continuity invalid; explicit new owner/session required')

    def _invalidate(self, reason):
        self._valid = False
        self._reason = self._reason or reason
        self._target_velocity = self._target_steering = 0.
        if self._feedback is not None:
            self._feedback.invalidate()

    def invalidate(self, reason):
        """실제 연결/시간 연속성 상실을 닫는다. pose/encoder HWM은 되돌리지 않는다."""
        with self._lock:
            self._invalidate(str(reason)[:96])

    def bind_sessions(self, pi_session, device_session):
        """유효 HELLO의 최초 session만 결박한다. 새 Pi로 encoder를 재발급하지 않는다."""
        with self._lock:
            self._owned(); self._current()
            if self._feedback is not None:
                if (pi_session, device_session) != (self._feedback.pi_session, self._feedback.device_session):
                    self._invalidate('SESSION_REPLACEMENT_REQUIRES_NEW_OWNER')
                    raise ValueError('plant cannot automatically rebind Pi/device session')
                return self._feedback
            self._feedback = RearWheelFeedback(metres_per_tick=self._encoder['metres_per_tick'],
                track_width_m=self._encoder['track_width_m'], encoder_bits=self._encoder['encoder_bits'],
                steering_error_rad=self._encoder['steering_error_rad'], pi_session=pi_session,
                device_session=device_session)
            self._feedback.advance_motion(0., 0., self._steering)
            return self._feedback

    @property
    def feedback(self):
        with self._lock:
            return self._feedback

    def apply_target(self, velocity_mps, steering_rad):
        """실제 actuator setpoint 수신 사건이다. 측정 velocity/steering은 여기서 바뀌지 않는다."""
        with self._lock:
            self._owned(); self._current()
            velocity, steering = _finite(velocity_mps), _finite(steering_rad)
            native_max = self._vehicle['max_wheel_rpm'] * 2*math.pi/60 * self._vehicle['wheel_radius_m']
            maximum = min(native_max, self._maximum_speed)
            if abs(velocity) > maximum or abs(steering) > 1.4:
                raise ValueError('setpoint exceeds original development geometry/speed bound')
            # 원 actuator 포화다. wire mrad 반올림을 새 물리 maxsteer로 채택하지 않는다.
            self._target_velocity = velocity
            self._target_steering = _clamp(steering, self._vehicle['max_steer_rad'])

    def stop(self):
        """목표 추진·서보를0으로 한다. 실제 제동과 stationary 관측은 다음 원 step/latch다."""
        with self._lock:
            self._target_velocity = self._target_steering = 0.

    def _advance(self, now, *, equal_ok=False):
        self._owned(); self._current()
        try:now = _time(now)
        except ValueError:
            self._invalidate('CLOCK_INVALID')
            raise
        elapsed = now-self._last_us
        if elapsed == 0 and equal_ok:
            return
        if not 0 < elapsed <= self._max_gap_us:
            self._invalidate('CLOCK_REGRESSION_OR_GAP')
            raise ValueError('invalid plant dt; no catchup or clock reset')
        dt = elapsed/1_000_000
        body = self._vehicle; radius = body['wheel_radius_m']
        target = self._target_velocity/radius
        wheel = self._wheel_rad_s
        if target == 0.:
            wheel -= _clamp(wheel, body['max_decel_mps2']/radius*dt)
        else:
            delta = (target-wheel)*min(1., dt/body['motor_tau_s'])
            accelerating = abs(target) > abs(wheel) and target*wheel >= 0
            limit = (body['max_accel_mps2'] if accelerating else body['max_decel_mps2'])/radius*dt
            wheel += _clamp(delta, limit)
        steering = self._steering + _clamp(self._target_steering-self._steering, body['max_steer_rate_rps']*dt)
        distance = wheel*radius*dt
        yaw_delta = distance/body['wheelbase_m']*math.tan(steering)
        x, y, yaw = self._pose
        middle = yaw+yaw_delta/2
        pose = (x+distance*math.cos(middle), y+distance*math.sin(middle),
                math.atan2(math.sin(yaw+yaw_delta), math.cos(yaw+yaw_delta)))
        if self._feedback is not None:
            try:self._feedback.advance_motion(distance, yaw_delta, steering)
            except SimulationFeedbackError:
                self._invalidate('WHEEL_CONTINUITY_INVALID')
                raise
        self._wheel_rad_s, self._steering, self._pose, self._last_us = wheel, steering, pose, now

    def step(self, now_us=None):
        with self._lock:
            self._advance(self.clock() if now_us is None else now_us)
            return self._snapshot()

    def latch(self, now_us=None):
        """같은 device clock의 실제 wheel/servo를 latch한다. 같은 시각은 같은 원 표본을 반환한다."""
        with self._lock:
            self._advance(self.clock() if now_us is None else now_us, equal_ok=True)
            if self._feedback is None:
                raise ValueError('valid HELLO sessions required before sensor latch')
            old = self._feedback.last_sample
            sample = old if old is not None and old.source_us == self._last_us else self._feedback.latch(self._last_us)
            return sample, self._snapshot()

    def _snapshot(self):
        return PlantSnapshot(self.epoch, self._last_us, self._pose,
            self._wheel_rad_s*self._vehicle['wheel_radius_m'], self._steering,
            self._target_velocity, self._target_steering, self._valid, self._reason)

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def renderer_pose(self):
        """renderer가 자기 원 before/after clock 구간으로 감쌀 단일 상태 읽기다."""
        with self._lock:
            self._current()
            return self._pose
