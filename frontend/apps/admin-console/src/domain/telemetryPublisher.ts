import type { Health } from '@xenova/ui';
import type { TelemetryHeartbeatResponse } from '../api/types';

const STATES = {
  running: { label: '수집 중', tone: 'ok' },
  degraded: { label: '수집 저하', tone: 'recovering' },
  stopping: { label: '종료 중', tone: 'recovering' },
} satisfies Record<
  NonNullable<TelemetryHeartbeatResponse['publisher_state']>,
  { label: string; tone: Health }
>;

/** Reported publisher health expires independently from VM and application status. */
export function publisherObservation(
  vmId: string,
  query: { data?: TelemetryHeartbeatResponse; isError: boolean; isPending: boolean },
): { report: TelemetryHeartbeatResponse | undefined; label: string; tone: Health } {
  const report = query.data?.vm_id === vmId ? query.data : undefined;
  if (query.isError) return { report, label: '조회 불가', tone: 'fault' };
  if (query.isPending) return { report, label: '조회 중', tone: 'stopped' };
  if (report?.status === 'FRESH' && report.publisher_state)
    return { report, ...STATES[report.publisher_state] };
  if (report?.status === 'STALE') return { report, label: '관측 만료', tone: 'recovering' };
  return { report, label: '미수신', tone: 'stopped' };
}
