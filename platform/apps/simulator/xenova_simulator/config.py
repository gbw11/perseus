"""시뮬레이터 설정. 개발 도구라 환경변수 대신 명령행 인자로 받는다 (__main__)."""

from __future__ import annotations

import math
from dataclasses import dataclass

# MQTT 계약 v1.1 5.4: vehicle.status 는 게이트웨이가 source_id dom0-gateway 로 낸다
SOURCE_ID = "dom0-gateway"
# 계약 9장: vehicle/status 발행 권한은 gateway 계정에 있다 (v1.1)
DEFAULT_USERNAME = "gateway"
# 계약 5.3: vehicle.status 20 Hz
STATUS_RATE_HZ = 20.0
# 계약 5.1: ESP32 와치독 300 ms
WATCHDOG_MS = 300.0
# 계약 11장 시뮬레이터 전용 토픽: 발행자 source_id·브로커 계정 simulator, sim.pose 20 Hz
SIM_SOURCE_ID = "simulator"
SIM_USERNAME = "simulator"
POSE_RATE_HZ = 20.0
MAX_OBSTACLES = 8
PUSH_MAX_M = 0.5


def _positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")


@dataclass(frozen=True)
class SimSettings:
    broker_host: str = "127.0.0.1"
    broker_port: int = 1883
    username: str | None = DEFAULT_USERNAME
    password: str | None = None
    # sim 토픽용 두 번째 계정 (vehicle.status 는 gateway 계정, sim/* 는 simulator 계정 — 계약 9장)
    sim_topics: bool = True
    sim_username: str | None = SIM_USERNAME
    sim_password: str | None = None
    # 트랙: 차선 중심선 타원 (트랙 고정 좌표, 원점 = 타원 중심). 6 m × 3 m, 차선 폭 0.4 m (시나리오 P4)
    track_a_m: float = 3.0
    track_b_m: float = 1.5
    lane_width_m: float = 0.4
    # 1/10 RC카 기준 값. 실차 측정 후 맞춘다
    wheelbase_m: float = 0.26
    max_accel_mps2: float = 2.0
    max_steer_rate_rps: float = 3.0
    max_steer_rad: float = 0.45
    # ESP32 근거리 감지(VL53L5CX) 흉내: 전방 이 거리·이 각도 안에 장애물이 있으면 전진을 막는다
    sense_range_m: float = 0.5
    sense_half_angle_rad: float = math.radians(30)
    obstacle_width_m: float = 0.2      # 터미널 'o' 로 놓는 장애물 폭 (sim.command 는 w 를 직접 준다)
    # 배터리: 시작 전압에서 시간에 따라 조금씩 떨어지는 흉내
    batt_full_v: float = 8.4
    batt_empty_v: float = 6.6
    batt_drain_v_per_min: float = 0.02
    watchdog_ms: float = WATCHDOG_MS
    status_rate_hz: float = STATUS_RATE_HZ
    pose_rate_hz: float = POSE_RATE_HZ
    physics_rate_hz: float = 100.0
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for name in (
            "wheelbase_m", "max_accel_mps2", "max_steer_rate_rps", "max_steer_rad", "sense_range_m",
            "sense_half_angle_rad", "obstacle_width_m", "watchdog_ms", "status_rate_hz", "physics_rate_hz",
            "pose_rate_hz", "track_a_m", "track_b_m", "lane_width_m",
        ):
            _positive(name, getattr(self, name))
        if not 1 <= self.broker_port <= 65535:
            raise ValueError(f"broker_port must be 1..65535, got {self.broker_port!r}")
        if self.batt_empty_v >= self.batt_full_v:
            raise ValueError("batt_empty_v must be below batt_full_v")
