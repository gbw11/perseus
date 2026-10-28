/**
 * 관리 콘솔 ↔ Spring 공개 API 타입. 팀이 정한 문서를 그대로 옮긴다 — 바뀌면 문서와 같이 고친다.
 *
 *   VM·템플릿·오류: docs/contracts/vm-api-contract-v1.md §2.2 §2.3 §3.3 §5 §8.2 §9
 *   인증:          노션 API 명세 "로그인"(POST /api/v1/auth/login), "토큰 재발급"(POST /api/v1/auth/refresh)
 */

// ---- 인증 (노션 API 명세) ----------------------------------------------------------

export interface LoginRequest {
  username: string;
  password: string;
}

export interface SignupRequest {
  username: string;
  nickname: string;
  password: string;
}

export interface SignupResponse {
  user_id: string;
  username: string;
  nickname: string;
  created_at: string;
}

/** 로그인·재발급 응답. refresh 토큰은 HttpOnly 쿠키(xenova_refresh)라 본문에 없다 */
export interface TokenResponse {
  access_token: string;
  token_type: 'Bearer';
  /** 초 (기본 900) */
  expires_in: number;
  /** ISO-8601 UTC */
  expires_at: string;
  user: { user_id: string; username: string; nickname: string };
}

// ---- VM (VM 관리 API 계약 §2.2, §3.3) ------------------------------------------------

export type GuestKind = 'linux' | 'zephyr';

/** §3.3 Spring 공개 state. "꺼짐"은 stopped (Spring 만 만드는 값) */
export type VmState = 'running' | 'stopping' | 'paused' | 'crashed' | 'stopped';

/** heartbeat 기준. zephyr 는 항상 UNKNOWN */
export type AgentStatus = 'UNKNOWN' | 'ONLINE' | 'STALE' | 'OFFLINE';

/** §2.2 프론트 공개 VM. agent_status·ipv4 는 목록·단건 조회에만 있다 */
export interface Vm {
  vm_id: string;
  name: string;
  guest_kind: GuestKind;
  state: VmState;
  agent_status?: AgentStatus;
  ipv4?: string | null;
  template_id: string;
  template_version: string;
  vcpus: number;
  memory_mib: number;
}

/** §5 GET /api/v1/vms */
export interface VmListResponse {
  items: Vm[];
  observed_at: string;
}

/** §5 POST /api/v1/vms 요청 (§9 입력값 규칙) */
export interface CreateVmRequest {
  /** 1~63자, 앞뒤 공백·제어문자 금지 */
  name: string;
  template_id: string;
  template_version: string;
  /** 1~4 */
  vcpus: number;
  /** 템플릿 min_memory_mib ~ 1024 */
  memory_mib: number;
}

/** §2.3 */
export interface Template {
  template_id: string;
  version: string;
  display_name: string;
  min_memory_mib: number;
  guest_kind: GuestKind;
}

// ---- 오류 (§8.2, 노션 공통 에러) ----------------------------------------------------

export interface ApiErrorBody {
  code: string;
  message: string;
  /** 입력 검증 실패(422)일 때만 */
  errors?: { field: string; reason: string }[];
}

/** 프론트가 분기에 쓰는 코드 (§8.2 + 인증) */
export type KnownErrorCode =
  | 'UNAUTHORIZED'
  | 'INVALID_TOKEN'
  | 'TOKEN_REVOKED'
  | 'INVALID_CREDENTIALS'
  | 'INVALID_REFRESH_TOKEN'
  | 'LOGIN_RATE_LIMITED'
  | 'VALIDATION_ERROR'
  | 'TEMPLATE_NOT_FOUND'
  | 'VM_NOT_FOUND'
  | 'VM_NAME_CONFLICT'
  | 'INVALID_VM_STATE'
  | 'VM_ALREADY_EXISTS'
  | 'INVALID_ARGUMENT'
  | 'INSUFFICIENT_RESOURCES'
  | 'VM_DRIVER_ERROR'
  | 'VM_CONTROL_NOT_SUPPORTED'
  | 'VM_CONTROL_UNAVAILABLE'
  | 'VM_CONTROL_TIMEOUT'
  | 'VM_CONTROL_BAD_RESPONSE'
  | 'INTERNAL_SERVER_ERROR';

// ---- 이벤트 (노션 API 명세 REST-18 "이벤트 조회", 백엔드 EventResponse) -------------------

export type EventType =
  | 'vm.created'
  | 'vm.deleted'
  | 'vm.state.changed'
  | 'agent.status.changed'
  | 'recovery.started'
  | 'recovery.completed'
  | 'recovery.failed'
  | 'demo.command.sent'
  | 'demo.command.completed'
  | 'demo.command.failed'
  | 'drive.mode.changed'
  | 'drive.fault.detected'
  | 'drive.resume.accepted'
  | 'drive.resume.cancelled';

export type EventSeverity = 'INFO' | 'WARNING' | 'ERROR';

export interface VmEvent {
  event_id: string;
  type: EventType;
  severity: EventSeverity;
  vm_id: string | null;
  operation_id: string | null;
  /** ISO-8601 UTC */
  occurred_at: string;
  recorded_at: string;
  /** drive.mode.changed: {from, to, generation, reason} · drive.fault.detected: {app_id, vm_id, reason} (MQTT 계약 §6) */
  payload: Record<string, unknown>;
}

export interface EventListResponse {
  items: VmEvent[];
}

// ---- 메트릭·프로세스 (노션 API 명세 "메트릭 조회", 백엔드 MetricsController) — 에이전트가 있는 Linux VM 만 ----

export interface MetricValue {
  name: string;
  value: number | null;
  unit: string | null;
  labels: Record<string, string> | null;
  unavailable_reason: string | null;
}

export interface MetricSample {
  sample_id: string;
  vm_id: string;
  boot_id?: string | null;
  source: string;
  observed_at: string;
  received_at?: string;
  window_ms?: number | null;
  values: MetricValue[];
}

export interface MetricHistoryResponse {
  items: MetricSample[];
}

export interface ProcessInfo {
  pid: number;
  name: string;
  cpu_percent: number | null;
  rss_bytes: number | null;
}

export interface ProcessesResponse {
  vm_id: string;
  observed_at: string | null;
  stale: boolean;
  items: ProcessInfo[];
}

// ---- 화면 배치 (노션 API 명세 REST-23·REST-24, 백엔드 -129) ----

export type DisplayView = 'split' | 'os-face';
export type DisplayStatus = 'DISCONNECTED' | 'IDLE' | 'SWITCHING' | 'ACTIVE' | 'ERROR';

export interface DisplayRoute {
  /** split 이면 null */
  vm_id: string | null;
  view: DisplayView;
  request_id: string;
  /** observed_route 에만 있다 */
  applied_at: string | null;
  verification_level: string | null;
}

export interface Display {
  display_id: string;
  label: string;
  /** 모니터 브라우저가 WS /ws/v1/displays 로 붙어 있는지 */
  connected: boolean;
  status: DisplayStatus;
  supported_views: DisplayView[];
  revision: number;
  desired_route: DisplayRoute;
  observed_route: DisplayRoute | null;
  last_error: string | null;
}

export interface DisplayListResponse {
  items: Display[];
  observed_at: string;
  stale: boolean;
}

export interface RouteRequest {
  vm_id: string | null;
  view: DisplayView;
  expected_revision: number;
}
