/**
 * 백엔드 없이(VITE_MOCK) 시연 흐름을 돌리는 표본 시나리오 — 주행 통합 상태·이벤트·VM 상태를 함께 바꾼다.
 * 흐름은 RC카 시나리오 §4.1·F1, MQTT 계약 v1.1 §5.2 그대로 (시간만 줄임):
 *   AUTO → [강제 종료] → 자율 명령 끊김 → MANUAL(인계) → 재시작(복구 중) → 3초 연속 정상 → AUTO_READY → [재개] → AUTO
 * 인지 끊김(D5)은 모드를 바꾸지 않는다: 자율주행이 정지 명령(v=0)을 내고 모드는 AUTO 그대로.
 */
import type { DriveApp, DriveKey, DriveMode, DriveState, InputResult } from '../api/drive';
import type { EventSeverity, EventType, VmEvent, VmState } from '../api/types';
import { VM_IDS } from './data';

const TICK_MS = 200;
/** 강제 종료 후 복구 서비스가 재시작하기까지 */
const RESTART_AFTER_MS = 3000;
/** 재시작 후 앱이 다시 명령을 내기까지 */
const BOOT_MS = 5000;
/** MQTT 계약 §5.2 AUTO_READY: 3초 연속 정상 */
const READY_FRESH_MS = 3000;
/** 인계 표본값 (≤150 ms 목표). 계약 이벤트에는 아직 필드가 없다 */
export const HANDOFF_MS = 112;

type Listener = (state: DriveState) => void;
type Target = 'autonomous' | 'manual' | 'message-board';

interface Store {
  mode: DriveMode;
  generation: number;
  reason: string;
  autonomous: { alive: boolean; aliveSince: number | null; downAt: number | null; vmState: VmState };
  perceptionLost: boolean;
  keys: DriveKey[];
  v: number;
  steer: number;
  battV: number;
  estop: boolean;
  obstacle: boolean;
  events: VmEvent[];
}

let seq = 0;

function event(
  type: EventType,
  severity: EventSeverity,
  vmId: string | null,
  payload: Record<string, unknown>,
  at = new Date(),
): VmEvent {
  return {
    event_id: `mock-${++seq}`,
    type,
    severity,
    vm_id: vmId,
    operation_id: null,
    occurred_at: at.toISOString(),
    recorded_at: at.toISOString(),
    payload,
  };
}

function initial(): Store {
  const ago = (ms: number) => new Date(Date.now() - ms);
  return {
    mode: 'AUTO',
    generation: 4,
    reason: 'start',
    autonomous: { alive: true, aliveSince: Date.now() - 60_000, downAt: null, vmState: 'running' },
    perceptionLost: false,
    keys: [],
    v: 0.5,
    steer: 0.07,
    battV: 7.9,
    estop: false,
    obstacle: false,
    events: [
      event(
        'drive.mode.changed',
        'INFO',
        null,
        { from: 'SAFE_STOP', to: 'AUTO', generation: 4, reason: 'start' },
        ago(120_000),
      ),
      event('vm.state.changed', 'INFO', VM_IDS.autonomous, { from: 'stopped', to: 'running' }, ago(125_000)),
    ],
  };
}

let store = initial();
const listeners = new Set<Listener>();
let timer: ReturnType<typeof setInterval> | undefined;

function setMode(to: DriveMode, reason: string, severity: EventSeverity = 'INFO') {
  const from = store.mode;
  store.mode = to;
  store.generation += 1;
  store.reason = reason;
  store.events.unshift(
    event('drive.mode.changed', severity, null, {
      from,
      to,
      generation: store.generation,
      reason,
      ...(reason === 'command_timeout' ? { handoff_ms: HANDOFF_MS } : {}),
    }),
  );
}

function activeApp(): 'autonomous' | 'manual' | null {
  if (store.mode === 'AUTO') return 'autonomous';
  if (store.mode === 'MANUAL' || store.mode === 'AUTO_READY') return 'manual';
  return null;
}

