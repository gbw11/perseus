/**
 * 주행 통합 상태 구독과 수동주행 입력.
 *   WS /ws/v1/drive/state → {"type":"drive.state","state":DriveState} (노션 API 명세 "주행 통합 상태 (WS)")
 *   WS /ws/v1/drive       → {"type":"input","keys":[...]} / {"type":"input.ack","result",...}
 *                           (MQTT 계약 §7, 노션 API 명세 "수동주행 입력 (WS)")
 * 첫 프레임은 {"type":"auth","access_token"}. 4401 로 닫히면 토큰을 재발급해 다시 붙는다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { getSession, refreshSession } from './client';

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

/** 백엔드 DriveState (MQTT 계약 §5.4 필드, "*_ago_ms" 는 백엔드 시계 기준). 블록이 null 이면 아직 못 받음 */
export interface DriveState {
  version: number;
  observed_at: string;
  control: {
    mode: DriveMode | null;
    generation: number | null;
    active_app: 'autonomous' | 'manual' | null;
    active_vm_id: string | null;
    reason: string | null;
    received_ago_ms: number;
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
  perception: {
    valid: boolean;
    received_ago_ms: number;
    hz: number;
    lane_offset_m: number | null;
    heading_err_rad: number | null;
    obstacle_count: number;
    stop: boolean;
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

/** 인증 소켓 하나를 열고, 끊기면 다시 붙는다. 4401 이면 refresh 후 재시도 */
function useAuthedSocket(path: string, onFrame: (frame: Frame) => void) {
  const [state, setState] = useState<SocketState>('connecting');
  const socketRef = useRef<WebSocket | null>(null);
  const frameRef = useRef(onFrame);
  useEffect(() => {
    frameRef.current = onFrame;
  });

  useEffect(() => {
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
        const frame = JSON.parse(String(message.data)) as Frame;
        if (frame.type === 'authenticated') setState('open');
        else frameRef.current(frame);
      };
      ws.onclose = async (event) => {
        if (socketRef.current === ws) socketRef.current = null;
        if (stopped) return;
        setState('closed');
        if (event.code === UNAUTHORIZED && !(await refreshSession())) return; // 세션 끝 → 로그인으로
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
export function useDriveState() {
  const [drive, setDrive] = useState<DriveState | null>(null);
  const { state } = useAuthedSocket('/ws/v1/drive/state', (frame) => {
    if (frame.type === 'drive.state') setDrive(frame.state as DriveState);
  });
  return { drive, socket: state };
}

/**
 * 수동주행 입력. 눌린 키 집합이 바뀔 때만 보낸다 — 누르고 있는 동안의 20 Hz 반복은 백엔드가 한다 (계약 §7).
 * 화면을 떠나면 빈 keys 를 보내 놓는다 (백엔드도 소켓이 닫히면 놓는다).
 */
export function useManualInput() {
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

const ORDER: DriveKey[] = ['up', 'down', 'left', 'right', 'brake'];

/** 눌린 키 집합을 바꾼다. 바뀌지 않으면 같은 배열을 돌려준다 (보내지 않으려고) */
export function pressKey(held: DriveKey[], key: DriveKey, down: boolean): DriveKey[] {
  if (held.includes(key) === down) return held;
  const next = down ? [...held, key] : held.filter((k) => k !== key);
  return ORDER.filter((k) => next.includes(k));
}
