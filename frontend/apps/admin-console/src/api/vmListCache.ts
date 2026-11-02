/**
 * 마지막 VM 목록을 탭의 sessionStorage 에 둔다 (S15P31E110-345).
 * 새로고침하면 메모리 캐시가 비어 응답이 올 때까지 빈 화면이었으므로, 이 값을 먼저 보여 주고 뒤에서 다시 읽는다.
 * 저장소를 못 쓰는 환경(사생활 보호 창 등)에서는 조용히 예전 동작으로 돌아간다.
 */
import type { VmListResponse } from './types';

const KEY = 'xenova.vm-list.v1';
/** 이보다 오래된 목록은 보여 주지 않는다 */
export const VM_LIST_CACHE_MAX_AGE_MS = 10 * 60 * 1000;

interface Saved {
  savedAt: number;
  data: VmListResponse;
}

export function saveVmList(data: VmListResponse, now = Date.now()): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ savedAt: now, data } satisfies Saved));
  } catch {
    // 저장 실패는 화면 동작에 영향이 없다
  }
}

export function loadVmList(now = Date.now()): Saved | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as Partial<Saved>;
    if (
      typeof saved.savedAt !== 'number' ||
      !saved.data ||
      !Array.isArray(saved.data.items) ||
      now - saved.savedAt > VM_LIST_CACHE_MAX_AGE_MS ||
      saved.savedAt > now
    ) {
      sessionStorage.removeItem(KEY);
      return null;
    }
    return { savedAt: saved.savedAt, data: saved.data };
  } catch {
    return null;
  }
}

export function clearVmList(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // 지울 수 없으면 만료 시간이 대신 처리한다
  }
}
