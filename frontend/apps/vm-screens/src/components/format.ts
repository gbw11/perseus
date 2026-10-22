import type { DriveMode } from '../telemetry/types';

export const MODE_LABEL: Record<DriveMode, string> = {
  AUTO: '자율주행',
  MANUAL: '수동주행',
  AUTO_READY: '재개 가능',
  SAFE_STOP: '안전 정지',
};

export const mps = (v: number) => `${v.toFixed(2)} m/s`;
export const rad = (r: number) => `${r >= 0 ? '+' : ''}${r.toFixed(2)} rad`;
export const seconds = (ms: number) => `${Math.max(0, Math.floor(ms / 1000))}초`;

/** 시뮬레이터 화면의 차 색 = 지금 차량을 제어하는 앱 색, 제어 앱이 없으면(SAFE_STOP) 정지 색 */
export function carColor(t: { activeApp: 'autonomous' | 'manual' | null }): string {
  return t.activeApp ? `var(--x-app-${t.activeApp})` : 'var(--x-fault)';
}
