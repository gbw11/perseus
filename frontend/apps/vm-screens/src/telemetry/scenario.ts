/**
 * 가짜 시나리오 엔진 — 실제 MQTT 연결 전까지 화면을 움직인다.
 * 규칙은 MQTT 계약 §5(주행 제어)와 RC카 시나리오 §4(장애·복구)를 그대로 흉내 낸다.
 * 순수 함수라서 시각(now)을 넣어 단위 시험한다.
 */
import type { Health } from '@xenova/ui';
import type { AppId, AppStatus, DriveCommand, DriveMode, Telemetry, TimelineEvent } from './types';

/** MQTT 계약 §5.1 / §5.2 */
export const COMMAND_TIMEOUT_MS = 100;
export const AUTO_READY_FRESH_MS = 3000;
/** 복구 서비스가 죽은 VM 을 다시 올리기까지 (가짜 값) */
export const RESTART_DELAY_MS = 2500;
/** 도메인이 올라온 뒤 앱이 명령을 내기까지 (가짜 값) */
export const BOOT_MS = 1500;
const COMMAND_PERIOD_MS = 20; // 50 Hz
const MAX_EVENTS = 40;
const SPEED_LIMIT = 0.5; // m/s (시나리오 측정 지표)

function app(health: Health, now: number, domain: AppStatus['domain'] = 'running'): AppStatus {
  return { domain, health, healthSince: now, lastCommandAt: null, commandHz: 0, cpuStress: false };
}

export function initialTelemetry(now: number): Telemetry {
  return {
    now,
    mode: 'AUTO',
    activeApp: 'autonomous',
    generation: 1,
    apps: { autonomous: app('ok', now), manual: app('ok', now), interaction: app('ok', now) },
    commands: { autonomous: null, manual: null },
    perception: { lane_offset_m: 0, heading_err_rad: 0, obstacles: [], stop: false },
    vehicle: {
      applied_generation: 1,
      v_mps: 0,
      steer_rad: 0,
      flags: { estop: false, watchdog: false, obstacle: false },
      batt_v: 7.9,
    },
    manualKeys: [],
    autonomousFreshSince: now,
    events: [{ id: 1, at: now, type: 'drive.mode.changed', level: 'info', text: '시작 — 자율주행(AUTO)' }],
  };
}

let eventSeq = 1;
function pushEvent(t: Telemetry, type: string, level: TimelineEvent['level'], text: string): Telemetry {
  const events = [{ id: ++eventSeq, at: t.now, type, level, text }, ...t.events].slice(0, MAX_EVENTS);
  return { ...t, events };
}

function setHealth(t: Telemetry, id: AppId, health: Health, patch: Partial<AppStatus> = {}): Telemetry {
  const current = t.apps[id];
  const changed = current.health !== health;
  return {
    ...t,
    apps: {
      ...t.apps,
      [id]: { ...current, ...patch, health, healthSince: changed ? t.now : current.healthSince },
    },
  };
}

function fresh(t: Telemetry, id: 'autonomous' | 'manual'): boolean {
  const last = t.apps[id].lastCommandAt;
  return last !== null && t.now - last <= COMMAND_TIMEOUT_MS;
}

function changeMode(t: Telemetry, mode: DriveMode, reason: string, text: string): Telemetry {
  const activeApp = mode === 'AUTO' ? 'autonomous' : mode === 'SAFE_STOP' ? null : 'manual';
  const next = { ...t, mode, activeApp, generation: t.generation + 1 } as Telemetry;
  return pushEvent(
    next,
    'drive.mode.changed',
    mode === 'SAFE_STOP' ? 'warning' : 'info',
    `${text} (${reason})`,
  );
}

/** 젯슨 인지 결과 흉내: 차선 오프셋이 천천히 흔들리고 가끔 장애물이 나타난다 */
function perceive(now: number): Telemetry['perception'] {
  const s = now / 1000;
  const obstacle = Math.sin(s / 7) > 0.85;
  return {
    lane_offset_m: Number((0.08 * Math.sin(s / 1.7)).toFixed(3)),
    heading_err_rad: Number((0.05 * Math.cos(s / 2.3)).toFixed(3)),
    obstacles: obstacle ? [{ x: 0.1 * Math.sin(s), y: 1.2, w: 0.25 }] : [],
    stop: false,
  };
}

