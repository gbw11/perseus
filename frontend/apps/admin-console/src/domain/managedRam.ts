import type { MetricSample, MetricValue, TelemetryExecutionContext } from '../api/types';
import { memoryObservationRunKey, selectMemoryObservationRun } from './memoryObservationRun';

/** 같은 원 memory 표본의 물리 관리 범위만 계산한다. pool/heap 내부 점유는 더하지 않는다. */
const FIELDS = [
  'memory.firmware.ram_region_bytes',
  'memory.firmware.mapped_bytes',
  'memory.firmware.static_data_bytes',
  'memory.page_allocator.free_bytes',
] as const;
const U64_MAX = 18_446_744_073_709_551_615n;
const RECEIPT_LEASE_MS = 6000;

export interface ManagedRamIdentity {
  vmId: string;
  bootId: string;
  publisherInstanceId: string;
  context: TelemetryExecutionContext;
}

export interface ManagedRamSummary {
  status: 'ok' | 'unknown' | 'invalid';
  reason: string | null;
  totalBytes: string | null;
  committedBytes: string | null;
  freeBytes: string | null;
  /** ROUND_HALF_UP, 소수 세 자리의 정확한 십진값. */
  percent: string | null;
  sourceStatus: Record<string, { status: string | null; reason: string | null; sourceRc: number | null }>;
}

export interface ManagedRamPoint {
  observedAt: number;
  percent: number | null;
}

export interface ManagedRamView {
  current: ManagedRamSummary;
  points: ManagedRamPoint[];
  lastObservationAt: number | null;
  firstReceivedAt: number | null;
  timeBasis: 'source_utc' | 'receipt_estimate' | null;
}

const unknown = (reason: string, status: ManagedRamSummary['status'] = 'unknown'): ManagedRamSummary => ({
  status,
  reason,
  totalBytes: null,
  committedBytes: null,
  freeBytes: null,
  percent: null,
  sourceStatus: {},
});

function u64(value: unknown): bigint | null {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,19})$/.test(value)) return null;
  const parsed = BigInt(value);
  return parsed <= U64_MAX ? parsed : null;
}

function contextKey(context: TelemetryExecutionContext): string {
  return JSON.stringify([context.binding, context.host_boot_id, context.execution_id, context.receipt_id]);
}

function identity(sample: MetricSample): string | null {
  const meta = sample.telemetry;
  if (!sample.boot_id || !meta?.publisher_instance_id || !meta.context) return null;
  const c = meta.context;
  const ids = [c.host_boot_id, c.execution_id, c.receipt_id];
  if (
    c.binding === 'host_provisioned'
      ? ids.some((v) => !v)
      : c.binding !== 'unbound' || ids.some((v) => v != null)
  )
    return null;
  return JSON.stringify([sample.vm_id, sample.boot_id, meta.publisher_instance_id, contextKey(c)]);
}

function metricValue(metric: MetricValue): bigint | null {
  return metric.unit === 'bytes' && metric.labels != null && Object.keys(metric.labels).length === 0
    ? u64(metric.raw_value)
    : null;
}

