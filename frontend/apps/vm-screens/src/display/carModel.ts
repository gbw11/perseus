/**
 * 차 3D 모델 (주행 장면의 모든 차와 정차 화면의 내 차가 같은 모양을 쓴다).
 * 차 좌표: u = 오른쪽, l = 뒤 범퍼에서 앞으로, y = 높이 (장면 단위, RC 1 m = 10).
 * 옆모습 단면(볼록)을 차 폭만큼 밀어낸 입체 두 개(차체·유리 칸) + 바퀴. 앞은 낮은 본닛, 뒤는 해치백.
 */
export const CAR = { w: 1.9, len: 4.6 };

type Profile = [number, number][];

/** 옆모습 단면 [l, y] (반시계 방향, 볼록) */
const BODY: Profile = [
  [0, 0.22],
  [CAR.len, 0.22],
  [CAR.len, 0.58],
  [CAR.len - 0.4, 0.84],
  [0, 0.95],
];
const GLASS: Profile = [
  [0.2, 0.9],
  [3.05, 0.9],
  [2.2, 1.48],
  [0.4, 1.48],
];
/** 진행 방향을 모르는 차: 앞뒤 구분 없는 대칭 모양 */
const BODY_PLAIN: Profile = [
  [0, 0.22],
  [CAR.len, 0.22],
  [CAR.len, 0.95],
  [0, 0.95],
];
const GLASS_PLAIN: Profile = [
  [1.0, 0.95],
  [3.6, 0.95],
  [3.3, 1.45],
  [1.3, 1.45],
];
/** 바퀴 중심 (뒤·앞), 반지름 */
export const WHEEL_L = [0.95, 3.6];
export const WHEEL_R = 0.36;
export const RIM_R = 0.22;

export type C3 = [number, number, number];

export interface CarFace {
  /** 색 구분: 끝면·경사면 back, 옆면 side, 위 top */
  key: 'back' | 'side' | 'top';
  /** 앞·뒤 끝면 (전조등·미등 자리), 왼쪽·오른쪽 옆면 (바퀴 자리) */
  tag?: 'front' | 'rear' | 'left' | 'right';
  corners: C3[];
  normal: C3;
}

function prism(profile: Profile, hw: number): CarFace[] {
  const faces: CarFace[] = [-1, 1].map((side) => ({
    key: 'side' as const,
    tag: side < 0 ? ('left' as const) : ('right' as const),
    corners: profile.map(([l, y]) => [side * hw, l, y] as C3),
    normal: [side, 0, 0],
  }));
  profile.forEach(([l0, y0], i) => {
    const [l1, y1] = profile[(i + 1) % profile.length]!;
    const len = Math.hypot(l1 - l0, y1 - y0);
    // 반시계 단면의 바깥 법선
    const nl = (y1 - y0) / len;
    const ny = -(l1 - l0) / len;
    faces.push({
      key: ny >= 0.9 ? 'top' : 'back',
      tag: nl > 0.9 ? 'front' : nl < -0.9 ? 'rear' : undefined,
      corners: [
        [-hw, l0, y0],
        [hw, l0, y0],
        [hw, l1, y1],
        [-hw, l1, y1],
      ],
      normal: [0, nl, ny],
    });
  });
  return faces;
}

/** 차체·유리 칸 면 (plain = 앞뒤 구분 없는 모양) */
export function carSolids(plain = false) {
  const hw = CAR.w / 2;
  return {
    body: prism(plain ? BODY_PLAIN : BODY, hw),
    glass: prism(plain ? GLASS_PLAIN : GLASS, hw - 0.16),
  };
}

/** 끝면 램프 두 개 (미등은 해치 아래, 전조등은 범퍼 위) */
export function carLamps(end: 'front' | 'rear'): C3[][] {
  const hw = CAR.w / 2;
  const l = end === 'rear' ? -0.01 : CAR.len + 0.01;
  const [y0, y1] = end === 'rear' ? [0.6, 0.76] : [0.4, 0.52];
  return [-1, 1].map((side) => {
    const u0 = side * (hw - 0.12);
    const u1 = side * (hw - 0.6);
    return [
      [u0, l, y0],
      [u1, l, y0],
      [u1, l, y1],
      [u0, l, y1],
    ];
  });
}

/** 한쪽 옆면의 바퀴 두 개: 타이어·휠 원판 (옆면 바로 바깥 평면) */
export function carWheels(side: 'left' | 'right'): { tire: C3[]; rim: C3[] }[] {
  const u = (side === 'left' ? -1 : 1) * (CAR.w / 2 + 0.01);
  const disc = (l: number, r: number) =>
    Array.from({ length: 16 }, (_, i) => {
      const a = (i / 16) * Math.PI * 2;
      return [u, l + r * Math.cos(a), WHEEL_R + r * Math.sin(a)] as C3;
    });
  return WHEEL_L.map((l) => ({ tire: disc(l, WHEEL_R), rim: disc(l, RIM_R) }));
}
