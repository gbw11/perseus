/**
 * 백엔드 주행 통합 상태 → 화면 상태. 순수 함수라서 시각(now)을 넣어 단위 시험한다.
 *   백엔드: GET /api/v1/drive/state, WS /ws/v1/drive/state (노션 API 명세 "주행 통합 상태"),
 *           WS /ws/v1/events (WS-02) 의 drive.mode.changed · drive.fault.detected
 *   필드 이름: MQTT 계약 §5.4 / §6 / §6.1 (백엔드가 snake_case 로 내보낸다)
 * "*_ago_ms" 는 백엔드 시계 기준 경과 시간이다. 보드 간 시각 비교는 하지 않는다 (MQTT 계약 §1).
 */
import type { Health } from '@xenova/ui';
import type { AppId, AppStatus, DriveMode, Telemetry, TimelineEvent } from './types';
import { initialTelemetry } from './scenario';

// ---- 백엔드 응답 모양 ---------------------------------------------------------------------

export interface DriveStateDto {
  version: number;
  observed_at: string;
  control: {
    mode: DriveMode | null;
    generation: number | null;
    active_app: 'autonomous' | 'manual' | null;
    active_vm_id: string | null;
    reason: string | null;
    received_ago_ms: number;
  } | null;
  vehicle: {
    applied_generation: number | null;
    v_mps: number | null;
    steer_rad: number | null;
    batt_v: number | null;
    flags: { estop: boolean; watchdog: boolean; obstacle: boolean } | null;
    received_ago_ms: number;
  } | null;
  /** app_id 별. 인터랙션 VM 은 message-board */
  apps: Record<string, DriveAppDto>;
  perception: {
    valid: boolean;
    received_ago_ms: number;
    hz: number;
    lane_offset_m: number | null;
    heading_err_rad: number | null;
    obstacle_count: number;
    stop: boolean;
  } | null;
}

export interface DriveAppDto {
  vm_id: string | null;
  command_fresh: boolean | null;
  command_ago_ms: number | null;
  command_hz: number;
  last_command: { generation: number | null; v_mps: number | null; steer_rad: number | null } | null;
  heartbeat_state: 'active' | 'standby' | 'idle' | 'error' | null;
  heartbeat_ago_ms: number | null;
}

/** REST-18 / WS-02 이벤트 한 건 */
export interface EventDto {
  event_id: string;
  type: string;
  severity: 'INFO' | 'WARNING' | 'ERROR' | string;
  vm_id: string | null;
  occurred_at: string;
  payload: Record<string, unknown> | null;
}

// ---- 변환 ------------------------------------------------------------------------------

/** app.heartbeat 는 1초 주기 (MQTT 계약 §6.1). 3번 놓치면 끊긴 것으로 본다 */
export const HEARTBEAT_STALE_MS = 3000;

const TOPIC_APP_ID: Record<AppId, string> = {
  autonomous: 'autonomous',
  manual: 'manual',
  interaction: 'message-board',
};

function heartbeatFresh(dto: DriveAppDto): boolean {
  return dto.heartbeat_ago_ms !== null && dto.heartbeat_ago_ms <= HEARTBEAT_STALE_MS;
}

/** 주행 앱은 명령 신선도(100 ms, 백엔드가 판정), 인터랙션 앱은 생존 신호로 상태를 정한다 */
function healthOf(id: AppId, dto: DriveAppDto | undefined): Health {
  if (!dto) return 'stopped';
  if (dto.heartbeat_state === 'error' && heartbeatFresh(dto)) return 'fault';
  if (id === 'interaction') return heartbeatFresh(dto) ? 'ok' : 'fault';
  return dto.command_fresh ? 'ok' : 'fault';
}

function appStatus(
  id: AppId,
  dto: DriveAppDto | undefined,
  previous: AppStatus,
  now: number,
  recovering: boolean,
): AppStatus {
  const raw = healthOf(id, dto);
  // 복구 서비스가 재시작한 앱은 다시 정상이 될 때까지 장애가 아니라 복구 중 (RC카 시나리오 §4.3 빨강 → 노랑 → 초록)
  const health = recovering && raw === 'fault' ? 'recovering' : raw;
  const alive = !!dto && (!!dto.command_fresh || heartbeatFresh(dto));
  return {
    domain: alive ? 'running' : recovering ? 'booting' : 'stopped',
    health,
    healthSince: health === previous.health ? previous.healthSince : now,
    lastCommandAt: dto?.command_ago_ms != null ? now - dto.command_ago_ms : null,
    commandHz: dto?.command_hz ?? 0,
    cpuStress: false,
  };
}

/** 빈 상태 — 백엔드에서 아무것도 못 받았을 때 */
export function emptyTelemetry(now: number): Telemetry {
  const base = initialTelemetry(now);
  const idle = (): AppStatus => ({
    domain: 'stopped',
    health: 'stopped',
    healthSince: now,
    lastCommandAt: null,
    commandHz: 0,
    cpuStress: false,
  });
  return {
    ...base,
    mode: 'SAFE_STOP',
    activeApp: null,
    generation: 0,
    apps: { autonomous: idle(), manual: idle(), interaction: idle() },
    vehicle: { ...base.vehicle, applied_generation: 0, batt_v: 0 },
    autonomousFreshSince: null,
    obstacleCount: 0,
    events: [],
  };
}

