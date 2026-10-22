import type { CircuitTrack } from './types';

/** 관중석·나무·텐트·타이어 벽 — 화면만의 장식 (트랙마다 같은 자리에 오도록 고정 난수) */
export interface Deco {
  k: 'oak' | 'spruce' | 'tent_r' | 'tent_b' | 'tires';
  x: number;
  y: number;
  a: number;
  w: number;
}
export interface Spectator {
  row: number;
  x: number;
  y: number;
  c: string;
}

/** sim.world.track 에서 화면이 그리는 데 필요한 값 (한 번 계산해 둔다) */
export interface Circuit {
  track: CircuitTrack;
  C: [number, number][];
  N: number;
  tan: [number, number][];
  /** 왼쪽 법선 */
  nor: [number, number][];
  /** 곡률 (rad/m, + 왼쪽으로 돈다) */
  turn: number[];
  corner: boolean[];
  /** 트랙을 따라 잰 거리 (m) */
  SL: number[];
  lap: number;
  /** 실제 서킷 m / 모의 m — 화면 단위 변환 */
  scale: number;
  WL: number[];
  WR: number[];
  road: number;
  laneW: number;
  start: number;
  standRows: number[];
  crowd: Spectator[];
  deco: Deco[];
  box: { x0: number; x1: number; y0: number; y1: number };
}

/** 관중석은 출발 직선 오른쪽 */
export const STAND_SIDE = -1;
const CROWD = ['#38bdf8', '#a78bfa', '#ef4444', '#f59e0b', '#8b919d', '#4edea3', '#59606b', '#c9ced6'];

const cache = new Map<string, Circuit>();

