/**
 * VM 역할은 백엔드가 주는 vm.role(DB vms.role, 배포자가 VM 등록·수정 때 지정)로만 붙인다.
 * VM 이름·app_id 로 추정하지 않는다. 기존 app_id 키는 통신 호환용이며 역할의 근거가 아니다.
 * 두 Zephyr는 주 구동·백업으로 구분하고 AUTO/MANUAL 모드와 분리한다.
 * 화면은 VM 을 고정 칸(VM A·B·C)이 아니라 API 목록 그대로 VM 이름으로 보여 준다.
 */
import type { Health } from '@xenova/ui';
import type { DriveApp, DriveMode, DriveState } from '../api/drive';
import type { Vm, VmRole } from '../api/types';
import { STATE_HEALTH } from '../format';

export type AppId = 'autonomous' | 'manual' | 'interaction';

export interface Role {
  app: AppId;
  vmRole: VmRole;
  title: string;
  short: string;
  color: string;
  /** 주행 명령을 내는 앱인가 (명령 신선도로 상태를 본다) */
  driving: boolean;
}

export const ROLES: Record<AppId, Role> = {
  autonomous: {
    app: 'autonomous',
    vmRole: 'primary_drive',
    title: '주 구동 VM',
    short: '주 구동',
    color: 'var(--x-app-autonomous)',
    driving: true,
  },
  manual: {
    app: 'manual',
    vmRole: 'backup_drive',
    title: '백업 VM',
    short: '백업',
    color: 'var(--x-app-manual)',
    driving: true,
  },
  interaction: {
    app: 'interaction',
    vmRole: 'interaction',
    title: '인터랙션 VM',
    short: '인터랙션',
    color: 'var(--x-app-interaction)',
    driving: false,
  },
};

export const ROLE_ORDER: AppId[] = ['autonomous', 'manual', 'interaction'];

/** 역할 미지정 VM 의 카드 색 */
export const UNASSIGNED_COLOR = 'var(--x-text-muted)';

/** vm.role 로만 역할을 붙인다. 미지정(null)이면 null */
export function roleOf(vm: Vm, _drive?: DriveState | null): Role | null {
  if (!vm.role) return null;
  return ROLE_ORDER.map((app) => ROLES[app]).find((role) => role.vmRole === vm.role) ?? null;
}

export function vmOf(app: AppId, vms: Vm[] | undefined, _drive?: DriveState | null): Vm | undefined {
  return vms?.find((vm) => vm.role === ROLES[app].vmRole);
}

