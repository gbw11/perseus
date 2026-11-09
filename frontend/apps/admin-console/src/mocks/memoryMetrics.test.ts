import { describe, expect, it } from 'vitest';
import { APP_MEMORY_REGIONS, buildAppMemorySeries } from '../domain/appMemory';
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
import {
  mockAppMemorySamples,
  mockKernelHeapSamples,
  mockMemoryPoolSamples,
  mockZephyrNetworkSamples,
} from './memoryMetrics';

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
    // 가장 최근 관측은 조회 시각보다 2초 전 (화면 시계보다 미래로 판정되지 않게)
    expect(Date.parse(series.points[0]!.observedAt)).toBe(now - 2_000 - 290_000);
    expect(Date.parse(series.points.at(-1)!.observedAt)).toBe(now - 2_000);
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

describe('App memory mock samples', () => {
  it('passes the app memory validation for all eight regions with varied usage and one abnormal input', () => {
    const now = 1_000_000_000;
    const series = (vmId: string) =>
      buildAppMemorySeries(
        [
          ...mockKernelHeapSamples(vmId, now),
          ...mockMemoryPoolSamples(vmId, now),
          ...mockAppMemorySamples(vmId, now),
        ],
        vmId,
        now,
      );
    for (const vmId of [VM_IDS.autonomous, VM_IDS.manual]) {
      const regions = series(vmId);
      for (const { id } of APP_MEMORY_REGIONS) {
        expect(regions[id].current.state, `${vmId} ${id}`).toBe('ok');
        expect(regions[id].current.stale).toBe(false);
      }
    }
    const a = series(VM_IDS.autonomous);
    // 지도 풀 87.5% (주의 색), 경로 풀 공간 부족 1회
    expect(a.drive_maps.current.usagePercent).toBe(87.5);
    expect(a.drive_paths.current.capacity_failure_total).toBe(1n);
    expect(a.manual_input.current.buffer?.input_status).toBe('accepted');
    // VM B 조작 입력은 만료 상태 (이상 입력 신호)
    const b = series(VM_IDS.manual);
    expect(b.manual_input.current.buffer?.phase).toBe('stored');
    expect(b.manual_input.current.buffer?.input_status).toBe('expired');
    expect(b.manual_pending.current.buffer?.phase).toBe('empty');
    expect(mockAppMemorySamples(VM_IDS.interaction, now)).toEqual([]);
  });
});
