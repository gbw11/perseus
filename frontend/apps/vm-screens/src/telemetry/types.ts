/**
 * 모니터 화면이 보여주는 상태. 필드 이름·값은 MQTT 계약을 따른다.
 *   docs/contracts/mqtt-contract-v1.md §4(app_id) §5.2(모드) §5.4(payload) §6(앱 이벤트)
 *   docs/scenario-rc-car.md §4.3(표시 상태) §5(화면)
 */
import type { Health, SimPose, SimWorld } from '@xenova/ui';

export type AppId = 'autonomous' | 'manual' | 'interaction';
export const APP_IDS: readonly AppId[] = ['autonomous', 'manual', 'interaction'];

/** MQTT 계약 §5.2 */
export type DriveMode = 'AUTO' | 'MANUAL' | 'AUTO_READY' | 'SAFE_STOP';
export type ActiveApp = 'autonomous' | 'manual' | null;

/** Xen 도메인 상태 (VM 관리 API 계약 §3.3 의 화면용 축약 + 재시작 중) */
export type DomainState = 'running' | 'crashed' | 'booting' | 'stopped';

export interface AppMeta {
  id: AppId;
  /** 화면 이름 */
  title: string;
  /** MQTT 계약의 app_id (인터랙션 VM 은 interaction) */
  topicAppId: 'autonomous' | 'manual' | 'interaction';
  guest: 'zephyr' | 'debian';
  role: string;
}

export const APPS: Record<AppId, AppMeta> = {
  autonomous: {
    id: 'autonomous',
    title: '자율 명령 앱',
    topicAppId: 'autonomous',
    guest: 'zephyr',
    role: '기존 autonomous 앱 · VM 주 구동 역할과 별개',
  },
  manual: {
    id: 'manual',
    title: '수동 입력 앱',
    topicAppId: 'manual',
    guest: 'zephyr',
    role: '기존 manual 앱 · VM 백업 역할과 별개',
  },
  interaction: {
    id: 'interaction',
    title: 'Debian 인터랙션 앱',
    topicAppId: 'interaction',
    guest: 'debian',
    role: '인터랙션·지도·영상 표시 · AI/인식은 Jetson 담당',
  },
};

export interface AppStatus {
  domain: DomainState;
  health: Health;
  /** health 가 바뀐 시각 (경과 시간 표시용, ms) */
  healthSince: number;
  /** 마지막 drive.command (주행 앱만) */
  lastCommandAt: number | null;
  /** 최근 1초 명령 수 */
  commandHz: number;
  cpuStress: boolean;
}

/** MQTT 계약 §5.4 perception.result */
export interface PerceptionResult {
  lane_offset_m: number;
  heading_err_rad: number;
  obstacles: { x: number; y: number; w: number }[];
  stop: boolean;
}

/** MQTT 계약 §5.4 vehicle.status (ESP32) */
export interface VehicleStatus {
  applied_generation: number;
  v_mps: number;
  steer_rad: number;
  flags: { estop: boolean; watchdog: boolean; obstacle: boolean };
  batt_v: number;
}

/** MQTT 계약 §5.4 drive.command */
export interface DriveCommand {
  generation: number;
  v_mps: number;
  steer_rad: number;
}

export interface TimelineEvent {
  id: number;
  at: number;
  /** MQTT 계약 §6 type (drive.mode.changed 등) 또는 화면용 종류 */
  type: string;
  level: 'info' | 'warning' | 'ok';
  text: string;
}

export interface Telemetry {
  now: number;
  mode: DriveMode;
  activeApp: ActiveApp;
  generation: number;
  apps: Record<AppId, AppStatus>;
  commands: Record<'autonomous' | 'manual', DriveCommand | null>;
  perception: PerceptionResult;
  /** 장애물 수. 실제 연결은 위치 없이 수만 준다 (없으면 perception.obstacles.length) */
  obstacleCount?: number;
  vehicle: VehicleStatus;
  manualKeys: string[];
  /** 자율 VM 명령이 연속으로 신선했던 시작 시각 (AUTO_READY 판정) */
  autonomousFreshSince: number | null;
  events: TimelineEvent[];
  /** live·콘솔 mock: 앱별 vm_id (복구 이벤트를 앱에 붙이려고) */
  appVmIds?: Partial<Record<AppId, string>>;
  /** live·콘솔 mock: 복구 서비스가 재시작한 앱 — 명령(생존 신호)이 다시 신선해질 때까지 '복구 중' */
  recoveringApps?: Partial<Record<AppId, true>>;
  /** 받아들여진 자율주행 재개가 AUTO 로 바뀔 예상 시각 (화면 시계, MQTT 계약 v1.2 §6). 없으면 null */
  resumeAt?: number | null;
  /** 카운트다운이 자동 복귀인가 (계약 v1.5 requested_by auto) */
  resumeAuto?: boolean;
  /** 추정 주행 거리 — 차량 속도(vehicle.status v_mps)를 화면에서 적분한 값. 위치 센서가 아니다 */
  odometer?: { distanceM: number; at: number };
  /** 시뮬레이터 구성일 때만 (MQTT 계약 v1.3 §11, WS-06 sim.snapshot). 실차 구성에는 없다 */
  sim?: SimTelemetry;
}

export interface SimTelemetry {
  available: boolean;
  pose: SimPose | null;
  world: SimWorld | null;
  /** 최근 위치 (오래된 것부터) */
  trail: [number, number][];
  /** 받은 시각 (화면 시계 ms) */
  receivedAt: number;
}

/** 위치가 이보다 오래되면 시뮬레이터가 없는 것으로 본다 (화면 배치 계약 v1.1 §5.6: 1초) */
export const SIM_STALE_MS = 1000;
const TRAIL_POINTS = 120;

/** 세 번째 칸을 시뮬레이터로 바꿀지 — 위치가 1초 안에 왔을 때만 */
export function simLive(t: Telemetry): boolean {
  return !!t.sim?.available && !!t.sim.pose && t.now - t.sim.receivedAt <= SIM_STALE_MS;
}

/** 시뮬레이터를 다시 켜거나 차를 옮기면 위치가 튄다 — 그때는 지나온 길을 새로 그린다 (선이 트랙을 가로지르지 않게) */
export const TRAIL_JUMP_M = 0.6;
function jumped(trail: [number, number][], pose: { x: number; y: number }): boolean {
  const last = trail[trail.length - 1];
  return !!last && Math.hypot(pose.x - last[0], pose.y - last[1]) > TRAIL_JUMP_M;
}

/** WS-06 sim.snapshot 프레임을 화면 상태로 */
export function applySimSnapshot(
  t: Telemetry,
  frame: { available?: boolean; pose?: SimPose | null; world?: SimWorld | null },
  now: number,
): Telemetry {
  const pose = frame.pose ?? null;
  const prev = t.sim?.trail ?? [];
  const trail = pose
    ? [...(jumped(prev, pose) ? [] : prev), [pose.x, pose.y] as [number, number]].slice(-TRAIL_POINTS)
    : prev;
  return {
    ...t,
    sim: {
      available: !!frame.available,
      pose,
      world: frame.world ?? t.sim?.world ?? null,
      trail,
      receivedAt: now,
    },
  };
}
