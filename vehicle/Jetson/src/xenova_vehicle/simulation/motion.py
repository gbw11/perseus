"""원 Gateway Vehicle을 SYNTHETIC full-capture pose 입력에 연결하는 개발 전용 port다.

Device↔Pi clock install/ACK나 Pi↔J trust를 만들지 않는다. caller가 준 실제 원 Vehicle
bytes/Probe snapshot을 기존 MapperSensorState에 소비시키며 GT pose·목표·속도 적분은 없다.
원 calibration/source/시각을 바꾸는 adapter나 Resident 운영 fallback으로 사용하지 않는다.
"""

from dataclasses import replace
import threading

from ..perception.inputs import PerceptionInput
from ..runtime.mapper_sensor_state import MapperSensorState
from .motion_profile import DevelopmentMotionProfile
from .wall_sensor import MAX_CAPTURE_LIFETIME_US


class DevelopmentMotion:
    """한 main owner의 원 admission/HWM/PoseBuffer다. compute에는 frame snapshot만 넘긴다."""

    def __init__(self,profile,raw_binding,clock_policy,*,sensor_context,mapper_worker,mapper_instance):
        if type(profile) is not DevelopmentMotionProfile:
            raise ValueError('exact explicit development motion profile required')
        self._profile=profile
        self._context=sensor_context
        self._raw=raw_binding
        self._thread=threading.get_ident()
        self.sensor_state=MapperSensorState.for_development(profile,raw_binding,clock_policy,
            sensor_context=sensor_context,mapper_worker=mapper_worker,mapper_instance=mapper_instance)

    @property
    def profile(self):return self._profile

    @property
    def context(self):return self._context

    @property
    def raw(self):return self._raw

    def _owned(self):
        if threading.get_ident()!=self._thread:
            raise RuntimeError('development motion belongs to the original main owner')

    def ingest(self,source_snapshot,*,now_us):
        """원 row를 도착 당시 현재 J로 수락한다. capture·GT·새 clock/trust를 만들지 않는다."""
        self._owned()
        return self.sensor_state.ingest(source_snapshot,now_us=now_us)

    def _capture_stamp(self,observation,*,now_us,expires_j_us):
        self._owned()
        if (type(observation) is not PerceptionInput or observation.kind!='SYNTHETIC'
                or observation.calibration_id!=self.profile.bound_calibration.sha256
                or observation.rgb.stamp.clock_id!=self.context.pose_clock_id
                or observation.rgb.stamp.source_id!='sim-wall:'+self.raw.fields['capture_epoch']):
            raise ValueError('original bound SYNTHETIC wall observation required')
        # 실제 전체 hull만 쓰며 zero-width로 만들어 motion 경로를 건너뛰지 않는다.
        full=observation.capture_interval
        if full is not None and full[0]==full[1]:
            raise ValueError('development motion requires the original nonzero capture interval')
        if (type(expires_j_us) is not int or type(now_us) is not int
                or not 0<=now_us<expires_j_us<2**63
                or full is not None and not full[1]<expires_j_us<=full[0]+MAX_CAPTURE_LIFETIME_US):
            raise ValueError('original development capture lifetime expired or differs')
        available=max(observation.rgb.stamp.available_us,
            0 if observation.depth is None else observation.depth.stamp.available_us)
        return replace(observation.rgb.stamp,captured_us=full,available_us=available)

    def frame_snapshot(self,observation,*,now_us,expires_j_us,job_id,source_generation,source_key):
        """원 frame job의 query만 고정한다. 늦은 Vehicle 재-admission이나 E 갱신은 없다."""
        stamp=self._capture_stamp(observation,now_us=now_us,expires_j_us=expires_j_us)
        if source_generation!=self.raw.sha256 or source_key!=observation.rgb.stamp.key:
            raise ValueError('original development RAW generation and RGB source key required')
        return self.sensor_state.frame_snapshot(stamp,now_us=now_us,expires_us=expires_j_us,
            job_id=job_id,source_generation=source_generation,source_key=source_key)

    def read(self,source_snapshot,observation,*,now_us,expires_j_us):
        """software fixture용 legacy ingest+query다. 실제 main은 ingest/frame_snapshot을 분리한다."""
        stamp=self._capture_stamp(observation,now_us=now_us,expires_j_us=expires_j_us)
        return self.sensor_state.read(source_snapshot,stamp,now_us=now_us)

    def original_pose_bracket(self,pose):
        return self.sensor_state.original_pose_bracket(pose)

    def original_vehicle_admission(self,evidence):
        return self.sensor_state.original_vehicle_admission(evidence)

    def validate_vehicle_admission(self,record):
        return self.sensor_state.validate_vehicle_admission(record)

    def vehicle_from_gateway(self,odometry,*,odometry_module,common,now_pi_us):
        """coherent B08 owner의 현재 원 row만 같은 codec으로 만든다. mapped interval은 발급하지 않는다.

        actual feedback3 owner가 Device↔Pi lease에서 observe_mapped를 수행한 뒤 호출한다.
        이 함수 자체는 serial 수신/clock 수락 증거가 아니며 caller가 paired modules를 보장한다.
        """
        self._owned()
        expected_type=getattr(odometry_module,'VehicleOdometry',None)
        config_type=getattr(odometry_module,'OdometryConfig',None)
        if (expected_type is None or type(odometry) is not expected_type or config_type is None
                or odometry.config!=config_type(**self.profile.odometry_arguments)
                or odometry.host_boot!=self.context.pi_host_boot or odometry.device!=self.context.device_session
                or type(now_pi_us) is not int or not 0<=now_pi_us<2**63):
            raise ValueError('actual paired Gateway odometry/config/origin required')
        row=odometry.current(now_pi_us)
        if row is None:return None
        if (row.get('pi_session')!=self.context.pi_session
                or row.get('odom_epoch')!=self.context.source['odom_epoch']):
            raise ValueError('Gateway row differs from the original bound Pi/odom source')
        sample=common.schema.VehicleSample(major=1,**dict(row,gateway_instance=self.context.gateway_instance))
        raw=common.encode(sample)
        if type(raw) is not bytes or not 0<len(raw)<=1024:
            raise ValueError('paired VehicleSample codec exceeded the original sample bound')
        return raw
