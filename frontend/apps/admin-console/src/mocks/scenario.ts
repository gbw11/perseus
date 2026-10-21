/**
 * 백엔드 없이(VITE_MOCK) 시연 흐름을 돌리는 표본 시나리오 — 주행 통합 상태·이벤트·VM 상태·화면 배치를 함께 바꾼다.
 * 규칙은 MQTT 계약 v1.2 §5.2·§6, RC카 시나리오 §4 (시간만 줄임):
 *   - 자율주행 강제 종료 → 명령 끊김 → MANUAL(인계) → 재시작(복구 중) → 3초 연속 정상 → AUTO_READY
 *   - [재개] → drive.resume.accepted(3초 카운트다운) → AUTO, 그 사이 자율주행이 다시 죽으면 drive.resume.cancelled
 *   - 수동주행이 제어 중 죽으면 SAFE_STOP, 돌아오면 MANUAL. 인터랙션이 죽어도 주행은 그대로 (격리)
 *   - 인지 끊김(D5)은 모드를 바꾸지 않는다: 자율주행이 정지 명령(v=0)을 낸다
 */
import type { DriveApp, DriveKey, DriveMode, DriveState, InputResult } from '../api/drive';
import type { EventSeverity, EventType, VmEvent, VmState } from '../api/types';
import { VM_IDS } from './data';

const TICK_MS = 100;
/** 강제 종료 후 복구 서비스가 재시작하기까지 */
const RESTART_AFTER_MS = 3000;
/** 재시작 후 앱이 다시 명령·생존 신호를 내기까지 */
const BOOT_MS = 5000;
/** MQTT 계약 §5.2 AUTO_READY: 3초 연속 정상 */
const READY_FRESH_MS = 3000;
/** MQTT 계약 v1.2 §6: 재개 받아들인 뒤 전환까지 */
export const RESUME_COUNTDOWN_MS = 3000;
/** 인계 표본값 (≤150 ms 목표). 계약 이벤트에는 아직 필드가 없다 */
export const HANDOFF_MS = 112;

type Listener = (state: DriveState) => void;
export type Target = 'autonomous' | 'manual' | 'message-board';
const TARGETS: Target[] = ['autonomous', 'manual', 'message-board'];

interface AppLife {
  alive: boolean;
  aliveSince: number | null;
  downAt: number | null;
  vmState: VmState;
  /** app.heartbeat 1초 주기의 위상 — 앱마다 달라야 "0.4초 전" 이 고정되지 않는다 */
  beatPhase: number;
}

interface Store {
  mode: DriveMode;
  generation: number;
  reason: string;
  apps: Record<Target, AppLife>;
  resumeAt: number | null;
  perceptionLost: boolean;
  keys: DriveKey[];
  v: number;
  steer: number;
  estop: boolean;
  obstacle: boolean;
  events: VmEvent[];
}

const vmOf = (target: Target) =>
  target === 'autonomous' ? VM_IDS.autonomous : target === 'manual' ? VM_IDS.manual : VM_IDS.interaction;
const NAME: Record<Target, string> = {
  autonomous: '자율주행',
  manual: '수동주행',
  'message-board': '인터랙션',
};

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

function life(phase: number): AppLife {
  return { alive: true, aliveSince: Date.now() - 60_000, downAt: null, vmState: 'running', beatPhase: phase };
}

