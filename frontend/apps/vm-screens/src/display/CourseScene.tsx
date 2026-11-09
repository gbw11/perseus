import { CAR, carLamps, carSolids, carWheels, WHEEL_L, type CarFace } from './carModel';
import {
  APRON,
  BOUNDS,
  courseAt,
  courseLoop,
  LAP,
  PAINT_LINES,
  ROAD_HALF,
  SLOTS,
  START_LINE,
  type Obstacle,
  type P2,
} from './course';
import type { Pose } from './useSmoothPose';
import styles from './combined.module.css';

/**
 * Jetson 시험 코스를 내 차 뒤·위에서 내려다본 3D 주행 장면 (SVG 원근 투영).
 * 코스 좌표(m)를 내 차 기준으로 옮겨 그린다: 내 차는 늘 화면 가운데에 있고 코스가 차를 따라 돌고 움직인다.
 * 그리는 것: 두 차로(흰 실선 가장자리·점선 분리선), 출발선, 주차 마당·칸(노란 선), 콘, 주행 계획(무지개), 내 차.
 * 장면 단위: 1 m = 10 단위. 원점은 내 차 뒤 범퍼 가운데, x 오른쪽, y 위, z 앞.
 */
const M = 10;
const VIEW_W = 800;
const VIEW_H = 600;
const CX = VIEW_W / 2;
const HORIZON = 60;
const FOCAL = 780;
const CAM_H = 9;
const CAM_BACK = 18;
const CLIP_D = 1;
/** 이보다 먼 작은 조각(점선 등)은 그리지 않는다 */
const FAR = 140;
/** 차 모델의 후륜축 자리 (뒤 범퍼에서 앞으로) — 코스 pose 는 후륜축 기준 */
const REAR_AXLE = WHEEL_L[0]!;
const CAMERA: P3 = [0, CAM_H, -CAM_BACK];

type P3 = [number, number, number];

/** 코스 좌표 (m) → 장면 좌표 (내 차 기준) */
function toScene(pose: Pose, [X, Y]: P2, y = 0): P3 {
  const c = Math.cos(pose.yaw);
  const s = Math.sin(pose.yaw);
  const dx = X - pose.x;
  const dy = Y - pose.y;
  return [(dx * s - dy * c) * M, y, (dx * c + dy * s) * M + REAR_AXLE];
}

function project([x, y, z]: P3): [number, number] {
  const d = z + CAM_BACK;
  return [CX + (FOCAL * x) / d, HORIZON + (FOCAL * (CAM_H - y)) / d];
}

/** 다각형을 카메라 앞 평면으로 자른 뒤 투영한 "x,y x,y …" (다 잘리면 null) */
function poly(points: P3[]): string | null {
  const inside = (p: P3) => p[2] + CAM_BACK >= CLIP_D;
  const clipped: P3[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i]!;
    const b = points[(i + 1) % points.length]!;
    if (inside(a)) clipped.push(a);
    if (inside(a) !== inside(b)) {
      const t = (CLIP_D - CAM_BACK - a[2]) / (b[2] - a[2]);
      clipped.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, CLIP_D - CAM_BACK]);
    }
  }
  if (clipped.length < 3) return null;
  return clipped
    .map((p) => {
      const [sx, sy] = project(p);
      return `${sx.toFixed(1)},${sy.toFixed(1)}`;
    })
    .join(' ');
}

/** 바닥 다각형 (코스 좌표) */
const ground = (pose: Pose, pts: P2[], y = 0) => poly(pts.map((p) => toScene(pose, p, y)));

/** 고리 두 개 사이 (도로·가장자리 선): evenodd 로 채우는 path */
function ring(pose: Pose, outer: P2[], inner: P2[], y: number) {
  const a = ground(pose, outer, y);
  const b = ground(pose, inner, y);
  return a ? `M ${a} Z${b ? ` M ${b} Z` : ''}` : null;
}

/** 선분을 폭 width 의 바닥 띠로 */
function bar(pose: Pose, a: P2, b: P2, width: number, y: number) {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const nx = (-(b[1] - a[1]) / len) * (width / 2);
  const ny = ((b[0] - a[0]) / len) * (width / 2);
  return ground(
    pose,
    [
      [a[0] + nx, a[1] + ny],
      [b[0] + nx, b[1] + ny],
      [b[0] - nx, b[1] - ny],
      [a[0] - nx, a[1] - ny],
    ],
    y,
  );
}

