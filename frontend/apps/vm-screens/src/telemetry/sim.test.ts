import { describe, expect, it } from 'vitest';
import { emptyTelemetry } from './live';
import { shownKeys } from '../components/format';
import { SIM_STALE_MS, applySimSnapshot, simLive } from './types';

const pose = { x: 3, y: 0, yaw: 1.57, v_mps: 0.5, steer_rad: 0, world_revision: 1 };
const world = {
  revision: 1,
  track: { shape: 'ellipse' as const, a_m: 3, b_m: 1.5, lane_width_m: 0.4 },
  obstacles: [],
  estop: false,
};

describe('세 번째 칸 자동 전환 (화면 배치 계약 v1.1 §5.6)', () => {
  it('시뮬레이터 프레임이 없으면 네비게이션 칸', () => {
    expect(simLive(emptyTelemetry(0))).toBe(false);
  });

  it('위치가 1초 안에 왔으면 시뮬레이터, 끊기면 다시 네비게이션', () => {
    const t = applySimSnapshot(emptyTelemetry(0), { available: true, pose, world }, 1000);
    expect(simLive({ ...t, now: 1000 + SIM_STALE_MS })).toBe(true);
    expect(simLive({ ...t, now: 1001 + SIM_STALE_MS })).toBe(false);
  });

  it('available 이 false 거나 위치가 없으면 네비게이션', () => {
    expect(
      simLive({ ...applySimSnapshot(emptyTelemetry(0), { available: false, pose, world }, 0), now: 0 }),
    ).toBe(false);
    expect(
      simLive({ ...applySimSnapshot(emptyTelemetry(0), { available: true, pose: null, world }, 0), now: 0 }),
    ).toBe(false);
  });

  it('위치가 오면 꼬리에 붙고, world 는 다음 프레임에 없으면 이전 것을 쓴다', () => {
    let t = applySimSnapshot(emptyTelemetry(0), { available: true, pose, world }, 0);
    t = applySimSnapshot(t, { available: true, pose: { ...pose, y: 0.1 } }, 50);
    expect(t.sim?.trail).toEqual([
      [3, 0],
      [3, 0.1],
    ]);
    expect(t.sim?.world?.revision).toBe(1);
  });

  it('위치가 크게 튀면(시뮬레이터 재시작) 지나온 길을 새로 시작한다', () => {
    let t = applySimSnapshot(emptyTelemetry(0), { available: true, pose, world }, 0);
    t = applySimSnapshot(t, { available: true, pose: { ...pose, x: -3 } }, 50);
    expect(t.sim?.trail).toEqual([[-3, 0]]);
  });
});

describe('조작 키 표시 (shownKeys)', () => {
  const base = { manualKeys: [] as string[], activeApp: 'manual' as const };
  it('웹 입력이 있으면 그대로', () => {
    expect(shownKeys({ ...base, manualKeys: ['brake'], commands: { manual: null } })).toEqual(['brake']);
  });
  it('없으면 수동 명령을 거꾸로 읽는다 (계약 §7)', () => {
    expect(shownKeys({ ...base, commands: { manual: { v_mps: 0.45, steer_rad: 0.3 } } })).toEqual([
      'up',
      'left',
    ]);
    expect(shownKeys({ ...base, commands: { manual: { v_mps: -0.3, steer_rad: -0.3 } } })).toEqual([
      'down',
      'right',
    ]);
  });
  it('수동이 운전하지 않으면 켜지 않는다', () => {
    expect(
      shownKeys({
        manualKeys: [],
        activeApp: 'autonomous',
        commands: { manual: { v_mps: 0.4, steer_rad: 0 } },
      }),
    ).toEqual([]);
  });
});
