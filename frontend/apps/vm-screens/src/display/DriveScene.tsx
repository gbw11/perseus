import { CAR, carLamps, carSolids, carWheels, type CarFace } from './carModel';
import { TURN_RADIUS_RC_M } from './cityMap';
import type { RoadInfo, WorldObject } from './driveExtras';
import styles from './combined.module.css';

/**
 * 차 뒤·위에서 내려다본 3D 주행 장면 (SVG 원근 투영). 양산차 주행 화면처럼 내 차는 어둡게, 다른 차는 회색 입체
 * (앞은 본닛, 뒤는 해치백 모양이라 진행 방향이 보인다).
 * 무지개 경로는 차 앞부터 그리고, 회전할 사거리에서는 차가 돌 원호를 따라 그 방향으로 꺾인다 (road.intersection.turn).
 * 도로: 오른쪽이 내 방향 차로(1차로 = 중앙선 옆), 왼쪽이 반대 방향 차로, 사이는 노란 이중 중앙선.
 * 내 차는 늘 화면 가운데(x = 0)에 있고, 차로를 옮기면 도로가 옆으로 움직인다.
 * 사거리가 가까우면 가로지르는 도로·횡단보도·정지선·신호등을 그린다 (신호등은 사거리에만).
 * 회전 교차로에서는 내 차를 그대로 두고 도로·주변을 차 옆의 원호 중심을 축으로 돌린다 — 차가 제자리에서 돌지 않고
 * 원호를 따라 도는 것처럼 보인다 (오른쪽 회전이면 세상이 왼쪽으로 돈다).
 * 장면 단위: RC 트랙 1 m = 10 단위 (차로 폭 3.8 단위 ≈ 0.38 m). 원점은 내 차 뒤 범퍼 가운데, z 는 앞, x 는 오른쪽.
 */
const M = 10;
const VIEW_W = 800;
const VIEW_H = 600;
const CX = VIEW_W / 2;
const HORIZON = 60;
const FOCAL = 780;
const CAM_H = 9;
const CAM_BACK = 18;
const LANE = 3.8;
const NEAR = -13;
/** 도로를 차 뒤로 이만큼까지 그린다 (회전하면 뒤쪽 도로가 옆으로 드러난다) */
const BEHIND = -60;
const FAR = 120;
/** 장면이 도는 중심 (내 차 가운데) */
const PIVOT_Z = CAR.len / 2;
/** 카메라 앞 이만큼보다 가까운 부분은 잘라 낸다 */
const CLIP_D = 1;
const DEFAULT_ROAD: Pick<RoadInfo, 'lanes' | 'lane' | 'oncomingLanes' | 'intersection'> = {
  lanes: 3,
  lane: 2,
  oncomingLanes: 3,
  intersection: null,
};

type P3 = [number, number, number];

/**
 * 그리기 문맥: 조향 휨 + 회전 (내 차는 회전 없이 그린다).
 * 회전은 도로 좌표의 회전 중심 (px, pz) 을 차 기준 (qx, PIVOT_Z) 로 옮기며 돈다. 회전 중심이 차 옆에 있으면
 * 차가 원호를 따라 도는 것처럼 보인다 (중심이 차 가운데면 제자리 회전)
 */
interface View {
  curve: number;
  cos: number;
  sin: number;
  px: number;
  pz: number;
  qx: number;
}

const STILL: Omit<View, 'curve'> = { cos: 1, sin: 0, px: 0, pz: PIVOT_Z, qx: 0 };

function rotate([x, y, z]: P3, v: View): P3 {
  const dx = x - v.px;
  const dz = z - v.pz;
  return [dx * v.cos - dz * v.sin + v.qx, y, dx * v.sin + dz * v.cos + PIVOT_Z];
}

/** 조향에 따라 앞쪽이 옆으로 휘는 정도 (단위/단위²) */
function bendAt(z: number, curve: number) {
  return curve * Math.max(0, z) ** 2;
}

function projectRotated([x, y, z]: P3, curve: number): [number, number] {
  const d = z + CAM_BACK;
  return [CX + (FOCAL * (x + bendAt(z, curve))) / d, HORIZON + (FOCAL * (CAM_H - y)) / d];
}

function project(p: P3, v: View): [number, number] | null {
  const r = rotate(p, v);
  return r[2] + CAM_BACK < CLIP_D ? null : projectRotated(r, v.curve);
}

/** 다각형: 회전 → 카메라 앞 평면으로 자르기 → 투영. 회전해서 뒤로 넘어간 부분이 있어도 남은 부분은 그린다 */
function poly(points: P3[], v: View): string | null {
  const rotated = points.map((p) => rotate(p, v));
  const inside = (p: P3) => p[2] + CAM_BACK >= CLIP_D;
  const clipped: P3[] = [];
  for (let i = 0; i < rotated.length; i += 1) {
    const a = rotated[i]!;
    const b = rotated[(i + 1) % rotated.length]!;
    if (inside(a)) clipped.push(a);
    if (inside(a) !== inside(b)) {
      const t = (CLIP_D - CAM_BACK - a[2]) / (b[2] - a[2]);
      clipped.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, CLIP_D - CAM_BACK]);
    }
  }
  if (clipped.length < 3) return null;
  return clipped
    .map((p) => {
      const [sx, sy] = projectRotated(p, v.curve);
      return `${sx.toFixed(1)},${sy.toFixed(1)}`;
    })
    .join(' ');
}

