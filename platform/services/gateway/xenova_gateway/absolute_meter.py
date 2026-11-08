"""Encoder ingress가 소유하는 절대 양바퀴 이동 기준. actor pose 차분은 사용하지 않는다.

각 유효 표본 사이 max(|ΔL|, |ΔR|)를 wrap-aware tick 정수로 누적한다. gap 뒤
새 연속 구간은 다른 epoch이며 이전 임무/episode의 잃어버린 이동을 0으로 복원하지 않는다.
"""
from collections import deque
from dataclasses import dataclass
from uuid import uuid4
import math


def upper_distance(ticks,scale):
    """정수 차분만 환산하고 double 곱의 하향 반올림도 보수화한다."""
    if type(ticks) is not int or not 0<=ticks<=2**53 or not math.isfinite(scale) or scale<=0:return math.inf
    if ticks==0:return 0.
    return math.nextafter(ticks*scale,math.inf)


@dataclass(frozen=True,slots=True)
class MeterAnchor:
    meter_epoch: str
    gap_revision: int
    valid: bool
    total_ticks: int
    metres_per_tick: float
    host_boot: str
    pi_session: str
    device_session: str
    odom_epoch: str
    calibration: str
    vehicle_sequence: int
    source_sample: int
    source_us: int
    pi_sample_us: int
    clock_error_us: int
    left_ticks: int
    right_ticks: int

    @property
    def total_m(self):return self.total_ticks*self.metres_per_tick


class AbsoluteWheelMeter:
    """한 serial owner만 변경한다. actor에는 불변 current/history snapshot을 공개한다."""
    def __init__(self,config,host_boot):
        self.config=config;self.host_boot=host_boot;self.epoch=str(uuid4())
        self.gap_revision=0;self.previous=None;self.scope=None;self.total_ticks=0
        self.history=deque(maxlen=128);self.snapshot=None

    def invalidate(self):
        if self.previous is not None or self.snapshot is not None:
            self.gap_revision+=1
        self.epoch=str(uuid4());self.previous=None;self.scope=None;self.total_ticks=0
        self.history.clear();self.snapshot=None

    def record(self,*,pi_session,device_session,odom_epoch,sequence,source_sample,source_us,
               left,right,pi_sample_us,clock_error_us):
        scope=(pi_session,device_session,odom_epoch,self.config.calibration,self.config.encoder_bits,
            self.config.metres_per_tick,getattr(self.config,'metres_per_tick_upper',None))
        previous=self.previous
        continuous=previous is not None and scope==self.scope
        if continuous:
            sample,stamp,old_left,old_right,pi_stamp=previous
            continuous=(source_sample==sample+1 and source_us>stamp and pi_sample_us>pi_stamp
                and max(source_us-stamp,pi_sample_us-pi_stamp)<=self.config.maximum_sample_age_us)
        if previous is not None and not continuous:self.invalidate()
        if continuous:
            modulus=1<<self.config.encoder_bits
            delta=lambda current,old:((current-old+modulus//2)%modulus)-modulus//2
            increment=max(abs(delta(left,old_left)),abs(delta(right,old_right)))
            if self.total_ticks+increment>=2**63:
                self.invalidate();continuous=False
            else:self.total_ticks+=increment
        self.scope=scope;self.previous=(source_sample,source_us,left,right,pi_sample_us)
        upper=getattr(self.config,'metres_per_tick_upper',None)
        anchor=MeterAnchor(self.epoch,self.gap_revision,continuous and upper is not None,self.total_ticks,
            self.config.metres_per_tick if upper is None else upper,self.host_boot,pi_session,device_session,odom_epoch,
            self.config.calibration,sequence,source_sample,source_us,pi_sample_us,clock_error_us,left,right)
        self.history.append(anchor)
        self.snapshot=(anchor,tuple(self.history))
        return anchor