/** 화면 표시 순서: 주 구동 → 백업 → 인터랙션 → 역할 미지정(이름순) */
export function sortVms(vms: Vm[]): Vm[] {
  const rank = (vm: Vm) => {
    const role = roleOf(vm);
    return role ? ROLE_ORDER.indexOf(role.app) : ROLE_ORDER.length;
  };
  return [...vms].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/** 앱 보고(drive.apps)는 app_id 키라서 vm_id 로 이 VM 의 보고를 찾는다 */
export function appReportOf(vm: Vm, drive: DriveState | null): DriveApp | undefined {
  return Object.values(drive?.apps ?? {}).find((app) => app?.vm_id === vm.vm_id);
}

export const MODE_LABEL: Record<DriveMode, string> = {
  AUTO: '자율주행',
  MANUAL: '수동주행',
  AUTO_READY: '재개 가능',
  SAFE_STOP: '안전 정지',
};

/** 모드 색: AUTO 정상, MANUAL·AUTO_READY 전환 중(노랑), SAFE_STOP 장애 (DESIGN.md 상태 규칙) */
export const MODE_HEALTH: Record<DriveMode, Health> = {
  AUTO: 'ok',
  MANUAL: 'recovering',
  AUTO_READY: 'recovering',
  SAFE_STOP: 'fault',
};

/**
 * 앱 heartbeat 신선 기준 — backend DriveStateTracker HEARTBEAT_FRESH_MS(3000, age <= 3000 이면 fresh)와
 * domain/driveObservations 와 같은 경계.
 */
const HEARTBEAT_FRESH_MS = 3000;
const heartbeatFresh = (app: DriveApp | undefined) =>
  app?.heartbeat_ago_ms != null && app.heartbeat_ago_ms <= HEARTBEAT_FRESH_MS;

/**
 * 앱 칸 상태 배지 (화면 정의서 §3 배지 4종).
 * Xen 상태와 동일 UUID의 앱 heartbeat를 구분한다. 미수신은 경고색이며 라벨은 미확인으로 표시한다.
 */
export function appHealth(_role: Role | null, vm: Vm | undefined, app: DriveApp | undefined): Health {
  if (vm && vm.state !== 'running') return STATE_HEALTH[vm.state];
  if (!app) return vm ? 'recovering' : 'stopped';
  if (!vm || app.vm_id !== vm.vm_id) return 'recovering';
  if (!heartbeatFresh(app)) return 'recovering';
  return app.heartbeat_state === 'error' ? 'fault' : 'ok';
}

export function appHealthLabel(vm: Vm | undefined, app: DriveApp | undefined): string | undefined {
  if (!vm) return 'VM 역할/등록 미확인';
  if (vm.state !== 'running') return undefined;
  if (app?.vm_id !== vm.vm_id) return '앱 보고 UUID 미확인';
  if (!heartbeatFresh(app)) return '앱 상태 미확인';
  return app.heartbeat_state === 'error' ? '앱 오류' : '앱 보고 수신 · 주행 준비는 별도 확인';
}

/** "▶ 차량 제어 중" / "예비 — 준비됨" (화면 정의서 §3 준비 ≠ 제어권) */
export function controlLabel(role: Role, vm: Vm | undefined, drive: DriveState | null): string | null {
  const control = drive?.control;
  if (!role.driving || !control) return null;
  const vmId = vm?.vm_id.toLowerCase();
  if (!vmId || !control.active_vm_id || control.received_ago_ms >= 3000) return '활성 구동 VM 미확인';
  if (control.active_vm_id.toLowerCase() === vmId) return '▶ 활성 구동 · 주행 모드는 별도 표시';
  const app = drive.apps[role.app];
  return app?.vm_id?.toLowerCase() === vmId && heartbeatFresh(app)
    ? '앱 보고 수신 · 승계 준비는 별도 확인'
    : '앱 상태 미확인';
}

/** 조향·오프셋처럼 방향이 있는 값: +0.07 */
export const signed = (value: number | null | undefined, digits = 2, unit = '') =>
  value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}${unit}`;

export const plain = (value: number | null | undefined, digits = 2, unit = '') =>
  value == null ? '—' : `${value.toFixed(digits)}${unit}`;

/** 경과 시간. 1초 미만은 소수 한 자리, 60초 이상은 "N분 전", 60분 이상은 "N시간 N분 전" */
export const ago = (ms: number | null | undefined): string => {
  if (ms == null) return '—';
  if (ms < 1000) return `${(ms / 1000).toFixed(1)}초 전`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}초 전`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}분 전`;
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분 전`;
};

/** 템플릿 id → "Debian 13" · "Ubuntu 24.04" · "Zephyr" */
export function osLabel(vm: Pick<Vm, 'guest_kind' | 'template_id'>): string {
  if (vm.guest_kind === 'zephyr') return 'Zephyr';
  const [distro = vm.template_id, version] = vm.template_id.split('-');
  return `${distro.charAt(0).toUpperCase()}${distro.slice(1)}${version ? ` ${version}` : ''}`;
}

/**
 * 차량 안전 플래그 (MQTT 계약 §5.4 vehicle.status.flags, ESP32 가 재서 게이트웨이가 낸다).
 * 화면에는 짧은 코드 대신 무슨 뜻인지 풀어 쓴다.
 */
export const SAFETY = [
  {
    key: 'estop',
    label: '비상정지 버튼',
    on: '눌림',
    off: '안 눌림',
    hint: '차에 달린 하드웨어 비상정지 버튼. 눌리면 모터 전원이 끊기고 소프트웨어로는 풀 수 없다',
  },
  {
    key: 'watchdog',
    label: '명령 끊김 정지 (300 ms)',
    on: '작동',
    off: '아님',
    hint: 'ESP32 가 게이트웨이 명령을 300 ms 동안 못 받으면 스스로 차를 세운다 — VM·dom0 가 모두 죽어도 서는 마지막 안전장치',
  },
  {
    key: 'obstacle',
    label: '근거리 장애물',
    on: '감지',
    off: '없음',
    hint: 'ESP32 근거리 센서가 전방 장애물을 감지하면 전진을 막는다 (후진은 된다)',
  },
] as const;
