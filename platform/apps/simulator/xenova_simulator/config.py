"""시뮬레이터 설정. 개발 도구라 환경변수 대신 명령행 인자로 받는다 (__main__)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

# MQTT 계약 v1.1 5.4: vehicle.status 는 게이트웨이가 source_id dom0-gateway 로 낸다
SOURCE_ID = "dom0-gateway"
# 계약 9장: vehicle/status 발행 권한은 gateway 계정에 있다 (v1.1)
DEFAULT_USERNAME = "gateway"
# 계약 5.3: vehicle.status 20 Hz
STATUS_RATE_HZ = 20.0
# 계약 5.1: ESP32 와치독 300 ms
WATCHDOG_MS = 300.0
# 명령을 받는 경로
#   mqtt   = 앱의 drive.command 를 직접 본다 (게이트웨이 없이 시험. 게이트웨이의 출력 허가·재무장은 못 본다)
#   frames = 게이트웨이가 ESP32 로 보내는 시리얼 프레임을 TCP 로 받는다 (실제 ESP32 자리)
INPUTS = ("mqtt", "frames")


def _positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")


@dataclass(frozen=True)
class Drivetrain:
    """후륜 구동 모터·엔코더와 전륜 조향 서보. 1/10 RC카 기준 값이며 실차 측정 후 맞춘다."""

    wheel_radius_m: float = 0.033        # 구동 바퀴 반지름 (지름 66 mm)
    encoder_cpr: int = 1320              # 바퀴 한 바퀴당 엔코더 count (11 PPR × 감속 30 × 4체배)
    motor_tau_s: float = 0.12            # 모터 속도 응답 시정수 (1차 지연)
    max_wheel_rpm: float = 450.0         # 최고 회전수 (≈1.55 m/s)
    max_accel_mps2: float = 2.0          # 가속 한계 (바퀴 둘레 속도 기준)
    max_decel_mps2: float = 4.0          # 감속·정지 한계
    max_steer_rad: float = 0.45          # 서보 기계 한계 (가상 전륜각)
    max_steer_rate_rps: float = 5.0      # 서보 속도 (≈0.2 s/60°)

    def __post_init__(self) -> None:
        for name in ("wheel_radius_m", "motor_tau_s", "max_wheel_rpm", "max_accel_mps2", "max_decel_mps2",
                     "max_steer_rad", "max_steer_rate_rps"):
            _positive(name, getattr(self, name))
        if self.encoder_cpr <= 0:
            raise ValueError("encoder_cpr must be positive")


@dataclass(frozen=True)
class CameraSpec:
    """전방 카메라. 인지 대역(xenova_perception.config.Camera)의 기본값과 같아야 거리가 맞는다."""

    width: int = 320
    height: int = 240
    height_m: float = 0.15               # 지면에서 렌즈까지
    pitch_rad: float = math.radians(15)  # 아래로 숙인 각
    hfov_rad: float = math.radians(60)   # 수평 화각
    mount_x_m: float = 0.20              # 후륜 축 중심에서 카메라까지 전방 거리
    fps: float = 15.0
    max_range_m: float = 8.0             # 이보다 먼 지면은 안개색으로 그린다
    line_width_m: float = 0.025          # 차선 양쪽 흰 선 폭 (차선 안쪽 가장자리에 칠한다)
    raster_m: float = 0.02               # 트랙 오프셋 지도 해상도 (픽셀마다 중심선 거리를 찾는 표)
    jpeg_quality: int = 80

    def __post_init__(self) -> None:
        for name in ("height_m", "hfov_rad", "fps", "max_range_m", "line_width_m", "raster_m"):
            _positive(f"camera {name}", getattr(self, name))
        if self.width < 16 or self.height < 16:
            raise ValueError("camera size too small")
        if not 0 < self.pitch_rad < math.pi / 2:
            raise ValueError("camera pitch_rad must be within (0, 90°)")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("camera jpeg_quality must be 1..100")


@dataclass(frozen=True)
class SimSettings:
    broker_host: str = "127.0.0.1"
    broker_port: int = 1883
    username: str | None = DEFAULT_USERNAME
    password: str | None = None
    # 시뮬레이터 전용 토픽(계약 §12: sim.pose·sim.world·sim.command·sim.command.result)용 두 번째 계정.
    # vehicle.status 는 gateway 계정, sim/* 는 simulator 계정 (계약 9장)
    sim_topics: bool = True
    sim_username: str | None = "simulator"
    sim_password: str | None = None
    input: str = "mqtt"
    frame_listen: str = "0.0.0.0:7600"            # frames 입력: 게이트웨이 시리얼 프레임을 받을 TCP 주소
    camera_listen: str | None = "0.0.0.0:8090"    # 전방 카메라 MJPEG. None 이면 카메라를 끈다
    track_id: str = "spielberg"                   # 계약 v1.4 §11 트랙 10개 중 하나 (track.track_ids())
    # 출발 신호 (계약 §12 sim.pose.race, 시나리오 P7). False 면 바로 go — 랩 시간만 잰다 (시험·개발용)
    start_lights: bool = True
    drivetrain: Drivetrain = field(default_factory=Drivetrain)
    camera: CameraSpec = field(default_factory=CameraSpec)
    wheelbase_m: float = 0.26
    # ESP32 근거리 감지(VL53L5CX) 흉내: 전방 이 거리·이 각도 안에 장애물이 있으면 전진을 막는다
    sense_range_m: float = 0.5
    sense_half_angle_rad: float = math.radians(30)
    # 배터리: 시작 전압에서 시간에 따라 조금씩 떨어지는 흉내
    batt_full_v: float = 8.4
    batt_empty_v: float = 6.6
    batt_drain_v_per_min: float = 0.02
    watchdog_ms: float = WATCHDOG_MS
    status_rate_hz: float = STATUS_RATE_HZ
    physics_rate_hz: float = 100.0
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for name in (
            "wheelbase_m", "sense_range_m", "sense_half_angle_rad", "watchdog_ms", "status_rate_hz", "physics_rate_hz",
        ):
            _positive(name, getattr(self, name))
        if not 1 <= self.broker_port <= 65535:
            raise ValueError(f"broker_port must be 1..65535, got {self.broker_port!r}")
        if self.batt_empty_v >= self.batt_full_v:
            raise ValueError("batt_empty_v must be below batt_full_v")
        from .track import track_ids  # 트랙 데이터는 패키지 안 tracks.json

        if self.track_id not in track_ids():
            raise ValueError(f"track_id must be one of {track_ids()}, got {self.track_id!r}")
        if self.input not in INPUTS:
            raise ValueError(f"input must be one of {INPUTS}, got {self.input!r}")
        parse_address(self.frame_listen)
        if self.camera_listen is not None:
            parse_address(self.camera_listen)


def parse_address(text: str) -> tuple[str, int]:
    """'host:port' → (host, port). port 0 은 빈 포트 자동 선택 (시험용)."""
    host, sep, port = text.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise ValueError(f"address must be host:port, got {text!r}")
    return host, int(port)


def camera_for_layout(camera: CameraSpec, layout: dict | None) -> CameraSpec:
    """새 시험 코스의 잠정 extrinsic/FOV만 적용하고 출력 해상도는 보존한다."""
    if not layout:
        return camera
    return replace(camera, **layout["camera"])


def settings_for_track(settings: SimSettings, track) -> SimSettings:
    """기존 서킷 설정을 보존하며 Jetson 코스의 미실측 차량 가정을 적용한다."""
    if not track.layout:
        return replace(settings, track_id=track.id)
    vehicle = track.layout["vehicle"]
    wheelbase = vehicle["wheelbase_m"]
    drivetrain = replace(settings.drivetrain,
                         max_steer_rad=math.atan(wheelbase / vehicle["min_turn_radius_m"]))
    return replace(settings, track_id=track.id, wheelbase_m=wheelbase, drivetrain=drivetrain,
                   camera=camera_for_layout(settings.camera, track.layout))
