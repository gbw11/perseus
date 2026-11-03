"""서킷 트랙 (MQTT 계약 v1.4 §11 `sim.world.track`). 관리 콘솔 mock(`admin-console/src/mocks/simEngine.ts` buildTrack)과
같은 계산이라 화면 탑뷰·출발선·벽이 시뮬레이터와 맞는다.

- 중심선: tracks.json (tracks.ts 에서 tools/export_tracks.py 로 옮김). 약 0.2 m 간격, 닫힌 고리, 진행 방향 순서.
- 점마다 접선·법선(왼쪽 +)·곡률·코너 여부, 누적 거리, 왼쪽·오른쪽 벽까지 거리(출발 직선은 바로 벽, 코너 근처는 두 배,
  다른 구간과 가까우면 가운데에서 만나게), 출발선(가장 긴 직선의 30 % 지점).
- 횡방향 오프셋 off: 중심선 기준 + = 왼쪽. 차선 |off| ≤ 0.3, 연석 0.3~0.36, 그 밖 벽까지 잔디(코너 바깥은 흙).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import numpy as np

LANE_WIDTH_M = 0.6
CURB_M = 0.06
ROAD_M = LANE_WIDTH_M / 2 + CURB_M
_GRASS = 0.34
_GRASS_CORNER = 0.68
_APRON = 0.03
DEFAULT_TRACK = "spielberg"


@dataclass(frozen=True)
class TrackSource:
    id: str
    name: str
    real_m: int
    centerline: tuple[tuple[float, float], ...]


@lru_cache(maxsize=1)
def sources() -> dict[str, TrackSource]:
    data = json.loads(resources.files(__package__).joinpath("tracks.json").read_text(encoding="utf-8"))
    return {t["id"]: TrackSource(t["id"], t["name"], t["real_m"], tuple((float(x), float(y)) for x, y in t["centerline"]))
            for t in data["tracks"]}


def _in_range(i: int, a: int, b: int) -> bool:
    return a <= i <= b if a <= b else i >= a or i <= b


def _round_mm(v: float) -> float:
    return math.floor(v * 1000 + 0.5) / 1000  # JS Math.round 과 같은 반올림


class Track:
    """한 서킷의 기하. 물리·시험은 float 로, 카메라는 raster(카메라 모듈)로 쓴다."""

    def __init__(self, src: TrackSource) -> None:
        self.id, self.name, self.real_m = src.id, src.name, src.real_m
        self.lane_width_m, self.curb_m, self.road_m = LANE_WIDTH_M, CURB_M, ROAD_M
        C = self.C = [tuple(p) for p in src.centerline]
        N = self.N = len(C)
        tan = []
        for i in range(N):
            a, b = C[(i - 1) % N], C[(i + 1) % N]
            l = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
            tan.append(((b[0] - a[0]) / l, (b[1] - a[1]) / l))
        self.tan = tan
        self.nor = [(-y, x) for x, y in tan]
        turn = []
        for i in range(N):
            a, b = tan[(i - 3) % N], tan[(i + 3) % N]
            turn.append(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]) / 1.2)
        self.turn = turn
        self.corner = [max(abs(turn[(i + d) % N]) for d in range(-6, 7)) > 0.35 for i in range(N)]
        SL = [0.0]
        for i in range(1, N):
            SL.append(SL[-1] + math.hypot(C[i][0] - C[i - 1][0], C[i][1] - C[i - 1][1]))
        self.SL = SL
        self.lap_m = SL[-1] + math.hypot(C[0][0] - C[-1][0], C[0][1] - C[-1][1])

        best_len = best_start = 0
        for i in range(N):
            L = 0
            while L < N and not self.corner[(i + L) % N]:
                L += 1
            if L > best_len:
                best_len, best_start = L, i
        self.start_index = (best_start + int(best_len * 0.3)) % N
        straight = ((best_start + 4) % N, (best_start + best_len - 4) % N)

        def want(i: int) -> float:
            if _in_range(i, *straight):
                return _APRON
            return _GRASS_CORNER if any(self.corner[(i + d) % N] for d in range(-8, 9)) else _GRASS

        def smooth(a: list[float], r: int) -> list[float]:
            return [sum(a[(i + d) % N] for d in range(-r, r + 1)) / (2 * r + 1) for i in range(N)]

        def room(sd: int) -> list[float]:
            # 다른 구간과 가까우면 두 구간 사이 가운데에서 벽이 만나게.
            # simEngine 의 g 루프(0.02 씩 더함)와 같은 값을 numpy 로 한 번에 본다: 처음 걸리는 g, 없으면 루프 끝 값
            steps, g = [], _APRON
            while g < _GRASS_CORNER:
                steps.append(g)
                g += 0.02
            g_end = g
            gs = np.array(steps)
            offsets = ROAD_M + gs + 0.05
            pts = np.array(C)
            even = np.arange(0, N, 2)
            out = []
            for i in range(N):
                dj = np.minimum((even - i) % N, (i - even) % N)
                others = pts[even[dj > 15]]
                cx, cy = C[i][0] + self.nor[i][0] * sd * offsets, C[i][1] + self.nor[i][1] * sd * offsets
                dist = np.hypot(others[None, :, 0] - cx[:, None], others[None, :, 1] - cy[:, None])
                blocked = (dist < offsets[:, None]).any(axis=1)
                out.append(float(gs[np.argmax(blocked)]) if blocked.any() else g_end)
            return out

        def inner(sd: int) -> list[float]:
            # 코너 안쪽은 반경보다 넓히면 벽이 접힌다 → 반경의 75 % 안으로
            out = []
            for i in range(N):
                k = 0.0
                for d in range(-8, 9):
                    t = turn[(i + d) % N]
                    if (t > 0) - (t < 0) == sd:
                        k = max(k, abs(t))
                out.append(max(_APRON, 0.75 / k - ROAD_M) if k > 0.05 else 9.0)
            return out

        w = smooth([want(i) for i in range(N)], 6)

        def fin(r: list[float], cap: list[float]) -> list[float]:
            m = [min(w[i], r[i], cap[i]) for i in range(N)]
            lo = [min(m[(i + d) % N] for d in range(-3, 4)) for i in range(N)]
            return [_round_mm(ROAD_M + max(_APRON, v)) for v in smooth(lo, 2)]

        self.wall_left_m = fin(room(1), inner(1))
        self.wall_right_m = fin(room(-1), inner(-1))

    # --- 기하 ---

    def at(self, i: int, off: float) -> tuple[float, float]:
        c, n = self.C[i], self.nor[i]
        return c[0] + n[0] * off, c[1] + n[1] * off

    def nearest(self, x: float, y: float, hint: int | None = None) -> int:
        """가장 가까운 중심선 점. hint 가 있으면 그 앞뒤 40점만 본다 (simEngine nearestT 와 같음)."""
        C, N = self.C, self.N
        idx = range(N) if hint is None else ((hint + d) % N for d in range(-40, 40))
        return min(idx, key=lambda i: (C[i][0] - x) ** 2 + (C[i][1] - y) ** 2)

    def frame(self, x: float, y: float, hint: int | None = None) -> tuple[int, float, float]:
        """(가까운 점, 횡방향 오프셋 + 왼쪽, 출발점 기준이 아닌 중심선 누적 거리)."""
        i = self.nearest(x, y, hint)
        dx, dy = x - self.C[i][0], y - self.C[i][1]
        return i, dx * self.nor[i][0] + dy * self.nor[i][1], self.SL[i] + dx * self.tan[i][0] + dy * self.tan[i][1]

    def wall(self, i: int, off: float) -> float:
        return self.wall_left_m[i] if off > 0 else self.wall_right_m[i]

    def surface(self, i: int, off: float) -> str:
        """차 중심 노면 (계약 v1.4 sim.pose.surface). 코너 바깥쪽은 흙, 나머지는 잔디."""
        if abs(off) <= ROAD_M:
            return "road"
        outside = -((self.turn[i] > 0) - (self.turn[i] < 0)) or 1
        return "dirt" if self.corner[i] and ((off > 0) - (off < 0)) == outside else "grass"

    def start_pose(self) -> tuple[float, float, float]:
        """출발선 위, 진행 방향을 본다. (x, y, heading)."""
        i = self.start_index
        return self.C[i][0], self.C[i][1], math.atan2(self.tan[i][1], self.tan[i][0])

    def progress(self, x: float, y: float, hint: int | None = None) -> float:
        """출발선에서 진행 방향으로 잰 거리 (0 ~ 한 바퀴)."""
        _, _, s = self.frame(x, y, hint)
        return (s - self.SL[self.start_index]) % self.lap_m

    def bounds(self, margin: float) -> tuple[float, float, float, float]:
        xs, ys = [p[0] for p in self.C], [p[1] for p in self.C]
        return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin

    def payload(self) -> dict:
        """sim.world.track (계약 v1.4 §11)."""
        return {
            "shape": "centerline", "id": self.id, "name": self.name, "real_m": self.real_m,
            "lane_width_m": self.lane_width_m, "curb_m": self.curb_m,
            "centerline": [[round(x, 3), round(y, 3)] for x, y in self.C],
            "wall_left_m": self.wall_left_m, "wall_right_m": self.wall_right_m,
            "start_index": self.start_index,
        }


@lru_cache(maxsize=16)
def load(track_id: str = DEFAULT_TRACK) -> Track:
    try:
        return Track(sources()[track_id])
    except KeyError:
        raise ValueError(f"모르는 트랙 {track_id!r}. 있는 것: {', '.join(sources())}") from None


def track_ids() -> list[str]:
    return list(sources())