function autonomousCommand(t: Telemetry): DriveCommand {
  const slow = t.perception.obstacles.length > 0;
  return {
    generation: t.generation,
    v_mps: slow ? SPEED_LIMIT / 2 : SPEED_LIMIT,
    steer_rad: Number((-1.5 * t.perception.lane_offset_m - 0.8 * t.perception.heading_err_rad).toFixed(3)),
  };
}

function manualCommand(t: Telemetry): DriveCommand {
  const k = new Set(t.manualKeys);
  const v = k.has('up') ? SPEED_LIMIT * 0.6 : k.has('down') ? -SPEED_LIMIT * 0.3 : 0; // 입력 없으면 v=0 (계약 §5.2)
  const steer = k.has('left') ? 0.3 : k.has('right') ? -0.3 : 0;
  return { generation: t.generation, v_mps: v, steer_rad: steer };
}

/** 앱이 살아 있으면 50 Hz 로 명령을 낸다. 부하가 걸리면 간격이 늘어난다. */
function emitCommands(t: Telemetry, dt: number): Telemetry {
  let next = t;
  for (const id of ['autonomous', 'manual'] as const) {
    const a = next.apps[id];
    const alive = a.domain === 'running' && a.health !== 'fault' && a.health !== 'stopped';
    if (!alive) {
      next = { ...next, apps: { ...next.apps, [id]: { ...a, commandHz: 0 } } };
      continue;
    }
    // 부하 시 간격이 150 ms 로 벌어진다 → 게이트웨이 100 ms 판정에 걸림 (시나리오 F2)
    const period = a.cpuStress ? 150 : COMMAND_PERIOD_MS;
    const due = a.lastCommandAt === null || next.now - a.lastCommandAt >= period;
    const hz = Math.round(1000 / period);
    if (!due) {
      next = { ...next, apps: { ...next.apps, [id]: { ...a, commandHz: hz } } };
      continue;
    }
    const command = id === 'autonomous' ? autonomousCommand(next) : manualCommand(next);
    next = {
      ...next,
      apps: { ...next.apps, [id]: { ...a, lastCommandAt: next.now, commandHz: hz } },
      commands: { ...next.commands, [id]: command },
    };
  }
  void dt;
  return next;
}

/** dom0 게이트웨이 판정 (MQTT 계약 §5.2) */
function gateway(t: Telemetry): Telemetry {
  let next = t;
  if (next.mode === 'AUTO' && !fresh(next, 'autonomous')) {
    next = pushEvent(next, 'drive.fault.detected', 'warning', '자율주행 명령 100 ms 끊김');
    next = fresh(next, 'manual')
      ? changeMode(next, 'MANUAL', 'command_timeout', '수동주행으로 즉시 인계')
      : changeMode(next, 'SAFE_STOP', 'no_fresh_standby', '넘길 앱 없음 — 안전 정지');
  } else if ((next.mode === 'MANUAL' || next.mode === 'AUTO_READY') && !fresh(next, 'manual')) {
    next = changeMode(next, 'SAFE_STOP', 'no_fresh_standby', '수동주행도 끊김 — 안전 정지');
  } else if (next.mode === 'SAFE_STOP' && fresh(next, 'manual')) {
    next = changeMode(next, 'MANUAL', 'command_timeout', '수동주행 명령 복귀');
  }

  // AUTO_READY: MANUAL 중 자율 명령이 3초 연속 신선 (Zephyr 라 에이전트 대신 명령 신선도, 시나리오 §8 D8)
  const autoFresh = fresh(next, 'autonomous');
  const freshSince = autoFresh ? (next.autonomousFreshSince ?? next.now) : null;
  next = { ...next, autonomousFreshSince: freshSince };
  if (next.mode === 'MANUAL' && freshSince !== null && next.now - freshSince >= AUTO_READY_FRESH_MS) {
    next = changeMode(next, 'AUTO_READY', 'resume_ready', '자율주행 재개 가능');
    next = setHealth(next, 'autonomous', 'ok');
  }
  return next;
}

