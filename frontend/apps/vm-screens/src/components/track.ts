/**
 * 네비게이션: 트랙 지도 + 추정 위치. 위치 센서가 없어서 차량 속도(vehicle.status v_mps)를 화면에서
 * 적분한 거리를 트랙 위에 놓는다 — 출발점에서 트랙을 따라 돈다고 가정한 추정값이다 (계약 변경 없음).
 * 트랙 치수는 표본(직선 3 m · 코너 반지름 1 m). 실제 트랙이 정해지면 STRAIGHT_M·RADIUS_M 만 바꾼다.
 */
export const STRAIGHT_M = 3;
export const RADIUS_M = 1;
export const ARC_M = Math.PI * RADIUS_M;
export const LAP_M = 2 * STRAIGHT_M + 2 * ARC_M;

/** 시계 반대 방향: 아래 직선(왼→오) → 오른쪽 코너 → 위 직선(오→왼) → 왼쪽 코너 */
export const SEGMENTS = [
  { name: '아래 직선', kind: 'straight', length: STRAIGHT_M },
  { name: '오른쪽 코너', kind: 'corner', length: ARC_M },
  { name: '위 직선', kind: 'straight', length: STRAIGHT_M },
  { name: '왼쪽 코너', kind: 'corner', length: ARC_M },
] as const;

export interface TrackPose {
  /** 트랙 좌표 (m). 원점은 왼쪽 코너 중심, y 는 위쪽이 + */
  x: number;
  y: number;
  /** 진행 방향 (rad, x 축 기준 반시계) */
  heading: number;
  lap: number;
  /** 이번 바퀴 진행 비율 0~1 */
  progress: number;
  segment: number;
  /** 다음 구간까지 남은 거리 (m) */
  toNextM: number;
}

export function poseAt(distanceM: number): TrackPose {
  const lap = Math.floor(distanceM / LAP_M);
  let d = distanceM - lap * LAP_M;
  const progress = d / LAP_M;
  for (let segment = 0; segment < SEGMENTS.length; segment++) {
    const length = SEGMENTS[segment]!.length;
    if (d <= length || segment === SEGMENTS.length - 1) {
      const toNextM = Math.max(0, length - d);
      const a = d / RADIUS_M;
      switch (segment) {
        case 0:
          return { x: d, y: -RADIUS_M, heading: 0, lap, progress, segment, toNextM };
        case 1:
          return {
            x: STRAIGHT_M + RADIUS_M * Math.sin(a),
            y: -RADIUS_M * Math.cos(a),
            heading: a,
            lap,
            progress,
            segment,
            toNextM,
          };
        case 2:
          return { x: STRAIGHT_M - d, y: RADIUS_M, heading: Math.PI, lap, progress, segment, toNextM };
        default:
          return {
            x: -RADIUS_M * Math.sin(a),
            y: RADIUS_M * Math.cos(a),
            heading: Math.PI + a,
            lap,
            progress,
            segment,
            toNextM,
          };
      }
    }
    d -= length;
  }
  throw new Error('unreachable');
}
