/** 화면 보간의 축거와 시간 상한 회귀. 실제 차량 추종 성능 시험은 아니다. */
import { describe, expect, it } from 'vitest';
import { extrapolate } from './useCanvasLoop';
import type { SimPose } from './types';

describe('차량 profile을 따른 화면 보간', () => {
  const pose: SimPose = {
    x: 0,
    y: 0,
    yaw: 0,
    v_mps: 0.3,
    steer_rad: Math.atan(0.2 / 0.55),
    world_revision: 1,
  };

  it('축거0.20m에서는 일정 조향의 반경0.55m yaw rate를 쓴다', () => {
    expect(extrapolate(pose, 300, 1, 0.2).yaw).toBeCloseTo((0.3 / 0.55) * 0.3, 12);
    expect(extrapolate(pose, 300).yaw).toBeCloseTo(((0.3 * (0.2 / 0.55)) / 0.26) * 0.3, 12);
  });

  it('새 위치가 끊겨도300ms 뒤로 더 달리지 않는다', () => {
    expect(extrapolate(pose, 3000, 1, 0.2)).toEqual(extrapolate(pose, 300, 1, 0.2));
  });
});
