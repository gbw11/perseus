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

    @classmethod
    def from_match(cls, odom, fitted):
        if type(odom) is not PoseEstimate or type(fitted) is not PoseEstimate or odom.sample.context!=fitted.sample.context:
            raise ValueError('alignment requires the same original odom context')
        raw,goal=odom.sample,fitted.sample
        yaw=wrap_yaw(goal.yaw-raw.yaw)
        rotated=transform_point((raw.x,raw.y),(0.,0.,yaw))
        angle_error=min(math.pi,goal.yaw_error_rad+raw.yaw_error_rad)
        error=goal.position_error_m+raw.position_error_m+2*math.hypot(raw.x,raw.y)*math.sin(angle_error/2)
        return cls((goal.x-rotated[0],goal.y-rotated[1],yaw),error,angle_error,raw.context)

    def apply(self, odom):
        if type(odom) is not PoseEstimate or odom.sample.context!=self.context:
            raise ValueError('alignment cannot cross odom epoch/calibration/clock/origin')
        raw=odom.sample
        x,y=transform_point((raw.x,raw.y),self.pose)
        error=self.position_error_m+raw.position_error_m+2*math.hypot(raw.x,raw.y)*math.sin(self.yaw_error_rad/2)
        angle=self.yaw_error_rad+raw.yaw_error_rad
        if angle>math.pi:
            raise ValueError('alignment no longer bounds a usable heading')
        sample=replace(raw,x=x,y=y,yaw=wrap_yaw(raw.yaw+self.pose[2]),position_error_m=error,yaw_error_rad=angle)
        return PoseEstimate(sample,odom.captured_us,odom.bracket_us)
