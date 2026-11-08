import type { MetricSample } from '../api/types';
import { memoryObservationRunKey, selectMemoryObservationRun } from './memoryObservationRun';

/** heap 직접 수집과 RX/TX cache 관측의 서로 다른 원 시각을 사용한다. */
export interface SharedMemoryObservation {
  state: 'fresh' | 'expired' | 'unverified' | 'unavailable';
  reason: string;
  sample: MetricSample | null;
  observedAt: string | null;
}
const u64 = (value: string | null | undefined): bigint | null =>
  typeof value === 'string' &&
  /^(0|[1-9][0-9]{0,19})$/.test(value) &&
  BigInt(value) <= 18_446_744_073_709_551_615n
    ? BigInt(value)
    : null;

export function sharedObservedAt(sample: MetricSample, family: 'memory' | 'memory_regions'): string | null {
  const m = sample.telemetry;
  const origin = u64(family === 'memory' ? m?.collection?.end_uptime_ms : m?.observed_uptime_ms),
    sent = u64(m?.sent_uptime_ms);
  const received = Date.parse(sample.received_at ?? '');
  if (
    !m ||
    origin == null ||
    sent == null ||
    origin > sent ||
    !Number.isFinite(received) ||
    sent - origin > 2000n
  )
    return null;
  const utc = m.utc_quality === 'synchronized' ? Date.parse(m.sent_at ?? '') : NaN;
  return new Date((Number.isFinite(utc) ? utc : received) - Number(sent - origin)).toISOString();
}

export function sharedMemoryObservation(
  samples: readonly MetricSample[],
  family: 'memory' | 'memory_regions',
  names: readonly string[],
  now: number,
  region?: string,
): SharedMemoryObservation {
  const empty = (state: SharedMemoryObservation['state'], reason: string): SharedMemoryObservation => ({
    state,
    reason,
    sample: null,
    observedAt: null,
  });
  const matching = samples
    .filter((s) =>
      s.values.some((v) => names.includes(v.name) && (region == null || v.labels?.region === region)),
    )
    .sort((a, b) => Date.parse(a.received_at ?? a.observed_at) - Date.parse(b.received_at ?? b.observed_at));
  if (!matching.length) return empty('unverified', '표본 미수신');
  const vmIds = new Set(samples.map((sample) => sample.vm_id));
  if (vmIds.size !== 1) return empty('unverified', 'VM 관측 문맥 불일치');
  const run = selectMemoryObservationRun(samples, matching[0]!.vm_id);
  if (run.state !== 'selected')
    return empty(
      'unverified',
      run.state === 'ambiguous' ? '관측 세션 순서 미확인' : '원 관측 시각 미확인 · 이력만 표시',
    );
  const relevant = matching.filter((sample) => memoryObservationRunKey(sample) === run.key);
  const groups = new Map<
    string,
    {
      sample: MetricSample;
      order: bigint;
      origin: bigint;
      signature: string;
      received: number;
      age: number;
      available: boolean;
    }
  >();
  for (const sample of relevant) {
    const m = sample.telemetry;
    if (
      !m ||
      m.family !== family ||
      !sample.boot_id ||
      !m.publisher_instance_id ||
      !m.context ||
      !m.collection
    )
      continue;
    const origin = u64(family === 'memory' ? m.collection.end_uptime_ms : m.observed_uptime_ms);
    const order = family === 'memory' ? origin : u64(m.source_sample_seq);
    const sent = u64(m.sent_uptime_ms),
      received = Date.parse(sample.received_at ?? '');
    if (
      origin == null ||
      order == null ||
      sent == null ||
      origin > sent ||
      !Number.isFinite(received) ||
      sent - origin > BigInt(Number.MAX_SAFE_INTEGER)
    )
      continue;
    const key = `${order}/${family === 'memory' ? m.collection.sample_id : ''}`;
    const values = sample.values.filter(
      (v) => names.includes(v.name) && (region == null || v.labels?.region === region),
    );
    const available =
      sent - origin <= 2000n &&
      values.length === names.length &&
      m.collection.status === 'ok' &&
      m.collection.source_rc === 0 &&
      values.every((v) => v.status === 'ok' && v.source_rc === 0 && !v.unavailable_reason && v.value != null);
    const signature = JSON.stringify([
      [m.context.binding, m.context.host_boot_id, m.context.execution_id, m.context.receipt_id],
      [...values]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((v) => [
          v.name,
          v.raw_value,
          v.value,
          v.unit,
          v.labels,
          v.status,
          v.source_rc,
          v.unavailable_reason,
        ]),
    ]);
    const old = groups.get(key);
    if (!old)
      groups.set(key, { sample, order, origin, signature, received, age: Number(sent - origin), available });
    else if (old.available && (!available || old.origin !== origin || old.signature !== signature)) {
      old.sample = sample;
      old.available = false;
    }
  }
  const ordered = [...groups.values()].sort((a, b) => (a.order < b.order ? -1 : a.order > b.order ? 1 : 0));
  let previousOrigin: bigint | null = null;
  for (const report of ordered) {
    if (previousOrigin != null && report.origin < previousOrigin) report.available = false;
    if (previousOrigin == null || report.origin > previousOrigin) previousOrigin = report.origin;
  }
  const last = ordered.at(-1);
  if (!last) return empty('unverified', '원 관측 시각 미확인 · 이력만 표시');
  const observedAt = sharedObservedAt(last.sample, family);
  if (!observedAt || Date.parse(observedAt) > now)
    return empty('unverified', '원 관측 시각 오류 · 이력만 표시');
  if (!last.available)
    return { state: 'unavailable', reason: '수집 불가 · 이력만 표시', sample: null, observedAt };
  if (now < last.received) return empty('unverified', '수신 시각 오류 · 이력만 표시');
  if (now - last.received + last.age > 6000)
    return {
      state: 'expired',
      reason: '관측 만료 · 최근 관측값',
      sample: now - Date.parse(observedAt) <= 60_000 ? last.sample : null,
      observedAt,
    };
  return { state: 'fresh', reason: '최근 메모리 관측', sample: last.sample, observedAt };
}
