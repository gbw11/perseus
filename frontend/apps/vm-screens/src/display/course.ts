/**
 * Jetson 두 차로 시험 코스 (기본 `jetson-demo`, 안쪽 차로 중심 반경 0.60 m) 와 시연 시나리오 경로.
 * 형상은 시뮬레이터 `platform/apps/simulator/tools/generate_jetson_tracks.py` 의 generate() 와 같은 식이다
 * (feat/common/jetson-course-simulator). 좌표: x 오른쪽·y 위 (m), yaw 반시계 (rad), 진행은 시계 방향.
 * 차 위치(pose)는 시뮬레이터 sim.pose 와 같이 후륜축 기준이다.
 * 실측 전 도식이다: 차량·카메라 값과 회피·주차 경로는 실제 기동 가능성을 보증하지 않는다.
 */
export type P2 = [number, number];

export const TRACK_ID = 'jetson-demo';
export const TRACK_NAME = 'Jetson 기본 코스 (R60cm)';
/** 안쪽 차로 중심 반경, 차로 폭, 도로 반폭 (분리선 = 중심선 기준) */
export const INNER_RADIUS = 0.6;
export const LANE_W = 0.4;
export const ROAD_HALF = 0.4;
/** 전체 바닥 (world bounds) */
export const BOUNDS = { w: 4.5, h: 3.0 };
/** 차량 profile (시뮬레이터 layout.vehicle, 미실측): 후륜축 앞뒤 돌출 0.05 m */
export const VEHICLE = { length: 0.3, width: 0.22, wheelbase: 0.2, rearOverhang: 0.05 };

const R = INNER_RADIUS + LANE_W / 2; // 중심선(분리선) 반경
const OUTER = INNER_RADIUS + 0.6;
const TX = 0.1 + OUTER;
const CY = 0.1 + OUTER;
const HORIZONTAL = 4.3 - 2 * OUTER;
const VERTICAL = 2.8 - 2 * OUTER;
const RX = TX + HORIZONTAL;
const TOP_CY = CY + VERTICAL;
const START_X = 2.56;

export const START_LINE: [P2, P2] = [
  [START_X, 0.5 - ROAD_HALF],
  [START_X, 0.5 + ROAD_HALF],
];
/** 후륜축 초기 pose [x, y, yaw]: 아래 직선 안쪽 차로, 앞범퍼가 출발선 */
export const START_POSE: [number, number, number] = [START_X + 0.25, 0.7, Math.PI];

/** 주차칸 2개 (x 2.7~3.5, 칸마다 0.4 × 0.6 m, 입구 y 1.1) */
const MOUTH = 1.1;
const BACK = MOUTH + 0.6;
export interface Slot {
  id: 'P1' | 'P2';
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}
export const SLOTS: Slot[] = [
  { id: 'P1', x0: 2.7, x1: 3.1, y0: MOUTH, y1: BACK },
  { id: 'P2', x0: 3.1, x1: 3.5, y0: MOUTH, y1: BACK },
];
/** 주차칸 앞 연결 마당 */
export const APRON = { x0: 2.7, x1: 3.5, y0: 0.885, y1: MOUTH };
const RIGHT_JOIN_Y = CY - Math.sqrt((INNER_RADIUS - 0.2) ** 2 - Math.max(0, 3.5 - RX) ** 2);
/** 주차 표시선 (칸 윤곽 + 마당 양옆) */
export const PAINT_LINES: P2[][] = [
  ...SLOTS.map((s): P2[] => [
    [s.x0, s.y0],
    [s.x0, s.y1],
    [s.x1, s.y1],
    [s.x1, s.y0],
    [s.x0, s.y0],
  ]),
  [
    [2.7, MOUTH],
    [2.7, 0.9],
  ],
  [
    [3.5, MOUTH],
    [3.5, RIGHT_JOIN_Y],
  ],
];

export interface Obstacle {
  id: string;
  kind: string;
  x: number;
  y: number;
  w: number;
  h: number;
}
/** 처음 놓는 콘 2개: A 안쪽 차로, B 바깥 차로 */
export const OBSTACLE_SEEDS: Obstacle[] = [
  { id: 'A', kind: 'cone', x: 1.92, y: 0.7, w: 0.18, h: 0.18 },
  { id: 'B', kind: 'cone', x: 1.4, y: 0.3, w: 0.18, h: 0.18 },
];

