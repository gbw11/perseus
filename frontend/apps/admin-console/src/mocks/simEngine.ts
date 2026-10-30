/**
 * 시뮬레이터 엔진 mock — 실제 시뮬레이터(platform/apps/simulator, -261)와 자율주행 앱(-259)이 할 일을
 * 콘솔 mock 안에서 흉내 낸다. 시뮬레이터 시연 시나리오 §3·§4-2 와 MQTT 계약 v1.4 §11 기준 (웹 시안에서 옮김).
 * 값은 모의 단위 (m, m/s, 초). 화면은 트랙 real_m 비율로 실제 단위로 보인다.
 */
import type { TrackSource } from './tracks';

// ---- 크기·속도 (시나리오 §4-2) ----
export const LW = 0.6;
export const CURB = 0.06;
export const ROAD = LW / 2 + CURB;
const GRASS = 0.34;
const GRASS_CORNER = 0.68;
const APRON = 0.03;
export const CAR_HALF = 0.1;
export const CAR_L = 0.42;
export const MAX_V = 1.0;
const ACC = (v: number) => 0.26 * (1 - (0.6 * Math.max(0, v)) / MAX_V);
export const BRAKE = 1.0;
/** 놓았을 때 감속 (수동) */
export const COAST = 0.12;
/** 후진 최고 속도 (수동) */
export const REV_V = 0.2;
const ACC_REV = 0.2;
const A_LAT = 0.22;
const BRAKE_PLAN = 0.5;
const AVOID_V = 0.4;
export const SLOW_V = 0.3;
const STOP_M = 0.8;
export const LOOK_M = 4.0;
const SAFE = 0.03;
export const NEED = 2 * (CAR_HALF + SAFE);
/** 장애물 종류마다 폭 (m) — 계약 v1.4 obstacle.add kind */
export const KIND_W = { cone: 0.18, box: 0.3, tires: 0.26 } as const;
export type Kind = keyof typeof KIND_W;

export interface Track {
  id: string;
  name: string;
  real_m: number;
  C: [number, number][];
  N: number;
  tan: [number, number][];
  nor: [number, number][];
  turn: number[];
  corner: boolean[];
  SL: number[];
  LAP: number;
  WL: number[];
  WR: number[];
  S0: number;
  vProf: number[];
}

export interface Car {
  x: number;
  y: number;
  yaw: number;
  v: number;
  steer: number;
  idx: number;
  wallAt: number;
  touchAt: number;
  touched?: boolean;
}

export interface Obstacle {
  id: string;
  kind: Kind;
  x: number;
  y: number;
  i: number;
  off: number;
  s: number;
}

const inRange = (i: number, a: number, b: number) => (a <= b ? i >= a && i <= b : i >= a || i <= b);

