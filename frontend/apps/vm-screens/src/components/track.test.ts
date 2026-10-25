import { describe, expect, it } from 'vitest';
import { LAP_M, poseAt, RADIUS_M, STRAIGHT_M } from './track';

describe('poseAt — 추정 거리 → 트랙 위치', () => {
  it('출발점은 아래 직선 시작, 오른쪽을 본다', () => {
    const p = poseAt(0);
    expect([p.x, p.y, p.heading, p.lap, p.segment]).toEqual([0, -RADIUS_M, 0, 0, 0]);
    expect(p.toNextM).toBe(STRAIGHT_M);
  });

  it('오른쪽 코너 한가운데는 위를 본다', () => {
    const p = poseAt(STRAIGHT_M + (Math.PI * RADIUS_M) / 2);
    expect(p.segment).toBe(1);
    expect(p.x).toBeCloseTo(STRAIGHT_M + RADIUS_M);
    expect(p.y).toBeCloseTo(0);
    expect(p.heading).toBeCloseTo(Math.PI / 2);
  });

  it('위 직선은 왼쪽으로 간다', () => {
    const p = poseAt(STRAIGHT_M + Math.PI * RADIUS_M + 1);
    expect(p.segment).toBe(2);
    expect(p.x).toBeCloseTo(STRAIGHT_M - 1);
    expect(p.y).toBeCloseTo(RADIUS_M);
    expect(p.heading).toBeCloseTo(Math.PI);
  });

  it('한 바퀴 넘으면 바퀴 수가 늘고 처음으로 돌아온다', () => {
    const p = poseAt(LAP_M + 0.5);
    expect(p.lap).toBe(1);
    expect(p.segment).toBe(0);
    expect(p.x).toBeCloseTo(0.5);
  });
});
