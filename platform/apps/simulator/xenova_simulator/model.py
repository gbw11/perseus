"""차체 운동 모델. MQTT 를 모르는 순수 계산이라 따로 시험한다.

차를 목표 속도로 바로 옮기지 않고 실차처럼 나눈다.
- ESP32 가 목표 속도(m/s)를 바퀴 목표 회전수로 바꾼다 (v / 바퀴 반지름).
- 구동 모터는 1차 지연 + 가속·감속 한계로 그 회전수를 따라가고, 엔코더가 회전을 count 로 센다.
- 서보는 목표 전륜각을 속도 한계로 따라간다 (실제 각도 센서는 없다고 본다).
- 차는 구동 바퀴 회전(바퀴 둘레 속도)과 전륜각으로 자전거 모델을 따라 움직인다 (기준점 = 후륜 축 중심, 미끄럼 없음).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import SimSettings

TWO_PI = 2 * math.pi
# 계약 §12 sim.command obstacle.add: 폭은 종류로 정한다 (관리 콘솔 mock KIND_W 와 같음). 높이는 카메라 그림용
KIND_W = {"cone": 0.18, "box": 0.30, "tires": 0.26}
KIND_H = {"cone": 0.18, "box": 0.20, "tires": 0.15}


@dataclass(frozen=True)
class Obstacle:
    """장애물 바닥 가운데 위치(m), 정사각 바닥 한 변(m), 높이(m), 종류."""

    x: float
    y: float
    w: float = KIND_W["cone"]
    h: float = KIND_H["cone"]
    id: str = ""
    kind: str = "cone"

    @classmethod
    def of(cls, kind: str, x: float, y: float, obstacle_id: str = "") -> Obstacle:
        return cls(x, y, KIND_W[kind], KIND_H[kind], obstacle_id, kind)


@dataclass
class Vehicle:
    settings: SimSettings
    x: float = 0.0
    y: float = 0.0
    heading_rad: float = 0.0
    wheel_rad_s: float = 0.0       # 구동 바퀴 각속도 (실제)
    target_rad_s: float = 0.0      # ESP32 속도 루프의 목표 각속도
    steer_rad: float = 0.0         # 서보가 만든 전륜각 (실제)
    encoder_total: float = 0.0     # 엔코더 누계 (소수까지). 정수 count 는 encoder_count
    obstacles: list[Obstacle] = field(default_factory=list)

    @property
    def v_mps(self) -> float:
        """바퀴 둘레 속도 = 차 속도 (미끄럼 없음)."""
        return self.wheel_rad_s * self.settings.drivetrain.wheel_radius_m

    @property
    def wheel_rpm(self) -> float:
        return self.wheel_rad_s * 60.0 / TWO_PI

    @property
    def encoder_count(self) -> int:
        return math.floor(self.encoder_total)

    def reset(self, x: float = 0.0, y: float = 0.0, heading_rad: float = 0.0) -> None:
        self.x, self.y, self.heading_rad = x, y, heading_rad
        self.wheel_rad_s = self.target_rad_s = self.steer_rad = 0.0

    def place_obstacle_ahead(self, distance_m: float, obstacle_id: str = "", kind: str = "cone") -> Obstacle:
        """현재 진행 방향으로 distance_m 앞(후륜 축 기준)에 장애물을 놓는다 (시연 명령 'o')."""
        obstacle = Obstacle.of(
            kind, self.x + distance_m * math.cos(self.heading_rad), self.y + distance_m * math.sin(self.heading_rad),
            obstacle_id,
        )
        # 목록은 통째로 바꿔 끼운다: 물리·카메라 스레드가 옛 목록을 끝까지 읽을 수 있다
        self.obstacles = [*self.obstacles, obstacle]
        return obstacle

    def obstacle_ahead(self) -> bool:
        """앞바퀴 축에서 전방 감지 범위 안에 장애물 가장자리가 들어왔는가 (ESP32 근거리 센서 흉내)."""
        s = self.settings
        fx = self.x + s.wheelbase_m * math.cos(self.heading_rad)
        fy = self.y + s.wheelbase_m * math.sin(self.heading_rad)
        for o in self.obstacles:
            dx, dy = o.x - fx, o.y - fy
            if math.hypot(dx, dy) - o.w / 2 > s.sense_range_m:
                continue
            bearing = math.atan2(dy, dx) - self.heading_rad
            bearing = math.atan2(math.sin(bearing), math.cos(bearing))
            if abs(bearing) <= s.sense_half_angle_rad:
                return True
        return False

    def step(self, target_v: float, target_steer: float, dt: float) -> None:
        """dt 초 동안 ESP32·모터·서보·차체를 진행한다. 전방 장애물이면 전진 목표를 0 으로 막는다 (후진은 허용)."""
        d = self.settings.drivetrain
        r = d.wheel_radius_m
        max_rad_s = d.max_wheel_rpm * TWO_PI / 60.0
        if target_v > 0 and self.obstacle_ahead():
            target_v = 0.0
            # 이미 전진 중이면 바로 세운다: ESP32 근거리 감지는 가속 제한 없이 모터를 끊는다
            if self.wheel_rad_s > 0:
                self.wheel_rad_s = 0.0
        self.target_rad_s = max(-max_rad_s, min(max_rad_s, target_v / r))

        # 모터: 구동은 1차 지연 + 가속 한계, 정지 목표는 감속 한계로 끝까지 제동한다 (1차 지연은 0 에 닿지 않는다)
        if self.target_rad_s == 0.0:
            self.wheel_rad_s -= _clamp(self.wheel_rad_s, d.max_decel_mps2 / r * dt)
        else:
            delta = (self.target_rad_s - self.wheel_rad_s) * min(1.0, dt / d.motor_tau_s)
            speeding_up = abs(self.target_rad_s) > abs(self.wheel_rad_s) and self.target_rad_s * self.wheel_rad_s >= 0
            limit = (d.max_accel_mps2 if speeding_up else d.max_decel_mps2) / r * dt
            self.wheel_rad_s += _clamp(delta, limit)

        # 서보: 목표 전륜각을 속도 한계로 따라간다
        target_steer = max(-d.max_steer_rad, min(d.max_steer_rad, target_steer))
        self.steer_rad += _clamp(target_steer - self.steer_rad, d.max_steer_rate_rps * dt)

        # 엔코더와 차체: 바퀴가 돈 만큼만 움직인다
        self.encoder_total += self.wheel_rad_s * dt / TWO_PI * d.encoder_cpr
        v = self.v_mps
        heading_mid = self.heading_rad + v / self.settings.wheelbase_m * math.tan(self.steer_rad) * dt / 2
        self.x += v * math.cos(heading_mid) * dt
        self.y += v * math.sin(heading_mid) * dt
        self.heading_rad += v / self.settings.wheelbase_m * math.tan(self.steer_rad) * dt
        self.heading_rad = math.atan2(math.sin(self.heading_rad), math.cos(self.heading_rad))


def _clamp(delta: float, limit: float) -> float:
    return max(-limit, min(limit, delta))