/** 트랙 만들기: 중심선 → 접선·법선·곡률, 구간별 벽 (출발 직선은 바로 벽, 코너 근처 두 배), 자율 속도 계획 */
export function buildTrack(src: TrackSource): Track {
  const C = src.centerline;
  const N = C.length;
  const tan = C.map((_, i): [number, number] => {
    const a = C[(i - 1 + N) % N]!;
    const b = C[(i + 1) % N]!;
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [(b[0] - a[0]) / l, (b[1] - a[1]) / l];
  });
  const nor = tan.map(([x, y]): [number, number] => [-y, x]);
  const turn = C.map((_, i) => {
    const a = tan[(i - 3 + N) % N]!;
    const b = tan[(i + 3) % N]!;
    return Math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]) / 1.2;
  });
  const corner = turn.map((_, i) => {
    let m = 0;
    for (let d = -6; d <= 6; d++) m = Math.max(m, Math.abs(turn[(i + d + N) % N]!));
    return m > 0.35;
  });
  const SL = [0];
  for (let i = 1; i < N; i++)
    SL.push(SL[i - 1]! + Math.hypot(C[i]![0] - C[i - 1]![0], C[i]![1] - C[i - 1]![1]));
  const LAP = SL[N - 1]! + Math.hypot(C[0]![0] - C[N - 1]![0], C[0]![1] - C[N - 1]![1]);
  let bestLen = 0;
  let bestStart = 0;
  for (let i = 0; i < N; i++) {
    let L = 0;
    while (L < N && !corner[(i + L) % N]) L++;
    if (L > bestLen) {
      bestLen = L;
      bestStart = i;
    }
  }
  const S0 = (bestStart + Math.floor(bestLen * 0.3)) % N;
  const straight: [number, number] = [(bestStart + 4) % N, (bestStart + bestLen - 4) % N];
  const at = (i: number, off: number): [number, number] => [
    C[i]![0] + nor[i]![0] * off,
    C[i]![1] + nor[i]![1] * off,
  ];
  const want = C.map((_, i) => {
    if (inRange(i, straight[0], straight[1])) return APRON;
    for (let d = -8; d <= 8; d++) if (corner[(i + d + N) % N]) return GRASS_CORNER;
    return GRASS;
  });
  const smooth = (a: number[], r: number) =>
    a.map((_, i) => {
      let s = 0;
      for (let d = -r; d <= r; d++) s += a[(i + d + N) % N]!;
      return s / (2 * r + 1);
    });
  // 다른 구간과 가까우면 두 구간 사이 가운데에서 벽이 만나게
  const room = (sd: number) =>
    C.map((_, i) => {
      let g = APRON;
      for (; g < GRASS_CORNER; g += 0.02) {
        const o = ROAD + g + 0.05;
        const [x, y] = at(i, sd * o);
        let ok = true;
        for (let j = 0; j < N && ok; j += 2) {
          const dj = Math.min((j - i + N) % N, (i - j + N) % N);
          if (dj > 15 && Math.hypot(C[j]![0] - x, C[j]![1] - y) < o) ok = false;
        }
        if (!ok) break;
      }
      return g;
    });
  // 코너 안쪽은 반경보다 넓히면 벽이 접힌다 → 반경의 75% 안으로
  const inner = (sd: number) =>
    C.map((_, i) => {
      let k = 0;
      for (let d = -8; d <= 8; d++) {
        const t = turn[(i + d + N) % N]!;
        if (Math.sign(t) === sd) k = Math.max(k, Math.abs(t));
      }
      return k > 0.05 ? Math.max(APRON, 0.75 / k - ROAD) : 9;
    });
  const w = smooth(want, 6);
  const fin = (r: number[], cap: number[]) => {
    const m = w.map((v, i) => Math.min(v, r[i]!, cap[i]!));
    const lo = m.map((_, i) => {
      let v = 9;
      for (let d = -3; d <= 3; d++) v = Math.min(v, m[(i + d + N) % N]!);
      return v;
    });
    return smooth(lo, 2).map((v) => Math.round((ROAD + Math.max(APRON, v)) * 1000) / 1000);
  };
  const WL = fin(room(1), inner(1));
  const WR = fin(room(-1), inner(-1));
  // 자율 속도 계획: 코너 √(A_LAT/곡률) 을 넘지 않게, 그 앞에서 BRAKE_PLAN 으로 미리 줄인다
  const vc = turn.map((k) => Math.min(MAX_V, Math.sqrt(A_LAT / Math.max(0.02, Math.abs(k)))));
  const vProf = [...vc];
  for (let pass = 0; pass < 2; pass++)
    for (let i = N - 1; i >= 0; i--) {
      const j = (i + 1) % N;
      const ds = Math.hypot(C[j]![0] - C[i]![0], C[j]![1] - C[i]![1]);
      vProf[i] = Math.min(vc[i]!, Math.sqrt(vProf[j]! ** 2 + 2 * BRAKE_PLAN * ds));
    }
  return {
    id: src.id,
    name: src.name,
    real_m: src.real_m,
    C,
    N,
    tan,
    nor,
    turn,
    corner,
    SL,
    LAP,
    WL,
    WR,
    S0,
    vProf,
  };
}

