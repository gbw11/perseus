"""카메라 없이 시간표대로 장애물·차선을 만드는 합성 입력. 시험·CI·데모 백업용.

시나리오는 경과 시간 t(초) → 차량 좌표계의 장애물·차선이고, 주기마다 반복한다.
stop 은 시나리오가 정하지 않고 실제 입력과 같은 판정(geometry.in_stop_zone)을 거친다.

| 이름 | 주기 | 내용 |
| --- | --- | --- |
| approach | 12 s | 0~2 s 빈 길 → 2 s 에 3 m 앞 콘, 0.5 m/s 로 다가와 0.5 m 에서 멈춤 (6.4 s 부터 stop) → 8 s 치움 |
| cut-in | 10 s | 0~2 s 빈 길 → 0.6 m 앞 왼쪽 0.8 m 에서 콘이 0.25 m/s 로 가운데로 끼어듦 (약 4.1 s 부터 stop) → 6 s 치움 |
| clear | 6 s | 장애물 없음, 차선만 좌우로 조금 흔들림 |
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from .config import PerceptionSettings
from .geometry import Lane, Obstacle, Result, make_result

CONE_W = 0.25


def _lane(t: float) -> Lane:
    # 6 s 주기로 차가 차선 안에서 ±5 cm 흔들리는 모양. 방향 오차는 그 변화를 따라간다
    phase = 2 * math.pi * t / 6.0
    return Lane(offset_m=0.05 * math.sin(phase), heading_err_rad=-0.08 * math.cos(phase))


def approach(t: float) -> list[Obstacle]:
    if 2.0 <= t < 8.0:
        return [Obstacle(x=max(0.5, 3.0 - 0.5 * (t - 2.0)), y=0.0, w=CONE_W)]
    return []


def cut_in(t: float) -> list[Obstacle]:
    if 2.0 <= t < 6.0:
        return [Obstacle(x=0.6, y=max(0.0, 0.8 - 0.25 * (t - 2.0)), w=CONE_W)]
    return []


def clear(t: float) -> list[Obstacle]:
    return []


SCENARIOS: dict[str, tuple[float, Callable[[float], list[Obstacle]]]] = {
    "approach": (12.0, approach),
    "cut-in": (10.0, cut_in),
    "clear": (6.0, clear),
}


class SyntheticSource:
    """current(now) 를 부를 때마다 그 시각의 결과를 만든다. 늘 신선하다."""

    def __init__(self, settings: PerceptionSettings, *, clock: Callable[[], float] = time.monotonic) -> None:
        if settings.scenario not in SCENARIOS:
            raise ValueError(f"scenario must be one of {tuple(SCENARIOS)}, got {settings.scenario!r}")
        self._settings = settings
        self._period, self._obstacles = SCENARIOS[settings.scenario]
        self._start = clock()

    def at(self, elapsed_s: float, now: float = 0.0) -> Result:
        t = elapsed_s % self._period
        return make_result(
            _lane(t), self._obstacles(t), self._settings.stop_zone, now, limit=self._settings.max_obstacles,
        )

    def current(self, now: float) -> Result:
        return self.at(now - self._start, now)
