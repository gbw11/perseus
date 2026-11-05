import { describe, expect, it } from 'vitest';
import type { MetricSample, MetricValue } from '../api/types';
import { buildAppMemorySeries, formatAppBytes, type AppMemoryRegion } from './appMemory';

const NOW = Date.parse('2026-11-04T10:00:00Z');
const names = [
  'reserved_bytes',
  'used_bytes',
  'free_bytes',
  'capacity_bytes',
  'high_water_bytes',
  'min_free_bytes',
  'capacity_failure_total',
  'contention_total',
];
function sample(seq: number, used = 0n, peak = used, region: AppMemoryRegion = 'drive_maps'): MetricSample {
  const capacity = 8192n;
  const amounts = [12288n, used, capacity - used, capacity, peak, capacity - peak, 0n, 0n];
  return {
    sample_id: `sample-${seq}-${region}`,
    vm_id: 'vm-a',
    boot_id: 'boot-a',
    source: 'zephyr_telemetry',
    observed_at: new Date(NOW - (10 - seq) * 1000).toISOString(),
    received_at: new Date(NOW - (10 - seq) * 1000).toISOString(),
    telemetry: {
      family: 'app_memory',
      publisher_instance_id: 'publisher-a',
      source_sample_seq: String(seq),
      observed_uptime_ms: String(seq * 1000 - 100),
      sent_uptime_ms: String(seq * 1000),
      sent_at: null,
      utc_quality: 'unavailable',
      utc_source: null,
      uncertainty_ms: null,
      collection: {
        sample_id: `collection-${seq}`,
        start_uptime_ms: String(seq * 1000 - 50),
        end_uptime_ms: String(seq * 1000),
        elapsed_us: '50',
        status: 'ok',
        source_rc: 0,
      },
      context: { binding: 'bound', host_boot_id: 'host-a', execution_id: 'exec-a', receipt_id: 'receipt-a' },
    },
    values: names.map((field, index): MetricValue => ({
      name: `memory.app.${field}`,
      value: Number(amounts[index]),
      raw_value: amounts[index]!.toString(),
      unit: field.endsWith('_total') ? 'count' : 'bytes',
      labels: { region },
      unavailable_reason: null,
      status: 'ok',
      source_rc: 0,
    })),
  };
}
function busy(seq: number): MetricSample {
  const report = sample(seq);
  report.values = report.values.map((value) => ({
    ...value,
    value: null,
    raw_value: null,
    status: 'collection_failed',
    unavailable_reason: 'owner_busy',
    source_rc: -16,
  }));
  return report;
}
const maps = (reports: MetricSample[]) => buildAppMemorySeries(reports, 'vm-a', NOW).drive_maps;

