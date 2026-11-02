import { describe, expect, it } from 'vitest';
import type { MetricSample } from '../api/types';
import { cpuPercentOf, latest, memoryPercentOf } from './vmMetrics';

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
});
