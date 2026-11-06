import type { TelemetryHeartbeatResponse } from '../api/types';
import { VM_IDS } from './data';

/**
 * v2 telemetry heartbeat mock. Network 탭 수집 상태 배지가 상태별로 보이도록 VM 마다 다르게 둔다.
 * VM A 수집 중 · VM B 수집 저하 · Linux 관측 만료(STALE) · 그 밖의 VM 미수신(UNKNOWN)
 */
export function mockTelemetryHeartbeat(vmId: string, now = Date.now()): TelemetryHeartbeatResponse {
  const fresh = (
    state: 'running' | 'degraded',
    successAgeMs: number,
    failures: number,
    submitFailures: number,
  ) => ({
    vm_id: vmId,
    status: 'FRESH' as const,
    publisher_state: state,
    last_collection_success_uptime_ms: String(120_000 - successAgeMs),
    last_collection_success_age_ms: successAgeMs,
    collection_failures_total: failures,
    publish_submit_failures_total: submitFailures,
    received_at: new Date(now).toISOString(),
    age_ms: 0,
  });
  if (vmId === VM_IDS.autonomous) return fresh('running', 500, 2, 0);
  if (vmId === VM_IDS.manual) return fresh('degraded', 8_000, 5, 1);
  if (vmId === VM_IDS.interaction) {
    // 마지막 heartbeat 를 9초 전에 받아 lease(6초)가 지난 상태
    return {
      ...fresh('running', 500, 0, 0),
      status: 'STALE' as const,
      received_at: new Date(now - 9_000).toISOString(),
      age_ms: 9_000,
    };
  }
  return {
    vm_id: vmId,
    status: 'UNKNOWN' as const,
    publisher_state: null,
    last_collection_success_uptime_ms: null,
    last_collection_success_age_ms: null,
    collection_failures_total: null,
    publish_submit_failures_total: null,
    received_at: null,
    age_ms: null,
  };
}
