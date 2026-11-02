/**
 * VM 메트릭 표본에서 CPU·메모리 사용률을 꺼낸다.
 * Linux 는 게스트 에이전트(source guest_agent), Zephyr 는 Telemetry Publisher(source zephyr_telemetry)가 보내며
 * 이름이 다르다. Zephyr 는 한 표본에 family 하나(cpu, memory, memory_regions …)만 실으므로
 * 마지막 표본이 아니라 그 값을 가진 가장 최근 표본을 본다.
 */
import { VM_CPU_PERCENT_METRIC, type MetricSample, type Vm } from '../api/types';

/** Zephyr Telemetry 계약 2.1 metric catalog */
export const ZEPHYR_CPU_PERCENT_METRIC = 'cpu.system.percent';
export const ZEPHYR_HEAP_USED_METRIC = 'memory.kernel_heap.used_bytes';
export const ZEPHYR_HEAP_FREE_METRIC = 'memory.kernel_heap.free_bytes';

const MIB_BYTES = 1024 * 1024;

export function metricValue(sample: MetricSample, name: string): number | null {
  return sample.values.find((value) => value.name === name)?.value ?? null;
}

/** VM 전체 CPU 사용률(%). 이 표본에 없으면 null */
export function cpuPercentOf(sample: MetricSample): number | null {
  return metricValue(sample, VM_CPU_PERCENT_METRIC) ?? metricValue(sample, ZEPHYR_CPU_PERCENT_METRIC);
}

/** 메모리 사용률(%). Linux 는 할당 메모리 대비, Zephyr 는 커널 heap(used+free) 대비 */
export function memoryPercentOf(sample: MetricSample, vm: Pick<Vm, 'memory_mib'>): number | null {
  const used = metricValue(sample, 'memory_used_bytes');
  if (used != null) return (used / (vm.memory_mib * MIB_BYTES)) * 100;
  const heapUsed = metricValue(sample, ZEPHYR_HEAP_USED_METRIC);
  const heapFree = metricValue(sample, ZEPHYR_HEAP_FREE_METRIC);
  if (heapUsed == null || heapFree == null || heapUsed + heapFree <= 0) return null;
  return (heapUsed / (heapUsed + heapFree)) * 100;
}

/** pick 이 값을 내는 가장 최근 표본의 값 */
export function latest(
  samples: MetricSample[],
  pick: (sample: MetricSample) => number | null,
): number | null {
  for (let i = samples.length - 1; i >= 0; i -= 1) {
    const value = pick(samples[i]!);
    if (value != null) return value;
  }
  return null;
}
