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
export const LINUX_MEMORY_USED_METRIC = 'memory_used_bytes';

const MIB_BYTES = 1024 * 1024;

export interface LinuxMemoryPoint {
  observedAt: string;
  usedBytes: number;
  percent: number;
}

export interface LinuxMemorySeries {
  points: LinuxMemoryPoint[];
  current: LinuxMemoryPoint | null;
  totalBytes: number;
}

interface ZephyrHeapPoint extends LinuxMemoryPoint {
  freeBytes: number;
}

function measured(value: number | null): value is number {
  return value != null && Number.isFinite(value) && value >= 0;
}

function heapPoint(sample: MetricSample): ZephyrHeapPoint | null {
  const usedBytes = metricValue(sample, ZEPHYR_HEAP_USED_METRIC);
  const freeBytes = metricValue(sample, ZEPHYR_HEAP_FREE_METRIC);
  if (!measured(usedBytes) || !measured(freeBytes) || usedBytes + freeBytes <= 0) return null;
  return {
    observedAt: sample.observed_at,
    usedBytes,
    freeBytes,
    percent: (usedBytes / (usedBytes + freeBytes)) * 100,
  };
}

/** The heap capacity and usage come from the same allocator observation. */
export function buildZephyrHeapSeries(samples: readonly MetricSample[]) {
  const observations = samples
    .filter((sample) =>
      sample.values.some(
        (value) => value.name === ZEPHYR_HEAP_USED_METRIC || value.name === ZEPHYR_HEAP_FREE_METRIC,
      ),
    )
    .map(heapPoint);
  return {
    points: observations.filter((point): point is ZephyrHeapPoint => point != null),
    current: observations.at(-1) ?? null,
  };
}

export function metricValue(sample: MetricSample, name: string): number | null {
  return sample.values.find((value) => value.name === name)?.value ?? null;
}

/** VM 전체 CPU 사용률(%). 이 표본에 없으면 null */
export function cpuPercentOf(sample: MetricSample): number | null {
  return metricValue(sample, VM_CPU_PERCENT_METRIC) ?? metricValue(sample, ZEPHYR_CPU_PERCENT_METRIC);
}

/** 메모리 사용률(%). Linux 는 할당 메모리 대비, Zephyr 는 커널 heap(used+free) 대비 */
export function memoryPercentOf(sample: MetricSample, vm: Pick<Vm, 'memory_mib'>): number | null {
  const used = metricValue(sample, LINUX_MEMORY_USED_METRIC);
  if (used != null) return (used / (vm.memory_mib * MIB_BYTES)) * 100;
  return heapPoint(sample)?.percent ?? null;
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

/** Linux guest-agent 표본만 사용해 할당 메모리 대비 실제 사용량 시계열을 만든다. */
export function buildLinuxMemorySeries(
  samples: readonly MetricSample[],
  vm: Pick<Vm, 'memory_mib'>,
): LinuxMemorySeries {
  const totalBytes = vm.memory_mib * MIB_BYTES;
  const points =
    totalBytes > 0
      ? samples.flatMap((sample) => {
          const usedBytes = metricValue(sample, LINUX_MEMORY_USED_METRIC);
          if (usedBytes == null || !Number.isFinite(usedBytes) || usedBytes < 0) return [];
          return [{ observedAt: sample.observed_at, usedBytes, percent: (usedBytes / totalBytes) * 100 }];
        })
      : [];

  return { points, current: points.at(-1) ?? null, totalBytes };
}