/** 바닥 위 띠 (x0~x1, z0~z1). 휘는 도로를 따라가도록 z 를 잘게 나눈다 */
function strip(x0: number, x1: number, z0: number, z1: number, v: View, y = 0.004, steps = 12) {
  const zs = Array.from({ length: steps + 1 }, (_, i) => z0 + ((z1 - z0) * i) / steps);
  return poly([...zs.map((z) => [x1, y, z] as P3), ...[...zs].reverse().map((z) => [x0, y, z] as P3)], v);
}

/** 바닥과 평행한 가로 띠 (z0~z1 고정, x 를 잘게 나눔) — 회전해도 휘어지는 가로 도로용 */
function crossStrip(x0: number, x1: number, z0: number, z1: number, v: View, y = 0.01, steps = 24) {
  const xs = Array.from({ length: steps + 1 }, (_, i) => x0 + ((x1 - x0) * i) / steps);
  return poly([...xs.map((x) => [x, y, z0] as P3), ...[...xs].reverse().map((x) => [x, y, z1] as P3)], v);
}

const CAMERA: P3 = [0, CAM_H, -CAM_BACK];

type Face = Omit<CarFace, 'corners' | 'normal'> & { corners: P3[]; normal: P3 };

/** 볼록한 입체의 면 중 카메라를 향한 면을 먼 것부터 (회전 반영) */
function solid(faces: Face[], v: View) {
  return faces
    .map((f) => {
      const [nx, ny, nz] = f.normal;
      const n: P3 = [nx * v.cos - nz * v.sin, ny, nx * v.sin + nz * v.cos];
      const c = f.corners.map((p) => rotate(p, v));
      const cx = c.reduce((s, p) => s + p[0], 0) / c.length;
      const cy = c.reduce((s, p) => s + p[1], 0) / c.length;
      const cz = c.reduce((s, p) => s + p[2], 0) / c.length;
      const toCam: P3 = [CAMERA[0] - cx, CAMERA[1] - cy, CAMERA[2] - cz];
      const facing = n[0] * toCam[0] + n[1] * toCam[1] + n[2] * toCam[2] > 0;
      return { key: f.key, tag: f.tag, facing, depth: Math.hypot(...toCam), points: poly(f.corners, v) };
    })
    .filter((f) => f.facing && f.points)
    .sort((a, b) => b.depth - a.depth);
}

/** 직육면체에서 카메라를 향한 면을 먼 것부터 (회전 반영) */
function box(x0: number, x1: number, y0: number, y1: number, z0: number, z1: number, v: View) {
  const faces: Face[] = [
    {
      key: 'back',
      corners: [
        [x0, y0, z0],
        [x1, y0, z0],
        [x1, y1, z0],
        [x0, y1, z0],
      ],
      normal: [0, 0, -1],
    },
    {
      key: 'back',
      corners: [
        [x0, y0, z1],
        [x1, y0, z1],
        [x1, y1, z1],
        [x0, y1, z1],
      ],
      normal: [0, 0, 1],
    },
    {
      key: 'side',
      corners: [
        [x0, y0, z0],
        [x0, y0, z1],
        [x0, y1, z1],
        [x0, y1, z0],
      ],
      normal: [-1, 0, 0],
    },
    {
      key: 'side',
      corners: [
        [x1, y0, z0],
        [x1, y0, z1],
        [x1, y1, z1],
        [x1, y1, z0],
      ],
      normal: [1, 0, 0],
    },
    {
      key: 'top',
      corners: [
        [x0, y1, z0],
        [x1, y1, z0],
        [x1, y1, z1],
        [x0, y1, z1],
      ],
      normal: [0, 1, 0],
    },
  ];
  return solid(faces, v);
}

/** 차 좌표계: 뒤 범퍼 가운데(o), 앞 방향(f), 오른쪽(r) — 바닥(x, z) 기준 */
interface CarFrame {
  o: [number, number];
  f: [number, number];
  r: [number, number];
}

/** 장면 위치(x, z)는 차가 차지하는 칸의 가까운 쪽 가운데. 진행 방향에 따라 앞·뒤를 놓는다 */
function carFrame(x: number, z: number, heading: WorldObject['heading']): CarFrame {
  switch (heading) {
    case 'oncoming':
      return { o: [x, z + CAR.len], f: [0, -1], r: [-1, 0] };
    case 'cross_left':
      return { o: [x + CAR.len / 2, z + CAR.w / 2], f: [-1, 0], r: [0, 1] };
    case 'cross_right':
    case 'cross':
      return { o: [x - CAR.len / 2, z + CAR.w / 2], f: [1, 0], r: [0, -1] };
    default:
      return { o: [x, z], f: [0, 1], r: [1, 0] };
  }
}

