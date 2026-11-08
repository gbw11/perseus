import { describe, expect, it } from 'vitest';
import type { MetricSample } from '../api/types';
import { memoryFixture } from './memoryContractFixtures';
import { sharedMemoryObservation, sharedObservedAt } from './sharedMemoryObservation';

const NOW = Date.parse('2026-11-04T10:00:00Z');
const HEAP = ['memory.kernel_heap.used_bytes', 'memory.kernel_heap.free_bytes'];
const POOL = ['memory.region.used_bytes', 'memory.region.free_bytes', 'memory.region.capacity_bytes'];
function pool(): MetricSample {
  const sample = memoryFixture('ram-committed', NOW);
  sample.telemetry!.family = 'memory_regions';
  sample.values = ['net_rx_data', 'net_tx_data'].flatMap((region) =>
    [256, 768, 1024].map((value, index) => ({
      name: POOL[index]!,
      value,
      raw_value: String(value),
      status: 'ok',
      source_rc: 0,
      unavailable_reason: null,
      unit: 'bytes',
      labels: { region },
    })),
  );
  return sample;
}

describe('shared heap and network pool observation clocks', () => {
  it('rejects a larger source sequence with reversed observation uptime and later recovers', () => {
    const first = pool(),
      reversed = pool();
    reversed.received_at = new Date(NOW + 1000).toISOString();
    Object.assign(reversed.telemetry!, { source_sample_seq: '2', observed_uptime_ms: '9000' });
    expect(
      sharedMemoryObservation([first, reversed], 'memory_regions', POOL, NOW + 1000, 'net_rx_data').state,
    ).toBe('unavailable');
    const next = pool();
    next.received_at = new Date(NOW + 2000).toISOString();
    Object.assign(next.telemetry!, { source_sample_seq: '3', observed_uptime_ms: '9500' });
    expect(
      sharedMemoryObservation([first, reversed, next], 'memory_regions', POOL, NOW + 2000, 'net_rx_data')
        .state,
    ).toBe('unavailable');
    const recovered = pool();
    recovered.received_at = new Date(NOW + 3000).toISOString();
    Object.assign(recovered.telemetry!, {
      source_sample_seq: '4',
      observed_uptime_ms: '10001',
      sent_uptime_ms: '10003',
    });
    expect(
      sharedMemoryObservation(
        [first, reversed, next, recovered],
        'memory_regions',
        POOL,
        NOW + 3000,
        'net_rx_data',
      ).state,
    ).toBe('fresh');
  });

  it('rejects future UTC observations and recovers with the next valid source', () => {
    const future = pool();
    Object.assign(future.telemetry!, {
      utc_quality: 'synchronized',
      sent_at: new Date(NOW + 10000).toISOString(),
    });
    expect(sharedMemoryObservation([future], 'memory_regions', POOL, NOW, 'net_rx_data').state).toBe(
      'unverified',
    );
    const next = pool();
    next.received_at = new Date(NOW + 1000).toISOString();
    next.telemetry!.source_sample_seq = '2';
    expect(
      sharedMemoryObservation([future, next], 'memory_regions', POOL, NOW + 1000, 'net_rx_data').state,
    ).toBe('fresh');
  });

  it('does not hide conflicting values under the same source and recovers with a new source', () => {
    const first = pool(),
      changed = structuredClone(first),
      replay = structuredClone(first);
    changed.received_at = new Date(NOW + 1000).toISOString();
    Object.assign(changed.values[0]!, { value: 128, raw_value: '128' });
    Object.assign(changed.values[1]!, { value: 896, raw_value: '896' });
    replay.received_at = new Date(NOW + 2000).toISOString();
    expect(
      sharedMemoryObservation([first, changed, replay], 'memory_regions', POOL, NOW + 2000, 'net_rx_data')
        .state,
    ).toBe('unavailable');
    const next = pool();
    next.received_at = new Date(NOW + 3000).toISOString();
    next.telemetry!.source_sample_seq = '2';
    expect(
      sharedMemoryObservation(
        [first, changed, replay, next],
        'memory_regions',
        POOL,
        NOW + 3000,
        'net_rx_data',
      ).state,
    ).toBe('fresh');
  });

  it('does not revive the old heap after a different memory family reports a new boot', () => {
    const heap = memoryFixture('ram-committed', NOW),
      next = pool();
    next.boot_id = 'new-boot';
    next.received_at = new Date(NOW + 1000).toISOString();
    const old = structuredClone(heap);
    old.received_at = new Date(NOW + 2000).toISOString();
    expect(sharedMemoryObservation([heap, next, old], 'memory', HEAP, NOW + 2000).state).toBe('unverified');
  });

  it('uses the direct heap collection time even in formal legacy memory without a source pair', () => {
    const legacy = memoryFixture('ram-legacy-unidentified', NOW);
    expect(legacy.telemetry!.source_sample_seq).toBeNull();
    expect(sharedMemoryObservation([legacy], 'memory', HEAP, NOW).state).toBe('fresh');
    const old = { ...legacy, telemetry: undefined };
    expect(sharedMemoryObservation([old], 'memory', HEAP, NOW).state).toBe('unverified');
  });

  it('does not refresh stale pool or heap with an unrelated family', () => {
    const original = pool();
    const network = memoryFixture('ram-committed', NOW + 7000);
    network.telemetry!.family = 'network';
    network.values = [
      {
        name: 'network.netfront.rx_packets',
        value: 10,
        unit: 'packets',
        labels: {},
        unavailable_reason: null,
      },
    ];
    expect(
      sharedMemoryObservation([original, network], 'memory_regions', POOL, NOW + 7000, 'net_rx_data').state,
    ).toBe('expired');
    const heap = memoryFixture('ram-committed', NOW);
    expect(sharedMemoryObservation([heap, network], 'memory', HEAP, NOW + 7000).state).toBe('expired');
  });

  it('keeps a repeated pool source at its first receipt and lets a new heap collection refresh independently', () => {
    const first = pool(),
      repeated = structuredClone(first);
    repeated.received_at = new Date(NOW + 5000).toISOString();
    expect(
      sharedMemoryObservation([first, repeated], 'memory_regions', POOL, NOW + 6100, 'net_rx_data').state,
    ).toBe('expired');
    const heap = memoryFixture('ram-committed', NOW),
      newHeap = structuredClone(heap);
    newHeap.received_at = new Date(NOW + 5000).toISOString();
    Object.assign(newHeap.telemetry!.collection, {
      sample_id: 'new-heap-collection',
      start_uptime_ms: '14999',
      end_uptime_ms: '15000',
    });
    newHeap.telemetry!.sent_uptime_ms = '15002';
    expect(sharedMemoryObservation([heap, newHeap], 'memory', HEAP, NOW + 6100).state).toBe('fresh');
  });

  it('keeps RX failure independent of valid TX and does not turn an owner error into zero', () => {
    const report = pool();
    report.values
      .filter((v) => v.labels?.region === 'net_rx_data')
      .forEach((v) =>
        Object.assign(v, {
          value: null,
          raw_value: null,
          status: 'collection_failed',
          unavailable_reason: 'owner_busy',
          source_rc: -134,
        }),
      );
    expect(sharedMemoryObservation([report], 'memory_regions', POOL, NOW, 'net_rx_data').state).toBe(
      'unavailable',
    );
    expect(sharedMemoryObservation([report], 'memory_regions', POOL, NOW, 'net_tx_data').state).toBe('fresh');
  });

  it('keeps only a valid recent expired observation for display without renewing freshness', () => {
    const report = pool();
    expect(
      sharedMemoryObservation([report], 'memory_regions', POOL, NOW + 7000, 'net_rx_data'),
    ).toMatchObject({
      state: 'expired',
      sample: report,
    });
    expect(
      sharedMemoryObservation([report], 'memory_regions', POOL, NOW + 61_000, 'net_rx_data'),
    ).toMatchObject({
      state: 'expired',
      sample: null,
    });
    expect(sharedMemoryObservation([report], 'memory_regions', POOL, NOW - 1, 'net_rx_data')).toMatchObject({
      state: 'unverified',
      sample: null,
    });
  });

  it('uses original uptime and rejects conflicting context on the same source', () => {
    const report = pool();
    report.telemetry!.sent_at = new Date(NOW).toISOString();
    report.telemetry!.utc_quality = 'synchronized';
    expect(sharedObservedAt(report, 'memory_regions')).toBe(new Date(NOW - 2).toISOString());
    const other = structuredClone(report);
    other.telemetry!.context.execution_id = 'new-execution';
    expect(sharedMemoryObservation([report, other], 'memory_regions', POOL, NOW, 'net_rx_data').state).toBe(
      'unavailable',
    );
  });

  it('does not turn an expired source interval into a fresh date or overflow the browser clock', () => {
    const report = pool();
    report.telemetry!.sent_uptime_ms = '18446744073709551615';
    expect(sharedObservedAt(report, 'memory_regions')).toBeNull();
    expect(sharedMemoryObservation([report], 'memory_regions', POOL, NOW, 'net_rx_data').state).not.toBe(
      'fresh',
    );
  });
});
