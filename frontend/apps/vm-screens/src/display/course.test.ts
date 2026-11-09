import { describe, expect, it } from 'vitest';
import {
  courseAt,
  courseOffset,
  insideSlot,
  LAP,
  laneOffsetAt,
  OBSTACLE_SEEDS,
  obstacleAhead,
  PATH_LENGTH,
  pathPose,
  SCENARIO_MARKS,
  SCENARIO_PATH,
  SLOTS,
  START_POSE,
  VEHICLE,
} from './course';

describe('Jetson 기본 코스 (jetson-demo) 형상', () => {
  it('시뮬레이터 생성식과 같은 크기: 중심선 한 바퀴 9.63 m, 출발선 중심 [2.56, 0.5]', () => {
    // 직선 1.26 + 0.4 + 1.9 + 0.4 + 0.64, 원호 4개 × (π/2 × 0.8)
    expect(LAP).toBeCloseTo(4.6 + 2 * Math.PI * 0.8, 6);
    expect(courseAt(0)).toMatchObject({ x: 2.56, y: 0.5, heading: Math.PI });
  });

  it('진행 방향 오른쪽(+d)이 안쪽 차로: 출발 pose 는 안쪽 차로 중심', () => {
    const start = courseAt(-(START_POSE[0] - 2.56), 0.2);
    expect(start.x).toBeCloseTo(START_POSE[0], 9);
    expect(start.y).toBeCloseTo(START_POSE[1], 9);
    expect(courseOffset(START_POSE[0], START_POSE[1]).d).toBeCloseTo(0.2, 2);
    // 위 직선은 동쪽으로 간다 (시계 방향)
    expect(courseAt(5).heading).toBeCloseTo(0, 9);
  });
});

describe('시연 시나리오 경로', () => {
  it('출발 pose 에서 시작해 P1 안에 앞으로 들어가 선다', () => {
    const first = SCENARIO_PATH[0]!;
    expect(first.x).toBeCloseTo(START_POSE[0], 6);
    expect(first.y).toBeCloseTo(START_POSE[1], 6);
    const end = pathPose(PATH_LENGTH);
    expect(insideSlot(end, SLOTS[0]!)).toBe(true);
    expect(end.yaw).toBeCloseTo(Math.PI / 2, 2);
  });

  it('단계 지점은 순서대로: 수동 전환 → 자동 복귀 → VM1 장애 → 주차 진입', () => {
    const { manualOn, manualOff, crash, park } = SCENARIO_MARKS;
    expect(
      0 < manualOn && manualOn < manualOff && manualOff < crash && crash < park && park < PATH_LENGTH,
    ).toBe(true);
  });

  it('콘 A 옆은 분리선 바깥, 콘 B 옆은 안쪽으로 지나 차체(미실측 profile)가 콘에 닿지 않는다', () => {
    expect(laneOffsetAt(0.6)).toBeLessThan(0);
    expect(laneOffsetAt(1.16)).toBeGreaterThan(0);
    let gap = Infinity;
    for (const p of SCENARIO_PATH) {
      const c = Math.cos(p.yaw);
      const s = Math.sin(p.yaw);
      for (const o of OBSTACLE_SEEDS) {
        const f = (o.x - p.x) * c + (o.y - p.y) * s;
        const l = -(o.x - p.x) * s + (o.y - p.y) * c;
        const ex = Math.max(-VEHICLE.rearOverhang - f, 0, f - (VEHICLE.length - VEHICLE.rearOverhang));
        const ey = Math.max(Math.abs(l) - VEHICLE.width / 2, 0);
        gap = Math.min(gap, Math.hypot(ex, ey) - o.w / 2);
      }
    }
    expect(gap).toBeGreaterThan(0.03);
  });

  it('차 앞 1 m 안 같은 줄의 콘을 찾는다', () => {
    const near = obstacleAhead({ x: 2.81, y: 0.7, yaw: Math.PI }, OBSTACLE_SEEDS);
    expect(near?.obstacle.id).toBe('A');
    // 앞범퍼(2.56) → 콘 A 가까운 면(2.01)
    expect(near?.ahead).toBeCloseTo(0.55, 6);
    expect(obstacleAhead({ x: 3.5, y: 2.5, yaw: 0 }, OBSTACLE_SEEDS)).toBeNull();
  });
});
