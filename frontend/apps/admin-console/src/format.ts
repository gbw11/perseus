import type { Health } from '@xenova/ui';
import type { AgentStatus, GuestKind, VmState } from './api/types';

/** VM 관리 API 계약 §3.3 state → 화면 상태 배지 */
export const STATE_HEALTH: Record<VmState, Health> = {
  running: 'ok',
  stopping: 'recovering',
  paused: 'stopped',
  crashed: 'fault',
  stopped: 'stopped',
};

export const STATE_LABEL: Record<VmState, string> = {
  running: '실행 중',
  stopping: '종료 중',
  paused: '일시정지',
  crashed: '비정상 종료',
  stopped: '꺼짐',
};

export const AGENT_LABEL: Record<AgentStatus, string> = {
  UNKNOWN: '알 수 없음',
  ONLINE: '연결됨',
  STALE: '응답 늦음',
  OFFLINE: '끊김',
};

export const GUEST_LABEL: Record<GuestKind, string> = { linux: 'Linux', zephyr: 'Zephyr' };

export const capitalizeFirst = (value: string): string =>
  value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : value;

/** 경과 시간 mm:ss (음수는 00:00). VM 생성·정지·삭제 진행 표시가 같이 쓴다 */
export function formatElapsedTime(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}
