import { describe, expect, it } from 'vitest';
import type { ProcessesResponse, ThreadsResponse } from '../api/types';
import {
  countLabel,
  processCollectionNotes,
  threadCollectionNotes,
  topByCpu,
  topProcesses,
  topThreads,
} from './workloads';

const processes = (overrides: Partial<ProcessesResponse> = {}): ProcessesResponse => ({
  vm_id: 'vm',
  observed_at: '2026-11-04T01:00:00Z',
  stale: false,
  items: Array.from({ length: 12 }, (_, index) => ({
    pid: index + 1,
    name: `p${index + 1}`,
    cpu_percent: index === 3 ? null : index,
    rss_bytes: 1024,
  })),
  process_collection: {
    status: 'ok',
    discovered: 87,
    read_ok: 87,
    vanished: 0,
    failed: 0,
    truncated: false,
    error: null,
  },
  ...overrides,
});

const threads = (overrides: Partial<ThreadsResponse> = {}): ThreadsResponse => ({
  vm_id: 'vm',
  boot_id: 'boot',
  publisher_instance_id: 'pub',
  sample_seq: '1',
  start_uptime_ms: '0',
  end_uptime_ms: '2000',
  window_ms: 2000,
  received_at: '2026-11-04T01:00:00Z',
  stale: false,
  threads_status: 'ok',
  rows_seen: 3,
  rows_returned: 3,
  parts_received: 1,
  part_count: 1,
  complete: true,
  conflicting_pages: false,
  items: [
    { thread_id: 'a', thread_name: 'idle', name_truncated: false, cpu_percent: 70, unavailable_reason: null },
    {
      thread_id: 'b',
      thread_name: 'drive',
      name_truncated: false,
      cpu_percent: 20,
      unavailable_reason: null,
    },
    { thread_id: 'c', thread_name: 'net', name_truncated: false, cpu_percent: null, unavailable_reason: 'x' },
  ],
  ...overrides,
});

describe('CPU 높은 순 상위 목록', () => {
  it('CPU 높은 순으로 최대 10개, 값이 없는 행은 뒤로 보낸다', () => {
    const top = topByCpu(processes().items);
    expect(top).toHaveLength(10);
    expect(top.map((row) => row.cpu_percent)).toEqual([11, 10, 9, 8, 7, 6, 5, 4, 2, 1]);
  });

  it('프로세스 전체 개수는 process_collection.discovered 를 쓰고 "N개 중 10개" 로 적는다', () => {
    expect(countLabel(topProcesses(processes()))).toBe('87개 중 10개');
    expect(countLabel(topProcesses(processes({ process_collection: null })))).toBe('12개 중 10개');
    expect(countLabel(topProcesses(undefined))).toBe('0개 중 0개');
  });

  it('스레드 전체 개수는 rows_seen 을 쓴다', () => {
    const list = topThreads(threads({ rows_seen: 40 }));
    expect(list.items.map((row) => row.thread_name)).toEqual(['idle', 'drive', 'net']);
    expect(countLabel(list)).toBe('40개 중 3개');
  });
});

describe('수집 상태 안내', () => {
  it('프로세스 수집 상태와 stale·실패·잘림을 함께 알려 준다', () => {
    expect(processCollectionNotes(processes()).map((note) => note.text)).toEqual(['수집 정상']);
    expect(
      processCollectionNotes(
        processes({
          stale: true,
          process_collection: {
            status: 'partial',
            discovered: 10,
            read_ok: 8,
            vanished: 0,
            failed: 2,
            truncated: true,
            error: null,
          },
        }),
      ).map((note) => note.text),
    ).toEqual(['보고 만료 (stale)', '일부 프로세스만 수집', '읽기 실패 2개', 'Agent 목록 제한으로 잘림']);
  });

  it('구형 Agent 는 빈 표만으로 판단하지 않도록 미보고를 표시한다', () => {
    expect(processCollectionNotes(processes({ process_collection: null }))[0]?.text).toBe(
      '수집 상태 미보고 (구형 Agent)',
    );
  });

  it('스레드는 threads_status·complete·conflicting_pages 를 따로 알려 준다', () => {
    expect(threadCollectionNotes(threads()).map((note) => note.text)).toEqual(['수집 정상']);
    expect(
      threadCollectionNotes(
        threads({
          threads_status: 'truncated',
          complete: false,
          parts_received: 1,
          part_count: 2,
          conflicting_pages: true,
        }),
      ).map((note) => note.text),
    ).toEqual(['수집 잘림 (truncated)', '페이지 일부 누락 (1/2)', '서로 맞지 않는 페이지']);
    expect(
      threadCollectionNotes(threads({ threads_status: null, stale: true, items: [] })).map((n) => n.text),
    ).toEqual(['보고 만료 (stale)', '스레드 표본 없음']);
  });
});
