/**
 * 시연 시나리오 "회사 → 집 자율주행" 내비 지도 (가상의 시내, 오프라인). 단위는 지도 m, 북쪽이 위 (SVG y 아래 +).
 * 실제 위치 센서가 없으므로 차 위치 = 트립 거리 × ROUTE_SCALE 을 경로 위에 놓은 추정이다.
 * 경로·도로를 바꾸려면 이 파일만 고친다.
 */
export const MAP_W = 3200;
export const MAP_H = 2200;

/** RC카 1 m 를 지도 몇 m 로 볼지. 한 바퀴(12.3 m)에 지도 약 740 m */
export const ROUTE_SCALE = 60;

export interface Road {
  name: string;
  kind: 'major' | 'minor';
  points: [number, number][];
}

export const ROADS: Road[] = [
  {
    name: '중앙대로',
    kind: 'major',
    points: [
      [0, 700],
      [3200, 700],
    ],
  },
  {
    name: '강변로',
    kind: 'major',
    points: [
      [0, 1880],
      [3200, 1880],
    ],
  },
  {
    name: '한빛로',
    kind: 'major',
    points: [
      [900, 0],
      [900, 1880],
    ],
  },
  {
    name: '청솔로',
    kind: 'major',
    points: [
      [2300, 0],
      [2300, 1880],
    ],
  },
  {
    name: '은행나무길',
    kind: 'minor',
    points: [
      [0, 300],
      [2300, 300],
    ],
  },
  {
    name: '별빛길',
    kind: 'minor',
    points: [
      [0, 1100],
      [3200, 1100],
    ],
  },
  {
    name: '소담길',
    kind: 'minor',
    points: [
      [900, 1500],
      [3200, 1500],
    ],
  },
  {
    name: '마루길',
    kind: 'minor',
    points: [
      [400, 0],
      [400, 1880],
    ],
  },
  {
    name: '누리길',
    kind: 'minor',
    points: [
      [1500, 0],
      [1500, 1880],
    ],
  },
  {
    name: '새솔길',
    kind: 'minor',
    points: [
      [2800, 300],
      [2800, 1880],
    ],
  },
  {
    name: '',
    kind: 'minor',
    points: [
      [2300, 300],
      [3200, 300],
    ],
  },
];

export const RIVER: [number, number][] = [
  [0, 1960],
  [800, 1930],
  [1600, 1975],
  [2400, 1940],
  [3200, 1965],
  [3200, 2200],
  [0, 2200],
];

export const PARKS = [
  { name: '한빛공원', x: 1560, y: 1160, w: 680, h: 280 },
  { name: '', x: 2860, y: 760, w: 300, h: 280 },
];

export const PLACES = {
  origin: { name: '회사', x: 400, y: 300 },
  destination: { name: '집', x: 2800, y: 1720 },
};

/** 회사 → 집 경로 (교차로마다 꼭짓점). road = 그 꼭짓점부터 다음 꼭짓점까지 달리는 도로 */
export const ROUTE: { x: number; y: number; road: string }[] = [
  { x: 400, y: 300, road: '은행나무길' },
  { x: 900, y: 300, road: '한빛로' },
  { x: 900, y: 700, road: '중앙대로' },
  { x: 2300, y: 700, road: '청솔로' },
  { x: 2300, y: 1500, road: '소담길' },
  { x: 2800, y: 1500, road: '새솔길' },
  { x: 2800, y: 1720, road: '' },
];

const SEGMENT_LENGTHS = ROUTE.slice(1).map((p, i) => Math.hypot(p.x - ROUTE[i]!.x, p.y - ROUTE[i]!.y));
export const ROUTE_LENGTH = SEGMENT_LENGTHS.reduce((a, b) => a + b, 0);

export interface RoutePosition {
  x: number;
  y: number;
  /** 진행 방향 (deg, 동쪽 0, 시계 방향 +) */
  heading: number;
  traveled: number;
  remaining: number;
  segment: number;
  arrived: boolean;
  next: { turn: 'left' | 'right'; inM: number; road: string } | null;
}