/** 복구 서비스: 죽은 VM 재시작 (시나리오 §4.2, §4.3) */
function recovery(t: Telemetry): Telemetry {
  let next = t;
  for (const id of ['autonomous', 'manual', 'interaction'] as const) {
    const a = next.apps[id];
    const elapsed = next.now - a.healthSince;
    if (a.domain === 'crashed' && elapsed >= RESTART_DELAY_MS) {
      next = setHealth(next, id, 'recovering', { domain: 'booting', lastCommandAt: null });
      next = pushEvent(next, 'vm.restarted', 'info', `${label(id)} VM 재시작`);
    } else if (a.domain === 'booting' && elapsed >= BOOT_MS) {
      next = { ...next, apps: { ...next.apps, [id]: { ...a, domain: 'running' } } };
      if (id === 'interaction') {
        next = setHealth(next, id, 'ok');
        next = pushEvent(next, 'vm.recovered', 'ok', '네비게이션 VM 복구 완료');
      } else if (id === 'manual') {
        next = setHealth(next, id, 'ok');
        next = pushEvent(next, 'vm.recovered', 'ok', '수동주행 VM 복구 완료 (대기)');
      }
      // 자율주행은 명령 3초 신선 → AUTO_READY 에서 ok 로 바뀐다 (gateway)
    }
  }
  return next;
}

/** ESP32: 활성 앱의 현재 세대 명령만 적용 (MQTT 계약 §5.5) */
function vehicle(t: Telemetry): Telemetry {
  const command = t.activeApp ? t.commands[t.activeApp] : null;
  const accepted = command && command.generation === t.generation && t.mode !== 'SAFE_STOP';
  const target = accepted ? command : { v_mps: 0, steer_rad: 0 };
  const v = t.vehicle.v_mps + (target.v_mps - t.vehicle.v_mps) * 0.3;
  return {
    ...t,
    vehicle: {
      ...t.vehicle,
      applied_generation: accepted ? t.generation : t.vehicle.applied_generation,
      v_mps: Math.abs(v) < 0.005 ? 0 : Number(v.toFixed(3)),
      steer_rad: Number((t.vehicle.steer_rad + (target.steer_rad - t.vehicle.steer_rad) * 0.4).toFixed(3)),
      flags: { ...t.vehicle.flags, obstacle: t.perception.obstacles.length > 0 },
      batt_v: Number(Math.max(6.6, t.vehicle.batt_v - 0.00002).toFixed(3)),
    },
  };
}

export function step(t: Telemetry, now: number): Telemetry {
  const dt = now - t.now;
  let next: Telemetry = { ...t, now, perception: perceive(now) };
  next = recovery(next);
  next = emitCommands(next, dt);
  next = gateway(next);
  next = vehicle(next);
  return next;
}

export function label(id: AppId): string {
  return id === 'autonomous' ? '자율주행' : id === 'manual' ? '수동주행' : '네비게이션';
}

// ---- 조작 (관리 콘솔·개발 조작 바에서 부른다) -------------------------------------------

/** VM 강제 종료 (데모 D3·D5) */
export function crash(t: Telemetry, id: AppId): Telemetry {
  if (t.apps[id].domain !== 'running') return t;
  const next = setHealth(t, id, 'fault', { domain: 'crashed', commandHz: 0, cpuStress: false });
  return pushEvent(next, 'vm.crashed', 'warning', `${label(id)} VM 강제 종료`);
}

/** CPU 부하 토글 (데모 D2) */
export function toggleStress(t: Telemetry, id: AppId): Telemetry {
  const a = t.apps[id];
  const next = { ...t, apps: { ...t.apps, [id]: { ...a, cpuStress: !a.cpuStress } } };
  return pushEvent(next, 'agent.command', 'info', `${label(id)} CPU 부하 ${a.cpuStress ? '해제' : '시작'}`);
}

/** 네비게이션에서 자율주행 재개 요청 — AUTO_READY 에서만 받는다 (계약 §6) */
export function resume(t: Telemetry): Telemetry {
  if (t.mode !== 'AUTO_READY') return t;
  const next = pushEvent(t, 'drive.resume.requested', 'info', '네비게이션: 자율주행 재개 요청');
  return changeMode(next, 'AUTO', 'resume_requested', '자율주행 복귀');
}

export function setManualKeys(t: Telemetry, keys: string[]): Telemetry {
  return { ...t, manualKeys: keys };
}
