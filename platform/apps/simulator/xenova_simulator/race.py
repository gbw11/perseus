"""출발 신호·랩 시간 (MQTT 계약 §12 `sim.pose.race`, 시나리오 P7). 관리 콘솔 mock(`mocks/sim.ts` newRace·advance)과 같은 규칙.

- F1 방식: 다시 출발하고 0.7 초 뒤부터 빨간 불이 1 초마다 하나씩 켜진다(최대 5개). 다섯 개가 모두 켜진 뒤
  0.2 ~ 3.0 초 사이 무작위 순간에 모두 꺼지면 `go`.
- 불이 켜져 있는 동안(lights_on ≥ 1) 수동 앱이 전진을 명령하면 부정 출발 → 첫 바퀴 랩 시간 +5 초.
- 출발 신호 중에는 수동이 아닌 차(자율·모름)를 출발선에 잡아 둔다 (mock 의 자율 앱이 불이 꺼질 때까지 기다리는 것과 같음.
  실제 자율 앱은 출발 신호를 보지 않으므로 시뮬레이터가 대신 잡는다). 수동은 잡지 않는다 — 부정 출발을 할 수 있다.
- 랩: 트랙 반(중심선 점 40 ~ 60 %)을 지난 뒤 출발선(중심선 점 기준 앞뒤 25점)을 넘으면 한 바퀴.

시각은 모두 시뮬레이터 시계(초). 시뮬레이터 시간 배율이 1 이라 mock 의 화면 시계·시뮬레이터 시간 구분이 없다.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any

LIGHTS = 5
LIGHTS_DELAY_S = 0.7      # 다시 출발 → 첫 불
LIGHT_STEP_S = 1.0        # 불 하나씩
OUT_MIN_S = 0.2           # 다섯 개 다 켜진 뒤 꺼지기까지 최소
OUT_SPREAD_S = 2.8        # + 무작위 0 ~ 2.8 초
JUMP_PENALTY_S = 5.0      # 부정 출발: 첫 바퀴 +5 초
LAP_WINDOW = 25           # 출발선 앞뒤 이 점 수 안에서 넘었는지 본다


class Race:
    def __init__(self, clock: Callable[[], float], *, rng: Callable[[], float] = random.random,
                 lights: bool = True) -> None:
        self._clock = clock
        self._rng = rng
        self._lights = lights
        self.restart()

    def restart(self) -> None:
        """다시 출발: 출발 신호를 처음부터. lights=False 면 바로 go (랩 시간만 잰다)."""
        now = self._clock()
        self.phase = "lights" if self._lights else "go"
        self.t0 = now + LIGHTS_DELAY_S
        self.out_at = self.t0 + (LIGHTS - 1) * LIGHT_STEP_S + OUT_MIN_S + self._rng() * OUT_SPREAD_S
        self.lights_on = 0
        self.lap = 0
        self.lap_start = now
        self.last: float | None = None
        self.best: float | None = None
        self.half = False
        self.jump = False
        self.prog: int | None = None

    def holds(self, manual: bool) -> bool:
        """이번 주기에 전진을 막아야 하나 (출발 신호 중, 수동이 아니면)."""
        return self.phase == "lights" and not manual

    def update(self, index: int, start_index: int, n_points: int, *, manual: bool, forward: bool) -> None:
        """물리 한 주기 뒤. index = 지금 가까운 중심선 점, forward = 활성 앱이 전진을 명령하는가."""
        now = self._clock()
        if self.phase == "lights":
            self.lights_on = 0 if now < self.t0 else min(LIGHTS, math.floor((now - self.t0) / LIGHT_STEP_S) + 1)
            if manual and forward and self.lights_on > 0:
                self.jump = True
            if now >= self.out_at:
                self.phase = "go"
                self.lights_on = 0
                self.lap_start = now
                self.half = False
        prog = (index - start_index) % n_points
        if n_points * 0.4 < prog < n_points * 0.6:
            self.half = True
        if (self.phase == "go" and self.half and self.prog is not None
                and self.prog > n_points - LAP_WINDOW and prog < LAP_WINDOW):
            lap_s = now - self.lap_start + (JUMP_PENALTY_S if self.lap == 0 and self.jump else 0.0)
            self.last = lap_s
            self.best = lap_s if self.best is None else min(self.best, lap_s)
            self.lap += 1
            self.lap_start = now
            self.half = False
        self.prog = prog

    def payload(self) -> dict[str, Any]:
        """sim.pose.race (계약 §12, 관리 콘솔 mock snapshotOf 와 같은 모양)."""
        go = self.phase == "go"
        return {
            "phase": self.phase,
            "lights_on": 0 if go else self.lights_on,
            "lap": self.lap,
            "lap_ms": _ms(self._clock() - self.lap_start) if go else 0,
            "last_lap_ms": None if self.last is None else _ms(self.last),
            "best_lap_ms": None if self.best is None else _ms(self.best),
            "jump_start": self.jump,
        }


def _ms(seconds: float) -> int:
    return math.floor(seconds * 1000 + 0.5)  # JS Math.round 과 같은 반올림
