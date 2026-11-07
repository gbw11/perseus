"""브로커·보드 없이 한 프로세스에서 도는 폐루프 리허설 (중간점검 전 확인용).

    전방 카메라 렌더 → 인지 대역 Detector (perception.result payload)
    → 자율 앱 계산 (Zephyr autonomous.c 의 auto_target 을 그대로 옮긴 Python 대역, 50 Hz)
    → 게이트웨이 시리얼 프레임 인코딩 (xenova_gateway.frame) → 가상 ESP32 FrameReceiver → 바퀴·서보·차체 모델

실제 시연 경로(MQTT·Zephyr·게이트웨이 상태 기계)는 거치지 않는다. 이 도구가 보여주는 것은
"카메라 화면만으로 인지·자율 계산이 트랙을 돌 수 있는가"이며, 제품 경로의 통과가 아니다.

    cd platform/apps/simulator
    PYTHONPATH=.:../../services/vm-agent/mqtt-client:../../services/gateway:../perception \\
        python tools/closed_loop.py --track spielberg --laps 1
        python tools/closed_loop.py --seconds 15 --obstacle 2.0
        python tools/closed_loop.py --all-tracks --laps 1        # 10개 트랙 모두 한 바퀴
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from typing import Any

from xenova_gateway.frame import FrameEncoder, encode
from xenova_perception.config import PerceptionSettings
from xenova_perception.detect import Detector
from xenova_perception.geometry import payload as perception_payload

from xenova_simulator.camera import Renderer
from xenova_simulator.config import SimSettings
from xenova_simulator.frames import FrameReceiver
from xenova_simulator.model import Obstacle, Vehicle
from xenova_simulator.placement import CAR_HALF
from xenova_simulator.track import load as load_track
from xenova_simulator.track import track_ids


MAX_HEADING_RAD = 1.4   # autonomous.c AUTO_MAX_HEADING


@dataclass(frozen=True)
class AutoCfg:
    """autonomous.c auto_cfg_default 와 같은 값."""

    cruise_mps: float = 0.5
    max_steer_rad: float = 0.4
    wheelbase_m: float = 0.26
    camera_ahead_m: float = 0.20     # 후륜 축 → 카메라 (인지 좌표 원점)
    lookahead_m: float = 0.6         # 인지 대역 LANE_LOOKAHEAD_M (heading_err 를 재는 거리)
    k_offset: float = 1.0            # pure pursuit 에 더하는 차로 중심 복귀 (rad/m)
    corridor_half_m: float = 0.15
    slow_m: float = 1.6
    stop_m: float = 0.8
    turn_slowdown: float = 0.4
    perception_timeout_ms: float = 300.0


def auto_target(cfg: AutoCfg, p: dict[str, Any]) -> tuple[float, float, str]:
    """autonomous.c auto_target 의 Python 대역 (같은 식, 같은 순서). (v, steer, reason)."""
    nearest = -1.0
    for o in p["obstacles"]:
        if o["x"] > 0 and abs(o["y"]) <= cfg.corridor_half_m + o["w"] / 2 and (nearest < 0 or o["x"] < nearest):
            nearest = o["x"]
    # pure pursuit: 카메라 lookahead 앞 중심선 점 (인지 정의를 거꾸로) 을 후륜 축에서 쫓는 원호
    h = max(-MAX_HEADING_RAD, min(MAX_HEADING_RAD, p["heading_err_rad"]))
    y = -p["lane_offset_m"] + cfg.lookahead_m * math.tan(h)
    x = cfg.camera_ahead_m + cfg.lookahead_m
    steer = math.atan(cfg.wheelbase_m * 2 * y / (x * x + y * y)) - cfg.k_offset * p["lane_offset_m"]
    steer = max(-cfg.max_steer_rad, min(cfg.max_steer_rad, steer))
    if p["stop"] or (0 <= nearest <= cfg.stop_m):
        return 0.0, steer, "obstacle_stop"
    v = cfg.cruise_mps * (1 - cfg.turn_slowdown * abs(steer) / cfg.max_steer_rad)
    if 0 <= nearest < cfg.slow_m:
        return v * (nearest - cfg.stop_m) / (cfg.slow_m - cfg.stop_m), steer, "slowing"
    return v, steer, "cruise"


@dataclass
class LoopResult:
    track_id: str
    seconds: float
    distance_m: float
    laps: float
    max_abs_offset_m: float               # 후륜 축 중심
    out_of_lane_ticks: int
    max_body_offset_m: float              # 차체 가장자리 (전·후륜 축 중심 이탈 + 차 반폭)
    body_out_ticks: int                   # 차체 가장자리가 차로 선(±반폭) 밖
    stopped_gap_m: float | None           # 장애물 앞에 섰을 때 앞범퍼(앞바퀴 축)~장애물 앞면 거리
    reasons: dict[str, int] = field(default_factory=dict)


def run(seconds: float, *, obstacle_ahead_m: float | None = None, settings: SimSettings | None = None,
        generation: int = 1, laps_target: float | None = None) -> LoopResult:
    """seconds 동안(또는 laps_target 바퀴를 채울 때까지) 돈다."""
    s = settings or SimSettings(camera_listen=None)
    track = load_track(s.track_id)
    if track.layout:
        raise ValueError("이 단일 차로 폐루프 도구는 Jetson 두 차로·주차 코스를 검증하지 않습니다")
    cfg = AutoCfg()
    renderer = Renderer(s.camera)
    detector = Detector(PerceptionSettings(lane_width_m=track.lane_width_m))
    car = Vehicle(s)
    car.reset(*track.start_pose())
    obstacles: list[Obstacle] = []
    if obstacle_ahead_m is not None:
        obstacles = [car.place_obstacle_ahead(obstacle_ahead_m, "t1")]
    receiver, encoder = FrameReceiver(), FrameEncoder()

    dt, t = 0.01, 0.0
    cam_period, ctrl_period = 1.0 / s.camera.fps, 0.02
    next_cam = next_ctrl = 0.0
    latest: tuple[float, dict[str, Any]] | None = None   # (만든 시각, perception payload)
    pending: tuple[float, dict[str, Any]] | None = None  # 처리 지연 한 프레임 (카메라 → 인지 결과 발행)
    half = track.lane_width_m / 2
    max_off, out_ticks, distance = 0.0, 0, 0.0
    max_body, body_out = 0.0, 0
    front_hint = hint = track.start_index
    reasons: dict[str, int] = {}
    stopped_gap = None
    travelled = 0.0          # 중심선을 따라 앞으로 간 거리 (바퀴 수 = travelled / 한 바퀴)
    last_s = track.SL[hint]
    while t < seconds and (laps_target is None or travelled < laps_target * track.lap_m):
        if t >= next_cam:
            next_cam += cam_period
            if pending is not None:
                latest = pending
            result = detector.detect(renderer.render(track, car.x, car.y, car.heading_rad, obstacles), t)
            pending = (t, perception_payload(result))
        if t >= next_ctrl:
            next_ctrl += ctrl_period
            if latest is not None and (t - latest[0]) * 1000 <= cfg.perception_timeout_ms:
                v, steer, reason = auto_target(cfg, latest[1])
            else:
                v, steer, reason = 0.0, 0.0, "no_perception"
            reasons[reason] = reasons.get(reason, 0) + 1
            receiver.feed(encode(encoder.drive(generation, v, steer)), t)
        cmd = receiver.latest()
        fresh = cmd is not None and (t - cmd.received) * 1000 <= s.watchdog_ms
        before = (car.x, car.y)
        car.step(cmd.v_mps if fresh else 0.0, cmd.steer_rad if fresh else car.steer_rad, dt)
        distance += math.hypot(car.x - before[0], car.y - before[1])
        hint, off, s_now = track.frame(car.x, car.y, hint)
        max_off = max(max_off, abs(off))
        out_ticks += abs(off) > half
        fx = car.x + s.wheelbase_m * math.cos(car.heading_rad)
        fy = car.y + s.wheelbase_m * math.sin(car.heading_rad)
        front_hint, front_off, _ = track.frame(fx, fy, front_hint)
        body = max(abs(off), abs(front_off)) + CAR_HALF
        max_body = max(max_body, body)
        body_out += body > half
        step = (s_now - last_s + track.lap_m / 2) % track.lap_m - track.lap_m / 2
        travelled += step
        last_s = s_now
        if obstacles and car.v_mps == 0.0 and t > 1.0 and stopped_gap is None:
            o = obstacles[0]
            fx = car.x + s.wheelbase_m * math.cos(car.heading_rad)
            fy = car.y + s.wheelbase_m * math.sin(car.heading_rad)
            stopped_gap = math.hypot(o.x - fx, o.y - fy) - o.w / 2
        t += dt
    return LoopResult(track.id, t, distance, travelled / track.lap_m, max_off, out_ticks, max_body, body_out,
                      stopped_gap, reasons)


def supported_track_ids() -> list[str]:
    """기존 단일 차로 인지·제어 profile을 사용하는 회귀 코스만 반환한다."""
    return [track_id for track_id in track_ids() if load_track(track_id).layout is None]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="카메라 → 인지 → 자율 계산 → 시리얼 프레임 → 바퀴 폐루프 리허설")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--obstacle", type=float, help="출발선에서 이만큼 앞(m)에 장애물")
    parser.add_argument("--track", default=SimSettings.track_id, choices=supported_track_ids())
    parser.add_argument("--all-tracks", action="store_true", help="기존 단일 차로 서킷 10개 회귀")
    parser.add_argument("--laps", type=float, help="이만큼 돌면 멈춘다 (--seconds 는 상한)")
    a = parser.parse_args(argv)
    seconds = a.seconds if a.laps is None else max(a.seconds, 3600.0)
    failed = 0
    for track_id in (supported_track_ids() if a.all_tracks else [a.track]):
        r = run(seconds, obstacle_ahead_m=a.obstacle, laps_target=a.laps,
                settings=SimSettings(camera_listen=None, track_id=track_id))
        failed += r.out_of_lane_ticks > 0 or r.body_out_ticks > 0
        print(f"{r.track_id:12s} {r.seconds:5.0f}s 주행 {r.distance_m:6.2f} m, {r.laps:.2f} 바퀴, "
              f"차로 중심 최대 이탈 {r.max_abs_offset_m:.3f} m, 차로 밖 {r.out_of_lane_ticks} tick, "
              f"차체 최대 {r.max_body_offset_m:.3f} m·선 밖 {r.body_out_ticks} tick, 판정 {r.reasons}"
              + (f", 장애물 앞 정지 간격 {r.stopped_gap_m:.2f} m" if r.stopped_gap_m is not None else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
