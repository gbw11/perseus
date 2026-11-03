import { useSyncExternalStore } from 'react';

export const VM_CREATION_LEAVE_MESSAGE =
  '다른 화면으로 이동해도 VM 생성은 계속됩니다. 완료 여부는 VM 목록에서 확인하세요.';

const LAST_DURATION_KEY = 'xenova.vm-create.last-duration-ms';
const PENDING_CREATIONS_KEY = 'xenova.vm-create.pending';
const PENDING_CREATION_TTL_MS = 16 * 60 * 1000;

export type PendingVmCreation = {
  name: string;
  startedAt: number;
};

let pendingCreations = loadPendingCreations();
const listeners = new Set<() => void>();

export function usePendingVmCreations(): readonly PendingVmCreation[] {
  return useSyncExternalStore(subscribe, getPendingCreations, getPendingCreations);
}

export function startVmCreation(name: string, startedAt = Date.now()): void {
  pendingCreations = [...pendingCreations.filter((creation) => creation.name !== name), { name, startedAt }];
  persistPendingCreations();
  emitChange();
}

export function finishVmCreation(name: string): void {
  const next = pendingCreations.filter((creation) => creation.name !== name);
  if (next.length === pendingCreations.length) return;
  pendingCreations = next;
  persistPendingCreations();
  emitChange();
}

export function isVmCreationPending(
  name: string,
  state: string,
  creations: readonly PendingVmCreation[],
): boolean {
  return state !== 'running' && creations.some((creation) => creation.name === name);
}

export function readLastVmCreationDuration(): number | null {
  const value = readStorage(LAST_DURATION_KEY);
  if (value === null) return null;
  const duration = Number(value);
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

export function saveLastVmCreationDuration(durationMs: number): void {
  if (!Number.isFinite(durationMs) || durationMs <= 0) return;
  writeStorage(LAST_DURATION_KEY, String(Math.round(durationMs)));
}

export function formatVmCreationTime(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function estimatedRemainingTime(elapsedMs: number, previousDurationMs: number): number {
  return Math.max(0, previousDurationMs - Math.max(0, elapsedMs));
}

export function blockVmCreationUnload(event: BeforeUnloadEvent): string {
  event.preventDefault();
  event.returnValue = VM_CREATION_LEAVE_MESSAGE;
  return VM_CREATION_LEAVE_MESSAGE;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getPendingCreations(): readonly PendingVmCreation[] {
  return pendingCreations;
}

function emitChange(): void {
  listeners.forEach((listener) => listener());
}

function loadPendingCreations(): PendingVmCreation[] {
  const value = readStorage(PENDING_CREATIONS_KEY);
  if (!value) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!Array.isArray(parsed)) return [];
    const cutoff = Date.now() - PENDING_CREATION_TTL_MS;
    return parsed.filter(isPendingVmCreation).filter((creation) => creation.startedAt >= cutoff);
  } catch {
    return [];
  }
}

function isPendingVmCreation(value: unknown): value is PendingVmCreation {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<PendingVmCreation>;
  return (
    typeof candidate.name === 'string' && candidate.name.length > 0 && Number.isFinite(candidate.startedAt)
  );
}

function persistPendingCreations(): void {
  writeStorage(PENDING_CREATIONS_KEY, JSON.stringify(pendingCreations));
}

function readStorage(key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem(key, value);
  } catch {
    // 저장소가 차단되어도 현재 탭의 생성 요청은 계속 추적한다.
  }
}