/** 차 좌표 (오른쪽 u, 앞 l, 높이 y) → 장면 좌표 */
function carPoint(c: CarFrame, u: number, l: number, y: number): P3 {
  return [c.o[0] + c.f[0] * l + c.r[0] * u, y, c.o[1] + c.f[1] * l + c.r[1] * u];
}

/** 차 모델 면 → 장면 면 (차 좌표 [u, l, y] 를 놓을 자리·방향으로) */
function placeFaces(faces: CarFace[], c: CarFrame): Face[] {
  return faces.map((f) => ({
    ...f,
    corners: f.corners.map(([u, l, y]) => carPoint(c, u, l, y)),
    normal: [
      c.f[0] * f.normal[1] + c.r[0] * f.normal[0],
      f.normal[2],
      c.f[1] * f.normal[1] + c.r[1] * f.normal[0],
    ],
  }));
}

/**
 * 차 한 대. 앞은 본닛·비스듬한 앞유리, 뒤는 해치백이라 진행 방향이 보인다.
 * 뒤가 보이면 미등, 앞이 보이면 전조등. heading cross(방향 모름)는 앞뒤 구분 없이 그린다
 */
function Car({
  x,
  z,
  heading,
  self,
  view,
}: {
  x: number;
  z: number;
  heading: WorldObject['heading'];
  self?: boolean;
  view: View;
}) {
  const frame = carFrame(x, z, heading);
  const plain = heading === 'cross';
  const hw = CAR.w / 2;
  const model = carSolids(plain);
  const body = solid(placeFaces(model.body, frame), view);
  const cabin = solid(placeFaces(model.glass, frame), view);
  const end = plain
    ? undefined
    : (body.find((f) => f.tag === 'front' || f.tag === 'rear')?.tag as 'front' | 'rear' | undefined);
  const place = (pts: [number, number, number][]) =>
    poly(
      pts.map(([u, l, y]) => carPoint(frame, u, l, y)),
      view,
    );
  const lamps = end ? carLamps(end).map(place) : [];
  // 보이는 옆면의 바퀴
  const wheels = body
    .filter((f) => f.tag === 'left' || f.tag === 'right')
    .flatMap((f) => carWheels(f.tag as 'left' | 'right'))
    .map((w) => ({ tire: place(w.tire), rim: place(w.rim) }));
  const shadow = poly(
    [
      carPoint(frame, -hw - 0.2, -0.3, 0),
      carPoint(frame, hw + 0.2, -0.3, 0),
      carPoint(frame, hw + 0.2, CAR.len + 0.3, 0),
      carPoint(frame, -hw - 0.2, CAR.len + 0.3, 0),
    ],
    view,
  );
  return (
    <g className={self ? styles.selfCar : styles.otherCar} data-heading={heading}>
      {shadow && <polygon className={styles.shadow} points={shadow} />}
      {body.map((f, i) => (
        <polygon key={`b${i}`} className={styles[f.key]} points={f.points!} />
      ))}
      {wheels.map((w, i) => (
        <g key={`w${i}`}>
          {w.tire && <polygon className={styles.tire} points={w.tire} />}
          {w.rim && <polygon className={styles.rim} points={w.rim} />}
        </g>
      ))}
      {cabin.map((f, i) => (
        <polygon key={`c${i}`} className={styles[`cabin_${f.key}`]} points={f.points!} />
      ))}
      {lamps.map(
        (p, i) =>
          p && (
            <polygon
              key={`l${i}`}
              className={end === 'front' ? styles.headLight : styles.tailLight}
              data-lamp={end}
              points={p}
            />
          ),
      )}
    </g>
  );
}

/** 무지개 경로 색 (한 주기), 주기 길이·흐름 배율 (장면 단위) */
const RAINBOW = ['#ff3b5c', '#ff9f1c', '#ffe14d', '#3ddc84', '#2f9bff', '#8b5cf6'];
const RAINBOW_PERIOD = 30;
const RAINBOW_FLOW = 3;
const PATH_AHEAD = 70;
const PATH_SIDE = 60;

type P2 = [number, number];

function rainbowColor(a: number) {
  const t = ((((a / RAINBOW_PERIOD) % 1) + 1) % 1) * RAINBOW.length;
  const i = Math.floor(t);
  const mix = (hex0: string, hex1: string, k: number) =>
    [1, 3, 5]
      .map((o) => {
        const c0 = parseInt(hex0.slice(o, o + 2), 16);
        const c1 = parseInt(hex1.slice(o, o + 2), 16);
        return Math.round(c0 + (c1 - c0) * k);
      })
      .join(',');
  return `rgb(${mix(RAINBOW[i % RAINBOW.length]!, RAINBOW[(i + 1) % RAINBOW.length]!, t - i)})`;
}

