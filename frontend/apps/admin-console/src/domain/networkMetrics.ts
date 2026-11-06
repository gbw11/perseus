/**
 * Network 탭 계산. 이름은 backend/README.md "Zephyr 지표와 앱 보고 조회" 와 Telemetry 2.1 카탈로그를 따른다.
 * - Linux: guest_agent 의 net_rx/tx_bytes_total (value 숫자, labels.iface 별)
 * - Zephyr: v1 net_rx/tx_bytes_total 은 value=null, counter_value(uint64 문자열)
 *   v2 rx_bytes_per_s(gauge), network.interface_up·network.carrier(boolean)
 */
import type { MetricSample, MetricValue } from '../api/types';
import { unsigned } from './networkRate';
import { buildLabeledCounterSeries, type DiskIoCounter, type LabeledCounterSeries } from './vmMetrics';

export const NET_RX_BYTES_METRIC = 'net_rx_bytes_total';
export const NET_TX_BYTES_METRIC = 'net_tx_bytes_total';
export const ZEPHYR_RX_BYTES_RATE_METRIC = 'rx_bytes_per_s';
export const ZEPHYR_INTERFACE_UP_METRIC = 'network.interface_up';
export const ZEPHYR_CARRIER_METRIC = 'network.carrier';

const NO_DATA = '데이터 없음';
export const BACKEND_UNSUPPORTED = '데이터 없음 (백엔드 미지원)';
/** 백엔드가 값을 못 읽으면 표본 status 문자열이 사유 자리에 들어온다 (예: "ok"). 사유로 보여 주지 않는다 */
const STATUS_WORDS = new Set(['ok', 'unavailable']);

export interface RxTxPoint {
  observedAt: string;
  rx: number | null;
  tx: number | null;
}

export interface NetworkByteSeries {
  observedAt: string | null;
  rx: DiskIoCounter;
  tx: DiskIoCounter;
  rates: RxTxPoint[];
}

/** Linux 송수신. 인터페이스별(labels.iface) 합산, 같은 boot_id·인터페이스 묶음일 때만 속도 */
export function buildLinuxNetworkSeries(samples: readonly MetricSample[]): NetworkByteSeries {
  return fromLabeled(buildLabeledCounterSeries(samples, [NET_RX_BYTES_METRIC, NET_TX_BYTES_METRIC], 'iface'));
}

function fromLabeled(series: LabeledCounterSeries): NetworkByteSeries {
  return {
    observedAt: series.observedAt,
    rx: series.first,
    tx: series.second,
    rates: series.rates.map((point) => ({ observedAt: point.observedAt, rx: point.first, tx: point.second })),
  };
}

interface ExactCounter {
  /** boot_id·publisher_instance_id·iface·counter_epoch — 같을 때만 차분한다 */
  key: string;
  value: bigint | null;
  reason: string | null;
}

interface ExactObservation {
  observedAt: string;
  uptimeMs: bigint | null;
  rx: ExactCounter | null;
  tx: ExactCounter | null;
}

/**
 * Zephyr RX/TX 바이트. counter_value 를 BigInt 로 읽고, VM·boot_id·network.publisher_instance_id·
 * labels.iface·labels.counter_epoch 가 같고 network.observed_uptime_ms 가 증가한 두 표본만 차분한다.
 * 간격은 수신 시각이 아니라 관측 업타임으로 구한다.
 */
export function buildZephyrByteSeries(samples: readonly MetricSample[]): NetworkByteSeries {
  const observations = [...samples]
    .sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at))
    .filter((sample) =>
      sample.values.some((value) => value.name === NET_RX_BYTES_METRIC || value.name === NET_TX_BYTES_METRIC),
    )
    .map((sample): ExactObservation => ({
      observedAt: sample.observed_at,
      uptimeMs: unsigned(sample.network?.observed_uptime_ms),
      rx: exactCounter(sample, NET_RX_BYTES_METRIC),
      tx: exactCounter(sample, NET_TX_BYTES_METRIC),
    }));

  const current = observations.at(-1);
  if (!current) {
    const missing = { totalBytes: null, bytesPerSecond: null, reason: NO_DATA };
    return { observedAt: null, rx: missing, tx: missing, rates: [] };
  }

  const rates = observations.slice(1).map((now, index) => {
    const before = observations[index]!;
    return {
      observedAt: now.observedAt,
      rx: exactRate(before, now, before.rx, now.rx),
      tx: exactRate(before, now, before.tx, now.tx),
    };
  });
  const latest = rates.at(-1);
  const counter = (value: ExactCounter | null, rate: number | null | undefined): DiskIoCounter => ({
    totalBytes: value?.value == null ? null : Number(value.value),
    bytesPerSecond: rate ?? null,
    reason: value?.value == null ? (value?.reason ?? NO_DATA) : null,
  });

  return {
    observedAt: current.observedAt,
    rx: counter(current.rx, latest?.rx),
    tx: counter(current.tx, latest?.tx),
    rates,
  };
}

