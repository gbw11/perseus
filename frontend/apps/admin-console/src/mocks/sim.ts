/**
 * 시뮬레이터 mock (VITE_MOCK) — MQTT 계약 v1.4 §11 sim.pose·sim.world·sim.command.result 를 흉내 낸다 (시뮬레이터 구성, 데모 기본).
 * 실제는 platform/apps/simulator 가 내고 백엔드가 WS /ws/v1/sim(WS-06)으로 중계한다.
 * 차는 실제 서킷(레드불링·몬차 1/50)을 달린다: 자율이면 자율주행 규칙(회피·속도 계획, simEngine), 수동이면 콘솔 키,
 * 둘 다 아니면 선다. 출발은 F1 출발 신호(빨간 불 다섯 개가 모두 꺼지면). 시나리오 §3·§4-2.
 */
import type { SimPose, SimWorld } from '@xenova/ui';
import {
  atT,
  BRAKE,
  buildTrack,
  COAST,
  REV_V,
  carAtS,
  checkPlacement,
  control,
  decide,
  KIND_W,
  LOOK_M,
  MAX_V,
  perceive,
  physics,
  surfaceAt,
  trackFrame,
  wrapS,
  CAR_HALF,
  CAR_L,
  type Car,
  type Kind,
  type Obstacle,
  type RejectCode,
  type Track,
} from './simEngine';
import { TRACKS } from './tracks';

export interface SimSnapshot {
  available: boolean;
  pose: SimPose | null;
  world: SimWorld | null;
  pose_age_ms: number | null;
}

/** MQTT-18 sim.command.result (계약 v1.4) */
export interface SimCommandResult {
  request_id: string;
  op: string;
  ok: boolean;
  code: RejectCode | 'INVALID' | null;
  message: string | null;
}

interface Race {
  phase: 'lights' | 'go';
  /** 출발 신호 시작 (화면 시계 ms) */
  t0: number;
  outAt: number;
  lights: number;
  lap: number;
  lapStart: number;
  last: number | null;
  best: number | null;
  half: boolean;
  jump: boolean;
  prog: number | null;
}

export interface SimState {
  T: Track;
  car: Car;
  race: Race;
  obstacles: Obstacle[];
  revision: number;
  estop: boolean;
  /** 시뮬레이터 시간 (초) */
  time: number;
  planTarget: number | null;
  seq: number;
  /** 시연용 장애물을 다시 놓을 시각 (mock 만) */
  nextDemoAt: number;
  /** 차선 오프셋 (m, + 왼쪽) — 인지 대역 lane_offset_m 흉내 */
  lateral: number;
}

/** 시뮬레이터 시간 배율 — 화면에서 보기 좋은 빠르기 (시안의 ×1). sim.world.time_scale 로 알린다 */
export const SIM_TIME_SCALE = 3;
/** 시연용 장애물: 30초마다 차 앞쪽에 다시 놓는다 (콘솔 트랙 조작 -267 이 생기면 운영자가 놓는다) */
const DEMO_EVERY_MS = 30000;
const SUBSTEP = 0.01;
const built = new Map<string, Track>();
function trackOf(id: string): Track {
  const src = TRACKS.find((t) => t.id === id) ?? TRACKS[0]!;
  let T = built.get(src.id);
  if (!T) {
    T = buildTrack(src);
    built.set(src.id, T);
  }
  return T;
}
const scaleOf = (T: Track) => T.real_m / T.LAP;

function newRace(now: number): Race {
  return {
    phase: 'lights',
    t0: now + 700,
    outAt: now + 700 + 4000 + 200 + Math.random() * 2800,
    lights: 0,
    lap: 0,
    lapStart: 0,
    last: null,
    best: null,
    half: false,
    jump: false,
    prog: null,
  };
}

const gridCar = (T: Track): Car => carAtS(T, T.SL[T.S0]! - 0.35, 0);

export function initialSim(now: number, trackId = TRACKS[0]!.id): SimState {
  const T = trackOf(trackId);
  const s: SimState = {
    T,
    car: gridCar(T),
    race: newRace(now),
    obstacles: [],
    revision: 1,
    estop: false,
    time: 0,
    planTarget: null,
    seq: 0,
    nextDemoAt: now + DEMO_EVERY_MS,
    lateral: 0,
  };
  // 처음 화면: 출발 뒤 장애물 셋 (지나갈 수 있는 자리에만)
  for (const [di, off, kind] of [
    [60, 0.05, 'cone'],
    [110, -0.15, 'box'],
    [170, 0.2, 'tires'],
  ] as const)
    place(s, kind, ...atT(T, (T.S0 + di) % T.N, off));
  s.revision = 1;
  return s;
}

