import type { ManualBufferObservation, MetricSample, MetricValue } from '../api/types';
import { memoryObservationRunKey, selectMemoryObservationRun } from './memoryObservationRun';

/** 앱 고정 풀의 논리 점유를 읽는다. 예약 RAM·힙·VM 전체 메모리는 합산하지 않는다. */
export const APP_MEMORY_REGIONS = [
  { id: 'drive_maps', label: '지도 풀', group: '자동' },
  { id: 'drive_paths', label: '경로 풀', group: '자동' },
  { id: 'drive_search_nodes', label: '탐색 노드 풀', group: '자동' },
  { id: 'drive_odometry', label: '차량 관측 링', group: '공통' },
  { id: 'manual_input', label: '조작 입력', group: '수동' },
  { id: 'manual_pending', label: '대기 작업', group: '수동' },
  { id: 'manual_running', label: '실행 작업', group: '수동' },
  { id: 'manual_result', label: '결과 보관', group: '수동' },
] as const;

export type AppMemoryRegion = (typeof APP_MEMORY_REGIONS)[number]['id'];
const UINT64_MAX = 18_446_744_073_709_551_615n;
const WINDOW_MS = 60_000;
const LEASE_MS = 6000;
const FIELDS = [
  'reserved_bytes',
  'used_bytes',
  'free_bytes',
  'capacity_bytes',
  'high_water_bytes',
  'min_free_bytes',
  'capacity_failure_total',
  'contention_total',
] as const;
type Field = (typeof FIELDS)[number];
type Amounts = Record<Field, bigint | null>;

export interface AppMemorySnapshot extends Amounts {
  state: 'ok' | 'missing' | 'unavailable' | 'invalid';
  reason: string | null;
  sourceRc: number | null;
  sourceSampleSeq: string | null;
  observedUptimeMs: string | null;
  observedAt: number | null;
  receivedAt: string | null;
  timeBasis: 'source_utc' | 'receipt_estimate' | null;
  usagePercent: number | null;
  semanticKey: string | null;
  buffer: ManualBufferObservation | null;
  sourceAgeMs: number | null;
  stale: boolean;
}

export interface AppMemoryPoint {
  observedAt: number;
  percent: number | null;
}

export interface AppMemoryRegionSeries {
  current: AppMemorySnapshot;
  points: AppMemoryPoint[];
}

export type AppMemorySeries = Record<AppMemoryRegion, AppMemoryRegionSeries>;
const emptyAmounts = (): Amounts => Object.fromEntries(FIELDS.map((name) => [name, null])) as Amounts;
const emptySnapshot = (reason = '미수신'): AppMemorySnapshot => ({
  ...emptyAmounts(),
  state: 'missing',
  reason,
  sourceRc: null,
  sourceSampleSeq: null,
  observedUptimeMs: null,
  observedAt: null,
  receivedAt: null,
  timeBasis: null,
  usagePercent: null,
  semanticKey: null,
  buffer: null,
  sourceAgeMs: null,
  stale: false,
});

function unsigned(value: unknown): bigint | null {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,19})$/.test(value)) return null;
  const result = BigInt(value);
  return result <= UINT64_MAX ? result : null;
}

function receivedTime(sample: MetricSample): number {
  return Date.parse(sample.received_at ?? sample.observed_at);
}

