"""가상 RC카 본체: ESP32 가 받았을 명령만 골라 바퀴·서보에 적용하고 vehicle.status 를 낸다.

명령 경로 (settings.input):
- frames: 게이트웨이가 ESP32 로 보내는 시리얼 프레임을 TCP 로 받는다 (frames.py). 실제 ESP32 와 같은 입력이다.
  정지 프레임이면 선다. 유효 프레임이 300 ms 없으면 정지하고 flags.watchdog 를 올린다 (계약 5.1).
- mqtt: 게이트웨이 없이 시험할 때. vehicle/control 의 활성 앱(공통 펌웨어면 활성 VM 까지)의 drive.command 를 직접 본다.
  · legacy 분리 앱: `xenova/v1/apps/{app}/events`
  · 공통 A/B(verified-drive-1): `xenova/v1/vms/{vm_id}/apps/{app}/events`, vehicle.control 의 active_vm_id 와 맞아야 한다
  · 같은 토픽의 app.heartbeat·app.readiness 등은 무시하고 drive.command 만 보관한다.
  · generation 이 현재 세대와 다르면 버린다 (5.5). seq 는 클라이언트가 거른다.
  게이트웨이의 출력 허가·재무장 판정은 이 경로에서 보이지 않는다 → 실제 출력과 맞추려면 frames 를 쓴다.

vehicle.status 의 v_mps 는 엔코더 count 변화로 잰 속도, steer_rad 는 서보가 만든 전륜각이다.
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

from .config import SimSettings
from .frames import FrameReceiver
from .model import Obstacle, Vehicle
from .track import Track
from .track import load as load_track

log = logging.getLogger("xenova_simulator")

DRIVE_APPS = ("autonomous", "manual")
VM_APP_EVENTS = "xenova/v1/vms/+/apps/+/events"


@dataclass(frozen=True)
class Command:
    generation: int
    v_mps: float
    steer_rad: float
    received: float


def _number(payload: dict[str, Any], name: str) -> float | None:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class Simulator:
    def __init__(
        self,
        client: Any,
        settings: SimSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
        frames: FrameReceiver | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._pose_lock = threading.Lock()
        # (vm_id, app_id) → 마지막 drive.command. legacy 토픽은 vm_id ""
        self._commands: dict[tuple[str, str], Command] = {}
        self.frames = frames if frames is not None else (FrameReceiver() if settings.input == "frames" else None)
        self.vehicle = Vehicle(settings)
        self.estop = False
        self.watchdog = False
        self.applied_generation = 0
        self._started = clock()
        self._next_status: float | None = None
        self._last_mode: tuple[Any, ...] | None = None
        self._last_encoder: tuple[float, int] | None = None
        self.measured_v_mps = 0.0
        self.published = 0
        self._auto_id = 0
        self.track = load_track(settings.track_id)
        self.track_index: int | None = None   # 가까운 중심선 점 (다음 찾기의 시작점)
        self.reset_to_start()

    # --- 트랙·장애물 ---

    def reset_to_start(self) -> None:
        """출발선 위에서 진행 방향을 보고 선다."""
        with self._pose_lock:
            self.vehicle.reset(*self.track.start_pose())
            self.track_index = self.track.start_index

    def select_track(self, track_id: str) -> None:
        """트랙 바꾸기: 장애물을 모두 치우고 출발선에 선다 (계약 v1.4 track.select 와 같은 동작)."""
        track = load_track(track_id)
        with self._pose_lock:
            self.track = track
            self.vehicle.obstacles = []
        self.reset_to_start()

    def add_obstacle_ahead(self, distance_m: float, kind: str = "cone") -> Obstacle:
        self._auto_id += 1
        return self.vehicle.place_obstacle_ahead(distance_m, f"t{self._auto_id}", kind)

    def clear_obstacles(self) -> None:
        self.vehicle.obstacles = []

    def push(self, dy_m: float) -> None:
        """차를 진행 방향 기준 옆으로 옮긴다 (+ 왼쪽). 시연용 '차 밀기' (계약 §12 sim.command push)."""
        with self._pose_lock:
            v = self.vehicle
            v.x -= dy_m * math.sin(v.heading_rad)
            v.y += dy_m * math.cos(v.heading_rad)

    def track_frame(self) -> tuple[int, float, float]:
        """(가까운 중심선 점, 횡방향 오프셋 + 왼쪽, 출발선부터 진행 거리)."""
        v = self.vehicle
        i, off, _ = self.track.frame(v.x, v.y, self.track_index)
        self.track_index = i
        return i, off, self.track.progress(v.x, v.y, i)

    def camera_pose(self) -> tuple[Track, float, float, float, list[Obstacle]]:
        """카메라 스레드용: 같은 순간의 트랙·(x, y, heading)·장애물 목록."""
        with self._pose_lock:
            v = self.vehicle
            return self.track, v.x, v.y, v.heading_rad, v.obstacles

    # --- 수신 (mqtt 입력) ---

    def subscribe(self) -> None:
        if self.frames is not None:
            return  # frames 입력이면 명령·세대는 시리얼 프레임으로만 받는다 (ESP32 는 MQTT 에 붙지 않는다)
        self._client.subscribe(topics.vehicle_control(), qos=1)
        for app_id in DRIVE_APPS:
            self._client.subscribe(topics.app_events(app_id), self.on_app_event, qos=0)
        self._client.subscribe(VM_APP_EVENTS, self.on_app_event, qos=0)

    def on_app_event(self, message: Any) -> None:
        if message.type != "drive.command":
            return
        info = topics.parse(message.topic)
        if info.app_id not in DRIVE_APPS:
            return
        payload = message.payload
        generation = payload.get("generation")
        v, steer = _number(payload, "v_mps"), _number(payload, "steer_rad")
        if isinstance(generation, bool) or not isinstance(generation, int) or v is None or steer is None:
            log.debug("버림 %s: drive.command payload 형식", message.topic)
            return
        with self._lock:
            self._commands[(info.vm_id or "", info.app_id)] = Command(generation, v, steer, self._clock())

    def control(self) -> tuple[int, str | None, str | None, str | None] | None:
        """(generation, mode, active_app, active_vm_id). vehicle/control 이 없거나 형식이 틀리면 None."""
        message = self._client.latest(topics.vehicle_control())
        if message is None:
            return None
        generation = message.payload.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            return None
        p = message.payload
        control = (generation, p.get("mode"), p.get("active_app"), p.get("active_vm_id"))
        if control != self._last_mode:
            log.info("vehicle/control generation=%d mode=%s active_app=%s active_vm_id=%s", *control)
            self._last_mode = control
        return control

    def _active_command(self, active_app: str, active_vm_id: str | None) -> Command | None:
        with self._lock:
            if active_vm_id:
                command = self._commands.get((active_vm_id, active_app))
                if command is not None:
                    return command
            return self._commands.get(("", active_app))

    # --- 판정 ---

    def target(self) -> tuple[float, float]:
        """이번 주기에 ESP32 가 따를 목표 (v, steer). 와치독·estop 플래그도 여기서 정한다."""
        if self.frames is not None:
            return self._frame_target()
        now = self._clock()
        control = self.control()
        if self.estop or control is None:
            self.watchdog = False
            return 0.0, self.vehicle.steer_rad
        generation, _, active_app, active_vm_id = control
        if active_app not in DRIVE_APPS:
            self.watchdog = False  # SAFE_STOP: 게이트웨이가 정지 프레임을 보낸다
            return 0.0, self.vehicle.steer_rad
        command = self._active_command(active_app, active_vm_id)
        fresh = command is not None and command.generation == generation and (now - command.received) * 1000 <= self._settings.watchdog_ms
        if not fresh:
            self.watchdog = True
            return 0.0, self.vehicle.steer_rad
        self.watchdog = False
        self.applied_generation = generation
        return command.v_mps, command.steer_rad

    def _frame_target(self) -> tuple[float, float]:
        command = self.frames.latest()
        if command is None:  # 아직 세션 전: 움직이지 않는다
            self.watchdog = False
            return 0.0, self.vehicle.steer_rad
        if (self._clock() - command.received) * 1000 > self._settings.watchdog_ms:
            self.watchdog = True
            return 0.0, self.vehicle.steer_rad
        self.watchdog = False
        self.applied_generation = command.generation
        if self.estop or command.stop:
            return 0.0, self.vehicle.steer_rad
        return command.v_mps, command.steer_rad

    def battery_v(self) -> float:
        s = self._settings
        minutes = (self._clock() - self._started) / 60.0
        return round(max(s.batt_empty_v, s.batt_full_v - s.batt_drain_v_per_min * minutes), 2)

    def _measure_speed(self) -> None:
        """엔코더 count 변화로 속도를 잰다 (ESP32 가 하는 방식. count 양자화가 그대로 남는다)."""
        now, count = self._clock(), self.vehicle.encoder_count
        if self._last_encoder is not None:
            t0, c0 = self._last_encoder
            if now > t0:
                d = self._settings.drivetrain
                self.measured_v_mps = (count - c0) / d.encoder_cpr * 2 * math.pi * d.wheel_radius_m / (now - t0)
        self._last_encoder = (now, count)

    def status(self) -> dict[str, Any]:
        v = self.vehicle
        return {
            "applied_generation": self.applied_generation,
            "v_mps": round(self.measured_v_mps, 3),
            "steer_rad": round(v.steer_rad, 3),
            "flags": {"estop": self.estop, "watchdog": self.watchdog, "obstacle": v.obstacle_ahead()},
            "batt_v": self.battery_v(),
        }

    # --- 주기 ---

    def tick(self, dt: float) -> dict[str, Any] | None:
        """물리 한 단계. status 주기가 되면 vehicle.status 를 발행하고 payload 를 돌려준다."""
        target_v, target_steer = self.target()
        with self._pose_lock:
            self.vehicle.step(target_v, target_steer, dt)
        # 마감 시각을 누적한다. "지난 발행 후 50 ms 이상" 으로 재면 물리 주기(10 ms) 오차로 60 ms 간격이 섞여 17 Hz 가 된다
        now = self._clock()
        period = 1.0 / self._settings.status_rate_hz
        if self._next_status is None or now - self._next_status > period:
            self._next_status = now
        if now < self._next_status - 1e-9:
            return None
        self._next_status += period
        self._measure_speed()
        payload = self.status()
        self._client.publish(topics.vehicle_status(), "vehicle.status", payload, qos=0)
        self.published += 1
        return payload

    def run(self, stop: threading.Event, after_tick: Callable[[], None] | None = None) -> None:
        """고정 주기로 tick 한다 (수동 앱과 같은 마감 시각 누적 방식). after_tick 은 시뮬레이터 전용 토픽 발행."""
        period = 1.0 / self._settings.physics_rate_hz
        deadline = self._clock()
        while not stop.is_set():
            self.tick(period)
            if after_tick is not None:
                after_tick()
            deadline += period
            delay = deadline - self._clock()
            if delay < -period:
                deadline = self._clock()
                delay = 0.0
            if delay > 0:
                time.sleep(delay)
