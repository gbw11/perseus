import { describe, expect, it } from 'vitest';
import type { MetricSample, MetricValue } from '../api/types';
import {
  buildMemoryPoolSeries,
  bytesToKiB,
  MEMORY_POOL_METRICS,
  POOL_FAULT_PERCENT,
  POOL_WARN_PERCENT,
  poolLevel,
} from './memoryPools';

const value = (name: string, amount: number, labels: Record<string, string> | null = null): MetricValue => ({
  name,
  value: amount,
  unit: name.includes('bytes') ? 'bytes' : 'count',
  labels,
  unavailable_reason: null,
});

function sample(at: string, values: MetricValue[], bootId = 'boot-1'): MetricSample {
  return {
    sample_id: `${bootId}-${at}`,
    vm_id: 'vm-a',
    boot_id: bootId,
    source: 'zephyr_telemetry',
    observed_at: at,
    values,
  };
}

function pool(region: 'net_rx_data' | 'net_tx_data', used: number, free: number, capacity: number) {
  const labels = { region };
  return [
    value(MEMORY_POOL_METRICS.used, used, labels),
    value(MEMORY_POOL_METRICS.free, free, labels),
    value(MEMORY_POOL_METRICS.capacity, capacity, labels),
  ];
}

function counters(rx: number, tx: number) {
  return [value(MEMORY_POOL_METRICS.rxPackets, rx), value(MEMORY_POOL_METRICS.txPackets, tx)];
}

describe('Memory Pool telemetry', () => {
  it('region별 used/free/capacity와 실제 0을 구분해 계산한다', () => {
    const series = buildMemoryPoolSeries([
      sample('2026-10-28T05:00:00.000Z', [
        ...pool('net_rx_data', 0, 8192, 8192),
        ...pool('net_tx_data', 1024, 7168, 8192),
      ]),
    ]);

    expect(series.rx).toMatchObject({ state: 'ok', usedBytes: 0, freeBytes: 8192, usagePercent: 0 });
    expect(series.tx).toMatchObject({ state: 'ok', usedBytes: 1024, freeBytes: 7168, usagePercent: 12.5 });
    expect(bytesToKiB(8192)).toBe(8);
  });

  it('capacity 0과 불완전한 region을 0%로 숨기지 않는다', () => {
    const invalid = buildMemoryPoolSeries([
      sample('2026-10-28T05:00:00.000Z', [
        ...pool('net_rx_data', 0, 0, 0),
        value(MEMORY_POOL_METRICS.used, 256, { region: 'net_tx_data' }),
      ]),
    ]);

    expect(invalid.rx).toMatchObject({ state: 'invalid', usagePercent: null });
    expect(invalid.tx).toMatchObject({ state: 'missing', usagePercent: null });
  });

  it('실제 표본 간격으로 packets/s를 계산한다', () => {
    const series = buildMemoryPoolSeries([
      sample('2026-10-28T05:00:00.000Z', counters(100, 60)),
      sample('2026-10-28T05:00:02.500Z', counters(125, 75)),
    ]);

    expect(series.packetRates.at(-1)).toMatchObject({ rxPacketsPerSecond: 10, txPacketsPerSecond: 6 });
  });

  it('재시작이나 counter 감소 뒤 음수 rate를 만들지 않는다', () => {
    const reset = buildMemoryPoolSeries([
      sample('2026-10-28T05:00:00.000Z', counters(100, 60), 'boot-1'),
      sample('2026-10-28T05:00:02.000Z', counters(2, 1), 'boot-2'),
    ]);

    expect(reset.packetRates.at(-1)).toMatchObject({ rxPacketsPerSecond: null, txPacketsPerSecond: null });
  });
});

describe('Pool 경고 단계', () => {
  it('80% 이상은 주의, 95% 이상은 위험이다', () => {
    expect(POOL_WARN_PERCENT).toBe(80);
    expect(POOL_FAULT_PERCENT).toBe(95);
    expect(poolLevel(79.9)).toBe('ok');
    expect(poolLevel(80)).toBe('warn');
    expect(poolLevel(94.9)).toBe('warn');
    expect(poolLevel(95)).toBe('fault');
    expect(poolLevel(null)).toBe('ok');
  });
});
