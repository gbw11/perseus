import { describe, expect, it } from 'vitest';
import type { MetricSample, MetricValue } from '../api/types';
import {
  buildDiskIoSnapshot,
  buildLinuxMemorySeries,
  buildZephyrHeapSeries,
  cpuPercentOf,
  latest,
  linuxFreeMemory,
  memoryPercentOf,
} from './vmMetrics';

const MIB = 1024 * 1024;

const raw = (
  observedAt: string,
  values: Partial<MetricValue>[],
  bootId: string | null = 'boot-1',
): MetricSample => ({
  sample_id: crypto.randomUUID(),
  vm_id: 'vm',
  boot_id: bootId,
  source: 'guest_agent',
  observed_at: observedAt,
  values: values.map((value) => ({
    name: '',
    value: null,
    unit: 'bytes',
    labels: null,
    unavailable_reason: null,
    ...value,
  })),
});

const disk = (read: [string, number | null][], write: [string, number | null][], reason?: string) => [
  ...read.map(([device, value]) => ({
    name: 'disk_read_bytes_total',
    value,
    labels: { device },
    unavailable_reason: value == null ? (reason ?? null) : null,
  })),
  ...write.map(([device, value]) => ({
    name: 'disk_write_bytes_total',
    value,
    labels: { device },
    unavailable_reason: value == null ? (reason ?? null) : null,
  })),
];

const sample = (source: string, values: Record<string, number | null>): MetricSample => ({
  sample_id: crypto.randomUUID(),
  vm_id: 'vm',
  source,
  observed_at: '2026-11-02T03:00:00Z',
  values: Object.entries(values).map(([name, value]) => ({
    name,
    value,
    unit: null,
    labels: null,
    unavailable_reason: null,
  })),
});

describe('vm metrics', () => {
  it('reads Linux agent CPU and memory against allocated memory', () => {
    const linux = sample('guest_agent', { cpu_percent_allocated: 12, memory_used_bytes: 256 * 1024 * 1024 });
    expect(cpuPercentOf(linux)).toBe(12);
    expect(memoryPercentOf(linux, { memory_mib: 512 })).toBe(50);
  });
  it('reads Zephyr CPU and kernel heap usage', () => {
    const cpu = sample('zephyr_telemetry', { 'cpu.system.percent': 25.25 });
    const heap = sample('zephyr_telemetry', {
      'memory.kernel_heap.used_bytes': 3072,
      'memory.kernel_heap.free_bytes': 1024,
    });
    expect(cpuPercentOf(cpu)).toBe(25.25);
    expect(memoryPercentOf(heap, { memory_mib: 16 })).toBe(75);
    expect(memoryPercentOf(cpu, { memory_mib: 16 })).toBeNull();
  });
  it('takes the latest sample that carries the value, across Zephyr families', () => {
    const samples = [
      sample('zephyr_telemetry', { 'cpu.system.percent': 10 }),
      sample('zephyr_telemetry', { 'cpu.system.percent': 30 }),
      sample('zephyr_telemetry', { 'memory.region.used_bytes': 256 }),
    ];
    expect(latest(samples, cpuPercentOf)).toBe(30);
    expect(latest(samples, (s) => memoryPercentOf(s, { memory_mib: 16 }))).toBeNull();
  });
  it('keeps measured zero and does not fill an incomplete heap report with an older capacity', () => {
    const zero = sample('zephyr_telemetry', {
      'memory.kernel_heap.used_bytes': 0,
      'memory.kernel_heap.free_bytes': 1024,
    });
    const other = sample('zephyr_telemetry', { 'memory.region.used_bytes': 256 });
    const missing = sample('zephyr_telemetry', { 'memory.kernel_heap.used_bytes': 512 });
    expect(buildZephyrHeapSeries([zero, other]).current).toMatchObject({
      usedBytes: 0,
      freeBytes: 1024,
      percent: 0,
    });
    expect(buildZephyrHeapSeries([zero, other, missing])).toEqual({
      points: [expect.objectContaining({ usedBytes: 0, percent: 0 })],
      current: null,
      reason: '데이터 없음',
    });
  });
  it('builds Linux memory usage history from samples that carry memory data', () => {
    const samples = [
      sample('guest_agent', { memory_used_bytes: 128 * 1024 * 1024 }),
      sample('guest_agent', { cpu_percent_allocated: 8 }),
      sample('guest_agent', { memory_used_bytes: 256 * 1024 * 1024 }),
    ];

    const series = buildLinuxMemorySeries(samples, { memory_mib: 512 });

    expect(series.totalBytes).toBe(512 * 1024 * 1024);
    expect(series.points).toHaveLength(2);
    expect(series.points.map((point) => point.percent)).toEqual([25, 50]);
    expect(series.current?.usedBytes).toBe(256 * 1024 * 1024);
  });
  it('ignores invalid memory samples and an invalid VM allocation', () => {
    const samples = [
      sample('guest_agent', { memory_used_bytes: -1 }),
      sample('guest_agent', { memory_used_bytes: Number.POSITIVE_INFINITY }),
    ];

    expect(buildLinuxMemorySeries(samples, { memory_mib: 512 }).current).toBeNull();
    expect(
      buildLinuxMemorySeries([sample('guest_agent', { memory_used_bytes: 10 })], { memory_mib: 0 }).points,
    ).toEqual([]);
  });
});

