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

/** 메모리 사용률(%). Linux 는 할당 메모리, Zephyr 는 커널 heap(accounted 또는 used+free) 대비 */
export function memoryPercentOf(sample: MetricSample, vm: Pick<Vm, 'memory_mib'>): number | null {
  const used = metricValue(sample, LINUX_MEMORY_USED_METRIC);
  if (used != null) return (used / (vm.memory_mib * MIB_BYTES)) * 100;
  return buildZephyrHeapSeries([sample]).current?.percent ?? null;
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
  let current: ZephyrHeapPoint | null = null;
  const points = ordered.flatMap((sample): ZephyrHeapPoint[] => {
    if (!sample.values.some((value) => value.name.startsWith('memory.kernel_heap.'))) return [];
    current = null;
    const usedBytes = validValue(sample, ZEPHYR_HEAP_USED_METRIC);
    const freeBytes = validValue(sample, ZEPHYR_HEAP_FREE_METRIC);
    if (usedBytes == null || freeBytes == null) {
      reason =
        (usedBytes == null ? reasonOf(sample, ZEPHYR_HEAP_USED_METRIC) : null) ??
        (freeBytes == null ? reasonOf(sample, ZEPHYR_HEAP_FREE_METRIC) : null) ??
        NO_DATA;
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
    current = {
      observedAt: sample.observed_at,
      usedBytes,
      freeBytes,
      totalBytes,
      totalBasis: useAccounted ? 'accounted' : 'used_plus_free',
      percent: (usedBytes / totalBytes) * 100,
      highWaterBytes: validValue(sample, ZEPHYR_HEAP_HIGH_WATER_METRIC),
    };
    return [current];
  });
  return { points, current, reason: current ? null : reason };
}

interface LabeledTotal {
  bytes: number | null;
  /** 합산한 라벨 값 묶음 (예: "xvda,xvdb"). 묶음이 바뀌면 차분하지 않는다 */
  members: string;
  reason: string | null;
}

interface LabeledObservation {
  observedAt: string;
  bootId: string | null;
  first: LabeledTotal;
  second: LabeledTotal;
}

/** 두 누적 카운터(읽기/쓰기, 수신/송신)의 현재 값·속도와 표본 간 속도 시계열 */
export interface LabeledCounterSeries {
  observedAt: string | null;
  first: DiskIoCounter;
  second: DiskIoCounter;
  rates: { observedAt: string; first: number | null; second: number | null }[];
}

/**
 * 라벨별(labels[labelKey]) 누적 바이트를 합산하고, 속도는 같은 boot_id 이고 라벨 묶음이 같으며
 * 카운터가 줄지 않은 직전 표본과의 차이로만 구한다. Linux 디스크(device)·네트워크(iface)가 함께 쓴다.
 */
export function buildLabeledCounterSeries(
  samples: readonly MetricSample[],
  names: readonly [string, string],
  labelKey: string,
): LabeledCounterSeries {
  const observations = [...samples]
    .sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at))
    .filter((sample) => sample.values.some((value) => value.name === names[0] || value.name === names[1]))
    .map((sample): LabeledObservation => ({
      observedAt: sample.observed_at,
      bootId: sample.boot_id ?? null,
      first: labeledTotal(sample, names[0], labelKey),
      second: labeledTotal(sample, names[1], labelKey),
    }));

  const current = observations.at(-1);
  if (!current) {
    const missing = { totalBytes: null, bytesPerSecond: null, reason: NO_DATA };
    return { observedAt: null, first: missing, second: missing, rates: [] };
  }

  const rates = observations.slice(1).map((now, index) => {
    const before = observations[index]!;
    return {
      observedAt: now.observedAt,
      first: labeledRate(before, now, (observation) => observation.first),
      second: labeledRate(before, now, (observation) => observation.second),
    };
  });
  const latest = rates.at(-1);
  const counter = (total: LabeledTotal, rate: number | null | undefined): DiskIoCounter => ({
    totalBytes: total.bytes,
    bytesPerSecond: rate ?? null,
    reason: total.bytes == null ? total.reason : null,
  });

  return {
    observedAt: current.observedAt,
    first: counter(current.first, latest?.first),
    second: counter(current.second, latest?.second),
    rates,
  };
}

/** Linux 디스크 I/O. 장치별(labels.device) 합산 */
export function buildDiskIoSnapshot(samples: readonly MetricSample[]): DiskIoSnapshot {
  const series = buildLabeledCounterSeries(
    samples,
    [LINUX_DISK_READ_METRIC, LINUX_DISK_WRITE_METRIC],
    'device',
  );
  return { observedAt: series.observedAt, read: series.first, write: series.second };
}

function labeledRate(
  before: LabeledObservation,
  now: LabeledObservation,
  pick: (observation: LabeledObservation) => LabeledTotal,
): number | null {
  const a = pick(before);
  const b = pick(now);
  const seconds = (Date.parse(now.observedAt) - Date.parse(before.observedAt)) / 1000;
  if (
    now.bootId == null ||
    now.bootId !== before.bootId ||
    a.bytes == null ||
    b.bytes == null ||
    a.members !== b.members ||
    !Number.isFinite(seconds) ||
    seconds <= 0 ||
    b.bytes < a.bytes
  ) {
    return null;
  }
  return (b.bytes - a.bytes) / seconds;
}

function labeledTotal(sample: MetricSample, name: string, labelKey: string): LabeledTotal {
  const metrics = sample.values.filter((value) => value.name === name);
  if (metrics.length === 0) return { bytes: null, members: '', reason: NO_DATA };
  const invalid = metrics.find(
    (metric) => metric.value == null || !Number.isFinite(metric.value) || metric.value < 0,
  );
  if (invalid) return { bytes: null, members: '', reason: invalid.unavailable_reason || NO_DATA };
  const members = metrics
    .map((metric) => metric.labels?.[labelKey] ?? '')
    .sort()
    .join(',');
  return { bytes: metrics.reduce((sum, metric) => sum + metric.value!, 0), members, reason: null };
}
