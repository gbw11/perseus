import { describe, expect, it } from 'vitest';
import { buildMemoryPoolSeries } from '../domain/memoryPools';
import { VM_IDS } from './data';
import { mockMemoryPoolSamples } from './memoryMetrics';

describe('Memory Pool mock samples', () => {
  it('VM A의 RX 점유 상승과 최근 drop 관측 예시를 제공한다', () => {
    const series = buildMemoryPoolSeries(mockMemoryPoolSamples(VM_IDS.autonomous, 1_000_000));

    expect(series.rx.usagePercent).toBe(87.5);
    expect(series.counters.rxDrop).toBe(6);
    expect(series.packetRates.at(-1)?.rxPacketsPerSecond).toBe(25);
  });

  it('VM B의 낮은 점유와 drop 없는 정상 예시를 제공한다', () => {
    const series = buildMemoryPoolSeries(mockMemoryPoolSamples(VM_IDS.manual, 1_000_000));

    expect(series.rx.usagePercent).toBeLessThan(10);
    expect(series.tx.usagePercent).toBeLessThan(10);
    expect(series.counters.rxDrop).toBe(0);
  });
});
