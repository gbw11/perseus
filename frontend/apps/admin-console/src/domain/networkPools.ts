import type { MetricSample } from '../api/types';
import { buildMemoryPoolSeries, type PoolSnapshot, type PoolUsagePoint } from './memoryPools';
import { memoryObservationRunKey, selectMemoryObservationRun } from './memoryObservationRun';
import { sharedMemoryObservation, sharedObservedAt } from './sharedMemoryObservation';

export type NetworkPool = PoolSnapshot & { expired?: boolean };

export interface NetworkPoolView {
  rx: NetworkPool;
  tx: NetworkPool;
  /** 점유율 그래프. 선택된 관측 실행(run)의 표본만, 원 관측 시각 기준 */
  usage: PoolUsagePoint[];
  lastObservedAt: string | null;
}

const POOL_METRICS = [
  'memory.region.used_bytes',
  'memory.region.free_bytes',
  'memory.region.capacity_bytes',
] as const;

/**
 * RX/TX Pool 현재값과 점유율 이력. Memory 탭(origin/frontend)의 Pool 계산 규칙을 그대로 따른다:
 * 관측 실행(run)을 하나 골라 그 표본만 쓰고, 그래프 시각은 원 관측 시각(sharedObservedAt)으로 옮긴다.
 * 현재값은 영역별 최근 관측(sharedMemoryObservation)이며 만료면 expired 로 표시한다.
 */
export function networkPoolView(samples: readonly MetricSample[], now: number): NetworkPoolView {
  const run = selectMemoryObservationRun(samples, samples[0]?.vm_id ?? '');
  const projected = (
    run.state === 'selected' ? samples.filter((sample) => memoryObservationRunKey(sample) === run.key) : []
  ).map((sample) => {
    const family = sample.telemetry?.family;
    const at = family === 'memory' || family === 'memory_regions' ? sharedObservedAt(sample, family) : null;
    return at ? { ...sample, observed_at: at } : sample;
  });
  const series = buildMemoryPoolSeries(projected);

  const pool = (region: 'net_rx_data' | 'net_tx_data'): NetworkPool => {
    const view = sharedMemoryObservation(samples, 'memory_regions', POOL_METRICS, now, region);
    if ((view.state === 'fresh' || view.state === 'expired') && view.sample) {
      return {
        ...buildMemoryPoolSeries([view.sample])[region === 'net_rx_data' ? 'rx' : 'tx'],
        expired: view.state === 'expired',
      };
    }
    return {
      state: 'missing',
      usedBytes: null,
      freeBytes: null,
      capacityBytes: null,
      usagePercent: null,
      reason: view.reason,
    };
  };

  return {
    rx: pool('net_rx_data'),
    tx: pool('net_tx_data'),
    usage: series.usage,
    lastObservedAt: series.lastObservedAt,
  };
}
