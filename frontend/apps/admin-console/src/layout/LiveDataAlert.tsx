import { useQueryClient, type Query, type QueryKey } from '@tanstack/react-query';
import { useToast } from '@xenova/ui';
import { useCallback, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { ApiError } from '../api/client';
import { lastFetchFailed } from '../api/queries';
import styles from './layout.module.css';

/**
 * 지금 화면이 쓰는 조회 중 마지막 조회가 실패한 것을 알린다. 콘솔 어느 화면에서나 같은 알림이다.
 * - 실패한 동안은 다시 읽는 중에도 계속 보인다 (주기마다 사라졌다 나타나면 화면 구성이 흔들린다)
 * - 모두 다시 성공하면 알림을 내리고, 복구됐다는 초록 알림을 한 번만 띄운다 (LiveDataAlertHost)
 * 실패한 조회의 값은 queries.ts 가 지우므로, 화면이 비어 있는 이유를 여기서 알려 준다.
 * 없음(404)·인증(401·403)은 화면이 따로 처리하는 정상 응답이라 넣지 않는다.
 *
 * 위치: 페이지가 <LiveDataAlert /> 를 두면 그 자리(예전 페이지별 오류 문구 자리)에 보인다.
 * 그런 자리가 없는 페이지에서만 레이아웃의 LiveDataAlertHost 가 페이지 위에 보인다.
 */
export function LiveDataAlert() {
  const snapshot = useFailingSnapshot();
  useLayoutEffect(() => registerSlot(), []);
  return <AlertBox snapshot={snapshot} />;
}

/** 레이아웃용: 복구 알림을 맡고, 페이지에 알림 자리가 없을 때만 페이지 위에 보인다 */
export function LiveDataAlertHost() {
  const snapshot = useFailingSnapshot();
  const pageSlots = useSyncExternalStore(subscribeSlots, () => slots);
  const toast = useToast();
  const wasFailing = useRef(false);

  useEffect(() => {
    if (snapshot) {
      wasFailing.current = true;
    } else if (wasFailing.current) {
      wasFailing.current = false;
      toast.show('실시간 데이터 연결이 복구되었습니다', 'ok', 3000);
    }
  }, [snapshot, toast]);

  return pageSlots > 0 ? null : <AlertBox snapshot={snapshot} />;
}

/** 지금 화면에 놓인 페이지 알림 자리 수 */
let slots = 0;
const slotListeners = new Set<() => void>();

function subscribeSlots(listener: () => void): () => void {
  slotListeners.add(listener);
  return () => slotListeners.delete(listener);
}

function registerSlot(): () => void {
  slots += 1;
  slotListeners.forEach((notify) => notify());
  return () => {
    slots -= 1;
    slotListeners.forEach((notify) => notify());
  };
}

function useFailingSnapshot(): string {
  const cache = useQueryClient().getQueryCache();
  const subscribe = useCallback((notify: () => void) => cache.subscribe(notify), [cache]);
  return useSyncExternalStore(subscribe, () => failingSnapshot(cache.getAll()));
}

function AlertBox({ snapshot }: { snapshot: string }) {
  if (!snapshot) return null;
  const failures = JSON.parse(snapshot) as Failure[];
  return (
    <div className={styles.liveAlert} role="alert">
      <strong>실시간 데이터를 불러오지 못하고 있습니다</strong>
      <ul>
        {failures.map((failure) => (
          <li key={failure.label}>
            {failure.label}: {failure.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface Failure {
  label: string;
  message: string;
}

/** 조회별 마지막 오류. 다시 읽기 시작하면 TanStack Query 가 state.error 를 비우므로 여기 남겨 둔다 */
const lastErrors = new Map<string, unknown>();

/** 실패 목록을 문자열로 돌려준다 (내용이 같으면 같은 값이라 다시 그리지 않는다). 없으면 빈 문자열 */
function failingSnapshot(queries: Query[]): string {
  const byLabel = new Map<string, string>();
  for (const query of queries) {
    if (query.state.error) lastErrors.set(query.queryHash, query.state.error);
    if (!lastFetchFailed(query.state)) {
      lastErrors.delete(query.queryHash);
      continue;
    }
    const error = lastErrors.get(query.queryHash);
    if (query.getObserversCount() === 0 || !error) continue;
    if (error instanceof ApiError && [401, 403, 404].includes(error.status)) continue;
    const label = labelOf(query.queryKey);
    if (!byLabel.has(label)) byLabel.set(label, messageOf(error));
  }
  if (byLabel.size === 0) return '';
  return JSON.stringify([...byLabel].map(([label, message]) => ({ label, message })));
}

function labelOf(key: QueryKey): string {
  const [head, , sub] = key;
  if (head === 'events') return '이벤트';
  if (head === 'displays') return '화면 배치';
  if (head !== 'vms') return '데이터';
  if (key.length === 1) return 'VM 목록';
  switch (sub) {
    case undefined:
      return 'VM 상태';
    case 'resources':
      return 'VM 자원';
    case 'metrics':
      return 'VM 메트릭';
    case 'processes':
      return '프로세스';
    case 'threads':
      return '스레드';
    case 'telemetry-heartbeat':
      return '에이전트 상태';
    default:
      return 'VM 데이터';
  }
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : String(error);
}