function snapshot(sample: MetricSample, region: AppMemoryRegion): AppMemorySnapshot {
  const result = emptySnapshot();
  const data = sample.telemetry;
  const observed = unsigned(data?.observed_uptime_ms);
  const sent = unsigned(data?.sent_uptime_ms);
  if (
    !memoryObservationRunKey(sample) ||
    unsigned(data?.source_sample_seq) == null ||
    observed == null ||
    sent == null ||
    observed > sent
  ) {
    return { ...result, state: 'invalid', reason: '관측 문맥 불완전' };
  }
  const utc = data?.utc_quality !== 'unavailable' ? Date.parse(data?.sent_at ?? '') : NaN;
  const base = Number.isFinite(utc) ? utc : receivedTime(sample);
  const sourceAge = sent - observed;
  if (!Number.isFinite(base) || sourceAge > BigInt(Number.MAX_SAFE_INTEGER)) {
    return { ...result, state: 'invalid', reason: '관측 시각 오류' };
  }
  result.sourceSampleSeq = data!.source_sample_seq;
  result.observedUptimeMs = data!.observed_uptime_ms;
  result.observedAt = base - Number(sourceAge);
  result.receivedAt = sample.received_at ?? sample.observed_at;
  result.timeBasis = Number.isFinite(utc) ? 'source_utc' : 'receipt_estimate';
  result.sourceAgeMs = Number(sourceAge);
  if (sourceAge > 2000n) return { ...result, state: 'unavailable', reason: 'sample_expired' };
  const regionValues = sample.values.filter(
    (value) => value.name.startsWith('memory.app.') && value.labels?.region === region,
  );
  result.semanticKey = JSON.stringify([
    [data!.context.binding, data!.context.host_boot_id, data!.context.execution_id, data!.context.receipt_id],
    data!.observed_uptime_ms,
    data!.collection?.status,
    data!.collection?.source_rc,
    data!.buffer ?? null,
    [...regionValues]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((value) => [
        value.name,
        value.unit,
        value.raw_value,
        value.counter_value,
        value.status,
        value.unavailable_reason,
        value.source_rc,
      ]),
  ]);
  if (data!.collection?.status !== 'ok') {
    return {
      ...result,
      state: 'unavailable',
      reason: data!.collection?.status ?? '수집 상태 없음',
      sourceRc: data!.collection?.source_rc ?? null,
    };
  }
  const amounts = emptyAmounts();
  for (const field of FIELDS) {
    const candidates = sample.values.filter(
      (value) => value.name === `memory.app.${field}` && value.labels?.region === region,
    );
    if (candidates.length !== 1)
      return { ...result, state: 'invalid', reason: '불완전하거나 중복된 풀 표본' };
    const metric = candidates[0]!;
    if (metric.unavailable_reason || metric.status !== 'ok') {
      return {
        ...result,
        state: 'unavailable',
        reason: metric.unavailable_reason ?? metric.status ?? '상태 미확인',
        sourceRc: metric.source_rc ?? null,
      };
    }
    const amount = rawAmount(metric, field);
    if (amount == null) return { ...result, state: 'invalid', reason: '정밀 정수 표본 없음' };
    amounts[field] = amount;
  }
  const {
    reserved_bytes: reserved,
    used_bytes: used,
    free_bytes: free,
    capacity_bytes: capacity,
    high_water_bytes: peak,
    min_free_bytes: minFree,
  } = amounts as Record<Field, bigint>;
  if (
    capacity <= 0n ||
    reserved < capacity ||
    used + free !== capacity ||
    used > peak ||
    peak > capacity ||
    peak + minFree !== capacity
  ) {
    return { ...result, state: 'invalid', reason: '풀 점유·용량 불일치' };
  }
  const buffer = data!.buffer ?? null;
  if (region.startsWith('manual_')) {
    if (!buffer || !validBuffer(buffer, region, used, capacity))
      return { ...result, state: 'invalid', reason: '수동 버퍼 문맥 불완전' };
  } else if (buffer != null) {
    return { ...result, state: 'invalid', reason: '자동 풀에 수동 버퍼 문맥이 포함됨' };
  }
  return {
    ...result,
    ...amounts,
    buffer,
    state: 'ok',
    reason: null,
    sourceRc: null,
    usagePercent: Number((used * 100_000n) / capacity) / 1000,
  };
}

function rawAmount(metric: MetricValue, field: Field): bigint | null {
  if (metric.unit !== (field.endsWith('_total') ? 'count' : 'bytes')) return null;
  // 숫자 projection만 남은 구형 응답은 정확도를 보증할 수 없다.
  return unsigned(metric.raw_value ?? (field.endsWith('_total') ? metric.counter_value : null));
}

