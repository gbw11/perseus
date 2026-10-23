"""가상 RC카 본체: 게이트웨이가 시리얼로 넘겼을 명령만 골라 차체에 적용하고 vehicle.status 를 낸다.

계약 v1.1 을 차량 쪽에서 본 규칙:
- vehicle/control 이 없으면 움직이지 않는다 (ESP32 세션 전).
- active_app 이 없으면(SAFE_STOP) 정지한다. 게이트웨이가 정지 프레임을 보내는 것과 같아서 와치독은 아니다.
- active_app 토픽의 drive.command 이고 generation 이 현재 세대일 때만 받는다 (5.5). seq 는 클라이언트가 거른다.
- 받은 명령이 300 ms 없으면 정지하고 flags.watchdog 를 올린다 (5.1).
- 앱 토픽에는 app.heartbeat 도 섞여 오므로 latest() 대신 핸들러로 drive.command 만 보관한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from xenova_mqtt import topics

from .config import SimSettings
from .model import Vehicle

log = logging.getLogger("xenova_simulator")

DRIVE_APPS = ("autonomous", "manual")


@dataclass(frozen=True)
class Command:
    generation: int
    v_mps: float
    steer_rad: float
    received: float


def _number(payload: dict[str, Any], name: str) -> float | None:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class Simulator:
    def __init__(self, client: Any, settings: SimSettings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._commands: dict[str, Command] = {}
        self.vehicle = Vehicle(settings)
        self.estop = False
        self.watchdog = False
        self.applied_generation = 0
        self._started = clock()
        self._next_status: float | None = None
        self._last_mode: tuple[Any, Any, Any] | None = None
        self.published = 0

    # --- 수신 ---

    def subscribe(self) -> None:
        self._client.subscribe(topics.vehicle_control(), qos=1)
        for app_id in DRIVE_APPS:
            self._client.subscribe(topics.app_events(app_id), self.on_app_event, qos=0)

    def on_app_event(self, message: Any) -> None:
        if message.type != "drive.command":
            return
        app_id = topics.parse(message.topic).app_id
        payload = message.payload
        generation = payload.get("generation")
        v, steer = _number(payload, "v_mps"), _number(payload, "steer_rad")
        if isinstance(generation, bool) or not isinstance(generation, int) or v is None or steer is None:
            log.debug("버림 %s: drive.command payload 형식", message.topic)
            return
        with self._lock:
            self._commands[app_id] = Command(generation, v, steer, self._clock())

    def control(self) -> tuple[int, str | None, str | None] | None:
        """(generation, mode, active_app). vehicle/control 이 없거나 형식이 틀리면 None."""
        message = self._client.latest(topics.vehicle_control())
        if message is None:
            return None
        generation = message.payload.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            return None
        control = (generation, message.payload.get("mode"), message.payload.get("active_app"))
        if control != self._last_mode:
            log.info("vehicle/control generation=%d mode=%s active_app=%s", *control)
            self._last_mode = control
        return control

    # --- 판정 ---

    def target(self) -> tuple[float, float]:
        """이번 주기에 차체가 따를 목표 (v, steer). 와치독·estop 플래그도 여기서 정한다."""
        now = self._clock()
        control = self.control()
        if self.estop or control is None:
            self.watchdog = False
            return 0.0, self.vehicle.steer_rad
        generation, _, active_app = control
        if active_app not in DRIVE_APPS:
            self.watchdog = False  # SAFE_STOP: 게이트웨이가 정지 프레임을 보낸다
            return 0.0, self.vehicle.steer_rad
        with self._lock:
            command = self._commands.get(active_app)
        fresh = command is not None and command.generation == generation and (now - command.received) * 1000 <= self._settings.watchdog_ms
        if not fresh:
            self.watchdog = True
            return 0.0, self.vehicle.steer_rad
        self.watchdog = False
        self.applied_generation = generation
        return command.v_mps, command.steer_rad

    def battery_v(self) -> float:
        s = self._settings
        minutes = (self._clock() - self._started) / 60.0
        return round(max(s.batt_empty_v, s.batt_full_v - s.batt_drain_v_per_min * minutes), 2)

    def status(self) -> dict[str, Any]:
        v = self.vehicle
        return {
            "applied_generation": self.applied_generation,
            "v_mps": round(v.v_mps, 3),
            "steer_rad": round(v.steer_rad, 3),
            "flags": {"estop": self.estop, "watchdog": self.watchdog, "obstacle": v.obstacle_ahead()},
            "batt_v": self.battery_v(),
        }

    # --- 주기 ---

    def tick(self, dt: float) -> dict[str, Any] | None:
        """물리 한 단계. status 주기가 되면 vehicle.status 를 발행하고 payload 를 돌려준다."""
        target_v, target_steer = self.target()
        self.vehicle.step(target_v, target_steer, dt)
        # 마감 시각을 누적한다. "지난 발행 후 50 ms 이상" 으로 재면 물리 주기(10 ms) 오차로 60 ms 간격이 섞여 17 Hz 가 된다
        now = self._clock()
        period = 1.0 / self._settings.status_rate_hz
        if self._next_status is None or now - self._next_status > period:
            self._next_status = now
        if now < self._next_status - 1e-9:
            return None
        self._next_status += period
        payload = self.status()
        self._client.publish(topics.vehicle_status(), "vehicle.status", payload, qos=0)
        self.published += 1
        return payload

    def run(self, stop: threading.Event) -> None:
        """고정 주기로 tick 한다 (수동 앱과 같은 마감 시각 누적 방식)."""
        period = 1.0 / self._settings.physics_rate_hz
        deadline = self._clock()
        while not stop.is_set():
            self.tick(period)
            deadline += period
            delay = deadline - self._clock()
            if delay < -period:
                deadline = self._clock()
                delay = 0.0
            if delay > 0:
                time.sleep(delay)
