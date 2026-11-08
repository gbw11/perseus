import { useSyncExternalStore } from 'react';

/**
 * 1초마다 바뀌는 현재 시각. "n초 전" 처럼 경과 시간을 그리는 곳에서 쓴다.
 * 조회 결과가 그대로면 화면이 다시 그려지지 않아 경과 시간이 멈춰 보이므로, 데이터와 따로 1초마다 다시 그린다.
 * 화면 전체가 타이머 하나를 함께 쓴다 (쓰는 곳이 없으면 멈춘다).
 */
const TICK_MS = 1000;

let now = Date.now();
let timer: ReturnType<typeof setInterval> | undefined;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === undefined) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((notify) => notify());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== undefined) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

export function useNow(): number {
  return useSyncExternalStore(
    subscribe,
    () => now,
    () => now,
  );
}
