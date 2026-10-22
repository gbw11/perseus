import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { accessToken, openSocket, type Connection } from './backend';
import {
  advanceOdometer,
  applyDriveState,
  applyEvent,
  emptyTelemetry,
  type DriveStateDto,
  type EventDto,
} from './live';
import { crash, initialTelemetry, resume, setManualKeys, step, toggleStress } from './scenario';
import { applySimSnapshot, type AppId, type Telemetry } from './types';

/**
 * 화면이 쓰는 데이터 출처. 모두 같은 모양(TelemetryApi)이라 화면은 출처를 모른다.
 *   console  — 관리 콘솔 mock 시나리오를 따라감 (기본값)
 *   live     — 백엔드 중계 (VITE_TELEMETRY=live). RC카 시나리오 §8 D2: 브라우저는 백엔드 WebSocket 으로 받는다
 *   scenario — 이 앱 혼자 도는 가짜 시나리오 (VITE_TELEMETRY=scenario, 시험·디자인용)
 * 모니터는 조작하지 않는다 — 장애 주입·재개·수동 입력은 관리 콘솔(시연 제어)이 맡는다.
 */
export interface TelemetryApi {
  telemetry: Telemetry;
  /** mock: 이 앱 혼자 도는 가짜 시나리오, console: 관리 콘솔 mock 을 따라감, live: 백엔드 */
  source: 'mock' | 'console' | 'live';
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
    const id = window.setInterval(
      () =>
        setTelemetry((t) => {
          const now = performance.now();
          const next = step(t, now);
          return { ...next, odometer: advanceOdometer(next, next.vehicle.v_mps, now) };
        }),
      TICK_MS,
    );
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
const DRIVE_EVENTS = [
  'drive.mode.changed',
  'drive.fault.detected',
  'drive.resume.accepted',
  'drive.resume.cancelled',
  'recovery.started',
  'recovery.completed',
];

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
    // 시뮬레이터 구성일 때만 오는 WS-06. 백엔드가 아직 열지 않았거나 실차 구성이면 실패해도 그만이다
    const closeSim = openSocket({
      path: '/ws/v1/sim',
      getToken: accessToken,
      onConnection: noop,
      onFrame: (frame) => {
        if (frame.type === 'sim.snapshot') {
          const snapshot = frame as Parameters<typeof applySimSnapshot>[1];
          setTelemetry((t) => applySimSnapshot(t, snapshot, performance.now()));
        }
      },
    });
    return () => {
      window.clearInterval(clock);
      closeState();
      closeEvents();
      closeSim();
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

/** 관리 콘솔 mock 시나리오 채널 이름 (admin-console src/mocks/scenario.ts MOCK_BUS) */
export const MOCK_BUS = 'xenova-mock-bus';

/**
 * 관리 콘솔 mock 을 따라간다 (기본값). 콘솔 시연 제어에서 강제 종료·재개를 하면 모니터가 그 결과를 그린다 —
 * 모니터는 조작하지 않는다 (화면 정의서 §1). 프레임 모양은 백엔드 WS 와 같아서 live 와 같은 변환을 쓴다.
 * 같은 origin 이어야 하므로 콘솔 개발 서버의 /monitor/ 로 연다 (README).
 */
export function ConsoleBusTelemetryProvider({ children }: { children: ReactNode }) {
  const [telemetry, setTelemetry] = useState(() => emptyTelemetry(performance.now()));
  const [connection, setConnection] = useState<Connection>(() =>
    typeof BroadcastChannel === 'undefined' ? 'offline' : 'connecting',
  );

  useEffect(() => {
    const clock = window.setInterval(() => setTelemetry((t) => ({ ...t, now: performance.now() })), CLOCK_MS);
    if (typeof BroadcastChannel === 'undefined') return () => window.clearInterval(clock);
    const bus = new BroadcastChannel(MOCK_BUS);
    let lastFrame = 0;
    const watchdog = window.setInterval(
      () => setConnection(performance.now() - lastFrame < 2000 ? 'live' : 'offline'),
      1000,
    );
    bus.onmessage = (message: MessageEvent<{ type?: string; state?: unknown; payload?: unknown }>) => {
      const frame = message.data;
      lastFrame = performance.now();
      if (frame?.type === 'drive.state') {
        setTelemetry((t) => applyDriveState(t, frame.state as DriveStateDto, performance.now()));
      } else if (frame?.type === 'event') {
        const event = frame.payload as EventDto;
        if (DRIVE_EVENTS.includes(event.type)) setTelemetry((t) => applyEvent(t, event, performance.now()));
      } else if (frame?.type === 'sim.snapshot') {
        setTelemetry((t) =>
          applySimSnapshot(t, frame as Parameters<typeof applySimSnapshot>[1], performance.now()),
        );
      }
    };
    bus.postMessage({ type: 'hello' }); // 콘솔이 먼저 열려 있었으면 지난 이벤트를 다시 받는다
    return () => {
      window.clearInterval(clock);
      window.clearInterval(watchdog);
      bus.close();
    };
  }, []);

  const api = useMemo(
    () => ({
      telemetry,
      source: 'console' as const,
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
