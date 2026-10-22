import { describe, expect, it } from 'vitest';
import { applySimFrame, type SimView } from '../api/sim';
import { advance, initialSim, obstaclesAhead, pose, sensing, setEstop } from './sim';

describe('시뮬레이터 mock (계약 v1.3 §11)', () => {
  it('타원 오른쪽 끝에서 반시계(+y)를 보고 시작한다', () => {
    const p = pose(initialSim(0), 0, 0);
    expect([p.x, p.y]).toEqual([3, 0]);
    expect(p.yaw).toBeCloseTo(Math.PI / 2, 3);
  });

  it('속도대로 트랙을 따라 돈다', () => {
    let s = initialSim(0);
    for (let i = 0; i < 20; i++) s = advance(s, i * 100, 0.1, 0.5, 0, 'autonomous');
    const p = pose(s, 0.5, 0);
    expect(p.y).toBeGreaterThan(0.75); // 2초 × 0.5 m/s ≈ 1 m 를 반시계로 (곡선이라 y 는 1 m 보다 조금 덜)
    expect(Math.hypot(p.x / 3, p.y / 1.5)).toBeCloseTo(1, 2); // 차선 중심 위
  });

  it('자율주행은 옆으로 밀린 거리를 줄이고, 수동은 조향대로 벌어진다', () => {
    const pushed = { ...initialSim(0), lateral: 0.3 };
    expect(advance(pushed, 0, 1, 0.5, 0, 'autonomous').lateral).toBeLessThan(0.3);
    expect(advance(initialSim(0), 0, 1, 0.5, 0.3, 'manual').lateral).toBeGreaterThan(0);
  });

  it('시연용 장애물을 앞에 놓으면 감지하고, 시간이 지나면 치운다', () => {
    let s = initialSim(0);
    s = advance(s, 8000, 0.1, 0, 0, 'autonomous');
    expect(s.world.obstacles).toHaveLength(1);
    expect(s.world.revision).toBe(2);
    expect(obstaclesAhead(s)).toBe(1);
    expect(sensing(s)).toBe(false); // 1.4 m 앞 — 아직 감지 범위 밖
    s = { ...s, theta: s.theta + 1.0 / 1.5 }; // 약 1 m 다가감 (θ=0 근처 |dP/dθ| = b = 1.5)
    expect(sensing(s)).toBe(true);
    s = advance(s, 14000, 0.1, 0, 0, 'autonomous');
    expect(s.world.obstacles).toHaveLength(0);
    expect(s.world.revision).toBe(3);
  });

  it('E-stop 은 바뀔 때만 revision 을 올린다', () => {
    const s = setEstop(initialSim(0), true);
    expect(s.world.estop).toBe(true);
    expect(setEstop(s, true).world.revision).toBe(s.world.revision);
  });

  it('WS-06 프레임: 위치가 없으면 available 이 아니고, 꼬리는 120개까지', () => {
    let v: SimView = { available: false, pose: null, world: null, trail: [] };
    v = applySimFrame(v, { available: true, pose: null, world: null });
    expect(v.available).toBe(false);
    const p = pose(initialSim(0), 0, 0);
    for (let i = 0; i < 130; i++) v = applySimFrame(v, { available: true, pose: p, world: null });
    expect(v.available).toBe(true);
    expect(v.trail).toHaveLength(120);
  });
});
