import { describe, expect, it } from 'vitest';
import type { DriveState } from '../api/drive';
import { simFlow } from './simFlow';

function drive(over: {
  mode?: NonNullable<DriveState['control']>['mode'];
  app?: 'autonomous' | 'manual' | null;
  stop?: boolean;
  estop?: boolean;
  remaining_ms?: number;
}): DriveState {
  return {
    version: 1,
    observed_at: '2026-11-04T00:00:00Z',
    control: {
      mode: over.mode ?? 'AUTO',
      generation: 1,
      active_app: over.app === undefined ? 'autonomous' : over.app,
      active_vm_id: null,
      reason: null,
      received_ago_ms: 0,
    },
    vehicle: {
      applied_generation: 1,
      v_mps: 0,
      steer_rad: 0,
      batt_v: null,
      flags: { estop: !!over.estop, watchdog: false, obstacle: false },
      received_ago_ms: 0,
    },
    apps: {},
    perception: {
      valid: true,
      received_ago_ms: 0,
      hz: 15,
      lane_offset_m: 0,
      heading_err_rad: 0,
      obstacle_count: 0,
      stop: !!over.stop,
    },
    resume:
      over.remaining_ms == null
        ? null
        : { generation: 2, switch_in_ms: 3000, accepted_ago_ms: 0, remaining_ms: over.remaining_ms },
  };
}

describe('시뮬레이터 탭 주행 흐름 (S15P31E110-262)', () => {
  it('자율 → 장애물 앞 정지 → 수동 인계 → 재개 카운트다운 → 자율 을 차례로 보여 준다', () => {
    expect(simFlow(drive({}), 0.5, null)).toMatchObject({
      mode: { label: '자율주행', health: 'ok' },
      app: '자율주행 앱',
      motion: { label: '달리는 중', health: 'ok' },
    });
    expect(simFlow(drive({ stop: true }), 0, 40).motion).toEqual({
      label: '장애물 앞 정지',
      health: 'recovering',
    });
    expect(simFlow(drive({}), 0, 2.5).motion.label).toBe('장애물 앞 정지'); // 인지 stop 이 없어도 앞 4 m 안
    expect(simFlow(drive({ mode: 'MANUAL', app: 'manual' }), 0.3, null)).toMatchObject({
      mode: { label: '수동주행', health: 'recovering' },
      app: '수동주행 앱',
    });
    expect(simFlow(drive({ mode: 'AUTO_READY', app: 'manual', remaining_ms: 2100 }), 0, null).motion).toEqual(
      {
        label: '3초 뒤 자율주행 재개',
        health: 'recovering',
      },
    );
  });

  it('장애물 근거가 없으면 그냥 정지, E-stop 은 무엇보다 먼저, 모드를 모르면 null', () => {
    expect(simFlow(drive({}), 0.01, 30).motion).toEqual({ label: '정지', health: 'stopped' });
    expect(simFlow(drive({ estop: true, stop: true }), 0, 1).motion.label).toBe('E-stop 정지');
    expect(simFlow(null, null, null)).toEqual({
      mode: null,
      app: '없음',
      motion: { label: '—', health: 'stopped' },
    });
  });
});
