/**
 * 시뮬레이터 mock (VITE_MOCK) — MQTT 계약 v1.3 §11 sim.pose·sim.world 를 흉내 낸다 (시뮬레이터 구성, 데모 기본).
 * 실제는 platform/apps/simulator 가 내고 백엔드가 WS /ws/v1/sim(WS-06)으로 중계한다.
 * 차는 타원 트랙(차선 중심선 3.0 × 1.5 m)을 따라 돈다: 차선 중심에서 옆으로 벗어난 거리(lateral)만 따로 두고,
 * 자율주행이 제어 중이면 그 거리가 0 으로 돌아오고 수동이면 조향대로 벌어진다. 순수 함수라 단위 시험한다.
 */
import type { SimPose, SimWorld } from '@xenova/ui';

export interface SimSnapshot {
  available: boolean;
  pose: SimPose | null;
  world: SimWorld | null;
  pose_age_ms: number | null;
}

export interface SimState {
  /** 타원 매개변수 (rad). 0 = (a, 0), 반시계로 늘어난다 */
  theta: number;
  /** 차선 중심에서 왼쪽(+)으로 벗어난 거리 (m) */
  lateral: number;
  world: SimWorld;
  autoSeq: number;
  /** 다음 시연용 자동 장애물 시각 (mock 만) */
  nextDemoAt: number;
  demoUntil: number | null;
}

export const TRACK: SimWorld['track'] = { shape: 'ellipse', a_m: 3, b_m: 1.5, lane_width_m: 0.4 };
/** 시뮬레이터 근거리 감지 (xenova_simulator SimSettings: 0.5 m, ±30°) */
const SENSE_M = 0.5;
const SENSE_HALF = Math.PI / 6;
/** 시연용 자동 장애물: 처음 8초 뒤, 그다음 30초마다 1.4 m 앞에 놓고 6초 뒤 치운다 */
const DEMO_FIRST_MS = 8000;
const DEMO_EVERY_MS = 30000;
const DEMO_HOLD_MS = 6000;
const DEMO_AHEAD_M = 1.4;

export function initialSim(now: number): SimState {
  return {
    theta: 0,
    lateral: 0,
    world: { revision: 1, track: TRACK, obstacles: [], estop: false },
    autoSeq: 0,
    nextDemoAt: now + DEMO_FIRST_MS,
    demoUntil: null,
  };
}

function centerline(theta: number) {
  const { a_m: a, b_m: b } = TRACK;
  const tx = -a * Math.sin(theta);
  const ty = b * Math.cos(theta);
  const len = Math.hypot(tx, ty);
  return { x: a * Math.cos(theta), y: b * Math.sin(theta), tx: tx / len, ty: ty / len, len };
}

export function pose(s: SimState, v: number, steer: number): SimPose {
  const c = centerline(s.theta);
  // 왼쪽 법선 = 접선을 반시계 90°
  return {
    x: round(c.x - c.ty * s.lateral),
    y: round(c.y + c.tx * s.lateral),
    yaw: round(Math.atan2(c.ty, c.tx)),
    v_mps: round(v),
    steer_rad: round(steer),
    world_revision: s.world.revision,
  };
}

/** 장애물이 차 앞 감지 범위 안인가 (xenova_simulator Vehicle.obstacle_ahead 와 같은 규칙) */
export function sensing(s: SimState): boolean {
  const p = pose(s, 0, 0);
  return s.world.obstacles.some((o) => {
    const dx = o.x - p.x;
    const dy = o.y - p.y;
    if (Math.hypot(dx, dy) - o.w / 2 > SENSE_M + 0.2) return false;
    const bearing = Math.atan2(Math.sin(Math.atan2(dy, dx) - p.yaw), Math.cos(Math.atan2(dy, dx) - p.yaw));
    return Math.abs(bearing) <= SENSE_HALF;
  });
}

/** 앞쪽 2 m 안의 장애물 수 — 인지 대역이 낼 obstacle_count 흉내 */
export function obstaclesAhead(s: SimState): number {
  const p = pose(s, 0, 0);
  return s.world.obstacles.filter((o) => {
    const dx = o.x - p.x;
    const dy = o.y - p.y;
    const forward = dx * Math.cos(p.yaw) + dy * Math.sin(p.yaw);
    return forward > 0 && Math.hypot(dx, dy) < 2;
  }).length;
}

function changed(s: SimState, world: Partial<SimWorld>): SimState {
  return { ...s, world: { ...s.world, ...world, revision: s.world.revision + 1 } };
}

/** 한 tick. driver: 지금 제어 중인 앱 (자율이면 차선으로 돌아오고, 수동이면 조향대로 벌어진다) */
export function advance(
  s: SimState,
  now: number,
  dt: number,
  v: number,
  steer: number,
  driver: 'autonomous' | 'manual' | null,
): SimState {
  const c = centerline(s.theta);
  let lateral = s.lateral;
  if (driver === 'autonomous') lateral *= Math.exp(-dt / 0.8);
  else if (driver === 'manual') lateral = clamp(lateral + v * Math.sin(steer) * dt, -0.6, 0.6);
  let next: SimState = { ...s, theta: s.theta + (v * dt) / c.len, lateral };

  // 시연용 자동 장애물 (mock 만) — 콘솔 트랙 조작(-267)이 생기면 운영자가 놓는다
  if (next.demoUntil == null && now >= next.nextDemoAt) {
    const ahead = centerline(next.theta + DEMO_AHEAD_M / c.len);
    const id = `demo-${next.autoSeq + 1}`;
    next = changed(
      { ...next, autoSeq: next.autoSeq + 1, demoUntil: now + DEMO_HOLD_MS },
      { obstacles: [...next.world.obstacles, { id, x: round(ahead.x), y: round(ahead.y), w: 0.2 }] },
    );
  } else if (next.demoUntil != null && now >= next.demoUntil) {
    next = changed(
      { ...next, demoUntil: null, nextDemoAt: now + DEMO_EVERY_MS },
      { obstacles: next.world.obstacles.filter((o) => !o.id.startsWith('demo-')) },
    );
  }
  return next;
}

export function setEstop(s: SimState, estop: boolean): SimState {
  return s.world.estop === estop ? s : changed(s, { estop });
}

export function snapshotOf(s: SimState, v: number, steer: number): SimSnapshot {
  return { available: true, pose: pose(s, v, steer), world: s.world, pose_age_ms: 0 };
}

const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
const round = (x: number) => Math.round(x * 10000) / 10000;
