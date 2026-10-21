"""차량 좌표계, 화면 → 지면 투영, stop 판정, perception.result payload. OpenCV 없이 돈다.

좌표계 (계약에 없어 이 앱이 정한 가정, README "좌표계·단위"):
- 원점: 카메라 바로 아래 지면. x = 전방 m, y = 왼쪽 + m (오른손 좌표, z 위).
- 장애물 {x, y, w}: 지면에 닿은 아랫변 가운데의 x·y, w = 아랫변 좌우 폭 m.
- lane_offset_m: 차선 중심에 대한 차의 가로 위치. + = 차가 중심보다 왼쪽.
- heading_err_rad: 차의 진행 방향에서 본 차선 방향. + = 차선이 왼쪽으로 돈다 (steer_rad + 와 같은 쪽).

투영: 지면이 평평하고 카메라 롤이 0 인 핀홀 모델. 화면 픽셀 (u, v) 에서 나간 광선이 지면과 만나는 점을 구한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .config import Camera, StopZone

# 수평선 바로 아래는 거리가 무한대로 튄다. 광선이 이 값보다 덜 숙이면 지면에 안 닿는 것으로 본다
_MIN_DOWN = 1e-3


@dataclass(frozen=True)
class Obstacle:
    x: float
    y: float
    w: float


@dataclass(frozen=True)
class Lane:
    offset_m: float = 0.0
    heading_err_rad: float = 0.0


@dataclass(frozen=True)
class Result:
    """한 번의 인식 결과. at = 이 결과를 만든 시각 (발행 쪽 단조 시계)."""

    lane: Lane = Lane()
    obstacles: tuple[Obstacle, ...] = ()
    stop: bool = False
    at: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict, compare=False)  # 미리보기용 (박스 등). 발행하지 않는다


class Projector:
    """핀홀 카메라 투영. 화면 크기마다 하나 만든다."""

    def __init__(self, camera: Camera, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("image size must be positive")
        self.camera = camera
        self.width = width
        self.height = height
        # 정사각 픽셀 가정: 세로 초점거리 = 가로 초점거리
        self.f = (width / 2) / math.tan(camera.hfov_rad / 2)
        self.cx = width / 2
        self.cy = height / 2
        self._sin = math.sin(camera.pitch_rad)
        self._cos = math.cos(camera.pitch_rad)

    def _down(self, v: Any) -> Any:
        """광선의 아래쪽 성분. float·numpy 배열 모두 받는다 (사칙연산만 쓴다)."""
        d = (v - self.cy) / self.f
        return self._sin + d * self._cos, d

    def to_ground(self, u: float, v: float) -> tuple[float, float] | None:
        """픽셀 → 지면 (x, y). 수평선 위·근처면 None."""
        down, d = self._down(v)
        if down <= _MIN_DOWN:
            return None
        r = (u - self.cx) / self.f
        t = self.camera.height_m / down
        return t * (self._cos - d * self._sin), -t * r

    def to_ground_many(self, u: Any, v: Any) -> tuple[Any, Any, Any]:
        """numpy 배열판. (x, y, 유효 마스크). 유효하지 않은 점의 x·y 는 쓰지 않는다."""
        import numpy as np  # webcam·video 경로에서만 불린다

        down, d = self._down(np.asarray(v, dtype=float))
        valid = down > _MIN_DOWN
        safe = np.where(valid, down, 1.0)  # 0 나눗셈 방지
        r = (u - self.cx) / self.f
        t = self.camera.height_m / safe
        return t * (self._cos - d * self._sin), -t * r, valid

    def horizon_v(self) -> float:
        """수평선의 화면 행. 이보다 위(작은 v)는 지면이 아니다."""
        return self.cy - self.f * math.tan(self.camera.pitch_rad)

    def to_image(self, x: float, y: float) -> tuple[float, float] | None:
        """지면 (x, y) → 픽셀. 카메라 뒤면 None. 미리보기·시험용."""
        h = self.camera.height_m
        # 카메라 좌표: 전방 축 성분 c, 화면 아래 축 성분 dn, 오른쪽 성분 rt
        c = x * self._cos + h * self._sin
        dn = -x * self._sin + h * self._cos
        rt = -y
        if c <= 1e-9:
            return None
        return self.cx + self.f * rt / c, self.cy + self.f * dn / c


def obstacle_from_box(projector: Projector, left: float, right: float, bottom: float) -> Obstacle | None:
    """화면 박스의 아랫변(지면에 닿은 곳)으로 장애물 위치·폭을 구한다."""
    pl = projector.to_ground(left, bottom)
    pr = projector.to_ground(right, bottom)
    pc = projector.to_ground((left + right) / 2, bottom)
    if pl is None or pr is None or pc is None:
        return None
    return Obstacle(x=pc[0], y=pc[1], w=abs(pl[1] - pr[1]))


def in_stop_zone(obstacle: Obstacle, zone: StopZone) -> bool:
    """장애물이 전방 정지 영역(0 ≤ x < distance, 차폭/2+여유 안)에 조금이라도 걸치는지."""
    if not 0 <= obstacle.x < zone.distance_m:
        return False
    return abs(obstacle.y) - obstacle.w / 2 < zone.half_width_m


def make_result(lane: Lane, obstacles: list[Obstacle], zone: StopZone, at: float, *, limit: int = 8, **extra: Any) -> Result:
    """가까운 순 정렬, 개수 제한, stop 판정. stop 은 자르기 전 전체로 판정한다."""
    ordered = sorted(obstacles, key=lambda o: o.x)
    stop = any(in_stop_zone(o, zone) for o in ordered)
    return Result(lane=lane, obstacles=tuple(ordered[:limit]), stop=stop, at=at, extra=extra)


def _f(value: float) -> float:
    """JSON 에 실을 float. NaN·무한대는 계약 위반이라 0 으로 막는다."""
    value = float(value)
    return round(value, 3) if math.isfinite(value) else 0.0


def payload(result: Result) -> dict[str, Any]:
    """계약 5.4 perception.result payload. 필드는 정확히 이 넷이다."""
    return {
        "lane_offset_m": _f(result.lane.offset_m),
        "heading_err_rad": _f(result.lane.heading_err_rad),
        "obstacles": [{"x": _f(o.x), "y": _f(o.y), "w": _f(o.w)} for o in result.obstacles],
        "stop": bool(result.stop),
    }