/** 원 payload의 출판 당시 정합성이다. 최근 관측 선택·수신 신선도는 buildManagedRamSeries가 확인한다. */
export function managedRamSummary(sample: MetricSample): ManagedRamSummary {
  const meta = sample.telemetry;
  if (meta?.family !== 'memory') return unknown('RAM_NOT_MEMORY');
  if (!identity(sample)) return unknown('RAM_CONTEXT_UNVERIFIED');
  const seq = u64(meta.source_sample_seq),
    observed = u64(meta.observed_uptime_ms),
    sent = u64(meta.sent_uptime_ms);
  if (seq == null || observed == null || sent == null) return unknown('RAM_SOURCE_UNIDENTIFIED');
  const collection = meta.collection;
  const start = u64(collection?.start_uptime_ms),
    end = u64(collection?.end_uptime_ms);
  if (start == null || end == null || start > end || observed > end || end > sent)
    return unknown('RAM_SOURCE_TIME_INVALID', 'invalid');
  const selected = FIELDS.map((name) => sample.values.filter((value) => value.name === name));
  if (selected.some((values) => values.length !== 1)) return unknown('RAM_FIELDS_MISSING');
  const metrics = selected.map((values) => values[0]!);
  const sourceStatus = Object.fromEntries(
    metrics.map((value) => [
      value.name,
      {
        status: value.status ?? null,
        reason: value.unavailable_reason,
        sourceRc: value.source_rc ?? null,
      },
    ]),
  );
  const failure = (reason: string, status: ManagedRamSummary['status'] = 'unknown') => ({
    ...unknown(reason, status),
    sourceStatus,
  });
  if (collection.status !== 'ok' || collection.source_rc !== 0) {
    sourceStatus.collection = { status: collection.status, reason: null, sourceRc: collection.source_rc };
    return failure('RAM_COLLECTION_UNAVAILABLE');
  }
  if (
    metrics.some(
      (value) =>
        value.status !== 'ok' ||
        value.source_rc !== 0 ||
        value.unavailable_reason ||
        metricValue(value) == null,
    )
  )
    return failure('RAM_SOURCE_UNAVAILABLE');
  if (sent - observed > 2000n) return failure('RAM_SOURCE_EXPIRED');
  const [total, mapped, image, free] = metrics.map((value) => metricValue(value)!) as [
    bigint,
    bigint,
    bigint,
    bigint,
  ];
  const error =
    total === 0n
      ? 'RAM_TOTAL_ZERO'
      : free > total
        ? 'RAM_FREE_RANGE'
        : mapped > total
          ? 'RAM_MAPPED_RANGE'
          : image > mapped
            ? 'RAM_STATIC_RANGE'
            : mapped + free > total
              ? 'RAM_MAPPED_FREE_CONFLICT'
              : null;
  if (error) return failure(error, 'invalid');
  const committed = total - free;
  const thousandths = (committed * 100_000n * 2n + total) / (total * 2n);
  return {
    status: 'ok',
    reason: null,
    totalBytes: total.toString(),
    committedBytes: committed.toString(),
    freeBytes: free.toString(),
    percent: `${thousandths / 1000n}.${(thousandths % 1000n).toString().padStart(3, '0')}`,
    sourceStatus,
  };
}

interface Observation {
  sample: MetricSample;
  summary: ManagedRamSummary;
  sourceSeq: bigint;
  sourceUptime: bigint;
  receivedAt: number;
  sourceAge: number;
  observedAt: number;
  signature: string;
}