/** 중심선 구간: 직선 또는 시계 방향 원호 (출발선 중심에서 시작) */
type Piece =
  { kind: 'line'; from: P2; to: P2; length: number } | { kind: 'arc'; c: P2; a0: number; length: number };

const line = (from: P2, to: P2): Piece => ({
  kind: 'line',
  from,
  to,
  length: Math.hypot(to[0] - from[0], to[1] - from[1]),
});
const arc = (c: P2, a0: number): Piece => ({ kind: 'arc', c, a0, length: (R * Math.PI) / 2 });

const PIECES: Piece[] = [
  line([START_X, 0.5], [TX, 0.5]),
  arc([TX, CY], -Math.PI / 2),
  line([TX - R, CY], [TX - R, TOP_CY]),
  arc([TX, TOP_CY], Math.PI),
  line([TX, TOP_CY + R], [RX, TOP_CY + R]),
  arc([RX, TOP_CY], Math.PI / 2),
  line([RX + R, TOP_CY], [RX + R, CY]),
  arc([RX, CY], 0),
  line([RX, CY - R], [START_X, 0.5]),
].filter((p) => p.length > 1e-9);

const PIECE_START = PIECES.reduce<number[]>((acc, p, i) => [...acc, (acc[i] ?? 0) + p.length], [0]);
/** 중심선 한 바퀴 (m) */
export const LAP = PIECE_START[PIECES.length]!;

export interface CoursePoint {
  x: number;
  y: number;
  /** 진행 방향 (rad, 반시계) */
  heading: number;
}

/**
 * 중심선에서 u (출발선부터 진행 방향 m) 만큼 간 점을 진행 방향 오른쪽으로 d 만큼 옮긴 곳.
 * 시계 방향 진행이라 오른쪽 = 안마당 쪽: d = +0.2 안쪽 차로 중심, −0.2 바깥 차로 중심
 */
export function courseAt(u: number, d = 0): CoursePoint {
  let s = ((u % LAP) + LAP) % LAP;
  let i = 0;
  while (i < PIECES.length - 1 && s >= PIECE_START[i + 1]!) i += 1;
  s -= PIECE_START[i]!;
  const p = PIECES[i]!;
  if (p.kind === 'line') {
    const k = s / p.length;
    const heading = Math.atan2(p.to[1] - p.from[1], p.to[0] - p.from[0]);
    return {
      x: p.from[0] + (p.to[0] - p.from[0]) * k + Math.sin(heading) * d,
      y: p.from[1] + (p.to[1] - p.from[1]) * k - Math.cos(heading) * d,
      heading,
    };
  }
  const a = p.a0 - s / R;
  return {
    x: p.c[0] + (R - d) * Math.cos(a),
    y: p.c[1] + (R - d) * Math.sin(a),
    heading: a - Math.PI / 2,
  };
}

/** 오른쪽으로 d 옮긴 고리 (닫힌 다각형, 원호는 step m 간격) */
export function courseLoop(d: number, step = 0.05): P2[] {
  const points: P2[] = [];
  PIECES.forEach((p, i) => {
    const n = p.kind === 'line' ? 1 : Math.max(2, Math.ceil(p.length / step));
    for (let k = 0; k < n; k += 1) {
      const c = courseAt(PIECE_START[i]! + (p.length * k) / n, d);
      points.push([c.x, c.y]);
    }
  });
  return points;
}

const CENTER_SAMPLES = Array.from({ length: Math.ceil(LAP / 0.02) }, (_, i) => courseAt(i * 0.02));

/** 코스 위 한 점의 중심선 기준 위치: 진행 거리 u 와 오른쪽(안마당 쪽) 오프셋 d (m) */
export function courseOffset(x: number, y: number): { u: number; d: number } {
  let best = 0;
  let bestD2 = Infinity;
  CENTER_SAMPLES.forEach((c, i) => {
    const d2 = (c.x - x) ** 2 + (c.y - y) ** 2;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = i;
    }
  });
  const c = CENTER_SAMPLES[best]!;
  return { u: best * 0.02, d: (x - c.x) * Math.sin(c.heading) - (y - c.y) * Math.cos(c.heading) };
}