function step() {
  const t = Date.now();
  const a = store.autonomous;
  if (!a.alive && a.downAt != null) {
    if (a.vmState === 'crashed' && t - a.downAt >= RESTART_AFTER_MS) {
      a.vmState = 'running';
      store.events.unshift(event('recovery.started', 'INFO', VM_IDS.autonomous, { action: 'restart' }));
      store.events.unshift(
        event('vm.state.changed', 'INFO', VM_IDS.autonomous, { from: 'crashed', to: 'running' }),
      );
    }
    if (a.vmState === 'running' && t - a.downAt >= RESTART_AFTER_MS + BOOT_MS) {
      a.alive = true;
      a.aliveSince = t;
      a.downAt = null;
      store.events.unshift(event('recovery.completed', 'INFO', VM_IDS.autonomous, {}));
    }
  }
  if (store.mode === 'MANUAL' && a.alive && a.aliveSince != null && t - a.aliveSince >= READY_FRESH_MS) {
    setMode('AUTO_READY', 'autonomous_recovered');
  }
  // 차량은 활성 앱 명령을 따라간다 (시뮬레이터와 같은 규칙: 비상정지·장애물이면 전진 0)
  const active = activeApp();
  let target = 0;
  let steer = 0;
  if (active === 'autonomous' && a.alive && !store.perceptionLost) {
    target = 0.5;
    steer = 0.07;
  } else if (active === 'manual') {
    const k = store.keys;
    target = k.includes('brake') ? 0 : k.includes('up') ? 0.45 : k.includes('down') ? -0.3 : 0;
    steer = k.includes('left') ? 0.3 : k.includes('right') ? -0.3 : 0;
  }
  if (store.estop || (store.obstacle && target > 0)) target = 0;
  store.v += Math.max(-0.08, Math.min(0.08, target - store.v));
  store.steer = steer;
  store.battV = Math.max(6.6, store.battV - 0.00002);
  emit();
}

function app(
  vmId: string,
  fresh: boolean,
  v: number,
  steer: number,
  heartbeat: DriveApp['heartbeat_state'],
): DriveApp {
  return {
    vm_id: vmId,
    command_fresh: fresh,
    command_ago_ms: fresh ? 12 : null,
    command_hz: fresh ? 50 : 0,
    last_command: fresh
      ? { generation: store.generation, v_mps: Math.round(v * 100) / 100, steer_rad: steer }
      : null,
    heartbeat_state: heartbeat,
    heartbeat_ago_ms: heartbeat ? 400 : null,
  };
}

export function snapshot(): DriveState {
  const a = store.autonomous;
  const active = activeApp();
  const manualActive = active === 'manual';
  return {
    version: 1,
    observed_at: new Date().toISOString(),
    control: {
      mode: store.mode,
      generation: store.generation,
      active_app: active,
      active_vm_id: active === 'autonomous' ? VM_IDS.autonomous : manualActive ? VM_IDS.manual : null,
      reason: store.reason,
      received_ago_ms: 300,
    },
    vehicle: {
      applied_generation: store.generation,
      v_mps: Math.round(store.v * 100) / 100,
      steer_rad: store.steer,
      batt_v: Math.round(store.battV * 100) / 100,
      flags: { estop: store.estop, watchdog: false, obstacle: store.obstacle },
      received_ago_ms: 40,
    },
    apps: {
      autonomous: app(
        VM_IDS.autonomous,
        a.alive,
        store.perceptionLost ? 0 : 0.5,
        store.perceptionLost ? 0 : 0.07,
        a.alive ? (active === 'autonomous' ? 'active' : 'standby') : null,
      ),
      manual: app(
        VM_IDS.manual,
        true,
        manualActive ? store.v : 0,
        manualActive ? store.steer : 0,
        manualActive ? 'active' : 'standby',
      ),
      'message-board': {
        vm_id: VM_IDS.interaction,
        command_fresh: null,
        command_ago_ms: null,
        command_hz: 0,
        last_command: null,
        heartbeat_state: 'idle',
        heartbeat_ago_ms: 600,
      },
    },
    perception: store.perceptionLost
      ? {
          valid: false,
          received_ago_ms: 4200,
          hz: 0,
          lane_offset_m: null,
          heading_err_rad: null,
          obstacle_count: 0,
          stop: true,
        }
      : {
          valid: true,
          received_ago_ms: 60,
          hz: 15,
          lane_offset_m: 0.05,
          heading_err_rad: -0.02,
          obstacle_count: store.obstacle ? 1 : 0,
          stop: false,
        },
  };
}

