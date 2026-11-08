"""개발용 wheel/odom/시계 가정을 neutral 모델과 결합한 local manifest다.

실측 bundle이나 출력 권한이 아니다. scene과 독립인 neutral/model SHA를 참조한 뒤
그 manifest SHA만 SYNTHETIC calibration의 기존 whole-bundle 관계에 넣어 순환을 피한다.
추가 오차·나이·운동·시계 값은 모두 호출자가 명시하며 운영 기본값으로 선택하지 않는다.
"""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math

from ..perception.calibration import CalibrationProfile, canonical
from .profile import DevelopmentProfile


def _positive(value):
    if type(value) not in (int, float):
        raise ValueError('explicit positive development motion bound required')
    try:valid=math.isfinite(value) and value>0
    except OverflowError as error:raise ValueError('explicit positive development motion bound required') from error
    if not valid:raise ValueError('explicit positive development motion bound required')
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('explicit bounded development integer required')
    return value


@dataclass(frozen=True, slots=True, init=False)
class DevelopmentMotionProfile:
    """원 가상 모델과 명시 가정의 불변 사본이다. 실제 clock lease를 발급하지 않는다."""

    _bytes: bytes = field(repr=False)
    _development: DevelopmentProfile = field(repr=False)
    _neutral: CalibrationProfile = field(repr=False)
    _bound: CalibrationProfile = field(repr=False)
    _steering_sensor: object = field(repr=False)

    def __init__(self, development_profile, *, encoder_bits, metres_per_tick_upper,
                 maximum_speed_mps, maximum_clock_error_us, maximum_sample_age_us,
                 sample_period_ms, position_error_m, heading_error_rad, distance_error_ratio,
                 heading_error_per_m, vehicle_max_age_us, max_yaw_rate_rps, max_gap_us,
                 pose_capacity, timestamp_error_us, drift_ppm, max_offset_width_us, lease_us, lookback_us,
                 steering_sensor_profile=None):
        if type(development_profile) is not DevelopmentProfile:
            raise ValueError('exact original DevelopmentProfile required')
        source = development_profile.fields
        vehicle = source['vehicle']
        neutral = development_profile.calibration()
        if neutral.kind != 'SYNTHETIC' or neutral.vehicle_bundle_sha256 is not None:
            raise ValueError('original neutral SYNTHETIC calibration required')
        if type(encoder_bits) is not int or encoder_bits not in (32, 64):
            raise ValueError('explicit signed encoder width32 or64 required')
        scale = 2 * math.pi * vehicle['wheel_radius_m'] / vehicle['encoder_cpr']
        if _positive(metres_per_tick_upper) < scale:
            raise ValueError('upper encoder scale is below the original nominal scale')
        for value in (maximum_speed_mps, position_error_m, heading_error_rad,
                      distance_error_ratio, heading_error_per_m, max_yaw_rate_rps):
            _positive(value)
        if maximum_speed_mps > 100 or max_yaw_rate_rps > 100:
            raise ValueError('motion bound exceeds SensorSamples domain')
        for value in (maximum_clock_error_us, maximum_sample_age_us, sample_period_ms):
            _integer(value, 1, 2**31 - 1)
        _integer(vehicle_max_age_us, 1, 1_000_000)
        _integer(max_gap_us, 1, 1_000_000)
        _integer(pose_capacity, 2, 256)
        _integer(timestamp_error_us, 0, 1_000_000)
        _integer(drift_ppm, 0, 999_999)
        _integer(max_offset_width_us, 1, 2**31 - 1)
        _integer(lease_us, 1, 1_000_000)
        _integer(lookback_us, vehicle_max_age_us, 1_000_000)
        value = dict(profile='course-development-motion-1',
            neutral_calibration_sha256=neutral.sha256,
            calibration_model_sha256=development_profile.calibration_model_sha256,
            identity=source['identity'],
            encoder=dict(wheel_radius_m=vehicle['wheel_radius_m'], encoder_cpr=vehicle['encoder_cpr'],
                metres_per_tick=scale, metres_per_tick_upper=metres_per_tick_upper,
                track_width_m=vehicle['track_width_m'], encoder_bits=encoder_bits,
                steering_error_rad=vehicle['estimated_steering_error_rad']),
            odometry=dict(wheelbase_m=vehicle['wheelbase_m'], maximum_speed_mps=maximum_speed_mps,
                maximum_clock_error_us=maximum_clock_error_us, maximum_sample_age_us=maximum_sample_age_us,
                sample_period_ms=sample_period_ms, position_error_m=position_error_m,
                heading_error_rad=heading_error_rad, distance_error_ratio=distance_error_ratio,
                heading_error_per_m=heading_error_per_m),
            sensor=dict(vehicle_max_age_us=vehicle_max_age_us, max_yaw_rate_rps=max_yaw_rate_rps,
                max_gap_us=max_gap_us, pose_capacity=pose_capacity),
            clock=dict(timestamp_error_us=timestamp_error_us, drift_ppm=drift_ppm,
                max_offset_width_us=max_offset_width_us, lease_us=lease_us, lookback_us=lookback_us),
            qualification=dict(input_kind='SYNTHETIC', measured=False, physical_admission=False))
        if steering_sensor_profile is not None:
            from .steering_sensor import VirtualSteeringSensorProfile
            if (type(steering_sensor_profile) is not VirtualSteeringSensorProfile
                    or steering_sensor_profile.max_rate_rps<vehicle['max_steer_rate_rps']):
                raise ValueError('explicit virtual steering leaf must cover the original servo rate')
            value['profile']='course-development-motion-2'
            value['steering_sensor']=dict(leaf_sha256=steering_sensor_profile.sha256,
                profile=steering_sensor_profile.fields,combined_wire_quality=3)
        raw = canonical(value)
        if len(raw) > 4096:
            raise ValueError('bounded development motion manifest exceeds4096 bytes')
        fields = neutral.to_dict()
        fields['vehicle_bundle_sha256'] = sha256(raw).hexdigest()
        object.__setattr__(self, '_bytes', raw)
        object.__setattr__(self, '_development', development_profile)
        object.__setattr__(self, '_neutral', neutral)
        object.__setattr__(self, '_bound', CalibrationProfile.from_dict(fields))
        object.__setattr__(self, '_steering_sensor', steering_sensor_profile)

    @property
    def canonical_bytes(self):
        return self._bytes

    @property
    def sha256(self):
        return self._bound.vehicle_bundle_sha256

    @property
    def fields(self):
        return json.loads(self._bytes)

    @property
    def development_profile(self):
        return self._development

    @property
    def calibration_model_sha256(self):
        return self.fields['calibration_model_sha256']

    def check_development_profile(self, profile):
        """scene만 다른 원 profile도 같은 neutral/model 관계일 때만 받는다."""
        if (type(profile) is not DevelopmentProfile
                or profile.calibration_model_sha256 != self.calibration_model_sha256
                or profile.calibration().sha256 != self.neutral_calibration.sha256):
            raise ValueError('development profile changed the original neutral/model relation')

    @property
    def neutral_calibration(self):
        return self._neutral

    @property
    def bound_calibration(self):
        return self._bound

    @property
    def steering_sensor_profile(self):
        """None은 기존 estimated 경로다. 명시 leaf는 별도 실제 가상 관측을 요구한다."""
        return self._steering_sensor

    @property
    def odometry_arguments(self):
        """같은 B08 module의 실제 OdometryConfig 생성자에 넘길 값이며 codec은 복제하지 않는다."""
        fields = self.fields
        encoder = fields['encoder']
        return dict(fields['odometry'], vehicle=fields['identity']['vehicle'], calibration=self.sha256,
            metres_per_tick=encoder['metres_per_tick'], track_width_m=encoder['track_width_m'],
            encoder_bits=encoder['encoder_bits'], metres_per_tick_upper=encoder['metres_per_tick_upper'])

    @property
    def sensor_arguments(self):
        fields = self.fields
        return dict(tof_leaves={}, vehicle_max_age_us=fields['sensor']['vehicle_max_age_us'],
            # 이 owner는 Vehicle만 소비한다. 미설치 ToF의 나이 인자는 사용되지 않는다.
            tof_max_age_us=fields['sensor']['vehicle_max_age_us'],
            max_speed_mps=fields['odometry']['maximum_speed_mps'],
            max_yaw_rate_rps=fields['sensor']['max_yaw_rate_rps'])

    @property
    def pose_arguments(self):
        fields = self.fields
        return dict(max_gap_us=fields['sensor']['max_gap_us'],
            max_speed_mps=fields['odometry']['maximum_speed_mps'],
            max_yaw_rate_rps=fields['sensor']['max_yaw_rate_rps'], capacity=fields['sensor']['pose_capacity'])

    def clock_policy(self, *, device, primitive):
        """원 공용 정책 형식을 쓰며 실제 Probe/lease는 caller가 별도로 수락한다."""
        from xenova_drive_world.mapper_clock import ClockPolicy
        fields = self.fields
        return ClockPolicy(vehicle=fields['identity']['vehicle'], device=device,
            configuration=fields['identity']['configuration'], calibration=self.sha256,
            primitive=primitive, vehicle_max_age_us=fields['sensor']['vehicle_max_age_us'], **fields['clock'])