/** 장애물 놓기 — 배치 검사(계약 v1.4)를 통과해야 놓인다. 결과 = MQTT-18 */
export function place(
  s: SimState,
  kind: Kind,
  x: number,
  y: number,
  requestId = `mock-${s.seq + 1}`,
): SimCommandResult {
  const f = trackFrame(s.T, x, y);
  const o: Obstacle = { id: `ob-${s.seq + 1}`, kind, x, y, i: f.i, off: f.off, s: f.s };
  const list = [...s.obstacles.slice(s.obstacles.length >= 8 ? 1 : 0), o];
  const why = checkPlacement(s.T, list, o, s.car, s.race.phase === 'go', scaleOf(s.T));
  if (why)
    return { request_id: requestId, op: 'obstacle.add', ok: false, code: why.code, message: why.message };
  s.seq++;
  s.obstacles = list;
  s.revision++;
  return { request_id: requestId, op: 'obstacle.add', ok: true, code: null, message: null };
}

/** 트랙 바꾸기 (계약 v1.4 track.select) — 장애물을 새로 놓고 출발선에서 출발 신호를 처음부터 */
export function selectTrack(s: SimState, trackId: string, now: number): SimState {
  if (!TRACKS.some((t) => t.id === trackId)) return s;
  const next = initialSim(now, trackId);
  next.revision = s.revision + 1;
  next.estop = s.estop;
  return next;
}

/** 다시 출발 (계약 v1.4 race.start) */
export function restartRace(s: SimState, now: number): SimState {
  return { ...s, car: gridCar(s.T), race: newRace(now), planTarget: null };
}

/** 시연용: 차 앞쪽(6 m 넘게)에 장애물을 다시 놓는다. 차 바로 앞 것은 두고, 지나간 것은 치운다 */
function reshuffle(s: SimState) {
  const sc = trackFrame(s.T, s.car.x, s.car.y, s.car.idx).s;
  s.obstacles = s.obstacles.filter((o) => {
    const a = wrapS(s.T, o.s - sc);
    return a > -0.3 && a < 2.5;
  });
  const kinds: Kind[] = ['cone', 'box', 'tires'];
  for (let n = 0; n < 3; n++)
    for (let t = 0; t < 30; t++) {
      const i =
        (s.car.idx + 30 + Math.floor(Math.random() * s.T.N * 0.25) + n * Math.floor(s.T.N * 0.1)) % s.T.N;
      const off = (Math.random() * 2 - 1) * 0.25;
      if (place(s, kinds[Math.floor(Math.random() * 3)]!, ...atT(s.T, i, off)).ok) break;
    }
  s.revision++;
}

const esp32Blocks = (p: ReturnType<typeof perceive>) =>
  p.seen.some(
    (o) =>
      o.ahead > 0 && o.ahead < CAR_L / 2 + o.w / 2 + 0.25 && Math.abs(o.lat - p.off) < o.w / 2 + CAR_HALF,
  );

/**
 * 한 tick (화면 시계 dt 초). driver = 지금 차를 모는 앱 (자율 / 수동 / 없음 → 선다).
 * keys = 수동 입력 (콘솔 시연 제어). 시뮬레이터 시간은 SIM_TIME_SCALE 배로 흐른다.
 */
export function advance(
  prev: SimState,
  now: number,
  dt: number,
  driver: 'autonomous' | 'manual' | null,
  keys: readonly string[] = [],
): SimState {
  const s: SimState = { ...prev, car: { ...prev.car }, race: { ...prev.race } };
  const { T, car, race } = s;
  // 출발 신호 (화면 시계 — 사람이 보는 1초)
  if (race.phase === 'lights') {
    race.lights = now < race.t0 ? 0 : Math.min(5, Math.floor((now - race.t0) / 1000) + 1);
    if (driver === 'manual' && keys.includes('up') && race.lights > 0) race.jump = true;
    if (now >= race.outAt) {
      race.phase = 'go';
      race.lights = 0;
      race.lapStart = s.time;
      race.half = false;
    }
  }
  if (now >= s.nextDemoAt) {
    s.nextDemoAt = now + DEMO_EVERY_MS;
    if (race.phase === 'go') reshuffle(s);
  }
  const total = dt * SIM_TIME_SCALE;
  for (let t = 0; t < total - 1e-9; t += SUBSTEP) {
    const h = Math.min(SUBSTEP, total - t);
    s.time += h;
    const p = perceive(T, car, s.obstacles);
    let tv = 0;
    let ts = 0;
    let decel: number | undefined;
    if (driver === 'autonomous') {
      const d = decide(T, p, s.planTarget);
      s.planTarget = d.target;
      ({ tv, ts } = control(T, car, p, d));
      if (race.phase !== 'go') tv = 0; // 불이 꺼질 때까지 출발선에서 기다린다
    } else if (driver === 'manual') {
      s.planTarget = null;
      const up = keys.includes('up');
      const down = keys.includes('down');
      if (keys.includes('brake') || (up && down)) decel = BRAKE * 1.3;
      else if (up)
        tv = car.v < -0.02 ? 0 : MAX_V; // 꾹 누르면 최고 속도까지 서서히
      else if (down)
        tv = car.v > 0.02 ? 0 : -REV_V; // 달리는 중이면 브레이크, 서 있으면 후진
      else decel = COAST; // 놓으면 천천히
      const steerMax = 0.21 * (1 - 0.45 * Math.min(1, Math.abs(car.v) / MAX_V));
      ts = (keys.includes('left') ? steerMax : 0) - (keys.includes('right') ? steerMax : 0);
      if (tv > 0 && esp32Blocks(p)) tv = 0; // ESP32 근거리 감지 — 바로 앞 장애물이면 전진 차단
    } else s.planTarget = null;
    if (s.estop) {
      tv = 0;
      decel = BRAKE * 1.3;
    }
    physics(T, car, tv, ts, h, s.obstacles, s.time, decel);
  }
  // 랩: 트랙 반을 지난 뒤 출발선을 넘으면 한 바퀴
  const prog = (car.idx - T.S0 + T.N) % T.N;
  if (prog > T.N * 0.4 && prog < T.N * 0.6) race.half = true;
  if (race.phase === 'go' && race.half && race.prog != null && race.prog > T.N - 25 && prog < 25) {
    const lt = s.time - race.lapStart + (race.lap === 0 && race.jump ? 5 : 0);
    race.last = lt;
    race.best = race.best == null ? lt : Math.min(race.best, lt);
    race.lap++;
    race.lapStart = s.time;
    race.half = false;
  }
  race.prog = prog;
  s.lateral = trackFrame(T, car.x, car.y, car.idx).off;
  return s;
}