function initial(): Store {
  const ago = (ms: number) => new Date(Date.now() - ms);
  return {
    mode: 'AUTO',
    generation: 4,
    reason: 'start',
    apps: { autonomous: life(130), manual: life(570), 'message-board': life(820) },
    resumeAt: null,
    perceptionLost: false,
    keys: [],
    v: 0.5,
    steer: 0.07,
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

function push(e: VmEvent) {
  store.events.unshift(e);
}

function setMode(to: DriveMode, reason: string, severity: EventSeverity = 'INFO') {
  const from = store.mode;
  store.mode = to;
  store.generation += 1;
  store.reason = reason;
  push(
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

function cancelResume(reason: string) {
  if (store.resumeAt == null) return;
  store.resumeAt = null;
  push(event('drive.resume.cancelled', 'WARNING', null, { reason, generation: store.generation }));
}

function step() {
  const t = Date.now();
  // 복구 서비스: 죽은 VM 을 재시작하고, 부팅이 끝나면 앱이 다시 명령·생존 신호를 낸다
  for (const target of TARGETS) {
    const a = store.apps[target];
    if (a.alive || a.downAt == null) continue;
    if (a.vmState === 'crashed' && t - a.downAt >= RESTART_AFTER_MS) {
      a.vmState = 'running';
      push(event('recovery.started', 'INFO', vmOf(target), { action: 'restart' }));
      push(event('vm.state.changed', 'INFO', vmOf(target), { from: 'crashed', to: 'running' }));
    }
    if (a.vmState === 'running' && t - a.downAt >= RESTART_AFTER_MS + BOOT_MS) {
      a.alive = true;
      a.aliveSince = t;
      a.downAt = null;
      push(event('recovery.completed', 'INFO', vmOf(target), {}));
    }
  }
  const auto = store.apps.autonomous;
  const manual = store.apps.manual;
  // 게이트웨이 판정 (계약 §5.2)
  if (store.mode === 'SAFE_STOP' && manual.alive) setMode('MANUAL', 'command_timeout');
  if (
    store.mode === 'MANUAL' &&
    auto.alive &&
    auto.aliveSince != null &&
    t - auto.aliveSince >= READY_FRESH_MS
  ) {
    setMode('AUTO_READY', 'autonomous_recovered');
  }
  if (store.mode === 'AUTO_READY' && !auto.alive) {
    cancelResume('autonomous_lost');
    setMode('MANUAL', 'autonomous_lost', 'WARNING'); // AUTO_READY 조건이 깨지면 다시 MANUAL (계약 §5.2)
  }
  if (store.resumeAt != null) {
    if (!auto.alive || store.mode !== 'AUTO_READY') cancelResume('autonomous_lost');
    else if (t >= store.resumeAt) {
      store.resumeAt = null;
      setMode('AUTO', 'resume_requested');
    }
  }
  // 차량은 활성 앱 명령을 따라간다 (시뮬레이터와 같은 규칙: 비상정지·장애물이면 전진 0)
  const active = activeApp();
  let target = 0;
  let steer = 0;
  if (active === 'autonomous' && auto.alive && !store.perceptionLost) {
    target = 0.5;
    steer = 0.07;
  } else if (active === 'manual' && manual.alive) {
    const k = store.keys;
    target = k.includes('brake') ? 0 : k.includes('up') ? 0.45 : k.includes('down') ? -0.3 : 0;
    steer = k.includes('left') ? 0.3 : k.includes('right') ? -0.3 : 0;
  }
  if (store.estop || (store.obstacle && target > 0)) target = 0;
  store.v += Math.max(-0.04, Math.min(0.04, target - store.v));
  store.steer = steer;
  emit();
}

/** 마지막 1초 주기 heartbeat 뒤 지난 시간 (0.1초 단위) */
function beatAgo(a: AppLife, now: number): number | null {
  if (!a.alive) return null;
  return Math.floor(((now - a.beatPhase) % 1000) / 100) * 100;
}

function app(
  target: Target,
  a: AppLife,
  now: number,
  v: number,
  steer: number,
  heartbeat: DriveApp['heartbeat_state'],
  driving = true,
): DriveApp {
  const fresh = driving && a.alive;
  return {
    vm_id: vmOf(target),
    command_fresh: driving ? fresh : null,
    command_ago_ms: fresh ? now % 20 : null,
    command_hz: fresh ? 50 : 0,
    last_command: fresh
      ? { generation: store.generation, v_mps: Math.round(v * 100) / 100, steer_rad: steer }
      : null,
    heartbeat_state: a.alive ? heartbeat : null,
    heartbeat_ago_ms: beatAgo(a, now),
  };
}

export function snapshot(): DriveState {
  const now = Date.now();
  const { autonomous: auto, manual, 'message-board': board } = store.apps;
  const active = activeApp();
  const manualActive = active === 'manual';
  const lost = store.perceptionLost;
  return {
    version: 1,
    observed_at: new Date(now).toISOString(),
    control: {
      mode: store.mode,
      generation: store.generation,
      active_app: active,
      active_vm_id: active ? vmOf(active) : null,
      reason: store.reason,
      received_ago_ms: (now % 1000) - ((now % 1000) % 100),
    },
    vehicle: {
      applied_generation: store.generation,
      v_mps: Math.round(store.v * 100) / 100,
      steer_rad: store.steer,
      batt_v: null,
      flags: { estop: store.estop, watchdog: false, obstacle: store.obstacle },
      received_ago_ms: now % 50,
    },
    apps: {
      autonomous: app(
        'autonomous',
        auto,
        now,
        lost ? 0 : 0.5,
        lost ? 0 : 0.07,
        active === 'autonomous' ? 'active' : 'standby',
      ),
      manual: app(
        'manual',
        manual,
        now,
        manualActive ? store.v : 0,
        manualActive ? store.steer : 0,
        manualActive ? 'active' : 'standby',
      ),
      'message-board': app('message-board', board, now, 0, 0, 'idle', false),
    },
    perception: lost
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
          received_ago_ms: now % 67,
          hz: 15,
          lane_offset_m: 0.05,
          heading_err_rad: -0.02,
          obstacle_count: store.obstacle ? 1 : 0,
          stop: false,
        },
    resume:
      store.resumeAt == null
        ? null
        : {
            generation: store.generation,
            switch_in_ms: RESUME_COUNTDOWN_MS,
            accepted_ago_ms: RESUME_COUNTDOWN_MS - Math.max(0, store.resumeAt - now),
            remaining_ms: Math.max(0, store.resumeAt - now),
          },
  };
}

// ---- 모니터 화면이 따라오는 채널 ----

/**
 * 모니터 화면(vm-screens)이 이 시나리오를 따라가게 같은 origin 의 브라우저 채널로 내보낸다.
 * 프레임 모양은 백엔드 WS 와 같다 — /ws/v1/drive/state 의 drive.state, /ws/v1/events 의 event,
 * /ws/v1/displays 의 display.snapshot. 콘솔 개발 서버가 /monitor/ 를 모니터 앱으로 넘기므로 같은 origin 이다.
 */
export const MOCK_BUS = 'xenova-mock-bus';
const bus = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(MOCK_BUS);
const sentEvents = new Set<string>();
if (bus) {
  bus.onmessage = (message: MessageEvent<{ type?: string; display_id?: string; revision?: number }>) => {
    const frame = message.data;
    if (frame?.type === 'hello') sentEvents.clear(); // 늦게 열린 모니터에 지난 이벤트를 다시 보낸다
    if (frame?.type === 'display.hello' || frame?.type === 'hello') publishDisplays();
    if (frame?.type === 'route.applied' && frame.display_id) applied(frame.display_id, frame.revision ?? 0);
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

/** 운영자가 VM 을 강제 종료 (도메인만, 디스크는 그대로). 복구 서비스가 다시 띄운다 */
export function forceKill(target: Target) {
  push(event('demo.command.sent', 'INFO', vmOf(target), { command: 'force_kill', requested_by: 'operator' }));
  const a = store.apps[target];
  if (!a.alive || a.vmState !== 'running') return emit();
  a.alive = false;
  a.aliveSince = null;
  a.downAt = Date.now();
  a.vmState = 'crashed';
  if (target === 'message-board') return emit(); // 주행과 무관 — 격리
  push(
    event('drive.fault.detected', 'WARNING', vmOf(target), {
      app_id: target,
      vm_id: vmOf(target),
      reason: 'command_timeout',
    }),
  );
  if (target === 'autonomous') {
    cancelResume('autonomous_lost');
    if (store.mode === 'AUTO')
      setMode(store.apps.manual.alive ? 'MANUAL' : 'SAFE_STOP', 'command_timeout', 'WARNING');
  } else if (activeApp() === 'manual') {
    cancelResume('manual_lost');
    setMode('SAFE_STOP', 'no_fresh_standby', 'WARNING');
  }
  emit();
}

/** AUTO_READY 에서만 받는다. 바로 바꾸지 않고 3초 카운트다운 (MQTT 계약 v1.2 §6) */
export function resume(): boolean {
  if (store.mode !== 'AUTO_READY' || store.resumeAt != null) return false;
  store.resumeAt = Date.now() + RESUME_COUNTDOWN_MS;
  push(
    event('drive.resume.accepted', 'INFO', null, {
      switch_in_ms: RESUME_COUNTDOWN_MS,
      generation: store.generation,
      requested_by: 'admin',
    }),
  );
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

/** 강제 종료 ~ 복구 사이에만 mock VM 목록의 상태를 덮어쓴다 (그 밖에는 시작·정지 mock 을 따른다) */
export function vmStateOf(vmId: string): VmState | null {
  const target = TARGETS.find((t) => vmOf(t) === vmId);
  const a = target && store.apps[target];
  return a && a.downAt != null ? a.vmState : null;
}

export function scenarioEvents(): VmEvent[] {
  return store.events;
}

// ---- 화면 배치 (REST-23·REST-24 mock, 백엔드 -129 와 같은 모양) ----

export interface MockRoute {
  vm_id: string | null;
  view: 'split' | 'os-face';
  request_id: string;
  applied_at: string | null;
  verification_level: string;
}

export interface MockDisplay {
  display_id: string;
  label: string;
  connected: boolean;
  status: 'DISCONNECTED' | 'IDLE' | 'SWITCHING' | 'ACTIVE' | 'ERROR';
  supported_views: string[];
  revision: number;
  desired_route: MockRoute;
  observed_route: MockRoute | null;
  last_error: null;
}

function display(id: string, label: string, view: 'split' | 'os-face', vmId: string | null): MockDisplay {
  const route: MockRoute = {
    vm_id: vmId,
    view,
    request_id: `${id}-1`,
    applied_at: new Date().toISOString(),
    verification_level: 'browser_ready',
  };
  return {
    display_id: id,
    label,
    connected: false,
    status: 'ACTIVE',
    supported_views: ['split', 'os-face'],
    revision: 1,
    desired_route: route,
    observed_route: route,
    last_error: null,
  };
}

let displays: MockDisplay[] = [
  display('display-0', '모니터 1', 'split', null),
  display('display-1', '모니터 2', 'os-face', VM_IDS.autonomous),
];

function publishDisplays() {
  bus?.postMessage({ type: 'display.snapshot', items: displays });
}

export function getDisplays(): MockDisplay[] {
  return displays;
}

export class RouteError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

export function setRoute(
  id: string,
  body: { vm_id?: string | null; view?: string; expected_revision?: number },
) {
  const current = displays.find((d) => d.display_id === id);
  if (!current) throw new RouteError(404, 'DISPLAY_NOT_FOUND');
  if (body.view !== 'split' && body.view !== 'os-face') throw new RouteError(422, 'UNSUPPORTED_VIEW');
  if ((body.view === 'os-face') !== !!body.vm_id) throw new RouteError(422, 'VALIDATION_ERROR');
  if (body.vm_id && !TARGETS.some((t) => vmOf(t) === body.vm_id)) throw new RouteError(404, 'VM_NOT_FOUND');
  if (body.expected_revision !== current.revision) throw new RouteError(409, 'DISPLAY_REVISION_CONFLICT');
  const revision = current.revision + 1;
  const next: MockDisplay = {
    ...current,
    revision,
    status: 'SWITCHING',
    desired_route: {
      vm_id: body.vm_id ?? null,
      view: body.view,
      request_id: `${id}-${revision}`,
      applied_at: null,
      verification_level: 'service_applied',
    },
  };
  displays = displays.map((d) => (d.display_id === id ? next : d));
  publishDisplays();
  // 따라오는 모니터가 없으면 잠시 뒤 적용된 것으로 본다 (mock)
  setTimeout(() => {
    const d = displays.find((x) => x.display_id === id);
    if (d && d.revision === revision && d.status === 'SWITCHING') applied(id, revision);
  }, 1500);
  return next;
}

function applied(id: string, revision: number) {
  displays = displays.map((d) =>
    d.display_id === id && d.revision === revision
      ? {
          ...d,
          connected: true,
          status: 'ACTIVE',
          observed_route: {
            ...d.desired_route,
            applied_at: new Date().toISOString(),
            verification_level: 'browser_ready',
          },
        }
      : d,
  );
  publishDisplays();
}

export function resetScenario() {
  store = initial();
  displays = [
    display('display-0', '모니터 1', 'split', null),
    display('display-1', '모니터 2', 'os-face', VM_IDS.autonomous),
  ];
}

export { NAME as TARGET_NAME };