// ── 시연 시나리오 경로 (후륜축 궤적) ──
// ① 콘 A(안쪽 차로) 앞에서 바깥 차로로, 콘 B(바깥 차로) 앞에서 안쪽 차로로 돌아오는 S자
// ② 왼쪽 직선에서 수동 전환, 위 직선에서 자동 복귀 ③ 오른쪽 위 코너에서 VM1 장애 → VM2 승계
// ④ 오른쪽 아래 코너에서 마당을 건너 P1 에 앞으로 들어가 선다

/** 0 → 1 코사인 이음 (양 끝 기울기 0) */
const ease = (k: number) => (1 - Math.cos(Math.PI * Math.max(0, Math.min(1, k)))) / 2;

/**
 * 진행 거리 u 에서의 차로 오프셋 (콘 회피 S자). 두 콘 사이(0.52 m)가 짧아 차로 중심까지 다 옮기지 않고,
 * 차체가 콘 A 옆을 지날 때는 분리선 바깥(d < 0), 콘 B 옆을 지날 때는 안쪽(d > 0)에 있도록 넘나든다
 */
export function laneOffsetAt(u: number): number {
  if (u < -0.25) return 0.2;
  if (u < 0.45) return 0.2 - 0.32 * ease((u + 0.25) / 0.7);
  if (u < 1.3) return -0.12 + 0.32 * ease((u - 0.45) / 0.85);
  return 0.2;
}

const START_U = -(START_POSE[0] - START_X);
/** 오른쪽 아래 코너(마지막 원호)에서 60° 돈 곳에서 차로를 벗어나 주차칸으로 */
const BRANCH_U = PIECE_START[PIECES.length - 2]! + R * (Math.PI / 3);
const P1 = SLOTS[0]!;
const PARK_X = (P1.x0 + P1.x1) / 2;
/** 주차 끝 후륜축: 앞범퍼가 칸 뒤 선 3 cm 앞 */
const PARK_END: P2 = [PARK_X, P1.y1 - 0.03 - (VEHICLE.length - VEHICLE.rearOverhang)];

export interface PathPoint {
  s: number;
  x: number;
  y: number;
  yaw: number;
}

function buildPath(): {
  points: PathPoint[];
  marks: Record<'manualOn' | 'manualOff' | 'crash' | 'park', number>;
} {
  const raw: P2[] = [];
  const uMarks = { manualOn: 2.6, manualOff: 5.0, crash: 6.15 };
  const markIndex: Partial<Record<'manualOn' | 'manualOff' | 'crash' | 'park', number>> = {};
  for (let u = START_U; u < BRANCH_U; u += 0.02) {
    for (const [key, at] of Object.entries(uMarks) as ['manualOn' | 'manualOff' | 'crash', number][])
      if (markIndex[key] === undefined && u >= at) markIndex[key] = raw.length;
    const c = courseAt(u, laneOffsetAt(u));
    raw.push([c.x, c.y]);
  }
  // 코너에서 마당을 건너 P1 입구까지 3차 베지어, 그다음 칸 안으로 곧게
  const b0 = courseAt(BRANCH_U, 0.2);
  const b3: P2 = [PARK_X, P1.y0 + 0.1];
  const b1: P2 = [b0.x + 0.3 * Math.cos(b0.heading), b0.y + 0.3 * Math.sin(b0.heading)];
  const b2: P2 = [b3[0], b3[1] - 0.28];
  markIndex.park = raw.length;
  for (let i = 0; i <= 40; i += 1) {
    const t = i / 40;
    const m = 1 - t;
    raw.push([
      m ** 3 * b0.x + 3 * m * m * t * b1[0] + 3 * m * t * t * b2[0] + t ** 3 * b3[0],
      m ** 3 * b0.y + 3 * m * m * t * b1[1] + 3 * m * t * t * b2[1] + t ** 3 * b3[1],
    ]);
  }
  for (let i = 1; i <= 10; i += 1) raw.push([PARK_X, b3[1] + ((PARK_END[1] - b3[1]) * i) / 10]);

  let s = 0;
  const points = raw.map((p, i) => {
    if (i > 0) s += Math.hypot(p[0] - raw[i - 1]![0], p[1] - raw[i - 1]![1]);
    const a = raw[Math.max(0, i - 1)]!;
    const b = raw[Math.min(raw.length - 1, i + 1)]!;
    return { s, x: p[0], y: p[1], yaw: Math.atan2(b[1] - a[1], b[0] - a[0]) };
  });
  const sAt = (i: number) => points[i]!.s;
  return {
    points,
    marks: {
      manualOn: sAt(markIndex.manualOn!),
      manualOff: sAt(markIndex.manualOff!),
      crash: sAt(markIndex.crash!),
      park: sAt(markIndex.park!),
    },
  };
}

