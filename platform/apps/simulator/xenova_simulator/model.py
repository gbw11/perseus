"""차체 운동 모델: 자전거 모델 + 가속·조향 속도 제한. MQTT 를 모르는 순수 계산이라 따로 시험한다."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import SimSettings


@dataclass
class Obstacle:
    x: float
    y: float


@dataclass
class Vehicle:
    settings: SimSettings
    x: float = 0.0
    y: float = 0.0
    heading_rad: float = 0.0
    v_mps: float = 0.0
    steer_rad: float = 0.0
    obstacles: list[Obstacle] = field(default_factory=list)

    def reset(self) -> None:
        self.x = self.y = self.heading_rad = self.v_mps = self.steer_rad = 0.0

    def place_obstacle_ahead(self, distance_m: float) -> Obstacle:
        """현재 진행 방향으로 distance_m 앞에 장애물을 놓는다 (시연 명령 'o')."""
        obstacle = Obstacle(self.x + distance_m * math.cos(self.heading_rad), self.y + distance_m * math.sin(self.heading_rad))
        self.obstacles.append(obstacle)
        return obstacle

    def obstacle_ahead(self) -> bool:
        """전방 감지 범위 안에 장애물 가장자리가 들어왔는가."""
        s = self.settings
        for o in self.obstacles:
            dx, dy = o.x - self.x, o.y - self.y
            gap = math.hypot(dx, dy) - s.obstacle_radius_m
            if gap > s.sense_range_m:
                continue
            bearing = math.atan2(dy, dx) - self.heading_rad
            bearing = math.atan2(math.sin(bearing), math.cos(bearing))
            if abs(bearing) <= s.sense_half_angle_rad:
                return True
        return False

    def step(self, target_v: float, target_steer: float, dt: float) -> None:
        """dt 초 동안 목표값 쪽으로 움직인다. 전방 장애물이면 전진 목표를 0 으로 막는다 (후진은 허용)."""
        s = self.settings
        if target_v > 0 and self.obstacle_ahead():
            target_v = 0.0
            # 이미 전진 중이면 바로 세운다: ESP32 근거리 감지는 가속 제한 없이 모터를 끊는다
            if self.v_mps > 0:
                self.v_mps = 0.0
        target_steer = max(-s.max_steer_rad, min(s.max_steer_rad, target_steer))
        self.v_mps += _clamp(target_v - self.v_mps, s.max_accel_mps2 * dt)
        self.steer_rad += _clamp(target_steer - self.steer_rad, s.max_steer_rate_rps * dt)
        self.x += self.v_mps * math.cos(self.heading_rad) * dt
        self.y += self.v_mps * math.sin(self.heading_rad) * dt
        self.heading_rad += self.v_mps / s.wheelbase_m * math.tan(self.steer_rad) * dt
        self.heading_rad = math.atan2(math.sin(self.heading_rad), math.cos(self.heading_rad))


def _clamp(delta: float, limit: float) -> float:
    return max(-limit, min(limit, delta))
