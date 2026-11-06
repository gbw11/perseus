"""시뮬레이터 전용 토픽 (MQTT 계약 §12, S15P31E110-261). 관람 화면(앱 3번 칸·관리 콘솔)과 콘솔 조작용이다.

- 발행 `xenova/v1/sim/pose` (sim.pose, 20 Hz, QoS 0): 차 뒷바퀴 축 위치·방향·속도·조향·world_revision·노면·출발 신호와 랩(race)
- 발행 `xenova/v1/sim/world` (sim.world, 바뀔 때, QoS 1 retained): 트랙·트랙 목록·차 크기·장애물·E-stop. 정상 종료 시 retained 삭제
- 구독 `xenova/v1/sim/command` (sim.command, QoS 1): 백엔드(콘솔 REST-31~37)가 보내는 조작
- 발행 `xenova/v1/sim/result` (sim.command.result, QoS 1): 명령마다 한 번, 적용 여부·거부 사유 (배치 검사는 placement.py)

젯슨(인지)은 이 토픽을 받지 않는다 — 브로커 ACL 에서 perception 계정은 sim/* 를 읽을 수 없다. 젯슨은 카메라 화면만 본다.
계정은 simulator (vehicle.status 를 내는 gateway 계정과 따로, 계약 9장).

world 는 터미널 명령(o, c, e, t)으로 바뀌어도 다시 낸다: 매 주기 트랙·장애물·E-stop 을 이전과 비교해 바뀌었으면 revision +1.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any

from xenova_mqtt import topics

from . import placement
from .model import KIND_W, Obstacle
from .track import load as load_track
from .track import sources as track_sources

log = logging.getLogger("xenova_simulator")

SOURCE_ID = "simulator"
USERNAME = "simulator"
POSE_RATE_HZ = 20.0
MAX_OBSTACLES = 8
PUSH_MAX_M = 0.5
CAR_LENGTH_M = placement.CAR_L          # 계약 §12 sim.world.car 기본값 (관리 콘솔 mock CAR_L)
CAR_WIDTH_M = 2 * placement.CAR_HALF


def _number(payload: dict[str, Any], name: str) -> float | None:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class SimTopics:
    def __init__(self, sim: Any, client: Any, *, clock: Callable[[], float] = time.monotonic,
                 pose_rate_hz: float = POSE_RATE_HZ) -> None:
        self._sim = sim
        self._client = client
        self._clock = clock
        self._period = 1.0 / pose_rate_hz
        self._lock = threading.Lock()
        self.revision = 0
        self._last_world: tuple | None = None
        self._next_pose: float | None = None
        self.pose_published = 0
        self.world_published = 0
        self.results = 0

    # --- 수신 ---

    def subscribe(self) -> None:
        self._client.subscribe(topics.sim_command(), self.on_command, qos=1)

    def on_command(self, message: Any) -> None:
        if message.type != "sim.command":
            return
        payload = message.payload if isinstance(message.payload, dict) else {}
        result = self.apply(payload)
        level = logging.INFO if result["ok"] else logging.WARNING
        log.log(level, "sim.command %s %s → %s", result["op"], payload.get("obstacle_id") or "",
                "적용" if result["ok"] else f"{result['code']} {result['message']}")
        self._client.publish(topics.sim_result(), "sim.command.result", result, qos=1)
        self.results += 1

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        """명령 하나를 적용하고 sim.command.result payload 를 돌려준다."""
        op = payload.get("op") if isinstance(payload.get("op"), str) else ""
        request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else ""
        with self._lock:
            outcome = self._apply(op, payload)
        if isinstance(outcome, placement.Check):
            code, message, gap, need = outcome.code, outcome.message, outcome.gap_m, outcome.need_m
        else:
            (code, message), gap, need = outcome, None, None
        return {"request_id": request_id, "op": op, "ok": code is None, "code": code, "message": message,
                "gap_m": gap, "need_m": need}

    def _apply(self, op: str, p: dict[str, Any]) -> tuple[str | None, str | None] | placement.Check:
        sim = self._sim
        car = sim.vehicle
        if op in ("obstacle.add", "obstacle.move", "obstacle.remove"):
            oid = p.get("obstacle_id")
            if not isinstance(oid, str) or not 0 < len(oid) <= 64:
                return "INVALID", "장애물 id 가 없어요"
            existing = {o.id: o for o in car.obstacles}
            if op == "obstacle.remove":
                if oid not in existing:
                    return "INVALID", "그 장애물은 이미 없어요"
                car.obstacles = [o for o in car.obstacles if o.id != oid]
                return None, None
            if op == "obstacle.add" and oid in existing:
                return None, None  # QoS 1 중복 전달: 같은 id 는 다시 놓지 않는다
            if op == "obstacle.move" and oid not in existing:
                return "INVALID", "그 장애물은 없어요"
            x, y = _number(p, "x"), _number(p, "y")
            if x is None or y is None:
                return "INVALID", "위치 x·y 가 숫자가 아니에요"
            if op == "obstacle.add":
                kind = p.get("kind", "cone")
                if kind not in KIND_W:
                    return "INVALID", f"모르는 장애물 종류 {kind!r}"
                if len(existing) >= MAX_OBSTACLES:
                    return "INVALID", f"장애물은 {MAX_OBSTACLES}개까지 놓을 수 있어요"
                obstacle = Obstacle.of(kind, x, y, oid)
            else:
                old = existing[oid]
                obstacle = Obstacle.of(old.kind, x, y, oid)
            reason = self._placement(obstacle, [o for o in car.obstacles if o.id != oid])
            if reason:
                return reason
            if op == "obstacle.add":
                car.obstacles = [*car.obstacles, obstacle]
            else:
                car.obstacles = [obstacle if o.id == oid else o for o in car.obstacles]
            return None, None
        if op == "estop":
            engaged = p.get("engaged")
            if not isinstance(engaged, bool):
                return "INVALID", "engaged 는 true/false 예요"
            sim.estop = engaged
            return None, None
        if op == "push":
            dy = _number(p, "dy_m")
            if dy is None or abs(dy) > PUSH_MAX_M:
                return "INVALID", f"차는 옆으로 {PUSH_MAX_M} m 까지만 밀 수 있어요"
            sim.push(dy)
            return None, None
        if op == "track.select":
            track_id = p.get("track_id")
            if track_id not in track_sources():
                return "INVALID", f"모르는 트랙 {track_id!r}"
            sim.select_track(track_id)
            return None, None
        if op == "race.start":
            sim.reset_to_start()
            return None, None
        return "INVALID", f"모르는 명령 {op!r}"

    def _placement(self, o: Obstacle, others: list[Obstacle]) -> placement.Check | None:
        """놓을 수 없는 자리면 거부 사유 (OUT_OF_TRACK·ON_CAR·NO_GAP·TOO_DENSE·TOO_CLOSE, placement.py).
        others = 이 장애물을 뺀 지금 장애물. 옮기기면 옛 자리는 빼고 새 자리로 본다 (mock moveObstacle 과 같음)."""
        sim = self._sim
        track, v = sim.track, sim.vehicle
        item = placement.Item.at(track, o.id, o.kind, o.x, o.y)
        items = [placement.Item.at(track, q.id, q.kind, q.x, q.y) for q in others] + [item]
        car = placement.Car(v.x, v.y, v.heading_rad, v.v_mps, v.steer_rad,
                            sim.track_index if sim.track_index is not None else track.nearest(v.x, v.y))
        return placement.check(track, items, item, car, sim.race.phase == "go")

    # --- 발행 ---

    def world(self) -> dict[str, Any]:
        sim = self._sim
        return {
            "revision": self.revision,
            "track": sim.track.payload(),
            # 목록에는 이름·길이만 필요하다. Track() 을 10개 다 만들면(벽 계산) 첫 발행 때 물리 스레드가 약 0.8 s 멈춘다
            "tracks": [{"id": t.id, "name": t.name, "real_m": t.real_m} for t in track_sources().values()],
            "car": {"length_m": CAR_LENGTH_M, "width_m": CAR_WIDTH_M},
            "time_scale": 1.0,
            "obstacles": [{"id": o.id, "kind": o.kind, "x": round(o.x, 4), "y": round(o.y, 4), "w": round(o.w, 4)}
                          for o in sim.vehicle.obstacles],
            "estop": bool(sim.estop),
        }

    def pose(self) -> dict[str, Any]:
        sim = self._sim
        v = sim.vehicle
        i, off, _ = sim.track_frame()
        return {
            "x": round(v.x, 4), "y": round(v.y, 4), "yaw": round(v.heading_rad, 4),
            "v_mps": round(v.v_mps, 4), "steer_rad": round(v.steer_rad, 4),
            "world_revision": self.revision, "surface": sim.track.surface(i, off),
            "race": sim.race.payload(),
        }

    def publish(self) -> None:
        """주기마다 부른다: world 가 바뀌었으면 retained 로, pose 는 20 Hz 마감 시각 누적."""
        sim = self._sim
        key = (sim.track.id, tuple(sim.vehicle.obstacles), bool(sim.estop))
        if key != self._last_world:
            with self._lock:
                self._last_world = key
                self.revision += 1
            self._client.publish(topics.sim_world(), "sim.world", self.world(), qos=1, retain=True)
            self.world_published += 1
        now = self._clock()
        if self._next_pose is None or now - self._next_pose > self._period:
            self._next_pose = now
        if now < self._next_pose - 1e-9:
            return
        self._next_pose += self._period
        self._client.publish(topics.sim_pose(), "sim.pose", self.pose(), qos=0)
        self.pose_published += 1

    def clear(self) -> None:
        """정상 종료: 꺼진 시뮬레이터의 트랙·장애물이 retained 로 남지 않게 지운다 (다음 실행은 revision 1 부터)."""
        try:
            self._client.publish_raw(topics.sim_world(), b"", qos=1, retain=True, wait_s=1.0)
        except Exception as exc:  # 브로커가 없으면 지울 것도 없다
            log.warning("sim.world retained 삭제 실패: %s", exc)