describe('app buffer observation', () => {
  it('keeps a successful zero distinct from an unreported region', () => {
    const series = buildAppMemorySeries([sample(1)], 'vm-a', NOW);
    expect(series.drive_maps.current).toMatchObject({
      state: 'ok',
      used_bytes: 0n,
      free_bytes: 8192n,
      usagePercent: 0,
    });
    expect(series.drive_paths.current).toMatchObject({
      state: 'missing',
      used_bytes: null,
      usagePercent: null,
    });
    expect(buildAppMemorySeries([sample(1)], 'another-vm', NOW).drive_maps.current.state).toBe('missing');
  });

  it('shows occupancy then release while retaining the provider peak and failure total', () => {
    const released = sample(3, 0n, 6144n);
    released.values[6]!.raw_value = '1';
    const series = maps([sample(1), sample(2, 6144n), released]);
    expect(series.points.map((point) => point.percent)).toEqual([0, 75, 0]);
    expect(series.current).toMatchObject({
      state: 'ok',
      used_bytes: 0n,
      high_water_bytes: 6144n,
      min_free_bytes: 2048n,
      capacity_failure_total: 1n,
    });
  });

  it('does not borrow the last valid pool values during busy collection and recovers later', () => {
    const reports = [sample(1, 2048n), busy(2)];
    expect(maps(reports).current).toMatchObject({
      state: 'unavailable',
      reason: 'owner_busy',
      sourceRc: -16,
      used_bytes: null,
      usagePercent: null,
    });
    expect(maps(reports).points.map((point) => point.percent)).toEqual([25, null]);
    expect(maps([...reports, sample(3, 0n, 2048n)]).current).toMatchObject({
      state: 'ok',
      used_bytes: 0n,
      high_water_bytes: 2048n,
    });
  });

  it('requires one complete region in one original sample instead of merging partial messages', () => {
    const first = sample(1);
    first.values = first.values.slice(0, 4);
    const second = sample(2);
    second.values = second.values.slice(4);
    expect(maps([first, second]).current).toMatchObject({
      state: 'invalid',
      used_bytes: null,
      usagePercent: null,
    });
  });

  it('keeps each region timestamp independent and uses source uptime rather than send time', () => {
    const map = sample(1);
    const path = sample(9, 4096n, 4096n, 'drive_paths');
    const series = buildAppMemorySeries([map, path], 'vm-a', NOW);
    expect(series.drive_maps.current.observedAt).toBe(NOW - 9100);
    expect(series.drive_paths.current.observedAt).toBe(NOW - 1100);
    expect(series.drive_maps.current.state).toBe('ok'); // 2 seconds is the wire TTL, not a UI expiry.
    expect(series.drive_maps.current.timeBasis).toBe('receipt_estimate');

    const sameSource = sample(1, 0n, 0n, 'drive_paths');
    sameSource.telemetry!.sent_uptime_ms = '1500';
    sameSource.received_at = new Date(NOW - 8500).toISOString();
    expect(buildAppMemorySeries([map, sameSource], 'vm-a', NOW).drive_paths.current.observedAt).toBe(
      NOW - 9100,
    );
  });

  it('uses source UTC when provided and records the receipt separately', () => {
    const report = sample(1);
    report.telemetry!.sent_at = new Date(NOW - 12_000).toISOString();
    report.telemetry!.utc_quality = 'bounded';
    expect(maps([report]).current).toMatchObject({
      observedAt: NOW - 12_100,
      receivedAt: report.received_at,
      timeBasis: 'source_utc',
    });
  });

  it('selects the newly observed boot and ignores a delayed known old boot', () => {
    const first = sample(1);
    const changed = sample(8, 4096n, 4096n, 'drive_paths');
    changed.boot_id = 'boot-new';
    changed.telemetry!.publisher_instance_id = 'publisher-new';
    changed.telemetry!.context.execution_id = 'exec-new';
    const delayed = sample(9);
    const series = buildAppMemorySeries([first, changed, delayed], 'vm-a', NOW);
    expect(series.drive_maps.current).toMatchObject({
      state: 'missing',
      used_bytes: null,
      reason: '미수신',
    });
    expect(series.drive_paths.points.map((point) => point.percent)).toEqual([50]);
  });

  it('does not attach old app regions to a newly reported execution in another family', () => {
    const memory = sample(9);
    memory.telemetry!.family = 'memory';
    memory.boot_id = 'boot-new';
    memory.telemetry!.publisher_instance_id = 'publisher-new';
    memory.telemetry!.context.execution_id = 'exec-new';
    memory.values = [];
    expect(maps([sample(1), memory]).current.reason).toBe('미수신');
    expect(maps([sample(1), memory]).points).toEqual([]);
  });

  it('keeps uint64 values exact above the JavaScript number range', () => {
    const report = sample(1);
    const capacity = 9_007_199_254_741_001n;
    const amounts = [
      capacity + 10n,
      1n,
      capacity - 1n,
      capacity,
      1n,
      capacity - 1n,
      18_446_744_073_709_551_615n,
      9_007_199_254_741_003n,
    ];
    report.values = report.values.map((value, index) => ({
      ...value,
      value: null,
      raw_value: amounts[index]!.toString(),
    }));
    const current = maps([report]).current;
    expect(current.state).toBe('ok');
    expect(current.capacity_bytes).toBe(capacity);
    expect(current.contention_total!.toString()).toBe('9007199254741003');
    expect(formatAppBytes(current.capacity_bytes)).toBe('8,589,934,592.00 MiB');
  });

  it('does not accept only a rounded numeric projection or an overflowing decimal', () => {
    const legacy = sample(1);
    legacy.values[0]!.raw_value = undefined;
    expect(maps([legacy]).current.state).toBe('invalid');
    legacy.values[0]!.raw_value = '18446744073709551616';
    expect(maps([legacy]).current.state).toBe('invalid');
  });

  it('deduplicates retransmission without refreshing original source age', () => {
    const first = sample(1);
    const retransmitted = sample(1);
    retransmitted.received_at = new Date(NOW - 500).toISOString();
    retransmitted.telemetry!.sent_uptime_ms = '9500';
    const series = maps([first, retransmitted]);
    expect(series.points).toHaveLength(1);
    expect(series.current.observedAt).toBe(NOW - 9100);
    expect(series.current.receivedAt).toBe(first.received_at);
  });

  it.each(['uptime', 'amount', 'source_rc', 'collection'])(
    'rejects conflicting %s in the same original sequence',
    (change) => {
      const conflicting = sample(1);
      if (change === 'uptime') conflicting.telemetry!.observed_uptime_ms = '901';
      if (change === 'amount') conflicting.values[0]!.raw_value = '12289';
      if (change === 'source_rc') conflicting.values[0]!.source_rc = -16;
      if (change === 'collection') conflicting.telemetry!.collection.status = 'collection_failed';
      expect(maps([sample(1), conflicting]).current).toMatchObject({
        state: change === 'collection' ? 'unavailable' : 'invalid',
        reason: change === 'collection' ? 'collection_failed' : '같은 원 표본의 값 충돌',
        used_bytes: null,
      });
    },
  );

  it('rejects increasing sequence with reversed source time and future time', () => {
    const reversed = sample(2);
    reversed.telemetry!.observed_uptime_ms = '800';
    expect(maps([sample(1), reversed]).current).toMatchObject({
      state: 'invalid',
      reason: '원 관측 시각 역전',
      usagePercent: null,
    });
    const stillOlder = sample(3);
    Object.assign(stillOlder.telemetry!, { observed_uptime_ms: '850', sent_uptime_ms: '1000' });
    expect(maps([sample(1), reversed, stillOlder]).current.reason).toBe('원 관측 시각 역전');
    expect(maps([sample(1), reversed, stillOlder, sample(4)]).current.state).toBe('ok');
    const future = sample(1);
    future.telemetry!.sent_at = new Date(NOW + 10_000).toISOString();
    future.telemetry!.utc_quality = 'bounded';
    expect(maps([future]).current).toMatchObject({
      state: 'invalid',
      reason: '원 관측 시각이 미래',
      usagePercent: null,
    });
  });

  it('preserves expired source failure and never substitutes an unrelated fresh region', () => {
    const expired = busy(1);
    expired.values.forEach((value) => {
      value.unavailable_reason = 'sample_expired';
    });
    const series = buildAppMemorySeries([expired, sample(9, 0n, 0n, 'drive_odometry')], 'vm-a', NOW);
    expect(series.drive_maps.current).toMatchObject({
      state: 'unavailable',
      reason: 'sample_expired',
      sourceRc: -16,
      used_bytes: null,
    });
    expect(series.drive_odometry.current.state).toBe('ok');
  });
});