/** 지역별 원 표본과 최초 수신 lease를 보존한다. 작업 상태는 출력 권한이 아니다. */
export function buildAppMemorySeries(
  samples: readonly MetricSample[],
  vmId: string,
  now = Date.now(),
): AppMemorySeries {
  const empty = (): AppMemoryRegionSeries => ({ current: emptySnapshot(), points: [] });
  const result: AppMemorySeries = {
    drive_maps: empty(),
    drive_paths: empty(),
    drive_search_nodes: empty(),
    drive_odometry: empty(),
    manual_input: empty(),
    manual_pending: empty(),
    manual_running: empty(),
    manual_result: empty(),
  };
  const relevant = samples.filter(
    (sample) => sample.vm_id === vmId && sample.telemetry && Number.isFinite(receivedTime(sample)),
  );
  const run = selectMemoryObservationRun(samples, vmId);
  if (run.state !== 'selected') {
    for (const { id } of APP_MEMORY_REGIONS)
      result[id].current = emptySnapshot(
        run.state === 'ambiguous'
          ? '관측 세션 순서 미확인'
          : run.state === 'unverified'
            ? '관측 문맥 불완전'
            : '미수신',
      );
    return result;
  }
  for (const { id } of APP_MEMORY_REGIONS) {
    const groups = new Map<string, AppMemorySnapshot>();
    const reports = relevant
      .filter(
        (sample) =>
          memoryObservationRunKey(sample) === run.key &&
          sample.telemetry?.family === 'app_memory' &&
          sample.values.some((value) => value.name.startsWith('memory.app.') && value.labels?.region === id),
      )
      .sort(
        (a, b) =>
          receivedTime(a) - receivedTime(b) ||
          compareDecimal(a.telemetry?.message_seq, b.telemetry?.message_seq),
      );
    for (const sample of reports) {
      const report = snapshot(sample, id);
      const key = report.sourceSampleSeq ?? sample.sample_id;
      const previous = groups.get(key);
      if (!previous) {
        groups.set(key, report);
        continue;
      }
      if (previous.state !== 'ok') continue; // 같은 원 표본의 오류는 새 source에서만 회복한다.
      if (report.state !== 'ok' || previous.semanticKey !== report.semanticKey) {
        groups.set(key, {
          ...previous,
          ...emptyAmounts(),
          buffer: null,
          state: report.state !== 'ok' ? report.state : 'invalid',
          sourceRc: report.sourceRc,
          reason: report.state !== 'ok' ? report.reason : '같은 원 표본의 값 충돌',
          usagePercent: null,
        });
      }
    }
    const ordered = [...groups.values()].sort((a, b) => compareDecimal(a.sourceSampleSeq, b.sourceSampleSeq));
    let beforeUptime: bigint | null = null;
    let previousPoint: AppMemoryPoint | null = null;
    for (const report of ordered) {
      const uptime = unsigned(report.observedUptimeMs);
      if (
        (uptime != null && beforeUptime != null && uptime < beforeUptime) ||
        (report.observedAt != null && report.observedAt > now)
      ) {
        Object.assign(report, emptyAmounts(), {
          state: 'invalid',
          buffer: null,
          reason:
            uptime != null && beforeUptime != null && uptime < beforeUptime
              ? '원 관측 시각 역전'
              : '원 관측 시각이 미래',
          usagePercent: null,
        });
      }
      if (uptime != null && (beforeUptime == null || uptime > beforeUptime)) beforeUptime = uptime;
      report.stale =
        report.receivedAt != null &&
        report.sourceAgeMs != null &&
        now - Date.parse(report.receivedAt) + report.sourceAgeMs > LEASE_MS;
      if (report.observedAt != null && report.observedAt >= now - WINDOW_MS && report.observedAt <= now) {
        if (previousPoint && report.observedAt - previousPoint.observedAt > LEASE_MS)
          result[id].points.push({ observedAt: previousPoint.observedAt + LEASE_MS, percent: null });
        previousPoint = { observedAt: report.observedAt, percent: report.usagePercent };
        result[id].points.push(previousPoint);
      }
    }
    const latest = ordered.at(-1);
    result[id].current = latest ?? emptySnapshot();
    if (latest?.observedAt != null && latest.observedAt < now - WINDOW_MS)
      result[id].current = {
        ...latest,
        ...emptyAmounts(),
        buffer: null,
        state: 'missing',
        reason: '최근 60초 표본 없음',
        usagePercent: null,
      };
  }
  return result;
}

function compareDecimal(a: string | null | undefined, b: string | null | undefined): number {
  const aa = unsigned(a),
    bb = unsigned(b);
  return aa != null && bb != null ? (aa < bb ? -1 : aa > bb ? 1 : 0) : 0;
}

function validBuffer(
  buffer: ManualBufferObservation,
  region: AppMemoryRegion,
  used: bigint,
  capacity: bigint,
): boolean {
  const content = unsigned(buffer.content_bytes);
  if (
    content == null ||
    content > capacity ||
    unsigned(buffer.context_revision) == null ||
    unsigned(buffer.connection_revision) == null ||
    !Number.isInteger(buffer.source_rc) ||
    buffer.source_rc > 0 ||
    typeof buffer.revoked !== 'boolean' ||
    !['missing', 'unclassified', 'accepted', 'invalid', 'expired', 'context_changed'].includes(
      buffer.input_status,
    )
  )
    return false;
  for (const value of [
    buffer.sequence,
    buffer.source_uptime_ms,
    buffer.owner_lifetime,
    buffer.last_read_sequence,
  ])
    if (value !== null && unsigned(value) == null) return false;
  const expected: Record<string, string> = {
    manual_input: 'stored',
    manual_pending: 'pending',
    manual_running: 'running',
    manual_result: 'retained',
  };
  if (buffer.phase !== 'empty' && buffer.phase !== expected[region]) return false;
  if (buffer.phase === 'empty' && (used !== 0n || content !== 0n || buffer.sequence !== null)) return false;
  if (
    buffer.phase !== 'empty' &&
    (buffer.sequence === null || (region === 'manual_input' ? used !== content : used !== capacity))
  )
    return false;
  return region === 'manual_result'
    ? typeof buffer.performed === 'boolean'
    : buffer.performed === null && buffer.last_read_sequence === null;
}

/** 표시 단위만 바꾸고 uint64 원문은 bigint로 보존한다. */
export function formatAppBytes(value: bigint | null): string {
  if (value == null) return '—';
  if (value > 0n && value < 1024n) return `${value.toString()} B`;
  const divisor = value >= 1_048_576n ? 1_048_576n : 1024n;
  const unit = divisor === 1024n ? 'KiB' : 'MiB';
  if (value === 0n) return '0 KiB';
  const hundredths = (value * 100n + divisor / 2n) / divisor;
  return `${(hundredths / 100n).toLocaleString('ko-KR')}.${(hundredths % 100n).toString().padStart(2, '0')} ${unit}`;
}
