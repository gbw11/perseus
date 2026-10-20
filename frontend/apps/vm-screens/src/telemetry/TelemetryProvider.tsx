import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { crash, initialTelemetry, resume, setManualKeys, step, toggleStress } from './scenario';
import type { AppId, Telemetry } from './types';

/**
 * 화면이 쓰는 데이터 출처. 지금은 가짜 시나리오(mock)만 있다.
 * 실제 경로(브로커 WebSocket 직결 / 백엔드 중계)는 RC카 시나리오 §8 D2 결정 뒤 같은 모양으로 붙인다.
 */
export interface TelemetryApi {
  telemetry: Telemetry;
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
    () => ({ telemetry, crash: doCrash, toggleStress: doStress, resume: doResume, setManualKeys: doKeys }),
    [telemetry, doCrash, doStress, doResume, doKeys],
  );
  return <TelemetryContext.Provider value={api}>{children}</TelemetryContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTelemetry(): TelemetryApi {
  const api = useContext(TelemetryContext);
  if (!api) throw new Error('useTelemetry 는 <MockTelemetryProvider> 안에서만 쓴다');
  return api;
}
