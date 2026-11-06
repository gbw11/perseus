import type { DisplayVm, Presentation, Source } from './model';

/**
 * 계기판용 제안 확장 필드 (dom0 표시 수집기는 아직 보내지 않는다 — 노트북 미리보기 가짜 수집기만 보냄).
 *  - state.vehicle: vehicle.status (MQTT 계약 5.4, MQTT-10) — 게이트웨이가 ESP32 실측을 변환
 *  - state.drive_apps[vm_id].autonomous|manual: Zephyr 주행 펌웨어 보고
 *      heartbeat (6.1) · lifecycle (6.3 warm-drive-1) · command (5.4 drive.command) · perception (perception.event)
 * 규격 확정 전이라 parsePresentation 은 이 필드를 검사하지 않는다. 형식이 틀리거나 없으면 null 로 보고
 * 화면은 "미수신" 으로 그린다 (값을 지어내지 않는다).
 */
export interface VehicleStatus {
  v_mps: number | null;
  batt_v: number | null;
  steer_rad: number | null;
  flags: { estop: boolean; watchdog: boolean; obstacle: boolean } | null;
  source: Source;
}

export interface DriveAppReport {
  heartbeat: { state: 'active' | 'standby' | 'error'; uptime_s: number } | null;
  lifecycle: {
    phase: 'WAITING' | 'ACTIVATING' | 'EXECUTING' | 'STOPPING' | 'RESIDUAL';
    readiness: 'QUALIFIED' | 'READY' | 'NOT_READY';
    reason: string | null;
    applied_generation: number;
  } | null;
  command: { generation: number; v_mps: number; steer_rad: number } | null;
  perception: { kind: string; detail: string | null } | null;
  source: Source;
}

export interface DriveApps {
  autonomous: DriveAppReport | null;
  manual: DriveAppReport | null;
}

const object = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const SOURCES = ['fresh', 'stale', 'disconnected', 'unknown'];

function source(v: unknown): Source | null {
  if (!object(v) || !SOURCES.includes(String(v.status))) return null;
  return { status: v.status as Source['status'], age_ms: finite(v.age_ms) ? v.age_ms : null };
}

export function vehicleOf(p: Presentation | null): VehicleStatus | null {
  const v = (p?.state as Record<string, unknown> | undefined)?.vehicle;
  if (!object(v)) return null;
  const src = source(v.source);
  if (!src) return null;
  const f = v.flags;
  return {
    v_mps: finite(v.v_mps) ? v.v_mps : null,
    batt_v: finite(v.batt_v) ? v.batt_v : null,
    steer_rad: finite(v.steer_rad) ? v.steer_rad : null,
    flags:
      object(f) &&
      typeof f.estop === 'boolean' &&
      typeof f.watchdog === 'boolean' &&
      typeof f.obstacle === 'boolean'
        ? { estop: f.estop, watchdog: f.watchdog, obstacle: f.obstacle }
        : null,
    source: src,
  };
}

function report(v: unknown): DriveAppReport | null {
  if (!object(v)) return null;
  const src = source(v.source);
  if (!src) return null;
  const hb = v.heartbeat;
  const lc = v.lifecycle;
  const cmd = v.command;
  const per = v.perception;
  return {
    heartbeat:
      object(hb) && ['active', 'standby', 'error'].includes(String(hb.state)) && finite(hb.uptime_s)
        ? { state: hb.state as 'active' | 'standby' | 'error', uptime_s: hb.uptime_s }
        : null,
    lifecycle:
      object(lc) &&
      ['WAITING', 'ACTIVATING', 'EXECUTING', 'STOPPING', 'RESIDUAL'].includes(String(lc.phase)) &&
      ['QUALIFIED', 'READY', 'NOT_READY'].includes(String(lc.readiness)) &&
      Number.isSafeInteger(lc.applied_generation)
        ? {
            phase: lc.phase as NonNullable<DriveAppReport['lifecycle']>['phase'],
            readiness: lc.readiness as NonNullable<DriveAppReport['lifecycle']>['readiness'],
            reason: typeof lc.reason === 'string' ? lc.reason : null,
            applied_generation: lc.applied_generation as number,
          }
        : null,
    command:
      object(cmd) && Number.isSafeInteger(cmd.generation) && finite(cmd.v_mps) && finite(cmd.steer_rad)
        ? { generation: cmd.generation as number, v_mps: cmd.v_mps, steer_rad: cmd.steer_rad }
        : null,
    perception:
      object(per) && typeof per.kind === 'string'
        ? { kind: per.kind, detail: typeof per.detail === 'string' ? per.detail : null }
        : null,
    source: src,
  };
}

export function driveAppsOf(p: Presentation | null, vmId: string): DriveApps | null {
  const all = (p?.state as Record<string, unknown> | undefined)?.drive_apps;
  if (!object(all) || !object(all[vmId])) return null;
  const apps = all[vmId];
  return { autonomous: report(apps.autonomous), manual: report(apps.manual) };
}

export type AppTone = 'drive' | 'ok' | 'warning' | 'danger' | 'muted';

