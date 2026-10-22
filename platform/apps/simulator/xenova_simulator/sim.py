"""가상 RC카 본체: 게이트웨이가 시리얼로 넘겼을 명령만 골라 차체에 적용하고 vehicle.status 를 낸다.

계약 v1.1 을 차량 쪽에서 본 규칙:
- vehicle/control 이 없으면 움직이지 않는다 (ESP32 세션 전).
- active_app 이 없으면(SAFE_STOP) 정지한다. 게이트웨이가 정지 프레임을 보내는 것과 같아서 와치독은 아니다.
- active_app 토픽의 drive.command 이고 generation 이 현재 세대일 때만 받는다 (5.5). seq 는 클라이언트가 거른다.
- 받은 명령이 300 ms 없으면 정지하고 flags.watchdog 를 올린다 (5.1).
- 앱 토픽에는 app.heartbeat 도 섞여 오므로 latest() 대신 핸들러로 drive.command 만 보관한다.

계약 11장 시뮬레이터 전용 (sim_client 가 있을 때, 계정 simulator):
- sim.pose 20 Hz, sim.world 는 시작할 때와 트랙·장애물·E-stop 이 바뀔 때 (retained, revision +1).
- sim.command(장애물 놓기·옮기기·치우기, E-stop, 차 밀기)를 받아 적용한다. 응답 토픽은 없다.
- 차는 타원 트랙 위 (a, 0) 에서 반시계 방향(+y)을 보고 시작한다.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from xenova_mqtt import topics

from .config import MAX_OBSTACLES, PUSH_MAX_M, SimSettings
from .model import Obstacle, Vehicle

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
    def __init__(
        self,
        client: Any,
        settings: SimSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
        sim_client: Any | None = None,
    ) -> None:
        self._client = client
        self._sim_client = sim_client
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._world_lock = threading.Lock()
        self._commands: dict[str, Command] = {}
        self.vehicle = Vehicle(settings)
        self._estop = False
        self.watchdog = False
        self.applied_generation = 0
        self._started = clock()
        self._next_status: float | None = None
        self._next_pose: float | None = None
        self._last_mode: tuple[Any, Any, Any] | None = None
        self.published = 0
        self.pose_published = 0
        self.world_revision = 1
        self._world_dirty = True
        self._auto_id = 0
        self._pending_push = 0.0
        self.reset_to_start()

    # --- 트랙·월드 (계약 11장) ---

    @property
    def estop(self) -> bool:
        return self._estop

    @estop.setter
    def estop(self, engaged: bool) -> None:
        if engaged != self._estop:
            self._estop = engaged
            self._world_changed()

    def _world_changed(self) -> None:
        with self._world_lock:
            self.world_revision += 1
            self._world_dirty = True

    def reset_to_start(self) -> None:
        """타원 트랙의 오른쪽 끝 (a, 0) 에서 반시계 방향(+y)을 보고 선다."""
        v = self.vehicle
        v.reset()
        v.x, v.heading_rad = self._settings.track_a_m, math.pi / 2

    def in_bounds(self, x: float, y: float) -> bool:
        """트랙 바깥 1 m 까지만 장애물을 받는다."""
        s = self._settings
        return abs(x) <= s.track_a_m + 1.0 and abs(y) <= s.track_b_m + 1.0

    def add_obstacle_ahead(self, distance_m: float) -> Obstacle:
        """터미널 'o': 진행 방향 앞에 장애물. id 는 t1, t2, ..."""
        self._auto_id += 1
        obstacle = self.vehicle.place_obstacle_ahead(distance_m, f"t{self._auto_id}")
        self._world_changed()
        return obstacle

    def clear_obstacles(self) -> None:
        if self.vehicle.obstacles:
            self.vehicle.obstacles = []
            self._world_changed()

    def apply(self, payload: dict[str, Any]) -> str | None:
        """sim.command 하나를 적용한다. 버리면 이유, 적용(또는 이미 같은 상태)이면 None."""
        op = payload.get("op")
        v = self.vehicle
        obstacles = {o.id: o for o in v.obstacles}
        oid = payload.get("obstacle_id")
        if op in ("obstacle.add", "obstacle.move", "obstacle.remove"):
            if not isinstance(oid, str) or not oid or len(oid) > 64:
                return "obstacle_id"
        if op in ("obstacle.add", "obstacle.move"):
            x, y = _number(payload, "x"), _number(payload, "y")
            if x is None or y is None or not self.in_bounds(x, y):
                return "out_of_bounds"
        if op == "obstacle.add":
            if oid in obstacles:
                return None  # 같은 id 는 다시 놓지 않는다 (QoS 1 중복 전달)
            w = _number(payload, "w")
            w = self._settings.obstacle_width_m if w is None and "w" not in payload else w
            if w is None or not 0 < w <= 1.0:
                return "width"
            if len(obstacles) >= MAX_OBSTACLES:
                return "too_many"
            v.obstacles = [*v.obstacles, Obstacle(x, y, w, oid)]
        elif op == "obstacle.move":
            if oid not in obstacles:
                return "not_found"
            v.obstacles = [Obstacle(x, y, o.w, o.id) if o.id == oid else o for o in v.obstacles]
        elif op == "obstacle.remove":
            if oid not in obstacles:
                return "not_found"
            v.obstacles = [o for o in v.obstacles if o.id != oid]
        elif op == "estop":
            engaged = payload.get("engaged")
            if not isinstance(engaged, bool):
                return "engaged"
            self.estop = engaged
            return None  # setter 가 바뀐 경우에만 revision 을 올린다
        elif op == "push":
            dy = _number(payload, "dy_m")
            if dy is None or abs(dy) > PUSH_MAX_M:
                return "dy_m"
            with self._world_lock:
                self._pending_push += dy  # 물리 스레드가 다음 tick 에 옮긴다 (위치를 두 스레드가 동시에 쓰지 않게)
            return None  # 위치만 바뀐다 → sim.pose 에만 보인다
        else:
            return "op"
        self._world_changed()
        return None

    def on_sim_command(self, message: Any) -> None:
        if message.type != "sim.command":
            return
        payload = message.payload if isinstance(message.payload, dict) else {}
        reason = self.apply(payload)
        if reason:
            log.warning("sim.command 버림 (%s): %s", reason, payload)
        else:
            log.info("sim.command %s %s → revision %d", payload.get("op"), payload.get("obstacle_id") or "", self.world_revision)

    def world(self) -> dict[str, Any]:
        s = self._settings
        return {
            "revision": self.world_revision,
            "track": {"shape": "ellipse", "a_m": s.track_a_m, "b_m": s.track_b_m, "lane_width_m": s.lane_width_m},
            "obstacles": [{"id": o.id, "x": round(o.x, 4), "y": round(o.y, 4), "w": round(o.w, 4)} for o in self.vehicle.obstacles],
            "estop": self._estop,
        }

    def pose(self) -> dict[str, Any]:
        v = self.vehicle
        return {
            "x": round(v.x, 4),
            "y": round(v.y, 4),
            "yaw": round(v.heading_rad, 4),
            "v_mps": round(v.v_mps, 4),
            "steer_rad": round(v.steer_rad, 4),
            "world_revision": self.world_revision,
        }

    # --- 수신 ---

    def subscribe(self) -> None:
        self._client.subscribe(topics.vehicle_control(), qos=1)
        for app_id in DRIVE_APPS:
            self._client.subscribe(topics.app_events(app_id), self.on_app_event, qos=0)
        if self._sim_client is not None:
            self._sim_client.subscribe(topics.sim_command(), self.on_sim_command, qos=1)

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
        with self._world_lock:
            push, self._pending_push = self._pending_push, 0.0
        if push:
            self.vehicle.push(push)
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

    def publish_sim(self) -> None:
        """sim.world(바뀌었으면, retained)와 sim.pose(20 Hz 마감 시각 누적). sim_client 가 없으면 아무것도 안 한다."""
        if self._sim_client is None:
            return
        with self._world_lock:
            dirty, self._world_dirty = self._world_dirty, False
        if dirty:
            self._sim_client.publish(topics.sim_world(), "sim.world", self.world())
        now = self._clock()
        period = 1.0 / self._settings.pose_rate_hz
        if self._next_pose is None or now - self._next_pose > period:
            self._next_pose = now
        if now < self._next_pose - 1e-9:
            return
        self._next_pose += period
        self._sim_client.publish(topics.sim_pose(), "sim.pose", self.pose())
        self.pose_published += 1

    def run(self, stop: threading.Event) -> None:
        """고정 주기로 tick 한다 (수동 앱과 같은 마감 시각 누적 방식)."""
        period = 1.0 / self._settings.physics_rate_hz
        deadline = self._clock()
        while not stop.is_set():
            self.tick(period)
            self.publish_sim()
            deadline += period
            delay = deadline - self._clock()
            if delay < -period:
                deadline = self._clock()
                delay = 0.0
            if delay > 0:
                time.sleep(delay)
