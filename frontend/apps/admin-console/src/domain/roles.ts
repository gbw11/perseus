/**
 * RC카 시나리오의 VM 역할 (MQTT 계약 §4 app_id). 화면은 VM 을 "VM 1·2·3" 이 아니라 역할 이름으로 부른다
 * (화면 정의서 §3 — Zephyr 둘은 OS 가 같아 이름·역할·색으로만 구분된다).
 */
import type { Health } from '@xenova/ui';
import type { DriveApp, DriveMode, DriveState } from '../api/drive';
import type { Vm } from '../api/types';
import { STATE_HEALTH } from '../format';

export type AppId = 'autonomous' | 'manual' | 'message-board';

export interface Role {
  app: AppId;
  title: string;
  short: string;
  color: string;
  /** 주행 명령을 내는 앱인가 (명령 신선도로 상태를 본다) */
  driving: boolean;
}

export const ROLES: Record<AppId, Role> = {
  autonomous: {
    app: 'autonomous',
    title: '자율주행 VM',
    short: '자율주행',
    color: 'var(--x-app-autonomous)',
    driving: true,
  },
  manual: {
    app: 'manual',
    title: '수동주행 VM',
    short: '수동주행',
    color: 'var(--x-app-manual)',
    driving: true,
  },
  'message-board': {
    app: 'message-board',
    title: '네비게이션 VM',
    short: '네비게이션',
    color: 'var(--x-app-interaction)',
    driving: false,
  },
};

export const ROLE_ORDER: AppId[] = ['autonomous', 'manual', 'message-board'];

/** 주행 상태가 알려 주는 vm_id 로 먼저 맞추고, 없으면 VM 이름으로 짐작한다 */
export function roleOf(vm: Vm, drive: DriveState | null): Role | null {
  for (const app of ROLE_ORDER) {
    if (drive?.apps[app]?.vm_id === vm.vm_id) return ROLES[app];
  }
  const name = vm.name.toLowerCase();
  if (name.includes('auto')) return ROLES.autonomous;
  if (name.includes('manual')) return ROLES.manual;
  if (name.includes('message') || name.includes('interaction')) return ROLES['message-board'];
  return null;
}

export function vmOf(app: AppId, vms: Vm[] | undefined, drive: DriveState | null): Vm | undefined {
  return vms?.find((vm) => roleOf(vm, drive)?.app === app);
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
 * 앱 칸 상태 배지 (화면 정의서 §3 배지 4종).
 * VM 이 crashed 면 장애, 실행 중인데 명령(주행 앱)·생존 신호(네비게이션)가 없으면 복구 중, 운영자가 끈 VM 은 정지.
 */
export function appHealth(role: Role, vm: Vm | undefined, app: DriveApp | undefined): Health {
  if (vm && vm.state !== 'running') return STATE_HEALTH[vm.state];
  if (!app) return vm ? 'recovering' : 'stopped';
  if (role.driving) return app.command_fresh ? 'ok' : 'recovering';
  return app.heartbeat_ago_ms != null && app.heartbeat_ago_ms <= 3000 ? 'ok' : 'recovering';
}

/** "▶ 차량 제어 중" / "예비 — 준비됨" (화면 정의서 §3 준비 ≠ 제어권) */
export function controlLabel(role: Role, drive: DriveState | null): string | null {
  const control = drive?.control;
  if (!role.driving || !control) return null;
  if (control.active_app === role.app)
    return role.app === 'manual' ? '▶ 차량 제어 중 · 운영자 운전' : '▶ 차량 제어 중';
  const app = drive.apps[role.app];
  if (role.app === 'manual') return app?.command_fresh ? '대기 중' : '대기 중 — 명령 없음';
  return app?.command_fresh ? '대기 — 명령 신선' : '명령 끊김';
}

/** 조향·오프셋처럼 방향이 있는 값: +0.07 */
export const signed = (value: number | null | undefined, digits = 2, unit = '') =>
  value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}${unit}`;

export const plain = (value: number | null | undefined, digits = 2, unit = '') =>
  value == null ? '—' : `${value.toFixed(digits)}${unit}`;

export const ago = (ms: number | null | undefined) =>
  ms == null ? '—' : ms < 1000 ? `${(ms / 1000).toFixed(1)}초 전` : `${Math.round(ms / 1000)}초 전`;

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