type XenView = Pick<DisplayVm, 'xen_state' | 'sources'>;

/**
 * Xen 상태로 이미 정해지는 한 줄 (running 이 아니거나 수신이 끊긴 경우). running·신선하면 null.
 * 수집기는 Xen 조회가 실패해도 마지막 xen_state 를 남기고 sources.xen 만 stale 로 바꾸므로
 * 신선도를 먼저 본다 (수집기 policy.decide_vm 과 같은 순서).
 */
function xenStatus(vm: XenView): { label: string; tone: AppTone } | null {
  if (vm.sources.xen.status !== 'fresh' || vm.xen_state === null)
    return { label: 'VM 상태 미확인', tone: 'muted' };
  if (vm.xen_state === 'running') return null;
  if (vm.xen_state === 'crashed') return { label: 'VM 장애', tone: 'danger' };
  if (vm.xen_state === 'paused') return { label: '일시 정지', tone: 'warning' };
  if (vm.xen_state === 'stopping') return { label: '종료 중', tone: 'warning' };
  return { label: 'VM 꺼짐', tone: 'muted' };
}

/** 합친 차량 화면의 VM 한 줄 — 인포테인먼트는 켜짐 여부만, 주행 VM 은 주행 앱 상태 */
export function vmLineStatus(
  p: Presentation | null,
  vm: XenView & Pick<DisplayVm, 'vm_id' | 'role'>,
  connected: boolean,
): { label: string; tone: AppTone } {
  if (vm.role !== 'interaction') return driveAppStatus(driveAppsOf(p, vm.vm_id), vm, connected);
  if (!connected) return { label: '연결 미확인', tone: 'muted' };
  return xenStatus(vm) ?? { label: '인포테인먼트', tone: 'ok' };
}

/** VM 하나의 주행 앱 상태를 한 줄로 (자율·수동 중 지금 쓰는 쪽 우선) */
export function driveAppStatus(
  apps: DriveApps | null,
  vm: XenView,
  connected: boolean,
): { label: string; tone: AppTone } {
  if (!connected) return { label: '표시 연결 미확인', tone: 'muted' };
  const xen = xenStatus(vm);
  if (xen) return xen;
  if (!apps) return { label: '앱 보고 미수신', tone: 'muted' };
  const list = [apps.autonomous, apps.manual].filter((a): a is DriveAppReport => a !== null);
  if (!list.length || list.every((a) => a.source.status !== 'fresh' || !a.heartbeat))
    return { label: '응답 없음', tone: 'danger' };
  const fresh = list.filter((a) => a.source.status === 'fresh');
  if (fresh.some((a) => a.heartbeat?.state === 'error')) return { label: '앱 오류', tone: 'danger' };
  if (fresh.some((a) => a.lifecycle?.phase === 'EXECUTING' && a.heartbeat?.state === 'active'))
    return { label: '주행 계산 중', tone: 'drive' };
  if (fresh.some((a) => a.lifecycle?.phase === 'ACTIVATING')) return { label: '활성화 중', tone: 'warning' };
  if (fresh.some((a) => a.lifecycle?.readiness === 'NOT_READY'))
    return { label: '준비 안 됨', tone: 'warning' };
  if (fresh.some((a) => a.lifecycle?.readiness === 'QUALIFIED'))
    return { label: '대기 · 준비 완료', tone: 'ok' };
  return { label: '대기', tone: 'ok' };
}

/** 지금 주행 중인 앱의 명령·인지 (활성 VM 의 신선한 보고만) */
export function activeCommand(p: Presentation | null, connected: boolean) {
  const vmId = connected ? p?.state.drive.active_vm_id : null;
  if (!vmId || !p) return null;
  const apps = driveAppsOf(p, vmId);
  const app = p.state.drive.mode === 'MANUAL' ? apps?.manual : apps?.autonomous;
  if (!app || app.source.status !== 'fresh') return null;
  return { command: app.command, perception: app.perception };
}

/** 주행 프로필 한도 (state.drive_limits, 제안). 펌웨어 프로필 max_velocity */
export function maxSpeedOf(p: Presentation | null): number | null {
  const v = (p?.state as Record<string, unknown> | undefined)?.drive_limits;
  return object(v) && finite(v.max_v_mps) && v.max_v_mps > 0 ? v.max_v_mps : null;
}

/** 트립 (state.trip, 제안): 거리·주행 시간 */
export function tripOf(p: Presentation | null): { distance_m: number; driving_s: number } | null {
  const v = (p?.state as Record<string, unknown> | undefined)?.trip;
  if (!object(v) || source(v.source)?.status !== 'fresh') return null;
  return finite(v.distance_m) && finite(v.driving_s)
    ? { distance_m: v.distance_m, driving_s: v.driving_s }
    : null;
}