/** 곧은 구간을 잘게 나눈 점들 (원근에서 휘어 보이지 않게) — 시작점은 뺀다 */
function segment(from: P2, to: P2, step = 1.5): P2[] {
  const n = Math.max(1, Math.ceil(Math.hypot(to[0] - from[0], to[1] - from[1]) / step));
  return Array.from({ length: n }, (_, i) => {
    const k = (i + 1) / n;
    return [from[0] + (to[0] - from[0]) * k, from[1] + (to[1] - from[1]) * k];
  });
}

/** 원호 위 점들: 각도 t0 → t1 (라디안), at(t) 로 위치 — 시작점은 뺀다 */
function arcPoints(at: (t: number) => P2, t0: number, t1: number, r: number): P2[] {
  if (Math.abs(t1 - t0) < 1e-6) return [];
  const n = Math.max(2, Math.ceil((Math.abs(t1 - t0) * r) / 1.2));
  return Array.from({ length: n }, (_, i) => at(t0 + ((t1 - t0) * (i + 1)) / n));
}

/**
 * 회전 교차로에서 내 차의 원호 (도로 좌표). 차는 반지름 r, 중심 (s·r, cz) 원을 따라 돈다.
 * before: 회전 전 도로 기준 (cz = 원호 시작), done = 이미 돈 각도 (0 ~ 90°).
 * after: 새 도로 기준 (cz = 원호 끝), done 만큼 돌았고 남은 각도는 90° - done.
 * x, z, yaw: 차 가운데의 실제 위치와 방향 (도로 정보는 차가 도로 가운데 선을 따라간다고 보고 놓으므로, 그 기준에서 원호만큼 벗어난다)
 */
interface TurnPose {
  s: 1 | -1;
  r: number;
  cz: number;
  phase: 'before' | 'after';
  done: number;
  x: number;
  z: number;
  yaw: number;
}

/**
 * 회전 교차로에서 차의 실제 자세. t = 원호 시작부터 달린 거리 (장면 단위, 원호 전 음수).
 * 회전 전 도로 기준: 원호 전 직진 → 원호 → 원호를 다 돌면 새 방향으로 직진. 새 도로 기준(after): 남은 원호 → 직진
 */
function turnPose(dir: 'left' | 'right', t: number, phase: 'before' | 'after'): TurnPose | null {
  const s = dir === 'right' ? 1 : -1;
  const r = TURN_RADIUS_RC_M[dir] * M;
  const length = (r * Math.PI) / 2;
  if (phase === 'before') {
    const cz = PIVOT_Z - t;
    const th = Math.max(0, Math.min(t, length)) / r;
    const extra = Math.max(0, t - length);
    return {
      s,
      r,
      cz,
      phase,
      done: th,
      // 원호 전에는 도로 정보 그대로 (도로 가운데 선 위), 원호부터 실제 위치
      x: s * (r - r * Math.cos(th) + extra),
      z: t <= 0 ? PIVOT_Z : cz + r * Math.sin(th),
      yaw: s * th,
    };
  }
  // 원호를 다 돌았거나, 원호 시작 전인데 도로 정보가 새 도로인 (어긋난) 입력이면 회전하지 않은 장면으로 둔다
  const left = length - t;
  if (left <= 0 || left > length) return null;
  const b = left / r;
  const cz = PIVOT_Z + left;
  return {
    s,
    r,
    cz,
    phase,
    done: Math.PI / 2 - b,
    x: s * (r - r * Math.cos(b)),
    z: cz - r * Math.sin(b),
    yaw: -s * b,
  };
}

/** 무지개가 한 차로 옮겨 가는 길이 (장면 단위, 앞으로). 여러 차로면 그만큼 길게 */
const LANE_SHIFT = 12;
const shiftLength = (dx: number) => LANE_SHIFT * Math.max(1, Math.abs(dx) / LANE);

/** 0 → 1 부드럽게 (양 끝 기울기 0) */
function ease(k: number) {
  const c = Math.max(0, Math.min(1, k));
  return c * c * (3 - 2 * c);
}

/** 차로 계획: dx = 옮겨 갈 차로까지 옆 거리, back = backZ 부터 backDx 로 돌아감 (추월). 장면 단위 */
interface LanePlan {
  dx: number;
  backDx: number | null;
  backZ: number | null;
}

/** 차로 계획에서 앞 거리 z 의 옆 위치 */
function planX(plan: LanePlan, z: number) {
  let x = plan.dx * ease((z - CAR.len) / shiftLength(plan.dx));
  if (plan.backDx !== null && plan.backZ !== null) {
    const back = plan.backDx - plan.dx;
    x += back * ease((z - plan.backZ) / shiftLength(back));
  }
  return x;
}