const near = (pose: Pose, p: P2) => {
  const z = toScene(pose, p)[2];
  return z > -CAM_BACK && z < FAR;
};

const OUTER_EDGE = courseLoop(-ROAD_HALF);
const INNER_EDGE = courseLoop(ROAD_HALF);
const EDGE_W = 0.015;
const OUTER_LINE = courseLoop(-ROAD_HALF + EDGE_W);
const INNER_LINE = courseLoop(ROAD_HALF - EDGE_W);
/** 분리선 점선: 10 cm 마다 5 cm */
const DASHES: [P2, P2][] = Array.from({ length: Math.floor(LAP / 0.1) }, (_, i) => {
  const a = courseAt(i * 0.1);
  const b = courseAt(i * 0.1 + 0.05);
  return [
    [a.x, a.y],
    [b.x, b.y],
  ];
});
const FLOOR: P2[] = [
  [0, 0],
  [BOUNDS.w, 0],
  [BOUNDS.w, BOUNDS.h],
  [0, BOUNDS.h],
];
const rect = (x0: number, y0: number, x1: number, y1: number): P2[] => [
  [x0, y0],
  [x1, y0],
  [x1, y1],
  [x0, y1],
];

type Face = { key: string; tag?: string; points: string; depth: number };

/** 볼록 입체 면 중 카메라를 향한 면을 먼 것부터 (corners 는 장면 좌표, normal 은 바깥 법선) */
function visibleFaces(faces: { key: string; tag?: string; corners: P3[]; normal: P3 }[]): Face[] {
  return faces
    .flatMap((f) => {
      const n = f.corners.length;
      const c = f.corners.reduce<P3>(
        (s, p) => [s[0] + p[0] / n, s[1] + p[1] / n, s[2] + p[2] / n],
        [0, 0, 0],
      );
      const toCam: P3 = [CAMERA[0] - c[0], CAMERA[1] - c[1], CAMERA[2] - c[2]];
      if (f.normal[0] * toCam[0] + f.normal[1] * toCam[1] + f.normal[2] * toCam[2] <= 0) return [];
      const points = poly(f.corners);
      return points ? [{ key: f.key, tag: f.tag, points, depth: Math.hypot(...toCam) }] : [];
    })
    .sort((a, b) => b.depth - a.depth);
}

/** 내 차 (차 모델 좌표 [오른쪽, 앞, 높이] = 장면 좌표 [x, z, y]) */
function SelfCar() {
  const toP3 = ([u, l, y]: [number, number, number]): P3 => [u, y, l];
  const faces = (list: CarFace[]) =>
    visibleFaces(
      list.map((f) => ({
        key: f.key,
        tag: f.tag,
        corners: f.corners.map(toP3),
        normal: [f.normal[0], f.normal[2], f.normal[1]] as P3,
      })),
    );
  const model = carSolids();
  const body = faces(model.body);
  const cabin = faces(model.glass);
  const hw = CAR.w / 2;
  const wheels = body
    .filter((f) => f.tag === 'left' || f.tag === 'right')
    .flatMap((f) => carWheels(f.tag as 'left' | 'right'))
    .map((w) => ({ tire: poly(w.tire.map(toP3)), rim: poly(w.rim.map(toP3)) }));
  const lamps = carLamps('rear').map((l) => poly(l.map(toP3)));
  const shadow = poly([
    [-hw - 0.2, 0, -0.3],
    [hw + 0.2, 0, -0.3],
    [hw + 0.2, 0, CAR.len + 0.3],
    [-hw - 0.2, 0, CAR.len + 0.3],
  ]);
  return (
    <g className={styles.selfCar} data-testid="scene-self">
      {shadow && <polygon className={styles.shadow} points={shadow} />}
      {body.map((f, i) => (
        <polygon key={`b${i}`} className={styles[f.key]} points={f.points} />
      ))}
      {wheels.map((w, i) => (
        <g key={`w${i}`}>
          {w.tire && <polygon className={styles.tire} points={w.tire} />}
          {w.rim && <polygon className={styles.rim} points={w.rim} />}
        </g>
      ))}
      {cabin.map((f, i) => (
        <polygon key={`c${i}`} className={styles[`cabin_${f.key}`]} points={f.points} />
      ))}
      {lamps.map((p, i) => p && <polygon key={`l${i}`} className={styles.tailLight} points={p} />)}
    </g>
  );
}

