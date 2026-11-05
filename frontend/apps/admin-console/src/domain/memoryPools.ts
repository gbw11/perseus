import type { MetricSample, MetricValue } from '../api/types';

export const MEMORY_POOL_METRICS = {
  used: 'memory.region.used_bytes',
  free: 'memory.region.free_bytes',
  capacity: 'memory.region.capacity_bytes',
  rxPackets: 'network.netfront.rx_packets',
  txPackets: 'network.netfront.tx_packets',
} as const;

export type PoolRegion = 'net_rx_data' | 'net_tx_data';
export type PoolDataState = 'ok' | 'missing' | 'invalid';

export interface PoolSnapshot {
  state: PoolDataState;
  usedBytes: number | null;
  freeBytes: number | null;
  capacityBytes: number | null;
  usagePercent: number | null;
  reason: string | null;
}

export interface PoolUsagePoint {
  observedAt: string;
  rxPercent: number | null;
  txPercent: number | null;
}

export interface PacketRatePoint {
  observedAt: string;
  rxPacketsPerSecond: number | null;
  txPacketsPerSecond: number | null;
}

export interface MemoryPoolSeries {
  rx: PoolSnapshot;
  tx: PoolSnapshot;
  usage: PoolUsagePoint[];
  packetRates: PacketRatePoint[];
  lastObservedAt: string | null;
}

interface CounterSnapshot {
  observedAt: string;
  bootId: string | null;
  rxPackets: number | null;
  txPackets: number | null;
}

const EMPTY_POOL: PoolSnapshot = {
  state: 'missing',
  usedBytes: null,
  freeBytes: null,
  capacityBytes: null,
  usagePercent: null,
  reason: '데이터 없음',
};

export function buildMemoryPoolSeries(samples: MetricSample[]): MemoryPoolSeries {
  const ordered = [...samples].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  const usage: PoolUsagePoint[] = [];
  const packetRates: PacketRatePoint[] = [];
  const counters: CounterSnapshot[] = [];
  let rx = EMPTY_POOL;
  let tx = EMPTY_POOL;
  let lastObservedAt: string | null = null;

  for (const sample of ordered) {
    if (!Number.isFinite(Date.parse(sample.observed_at))) continue;
    const hasPoolMetric = sample.values.some((value) => value.name.startsWith('memory.region.'));
    const hasCounterMetric = sample.values.some((value) => value.name.startsWith('network.netfront.'));
    if (!hasPoolMetric && !hasCounterMetric) continue;

    lastObservedAt = sample.observed_at;
    if (hasPoolMetric) {
      rx = poolOf(sample.values, 'net_rx_data');
      tx = poolOf(sample.values, 'net_tx_data');
      usage.push({ observedAt: sample.observed_at, rxPercent: rx.usagePercent, txPercent: tx.usagePercent });
    }
    if (hasCounterMetric) counters.push(counterSnapshot(sample));
  }

  for (let index = 1; index < counters.length; index += 1) {
    const previous = counters[index - 1]!;
    const current = counters[index]!;
    const seconds = elapsedSeconds(previous.observedAt, current.observedAt);
    const sameBoot = !previous.bootId || !current.bootId || previous.bootId === current.bootId;
    packetRates.push({
      observedAt: current.observedAt,
      rxPacketsPerSecond: sameBoot ? rate(previous.rxPackets, current.rxPackets, seconds) : null,
      txPacketsPerSecond: sameBoot ? rate(previous.txPackets, current.txPackets, seconds) : null,
    });
  }

  return { rx, tx, usage, packetRates, lastObservedAt };
}

export function bytesToKiB(bytes: number): number {
  return bytes / 1024;
}

function poolOf(values: MetricValue[], region: PoolRegion): PoolSnapshot {
  const used = metricNumber(values, MEMORY_POOL_METRICS.used, region);
  const free = metricNumber(values, MEMORY_POOL_METRICS.free, region);
  const capacity = metricNumber(values, MEMORY_POOL_METRICS.capacity, region);
  if (used == null || free == null || capacity == null) return EMPTY_POOL;
  if (
    capacity <= 0 ||
    used < 0 ||
    free < 0 ||
    used > capacity ||
    free > capacity ||
    used + free !== capacity
  ) {
    return {
      state: 'invalid',
      usedBytes: used,
      freeBytes: free,
      capacityBytes: capacity,
      usagePercent: null,
      reason: '잘못된 Pool 데이터',
    };
  }
  return {
    state: 'ok',
    usedBytes: used,
    freeBytes: free,
    capacityBytes: capacity,
    usagePercent: (used / capacity) * 100,
    reason: null,
  };
}

function counterSnapshot(sample: MetricSample): CounterSnapshot {
  return {
    observedAt: sample.observed_at,
    bootId: sample.boot_id ?? null,
    rxPackets: metricNumber(sample.values, MEMORY_POOL_METRICS.rxPackets),
    txPackets: metricNumber(sample.values, MEMORY_POOL_METRICS.txPackets),
  };
}

function metricNumber(values: MetricValue[], name: string, region?: PoolRegion): number | null {
  const metric = values.find(
    (value) => value.name === name && (region == null || value.labels?.region === region),
  );
  if (!metric || metric.unavailable_reason || metric.value == null || !Number.isFinite(metric.value))
    return null;
  return metric.value;
}

function elapsedSeconds(previous: string, current: string): number | null {
  const value = (Date.parse(current) - Date.parse(previous)) / 1000;
  return Number.isFinite(value) && value > 0 ? value : null;
}

function rate(previous: number | null, current: number | null, seconds: number | null): number | null {
  const change = delta(previous, current);
  return change == null || seconds == null ? null : change / seconds;
}

function delta(previous: number | null, current: number | null): number | null {
  if (previous == null || current == null || current < previous) return null;
  return current - previous;
}
