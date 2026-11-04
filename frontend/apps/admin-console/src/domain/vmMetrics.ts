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
export const ZEPHYR_HEAP_HIGH_WATER_METRIC = 'memory.kernel_heap.high_water_bytes';
export const ZEPHYR_HEAP_ACCOUNTED_METRIC = 'memory.kernel_heap.accounted_bytes';
export const LINUX_MEMORY_USED_METRIC = 'memory_used_bytes';
export const LINUX_MEMORY_AVAILABLE_METRIC = 'memory_available_bytes';
export const LINUX_DISK_READ_METRIC = 'disk_read_bytes_total';
export const LINUX_DISK_WRITE_METRIC = 'disk_write_bytes_total';

const MIB_BYTES = 1024 * 1024;
const NO_DATA = '데이터 없음';

export interface LinuxMemoryPoint {
  observedAt: string;
  usedBytes: number;
  percent: number;
  /** 같은 표본의 MemAvailable. 없으면 null */
  availableBytes: number | null;
  /** availableBytes 가 없을 때의 사유 (unavailable_reason 또는 '데이터 없음') */
  availableReason: string | null;
}

export interface LinuxMemorySeries {
  points: LinuxMemoryPoint[];
  current: LinuxMemoryPoint | null;
  totalBytes: number;
}

export interface LinuxFreeMemory {
  bytes: number | null;
  /** available: MemAvailable 기준, estimated: 할당량 − 사용량 */
  basis: 'available' | 'estimated' | null;
  /** estimated 일 때 MemAvailable 을 못 쓴 사유 */
  reason: string | null;
}

export interface ZephyrHeapPoint {
  observedAt: string;
  usedBytes: number;
  freeBytes: number;
  totalBytes: number;
  /** accounted_bytes 를 쓰면 accounted, 없으면 used+free */
  totalBasis: 'accounted' | 'used_plus_free';
  percent: number;
  highWaterBytes: number | null;
}

export interface ZephyrHeapSeries {
  points: ZephyrHeapPoint[];
  current: ZephyrHeapPoint | null;
  /** current 가 없을 때 사유 */
  reason: string | null;
}

export interface DiskIoCounter {
  totalBytes: number | null;
  bytesPerSecond: number | null;
  /** totalBytes 가 없을 때 사유 */
  reason: string | null;
}

export interface DiskIoSnapshot {
  observedAt: string | null;
  read: DiskIoCounter;
  write: DiskIoCounter;
}

export function metricValue(sample: MetricSample, name: string): number | null {
  return sample.values.find((value) => value.name === name)?.value ?? null;
}

/** 유효한(유한·0 이상) 값만 꺼낸다 */
function validValue(sample: MetricSample, name: string): number | null {
  const value = metricValue(sample, name);
  return value != null && Number.isFinite(value) && value >= 0 ? value : null;
}

/** 값이 없을 때 표시할 사유. 지표가 표본에 없으면 null */
function reasonOf(sample: MetricSample, name: string): string | null {
  const metric = sample.values.find((value) => value.name === name);
  if (!metric) return null;
  return metric.unavailable_reason || NO_DATA;
}

/** VM 전체 CPU 사용률(%). 이 표본에 없으면 null */
export function cpuPercentOf(sample: MetricSample): number | null {
  return metricValue(sample, VM_CPU_PERCENT_METRIC) ?? metricValue(sample, ZEPHYR_CPU_PERCENT_METRIC);
}

/** 메모리 사용률(%). Linux 는 할당 메모리 대비, Zephyr 는 커널 heap(used+free) 대비 */
export function memoryPercentOf(sample: MetricSample, vm: Pick<Vm, 'memory_mib'>): number | null {
  const used = metricValue(sample, LINUX_MEMORY_USED_METRIC);
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
          const availableBytes = validValue(sample, LINUX_MEMORY_AVAILABLE_METRIC);
          return [
            {
              observedAt: sample.observed_at,
              usedBytes,
              percent: (usedBytes / totalBytes) * 100,
              availableBytes,
              availableReason:
                availableBytes == null ? (reasonOf(sample, LINUX_MEMORY_AVAILABLE_METRIC) ?? NO_DATA) : null,
            },
          ];
        })
      : [];

  return { points, current: points.at(-1) ?? null, totalBytes };
}

/** Linux 여유 메모리. MemAvailable 이 있으면 그 값을, 없으면 할당량 − 사용량을 사유와 함께 쓴다 */
export function linuxFreeMemory(series: LinuxMemorySeries): LinuxFreeMemory {
  const current = series.current;
  if (!current) return { bytes: null, basis: null, reason: null };
  if (current.availableBytes != null) {
    return { bytes: current.availableBytes, basis: 'available', reason: null };
  }
  return {
    bytes: Math.max(0, series.totalBytes - current.usedBytes),
    basis: 'estimated',
    reason: current.availableReason,
  };
}