describe('Linux 여유 메모리', () => {
  it('MemAvailable 이 있으면 그 값을 여유로 쓴다', () => {
    const series = buildLinuxMemorySeries(
      [sample('guest_agent', { memory_used_bytes: 200 * MIB, memory_available_bytes: 280 * MIB })],
      { memory_mib: 512 },
    );
    expect(linuxFreeMemory(series)).toEqual({ bytes: 280 * MIB, basis: 'available', reason: null });
  });

  it('MemAvailable 이 없으면 할당량 − 사용량을 쓰고 사유를 남긴다', () => {
    const series = buildLinuxMemorySeries(
      [
        raw('2026-11-04T01:00:00Z', [
          { name: 'memory_used_bytes', value: 200 * MIB },
          { name: 'memory_available_bytes', value: null, unavailable_reason: 'read_failed' },
        ]),
      ],
      { memory_mib: 512 },
    );
    expect(linuxFreeMemory(series)).toEqual({ bytes: 312 * MIB, basis: 'estimated', reason: 'read_failed' });

    const old = buildLinuxMemorySeries([sample('guest_agent', { memory_used_bytes: 100 * MIB })], {
      memory_mib: 512,
    });
    expect(linuxFreeMemory(old)).toEqual({ bytes: 412 * MIB, basis: 'estimated', reason: '데이터 없음' });
  });

  it('메모리 표본이 없으면 값을 만들지 않는다', () => {
    expect(linuxFreeMemory(buildLinuxMemorySeries([], { memory_mib: 512 }))).toEqual({
      bytes: null,
      basis: null,
      reason: null,
    });
  });
});

describe('Zephyr 커널 heap', () => {
  const heap = (observedAt: string, values: Record<string, number | null>, reason?: string) =>
    raw(
      observedAt,
      Object.entries(values).map(([name, value]) => ({
        name: `memory.kernel_heap.${name}`,
        value,
        unavailable_reason: value == null ? (reason ?? null) : null,
      })),
    );

  it('accounted_bytes 를 전체로 쓰고 high-water 를 함께 읽는다', () => {
    const series = buildZephyrHeapSeries([
      heap('2026-11-04T01:00:10Z', {
        used_bytes: 12_288,
        free_bytes: 20_000,
        accounted_bytes: 32_768,
        high_water_bytes: 16_384,
      }),
      heap('2026-11-04T01:00:00Z', { used_bytes: 8_192, free_bytes: 24_000, accounted_bytes: 32_768 }),
    ]);
    expect(series.points.map((point) => point.observedAt)).toEqual([
      '2026-11-04T01:00:00Z',
      '2026-11-04T01:00:10Z',
    ]);
    expect(series.current).toMatchObject({
      usedBytes: 12_288,
      freeBytes: 20_000,
      totalBytes: 32_768,
      totalBasis: 'accounted',
      percent: 37.5,
      highWaterBytes: 16_384,
    });
  });

  it('accounted_bytes 가 없거나 사용량보다 작으면 used+free 를 전체로 쓴다', () => {
    const missing = buildZephyrHeapSeries([
      heap('2026-11-04T01:00:00Z', { used_bytes: 3072, free_bytes: 1024 }),
    ]);
    expect(missing.current).toMatchObject({ totalBytes: 4096, totalBasis: 'used_plus_free', percent: 75 });

    const smaller = buildZephyrHeapSeries([
      heap('2026-11-04T01:00:00Z', { used_bytes: 3072, free_bytes: 1024, accounted_bytes: 2048 }),
    ]);
    expect(smaller.current?.totalBasis).toBe('used_plus_free');
  });

  it('값이 없으면 0 으로 채우지 않고 사유를 남기며, 다른 계열 표본은 무시한다', () => {
    const series = buildZephyrHeapSeries([
      sample('zephyr_telemetry', { 'cpu.system.percent': 12 }),
      heap('2026-11-04T01:00:00Z', { used_bytes: null, free_bytes: null }, 'source_unavailable'),
    ]);
    expect(series.current).toBeNull();
    expect(series.points).toEqual([]);
    expect(series.reason).toBe('source_unavailable');
  });
});

