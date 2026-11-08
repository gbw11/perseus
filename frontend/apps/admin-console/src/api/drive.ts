/**
 * 주행 통합 상태 구독과 수동주행 입력.
 *   WS /ws/v1/drive/state → {"type":"drive.state","state":DriveState} (노션 API 명세 "주행 통합 상태 (WS)")
 *   WS /ws/v1/drive       → {"type":"input","keys":[...]} / {"type":"input.ack","result",...}
 *                           (MQTT 계약 §7, 노션 API 명세 "수동주행 입력 (WS)")
 * 첫 프레임은 {"type":"auth","access_token"}. 4401 로 닫히면 토큰을 재발급해 다시 붙는다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { getSession, refreshSession, request } from './client';

/** VITE_MOCK 이면 WS 대신 표본 시나리오(src/mocks/scenario.ts)를 쓴다 */
const MOCK = import.meta.env.VITE_MOCK === 'true';
type Scenario = typeof import('../mocks/scenario');
let scenario: Promise<Scenario> | null = null;
const loadScenario = () => (scenario ??= import('../mocks/scenario'));

/** MQTT 계약 §5.2 */
export type DriveMode = 'AUTO' | 'MANUAL' | 'AUTO_READY' | 'SAFE_STOP';

export interface DriveApp {
  vm_id: string | null;
  command_fresh: boolean | null;
  command_ago_ms: number | null;
  command_hz: number;
  last_command: { generation: number | null; v_mps: number | null; steer_rad: number | null } | null;
  heartbeat_state: 'active' | 'standby' | 'idle' | 'error' | null;
  heartbeat_ago_ms: number | null;
}

export interface DriveSource {
  status: 'fresh' | 'stale' | 'disconnected' | 'unknown';
  age_ms: number | null;
}

export interface VmAppReport {
  lifecycle: {
    phase: 'WAITING' | 'ACTIVATING' | 'EXECUTING' | 'STOPPING' | 'RESIDUAL';
    readiness: 'QUALIFIED' | 'READY' | 'NOT_READY';
    reason: string | null;
  } | null;
  lifecycle_source: DriveSource;
  heartbeat: { state: NonNullable<DriveApp['heartbeat_state']> } | null;
  heartbeat_source: DriveSource;
}

/** 백엔드 DriveState (MQTT 계약 §5.4 필드, "*_ago_ms" 는 백엔드 시계 기준). 블록이 null 이면 아직 못 받음 */
export interface DriveState {
  version: number;
  observed_at: string;
  source?: DriveSource;
  control: {
    mode: DriveMode | null;
    generation: number | null;
    active_app: 'autonomous' | 'manual' | null;
    active_vm_id: string | null;
    reason: string | null;
    received_ago_ms: number;
    source?: DriveSource;
  } | null;
  vehicle: {
    applied_generation: number | null;
    v_mps: number | null;
    steer_rad: number | null;
    batt_v: number | null;
    flags: { estop: boolean; watchdog: boolean; obstacle: boolean } | null;
    received_ago_ms: number;
  } | null;
  apps: Record<string, DriveApp>;
  vm_apps?: Record<string, Record<string, VmAppReport>>;
  perception: {
    valid: boolean;
    received_ago_ms: number;
    hz: number;
    lane_offset_m: number | null;
    heading_err_rad: number | null;
    obstacle_count: number;
    stop: boolean;
  } | null;
  /** 받아들여진 재개 카운트다운 (MQTT 계약 v1.2 §6 drive.resume.accepted). 없으면 null */
  resume?: {
    generation: number | null;
    switch_in_ms: number;
    accepted_ago_ms: number;
    remaining_ms: number;
    /** 재개를 요청한 운영자 (drive.resume.accepted requested_by) */
    requested_by?: string | null;
  } | null;
  /**
   * 마지막 drive.resume.cancelled — 10초 동안만 (계약 v1.7). 운영자 취소(operator_cancelled)는 모드를 바꾸지 않고,
   * 그 밖의 reason 은 게이트웨이가 조건이 깨져 카운트다운을 끝낸 것이다.
   */
  resume_cancel?: {
    reason: string | null;
    requested_by: string | null;
    generation: number | null;
    received_ago_ms: number;
  } | null;
}

