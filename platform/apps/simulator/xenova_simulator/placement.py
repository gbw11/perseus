"""장애물 배치 검사 (MQTT 계약 §12 `sim.command` 배치 검사, S15P31E110-319).

관리 콘솔 mock(`admin-console/src/mocks/simEngine.ts` checkPlacement)을 같은 식·같은 순서로 옮겼다. 콘솔 mock 과
실제 시뮬레이터가 같은 자리를 같은 이유로 거부해야 운영자 화면이 바뀌지 않는다.

1. `OUT_OF_TRACK` — 장애물이 벽 밖으로 조금이라도 나간다 (|off| > 벽 − 폭/2)
2. `ON_CAR`       — 차 몸과 겹친다 (트랙 진행 방향 ±(차 길이 + 폭)/2, 옆 ±(차 반폭 + 폭/2))
3. `NO_GAP`       — 장애물 앞뒤 구간(3 cm 간격)에서 가장 넓은 틈이 NEED = 2 × (차 반폭 0.10 + 여유 0.03) = 0.26 m 보다 좁다
4. `TOO_DENSE`    — 틈은 있지만, 기준 회피 주행(아래)을 장애물 묶음 앞 4.8 m 에서 출발시켜 보면 서거나 닿거나 너무 느리다
5. `TOO_CLOSE`    — 출발 뒤(race go)이고 차 바로 앞(−0.3 ~ 4.8 m)이면, 지금 차 상태 그대로 돌려 봐도 못 지나간다

4·5 의 "기준 회피 주행"은 mock 의 자율주행 규칙(perceive → decide → control → physics, 시나리오 §4-2)이다.
지금 자율 앱(Zephyr)은 장애물 앞에서 서기만 하지만, 콘솔 mock 과 같은 판정을 위해 그 규칙으로 미리 돌려 본다.
차 모델도 mock 의 단순 모델(ACC·BRAKE)이며 이 시뮬레이터의 바퀴·모터 모델(model.Vehicle)과 다르다 — 판정 전용이다.
좌표 기준: 차 (x, y) 는 sim.pose 와 같은 점(뒷바퀴 축 가운데). mock 도 pose 의 x, y 를 그대로 쓴다.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field, replace
from functools import lru_cache

from .model import KIND_W
from .track import ROAD_M, Track

# ---- 크기·속도 (simEngine.ts 와 같음) ----
CAR_HALF = 0.1
CAR_L = 0.42
MAX_V = 1.0
BRAKE = 1.0
ACC_REV = 0.2
A_LAT = 0.22
BRAKE_PLAN = 0.5
AVOID_V = 0.4
SLOW_V = 0.3
STOP_M = 0.8
LOOK_M = 4.0
SAFE = 0.03
NEED = 2 * (CAR_HALF + SAFE)      # 0.26 m: 차 폭 0.20 + 양쪽 0.03
WHEELBASE = 0.26


def _acc(v: float) -> float:
    return 0.26 * (1 - (0.6 * max(0.0, v)) / MAX_V)


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


@dataclass(frozen=True)
class Item:
    """검사용 장애물: 종류·위치와 트랙 좌표 (가까운 점 i, 옆 off + 왼쪽, 중심선 누적 거리 s)."""

    id: str
    kind: str
    x: float
    y: float
    i: int
    off: float
    s: float

    @property
    def w(self) -> float:
        return KIND_W[self.kind]

    @classmethod
    def at(cls, track: Track, obstacle_id: str, kind: str, x: float, y: float) -> Item:
        i, off, s = track.frame(x, y)
        return cls(obstacle_id, kind, x, y, i, off, s)


@dataclass
class Car:
    x: float
    y: float
    yaw: float
    v: float
    steer: float
    idx: int
    touched: bool = False


@dataclass(frozen=True)
class Check:
    code: str
    message: str
    gap_m: float | None = None
    need_m: float | None = None


# ---- 기하 도우미 ----

@lru_cache(maxsize=16)
def _v_prof(track: Track) -> tuple[float, ...]:
    """자율 속도 계획: 코너 √(A_LAT/곡률) 을 넘지 않게, 그 앞에서 BRAKE_PLAN 으로 미리 줄인다 (buildTrack vProf)."""
    C, N = track.C, track.N
    vc = [min(MAX_V, math.sqrt(A_LAT / max(0.02, abs(k)))) for k in track.turn]
    prof = list(vc)
    for _ in range(2):
        for i in range(N - 1, -1, -1):
            j = (i + 1) % N
            ds = math.hypot(C[j][0] - C[i][0], C[j][1] - C[i][1])
            prof[i] = min(vc[i], math.sqrt(prof[j] ** 2 + 2 * BRAKE_PLAN * ds))
    return tuple(prof)


def wrap_s(T: Track, d: float) -> float:
    return ((d % T.lap_m) + T.lap_m * 1.5) % T.lap_m - T.lap_m / 2


def _s_at(T: Track, x: float, y: float, i: int) -> float:
    return T.SL[i] + (x - T.C[i][0]) * T.tan[i][0] + (y - T.C[i][1]) * T.tan[i][1]


def idx_at_s(T: Track, s: float) -> int:
    return max(0, bisect.bisect_right(T.SL, s % T.lap_m) - 1)


def _wall(T: Track, i: int, sd: int) -> float:
    return T.wall_left_m[i] if sd > 0 else T.wall_right_m[i]


def car_at_s(T: Track, s: float, v: float) -> Car:
    i = idx_at_s(T, s)
    return Car(T.C[i][0], T.C[i][1], math.atan2(T.tan[i][1], T.tan[i][0]), v, 0.0, i)


# ---- 기준 회피 주행: 인지 → 판단 → 조향·속도 (simEngine perceive·decide·control·physics) ----

@dataclass
class _Seen:
    o: Item
    ahead: float
    lat: float
    w: float


@dataclass
class _Perception:
    off: float
    i: int
    s: float
    seen: list[_Seen]


@dataclass
class _Decision:
    mode: str
    target: float
    blocking: list[_Seen] | None = None
    first_ahead: float | None = None


@dataclass
class _Group:
    items: list[_Seen]
    lo: float
    hi: float
    lanes: list[tuple[float, float]] = field(default_factory=list)


def _perceive(T: Track, st: Car, items: list[Item]) -> _Perception:
    st.idx = T.nearest(st.x, st.y, st.idx)
    i = st.idx
    off = (st.x - T.C[i][0]) * T.nor[i][0] + (st.y - T.C[i][1]) * T.nor[i][1]
    sc = _s_at(T, st.x, st.y, i)
    seen = []
    for o in items:
        ahead = wrap_s(T, o.s - sc)
        w = o.w
        if -(CAR_L / 2 + w / 2 + 0.05) < ahead < LOOK_M + 0.6:
            seen.append(_Seen(o, ahead, o.off, w))
    seen.sort(key=lambda s: s.ahead)
    return _Perception(off, i, sc, seen)


def _free_lanes(T: Track, obs: list[_Seen]) -> list[tuple[float, float]]:
    hi_l, lo_r = 9.0, -9.0
    for s in obs:
        hi_l = min(hi_l, T.wall_left_m[s.o.i] - CAR_HALF - SAFE)
        lo_r = max(lo_r, -T.wall_right_m[s.o.i] + CAR_HALF + SAFE)
    free = [(lo_r, hi_l)]
    for s in obs:
        clr = s.w / 2 + CAR_HALF + SAFE
        lo, hi = s.lat - clr, s.lat + clr
        nxt = []
        for a, b in free:
            if hi <= a or lo >= b:
                nxt.append((a, b))
            else:
                if lo > a:
                    nxt.append((a, lo))
                if hi < b:
                    nxt.append((hi, b))
        free = [(a, b) for a, b in nxt if b - a > 0.005]
    return free


def _reach(D: float) -> float:
    """앞으로 D m 안에 옆으로 옮겨 갈 수 있는 거리."""
    return max(0.0, (D - 0.6) / 1.1)


def _decide(T: Track, p: _Perception, prev_t: float | None) -> _Decision:
    vis = [s for s in p.seen if s.ahead < LOOK_M]
    if not vis:
        return _Decision("cruise", 0.0)
    groups: list[_Group] = []
    for s in vis:
        g = groups[-1] if groups else None
        lo, hi = s.ahead - s.w / 2, s.ahead + s.w / 2
        if g is not None and lo - g.hi < CAR_L:
            g.items.append(s)
            g.hi = max(g.hi, hi)
        else:
            groups.append(_Group([s], lo, hi))
    for g in groups:
        lanes = []
        for a, b in _free_lanes(T, g.items):
            e = min(0.05, (b - a) / 2)
            lanes.append((a + e, b - e))
        g.lanes = lanes
    first = groups[0]
    passing = first.lo < CAR_L / 2
    blocking = [s for s in first.items if abs(s.lat - p.off) < s.w / 2 + CAR_HALF + SAFE]
    combos: list[list[tuple[float, float]]] = [[]]
    for g in groups:
        nxt = [[*c, lane] for c in combos for lane in g.lanes]
        combos = nxt[:400]

    def room_of(k: int) -> float:
        if k == 0:
            return 0.0 if passing else first.lo - CAR_L / 2
        return groups[k].lo - groups[k - 1].hi - CAR_L

    def clamp_in(x: float, lane: tuple[float, float]) -> float:
        return min(lane[1], max(lane[0], x))

    best: tuple[float, list[float]] | None = None
    for lanes in combos:
        A, prev = [], p.off
        for lane in lanes:
            prev = clamp_in(prev, lane)
            A.append(prev)
        B = list(A)
        for k in range(len(B) - 2, -1, -1):
            B[k] = clamp_in(B[k + 1], lanes[k])
        for xs in (A, B):
            ok, cost, pv = True, 0.0, p.off
            for k, x in enumerate(xs):
                mv = abs(x - pv)
                lane = lanes[k]
                keep = k == 0 and prev_t is not None and lane[0] - 0.02 <= prev_t <= lane[1] + 0.02
                D = room_of(k)
                lim = (max(_reach(D) * 1.6 + 0.06, (D - 0.25) / 0.8) if keep else _reach(D)) + 0.015
                if mv > lim:
                    ok = False
                cost += (mv + 0.3 * abs(x) + 1.5 * max(0.0, abs(x) + CAR_HALF - ROAD_M)
                         + 2 * max(0.0, mv / lim - 0.5)
                         + (0.5 * abs(x - prev_t) if k == 0 and prev_t is not None else 0.0))
                pv = x
            if ok and (best is None or cost < best[0]):
                best = (cost, xs)
    if best is None:
        return _Decision("stop", p.off, blocking=blocking or first.items)
    t = best[1][0]
    if not passing:
        lane = next((ln for ln in first.lanes if ln[0] - 1e-9 <= t <= ln[1] + 1e-9), None)
        if lane is not None:
            c = min(lane[1], max(lane[0], 0.0))
            if abs(c - t) < 0.02 or len(groups) == 1:
                t = c if abs(c - p.off) <= abs(t - p.off) + 0.05 else t
    busy = bool(blocking) or any(abs(x - p.off) > 0.03 for x in best[1])
    return _Decision("avoid" if blocking or abs(t) > 0.03 else "cruise", t,
                     first_ahead=first.lo if busy else None)


def _control(T: Track, st: Car, p: _Perception, d: _Decision) -> tuple[float, float]:
    """조향 (pure pursuit, 평소 0.8 m 앞·회피 중 0.4 m 앞) + 속도 계획. (목표 속도, 목표 조향)."""
    avoid = d.mode != "cruise"
    tx, ty = T.at((p.i + (2 if avoid else 4)) % T.N, d.target)
    ld = max(0.3, math.hypot(tx - st.x, ty - st.y))
    alpha = math.atan2(ty - st.y, tx - st.x) - st.yaw
    ts = max(-0.45, min(0.45, math.atan2(2 * WHEELBASE * math.sin(alpha), ld)))
    if d.mode == "stop":
        f = d.blocking[0] if d.blocking else None
        return (AVOID_V * 0.5 if f is not None and f.ahead > STOP_M + 0.4 else 0.0), ts
    prof = _v_prof(T)
    tv = MAX_V
    for k in range(5):
        tv = min(tv, prof[(p.i + k) % T.N])
    if d.mode == "avoid":
        tv = min(tv, AVOID_V)
    if d.first_ahead is not None:
        tv = min(tv, math.sqrt(AVOID_V ** 2 + 2 * BRAKE_PLAN * max(0.0, d.first_ahead - 0.6)))
    tv *= 1 - (0.25 * abs(ts)) / 0.45
    if max(abs(p.off), abs(d.target)) + CAR_HALF > ROAD_M:
        tv = min(tv, SLOW_V)
    return tv, ts


def touching(T: Track, st: Car, items: list[Item]) -> bool:
    _, off, s = T.frame(st.x, st.y, st.idx)
    return any(abs(wrap_s(T, o.s - s)) < CAR_L / 2 + o.w / 2 and abs(o.off - off) < CAR_HALF + o.w / 2 for o in items)


def _physics(T: Track, st: Car, tv: float, ts: float, dt: float, items: list[Item]) -> None:
    """mock 차 모델: 가속은 서서히, 잔디 감속, 벽, 장애물 접촉 (판정 전용)."""
    px, py, pyaw = st.x, st.y, st.yaw
    dv = tv - st.v
    if dv > 0:
        st.v += min(dv, (BRAKE if st.v < -0.01 else _acc(st.v)) * dt)
    else:
        st.v -= min(-dv, (BRAKE if st.v > 0.01 else ACC_REV) * dt)
    st.steer += max(-3 * dt, min(3 * dt, ts - st.steer))
    st.x += st.v * math.cos(st.yaw) * dt
    st.y += st.v * math.sin(st.yaw) * dt
    st.yaw += (st.v / WHEELBASE) * math.tan(st.steer) * dt
    i, off, _ = T.frame(st.x, st.y, st.idx)
    sd = _sign(off) or 1
    lim = _wall(T, i, sd) - CAR_HALF
    if abs(off) > ROAD_M and st.v > SLOW_V:
        st.v = max(SLOW_V, st.v - 1.2 * dt)
    if abs(off) > lim:
        back = off - sd * lim
        st.x -= T.nor[i][0] * back
        st.y -= T.nor[i][1] * back
        along = math.atan2(T.tan[i][1], T.tan[i][0])
        into = math.sin(st.yaw - along) * sd
        if into > 0:
            st.yaw -= sd * math.asin(min(1.0, into)) * 0.6
        st.v *= 0.92
    if items and touching(T, st, items):
        st.x, st.y, st.yaw, st.v = px, py, pyaw, 0.0
        st.touched = True


def trial(T: Track, st: Car, items: list[Item], s_end: float) -> str | None:
    """기준 회피 주행을 0.02 s 씩 빨리 돌려 본다: s_end 를 지나면 통과(None), 서면 'stop', 닿으면 'touch', 시간 초과 'slow'."""
    d0 = wrap_s(T, s_end - _s_at(T, st.x, st.y, T.nearest(st.x, st.y, st.idx)))
    pt: float | None = None
    t = 0.0
    while t < d0 / 0.12 + 6:
        p = _perceive(T, st, items)
        d = _decide(T, p, pt)
        tv, ts = _control(T, st, p, d)
        pt = d.target
        _physics(T, st, tv, ts, 0.02, items)
        if st.touched:
            return "touch"
        if d.mode == "stop" and st.v < 0.01 and t > 0.5:
            return "stop"
        if wrap_s(T, s_end - _s_at(T, st.x, st.y, st.idx)) < 0:
            return None
        t += 0.02
    return "slow"


# ---- 배치 검사 ----

def widest_gap(T: Track, items: list[Item], s0: float) -> float:
    """s0 에서 차 길이만큼 앞뒤에 걸친 장애물을 빼고 벽~벽 사이 가장 넓은 틈 (m)."""
    i = idx_at_s(T, s0)
    occ = sorted((o.off - o.w / 2, o.off + o.w / 2) for o in items if abs(wrap_s(T, o.s - s0)) < CAR_L / 2 + o.w / 2)
    edge, best = -T.wall_right_m[i], 0.0
    for a, b in occ:
        best = max(best, a - edge)
        edge = max(edge, b)
    return max(best, T.wall_left_m[i] - edge)


def check(T: Track, items: list[Item], o: Item, car: Car, running: bool) -> Check | None:
    """놓아도 되면 None. items 는 o 를 넣은(옮긴) 뒤의 목록, car 는 지금 차, running 은 출발 뒤(race go)인가."""
    w = o.w
    scale = T.real_m / T.lap_m
    if abs(o.off) > _wall(T, o.i, _sign(o.off) or 1) - w / 2:
        return Check("OUT_OF_TRACK", "벽 밖에는 놓을 수 없어요")
    if touching(T, car, [o]):
        return Check("ON_CAR", "차와 겹치는 자리예요")
    ds = -(CAR_L / 2 + w / 2)
    while ds <= CAR_L / 2 + w / 2 + 1e-6:
        gap = widest_gap(T, items, o.s + ds)
        if gap < NEED:
            return Check("NO_GAP", f"차가 지나갈 틈이 없어요 — 가장 넓은 틈 {gap * scale:.1f} m < 필요 {NEED * scale:.1f} m",
                         gap_m=round(gap, 4), need_m=NEED)
        ds += 0.03
    rel = sorted(0.0 if q is o else wrap_s(T, q.s - o.s) for q in items)
    k0 = rel.index(0.0)
    lo = hi = 0.0
    k = k0 - 1
    while k >= 0 and rel[k + 1] - rel[k] <= LOOK_M + 1.5:
        lo = rel[k]
        k -= 1
    k = k0 + 1
    while k < len(rel) and rel[k] - rel[k - 1] <= LOOK_M + 1.5:
        hi = rel[k]
        k += 1
    s0 = o.s + lo - LOOK_M - 0.8
    if trial(T, car_at_s(T, s0, _v_prof(T)[idx_at_s(T, s0)]), items, o.s + hi + 0.6):
        return Check("TOO_DENSE", "틈은 있지만 장애물이 너무 촘촘해 차가 지나가지 못해요")
    if running:
        a = wrap_s(T, o.s + lo - _s_at(T, car.x, car.y, car.idx))
        if -0.3 < a < LOOK_M + 0.8 and trial(T, replace(car, touched=False), items, o.s + hi + 0.6):
            return Check("TOO_CLOSE", "차 바로 앞이라 피할 거리가 부족해요")
    return None
