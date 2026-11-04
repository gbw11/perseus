"""차선 추정 정확도: 시뮬레이터 전방 카메라 화면에서 낸 lane_offset_m·heading_err_rad 를 트랙 정답과 비교한다 (S15P31E110-320).

    차 자세 (중심선을 따라 0.5 m/s · 15 fps 간격, 횡방향·방향을 조금씩 흔든다)
    → 시뮬레이터 Renderer → 인지 대역 Detector (트랙마다 하나, 프레임 순서대로) → 정답과 차이

정답은 README "좌표계·단위" 의 정의 그대로 중심선에서 잰다: 원점 = 카메라 바로 아래 지면, c(x) = 차 전방 x 에서 중심선의 가로 위치
(왼쪽 +), lane_offset_m = −c(0), heading_err_rad = atan((c(0.6) − c(0)) / 0.6).

    cd platform/apps/perception
    PYTHONPATH=.:../simulator python tools/lane_eval.py                  # 10개 트랙 한 바퀴씩
    PYTHONPATH=.:../simulator python tools/lane_eval.py --track monza --worst 10

시뮬레이터(xenova_simulator)와 OpenCV 가 필요하다. 폐루프(주행이 차로 안에 머무는지)는 simulator/tools/closed_loop.py 가 본다.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from xenova_perception.config import PerceptionSettings
from xenova_perception.detect import LANE_LOOKAHEAD_M, Detector

from xenova_simulator.camera import Renderer
from xenova_simulator.config import CameraSpec
from xenova_simulator.track import Track, load, track_ids

FRAME_STEP_M = 0.5 / 15   # 0.5 m/s 로 달릴 때 15 fps 한 프레임 사이 거리
WOBBLE_OFF_M = 0.08       # 차를 중심선에서 흔드는 폭 (폐루프 최대 이탈 0.12~0.14 m 보다 작게)
WOBBLE_HEADING_RAD = 0.08


@dataclass(frozen=True)
class Sample:
    track_id: str
    s: float                  # 출발선부터 중심선 거리
    turn: float               # 그 점의 곡률 (1/m, + 왼쪽)
    found: bool
    offset: float
    heading: float
    true_offset: float
    true_heading: float

    @property
    def offset_err(self) -> float:
        return self.offset - self.true_offset

    @property
    def heading_err(self) -> float:
        return self.heading - self.true_heading


def _lateral_at(track: Track, cam_x: float, cam_y: float, heading: float, i0: int, x: float) -> float | None:
    """차 전방 x 에서 중심선의 가로 위치 (카메라 기준, 왼쪽 +). i0 = 카메라에 가까운 중심선 점. 앞으로만 찾는다.

    머리핀에서 중심선이 x 에 닿기 전에 되돌아오면(차 기준 반경 < x) 가장 앞까지 간 점의 값을 쓴다
    (인지 대역 LaneFit.centre 와 같은 약속. 되돌아온 뒤 다른 구간과 만나는 점을 정답으로 삼지 않게)."""
    c, s = math.cos(heading), math.sin(heading)

    def local(i: int) -> tuple[float, float]:
        px, py = track.C[i % track.N]
        dx, dy = px - cam_x, py - cam_y
        return dx * c + dy * s, -dx * s + dy * c

    a = local(i0 - 5)
    for k in range(i0 - 4, i0 + 40):
        b = local(k)
        if (a[0] - x) * (b[0] - x) <= 0 and a[0] != b[0]:
            t = (x - a[0]) / (b[0] - a[0])
            return a[1] + t * (b[1] - a[1])
        if a[0] > 0 and b[0] < a[0]:
            return a[1]
        a = b
    return None


def ground_truth(track: Track, cam_x: float, cam_y: float, heading: float, hint: int | None = None) -> tuple[float, float] | None:
    """(lane_offset_m, heading_err_rad) 정답. 중심선을 못 찾으면 None."""
    i = track.nearest(cam_x, cam_y, hint)
    c0 = _lateral_at(track, cam_x, cam_y, heading, i, 0.0)
    cl = _lateral_at(track, cam_x, cam_y, heading, i, LANE_LOOKAHEAD_M)
    if c0 is None or cl is None:
        return None
    return -c0, math.atan2(cl - c0, LANE_LOOKAHEAD_M)


def poses(track: Track, step_m: float = FRAME_STEP_M, start_m: float = 0.0, length_m: float | None = None,
          wobble: bool = True) -> Iterator[tuple[float, int, float, float, float]]:
    """(출발선부터 거리, 중심선 점, 후륜 축 x, y, heading). 중심선을 따라가며 횡방향·방향을 천천히 흔든다."""
    lap = track.lap_m
    length = lap if length_m is None else length_m
    s0 = track.SL[track.start_index]
    n = int(length / step_m)
    j = track.start_index
    for k in range(n):
        d = start_m + k * step_m
        s = (s0 + d) % lap
        while not (track.SL[j] <= s < (track.SL[j + 1] if j + 1 < track.N else lap)):
            j = (j + 1) % track.N
        k1 = (j + 1) % track.N
        seg = (track.SL[k1] if k1 else lap) - track.SL[j]
        t = (s - track.SL[j]) / seg if seg > 0 else 0.0
        (ax, ay), (bx, by) = track.C[j], track.C[k1]
        tx, ty = bx - ax, by - ay
        h = math.atan2(ty, tx)
        off = dh = 0.0
        if wobble:
            off = WOBBLE_OFF_M * math.sin(2 * math.pi * d / 6.1) * math.cos(2 * math.pi * d / 17.0)
            dh = WOBBLE_HEADING_RAD * math.sin(2 * math.pi * d / 4.3 + 1.0)
        nx, ny = -math.sin(h), math.cos(h)
        yield d, j, ax + tx * t + nx * off, ay + ty * t + ny * off, h + dh


def evaluate_track(track_id: str, *, step_m: float = FRAME_STEP_M, start_m: float = 0.0, length_m: float | None = None,
                   wobble: bool = True, renderer: Renderer | None = None) -> list[Sample]:
    track = load(track_id)
    renderer = renderer or Renderer(CameraSpec())
    detector = Detector(PerceptionSettings(lane_width_m=track.lane_width_m))
    out: list[Sample] = []
    hint = None
    for k, (d, j, x, y, h) in enumerate(poses(track, step_m, start_m, length_m, wobble)):
        cam_x, cam_y = renderer.camera_position(x, y, h)
        truth = ground_truth(track, cam_x, cam_y, h, hint)
        hint = track.nearest(cam_x, cam_y, hint)
        r = detector.detect(renderer.render(track, x, y, h), k / 15.0)
        if truth is None:
            continue
        out.append(Sample(track_id, d, track.turn[hint], bool(r.extra.get("lane_found")),
                          r.lane.offset_m, r.lane.heading_err_rad, truth[0], truth[1]))
    return out


@dataclass(frozen=True)
class Stats:
    n: int
    found: float
    offset_p50: float
    offset_p95: float
    offset_max: float
    heading_p50: float
    heading_p95: float
    heading_max: float

    def __str__(self) -> str:
        return (f"n={self.n:5d} 찾음 {self.found * 100:5.1f}% | offset |오차| p50 {self.offset_p50:.3f} "
                f"p95 {self.offset_p95:.3f} max {self.offset_max:.3f} m | heading p50 {self.heading_p50:.3f} "
                f"p95 {self.heading_p95:.3f} max {self.heading_max:.3f} rad")


def stats(samples: list[Sample]) -> Stats:
    """못 찾은 프레임도 0 을 낸 그대로 오차에 넣는다 (자율 앱은 그 0 을 받는다)."""
    off = np.abs([s.offset_err for s in samples])
    hd = np.abs([s.heading_err for s in samples])
    return Stats(len(samples), float(np.mean([s.found for s in samples])),
                 float(np.percentile(off, 50)), float(np.percentile(off, 95)), float(off.max()),
                 float(np.percentile(hd, 50)), float(np.percentile(hd, 95)), float(hd.max()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="시뮬레이터 화면으로 차선 추정 정확도를 정답과 비교")
    parser.add_argument("--track", choices=track_ids(), help="한 트랙만 (기본: 10개 모두)")
    parser.add_argument("--step", type=float, default=FRAME_STEP_M, help="프레임 사이 거리 m")
    parser.add_argument("--no-wobble", action="store_true", help="중심선 위에 바로 세운다")
    parser.add_argument("--worst", type=int, default=0, help="heading 오차가 가장 큰 프레임 N 개 출력")
    a = parser.parse_args(argv)
    renderer = Renderer(CameraSpec())
    every: list[Sample] = []
    for track_id in [a.track] if a.track else track_ids():
        samples = evaluate_track(track_id, step_m=a.step, wobble=not a.no_wobble, renderer=renderer)
        every += samples
        print(f"{track_id:12s} {stats(samples)}", flush=True)
    if not a.track:
        print(f"{'전체':12s} {stats(every)}")
    for s in sorted(every, key=lambda s: -abs(s.heading_err))[: a.worst]:
        print(f"  {s.track_id} s={s.s:6.2f} 곡률 {s.turn:+.2f} 찾음 {s.found} offset {s.offset:+.3f}/{s.true_offset:+.3f} "
              f"heading {s.heading:+.3f}/{s.true_heading:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