/** MQTT 계약 §7 */
export type DriveKey = 'up' | 'down' | 'left' | 'right' | 'brake';
export type InputResult = 'SENT' | 'RELEASED' | 'NOT_MANUAL' | 'NOT_CONNECTED';
export type SocketState = 'connecting' | 'open' | 'closed';

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const UNAUTHORIZED = 4401;
const RETRY_MS = 2000;

function wsUrl(path: string): string {
  return (BASE || window.location.origin).replace(/^http/, 'ws') + path;
}

type Frame = { type: string } & Record<string, unknown>;

/** 인증 소켓 하나를 열고, 끊기면 다시 붙는다. 4401 이면 refresh 후 재시도. path 가 비면 열지 않는다 */
export function useAuthedSocket(path: string, onFrame: (frame: Frame) => void) {
  const [state, setState] = useState<SocketState>('connecting');
  const socketRef = useRef<WebSocket | null>(null);
  const frameRef = useRef(onFrame);
  useEffect(() => {
    frameRef.current = onFrame;
  });

  useEffect(() => {
    if (!path) return; // 빈 주소 = 연결하지 않는다 (mock)
    let stopped = false;
    let timer: number | undefined;

    const connect = () => {
      const token = getSession()?.access_token;
      if (!token) {
        setState('closed');
        return;
      }
      setState('connecting');
      const ws = new WebSocket(wsUrl(path));
      socketRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', access_token: token }));
      ws.onmessage = (message) => {
        let frame: Frame;
        try {
          frame = JSON.parse(String(message.data)) as Frame;
        } catch {
          return; // 깨진 프레임 하나로 onmessage 가 예외를 던지지 않게 버린다
        }
        if (frame.type === 'authenticated') setState('open');
        else frameRef.current(frame);
      };
      ws.onclose = async (event) => {
        if (socketRef.current === ws) socketRef.current = null;
        if (stopped) return;
        setState('closed');
        if (event.code === UNAUTHORIZED && !(await refreshSession())) return; // 세션 끝 → 로그인으로
        // refresh 를 기다리는 사이 화면을 떠났으면 정리(clearTimeout)가 이미 끝났으므로 다시 걸지 않는다
        if (stopped) return;
        timer = window.setTimeout(connect, RETRY_MS);
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socketRef.current?.close();
    };
  }, [path]);

  const send = useCallback((frame: unknown) => {
    const ws = socketRef.current;
    if (ws?.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(frame));
    return true;
  }, []);

  return { state, send };
}

/** 주행 통합 상태 (최대 10 Hz, 변화 없으면 1초마다) */
export const useDriveState = MOCK ? useMockDriveState : useLiveDriveState;

function useLiveDriveState() {
  const [drive, setDrive] = useState<DriveState | null>(null);
  const { state } = useAuthedSocket('/ws/v1/drive/state', (frame) => {
    if (frame.type === 'drive.state') setDrive(frame.state as DriveState);
  });
  return { drive, socket: state };
}

function useMockDriveState() {
  const [drive, setDrive] = useState<DriveState | null>(null);
  useEffect(() => {
    let off: (() => void) | undefined;
    let stopped = false;
    void loadScenario().then((s) => {
      if (!stopped) off = s.subscribe(setDrive);
    });
    return () => {
      stopped = true;
      off?.();
    };
  }, []);
  return { drive, socket: (drive ? 'open' : 'connecting') as SocketState };
}

/**
 * 자율주행 재개 요청 (POST /api/v1/drive/resume → MQTT-13). 202 는 "보냈다" 일 뿐 — 게이트웨이가 받아들이면
 * DriveState.resume 에 3초 카운트다운이 생기고, 거절·취소는 drive.resume.cancelled 로 온다 (계약 v1.2 §6).
 */
export async function requestResume(): Promise<void> {
  if (MOCK) {
    const s = await loadScenario();
    if (!s.resume()) throw new Error('지금은 재개할 수 없습니다 (AUTO_READY 아님 또는 카운트다운 중)');
    return;
  }
  await request('/api/v1/drive/resume', { method: 'POST' });
}

/**
 * 재개 카운트다운 취소 (DELETE /api/v1/drive/resume → console/resume drive.resume.cancel_requested, 계약 v1.7).
 * 모드는 그대로(수동주행 유지)이고, 다시 [자율주행 재개]를 눌러야 AUTO 로 간다. 카운트다운이 없으면 409.
 */
export async function cancelResume(): Promise<void> {
  if (MOCK) {
    const s = await loadScenario();
    if (!s.cancelResumeCountdown()) throw new Error('취소할 카운트다운이 없습니다');
    return;
  }
  await request('/api/v1/drive/resume', { method: 'DELETE' });
}

/** 시연 동작 (mock 에서만 동작 — 실제 API 는 격리 시연 -135 가 정한다) */
export const mockDemo = MOCK
  ? {
      forceKill: (target: 'autonomous' | 'manual' | 'interaction') =>
        void loadScenario().then((s) => s.forceKill(target)),
      togglePerception: () => void loadScenario().then((s) => s.togglePerception()),
    }
  : null;

/**
 * 수동주행 입력. 눌린 키 집합이 바뀔 때만 보낸다 — 누르고 있는 동안의 20 Hz 반복은 백엔드가 한다 (계약 §7).
 * 화면을 떠나면 빈 keys 를 보내 놓는다 (백엔드도 소켓이 닫히면 놓는다).
 */
export const useManualInput = MOCK ? useMockManualInput : useLiveManualInput;

function useMockManualInput() {
  const [result, setResult] = useState<InputResult | null>(null);
  const sendKeys = useCallback((keys: DriveKey[]) => {
    void loadScenario().then((s) => setResult(s.setKeys(keys)));
    return true;
  }, []);
  return { socket: 'open' as SocketState, result, sendKeys };
}

function useLiveManualInput() {
  const [result, setResult] = useState<InputResult | null>(null);
  const { state, send } = useAuthedSocket('/ws/v1/drive', (frame) => {
    if (frame.type === 'input.ack') setResult(frame.result as InputResult);
  });
  const sendKeys = useCallback((keys: DriveKey[]) => send({ type: 'input', keys }), [send]);
  useEffect(() => () => void sendKeys([]), [sendKeys]);
  return { socket: state, result, sendKeys };
}

/** 키보드 → 계약 §7 키. 화살표·WASD, 스페이스는 brake */
export const KEY_OF: Record<string, DriveKey> = {
  ArrowUp: 'up',
  w: 'up',
  ArrowDown: 'down',
  s: 'down',
  ArrowLeft: 'left',
  a: 'left',
  ArrowRight: 'right',
  d: 'right',
  ' ': 'brake',
};

/** KeyboardEvent.key → 계약 키. Shift·CapsLock 으로 대문자가 와도 같은 키로 본다 (W 를 떼면 up 이 풀려야 한다) */
export function driveKeyOf(key: string): DriveKey | undefined {
  return KEY_OF[key.length === 1 ? key.toLowerCase() : key];
}

const ORDER: DriveKey[] = ['up', 'down', 'left', 'right', 'brake'];

/** 눌린 키 집합을 바꾼다. 바뀌지 않으면 같은 배열을 돌려준다 (보내지 않으려고) */
export function pressKey(held: DriveKey[], key: DriveKey, down: boolean): DriveKey[] {
  if (held.includes(key) === down) return held;
  const next = down ? [...held, key] : held.filter((k) => k !== key);
  return ORDER.filter((k) => next.includes(k));
}