/**
 * 모니터 화면(vm-screens)이 이 시나리오를 따라가게 같은 origin 의 브라우저 채널로 내보낸다.
 * 프레임 모양은 백엔드 WS 와 같다 — /ws/v1/drive/state 의 drive.state, /ws/v1/events 의 event.
 * 콘솔 개발 서버가 /monitor/ 를 모니터 앱으로 넘기므로 (vite.config.ts) 두 화면이 같은 origin 이다.
 */
export const MOCK_BUS = 'xenova-mock-bus';
const bus = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(MOCK_BUS);
const sentEvents = new Set<string>();
/** 늦게 열린 모니터가 hello 를 보내면 지난 이벤트를 다음 주기에 다시 보낸다 */
if (bus) {
  bus.onmessage = (message: MessageEvent<{ type?: string }>) => {
    if (message.data?.type === 'hello') sentEvents.clear();
  };
}

function emit() {
  const state = snapshot();
  listeners.forEach((listener) => listener(state));
  if (!bus) return;
  bus.postMessage({ type: 'drive.state', state });
  for (const e of [...store.events].reverse()) {
    if (sentEvents.has(e.event_id)) continue;
    sentEvents.add(e.event_id);
    bus.postMessage({ type: 'event', payload: e });
  }
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  if (timer === undefined) timer = setInterval(step, TICK_MS);
  listener(snapshot());
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== undefined) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

// ---- 시연 동작 (W-07) ----

const vmOf = (target: Target) =>
  target === 'autonomous' ? VM_IDS.autonomous : target === 'manual' ? VM_IDS.manual : VM_IDS.interaction;

/** 운영자가 자율주행 VM 을 강제 종료 → 게이트웨이가 100 ms 끊김으로 수동주행에 인계 */
export function forceKill(target: Target) {
  store.events.unshift(
    event('demo.command.sent', 'INFO', vmOf(target), { command: 'force_kill', requested_by: 'operator' }),
  );
  const a = store.autonomous;
  if (target === 'autonomous' && a.alive) {
    a.alive = false;
    a.aliveSince = null;
    a.downAt = Date.now();
    a.vmState = 'crashed';
    store.events.unshift(
      event('drive.fault.detected', 'WARNING', VM_IDS.autonomous, {
        app_id: 'autonomous',
        vm_id: VM_IDS.autonomous,
        reason: 'command_timeout',
      }),
    );
    if (store.mode === 'AUTO') setMode('MANUAL', 'command_timeout', 'WARNING');
  }
  emit();
}

/** AUTO_READY 에서만 받는다 (MQTT 계약 §6 drive.resume.requested) */
export function resume(): boolean {
  if (store.mode !== 'AUTO_READY') return false;
  setMode('AUTO', 'resume_requested');
  emit();
  return true;
}

/** 인지 끊김 장면 (D5): 모드는 그대로, 자율주행이 정지 명령 */
export function togglePerception() {
  store.perceptionLost = !store.perceptionLost;
  emit();
}

/** 백엔드와 같은 규칙: MANUAL 이 아니면 입력을 버린다 (MQTT 계약 §7) */
export function setKeys(keys: DriveKey[]): InputResult {
  if (store.mode !== 'MANUAL') {
    store.keys = [];
    return 'NOT_MANUAL';
  }
  store.keys = keys;
  return keys.length ? 'SENT' : 'RELEASED';
}

/** 강제 종료 ~ 복구 사이에만 mock VM 목록의 자율주행 VM 상태를 덮어쓴다 (그 밖에는 시작·정지 mock 을 따른다) */
export function vmStateOf(vmId: string): VmState | null {
  const a = store.autonomous;
  return vmId === VM_IDS.autonomous && a.downAt != null ? a.vmState : null;
}

export function scenarioEvents(): VmEvent[] {
  return store.events;
}

export function resetScenario() {
  store = initial();
}
