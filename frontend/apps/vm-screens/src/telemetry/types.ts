/**
 * 모니터 화면이 보여주는 상태. 필드 이름·값은 MQTT 계약을 따른다.
 *   docs/contracts/mqtt-contract-v1.md §4(app_id) §5.2(모드) §5.4(payload) §6(앱 이벤트)
 *   docs/scenario-rc-car.md §4.3(표시 상태) §5(화면)
 */
import type { Health } from '@xenova/ui';

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
  /** MQTT 계약의 app_id (네비게이션 VM 은 message-board) */
  topicAppId: 'autonomous' | 'manual' | 'message-board';
  guest: 'zephyr' | 'debian';
  role: string;
}

export const APPS: Record<AppId, AppMeta> = {
  autonomous: {
    id: 'autonomous',
    title: '자율주행',
    topicAppId: 'autonomous',
    guest: 'zephyr',
    role: '인지 결과로 주행 명령 50 Hz',
  },
  manual: {
    id: 'manual',
    title: '수동주행',
    topicAppId: 'manual',
    guest: 'zephyr',
    role: '대기 → 장애 시 즉시 인계',
  },
  interaction: {
    id: 'interaction',
    title: '네비게이션',
    topicAppId: 'message-board',
    guest: 'debian',
    role: '카메라 AI 장애물 감지·트랙 지도·알림',
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
  /** 추정 주행 거리 — 차량 속도(vehicle.status v_mps)를 화면에서 적분한 값. 위치 센서가 아니다 */
  odometer?: { distanceM: number; at: number };
}
