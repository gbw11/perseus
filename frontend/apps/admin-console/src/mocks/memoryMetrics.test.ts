import { describe, expect, it } from 'vitest';
import { buildMemoryPoolSeries } from '../domain/memoryPools';
import { buildZephyrHeapSeries } from '../domain/vmMetrics';
import { VM_IDS } from './data';
import { mockKernelHeapSamples, mockMemoryPoolSamples } from './memoryMetrics';

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