describe('Linux 디스크 I/O', () => {
  it('장치별 누적값을 합산하고 같은 부팅·장치 묶음이면 속도를 구한다', () => {
    const snapshot = buildDiskIoSnapshot([
      raw(
        '2026-11-04T01:00:00Z',
        disk(
          [
            ['xvda', 1000],
            ['xvdb', 500],
          ],
          [
            ['xvda', 200],
            ['xvdb', 0],
          ],
        ),
      ),
      raw(
        '2026-11-04T01:00:10Z',
        disk(
          [
            ['xvdb', 600],
            ['xvda', 3000],
          ],
          [
            ['xvda', 1200],
            ['xvdb', 0],
          ],
        ),
      ),
    ]);
    expect(snapshot.read).toEqual({ totalBytes: 3600, bytesPerSecond: 210, reason: null });
    expect(snapshot.write).toEqual({ totalBytes: 1200, bytesPerSecond: 100, reason: null });
  });

  it('부팅이 바뀌었거나 카운터가 줄었거나 장치 묶음이 다르면 속도를 만들지 않는다', () => {
    const rebooted = buildDiskIoSnapshot([
      raw('2026-11-04T01:00:00Z', disk([['xvda', 9000]], [['xvda', 9000]]), 'boot-1'),
      raw('2026-11-04T01:00:10Z', disk([['xvda', 100]], [['xvda', 100]]), 'boot-2'),
    ]);
    expect(rebooted.read).toEqual({ totalBytes: 100, bytesPerSecond: null, reason: null });

    const decreased = buildDiskIoSnapshot([
      raw('2026-11-04T01:00:00Z', disk([['xvda', 9000]], [['xvda', 10]])),
      raw('2026-11-04T01:00:10Z', disk([['xvda', 100]], [['xvda', 20]])),
    ]);
    expect(decreased.read.bytesPerSecond).toBeNull();
    expect(decreased.write.bytesPerSecond).toBe(1);

    const deviceAdded = buildDiskIoSnapshot([
      raw('2026-11-04T01:00:00Z', disk([['xvda', 1000]], [['xvda', 10]])),
      raw(
        '2026-11-04T01:00:10Z',
        disk(
          [
            ['xvda', 1000],
            ['xvdb', 50],
          ],
          [
            ['xvda', 10],
            ['xvdb', 0],
          ],
        ),
      ),
    ]);
    expect(deviceAdded.read).toEqual({ totalBytes: 1050, bytesPerSecond: null, reason: null });

    const noBoot = buildDiskIoSnapshot([
      raw('2026-11-04T01:00:00Z', disk([['xvda', 1000]], [['xvda', 10]]), null),
      raw('2026-11-04T01:00:10Z', disk([['xvda', 2000]], [['xvda', 20]]), null),
    ]);
    expect(noBoot.read.bytesPerSecond).toBeNull();
  });

  it('null·read_failed 면 사유를 표시하고, 표본이 없으면 데이터 없음이다', () => {
    const failed = buildDiskIoSnapshot([
      raw('2026-11-04T01:00:00Z', disk([['xvda', null]], [['xvda', null]], 'read_failed')),
    ]);
    expect(failed.read).toEqual({ totalBytes: null, bytesPerSecond: null, reason: 'read_failed' });
    expect(failed.write.reason).toBe('read_failed');

    const empty = buildDiskIoSnapshot([sample('guest_agent', { memory_used_bytes: 1 })]);
    expect(empty).toEqual({
      observedAt: null,
      read: { totalBytes: null, bytesPerSecond: null, reason: '데이터 없음' },
      write: { totalBytes: null, bytesPerSecond: null, reason: '데이터 없음' },
    });
  });
});
