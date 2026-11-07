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
from copy import deepcopy
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
    lane_width_m: float = LANE_WIDTH_M
    curb_m: float = CURB_M
    layout: dict | None = None


@lru_cache(maxsize=1)
def sources() -> dict[str, TrackSource]:
    data = json.loads(resources.files(__package__).joinpath("tracks.json").read_text(encoding="utf-8"))
    return {t["id"]: TrackSource(t["id"], t["name"], t["real_m"], tuple((float(x), float(y)) for x, y in t["centerline"]),
                                t.get("lane_width_m", LANE_WIDTH_M), t.get("curb_m", CURB_M), t.get("layout"))
            for t in data["tracks"]}


def _in_range(i: int, a: int, b: int) -> bool:
    return a <= i <= b if a <= b else i >= a or i <= b


def _round_mm(v: float) -> float:
    return math.floor(v * 1000 + 0.5) / 1000  # JS Math.round 과 같은 반올림


def _cross(a, b, p) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _area(polygon) -> float:
    return .5 * sum(a[0] * b[1] - b[0] * a[1]
                    for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def point_in_polygon(x: float, y: float, polygon) -> bool:
    """단순 polygon 내부와 경계를 포함한다. 좌표 단위는 metre이다."""
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        on_segment = (abs(_cross(a, b, (x, y))) <= 1e-10
                      and min(a[0], b[0]) - 1e-10 <= x <= max(a[0], b[0]) + 1e-10
                      and min(a[1], b[1]) - 1e-10 <= y <= max(a[1], b[1]) + 1e-10)
        if on_segment:
            return True
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return inside


def _clip_halfplane(polygon, a, b, side):
    result = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        ds, de = side * _cross(a, b, start), side * _cross(a, b, end)
        start_inside, end_inside = ds >= -1e-10, de >= -1e-10
        if start_inside != end_inside:
            t = ds / (ds - de)
            result.append((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
        if end_inside:
            result.append(end)
    return result


def _subtract_convex(subject, clip):
    """볼록 clip을 빼고 남은 볼록 조각들. 네 꼭짓점만 확인하는 hole 누락을 피한다."""
    remaining, outside = subject, []
    side = 1 if _area(clip) > 0 else -1
    for a, b in zip(clip, clip[1:] + clip[:1]):
        piece = _clip_halfplane(remaining, a, b, -side)
        if len(piece) >= 3 and abs(_area(piece)) > 1e-10:
            outside.append(piece)
        remaining = _clip_halfplane(remaining, a, b, side)
        if len(remaining) < 3:
            break
    return outside


class Track:
    """한 서킷의 기하. 물리·시험은 float 로, 카메라는 raster(카메라 모듈)로 쓴다."""

    def __init__(self, src: TrackSource) -> None:
        self.id, self.name, self.real_m = src.id, src.name, src.real_m
        self.lane_width_m, self.curb_m = src.lane_width_m, src.curb_m
        self.road_m = self.lane_width_m / 2 + self.curb_m
        self.layout = deepcopy(src.layout)
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
            ds = sum(math.dist(C[(i + d) % N], C[(i + d + 1) % N]) for d in range(-3, 3)) if self.layout else 1.2
            turn.append(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]) / ds)
        self.turn = turn
        self.corner = [max(abs(turn[(i + d) % N]) for d in range(-6, 7)) > 0.35 for i in range(N)]
        SL = [0.0]
        for i in range(1, N):
            SL.append(SL[-1] + math.hypot(C[i][0] - C[i - 1][0], C[i][1] - C[i - 1][1]))
        self.SL = SL
        self.lap_m = SL[-1] + math.hypot(C[0][0] - C[-1][0], C[0][1] - C[-1][1])

        if self.layout:
            self.start_index = 0
            self.wall_left_m = [self.road_m + _APRON] * N
            self.wall_right_m = [self.road_m + _APRON] * N
            self._paved_polygons = [
                [self.at(i, -self.road_m), self.at((i + 1) % N, -self.road_m),
                 self.at((i + 1) % N, self.road_m), self.at(i, self.road_m)] for i in range(N)
            ] + [[tuple(p) for p in polygon] for polygon in self.layout["paved_areas"]]
            return

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
            offsets = self.road_m + gs + 0.05
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
                out.append(max(_APRON, 0.75 / k - self.road_m) if k > 0.05 else 9.0)
            return out

        w = smooth([want(i) for i in range(N)], 6)

        def fin(r: list[float], cap: list[float]) -> list[float]:
            m = [min(w[i], r[i], cap[i]) for i in range(N)]
            lo = [min(m[(i + d) % N] for d in range(-3, 4)) for i in range(N)]
            return [_round_mm(self.road_m + max(_APRON, v)) for v in smooth(lo, 2)]

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

    def paved_at(self, x: float, y: float) -> bool:
        """도로 strip과 주차 apron/slot의 합집합. 새 데모만 polygon geometry를 쓴다."""
        if self.layout:
            return any(point_in_polygon(x, y, polygon) for polygon in self._paved_polygons)
        _, off, _ = self.frame(x, y)
        return abs(off) <= self.road_m

    def polygon_is_paved(self, polygon) -> bool:
        """볼록 footprint 전체가 포장 합집합에 포함되는지 검사한다.

        데모 도로 quad와 paved_areas는 볼록 polygon이다. 면적 1e-10 m² 이하의
        부동소수점 잔여 조각만 경계 접촉으로 취급한다. 점 표본이나 실제 기동 검증은 아니다.
        """
        if not self.layout:
            return all(self.paved_at(*p) for p in polygon)
        remaining = [list(polygon)]
        for paved in self._paved_polygons:
            remaining = [piece for subject in remaining for piece in _subtract_convex(subject, paved)]
            if not remaining:
                return True
        return not remaining

    def box_is_paved(self, x: float, y: float, width: float, height: float | None = None) -> bool:
        """장애물의 축정렬 사각형 전체 포함. width/height는 metre이다."""
        h = width if height is None else height
        return self.polygon_is_paved([(x - width / 2, y - h / 2), (x + width / 2, y - h / 2),
                                     (x + width / 2, y + h / 2), (x - width / 2, y + h / 2)])

    def surface_at(self, x: float, y: float, hint: int | None = None) -> str:
        """실좌표 노면. apron도 road이며 중심선 투영으로 slot 위치를 잃지 않는다."""
        if self.layout:
            return "road" if self.paved_at(x, y) else "grass"
        i, off, _ = self.frame(x, y, hint)
        return self.surface(i, off)

    def surface(self, i: int, off: float) -> str:
        """차 중심 노면 (계약 v1.4 sim.pose.surface). 코너 바깥쪽은 흙, 나머지는 잔디."""
        if self.layout:
            return self.surface_at(*self.at(i, off))
        if abs(off) <= self.road_m:
            return "road"
        outside = -((self.turn[i] > 0) - (self.turn[i] < 0)) or 1
        return "dirt" if self.corner[i] and ((off > 0) - (off < 0)) == outside else "grass"

    def start_pose(self) -> tuple[float, float, float]:
        """출발선 위, 진행 방향을 본다. (x, y, heading)."""
        if self.layout:
            return tuple(self.layout["start_pose"])
        i = self.start_index
        return self.C[i][0], self.C[i][1], math.atan2(self.tan[i][1], self.tan[i][0])

    def progress(self, x: float, y: float, hint: int | None = None) -> float:
        """출발선에서 진행 방향으로 잰 거리 (0 ~ 한 바퀴)."""
        _, _, s = self.frame(x, y, hint)
        return (s - self.SL[self.start_index]) % self.lap_m

    def bounds(self, margin: float) -> tuple[float, float, float, float]:
        xs, ys = [p[0] for p in self.C], [p[1] for p in self.C]
        box = min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin
        if self.layout:
            lx0, ly0, lx1, ly1 = self.layout["bounds"]
            return min(box[0], lx0), min(box[1], ly0), max(box[2], lx1), max(box[3], ly1)
        return box

    def payload(self) -> dict:
        """sim.world.track (계약 v1.4 §11)."""
        result = {
            "shape": "centerline", "id": self.id, "name": self.name, "real_m": self.real_m,
            "lane_width_m": self.lane_width_m, "curb_m": self.curb_m,
            "centerline": [list(p) for p in self.C] if self.layout else [[round(x, 3), round(y, 3)] for x, y in self.C],
            "wall_left_m": self.wall_left_m, "wall_right_m": self.wall_right_m,
            "start_index": self.start_index,
        }
        if self.layout:
            result["layout"] = deepcopy(self.layout)
        return result


@lru_cache(maxsize=16)
def load(track_id: str = DEFAULT_TRACK) -> Track:
    try:
        return Track(sources()[track_id])
    except KeyError:
        raise ValueError(f"모르는 트랙 {track_id!r}. 있는 것: {', '.join(sources())}") from None


def track_ids() -> list[str]:
    return list(sources())
