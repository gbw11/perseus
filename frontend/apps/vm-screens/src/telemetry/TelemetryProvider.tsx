import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { accessToken, openSocket, type Connection } from './backend';
import { applyDriveState, applyEvent, emptyTelemetry, type DriveStateDto, type EventDto } from './live';
import { crash, initialTelemetry, resume, setManualKeys, step, toggleStress } from './scenario';
import type { AppId, Telemetry } from './types';

/**
 * 화면이 쓰는 데이터 출처. 둘 다 같은 모양(TelemetryApi)이라 화면은 출처를 모른다.
 *   mock — 가짜 시나리오 (기본값)
 *   live — 백엔드 중계 (VITE_TELEMETRY=live). RC카 시나리오 §8 D2: 브라우저는 백엔드 WebSocket 으로 받는다
 * 장애 주입·재개 같은 조작은 mock 에만 있다. 실제 조작은 관리 콘솔(시연 제어)이 맡는다.
 */
export interface TelemetryApi {
  telemetry: Telemetry;
  source: 'mock' | 'live';
  connection: Connection | 'mock';
  crash: (id: AppId) => void;
  toggleStress: (id: AppId) => void;
  resume: () => void;
  setManualKeys: (keys: string[]) => void;
}

const TelemetryContext = createContext<TelemetryApi | null>(null);
const TICK_MS = 20;

export function MockTelemetryProvider({ children }: { children: ReactNode }) {
  const [telemetry, setTelemetry] = useState(() => initialTelemetry(performance.now()));

  useEffect(() => {
    const id = window.setInterval(() => setTelemetry((t) => step(t, performance.now())), TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  const doCrash = useCallback((id: AppId) => setTelemetry((t) => crash(t, id)), []);
  const doStress = useCallback((id: AppId) => setTelemetry((t) => toggleStress(t, id)), []);
  const doResume = useCallback(() => setTelemetry((t) => resume(t)), []);
  const doKeys = useCallback((keys: string[]) => setTelemetry((t) => setManualKeys(t, keys)), []);

  const api = useMemo(
    () => ({
      telemetry,
      source: 'mock' as const,
      connection: 'mock' as const,
      crash: doCrash,
      toggleStress: doStress,
      resume: doResume,
      setManualKeys: doKeys,
    }),
    [telemetry, doCrash, doStress, doResume, doKeys],
  );
  return <TelemetryContext.Provider value={api}>{children}</TelemetryContext.Provider>;
}

const noop = () => {};
/** 화면의 경과 시간 표시(복구 중 n초 등)가 멈추지 않게 now 만 따로 흘린다 */
const CLOCK_MS = 200;
const DRIVE_EVENTS = ['drive.mode.changed', 'drive.fault.detected'];

export function LiveTelemetryProvider({ children }: { children: ReactNode }) {
  const [telemetry, setTelemetry] = useState(() => emptyTelemetry(performance.now()));
  const [connection, setConnection] = useState<Connection>('connecting');

  useEffect(() => {
    const clock = window.setInterval(() => setTelemetry((t) => ({ ...t, now: performance.now() })), CLOCK_MS);
    const closeState = openSocket({
      path: '/ws/v1/drive/state',
      getToken: accessToken,
      onConnection: setConnection,
      onFrame: (frame) => {
        if (frame.type === 'drive.state') {
          const state = frame.state as DriveStateDto;
          setTelemetry((t) => applyDriveState(t, state, performance.now()));
        }
      },
    });
    const closeEvents = openSocket({
      path: '/ws/v1/events',
      getToken: accessToken,
      onConnection: noop, // 연결 표시는 주행 상태 소켓 기준
      afterAuth: (send) => send({ type: 'subscribe', request_id: 'screens', event_types: DRIVE_EVENTS }),
      onFrame: (frame) => {
        if (frame.type === 'event') {
          const event = frame.payload as EventDto;
          setTelemetry((t) => applyEvent(t, event, performance.now()));
        }
      },
    });
    return () => {
      window.clearInterval(clock);
      closeState();
      closeEvents();
    };
  }, []);

  const api = useMemo(
    () => ({
      telemetry,
      source: 'live' as const,
      connection,
      crash: noop,
      toggleStress: noop,
      resume: noop,
      setManualKeys: noop,
    }),
    [telemetry, connection],
  );
  return <TelemetryContext.Provider value={api}>{children}</TelemetryContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTelemetry(): TelemetryApi {
  const api = useContext(TelemetryContext);
  if (!api) throw new Error('useTelemetry 는 TelemetryProvider 안에서만 쓴다');
  return api;
}