function exactRate(
  before: ExactObservation,
  now: ExactObservation,
  a: ExactCounter | null,
  b: ExactCounter | null,
): number | null {
  if (!a || !b || a.value == null || b.value == null || a.key !== b.key) return null;
  if (before.uptimeMs == null || now.uptimeMs == null || now.uptimeMs <= before.uptimeMs) return null;
  if (b.value < a.value) return null;
  return (Number(b.value - a.value) * 1000) / Number(now.uptimeMs - before.uptimeMs);
}

function exactCounter(sample: MetricSample, name: string): ExactCounter | null {
  const metric = sample.values.find((value) => value.name === name);
  if (!metric) return null;
  const key = [
    sample.boot_id ?? '',
    sample.network?.publisher_instance_id ?? '',
    metric.labels?.iface ?? '',
    metric.labels?.counter_epoch ?? '',
  ].join('|');
  const value = unsigned(metric.counter_value);
  return { key, value, reason: value == null ? displayReason(metric) : null };
}

/** Zephyr 직접 계측 RX 바이트 속도(rx_bytes_per_s) 시계열 */
export function buildRxByteRateSeries(samples: readonly MetricSample[]): {
  points: { observedAt: string; value: number }[];
  current: number | null;
  reason: string | null;
} {
  const ordered = [...samples].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  let reason: string | null = NO_DATA;
  const points: { observedAt: string; value: number }[] = [];
  for (const sample of ordered) {
    const metric = sample.values.find((value) => value.name === ZEPHYR_RX_BYTES_RATE_METRIC);
    if (!metric) continue;
    const value = metric.value;
    if (value == null || !Number.isFinite(value) || value < 0) {
      reason = displayReason(metric);
      continue;
    }
    points.push({ observedAt: sample.observed_at, value });
    reason = null;
  }
  const last = points.at(-1);
  return { points, current: last?.value ?? null, reason: last ? reason : (reason ?? NO_DATA) };
}

/** 차분으로 속도를 못 구했을 때만 대체 속도를 쓴다. 누적값 자체가 없으면 대체하지 않는다 */
export function withRateFallback(counter: DiskIoCounter, fallback: number | null): DiskIoCounter {
  if (counter.bytesPerSecond != null || counter.totalBytes == null || fallback == null) return counter;
  return { ...counter, bytesPerSecond: fallback };
}

/** 대체 속도를 그 시각의 값으로 볼 수 있는 최대 나이 */
const FALLBACK_MAX_AGE_MS = 10_000;

/**
 * 그래프용 RX 속도 시계열. counter_value 차분 값이 있는 점은 그대로 두고, 차분을 못 구한 점(세대 바뀜 등)만
 * 그 시각 이전의 가장 가까운 대체 속도(rx_bytes_per_s)로 채운다. 차분 점이 하나도 없으면 대체 시계열을 쓴다.
 */
export function mergeRxRateFallback(
  rates: readonly RxTxPoint[],
  fallback: readonly { observedAt: string; value: number }[],
): RxTxPoint[] {
  if (rates.length === 0) {
    return fallback.map((point) => ({ observedAt: point.observedAt, rx: point.value, tx: null }));
  }
  return rates.map((point) => {
    if (point.rx != null) return point;
    const at = Date.parse(point.observedAt);
    let nearest: { observedAt: string; value: number } | undefined;
    for (const candidate of fallback) {
      const candidateAt = Date.parse(candidate.observedAt);
      if (candidateAt <= at && at - candidateAt <= FALLBACK_MAX_AGE_MS) nearest = candidate;
    }
    return nearest ? { ...point, rx: nearest.value } : point;
  });
}

export type InterfaceFlag =
  | { state: 'on'; observedAt: string }
  | { state: 'off'; observedAt: string }
  | { state: 'missing'; reason: string };

/**
 * network.interface_up / network.carrier 의 마지막 값. boolean·숫자(0/1)·"true"/"false" 를 모두 받는다.
 * 지금 백엔드는 boolean 을 읽지 못해 value=null, unavailable_reason="ok" 로 내려오므로 미지원으로 표시한다.
 */
export function interfaceFlagOf(samples: readonly MetricSample[], name: string): InterfaceFlag {
  const ordered = [...samples].sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
  for (const sample of ordered) {
    const metric = sample.values.find((value) => value.name === name);
    if (!metric) continue;
    const flag = booleanOf(metric.value as unknown);
    if (flag != null) return { state: flag ? 'on' : 'off', observedAt: sample.observed_at };
    return { state: 'missing', reason: displayReason(metric) };
  }
  return { state: 'missing', reason: NO_DATA };
}

function booleanOf(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value === 1 ? true : value === 0 ? false : null;
  if (value === 'true') return true;
  if (value === 'false') return false;
  return null;
}

/** 표시할 사유. status 문자열("ok" 등)이나 빈 사유는 백엔드가 값을 못 읽은 것으로 본다 */
export function displayReason(metric: Pick<MetricValue, 'unavailable_reason'>): string {
  const reason = metric.unavailable_reason?.trim();
  if (!reason || STATUS_WORDS.has(reason.toLowerCase())) return BACKEND_UNSUPPORTED;
  return reason;
}
