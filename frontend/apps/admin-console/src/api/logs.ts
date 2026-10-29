import { request } from './client';

export interface RuntimeLog {
  id: number;
  event_id: string;
  source_id: string;
  vm_id: string | null;
  component: string;
  boot_id: string;
  observed_at: string;
  received_at: number;
  level: string;
  message: string;
}
export interface LogPage {
  epoch: string;
  items: RuntimeLog[];
  next_cursor: number;
  has_more: boolean;
  history_truncated: boolean;
}

export function parseLogPage(value: unknown): LogPage {
  const p = value as Partial<LogPage> | null;
  if (
    !p ||
    typeof p.epoch !== 'string' ||
    !Array.isArray(p.items) ||
    p.items.length > 500 ||
    !Number.isSafeInteger(p.next_cursor) ||
    Number(p.next_cursor) < 0 ||
    typeof p.has_more !== 'boolean' ||
    typeof p.history_truncated !== 'boolean' ||
    p.items.some(
      (r) =>
        !r ||
        !Number.isSafeInteger(r.id) ||
        r.id < 1 ||
        typeof r.source_id !== 'string' ||
        typeof r.component !== 'string' ||
        !(r.vm_id === null || typeof r.vm_id === 'string') ||
        typeof r.observed_at !== 'string' ||
        typeof r.message !== 'string' ||
        r.message.length > 8192 ||
        !['DEBUG', 'INFO', 'WARN', 'ERROR', 'FATAL'].includes(r.level),
    )
  ) {
    throw new Error('로그 응답 형식을 확인할 수 없습니다.');
  }
  return p as LogPage;
}

export async function fetchLogs(options: {
  after?: number;
  vmId?: string;
  signal?: AbortSignal;
}): Promise<LogPage> {
  const params = new URLSearchParams({ limit: '200' });
  if (options.after !== undefined) params.set('after', String(options.after));
  if (options.vmId) params.set('vm_id', options.vmId);
  return parseLogPage(await request<unknown>(`/api/v1/logs?${params}`, { signal: options.signal }));
}

export interface LogSource {
  source_id: string;
  vm_id: string | null;
  components: string[];
  state: 'connected' | 'disconnected' | 'unavailable';
}
export function fetchLogSources(signal?: AbortSignal) {
  return request<{ epoch: string; items: LogSource[] }>('/api/v1/logs/sources', { signal });
}

/** Stable identity and a bounded browser buffer, including overlapping reconnect pages. */
export function appendLogs(previous: RuntimeLog[], incoming: RuntimeLog[]): RuntimeLog[] {
  return [...new Map([...previous, ...incoming].map((r) => [r.id, r])).values()]
    .sort((a, b) => a.id - b.id)
    .slice(-1000);
}
