"""수락한 map←odom 변환과 보수 오차를 다음 관측에 전파한다.

현재 관측을 검증하는 이전 frozen map은 움직이지 않는다. 정합이 허용한 실제
representative/bound로만 변환을 교체하고, 상관된 오차를 표본 수로 나누지 않는다.
"""

from dataclasses import dataclass, replace
import math

from .odometry import PoseEstimate
from .registration import transform_point, wrap_yaw


@dataclass(frozen=True)
class MapOdomAlignment:
    pose: tuple[float, float, float]
    position_error_m: float
    yaw_error_rad: float
    context: tuple
    observed_j_interval: tuple | None = None
    registration_revision: int | None = None
    valid_for_us: int | None = None
    position_drift_mps: float | None = None
    yaw_drift_rps: float | None = None

    def __post_init__(self):
        if (type(self.pose) is not tuple or len(self.pose)!=3 or type(self.context) is not tuple or len(self.context)!=4
                or any(type(v) is not str or not 1<=len(v)<=128 for v in self.context)
                or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>10000 for v in self.pose)
                or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10000
                       for v in (self.position_error_m,self.yaw_error_rad))):
            raise ValueError('invalid original map alignment/context/bounds')
        metadata=(self.observed_j_interval,self.registration_revision,self.valid_for_us,self.position_drift_mps,self.yaw_drift_rps)
        if all(value is None for value in metadata):return
        if any(value is None for value in metadata):raise ValueError('alignment observation/revision/validity/drift must be supplied together')
        _interval(self.observed_j_interval)
        if (type(self.registration_revision) is not int or not 1<=self.registration_revision<2**63
                or type(self.valid_for_us) is not int or not 1<=self.valid_for_us<=1_000_000
                or self.observed_j_interval[0]+self.valid_for_us>=2**64
                or any(type(value) not in (int,float) or not math.isfinite(value) or value<0
                       for value in (self.position_drift_mps,self.yaw_drift_rps))):
            raise ValueError('invalid explicit alignment lifetime/drift')

    @property
    def has_metadata(self):return self.observed_j_interval is not None

    def fields(self):
        """legacy는 원4필드만 보존한다. null metadata를 새 운영 근거로 직렬화하지 않는다."""
        result=dict(pose=self.pose,position_error_m=self.position_error_m,yaw_error_rad=self.yaw_error_rad,context=self.context)
        if self.has_metadata:
            result.update(observed_j_interval=self.observed_j_interval,registration_revision=self.registration_revision,
                valid_for_us=self.valid_for_us,position_drift_mps=self.position_drift_mps,yaw_drift_rps=self.yaw_drift_rps)
        return result

    @classmethod
    def from_fields(cls,value):
        old={'pose','position_error_m','yaw_error_rad','context'}
        new={'observed_j_interval','registration_revision','valid_for_us','position_drift_mps','yaw_drift_rps'}
        if type(value) is not dict or set(value) not in (old,old|new):raise ValueError('unsupported exact alignment metadata shape')
        fields=dict(value)
        if not isinstance(fields['pose'],(tuple,list)) or not isinstance(fields['context'],(tuple,list)):
            raise ValueError('original alignment vectors required')
        fields['pose']=tuple(fields['pose']);fields['context']=tuple(fields['context'])
        if set(value)==old|new:
            if any(fields[name] is None for name in new):raise ValueError('full alignment metadata cannot contain unknown fields')
            if not isinstance(fields['observed_j_interval'],(tuple,list)):raise ValueError('original observed interval required')
            fields['observed_j_interval']=tuple(fields['observed_j_interval'])
        return cls(**fields)

    def valid_at(self,now_us):
        """현재 publication/정상 clock 갱신은 원 earliest+valid_for 기한을 늘리지 않는다."""
        return (self.has_metadata and type(now_us) is int
            and self.observed_j_interval[1]<=now_us<self.observed_j_interval[0]+self.valid_for_us)

    @classmethod
    def from_match(cls, odom, fitted, *, observed_j_interval=None,registration_revision=None,
                   valid_for_us=None,position_drift_mps=None,yaw_drift_rps=None):
        if type(odom) is not PoseEstimate or type(fitted) is not PoseEstimate or odom.sample.context!=fitted.sample.context:
            raise ValueError('alignment requires the same original odom context')
        raw,goal=odom.sample,fitted.sample
        if observed_j_interval is not None and (observed_j_interval!=odom.captured_us or fitted.captured_us!=odom.captured_us):
            raise ValueError('alignment must preserve the full original accepted capture interval')
        yaw=wrap_yaw(goal.yaw-raw.yaw)
        rotated=transform_point((raw.x,raw.y),(0.,0.,yaw))
        angle_error=min(math.pi,goal.yaw_error_rad+raw.yaw_error_rad)
        error=goal.position_error_m+raw.position_error_m+2*math.hypot(raw.x,raw.y)*math.sin(angle_error/2)
        return cls((goal.x-rotated[0],goal.y-rotated[1],yaw),error,angle_error,raw.context,
            observed_j_interval,registration_revision,valid_for_us,position_drift_mps,yaw_drift_rps)

    def apply(self, odom):
        if type(odom) is not PoseEstimate or odom.sample.context!=self.context:
            raise ValueError('alignment cannot cross odom epoch/calibration/clock/origin')
        raw=odom.sample
        transform_angle=self.yaw_error_rad;position_drift=0.
        if self.has_metadata:
            _interval(odom.captured_us)
            if odom.captured_us[1]<self.observed_j_interval[1]:raise ValueError('alignment cannot apply to an older original capture')
            seconds=math.nextafter((odom.captured_us[1]-self.observed_j_interval[0])/1_000_000,math.inf)
            position_drift=math.nextafter(self.position_drift_mps*seconds,math.inf) if self.position_drift_mps else 0.
            yaw_drift=math.nextafter(self.yaw_drift_rps*seconds,math.inf) if self.yaw_drift_rps else 0.
            transform_angle=math.nextafter(transform_angle+yaw_drift,math.inf)
        x,y=transform_point((raw.x,raw.y),self.pose)
        rotation=2*math.hypot(raw.x,raw.y)*math.sin(min(math.pi,transform_angle)/2)
        error=self.position_error_m+raw.position_error_m+rotation+position_drift
        angle=transform_angle+raw.yaw_error_rad
        if angle>math.pi and not self.has_metadata:
            raise ValueError('alignment no longer bounds a usable heading')
        if self.has_metadata:
            error=math.nextafter(error,math.inf);angle=min(math.pi,math.nextafter(angle,math.inf))
        sample=replace(raw,x=x,y=y,yaw=wrap_yaw(raw.yaw+self.pose[2]),position_error_m=error,yaw_error_rad=angle)
        return PoseEstimate(sample,odom.captured_us,odom.bracket_us)


def _interval(value):
    if (type(value) is not tuple or len(value)!=2 or any(type(v) is not int or not 0<=v<2**64 for v in value)
            or value[0]>value[1]):raise ValueError('original alignment capture interval required')
