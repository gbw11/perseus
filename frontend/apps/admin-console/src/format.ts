import type { Health } from '@xenova/ui';
import type { VmAction } from './api/queries';
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

/**
 * VM 관리 API 계약 §5 (start·stop). start 는 stopped 에서만 의미가 있다 — crashed·paused·stopping 은
 * 409 INVALID_VM_STATE(정지 후 다시 시작), running 은 그대로 반환. stop 은 runtime 유무와 관계없이
 * dom0 stop 을 부르므로 이미 꺼진 VM 말고는 모두 누를 수 있어야 crashed·paused VM 을 정지 → 시작으로 되살린다.
 */
export const canStartVm = (state: VmState): boolean => state === 'stopped';
export const canStopVm = (state: VmState): boolean => state !== 'stopped';

/** VM 생명주기 요청 이름 (진행 표시·완료 알림이 같이 쓴다) */
export const VM_ACTION_LABEL: Record<VmAction, string> = {
  start: '시작',
  stop: '정지',
  delete: '삭제',
};