export function circuitOf(track: CircuitTrack): Circuit {
  const key = `${track.id}:${track.centerline.length}:${track.wall_left_m[0]}:${track.wall_right_m[0]}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const built = build(track);
  cache.set(key, built);
  return built;
}

export const at = (g: Circuit, i: number, off: number): [number, number] => [
  g.C[i]![0] + g.nor[i]![0] * off,
  g.C[i]![1] + g.nor[i]![1] * off,
];
export const wallOf = (g: Circuit, i: number, side: number) => (side > 0 ? g.WL[i]! : g.WR[i]!);

export function nearest(g: Circuit, x: number, y: number, hint?: number): number {
  let best = 0;
  let bd = Infinity;
  const from = hint == null ? 0 : hint - 40;
  const to = hint == null ? g.N : hint + 40;
  for (let j = from; j < to; j++) {
    const i = (j + g.N) % g.N;
    const d = (g.C[i]![0] - x) ** 2 + (g.C[i]![1] - y) ** 2;
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return best;
}

/** 노면: 연석까지 아스팔트, 그 밖은 코너 바깥이면 흙, 아니면 잔디 */
export function surfaceAt(g: Circuit, i: number, off: number): 'road' | 'grass' | 'dirt' {
  if (Math.abs(off) <= g.road) return 'road';
  return g.corner[i] && Math.sign(off) === (-Math.sign(g.turn[i]!) || 1) ? 'dirt' : 'grass';
}

function build(track: CircuitTrack): Circuit {
  const C = track.centerline;
  const N = C.length;
  const tan = C.map((_, i): [number, number] => {
    const a = C[(i - 1 + N) % N]!;
    const b = C[(i + 1) % N]!;
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const l = Math.hypot(dx, dy) || 1;
    return [dx / l, dy / l];
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
  const lap = SL[N - 1]! + Math.hypot(C[0]![0] - C[N - 1]![0], C[0]![1] - C[N - 1]![1]);
  const xs = C.map((p) => p[0]);
  const ys = C.map((p) => p[1]);
  const box = {
    x0: Math.min(...xs) - 1.6,
    x1: Math.max(...xs) + 1.6,
    y0: Math.min(...ys) - 1.6,
    y1: Math.max(...ys) + 1.6,
  };
  const g: Circuit = {
    track,
    C,
    N,
    tan,
    nor,
    turn,
    corner,
    SL,
    lap,
    scale: track.real_m / lap,
    WL: track.wall_left_m,
    WR: track.wall_right_m,
    road: track.lane_width_m / 2 + track.curb_m,
    laneW: track.lane_width_m,
    start: track.start_index,
    standRows: [],
    crowd: [],
    deco: [],
    box,
  };

  // 관중석: 출발 직선(코너 없이 가장 긴 구간) 오른쪽, 다른 트랙 구간 벽에 닿지 않을 만큼만
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
  const s0 = (bestStart + 6) % N;
  const s1 = (bestStart + bestLen - 6) % N;
  const inStand = (i: number) => (s0 <= s1 ? i >= s0 && i <= s1 : i >= s0 || i <= s1);
  g.standRows = C.map((_, i) => {
    if (!inStand(i)) return 0;
    for (let rows = 5; rows > 0; rows--) {
      const [x, y] = at(g, i, (g.WR[i]! + 0.3 + rows * 0.16) * STAND_SIDE);
      let ok = true;
      for (let j = 0; j < N && ok; j++) {
        const dj = Math.min((j - i + N) % N, (i - j + N) % N);
        if (dj > 12 && Math.hypot(C[j]![0] - x, C[j]![1] - y) < Math.max(g.WL[j]!, g.WR[j]!) + 0.12)
          ok = false;
      }
      if (ok) return rows;
    }
    return 0;
  });
  let s = 11;
  const r1 = () => (s = (s * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < N; i++)
    for (let row = 0; row < g.standRows[i]!; row++)
      for (let k = 0; k < 3; k++) {
        if (r1() < 0.12) continue;
        const along = (k / 3) * 0.2;
        const d = (g.WR[i]! + 0.32 + row * 0.16) * STAND_SIDE;
        g.crowd.push({
          row,
          x: C[i]![0] + tan[i]![0] * along + nor[i]![0] * d,
          y: C[i]![1] + tan[i]![1] * along + nor[i]![1] * d,
          c: CROWD[Math.floor(r1() * CROWD.length)]!,
        });
      }

  // 장식: 코너 바깥 타이어 벽, 트랙에서 떨어진 곳 나무·텐트
  const far = (x: number, y: number, m: number) => {
    for (let i = 0; i < N; i += 2) {
      const r = Math.max(g.WL[i]!, g.WR[i]!) + m;
      if ((C[i]![0] - x) ** 2 + (C[i]![1] - y) ** 2 < r * r) return false;
    }
    return true;
  };
  for (let i = 0; i < N; i += 9) {
    if (!corner[i]) continue;
    const sd = -Math.sign(turn[i]!) || 1;
    const [x, y] = at(g, i, (wallOf(g, i, sd) + 0.25) * sd);
    if (far(x, y, 0.1)) g.deco.push({ k: 'tires', x, y, a: 0, w: 0.3 });
  }
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const want = Math.round(((box.x1 - box.x0) * (box.y1 - box.y0)) / 3.2);
  for (let t = 0; t < want * 8 && g.deco.length < want; t++) {
    const x = box.x0 + rnd() * (box.x1 - box.x0);
    const y = box.y0 + rnd() * (box.y1 - box.y0);
    if (!far(x, y, 0.8)) continue;
    const r = rnd();
    if (r < 0.08) g.deco.push({ k: r < 0.04 ? 'tent_r' : 'tent_b', x, y, a: rnd() * 6, w: 0.7 });
    else if (r < 0.55) g.deco.push({ k: 'oak', x, y, a: rnd() * 6, w: 0.75 + rnd() * 0.25 });
    else g.deco.push({ k: 'spruce', x, y, a: rnd() * 6, w: 0.45 + rnd() * 0.15 });
  }
  return g;
}

/** 화면 단위: 모의 m/s → 실제 서킷 km/h */
export const kmh = (g: Circuit, v: number) => v * g.scale * 3.6;
/** 화면 단위: 모의 m → 실제 m */
export const realM = (g: Circuit, m: number) => m * g.scale;
export const fmtLap = (ms: number | null | undefined) =>
  ms == null ? '—' : `${Math.floor(ms / 60000)}:${((ms % 60000) / 1000).toFixed(3).padStart(6, '0')}`;

/** 트랙을 따라 잰 거리 차이 (-한 바퀴/2 ~ +한 바퀴/2) */
export const wrapS = (g: Circuit, d: number) => (((d % g.lap) + g.lap * 1.5) % g.lap) - g.lap / 2;
/** 트랙 좌표: 가장 가까운 중심선 점, 옆 오프셋(+ 왼쪽), 트랙을 따라 잰 거리 */
export function frameOf(g: Circuit, x: number, y: number, hint?: number) {
  const i = nearest(g, x, y, hint);
  const dx = x - g.C[i]![0];
  const dy = y - g.C[i]![1];
  return {
    i,
    off: dx * g.nor[i]![0] + dy * g.nor[i]![1],
    s: g.SL[i]! + dx * g.tan[i]![0] + dy * g.tan[i]![1],
  };
}

export interface AheadObstacle<O> {
  o: O;
  /** 트랙을 따라 차 앞 거리 (모의 m) */
  ahead: number;
  /** 장애물 옆 위치 − 차 옆 위치 (모의 m, + 왼쪽) */
  side: number;
}
/** 차 앞 look m 안의 장애물 — 인지 대역이 시뮬레이터 위치로 계산하는 값과 같은 방식 (시나리오 §2, -268) */
export function obstaclesAhead<O extends { x: number; y: number }>(
  g: Circuit,
  pose: { x: number; y: number },
  obstacles: O[],
  look = 4,
): AheadObstacle<O>[] {
  const car = frameOf(g, pose.x, pose.y);
  return obstacles
    .map((o) => {
      const f = frameOf(g, o.x, o.y);
      return { o, ahead: wrapS(g, f.s - car.s), side: f.off - car.off };
    })
    .filter((a) => a.ahead > -0.3 && a.ahead < look)
    .sort((a, b) => a.ahead - b.ahead);
}