export function applyDriveState(t: Telemetry, state: DriveStateDto, now: number): Telemetry {
  const apps = {} as Telemetry['apps'];
  const appVmIds: Partial<Record<AppId, string>> = { ...t.appVmIds };
  const recoveringApps = { ...t.recoveringApps };
  for (const id of ['autonomous', 'manual', 'interaction'] as const) {
    const dto = state.apps[TOPIC_APP_ID[id]];
    if (dto?.vm_id) appVmIds[id] = dto.vm_id;
    apps[id] = appStatus(id, dto, t.apps[id], now, !!recoveringApps[id]);
    if (apps[id].health === 'ok') delete recoveringApps[id];
  }
  const command = (id: 'autonomous' | 'manual') => {
    const last = state.apps[id]?.last_command;
    return last
      ? { generation: last.generation ?? 0, v_mps: last.v_mps ?? 0, steer_rad: last.steer_rad ?? 0 }
      : null;
  };
  const autonomousFresh = !!state.apps.autonomous?.command_fresh;
  const control = state.control;
  const vehicle = state.vehicle;
  const perception = state.perception;

  return {
    ...t,
    now,
    mode: control?.mode ?? 'SAFE_STOP',
    activeApp: control?.active_app ?? null,
    generation: control?.generation ?? 0,
    apps,
    appVmIds,
    recoveringApps,
    commands: { autonomous: command('autonomous'), manual: command('manual') },
    perception: {
      lane_offset_m: perception?.lane_offset_m ?? 0,
      heading_err_rad: perception?.heading_err_rad ?? 0,
      // 백엔드는 장애물 수만 준다 (위치는 상세 그림에 쓰지 않는다)
      obstacles: [],
      stop: perception?.stop ?? false,
    },
    obstacleCount: perception?.obstacle_count ?? 0,
    vehicle: {
      applied_generation: vehicle?.applied_generation ?? 0,
      v_mps: vehicle?.v_mps ?? 0,
      steer_rad: vehicle?.steer_rad ?? 0,
      flags: vehicle?.flags ?? { estop: false, watchdog: false, obstacle: false },
      batt_v: vehicle?.batt_v ?? 0,
    },
    autonomousFreshSince: autonomousFresh ? (t.autonomousFreshSince ?? now) : null,
  };
}

const MAX_EVENTS = 40;
/** MQTT 계약 app_id → 화면 이름 */
const APP_TITLE: Record<string, string> = {
  autonomous: '자율주행',
  manual: '수동주행',
  'message-board': '인터랙션',
};

function text(event: EventDto): string {
  const p = event.payload ?? {};
  if (event.type === 'drive.mode.changed') {
    return `모드 ${String(p.from ?? '?')} → ${String(p.to ?? '?')}${p.reason ? ` (${String(p.reason)})` : ''}`;
  }
  if (event.type === 'recovery.started') return 'VM 재시작 (복구 서비스)';
  if (event.type === 'recovery.completed') return 'VM 복구 완료';
  if (event.type === 'drive.fault.detected') {
    const title = APP_TITLE[String(p.app_id)] ?? String(p.app_id ?? '앱');
    return `${title} 장애${p.reason ? ` (${String(p.reason)})` : ''}`;
  }
  return event.type;
}

/** 이벤트 하나를 타임라인 맨 앞에 넣는다. 같은 event_id 는 한 번만 (재연결 때 다시 올 수 있다) */
export function applyEvent(t: Telemetry, event: EventDto, now: number, wallNow = Date.now()): Telemetry {
  const id = hash(event.event_id);
  if (t.events.some((e) => e.id === id)) return t;
  const ageMs = Math.max(0, wallNow - Date.parse(event.occurred_at));
  const level: TimelineEvent['level'] = event.severity === 'INFO' ? 'info' : 'warning';
  const entry: TimelineEvent = { id, at: now - ageMs, type: event.type, level, text: text(event) };
  const next = { ...t, events: [entry, ...t.events].slice(0, MAX_EVENTS) };
  if (event.type === 'recovery.started' && event.vm_id) {
    const app = (Object.keys(t.appVmIds ?? {}) as AppId[]).find((id) => t.appVmIds?.[id] === event.vm_id);
    if (app) {
      next.recoveringApps = { ...t.recoveringApps, [app]: true };
      next.apps = { ...t.apps, [app]: { ...t.apps[app], health: 'recovering', healthSince: now } };
    }
  }
  return next;
}

/** TimelineEvent.id 는 숫자라서 event_id 문자열을 32비트 해시로 바꾼다 */
function hash(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) h = (Math.imul(31, h) + value.charCodeAt(i)) | 0;
  return h;
}
