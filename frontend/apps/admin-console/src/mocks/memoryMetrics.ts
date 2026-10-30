import type { MetricSample, MetricValue } from '../api/types';
import { MEMORY_POOL_METRICS } from '../domain/memoryPools';
import { VM_IDS } from './data';

const CAPACITY = 8 * 1024;
const INTERVAL_MS = 2000;
const SAMPLES = 30;

const metric = (
  name: string,
  value: number,
  unit: string,
  labels: Record<string, string> | null = null,
): MetricValue => ({ name, value, unit, labels, unavailable_reason: null });

function poolValues(region: 'net_rx_data' | 'net_tx_data', usedBytes: number): MetricValue[] {
  const labels = { region };
  return [
    metric(MEMORY_POOL_METRICS.used, usedBytes, 'bytes', labels),
    metric(MEMORY_POOL_METRICS.free, CAPACITY - usedBytes, 'bytes', labels),
    metric(MEMORY_POOL_METRICS.capacity, CAPACITY, 'bytes', labels),
  ];
}

/**
 * 정상 상태(VM B)와 RX 처리가 밀려 점유·drop이 증가한 관측 예시(VM A).
 * 화면은 이 값을 실제 장애 판정이나 확정 임계값으로 사용하지 않는다.
 */
export function mockMemoryPoolSamples(vmId: string, now = Date.now()): MetricSample[] {
  const delayed = vmId === VM_IDS.autonomous;
  let rxPackets = 20_000;
  let txPackets = 15_000;
  let rxDrop = delayed ? 12 : 2;

  return Array.from({ length: SAMPLES }, (_, index) => {
    const delayStep = Math.max(0, index - 14);
    const rxUsed = delayed ? Math.min(7 * 1024, 512 + delayStep * 512) : index % 6 === 0 ? 512 : 256;
    const txUsed = delayed ? (index % 7 === 0 ? 1280 : 1024) : index % 8 === 0 ? 768 : 512;
    const rxIncrement = delayed && delayStep > 0 ? 50 : delayed ? 170 : 130 + (index % 4) * 8;
    const txIncrement = delayed ? 96 + (index % 3) * 4 : 100 + (index % 5) * 5;
    rxPackets += rxIncrement;
    txPackets += txIncrement;
    if (delayed && index === SAMPLES - 1) rxDrop += 6;

    const observedAt = new Date(now - (SAMPLES - 1 - index) * INTERVAL_MS).toISOString();
    const labels = { interface: 'netfront' };
    return {
      sample_id: `memory-${vmId}-${index}-${now}`,
      vm_id: vmId,
      boot_id: `mock-boot-${vmId}`,
      source: 'zephyr_telemetry',
      observed_at: observedAt,
      received_at: observedAt,
      window_ms: INTERVAL_MS,
      values: [
        ...poolValues('net_rx_data', rxUsed),
        ...poolValues('net_tx_data', txUsed),
        metric(MEMORY_POOL_METRICS.rxPackets, rxPackets, 'packets', labels),
        metric(MEMORY_POOL_METRICS.txPackets, txPackets, 'packets', labels),
        metric(MEMORY_POOL_METRICS.rxDrop, rxDrop, 'count', labels),
        metric(MEMORY_POOL_METRICS.txDrop, 0, 'count', labels),
        metric(MEMORY_POOL_METRICS.poolBusy, 0, 'count', labels),
        metric(MEMORY_POOL_METRICS.protocolErrors, 0, 'count', labels),
      ],
    };
  });
}