/** 콘: 사각 밑면이 위로 좁아지는 뿔대 + 흰 띠 */
function Cone({ o, pose }: { o: Obstacle; pose: Pose }) {
  const H = 0.2;
  const half = o.w / 2;
  const at = (k: number, y: number): P3[] => {
    const r = half * (1 - 0.8 * k);
    return (
      [
        [o.x - r, o.y - r],
        [o.x + r, o.y - r],
        [o.x + r, o.y + r],
        [o.x - r, o.y + r],
      ] as P2[]
    ).map((p) => toScene(pose, p, y * M));
  };
  const center = toScene(pose, [o.x, o.y], (H / 2) * M);
  const band = (k0: number, k1: number) => {
    const lo = at(k0, H * k0);
    const hi = at(k1, H * k1);
    return lo.map((_, i) => {
      const j = (i + 1) % 4;
      const corners = [lo[i]!, lo[j]!, hi[j]!, hi[i]!];
      const mid = corners.reduce<P3>(
        (s, p) => [s[0] + p[0] / 4, s[1] + p[1] / 4, s[2] + p[2] / 4],
        [0, 0, 0],
      );
      return { key: 'cone', corners, normal: [mid[0] - center[0], 0, mid[2] - center[2]] as P3 };
    });
  };
  const sides = visibleFaces(band(0, 1));
  const stripe = visibleFaces(band(0.45, 0.65));
  const base = ground(pose, rect(o.x - half - 0.02, o.y - half - 0.02, o.x + half + 0.02, o.y + half + 0.02));
  return (
    <g data-testid={`scene-cone-${o.id}`}>
      {base && <polygon className={styles.coneBase} points={base} />}
      {sides.map((f, i) => (
        <polygon key={`s${i}`} className={styles.coneSide} points={f.points} />
      ))}
      {stripe.map((f, i) => (
        <polygon key={`t${i}`} className={styles.coneStripe} points={f.points} />
      ))}
    </g>
  );
}

const RAINBOW = ['#ff3b5c', '#ff9f1c', '#ffe14d', '#3ddc84', '#2f9bff', '#8b5cf6'];

function rainbowColor(k: number) {
  const t = (((k % 1) + 1) % 1) * RAINBOW.length;
  const i = Math.floor(t);
  const mix = (hex0: string, hex1: string, w: number) =>
    [1, 3, 5]
      .map((o) => {
        const c0 = parseInt(hex0.slice(o, o + 2), 16);
        const c1 = parseInt(hex1.slice(o, o + 2), 16);
        return Math.round(c0 + (c1 - c0) * w);
      })
      .join(',');
  return `rgb(${mix(RAINBOW[i % RAINBOW.length]!, RAINBOW[(i + 1) % RAINBOW.length]!, t - i)})`;
}

/** 계획 경로를 폭 width 의 띠 조각으로 (조각마다 경로 길이) */
function ribbon(line: P2[], width: number) {
  const half = width / 2;
  const sides = line.map((p, i) => {
    const a = line[Math.max(0, i - 1)]!;
    const b = line[Math.min(line.length - 1, i + 1)]!;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const nx = (-(b[1] - a[1]) / len) * half;
    const ny = ((b[0] - a[0]) / len) * half;
    return { l: [p[0] + nx, p[1] + ny] as P2, r: [p[0] - nx, p[1] - ny] as P2 };
  });
  let along = 0;
  return line.slice(1).map((p, i) => {
    const q = line[i]!;
    along += Math.hypot(p[0] - q[0], p[1] - q[1]);
    return { along, quad: [sides[i]!.l, sides[i]!.r, sides[i + 1]!.r, sides[i + 1]!.l] };
  });
}