function observation(sample: MetricSample): Observation | null {
  const m = sample.telemetry;
  const seq = u64(m?.source_sample_seq),
    at = u64(m?.observed_uptime_ms),
    sent = u64(m?.sent_uptime_ms);
  const received = Date.parse(sample.received_at ?? '');
  if (!m || seq == null || at == null || sent == null || at > sent || !Number.isFinite(received)) return null;
  const sourceAge = sent - at;
  if (sourceAge > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  const utc = m.utc_quality === 'synchronized' ? Date.parse(m.sent_at ?? '') : NaN;
  return {
    sample,
    summary: managedRamSummary(sample),
    sourceSeq: seq,
    sourceUptime: at,
    receivedAt: received,
    sourceAge: Number(sourceAge),
    observedAt: (Number.isFinite(utc) ? utc : received) - Number(sourceAge),
    signature: JSON.stringify([
      contextKey(m.context),
      FIELDS.map((name) =>
        sample.values
          .filter((v) => v.name === name)
          .map((v) => [v.raw_value, v.unit, v.status, v.unavailable_reason, v.source_rc]),
      ),
    ]),
  };
}

function orderedSources(samples: MetricSample[]): Observation[] {
  const groups = new Map<string, Observation>();
  for (const sample of [...samples].sort(
    (a, b) => Date.parse(a.received_at ?? '') - Date.parse(b.received_at ?? ''),
  )) {
    const report = observation(sample);
    if (!report) continue;
    const key = report.sourceSeq.toString(),
      prior = groups.get(key);
    if (!prior) {
      groups.set(key, report);
      continue;
    }
    if (prior.summary.status !== 'ok') continue; // 같은 원 표본의 실패는 새 source에서만 회복한다.
    if (
      prior.sourceUptime !== report.sourceUptime ||
      (report.summary.status === 'ok' && prior.signature !== report.signature)
    ) {
      prior.summary = unknown('RAM_SAMPLE_CONFLICT');
    } else if (report.summary.status !== 'ok') prior.summary = report.summary;
    // 새 heap 봉투와 중복 수신은 최초 RAM 수신 시각·source age를 갱신하지 않는다.
  }
  const result = [...groups.values()].sort((a, b) =>
    a.sourceSeq < b.sourceSeq ? -1 : a.sourceSeq > b.sourceSeq ? 1 : 0,
  );
  let previous: bigint | null = null;
  for (const report of result) {
    if (previous != null && report.sourceUptime < previous)
      report.summary = unknown('RAM_SOURCE_TIME_INVALID');
    if (previous == null || report.sourceUptime > previous) previous = report.sourceUptime;
  }
  return result;
}

/** 최근 관측 세션의 원 표본과 최초 수신 lease를 표시한다. expected는 명시 소비자의 선택적 엄격 비교다. */
export function buildManagedRamSeries(
  samples: readonly MetricSample[],
  vmId: string,
  now = Date.now(),
  expected?: ManagedRamIdentity,
): ManagedRamView {
  const relevant = samples.filter((sample) => sample.vm_id === vmId && sample.telemetry?.family === 'memory');
  const run = selectMemoryObservationRun(samples, vmId);
  const expectedKey = expected
    ? JSON.stringify([
        expected.vmId,
        expected.bootId,
        expected.publisherInstanceId,
        contextKey(expected.context),
      ])
    : null;
  const selected = relevant.filter((sample) => memoryObservationRunKey(sample) === run.key);
  const ordered = orderedSources(selected);
  const latest = ordered.at(-1);
  const points: ManagedRamPoint[] = [];
  let previousPoint: number | null = null;
  let previousContext: string | null = null;
  for (const report of ordered) {
    if (report.observedAt < now - 60_000 || report.observedAt > now) continue;
    const context = contextKey(report.sample.telemetry!.context);
    if (previousPoint != null && report.observedAt - previousPoint > RECEIPT_LEASE_MS)
      points.push({ observedAt: previousPoint + RECEIPT_LEASE_MS, percent: null });
    if (previousContext != null && context !== previousContext)
      points.push({ observedAt: report.observedAt, percent: null });
    points.push({
      observedAt: report.observedAt,
      percent: report.summary.status === 'ok' ? Number(report.summary.percent) : null,
    });
    previousPoint = report.observedAt;
    previousContext = context;
  }
  const view = {
    points,
    lastObservationAt: latest?.observedAt ?? null,
    firstReceivedAt: latest?.receivedAt ?? null,
    timeBasis: latest
      ? latest.sample.telemetry?.utc_quality === 'synchronized' &&
        Number.isFinite(Date.parse(latest.sample.telemetry.sent_at ?? ''))
        ? ('source_utc' as const)
        : ('receipt_estimate' as const)
      : null,
  };
  if (run.state !== 'selected')
    return {
      ...view,
      points: [],
      current: unknown(
        run.state === 'ambiguous'
          ? 'RAM_SESSION_AMBIGUOUS'
          : run.state === 'missing'
            ? 'RAM_NOT_RECEIVED'
            : 'RAM_CONTEXT_UNVERIFIED',
      ),
    };
  if (expected && (expected.vmId !== vmId || !latest || identity(latest.sample) !== expectedKey))
    return { ...view, current: unknown(relevant.length ? 'RAM_CONTEXT_CHANGED' : 'RAM_NOT_RECEIVED') };
  if (!latest) return { ...view, current: unknown('RAM_NOT_RECEIVED') };
  if (latest.summary.status !== 'ok') return { ...view, current: latest.summary };
  if (latest.observedAt > now) return { ...view, current: unknown('RAM_SOURCE_TIME_INVALID') };
  if (now < latest.receivedAt) return { ...view, current: unknown('RAM_RECEIVER_CLOCK_ORDER') };
  if (now - latest.receivedAt + latest.sourceAge > RECEIPT_LEASE_MS)
    return { ...view, current: unknown('RAM_RECEIPT_EXPIRED') };
  return { ...view, current: latest.summary };
}
