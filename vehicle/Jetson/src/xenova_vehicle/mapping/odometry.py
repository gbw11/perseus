"""같은 epoch·보정·clock의 원 odom을 보간한다. 가짜 정지 pose나 외삽은 만들지 않는다.

운동 상한과 양 끝의 오차로 capture 구간 전체를 덮는다. oracle은 합성 평가에서만
명시적으로 넣을 수 있으며 반환값의 origin에 남는다. 실차 오차 수락은 별도다.
"""

from collections import deque
from dataclasses import dataclass
import math

from ..perception.inputs import FrameStamp
from .registration import wrap_yaw, PoseBox


@dataclass(frozen=True)
class PoseSample:
    epoch: str
    clock_id: str
    calibration_id: str
    at_us: int
    x: float
    y: float
    yaw: float
    position_error_m: float
    yaw_error_rad: float
    origin: str = 'ODOMETRY'

    def __post_init__(self):
        if not self.epoch or not self.clock_id or not self.calibration_id:
            raise ValueError('odom requires original context identities')
        if type(self.at_us) is not int or not 0 <= self.at_us < 2**64:
            raise ValueError('invalid odom time')
        for name in ('x', 'y', 'yaw', 'position_error_m', 'yaw_error_rad'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10000:
                raise ValueError('invalid odom scalar')
        if self.position_error_m < 0 or self.yaw_error_rad < 0:
            raise ValueError('negative odom bound')
        if self.origin not in ('ODOMETRY', 'SYNTHETIC_ODOMETRY', 'ORACLE_SIMULATION'):
            raise ValueError('explicit odom provenance required')

    @property
    def context(self):
        return self.epoch, self.clock_id, self.calibration_id, self.origin


@dataclass(frozen=True)
class PoseEstimate:
    sample: PoseSample
    captured_us: tuple[int, int]
    bracket_us: tuple[int, int]

    @property
    def prior(self):
        p = self.sample
        return PoseBox((p.x-p.position_error_m, p.x+p.position_error_m),
                       (p.y-p.position_error_m, p.y+p.position_error_m),
                       (p.yaw-p.yaw_error_rad, p.yaw+p.yaw_error_rad))


class PoseBuffer:
    """최대256 원 sample. 다른 context는 버퍼를 교체하고 구 epoch를 연결하지 않는다."""

    def __init__(self, *, max_gap_us=100_000, max_speed_mps=.5, max_yaw_rate_rps=2., capacity=256):
        if type(max_gap_us) is not int or not 1 <= max_gap_us <= 1_000_000:
            raise ValueError('invalid odom gap bound')
        if type(capacity) is not int or not 2 <= capacity <= 256:
            raise ValueError('invalid odom capacity')
        for value in (max_speed_mps, max_yaw_rate_rps):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('positive finite motion bounds required')
        self.max_gap_us, self.speed, self.yaw_rate = max_gap_us, max_speed_mps, max_yaw_rate_rps
        self._samples = deque(maxlen=capacity)

    def add(self, sample):
        if type(sample) is not PoseSample:
            raise ValueError('expected PoseSample')
        if self._samples and self._samples[-1].context != sample.context:
            self._samples.clear()
        if self._samples and sample.at_us <= self._samples[-1].at_us:
            if sample == self._samples[-1]:
                return False
            raise ValueError('odom source time did not advance')
        if self._samples:
            old = self._samples[-1]
            dt = (sample.at_us - old.at_us) / 1e6
            if (math.hypot(sample.x-old.x, sample.y-old.y) > self.speed*dt + old.position_error_m + sample.position_error_m + 1e-9
                    or abs(wrap_yaw(sample.yaw-old.yaw)) > self.yaw_rate*dt + old.yaw_error_rad + sample.yaw_error_rad + 1e-9):
                raise ValueError('odom samples contradict the declared motion bounds')
        self._samples.append(sample)
        return True

    def at(self, stamp, calibration_id, *, allow_oracle=False):
        """양 끝이 실제 원 sample로 둘러싸인 capture 구간만 변환한다. 불명은 None이다."""
        if type(stamp) is not FrameStamp:
            raise ValueError('pose query requires an original FrameStamp')
        if stamp.captured_us is None or not self._samples:
            return None
        lo, hi = stamp.captured_us
        matching = [p for p in self._samples if p.clock_id == stamp.clock_id and p.calibration_id == calibration_id]
        if not matching or (matching[0].origin == 'ORACLE_SIMULATION' and not allow_oracle):
            return None
        left = next((p for p in reversed(matching) if p.at_us <= lo), None)
        right = next((p for p in matching if p.at_us >= hi), None)
        if left is None or right is None or left.context != right.context or right.at_us-left.at_us > self.max_gap_us:
            return None
        gap = right.at_us-left.at_us
        middle = (lo+hi)//2
        weight = 0. if gap == 0 else (middle-left.at_us)/gap
        x, y = left.x+(right.x-left.x)*weight, left.y+(right.y-left.y)*weight
        yaw = wrap_yaw(left.yaw+wrap_yaw(right.yaw-left.yaw)*weight)
        # 두 endpoint 각각의 reachable ball은 구간 전체를 덮는다. 같은 중심에서
        # 더 작은 ball을 택해도 보수적이며 sample 평균으로 오차를 줄이지 않는다.
        position_error = min(left.position_error_m+self.speed*(hi-left.at_us)/1e6+math.hypot(x-left.x,y-left.y),
                             right.position_error_m+self.speed*(right.at_us-lo)/1e6+math.hypot(x-right.x,y-right.y))
        yaw_error = min(left.yaw_error_rad+self.yaw_rate*(hi-left.at_us)/1e6+abs(wrap_yaw(yaw-left.yaw)),
                        right.yaw_error_rad+self.yaw_rate*(right.at_us-lo)/1e6+abs(wrap_yaw(yaw-right.yaw)))
        if yaw_error > math.pi:
            return None
        sample = PoseSample(left.epoch, left.clock_id, calibration_id, middle, x,y,yaw,
                            position_error,yaw_error,left.origin)
        return PoseEstimate(sample,stamp.captured_us,(left.at_us,right.at_us))
