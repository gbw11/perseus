import type { Obstacle, P2 } from './course';
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

export interface CourseState {
  trackId: string;
  /** 후륜축 위치 (코스 좌표 m, yaw 반시계 rad) — 시뮬레이터 sim.pose 와 같은 기준 */
  pose: { x: number; y: number; yaw: number };
  /** 코스 위 장애물 (sim.world obstacles 와 같은 꼴: 중심 x·y, 폭 w·깊이 h) */
  obstacles: Obstacle[];
  /** 주행 계획 경로 (코스 좌표, 차 앞부터). 수동 운전·정지 중이면 빈 배열 */
  plan: P2[];
}

/**
 * 코스 위 차 위치·장애물·계획 (state.course, 제안). 신선하지 않으면 null — 지도에 차를 그리지 않는다.
 * 실보드 수집기는 아직 보내지 않는다 (sim.pose·sim.world 또는 젯슨 위치 추정이 필요)
 */
export function courseOf(p: Presentation | null): CourseState | null {
  const v = (p?.state as Record<string, unknown> | undefined)?.course;
  if (!object(v) || source(v.source)?.status !== 'fresh') return null;
  const pose = v.pose;
  if (!object(pose) || !finite(pose.x) || !finite(pose.y) || !finite(pose.yaw)) return null;
  const point = (q: unknown): q is P2 => Array.isArray(q) && q.length === 2 && finite(q[0]) && finite(q[1]);
  return {
    trackId: typeof v.track_id === 'string' ? v.track_id : '',
    pose: { x: pose.x, y: pose.y, yaw: pose.yaw },
    obstacles: (Array.isArray(v.obstacles) ? v.obstacles : []).flatMap((o) =>
      object(o) && typeof o.id === 'string' && finite(o.x) && finite(o.y) && finite(o.w) && finite(o.h)
        ? [
            {
              id: o.id,
              kind: typeof o.kind === 'string' ? o.kind : 'unknown',
              x: o.x,
              y: o.y,
              w: o.w,
              h: o.h,
            },
          ]
        : [],
    ),
    plan: Array.isArray(v.plan) ? v.plan.filter(point) : [],
  };
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