const PATH = buildPath();
export const SCENARIO_PATH: readonly PathPoint[] = PATH.points;
export const PATH_LENGTH = PATH.points[PATH.points.length - 1]!.s;
/** 시나리오 단계가 바뀌는 경로 거리 (m): 수동 전환·자동 복귀·VM1 장애·주차 진입 */
export const SCENARIO_MARKS = PATH.marks;

/** 경로 거리 s 의 후륜축 pose */
export function pathPose(s: number): { x: number; y: number; yaw: number } {
  const pts = SCENARIO_PATH;
  const t = Math.max(0, Math.min(PATH_LENGTH, s));
  let lo = 0;
  let hi = pts.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (pts[mid]!.s <= t) lo = mid;
    else hi = mid;
  }
  const a = pts[lo]!;
  const b = pts[hi]!;
  const k = b.s > a.s ? (t - a.s) / (b.s - a.s) : 0;
  const dyaw = Math.atan2(Math.sin(b.yaw - a.yaw), Math.cos(b.yaw - a.yaw));
  return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k, yaw: a.yaw + dyaw * k };
}

/** 경로 거리 s 부터 앞으로 length m 의 점들 (주행 계획 표시용) */
export function pathAhead(s: number, length = 1.2, step = 0.05): P2[] {
  const out: P2[] = [];
  for (let t = s; t <= Math.min(PATH_LENGTH, s + length) + 1e-9; t += step) {
    const p = pathPose(t);
    out.push([Math.round(p.x * 1000) / 1000, Math.round(p.y * 1000) / 1000]);
  }
  return out;
}

/** 차체 네 모서리 (후륜축 pose, 미실측 profile) */
export function carCorners(pose: { x: number; y: number; yaw: number }): P2[] {
  const c = Math.cos(pose.yaw);
  const s = Math.sin(pose.yaw);
  const hw = VEHICLE.width / 2;
  return (
    [
      [-VEHICLE.rearOverhang, -hw],
      [VEHICLE.length - VEHICLE.rearOverhang, -hw],
      [VEHICLE.length - VEHICLE.rearOverhang, hw],
      [-VEHICLE.rearOverhang, hw],
    ] as P2[]
  ).map(([f, l]) => [pose.x + f * c - l * s, pose.y + f * s + l * c]);
}

/** 후륜축 pose 의 차체가 주차칸 안에 다 들어갔는지 */
export function insideSlot(pose: { x: number; y: number; yaw: number }, slot: Slot): boolean {
  return carCorners(pose).every(([x, y]) => x > slot.x0 && x < slot.x1 && y > slot.y0 && y < slot.y1);
}

/** 차 앞 (진행 방향) 가장 가까운 장애물: 차 기준 앞 거리·옆 거리 (m). 앞 range m·옆 0.35 m 안만 */
export function obstacleAhead(
  pose: { x: number; y: number; yaw: number },
  obstacles: readonly Obstacle[],
  range = 1.0,
): { obstacle: Obstacle; ahead: number; side: number } | null {
  const c = Math.cos(pose.yaw);
  const s = Math.sin(pose.yaw);
  let best: { obstacle: Obstacle; ahead: number; side: number } | null = null;
  for (const o of obstacles) {
    const dx = o.x - pose.x;
    const dy = o.y - pose.y;
    // 앞범퍼부터 장애물 가까운 면까지
    const ahead = dx * c + dy * s - (VEHICLE.length - VEHICLE.rearOverhang) - o.w / 2;
    const side = dx * s - dy * c;
    if (ahead > -0.1 && ahead < range && Math.abs(side) < 0.35 && (!best || ahead < best.ahead))
      best = { obstacle: o, ahead, side };
  }
  return best;
}