// ---- 기하 도우미 ----
export const atT = (T: Track, i: number, off: number): [number, number] => [
  T.C[i]![0] + T.nor[i]![0] * off,
  T.C[i]![1] + T.nor[i]![1] * off,
];
export function nearestT(T: Track, x: number, y: number, hint?: number): number {
  let best = 0;
  let bd = Infinity;
  const from = hint == null ? 0 : hint - 40;
  const to = hint == null ? T.N : hint + 40;
  for (let j = from; j < to; j++) {
    const i = (j + T.N) % T.N;
    const d = (T.C[i]![0] - x) ** 2 + (T.C[i]![1] - y) ** 2;
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return best;
}
export const wrapS = (T: Track, d: number) => (((d % T.LAP) + T.LAP * 1.5) % T.LAP) - T.LAP / 2;
const sAt = (T: Track, x: number, y: number, i: number) =>
  T.SL[i]! + (x - T.C[i]![0]) * T.tan[i]![0] + (y - T.C[i]![1]) * T.tan[i]![1];
export function idxAtS(T: Track, s: number): number {
  const ss = ((s % T.LAP) + T.LAP) % T.LAP;
  let lo = 0;
  let hi = T.N - 1;
  while (lo < hi) {
    const m = (lo + hi + 1) >> 1;
    if (T.SL[m]! <= ss) lo = m;
    else hi = m - 1;
  }
  return lo;
}
const wallOf = (T: Track, i: number, sd: number) => (sd > 0 ? T.WL[i]! : T.WR[i]!);
export function trackFrame(T: Track, x: number, y: number, hint?: number) {
  const i = nearestT(T, x, y, hint);
  const dx = x - T.C[i]![0];
  const dy = y - T.C[i]![1];
  return { i, off: dx * T.nor[i]![0] + dy * T.nor[i]![1], s: sAt(T, x, y, i) };
}
export function surfaceAt(T: Track, i: number, off: number): 'road' | 'grass' | 'dirt' {
  if (Math.abs(off) <= ROAD) return 'road';
  return T.corner[i] && Math.sign(off) === (-Math.sign(T.turn[i]!) || 1) ? 'dirt' : 'grass';
}
export function carAtS(T: Track, s: number, v: number): Car {
  const i = idxAtS(T, s);
  return {
    x: T.C[i]![0],
    y: T.C[i]![1],
    yaw: Math.atan2(T.tan[i]![1], T.tan[i]![0]),
    v,
    steer: 0,
    idx: i,
    wallAt: -9,
    touchAt: -9,
  };
}

// ---- 인지 → 판단 → 조향·속도 (자율주행 앱 -259 에 넣을 규칙) ----
interface Seen {
  o: Obstacle;
  ahead: number;
  lat: number;
  w: number;
}
interface Perception {
  off: number;
  i: number;
  s: number;
  seen: Seen[];
}
export interface Decision {
  mode: 'cruise' | 'avoid' | 'stop';
  target: number;
  blocking?: Seen[];
  firstAhead?: number | null;
}

export function perceive(T: Track, st: Car, list: Obstacle[]): Perception {
  st.idx = nearestT(T, st.x, st.y, st.idx);
  const i = st.idx;
  const off = (st.x - T.C[i]![0]) * T.nor[i]![0] + (st.y - T.C[i]![1]) * T.nor[i]![1];
  const sc = sAt(T, st.x, st.y, i);
  const seen: Seen[] = [];
  for (const o of list) {
    const ahead = wrapS(T, o.s - sc);
    const w = KIND_W[o.kind];
    if (ahead > -(CAR_L / 2 + w / 2 + 0.05) && ahead < LOOK_M + 0.6) seen.push({ o, ahead, lat: o.off, w });
  }
  seen.sort((a, b) => a.ahead - b.ahead);
  return { off, i, s: sc, seen };
}

function freeLanes(T: Track, obs: Seen[]): [number, number][] {
  let hiL = 9;
  let loR = -9;
  for (const s of obs) {
    hiL = Math.min(hiL, T.WL[s.o.i]! - CAR_HALF - SAFE);
    loR = Math.max(loR, -T.WR[s.o.i]! + CAR_HALF + SAFE);
  }
  let free: [number, number][] = [[loR, hiL]];
  for (const s of obs) {
    const clr = s.w / 2 + CAR_HALF + SAFE;
    const lo = s.lat - clr;
    const hi = s.lat + clr;
    const next: [number, number][] = [];
    for (const [a, b] of free) {
      if (hi <= a || lo >= b) next.push([a, b]);
      else {
        if (lo > a) next.push([a, lo]);
        if (hi < b) next.push([hi, b]);
      }
    }
    free = next.filter(([a, b]) => b - a > 0.005);
  }
  return free;
}

/** 앞으로 D m 안에 옆으로 옮겨 갈 수 있는 거리 (회피 조향 실측 D ≈ 0.38 + 0.8·Δ 에 여유) */
const reach = (D: number) => Math.max(0, (D - 0.6) / 1.1);

/** 판단: 장애물을 묶고, 묶음마다 어느 틈으로 갈지 조합 중 실제로 옮겨 갈 수 있는 것 (시나리오 §4-2) */
export function decide(T: Track, p: Perception, prevT: number | null): Decision {
  const vis = p.seen.filter((s) => s.ahead < LOOK_M);
  if (!vis.length) return { mode: 'cruise', target: 0 };
  const groups: { items: Seen[]; lo: number; hi: number; lanes: [number, number][] }[] = [];
  for (const s of vis) {
    const g = groups[groups.length - 1];
    const lo = s.ahead - s.w / 2;
    const hi = s.ahead + s.w / 2;
    if (g && lo - g.hi < CAR_L) {
      g.items.push(s);
      g.hi = Math.max(g.hi, hi);
    } else groups.push({ items: [s], lo, hi, lanes: [] });
  }
  for (const g of groups)
    g.lanes = freeLanes(T, g.items).map(([a, b]): [number, number] => {
      const e = Math.min(0.05, (b - a) / 2);
      return [a + e, b - e];
    });
  const first = groups[0]!;
  const passing = first.lo < CAR_L / 2;
  const blocking = first.items.filter((s) => Math.abs(s.lat - p.off) < s.w / 2 + CAR_HALF + SAFE);
  let combos: [number, number][][] = [[]];
  for (const g of groups) {
    const next: [number, number][][] = [];
    for (const c of combos) for (const l of g.lanes) next.push([...c, l]);
    combos = next.slice(0, 400);
  }
  const roomOf = (k: number) =>
    k === 0 ? (passing ? 0 : first.lo - CAR_L / 2) : groups[k]!.lo - groups[k - 1]!.hi - CAR_L;
  const clampIn = (x: number, [a, b]: [number, number]) => Math.min(b, Math.max(a, x));
  let best: { cost: number; path: number[] } | null = null;
  for (const lanes of combos) {
    const A: number[] = [];
    let prev = p.off;
    for (const l of lanes) {
      prev = clampIn(prev, l);
      A.push(prev);
    }
    const B = [...A];
    for (let k = B.length - 2; k >= 0; k--) B[k] = clampIn(B[k + 1]!, lanes[k]!);
    for (const xs of [A, B]) {
      let ok = true;
      let cost = 0;
      let pv = p.off;
      xs.forEach((x, k) => {
        const mv = Math.abs(x - pv);
        const l = lanes[k]!;
        const keep = k === 0 && prevT != null && prevT >= l[0] - 0.02 && prevT <= l[1] + 0.02;
        const D = roomOf(k);
        const lim = (keep ? Math.max(reach(D) * 1.6 + 0.06, (D - 0.25) / 0.8) : reach(D)) + 0.015;
        if (mv > lim) ok = false;
        cost +=
          mv +
          0.3 * Math.abs(x) +
          1.5 * Math.max(0, Math.abs(x) + CAR_HALF - ROAD) +
          2 * Math.max(0, mv / lim - 0.5) +
          (k === 0 && prevT != null ? 0.5 * Math.abs(x - prevT) : 0);
        pv = x;
      });
      if (ok && (!best || cost < best.cost)) best = { cost, path: xs };
    }
  }
  if (!best) return { mode: 'stop', target: p.off, blocking: blocking.length ? blocking : first.items };
  let t = best.path[0]!;
  if (!passing) {
    const lane = first.lanes.find(([a, b]) => t >= a - 1e-9 && t <= b + 1e-9);
    if (lane) {
      const c = Math.min(lane[1], Math.max(lane[0], 0));
      if (Math.abs(c - t) < 0.02 || groups.length === 1)
        t = Math.abs(c - p.off) <= Math.abs(t - p.off) + 0.05 ? c : t;
    }
  }
  const busy = blocking.length > 0 || best.path.some((x) => Math.abs(x - p.off) > 0.03);
  return {
    mode: blocking.length || Math.abs(t) > 0.03 ? 'avoid' : 'cruise',
    target: t,
    firstAhead: busy ? first.lo : null,
  };
}

/** 조향 (pure pursuit, 평소 0.8 m 앞·회피 중 0.4 m 앞) + 속도 계획 */
export function control(T: Track, st: Car, p: Perception, d: Decision) {
  const avoid = d.mode !== 'cruise';
  const [tx, ty] = atT(T, (p.i + (avoid ? 2 : 4)) % T.N, d.target);
  const ld = Math.max(0.3, Math.hypot(tx - st.x, ty - st.y));
  const alpha = Math.atan2(ty - st.y, tx - st.x) - st.yaw;
  const ts = Math.max(-0.45, Math.min(0.45, Math.atan2(2 * 0.26 * Math.sin(alpha), ld)));
  if (d.mode === 'stop') {
    const f = d.blocking?.[0];
    return { tv: f && f.ahead > STOP_M + 0.4 ? AVOID_V * 0.5 : 0, ts };
  }
  let tv = MAX_V;
  for (let k = 0; k <= 4; k++) tv = Math.min(tv, T.vProf[(p.i + k) % T.N]!);
  if (d.mode === 'avoid') tv = Math.min(tv, AVOID_V);
  if (d.firstAhead != null)
    tv = Math.min(tv, Math.sqrt(AVOID_V ** 2 + 2 * BRAKE_PLAN * Math.max(0, d.firstAhead - 0.6)));
  tv *= 1 - (0.25 * Math.abs(ts)) / 0.45;
  if (Math.max(Math.abs(p.off), Math.abs(d.target)) + CAR_HALF > ROAD) tv = Math.min(tv, SLOW_V);
  return { tv, ts };
}

export function touching(T: Track, st: Car, list: Obstacle[]): boolean {
  const q = trackFrame(T, st.x, st.y, st.idx);
  return list.some((o) => {
    const w = KIND_W[o.kind];
    return Math.abs(wrapS(T, o.s - q.s)) < CAR_L / 2 + w / 2 && Math.abs(o.off - q.off) < CAR_HALF + w / 2;
  });
}

/** 실제 차처럼: 가속은 서서히(빠를수록 덜), 줄일 때는 decel. 잔디 감속, 벽, 장애물 접촉 */
export function physics(
  T: Track,
  st: Car,
  tv: number,
  ts: number,
  dt: number,
  list: Obstacle[],
  time: number,
  decel = BRAKE,
) {
  const px = st.x;
  const py = st.y;
  const pyaw = st.yaw;
  const dv = tv - st.v;
  if (dv > 0) st.v += Math.min(dv, (st.v < -0.01 ? BRAKE : ACC(st.v)) * dt);
  else st.v -= Math.min(-dv, (st.v > 0.01 ? decel : ACC_REV) * dt);
  st.steer += Math.max(-3 * dt, Math.min(3 * dt, ts - st.steer));
  st.x += st.v * Math.cos(st.yaw) * dt;
  st.y += st.v * Math.sin(st.yaw) * dt;
  st.yaw += (st.v / 0.26) * Math.tan(st.steer) * dt;
  const q = trackFrame(T, st.x, st.y, st.idx);
  const sd = Math.sign(q.off) || 1;
  const lim = wallOf(T, q.i, sd) - CAR_HALF;
  if (Math.abs(q.off) > ROAD && st.v > SLOW_V) st.v = Math.max(SLOW_V, st.v - 1.2 * dt);
  if (Math.abs(q.off) > lim) {
    const back = q.off - sd * lim;
    st.x -= T.nor[q.i]![0] * back;
    st.y -= T.nor[q.i]![1] * back;
    const along = Math.atan2(T.tan[q.i]![1], T.tan[q.i]![0]);
    const into = Math.sin(st.yaw - along) * sd;
    if (into > 0) st.yaw -= sd * Math.asin(Math.min(1, into)) * 0.6;
    st.v *= 0.92;
    st.wallAt = time;
  }
  if (list.length && touching(T, st, list)) {
    st.x = px;
    st.y = py;
    st.yaw = pyaw;
    st.v = 0;
    st.touchAt = time;
    st.touched = true;
  }
}

// ---- 배치 검사 (계약 v1.4: 못 지나가는 자리는 거부) ----
export type RejectCode = 'NO_GAP' | 'TOO_DENSE' | 'TOO_CLOSE' | 'ON_CAR' | 'OUT_OF_TRACK';

function widestGap(T: Track, list: Obstacle[], s0: number): number {
  const i = idxAtS(T, s0);
  const occ = list
    .filter((o) => Math.abs(wrapS(T, o.s - s0)) < CAR_L / 2 + KIND_W[o.kind] / 2)
    .map((o): [number, number] => [o.off - KIND_W[o.kind] / 2, o.off + KIND_W[o.kind] / 2])
    .sort((a, b) => a[0] - b[0]);
  let edge = -T.WR[i]!;
  let best = 0;
  for (const [a, b] of occ) {
    best = Math.max(best, a - edge);
    edge = Math.max(edge, b);
  }
  return Math.max(best, T.WL[i]! - edge);
}

/** 자율주행을 그대로 빨리 돌려 본다: sEnd 를 지나면 통과(null), 서거나 닿으면 실패 */
function trial(T: Track, st: Car, list: Obstacle[], sEnd: number): 'touch' | 'stop' | 'slow' | null {
  const d0 = wrapS(T, sEnd - sAt(T, st.x, st.y, nearestT(T, st.x, st.y, st.idx)));
  let pt: number | null = null;
  for (let t = 0; t < d0 / 0.12 + 6; t += 0.02) {
    const p = perceive(T, st, list);
    const d = decide(T, p, pt);
    const c = control(T, st, p, d);
    pt = d.target;
    physics(T, st, c.tv, c.ts, 0.02, list, 0);
    if (st.touched) return 'touch';
    if (d.mode === 'stop' && st.v < 0.01 && t > 0.5) return 'stop';
    if (wrapS(T, sEnd - sAt(T, st.x, st.y, st.idx)) < 0) return null;
  }
  return 'slow';
}

export interface Check {
  code: RejectCode;
  message: string;
  gap_m?: number;
}

/** 놓아도 되면 null. car·running 은 지금 차와 출발했는지 (달리는 차 바로 앞이면 지금 상태로도 돌려 본다) */
export function checkPlacement(
  T: Track,
  list: Obstacle[],
  o: Obstacle,
  car: Car,
  running: boolean,
  scale: number,
): Check | null {
  const w = KIND_W[o.kind];
  if (Math.abs(o.off) > wallOf(T, o.i, Math.sign(o.off) || 1) - w / 2)
    return { code: 'OUT_OF_TRACK', message: '벽 바깥에는 놓을 수 없어요' };
  if (touching(T, car, [o])) return { code: 'ON_CAR', message: '차가 있는 자리에는 놓을 수 없어요' };
  for (let ds = -(CAR_L / 2 + w / 2); ds <= CAR_L / 2 + w / 2 + 1e-6; ds += 0.03) {
    const gap = widestGap(T, list, o.s + ds);
    if (gap < NEED)
      return {
        code: 'NO_GAP',
        gap_m: gap,
        message: `차가 지나갈 틈이 없어요 — 가장 넓은 틈 ${(gap * scale).toFixed(1)} m < 필요 ${(NEED * scale).toFixed(1)} m`,
      };
  }
  const ds = list.map((q) => (q === o ? 0 : wrapS(T, q.s - o.s))).sort((a, b) => a - b);
  const k0 = ds.indexOf(0);
  let lo = 0;
  let hi = 0;
  for (let k = k0 - 1; k >= 0 && ds[k + 1]! - ds[k]! <= LOOK_M + 1.5; k--) lo = ds[k]!;
  for (let k = k0 + 1; k < ds.length && ds[k]! - ds[k - 1]! <= LOOK_M + 1.5; k++) hi = ds[k]!;
  const s0 = o.s + lo - LOOK_M - 0.8;
  if (trial(T, carAtS(T, s0, T.vProf[idxAtS(T, s0)]!), list, o.s + hi + 0.6))
    return { code: 'TOO_DENSE', message: '틈은 있지만 장애물이 너무 촘촘해 차가 지나가지 못해요' };
  if (running) {
    const a = wrapS(T, o.s + lo - sAt(T, car.x, car.y, car.idx));
    if (a > -0.3 && a < LOOK_M + 0.8 && trial(T, { ...car, touched: false }, list, o.s + hi + 0.6))
      return { code: 'TOO_CLOSE', message: '차 바로 앞이라 피할 거리가 부족해요' };
  }
  return null;
}