/** 지도 경로 위 위치. 도착하면 목적지에 멈춘다 */
export function routePosition(mapDistance: number): RoutePosition {
  const d = Math.max(0, Math.min(ROUTE_LENGTH, mapDistance));
  let acc = 0;
  for (let i = 0; i < SEGMENT_LENGTHS.length; i += 1) {
    const len = SEGMENT_LENGTHS[i]!;
    if (d <= acc + len || i === SEGMENT_LENGTHS.length - 1) {
      const a = ROUTE[i]!;
      const b = ROUTE[i + 1]!;
      const t = len === 0 ? 0 : (d - acc) / len;
      const after = ROUTE[i + 2];
      let next: RoutePosition['next'] = null;
      if (after) {
        // y 가 아래로 + 인 좌표: 외적 > 0 이면 시계 방향 = 우회전
        const cross = (b.x - a.x) * (after.y - b.y) - (b.y - a.y) * (after.x - b.x);
        next = { turn: cross > 0 ? 'right' : 'left', inM: acc + len - d, road: b.road };
      }
      return {
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
        heading: (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI,
        traveled: d,
        remaining: ROUTE_LENGTH - d,
        segment: i,
        arrived: d >= ROUTE_LENGTH - 1,
        next,
      };
    }
    acc += len;
  }
  const last = ROUTE[ROUTE.length - 1]!;
  return {
    x: last.x,
    y: last.y,
    heading: 0,
    traveled: d,
    remaining: 0,
    segment: ROUTE.length - 2,
    arrived: true,
    next: null,
  };
}

/** 건물 블록 (도로 사이 빈 칸에 결정적으로 배치) */
export const BLOCKS: { x: number; y: number; w: number; h: number }[] = (() => {
  const xs = [0, 400, 900, 1500, 2300, 2800, 3200];
  const ys = [0, 300, 700, 1100, 1500, 1880];
  const out: { x: number; y: number; w: number; h: number }[] = [];
  for (let i = 0; i < xs.length - 1; i += 1) {
    for (let j = 0; j < ys.length - 1; j += 1) {
      const x0 = xs[i]! + 40;
      const y0 = ys[j]! + 40;
      const w = xs[i + 1]! - xs[i]! - 80;
      const h = ys[j + 1]! - ys[j]! - 80;
      const inPark = PARKS.some((p) => x0 < p.x + p.w && x0 + w > p.x && y0 < p.y + p.h && y0 + h > p.y);
      if (w < 120 || h < 120 || inPark) continue;
      const cols = Math.max(1, Math.floor(w / 170));
      const rows = Math.max(1, Math.floor(h / 150));
      for (let c = 0; c < cols; c += 1) {
        for (let r = 0; r < rows; r += 1) {
          const seed = (i * 31 + j * 17 + c * 7 + r * 3) % 5;
          out.push({
            x: x0 + (c * w) / cols + 8,
            y: y0 + (r * h) / rows + 8,
            w: w / cols - 16 - seed * 6,
            h: h / rows - 16 - ((seed * 7) % 4) * 6,
          });
        }
      }
    }
  }
  return out;
})();

/** 도로 종류별 한 방향 차로 수 (큰길 3, 골목 2) */
export const LANES = { major: 3, minor: 2 } as const;

/** 지금 달리는 도로의 차로 수 */
export function lanesOf(roadName: string): number {
  const road = ROADS.find((r) => r.name === roadName);
  return road ? LANES[road.kind] : LANES.minor;
}

export interface LaneGuide {
  /** 차로마다 화살표와 권장 여부 (왼쪽 1차로부터) */
  lanes: { arrow: 'left' | 'straight' | 'right'; recommended: boolean }[];
  turn: 'left' | 'right';
  inM: number;
}

/**
 * 다음 회전 전 차로 안내: 좌회전은 1차로(가장 왼쪽), 우회전은 가장 바깥(오른쪽) 차로.
 * 교차로 showWithinM 안쪽에서만 보인다 (그 밖에는 null).
 */
export function laneGuide(pos: RoutePosition, showWithinM = 600): LaneGuide | null {
  if (!pos.next || pos.arrived || pos.next.inM > showWithinM) return null;
  const count = lanesOf(ROUTE[pos.segment]!.road);
  const turn = pos.next.turn;
  const lanes = Array.from({ length: count }, (_, i) => {
    const turning = turn === 'left' ? i === 0 : i === count - 1;
    return { arrow: turning ? turn : ('straight' as const), recommended: turning };
  });
  return { lanes, turn, inM: pos.next.inM };
}

export interface Intersection {
  /** 경로 시작부터 이 교차로 가운데까지 지도 거리 (m) */
  at: number;
  /** 가로지르는 도로 */
  crossRoad: string;
  crossLanes: number;
  /** 이 교차로에서 경로가 꺾이면 방향 */
  turn: 'left' | 'right' | null;
}

/** 경로가 지나는 모든 교차로 (회전 교차로 + 직진으로 지나는 교차로). 신호등은 교차로에만 있다 */
export const INTERSECTIONS: Intersection[] = (() => {
  const out: Intersection[] = [];
  let acc = 0;
  for (let i = 0; i < ROUTE.length - 1; i += 1) {
    const a = ROUTE[i]!;
    const b = ROUTE[i + 1]!;
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    const horizontal = a.y === b.y;
    for (const road of ROADS) {
      if (road.name === a.road) continue;
      const [[rx0, ry0], [rx1, ry1]] = [road.points[0]!, road.points[road.points.length - 1]!];
      const crossesHere = horizontal
        ? rx0 === rx1 &&
          Math.min(ry0, ry1) <= a.y &&
          a.y <= Math.max(ry0, ry1) &&
          rx0 > Math.min(a.x, b.x) &&
          rx0 <= Math.max(a.x, b.x)
        : ry0 === ry1 &&
          Math.min(rx0, rx1) <= a.x &&
          a.x <= Math.max(rx0, rx1) &&
          ry0 > Math.min(a.y, b.y) &&
          ry0 <= Math.max(a.y, b.y);
      if (!crossesHere) continue;
      const along = horizontal ? Math.abs(rx0 - a.x) : Math.abs(ry0 - a.y);
      const atVertex = Math.abs(along - len) < 1 && i < ROUTE.length - 2;
      const turn = atVertex ? (routePosition(acc + len - 1).next?.turn ?? null) : null;
      if (out.some((o) => Math.abs(o.at - (acc + along)) < 1)) continue;
      out.push({ at: acc + along, crossRoad: road.name, crossLanes: LANES[road.kind], turn });
    }
    acc += len;
  }
  return out.sort((x, y) => x.at - y.at);
})();

/** 지금 위치에서 앞에 있는 가장 가까운 교차로 */
export function nextIntersection(mapDistance: number): (Intersection & { inM: number }) | null {
  const next = INTERSECTIONS.find((o) => o.at > mapDistance);
  return next ? { ...next, inM: next.at - mapDistance } : null;
}

/** 회전 교차로를 지난 뒤 지나온 도로를 장면 뒤에 남기는 구간 (지도 m). 좌회전 원호가 끝날 때까지 */
export const PASSED_TURN_M = 70;

/** 장면 차로 폭 (RC m) */
const LANE_RC_M = 0.38;
/**
 * 사거리 가운데에서 정지선까지 지도 거리 (차 앞 범퍼가 서는 곳). 주행 장면이 그리는 정지선과 같다:
 * 가로 도로 반폭(차로 수 × 차로 폭) + 횡단보도 0.36 m
 */
export function stopLineBefore(o: Pick<Intersection, 'crossLanes'>): number {
  return (Math.max(2, o.crossLanes) * LANE_RC_M + 0.36) * ROUTE_SCALE;
}

/** 회전 원호 반지름 (RC m): 우회전은 바깥 차로에서 바로 꺾어 작게, 좌회전은 교차로를 가로질러 크게 */
export const TURN_RADIUS_RC_M = { right: 0.45, left: 0.9 } as const;

/**
 * 회전 교차로의 원호 구간 (경로 지도 거리). 내 차 가운데가 원호를 따라 돌아 들어갈 차로에서 끝나게 시작점을 잡는다
 * (우측 통행: 우회전은 가로 도로의 가까운 쪽 바깥 차로, 좌회전은 먼 쪽 1차로).
 * 지도 거리는 내 차 앞 범퍼 기준이고 차 가운데는 그보다 차 길이 반만큼 뒤다.
 */
export function turnArc(o: Intersection): { start: number; length: number; sign: 1 | -1 } | null {
  if (!o.turn) return null;
  const r = TURN_RADIUS_RC_M[o.turn];
  const half = Math.max(2, o.crossLanes) * LANE_RC_M;
  const laneZ = o.turn === 'right' ? -(half - LANE_RC_M / 2) : LANE_RC_M / 2;
  // 차 길이 반 (carModel CAR.len 4.6 단위 = 0.46 m). 가짜 수집기(node)가 이 파일을 직접 읽어 import 를 늘리지 않는다
  const pivotBehindFront = 0.23;
  return {
    start: o.at + (laneZ - r + pivotBehindFront) * ROUTE_SCALE,
    length: ((r * Math.PI) / 2) * ROUTE_SCALE,
    sign: o.turn === 'right' ? 1 : -1,
  };
}

/**
 * 회전 교차로에서 차가 돈 각도 (deg, 오른쪽 +). 원호 전 0, 원호를 따라 0 → ±90, 지난 뒤 ±90.
 * 회전 전 도로 기준이다 — 장면은 도로 정보가 새 도로로 바뀌면 90° 를 빼서 새 도로 기준으로 그린다.
 * 어느 교차로인지는 도로 정보와 같은 규칙으로 고른다: from(도로 정보를 만든 지도 거리)에서 방금 지난 회전 교차로가
 * PASSED_TURN_M 안이면 그것, 아니면 앞의 다음 회전 교차로. 보간한 거리(mapDistance)가 도로 정보보다 앞서도
 * 같은 교차로를 기준으로 잰다
 */
export function turnAngle(mapDistance: number, from = mapDistance): number {
  const arc = currentTurnArc(from);
  if (!arc) return 0;
  return arc.sign * 90 * Math.max(0, Math.min(1, (mapDistance - arc.start) / arc.length));
}

/** 도로 정보(from)가 가리키는 회전 교차로의 원호 */
function currentTurnArc(from: number) {
  const passed = passedTurn(from);
  const o = passed ?? INTERSECTIONS.find((i) => i.turn && i.at > from);
  return o ? turnArc(o) : null;
}

/**
 * 도로 정보(from)가 가리키는 회전 교차로의 원호 시작부터 달린 거리 (RC m). 원호 전이면 음수, 원호를 지나면 원호 길이보다 크다.
 * 회전 교차로가 없으면 null. 주행 장면은 이 거리로 차의 실제 위치·방향(직진 → 원호 → 새 방향 직진)을 정한다
 */
export function turnTravel(mapDistance: number, from = mapDistance): number | null {
  const arc = currentTurnArc(from);
  return arc ? (mapDistance - arc.start) / ROUTE_SCALE : null;
}

/** 방금 지난 회전 교차로 (가운데를 지난 뒤 PASSED_TURN_M 안). 장면에서 지나온 도로를 뒤에 가로로 남기는 데 쓴다 */
export function passedTurn(
  mapDistance: number,
): (Intersection & { behindM: number; fromRoad: string }) | null {
  for (let i = 0; i < INTERSECTIONS.length; i += 1) {
    const o = INTERSECTIONS[i]!;
    if (!o.turn || mapDistance < o.at || mapDistance - o.at > PASSED_TURN_M) continue;
    const before = routePosition(o.at - 1);
    return { ...o, behindM: mapDistance - o.at, fromRoad: ROUTE[before.segment]!.road };
  }
  return null;
}
