import { describe, expect, it } from 'vitest';
import type { MetricSample, MetricValue } from '../api/types';
import {
  buildMemoryPoolSeries,
  buildPacketRateSeries,
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

  it('메모리 metadata 없는 원 네트워크 표본에서 무트래픽 0과 초기화·미측정을 구분한다', () => {
    const at = (index: number, rx: number, tx: number) =>
      sample(new Date(index * 2000).toISOString(), counters(rx, tx));
    const missing = at(3, 30, 60);
    missing.values[0]!.unavailable_reason = 'read_failed';
    expect(
      buildPacketRateSeries([
        at(0, 10, 20),
        at(1, 30, 60),
        at(2, 30, 60),
        missing,
        at(4, 50, 80),
        at(5, 1, 2),
      ]),
    ).toEqual([
      { observedAt: at(1, 0, 0).observed_at, rxPacketsPerSecond: 10, txPacketsPerSecond: 20 },
      { observedAt: at(2, 0, 0).observed_at, rxPacketsPerSecond: 0, txPacketsPerSecond: 0 },
      { observedAt: missing.observed_at, rxPacketsPerSecond: null, txPacketsPerSecond: 0 },
      { observedAt: at(4, 0, 0).observed_at, rxPacketsPerSecond: null, txPacketsPerSecond: 10 },
      { observedAt: at(5, 0, 0).observed_at, rxPacketsPerSecond: null, txPacketsPerSecond: null },
    ]);
  });

  it.each(['vm', 'boot', 'missing-boot', 'interface', 'publisher', 'epoch'])(
    '%s 경계를 넘는 패킷 차분을 만들지 않는다',
    (boundary) => {
      const first = sample('2026-11-05T04:00:00Z', counters(100, 200));
      first.network = { publisher_instance_id: 'publisher', observed_uptime_ms: '1000' };
      first.values.forEach((value) => {
        value.labels = { interface: 'if1', counter_epoch: '1' };
      });
      const next = structuredClone(first);
      next.observed_at = '2026-11-05T04:00:02Z';
      next.values.forEach((value) => {
        value.value! += 100;
      });
      if (boundary === 'vm') next.vm_id = 'vm-b';
      if (boundary === 'boot') next.boot_id = 'boot-2';
      if (boundary === 'missing-boot') next.boot_id = null;
      if (boundary === 'interface')
        next.values.forEach((value) => {
          value.labels!.interface = 'if2';
        });
      if (boundary === 'publisher') next.network!.publisher_instance_id = 'publisher-2';
      if (boundary === 'epoch')
        next.values.forEach((value) => {
          value.labels!.counter_epoch = '2';
        });
      expect(buildPacketRateSeries([first, next]).at(-1)).toMatchObject({
        rxPacketsPerSecond: null,
        txPacketsPerSecond: null,
      });
    },
  );

  it('무효·부정확한 원 카운터를 정상 숫자로 대신하지 않는다', () => {
    const first = sample('2026-11-05T04:00:00Z', counters(10, 20));
    const next = sample('2026-11-05T04:00:02Z', counters(100, 200));
    next.values[0]!.raw_value = '9007199254740993';
    next.values[1]!.status = 'unsupported';
    expect(buildPacketRateSeries([first, next]).at(-1)).toMatchObject({
      rxPacketsPerSecond: null,
      txPacketsPerSecond: null,
    });
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