export interface WorldObject {
  id: string;
  kind: 'car' | 'traffic_light' | 'unknown';
  /** 신호등이면 인식한 불빛, 모르면 null */
  state: 'red' | 'yellow' | 'green' | null;
  /** 차의 진행 방향: 같은 방향 / 마주 옴 / 교차로를 가로지름 (왼쪽으로·오른쪽으로, 방향 모르면 cross) */
  heading: 'same' | 'oncoming' | 'cross' | 'cross_left' | 'cross_right';
  /** 차 기준 상대 위치 (m): x 오른쪽 +, y 앞쪽 + */
  x_m: number;
  y_m: number;
}

/** 주변 물체 (state.world.objects, 제안). 신선하지 않으면 그리지 않는다 */
export function worldObjectsOf(p: Presentation | null): WorldObject[] | null {
  const v = (p?.state as Record<string, unknown> | undefined)?.world;
  if (!object(v) || source(v.source)?.status !== 'fresh' || !Array.isArray(v.objects)) return null;
  return v.objects.flatMap((o) =>
    object(o) && typeof o.id === 'string' && finite(o.x_m) && finite(o.y_m)
      ? [
          {
            id: o.id,
            kind: (['car', 'traffic_light'].includes(String(o.kind))
              ? o.kind
              : 'unknown') as WorldObject['kind'],
            state: (['red', 'yellow', 'green'].includes(String(o.state))
              ? o.state
              : null) as WorldObject['state'],
            heading: (['oncoming', 'cross', 'cross_left', 'cross_right'].includes(String(o.heading))
              ? o.heading
              : 'same') as WorldObject['heading'],
            x_m: o.x_m,
            y_m: o.y_m,
          },
        ]
      : [],
  );
}

/**
 * 완충 기준 연속 주행 가능 시간 (초). 배터리 잔량 센서가 없어 이 값에서 주행한 시간을 빼서 남은 시간을 추정한다.
 * 가정값 — 실제 차의 측정값을 알면 바꾼다
 */
export const RATED_RUNTIME_S = 60 * 60;

/** 예상 주행 가능 시간 (초): 완충 기준 시간 − 이번 주행에서 달린 시간 (트립 주행 시간). 0 아래로 내려가지 않는다 */
export function remainingRuntimeS(drivingS: number): number {
  return Math.max(0, RATED_RUNTIME_S - drivingS);
}

/** 앞에 있는 가장 가까운 신호등 (내 차선 근처, 앞쪽) */
export function signalAhead(objects: WorldObject[]): WorldObject | null {
  return (
    objects
      .filter((o) => o.kind === 'traffic_light' && o.y_m > 0 && Math.abs(o.x_m) < 0.6)
      .sort((a, b) => a.y_m - b.y_m)[0] ?? null
  );
}

export interface RoadInfo {
  name: string;
  /** 한 방향 차로 수 */
  lanes: number;
  /** 내 차로 (1 = 중앙선 옆 1차로). 소수면 차로를 옮기는 중 */
  lane: number;
  /** 반대 방향 차로 수 */
  oncomingLanes: number;
  /**
   * 앞 사거리 (차 기준 거리, 가로지르는 도로). 거리가 음수면 방금 돌아 나온 사거리이고 가로지르는 도로는 지나온 도로다.
   * turn: 경로가 이 사거리에서 꺾는 방향 (직진이면 null)
   */
  intersection: { y_m: number; crossRoad: string; crossLanes: number; turn: 'left' | 'right' | null } | null;
  /**
   * 주행 계획 (차로): lane = 옮겨 갈 차로, 그 뒤 thenAfterM(내 차 앞 RC m) 지점부터 thenLane 으로 돌아간다 (추월).
   * 없으면 지금 차로 그대로
   */
  plan: { lane: number; thenLane: number | null; thenAfterM: number | null } | null;
}

/** 지금 도로 (state.road, 제안). 신선하지 않으면 null — 장면은 기본 3차로로 그린다 */
export function roadOf(p: Presentation | null): RoadInfo | null {
  const v = (p?.state as Record<string, unknown> | undefined)?.road;
  if (!object(v) || source(v.source)?.status !== 'fresh') return null;
  if (!Number.isSafeInteger(v.lanes) || !finite(v.lane) || !Number.isSafeInteger(v.oncoming_lanes))
    return null;
  const i = v.intersection;
  return {
    name: typeof v.name === 'string' ? v.name : '',
    lanes: Math.max(1, v.lanes as number),
    lane: v.lane,
    oncomingLanes: Math.max(0, v.oncoming_lanes as number),
    intersection:
      object(i) && finite(i.y_m)
        ? {
            y_m: i.y_m,
            crossRoad: typeof i.cross_road === 'string' ? i.cross_road : '',
            crossLanes: Number.isSafeInteger(i.cross_lanes) ? (i.cross_lanes as number) : 2,
            turn: i.turn === 'left' || i.turn === 'right' ? i.turn : null,
          }
        : null,
    plan: planOf(v.plan),
  };
}

function planOf(v: unknown): RoadInfo['plan'] {
  if (!object(v) || !finite(v.lane)) return null;
  const then = finite(v.then_lane) && finite(v.then_after_m);
  return {
    lane: v.lane,
    thenLane: then ? (v.then_lane as number) : null,
    thenAfterM: then ? (v.then_after_m as number) : null,
  };
}