/** 차 바로 앞 근거리 감지에 장애물이 걸렸나 (vehicle.status flags.obstacle 흉내) */
export function sensing(s: SimState): boolean {
  return esp32Blocks(perceive(s.T, { ...s.car }, s.obstacles));
}

/** 앞쪽 인식 거리(4 m) 안 장애물 수 — 인지 대역 obstacle_count 흉내 */
export function obstaclesAhead(s: SimState): number {
  return perceive(s.T, { ...s.car }, s.obstacles).seen.filter((o) => o.ahead > 0 && o.ahead < LOOK_M).length;
}

export function setEstop(s: SimState, estop: boolean): SimState {
  return s.estop === estop ? s : { ...s, estop, revision: s.revision + 1 };
}

const r3 = (x: number) => Math.round(x * 1000) / 1000;
const r4 = (x: number) => Math.round(x * 10000) / 10000;
const trackPayload = new Map<string, SimWorld['track']>();
function trackOfPayload(T: Track): SimWorld['track'] {
  let p = trackPayload.get(T.id);
  if (!p) {
    p = {
      shape: 'centerline',
      id: T.id,
      name: T.name,
      real_m: T.real_m,
      lane_width_m: 0.6,
      curb_m: 0.06,
      centerline: T.C.map(([x, y]): [number, number] => [r3(x), r3(y)]),
      wall_left_m: T.WL,
      wall_right_m: T.WR,
      start_index: T.S0,
    };
    trackPayload.set(T.id, p);
  }
  return p;
}
let worldCache: { key: string; world: SimWorld } | null = null;

export function snapshotOf(s: SimState): SimSnapshot {
  const { T, car, race } = s;
  const key = `${T.id}:${s.revision}:${s.estop}:${s.obstacles.length}`;
  if (!worldCache || worldCache.key !== key)
    worldCache = {
      key,
      world: {
        revision: s.revision,
        track: trackOfPayload(T),
        tracks: TRACKS.map((t) => ({ id: t.id, name: t.name, real_m: t.real_m })),
        car: { length_m: CAR_L, width_m: CAR_HALF * 2 },
        time_scale: SIM_TIME_SCALE,
        obstacles: s.obstacles.map((o) => ({
          id: o.id,
          kind: o.kind,
          x: r4(o.x),
          y: r4(o.y),
          w: KIND_W[o.kind],
        })),
        estop: s.estop,
      },
    };
  const f = trackFrame(T, car.x, car.y, car.idx);
  const pose: SimPose = {
    x: r4(car.x),
    y: r4(car.y),
    yaw: r4(car.yaw),
    v_mps: r4(car.v),
    steer_rad: r4(car.steer),
    world_revision: s.revision,
    surface: surfaceAt(T, f.i, f.off),
    race: {
      phase: race.phase,
      lights_on: race.phase === 'lights' ? race.lights : 0,
      lap: race.lap,
      lap_ms: race.phase === 'go' ? Math.round((s.time - race.lapStart) * 1000) : 0,
      last_lap_ms: race.last == null ? null : Math.round(race.last * 1000),
      best_lap_ms: race.best == null ? null : Math.round(race.best * 1000),
      jump_start: race.jump,
    },
  };
  return { available: true, pose, world: worldCache.world, pose_age_ms: 0 };
}
