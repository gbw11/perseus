import { describe, expect, it } from 'vitest';
import type { MetricSample } from '../api/types';
import {
  buildLinuxMemorySeries,
  buildZephyrHeapSeries,
  cpuPercentOf,
  latest,
  memoryPercentOf,
} from './vmMetrics';

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