/**
 * Zephyr 커널 heap 사용량 시계열. VM 전체 RAM 이 아니라 kernel system heap 기준이다.
 * 전체는 accounted_bytes 를 우선하고, 없거나 사용량보다 작으면 used+free 를 쓴다.
 */
export function buildZephyrHeapSeries(samples: readonly MetricSample[]): ZephyrHeapSeries {
  const ordered = [...samples].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  let reason: string | null = null;
  const points = ordered.flatMap((sample): ZephyrHeapPoint[] => {
    if (!sample.values.some((value) => value.name.startsWith('memory.kernel_heap.'))) return [];
    const usedBytes = validValue(sample, ZEPHYR_HEAP_USED_METRIC);
    const freeBytes = validValue(sample, ZEPHYR_HEAP_FREE_METRIC);
    if (usedBytes == null || freeBytes == null) {
      reason =
        reasonOf(sample, ZEPHYR_HEAP_USED_METRIC) ?? reasonOf(sample, ZEPHYR_HEAP_FREE_METRIC) ?? NO_DATA;
      return [];
    }
    const accounted = validValue(sample, ZEPHYR_HEAP_ACCOUNTED_METRIC);
    const useAccounted = accounted != null && accounted > 0 && accounted >= usedBytes;
    const totalBytes = useAccounted ? accounted : usedBytes + freeBytes;
    if (totalBytes <= 0) {
      reason = NO_DATA;
      return [];
    }
    reason = null;
    return [
      {
        observedAt: sample.observed_at,
        usedBytes,
        freeBytes,
        totalBytes,
        totalBasis: useAccounted ? 'accounted' : 'used_plus_free',
        percent: (usedBytes / totalBytes) * 100,
        highWaterBytes: validValue(sample, ZEPHYR_HEAP_HIGH_WATER_METRIC),
      },
    ];
  });
  const current = points.at(-1) ?? null;
  return { points, current, reason: current ? null : reason };
}

interface DiskTotal {
  bytes: number | null;
  devices: string;
  reason: string | null;
}

interface DiskObservation {
  observedAt: string;
  bootId: string | null;
  read: DiskTotal;
  write: DiskTotal;
}

/**
 * Linux 디스크 I/O. 장치별(labels.device) 누적 바이트를 합산하고,
 * 속도는 같은 boot_id 이고 장치 묶음이 같은 직전 표본과의 차이로만 구한다 (카운터 감소면 null).
 */
export function buildDiskIoSnapshot(samples: readonly MetricSample[]): DiskIoSnapshot {
  const observations = [...samples]
    .sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at))
    .filter((sample) =>
      sample.values.some(
        (value) => value.name === LINUX_DISK_READ_METRIC || value.name === LINUX_DISK_WRITE_METRIC,
      ),
    )
    .map((sample): DiskObservation => ({
      observedAt: sample.observed_at,
      bootId: sample.boot_id ?? null,
      read: diskTotal(sample, LINUX_DISK_READ_METRIC),
      write: diskTotal(sample, LINUX_DISK_WRITE_METRIC),
    }));

  const current = observations.at(-1);
  if (!current) {
    const missing = { totalBytes: null, bytesPerSecond: null, reason: NO_DATA };
    return { observedAt: null, read: missing, write: missing };
  }
  const previous = observations.at(-2);
  const seconds = previous ? (Date.parse(current.observedAt) - Date.parse(previous.observedAt)) / 1000 : NaN;
  const sameBoot = !!previous && current.bootId != null && current.bootId === previous.bootId;

  const counter = (pick: (observation: DiskObservation) => DiskTotal): DiskIoCounter => {
    const now = pick(current);
    const before = previous ? pick(previous) : null;
    let bytesPerSecond: number | null = null;
    if (
      sameBoot &&
      before &&
      now.bytes != null &&
      before.bytes != null &&
      now.devices === before.devices &&
      Number.isFinite(seconds) &&
      seconds > 0 &&
      now.bytes >= before.bytes
    ) {
      bytesPerSecond = (now.bytes - before.bytes) / seconds;
    }
    return { totalBytes: now.bytes, bytesPerSecond, reason: now.bytes == null ? now.reason : null };
  };

  return {
    observedAt: current.observedAt,
    read: counter((observation) => observation.read),
    write: counter((observation) => observation.write),
  };
}

function diskTotal(sample: MetricSample, name: string): DiskTotal {
  const metrics = sample.values.filter((value) => value.name === name);
  if (metrics.length === 0) return { bytes: null, devices: '', reason: NO_DATA };
  const invalid = metrics.find(
    (metric) => metric.value == null || !Number.isFinite(metric.value) || metric.value < 0,
  );
  if (invalid) return { bytes: null, devices: '', reason: invalid.unavailable_reason || NO_DATA };
  const devices = metrics
    .map((metric) => metric.labels?.device ?? '')
    .sort()
    .join(',');
  return { bytes: metrics.reduce((sum, metric) => sum + metric.value!, 0), devices, reason: null };
}
