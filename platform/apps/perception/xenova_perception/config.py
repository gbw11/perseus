"""설정. 브로커 접속값은 환경변수(XENOVA_PERCEPTION_*)로 받고 명령행 인자가 있으면 그것을 쓴다 (__main__).

비밀번호는 저장소에 두지 않는다. 환경변수 XENOVA_PERCEPTION_BROKER_PASSWORD 로 넘기는 것을 권장한다.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

# 계약 v1.1 2장·4장: 젯슨은 vm_id 대신 source_id jetson, 토픽 app_id 는 perception
SOURCE_ID = "jetson"
APP_ID = "perception"
MESSAGE_TYPE = "perception.result"
# 계약 9장: 젯슨 계정 = perception
DEFAULT_USERNAME = "perception"
# 계약 5.3: perception.result 15 Hz
RESULT_RATE_HZ = 15.0

SOURCES = ("synthetic", "webcam", "video")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
ENV_PREFIX = "XENOVA_PERCEPTION_"


def _positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")


@dataclass(frozen=True)
class Camera:
    """핀홀 카메라와 차체 위 장착값. 1/10 RC카에 노트북 웹캠을 대신 단 정도의 기본값이다."""

    height_m: float = 0.15            # 지면에서 카메라 렌즈까지
    pitch_rad: float = math.radians(15)  # 아래로 숙인 각 (+ = 아래)
    hfov_rad: float = math.radians(60)   # 수평 화각. 노트북 웹캠은 대개 55~70°

    def __post_init__(self) -> None:
        _positive("camera height_m", self.height_m)
        _positive("camera hfov_rad", self.hfov_rad)
        if not self.hfov_rad < math.pi:
            raise ValueError("camera hfov_rad must be below 180°")
        if not -math.pi / 2 < self.pitch_rad < math.pi / 2:
            raise ValueError("camera pitch_rad must be within ±90°")


@dataclass(frozen=True)
class StopZone:
    """전방 정지 영역. 이 안에 장애물이 조금이라도 걸치면 stop = true."""

    distance_m: float = 0.8           # 카메라 바로 아래 지면(원점)부터 전방
    vehicle_width_m: float = 0.20     # 1/10 RC카 차폭
    margin_m: float = 0.05            # 좌우 여유

    def __post_init__(self) -> None:
        _positive("stop distance_m", self.distance_m)
        _positive("vehicle_width_m", self.vehicle_width_m)
        if not (math.isfinite(self.margin_m) and self.margin_m >= 0):
            raise ValueError("margin_m must be >= 0")

    @property
    def half_width_m(self) -> float:
        return self.vehicle_width_m / 2 + self.margin_m


@dataclass(frozen=True)
class PerceptionSettings:
    source: str = "synthetic"
    device: int = 0                   # webcam 장치 번호
    video_path: str | None = None     # video 파일
    scenario: str = "approach"        # synthetic 시나리오
    broker_host: str = "127.0.0.1"
    broker_port: int = 1883
    username: str | None = DEFAULT_USERNAME
    password: str | None = None
    camera: Camera = Camera()
    stop_zone: StopZone = StopZone()
    lane_width_m: float = 0.35        # 한쪽 차선만 보일 때 중심을 추정하는 차로 폭
    max_obstacles: int = 8            # 가까운 순으로 이만큼만 싣는다
    # 이 시간보다 오래된 프레임 결과는 다시 내지 않는다. 카메라가 멈췄는데 옛 결과를 계속 내면
    # 자율주행 VM 이 젯슨 끊김(시나리오 F6)을 알아채지 못한다
    stale_s: float = 0.5
    rate_hz: float = RESULT_RATE_HZ
    show: bool = False
    duration_s: float | None = None   # 주면 그 시간 뒤 종료 (시험용)
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {self.source!r}")
        if self.source == "video" and not self.video_path:
            raise ValueError("source video needs a video file path")
        if not 1 <= self.broker_port <= 65535:
            raise ValueError(f"broker_port must be 1..65535, got {self.broker_port!r}")
        if not self.broker_host:
            raise ValueError("broker_host must not be empty")
        for name in ("lane_width_m", "stale_s", "rate_hz"):
            _positive(name, getattr(self, name))
        if self.max_obstacles < 0:
            raise ValueError("max_obstacles must be >= 0")
        if self.duration_s is not None:
            _positive("duration_s", self.duration_s)
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {LOG_LEVELS}, got {self.log_level!r}")


def broker_from_env(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """환경변수의 브로커 접속값. 비어 있는 변수는 빼서 기본값을 쓰게 한다."""
    env = os.environ if env is None else env
    values: dict[str, object] = {}
    for key, name, cast in (
        ("broker_host", "BROKER_HOST", str),
        ("broker_port", "BROKER_PORT", int),
        ("username", "BROKER_USERNAME", str),
        ("password", "BROKER_PASSWORD", str),
        ("log_level", "LOG_LEVEL", str.upper),
    ):
        raw = env.get(ENV_PREFIX + name, "").strip()
        if raw:
            try:
                values[key] = cast(raw)
            except ValueError as exc:
                raise ValueError(f"{ENV_PREFIX}{name}: {exc}") from exc
    return values