/** 차로 계획대로 차 앞에서 S 자로 옮겨 가고, 돌아올 곳부터 다시 S 자로 돌아오는 선 (z = PIVOT_Z ~ toZ) */
function lanePath(plan: LanePlan, toZ = PATH_AHEAD): P2[] {
  const n = Math.max(1, Math.ceil((toZ - PIVOT_Z) / 1.2));
  return Array.from({ length: n + 1 }, (_, i) => {
    const z = PIVOT_Z + ((toZ - PIVOT_Z) * i) / n;
    return [planX(plan, z), z];
  });
}

/**
 * 무지개 경로 가운데 선 (도로 좌표, 내 차 가운데의 실제 위치에서 시작). 회전 교차로면 차가 따라갈 원호를 그대로 그린다:
 * 회전 전 = 원호 시작까지 직진 → 남은 원호 → 가로 도로의 들어갈 차로를 따라 옆으로, 회전 뒤 = 남은 원호 → 직진.
 * 회전이 아니고 차로 계획(추월 등)이 있으면 그 계획대로 차로를 옮겨 가는 선.
 */
function pathAhead(turn: TurnPose | null, plan: LanePlan | null = null): P2[] {
  if (!turn && plan) return lanePath(plan);
  if (!turn) return [[0, PIVOT_Z], ...segment([0, PIVOT_Z], [0, PATH_AHEAD])];
  const { s, r, cz, phase, done } = turn;
  const start: P2 = [turn.x, turn.z];
  if (phase === 'before') {
    // 원호 전 직진 구간에 차로 계획(추월 등)이 있으면 그대로 S 자로 옮기고, 원호·가로 도로는 그만큼 옆으로 옮겨 잇는다
    const lanePlanned = done === 0 && cz > start[1] ? plan : null;
    const shift = lanePlanned ? planX(lanePlanned, cz) : 0;
    const at = (t: number): P2 => [shift + s * r - s * r * Math.cos(t), cz + r * Math.sin(t)];
    const straight = lanePlanned
      ? lanePath(lanePlanned, cz).slice(1)
      : done === 0 && cz > start[1]
        ? segment(start, [0, cz])
        : [];
    const arc = done < Math.PI / 2 ? arcPoints(at, done, Math.PI / 2, r) : [];
    const from = arc.length > 0 ? arc[arc.length - 1]! : start;
    return [start, ...straight, ...arc, ...segment(from, [s * PATH_SIDE, from[1]])];
  }
  const at = (t: number): P2 => [s * r - s * r * Math.cos(t), cz - r * Math.sin(t)];
  return [start, ...arcPoints(at, Math.PI / 2 - done, 0, r), ...segment([0, cz], [0, PATH_AHEAD])];
}

/** 가운데 선을 폭 width 의 띠 조각들로. 조각마다 경로 길이(내 차 기준)를 함께 준다 */
function ribbon(line: P2[], width: number, a0: number) {
  const half = width / 2;
  const sides = line.map((p, i) => {
    const a = line[Math.max(0, i - 1)]!;
    const b = line[Math.min(line.length - 1, i + 1)]!;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    // 진행 방향 (tx, tz) 의 오른쪽 법선 = (tz, -tx)
    const nx = (b[1] - a[1]) / len;
    const nz = -(b[0] - a[0]) / len;
    return {
      left: [p[0] - nx * half, p[1] - nz * half] as P2,
      right: [p[0] + nx * half, p[1] + nz * half] as P2,
    };
  });
  let along = a0;
  return line.slice(1).map((p, i) => {
    const q = line[i]!;
    const step = Math.hypot(p[0] - q[0], p[1] - q[1]);
    const mid = along + step / 2;
    along += step;
    const a = sides[i]!;
    const b = sides[i + 1]!;
    return { along: mid, quad: [a.left, a.right, b.right, b.left] };
  });
}

