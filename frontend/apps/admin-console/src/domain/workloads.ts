/**
 * CPU 탭 하단 목록. Linux 상위 프로세스(GET /processes)와 Zephyr 스레드(GET /threads)를
 * CPU 사용률 높은 순으로 최대 10개 보여 주고, 수집 상태를 사람이 읽을 문장으로 바꾼다.
 */
import type { ProcessesResponse, ProcessInfo, ThreadRow, ThreadsResponse } from '../api/types';

export const WORKLOAD_LIMIT = 10;

export interface TopList<T> {
  items: T[];
  /** 전체 개수를 알면 그 값, 모르면 받은 개수 */
  total: number;
}

export interface CollectionNote {
  tone: 'ok' | 'warn' | 'fault' | 'muted';
  text: string;
}

/** CPU 높은 순. 값이 없는 행은 뒤로 */
export function topByCpu<T extends { cpu_percent: number | null }>(
  rows: readonly T[],
  limit = WORKLOAD_LIMIT,
): T[] {
  return [...rows]
    .sort((a, b) => (b.cpu_percent ?? -Infinity) - (a.cpu_percent ?? -Infinity))
    .slice(0, limit);
}

export function topProcesses(response: ProcessesResponse | undefined): TopList<ProcessInfo> {
  const items = response?.items ?? [];
  const discovered = response?.process_collection?.discovered;
  return {
    items: topByCpu(items),
    total: typeof discovered === 'number' && discovered >= items.length ? discovered : items.length,
  };
}

export function topThreads(response: ThreadsResponse | undefined): TopList<ThreadRow> {
  const items = response?.items ?? [];
  const seen = response?.rows_seen;
  return {
    items: topByCpu(items),
    total: typeof seen === 'number' && seen >= items.length ? seen : items.length,
  };
}

const PROCESS_STATUS_TEXT: Record<string, CollectionNote> = {
  ok: { tone: 'ok', text: '수집 정상' },
  first_sample: { tone: 'muted', text: '첫 표본 · CPU% 계산 대기' },
  partial: { tone: 'warn', text: '일부 프로세스만 수집' },
  read_failed: { tone: 'fault', text: '수집 실패' },
  disabled: { tone: 'muted', text: '프로세스 수집 꺼짐' },
};

/** 프로세스 수집 상태. 구형 Agent 는 process_collection 이 null 이라 빈 표만으로 판단하지 않는다 */
export function processCollectionNotes(response: ProcessesResponse | undefined): CollectionNote[] {
  if (!response) return [];
  const notes: CollectionNote[] = [];
  if (response.stale) notes.push({ tone: 'warn', text: '보고 만료 (stale)' });
  const collection = response.process_collection;
  if (!collection) {
    notes.push({ tone: 'muted', text: '수집 상태 미보고 (구형 Agent)' });
    return notes;
  }
  notes.push(PROCESS_STATUS_TEXT[collection.status] ?? { tone: 'muted', text: collection.status });
  if (collection.failed > 0) notes.push({ tone: 'warn', text: `읽기 실패 ${collection.failed}개` });
  if (collection.truncated) notes.push({ tone: 'warn', text: 'Agent 목록 제한으로 잘림' });
  if (collection.error) notes.push({ tone: 'fault', text: collection.error });
  return notes;
}

/** 스레드 수집 상태. complete 는 페이지 전달 완전성, 수집 잘림은 threads_status=truncated 로 따로 본다 */
export function threadCollectionNotes(response: ThreadsResponse | undefined): CollectionNote[] {
  if (!response) return [];
  const notes: CollectionNote[] = [];
  if (response.stale) notes.push({ tone: 'warn', text: '보고 만료 (stale)' });
  const status = response.threads_status;
  if (status === 'ok') notes.push({ tone: 'ok', text: '수집 정상' });
  else if (status === 'truncated') notes.push({ tone: 'warn', text: '수집 잘림 (truncated)' });
  else if (status) notes.push({ tone: 'muted', text: `수집 불가 (${status})` });
  else notes.push({ tone: 'muted', text: '스레드 표본 없음' });
  if (status && !response.complete) {
    notes.push({
      tone: 'warn',
      text: `페이지 일부 누락 (${response.parts_received}/${response.part_count ?? '?'})`,
    });
  }
  if (response.conflicting_pages) notes.push({ tone: 'fault', text: '서로 맞지 않는 페이지' });
  return notes;
}

/** "N개 중 10개" */
export function countLabel(list: TopList<unknown>): string {
  return `${list.total}개 중 ${list.items.length}개`;
}
