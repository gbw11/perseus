import { describe, expect, it } from 'vitest';
import { buildMemoryPoolSeries } from '../domain/memoryPools';
import {
  BACKEND_UNSUPPORTED,
  buildRxByteRateSeries,
  buildZephyrByteSeries,
  interfaceFlagOf,
  ZEPHYR_CARRIER_METRIC,
} from '../domain/networkMetrics';
import { buildZephyrHeapSeries } from '../domain/vmMetrics';
import { VM_IDS } from './data';
import { mockKernelHeapSamples, mockMemoryPoolSamples, mockZephyrNetworkSamples } from './memoryMetrics';

describe('Zephyr network mock samples', () => {
  it('counter_value 바이트 속도와 rx_bytes_per_s 를 제공하고, interface 상태는 지금 백엔드처럼 미지원으로 보인다', () => {
    const samples = mockZephyrNetworkSamples(VM_IDS.manual, 1_000_000);
    const bytes = buildZephyrByteSeries(samples);

    expect(bytes.rates).toHaveLength(29);
    expect(bytes.rx.bytesPerSecond).toBeGreaterThan(0);
    expect(bytes.tx.bytesPerSecond).toBeGreaterThan(0);
    expect(buildRxByteRateSeries(samples).current).toBeGreaterThan(0);
    expect(interfaceFlagOf(samples, ZEPHYR_CARRIER_METRIC)).toEqual({
      state: 'missing',
      reason: BACKEND_UNSUPPORTED,
    });
  });
});

describe('Kernel heap mock samples', () => {
  it('최근 5분 커널 heap 표본을 accounted_bytes·high-water 와 함께 제공한다', () => {
    const now = 1_000_000;
    const samples = mockKernelHeapSamples(VM_IDS.autonomous, now);
    const series = buildZephyrHeapSeries(samples);

    expect(series.points).toHaveLength(30);
    expect(Date.parse(series.points[0]!.observedAt)).toBe(now - 290_000);
    expect(series.current?.totalBasis).toBe('accounted');
    expect(series.current?.highWaterBytes).toBeGreaterThanOrEqual(series.current!.usedBytes);
    // heap 표본은 Pool 계산에 섞이지 않는다
    expect(buildMemoryPoolSeries(samples).rx.state).toBe('missing');
  });
});

describe('Memory Pool mock samples', () => {
  it('VM A의 RX 점유 상승 관측 예시를 제공한다', () => {
    const series = buildMemoryPoolSeries(mockMemoryPoolSamples(VM_IDS.autonomous, 1_000_000));

    expect(series.rx.usagePercent).toBe(87.5);
    expect(series.packetRates.at(-1)?.rxPacketsPerSecond).toBe(25);
  });

  it('VM B의 낮은 점유 정상 예시를 제공한다', () => {
    const series = buildMemoryPoolSeries(mockMemoryPoolSamples(VM_IDS.manual, 1_000_000));

    expect(series.rx.usagePercent).toBeLessThan(10);
    expect(series.tx.usagePercent).toBeLessThan(10);
  });
});