/** 사거리 신호등: 사거리 건너편 오른쪽 기둥 + 내 방향 차로 위로 뻗은 팔 + 가로형 3구. 인식한 불빛만 켠다 */
/** 목적지 집: 도로 오른쪽 보도 너머에 박공지붕 집 + 위에 "집" 표시 */
function House({ x, z, view }: { x: number; z: number; view: View }) {
  const w = 5.6;
  const d = 5;
  const wallH = 2.8;
  const ridgeH = 4.6;
  const x0 = x;
  const x1 = x + w;
  const walls = box(x0, x1, 0, wallH, z, z + d, view);
  const mid = (x0 + x1) / 2;
  // 지붕: 길이 방향(z)으로 용마루, 양쪽 경사면 + 앞 박공
  const roofLeft = poly(
    [
      [x0 - 0.4, wallH, z - 0.4],
      [mid, ridgeH, z - 0.4],
      [mid, ridgeH, z + d + 0.4],
      [x0 - 0.4, wallH, z + d + 0.4],
    ],
    view,
  );
  const roofRight = poly(
    [
      [mid, ridgeH, z - 0.4],
      [x1 + 0.4, wallH, z - 0.4],
      [x1 + 0.4, wallH, z + d + 0.4],
      [mid, ridgeH, z + d + 0.4],
    ],
    view,
  );
  const gable = poly(
    [
      [x0, wallH, z],
      [x1, wallH, z],
      [mid, ridgeH - 0.3, z],
    ],
    view,
  );
  const door = poly(
    [
      [mid - 0.7, 0, z - 0.02],
      [mid + 0.7, 0, z - 0.02],
      [mid + 0.7, 2.2, z - 0.02],
      [mid - 0.7, 2.2, z - 0.02],
    ],
    view,
  );
  const windows = [x0 + 0.9, x1 - 2.1].map((wx) =>
    poly(
      [
        [wx, 1.3, z - 0.02],
        [wx + 1.2, 1.3, z - 0.02],
        [wx + 1.2, 2.4, z - 0.02],
        [wx, 2.4, z - 0.02],
      ],
      view,
    ),
  );
  const pin = project([mid, ridgeH + 2.4, z + d / 2], view);
  return (
    <g className={styles.house} data-testid="scene-home">
      {walls.map((f, i) => (
        <polygon key={i} className={styles[`wall_${f.key}`]} points={f.points!} />
      ))}
      {gable && <polygon className={styles.wall_back} points={gable} />}
      {door && <polygon className={styles.houseDoor} points={door} />}
      {windows.map((p, i) => p && <polygon key={`w${i}`} className={styles.houseWindow} points={p} />)}
      {roofLeft && <polygon className={styles.roofDark} points={roofLeft} />}
      {roofRight && <polygon className={styles.roofLight} points={roofRight} />}
      {pin && (
        <g transform={`translate(${pin[0].toFixed(1)} ${pin[1].toFixed(1)})`} className={styles.homePin}>
          <path d="M 0 0 C -9 -10 -11 -16 -11 -21 A 11 11 0 1 1 11 -21 C 11 -16 9 -10 0 0 Z" />
          <text y="-28">집</text>
        </g>
      )}
    </g>
  );
}

function TrafficLight({
  z,
  state,
  headX,
  poleX,
  view,
}: {
  z: number;
  state: WorldObject['state'];
  headX: number;
  poleX: number;
  view: View;
}) {
  const pole = poly(
    [
      [poleX - 0.14, 0, z],
      [poleX + 0.14, 0, z],
      [poleX + 0.14, 6.4, z],
      [poleX - 0.14, 6.4, z],
    ],
    view,
  );
  const arm = poly(
    [
      [headX - 1.4, 6.2, z],
      [poleX, 6.2, z],
      [poleX, 6.42, z],
      [headX - 1.4, 6.42, z],
    ],
    view,
  );
  const head = box(headX - 1.4, headX + 1.4, 5.35, 6.2, z, z + 0.5, view);
  const lights = (['red', 'yellow', 'green'] as const).map((color, i) => {
    const c = project([headX - 0.9 + i * 0.9, 5.78, z - 0.02], view);
    const edge = project([headX - 0.9 + i * 0.9 + 0.32, 5.78, z - 0.02], view);
    return c && edge ? { color, cx: c[0], cy: c[1], r: Math.hypot(edge[0] - c[0], edge[1] - c[1]) } : null;
  });
  return (
    <g className={styles.signal} data-state={state ?? 'unknown'}>
      {pole && <polygon className={styles.signalPole} points={pole} />}
      {arm && <polygon className={styles.signalPole} points={arm} />}
      {head.map((f, i) => (
        <polygon key={i} className={styles.signalHead} points={f.points!} />
      ))}
      {lights.map(
        (l) =>
          l && (
            <circle
              key={l.color}
              className={styles.signalLamp}
              data-color={l.color}
              data-on={state === l.color}
              cx={l.cx}
              cy={l.cy}
              r={l.r}
            />
          ),
      )}
    </g>
  );
}