export function CourseScene({
  pose,
  obstacles,
  plan,
  stopped,
  braking,
}: {
  /** 내 차 후륜축 (코스 좌표). 모르면 출발 자리에서 본 코스를 그린다 */
  pose: Pose | null;
  obstacles: Obstacle[];
  /** 주행 계획 경로 (코스 좌표). 비어 있으면 무지개를 그리지 않는다 */
  plan: P2[];
  stopped: boolean;
  braking: boolean;
}) {
  const at: Pose = pose ?? { x: 2.81, y: 0.7, yaw: Math.PI };
  const outside = ground(at, rect(-20, -20, BOUNDS.w + 20, BOUNDS.h + 20), -0.01);
  const floor = ground(at, FLOOR);
  const road = ring(at, OUTER_EDGE, INNER_EDGE, 0.002);
  const edges = [ring(at, OUTER_EDGE, OUTER_LINE, 0.004), ring(at, INNER_LINE, INNER_EDGE, 0.004)];
  const dashes = DASHES.filter(([a]) => near(at, a)).map(([a, b]) => bar(at, a, b, 0.014, 0.005));
  const paved = [
    rect(APRON.x0, APRON.y0, APRON.x1, APRON.y1),
    ...SLOTS.map((s) => rect(s.x0, s.y0, s.x1, s.y1)),
  ].map((r) => ground(at, r, 0.003));
  const paint = PAINT_LINES.flatMap((l) => l.slice(1).map((b, i) => bar(at, l[i]!, b, 0.016, 0.006)));
  const start = bar(at, START_LINE[0], START_LINE[1], 0.035, 0.006);
  const band = ribbon(plan, 0.22).flatMap(({ along, quad }) => {
    const points = ground(at, quad, 0.008);
    return points ? [{ points, color: rainbowColor(along / 1.5) }] : [];
  });
  // 안전 정지: 차 앞 0.6 m 에 빨간 정지선
  const front = { x: at.x + Math.cos(at.yaw) * 0.6, y: at.y + Math.sin(at.yaw) * 0.6 };
  const side = { x: -Math.sin(at.yaw) * 0.2, y: Math.cos(at.yaw) * 0.2 };
  const safeStop = stopped
    ? bar(at, [front.x - side.x, front.y - side.y], [front.x + side.x, front.y + side.y], 0.05, 0.01)
    : null;
  // 콘과 내 차를 먼 것부터 (내 차 가운데보다 먼 콘은 차 뒤에 가린다)
  const carDepth = Math.hypot(0 - CAMERA[0], 0.8 - CAMERA[1], CAR.len / 2 - CAMERA[2]);
  const cones = obstacles
    .map((o) => {
      const p = toScene(at, [o.x, o.y]);
      return { o, z: p[2], depth: Math.hypot(p[0] - CAMERA[0], 1 - CAMERA[1], p[2] - CAMERA[2]) };
    })
    // 차 뒤로 30 cm 넘게 지나간 콘은 그리지 않는다 (카메라 앞을 크게 가린다)
    .filter((c) => c.z > -3 && c.z < FAR)
    .sort((a, b) => b.depth - a.depth);
  const farCones = cones.filter((c) => c.depth >= carDepth);
  const nearCones = cones.filter((c) => c.depth < carDepth);

  return (
    <svg
      className={styles.scene}
      data-brake={braking}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      data-testid="course-scene"
    >
      <defs>
        <linearGradient id="scene-horizon" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0.35" className={styles.fadeSolid} />
          <stop offset="1" className={styles.fadeClear} />
        </linearGradient>
      </defs>
      {outside && <polygon className={styles.sidewalk} points={outside} />}
      {floor && <polygon className={styles.courseFloor} points={floor} />}
      {road && <path className={styles.roadSurface} d={road} fillRule="evenodd" />}
      {paved.map((p, i) => p && <polygon key={`p${i}`} className={styles.roadSurface} points={p} />)}
      {edges.map(
        (d, i) => d && <path key={`e${i}`} className={styles.roadEdgeLine} d={d} fillRule="evenodd" />,
      )}
      {dashes.map((d, i) => d && <polygon key={`d${i}`} className={styles.dash} points={d} />)}
      {paint.map((d, i) => d && <polygon key={`y${i}`} className={styles.centerLine} points={d} />)}
      {start && <polygon className={styles.startLine} points={start} />}
      {band.length > 0 && (
        <g className={styles.rainbow} data-testid="scene-route">
          {/* 같은 색 테두리로 조각 사이 이음매를 메운다 */}
          {band.map((b, i) => (
            <polygon key={i} points={b.points} fill={b.color} stroke={b.color} strokeWidth={0.8} />
          ))}
        </g>
      )}
      {safeStop && <polygon className={styles.stopLine} points={safeStop} />}
      {farCones.map(({ o }) => (
        <Cone key={o.id} o={o} pose={at} />
      ))}
      <SelfCar />
      {nearCones.map(({ o }) => (
        <Cone key={o.id} o={o} pose={at} />
      ))}
      <rect fill="url(#scene-horizon)" x="0" y="0" width={VIEW_W} height={HORIZON + 110} />
    </svg>
  );
}
