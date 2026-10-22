import { circuitOf, isCircuit, kmh, realM } from '@xenova/ui';
import type { DriveMode, Telemetry } from '../telemetry/types';

export const MODE_LABEL: Record<DriveMode, string> = {
  AUTO: '자율주행',
  MANUAL: '수동주행',
  AUTO_READY: '재개 가능',
  SAFE_STOP: '안전 정지',
};

export const mps = (v: number) => `${v.toFixed(2)} m/s`;
/** 시뮬레이터 구성(계약 v1.4 실제 서킷)이면 실제 단위 km/h, 아니면 m/s */
export function speedText(t: Telemetry, v: number): string {
  const track = t.sim?.world?.track;
  return isCircuit(track) ? `${Math.abs(kmh(circuitOf(track), v)).toFixed(0)} km/h` : mps(v);
}
/** 속도 타일용 [값, 단위] — 시뮬레이터 구성(실제 서킷)이면 km/h */
export function speedParts(t: Telemetry, v: number): [string, string] {
  const track = t.sim?.world?.track;
  return isCircuit(track) ? [Math.abs(kmh(circuitOf(track), v)).toFixed(0), 'km/h'] : [v.toFixed(2), 'm/s'];
}
/** 시뮬레이터 구성이면 실제 단위 m (모의 m × 트랙 비율) */
export function distText(t: Telemetry, m: number, digits = 1): string {
  const track = t.sim?.world?.track;
  return isCircuit(track) ? `${realM(circuitOf(track), m).toFixed(digits)} m` : `${m.toFixed(2)} m`;
}
export const rad = (r: number) => `${r >= 0 ? '+' : ''}${r.toFixed(2)} rad`;
export const seconds = (ms: number) => `${Math.max(0, Math.floor(ms / 1000))}초`;

/** 시뮬레이터 화면의 차 색 = 지금 차량을 제어하는 앱 색, 제어 앱이 없으면(SAFE_STOP) 정지 색 */
export function carColor(t: { activeApp: 'autonomous' | 'manual' | null }): string {
  return t.activeApp ? `var(--x-app-${t.activeApp})` : 'var(--x-fault)';
}

/**
 * 모니터에 켤 조작 키. 웹 입력(manualKeys)이 있으면 그것, 없으면 수동주행이 운전 중일 때 그 명령을 거꾸로 읽는다 —
 * 주행 상태(/ws/v1/drive/state)에는 눌린 키가 없고, 수동 앱은 키를 그대로 속도·조향으로 바꾸기 때문이다
 * (MQTT 계약 §7: up 전진, down 후진, left 조향 +, right 조향 −). brake 는 '입력 없음' 과 구분되지 않는다.
 */
export function shownKeys(t: {
  manualKeys: string[];
  activeApp: 'autonomous' | 'manual' | null;
  commands: { manual: { v_mps: number; steer_rad: number } | null };
}): string[] {
  if (t.manualKeys.length) return t.manualKeys;
  const c = t.commands.manual;
  if (t.activeApp !== 'manual' || !c) return [];
  const keys: string[] = [];
  if (c.v_mps > 0.01) keys.push('up');
  if (c.v_mps < -0.01) keys.push('down');
  if (c.steer_rad > 0.01) keys.push('left');
  if (c.steer_rad < -0.01) keys.push('right');
  return keys;
}