export function DriveScene({
  steer,
  objects,
  distanceM,
  stopped,
  braking,
  rainbow,
  road,
  turnTravelM = null,
  homeAheadM = null,
}: {
  steer: number;
  objects: WorldObject[];
  distanceM: number;
  stopped: boolean;
  braking: boolean;
  /** 바닥 무지개 경로 (주행 중). 차 앞부터, 회전할 사거리에서는 차가 돌 원호를 따라 꺾인다. 달린 만큼 흐른다 */
  rainbow: boolean;
  road: RoadInfo | null;
  /**
   * 도로 정보의 회전 교차로(road.intersection.turn)에서 원호 시작부터 달린 거리 (RC m, 원호 전 음수, 없으면 null).
   * 원호 반지름은 TURN_RADIUS_RC_M. 도로 정보가 회전 전/후 어느 도로 기준인지 정한다
   */
  turnTravelM?: number | null;
  /** 목적지(집)까지 남은 거리 (RC m). 가까우면 도로 오른쪽에 집을 그린다 */
  homeAheadM?: number | null;
}) {
  const { lanes, lane, oncomingLanes, intersection } = road ?? DEFAULT_ROAD;
  // 사거리 (뒤에 있으면 방금 돌아 나온 도로)
  const zi = intersection ? intersection.y_m * M + CAR.len : null;
  const halfCross = intersection ? Math.max(2, intersection.crossLanes) * LANE : 0;
  const ahead = zi !== null && zi > CAR.len;
  // 회전 교차로: 차는 원호를 따라 돈다 (도로 정보가 회전 전/후 어느 도로 기준인지 정한다).
  // 장면은 차의 실제 위치·방향을 화면 가운데 내 차 자리로 옮겨 그린다
  const turnDir = intersection?.turn ?? null;
  // 원호 시작부터 달린 거리. 주어지지 않으면 회전 전에는 도로 정보로 잰다: 원호는 들어갈 차로에서 끝난다
  // (우측 통행: 우회전은 가로 도로 가까운 쪽 바깥 차로, 좌회전은 먼 쪽 1차로)
  const travel =
    turnTravelM !== null
      ? turnTravelM * M
      : turnDir && zi !== null && ahead
        ? PIVOT_Z -
          ((turnDir === 'right' ? zi - halfCross + LANE / 2 : zi + LANE / 2) - TURN_RADIUS_RC_M[turnDir] * M)
        : null;
  const turn =
    turnDir && zi !== null && travel !== null ? turnPose(turnDir, travel, ahead ? 'before' : 'after') : null;
  const yaw = turn?.yaw ?? 0;
  // 조향(rad, 왼쪽 +) → 앞쪽이 휘는 정도. RC카 조향 범위 ±0.4 rad. 원호를 도는 중에는 원호가 대신한다
  const curve =
    turn && turn.done > 0 && turn.done < Math.PI / 2 ? 0 : -Math.max(-1, Math.min(1, steer / 0.4)) * 0.0035;
  const world: View = turn
    ? { curve: yaw !== 0 ? 0 : curve, cos: Math.cos(yaw), sin: Math.sin(yaw), px: turn.x, pz: turn.z, qx: 0 }
    : { curve, ...STILL };
  const self: View = { curve: 0, ...STILL };
  // 차로 경계 x: 중앙선 = 1차로 왼쪽
  const center = (0.5 - lane) * LANE;
  const rightEdge = center + lanes * LANE;
  const leftEdge = center - oncomingLanes * LANE;
  const sameDividers = Array.from({ length: Math.max(0, lanes - 1) }, (_, i) => center + (i + 1) * LANE);
  const oncomingDividers = Array.from(
    { length: Math.max(0, oncomingLanes - 1) },
    (_, i) => center - (i + 1) * LANE,
  );
  // 점선: 실제 주행 거리만큼 뒤로 흐른다 (6 단위 간격)
  const phase = (distanceM * M) % 6;
  const dashes = [...sameDividers, ...oncomingDividers].flatMap((x) =>
    Array.from({ length: 31 }, (_, i) => {
      const z0 = BEHIND + i * 6 - phase;
      return strip(x - 0.09, x + 0.09, z0, z0 + 3, world, 0.006, 2);
    }),
  );
  const crossWalks =
    zi === null
      ? []
      : [zi - halfCross - 2.6, zi + halfCross + 0.4].flatMap((zw) =>
          Array.from({ length: Math.ceil((rightEdge - leftEdge) / 1.2) }, (_, i) => {
            const x0 = leftEdge + 0.2 + i * 1.2;
            return strip(x0, x0 + 0.6, zw, zw + 2.2, world, 0.008, 1);
          }),
        );
  const signal = objects.find((o) => o.kind === 'traffic_light') ?? null;
  const placed = objects
    .filter((o) => o.kind === 'car')
    .map((o) => ({ ...o, X: o.x_m * M, Z: o.y_m * M + CAR.len }))
    .filter((o) => o.Z > NEAR + 2 && o.Z < 110)
    .map((o) => ({ ...o, depth: rotate([o.X, 0, o.Z], world)[2] }))
    .sort((a, b) => b.depth - a.depth);
  const laneLine = (x: number, cls: string | undefined, key: string) => {
    const pts = strip(x - 0.09, x + 0.09, BEHIND, FAR, world, 0.006, 30);
    return pts && <polygon key={key} className={cls} points={pts} />;
  };
  const roadArea = strip(leftEdge, rightEdge, BEHIND, FAR, world, 0.002, 30);
  const ground = strip(-160, 160, -60, FAR + 40, world, 0, 24);
  const crossArea = zi !== null ? crossStrip(-160, 160, zi - halfCross, zi + halfCross, world) : null;
  const stopLine =
    ahead && zi !== null
      ? strip(center, rightEdge, zi - halfCross - 3.6, zi - halfCross - 3.0, world, 0.009, 1)
      : null;
  // 무지개 경로: 차 앞 범퍼부터 앞으로만 (차 뒤에는 그리지 않는다). 회전 교차로에서는 차가 돌 원호를 따라간다.
  // 색은 경로 길이를 따라 바뀌고, 달린 거리만큼 차 쪽으로 흐른다
  const bandWidth = LANE - 0.6;
  const flow = distanceM * M * RAINBOW_FLOW;
  // 차로 계획 (추월 등): 갈 차로·돌아올 차로가 지금 차로와 다르면 무지개를 그 계획대로 옮겨 그린다
  const plan = road?.plan ?? null;
  const lanePlan: LanePlan | null =
    (!turn || (turn.phase === 'before' && turn.done === 0)) &&
    plan &&
    (Math.abs(plan.lane - lane) > 0.02 || (plan.thenLane !== null && Math.abs(plan.thenLane - lane) > 0.02))
      ? {
          dx: (plan.lane - lane) * LANE,
          backDx: plan.thenLane !== null ? (plan.thenLane - lane) * LANE : null,
          backZ: plan.thenAfterM !== null ? CAR.len + plan.thenAfterM * M : null,
        }
      : null;
  // 내 차 밑(뒤 범퍼 → 차 가운데)은 차 방향을 따라, 차 가운데부터는 경로를 따라 (차 뒤로는 그리지 않는다)
  const band = rainbow
    ? [
        ...ribbon([[0, 0], ...segment([0, 0], [0, PIVOT_Z], 0.8)], bandWidth, 0).map((q) => ({
          ...q,
          view: self,
        })),
        ...ribbon(pathAhead(turn, lanePlan), bandWidth, PIVOT_Z).map((q) => ({ ...q, view: world })),
      ].flatMap(({ along, quad, view }) => {
        const points = poly(
          quad.map(([x, z]) => [x, 0.012, z] as P3),
          view,
        );
        return points ? [{ points, color: rainbowColor(along + flow) }] : [];
      })
    : [];
  // 집: 오른쪽 보도 너머, 남은 거리만큼 앞 (도착하면 차 옆)
  const homeZ = homeAheadM !== null ? homeAheadM * M + CAR.len + 5 : null;
  const safeStopLine = stopped ? strip(-LANE / 2, LANE / 2, 11, 11.6, self, 0.014, 1) : null;

  return (
    <svg
      className={styles.scene}
      data-brake={braking}
      data-yaw={Math.round((yaw * 180) / Math.PI)}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="scene-horizon" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0.35" className={styles.fadeSolid} />
          <stop offset="1" className={styles.fadeClear} />
        </linearGradient>
      </defs>

      {/* 보도(바탕)와 도로 면 */}
      {ground && <polygon className={styles.sidewalk} points={ground} />}
      {roadArea && <polygon className={styles.roadSurface} points={roadArea} />}
      {laneLine(leftEdge, styles.roadEdgeLine, 'edge-l')}
      {laneLine(rightEdge, styles.roadEdgeLine, 'edge-r')}
      {dashes.map((d, i) => d && <polygon key={`d${i}`} className={styles.dash} points={d} />)}
      {/* 노란 이중 중앙선: 내 방향과 반대 방향 사이 */}
      {laneLine(center - 0.22, styles.centerLine, 'center-a')}
      {laneLine(center + 0.22, styles.centerLine, 'center-b')}

      {/* 사거리: 가로 도로가 차로 표시를 덮는다 */}
      {crossArea && (
        <polygon className={styles.roadSurface} points={crossArea} data-testid="scene-intersection" />
      )}
      {crossWalks.map((w, i) => w && <polygon key={`w${i}`} className={styles.crosswalk} points={w} />)}
      {stopLine && <polygon className={styles.signalStopLine} points={stopLine} />}

      {band.length > 0 && (
        <g
          className={styles.rainbow}
          data-testid="scene-route"
          data-turn={(ahead && intersection?.turn) || 'none'}
        >
          {/* 같은 색 테두리로 조각 사이 이음매를 메운다 */}
          {band.map((b, i) => (
            <polygon key={i} points={b.points} fill={b.color} stroke={b.color} strokeWidth={0.8} />
          ))}
        </g>
      )}
      {safeStopLine && <polygon className={styles.stopLine} points={safeStopLine} />}

      {placed.map((o) => (
        <Car key={o.id} x={o.X} z={o.Z} heading={o.heading} view={world} />
      ))}
      <Car x={0} z={0} heading="same" self view={self} />
      <rect fill="url(#scene-horizon)" x="0" y="0" width={VIEW_W} height={HORIZON + 110} />
      {/* 목적지 집: 높이가 있어 먼 곳 흐림 위에 그린다 */}
      {homeZ !== null && homeZ < FAR && <House x={rightEdge + 2.5} z={homeZ} view={world} />}
      {/* 신호등은 앞 사거리에만, 먼 곳 흐림 위에 그린다 */}
      {ahead && zi !== null && signal && (
        <TrafficLight
          z={zi + halfCross + 0.6}
          state={signal.state}
          headX={center + (lanes * LANE) / 2}
          poleX={rightEdge + 1.2}
          view={world}
        />
      )}
    </svg>
  );
}
