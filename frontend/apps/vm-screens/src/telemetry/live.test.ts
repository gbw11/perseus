import { describe, expect, it } from 'vitest';
import { applyDriveState, applyEvent, emptyTelemetry, type DriveAppDto, type DriveStateDto } from './live';

const app = (patch: Partial<DriveAppDto> = {}): DriveAppDto => ({
  vm_id: 'vm',
  command_fresh: true,
  command_ago_ms: 20,
  command_hz: 50,
  last_command: { generation: 2, v_mps: 0.4, steer_rad: 0.1 },
  heartbeat_state: 'active',
  heartbeat_ago_ms: 300,
  ...patch,
});

const state = (patch: Partial<DriveStateDto> = {}): DriveStateDto => ({
  version: 1,
  observed_at: '2026-10-20T00:00:00Z',
  control: {
    mode: 'MANUAL',
    generation: 2,
    active_app: 'manual',
    active_vm_id: 'vm-m',
    reason: 'command_timeout',
    received_ago_ms: 10,
  },
  vehicle: {
    applied_generation: 2,
    v_mps: 0.3,
    steer_rad: 0,
    batt_v: 7.8,
    flags: { estop: false, watchdog: false, obstacle: true },
    received_ago_ms: 40,
  },
  apps: {
    autonomous: app({ command_fresh: false, command_ago_ms: 900, command_hz: 0, heartbeat_ago_ms: 5000 }),
    manual: app(),
    'message-board': app({ command_fresh: null, command_ago_ms: null, command_hz: 0, last_command: null }),
  },
  perception: {
    valid: true,
    received_ago_ms: 30,
    hz: 15,
    lane_offset_m: 0.05,
    heading_err_rad: 0.01,
    obstacle_count: 2,
    stop: false,
  },
  ...patch,
});

describe('applyDriveState', () => {
  it('모드·활성 앱·차량 상태를 그대로 옮긴다', () => {
    const t = applyDriveState(emptyTelemetry(0), state(), 1000);
    expect(t.mode).toBe('MANUAL');
    expect(t.activeApp).toBe('manual');
    expect(t.generation).toBe(2);
    expect(t.vehicle.v_mps).toBe(0.3);
    expect(t.vehicle.flags.obstacle).toBe(true);
    expect(t.obstacleCount).toBe(2);
    expect(t.commands.manual).toEqual({ generation: 2, v_mps: 0.4, steer_rad: 0.1 });
  });

  it('주행 앱은 명령 신선도, 인터랙션 앱은 생존 신호로 상태를 정한다', () => {
    const t = applyDriveState(emptyTelemetry(0), state(), 1000);
    expect(t.apps.manual.health).toBe('ok');
    expect(t.apps.autonomous.health).toBe('fault');
    expect(t.apps.autonomous.domain).toBe('stopped'); // 명령도 생존 신호도 끊겼다
    expect(t.apps.interaction.health).toBe('ok');
    expect(t.apps.manual.lastCommandAt).toBe(980);
  });

  it('아직 못 받은 앱은 stopped, 상태가 바뀐 시각만 새로 잡는다', () => {
    const first = applyDriveState(emptyTelemetry(0), state({ apps: {} }), 1000);
    expect(first.apps.manual.health).toBe('stopped');
    expect(first.apps.manual.healthSince).toBe(0); // 처음부터 stopped
    const second = applyDriveState(first, state(), 2000);
    expect(second.apps.manual.healthSince).toBe(2000);
    const third = applyDriveState(second, state(), 3000);
    expect(third.apps.manual.healthSince).toBe(2000);
  });

  it('gateway 상태가 없으면 SAFE_STOP 으로 보여 준다', () => {
    const t = applyDriveState(emptyTelemetry(0), state({ control: null }), 1000);
    expect(t.mode).toBe('SAFE_STOP');
    expect(t.activeApp).toBeNull();
  });

  it('자율 명령이 신선한 동안 시작 시각을 유지한다 (AUTO_READY 표시)', () => {
    const fresh = state({ apps: { autonomous: app() } });
    const a = applyDriveState(emptyTelemetry(0), fresh, 1000);
    const b = applyDriveState(a, fresh, 4000);
    expect(b.autonomousFreshSince).toBe(1000);
    const c = applyDriveState(b, state(), 5000);
    expect(c.autonomousFreshSince).toBeNull();
  });
});

describe('applyEvent', () => {
  const event = {
    event_id: 'evt-1',
    type: 'drive.mode.changed',
    severity: 'WARNING',
    vm_id: null,
    occurred_at: '2026-10-20T00:00:10.000Z',
    payload: { from: 'AUTO', to: 'SAFE_STOP', reason: 'command_timeout' },
  };

  it('모드 전환을 타임라인 맨 앞에 넣고, 같은 이벤트는 한 번만', () => {
    const wall = Date.parse('2026-10-20T00:00:12.000Z');
    const once = applyEvent(emptyTelemetry(0), event, 5000, wall);
    expect(once.events[0]).toMatchObject({ at: 3000, level: 'warning', type: 'drive.mode.changed' });
    expect(once.events[0]?.text).toBe('모드 AUTO → SAFE_STOP (command_timeout)');
    expect(applyEvent(once, event, 6000, wall).events).toHaveLength(1);
  });

  it('장애 이벤트는 앱 이름과 사유를 적는다', () => {
    const t = applyEvent(
      emptyTelemetry(0),
      {
        ...event,
        event_id: 'evt-2',
        type: 'drive.fault.detected',
        payload: { app_id: 'autonomous', reason: 'command_timeout' },
      },
      0,
    );
    expect(t.events[0]?.text).toBe('자율주행 장애 (command_timeout)');
  });
});

describe('복구 중 표시 (RC카 시나리오 §4.3)', () => {
  const dead = app({
    vm_id: 'vm-a',
    command_fresh: false,
    command_ago_ms: null,
    command_hz: 0,
    heartbeat_ago_ms: null,
  });
  const s = (autonomous: DriveAppDto) => state({ apps: { ...state().apps, autonomous } });

  it('recovery.started 를 받으면 명령이 다시 신선해질 때까지 장애가 아니라 복구 중', () => {
    let t = applyDriveState(emptyTelemetry(0), s(dead), 0);
    expect(t.apps.autonomous.health).toBe('fault');
    t = applyEvent(
      t,
      {
        event_id: 'r1',
        type: 'recovery.started',
        severity: 'INFO',
        vm_id: 'vm-a',
        occurred_at: '2026-10-20T00:00:00Z',
        payload: {},
      },
      100,
      Date.parse('2026-10-20T00:00:00Z'),
    );
    expect(t.apps.autonomous.health).toBe('recovering');
    t = applyDriveState(t, s(dead), 200);
    expect(t.apps.autonomous.health).toBe('recovering');
    t = applyDriveState(t, s(app({ vm_id: 'vm-a' })), 300);
    expect(t.apps.autonomous.health).toBe('ok');
    t = applyDriveState(t, s(dead), 400);
    expect(t.apps.autonomous.health).toBe('fault'); // 복구가 끝났으니 다시 끊기면 장애
  });
});
