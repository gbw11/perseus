import { describe, expect, it } from 'vitest';
import type { MetricSample } from '../api/types';
import { buildManagedRamSeries, managedRamSummary, type ManagedRamIdentity } from './managedRam';
import { memoryFixture, ramOracle, wireFixture } from './memoryContractFixtures';

const NOW = Date.parse('2026-11-04T10:00:00Z');
const identity = (sample: MetricSample): ManagedRamIdentity => ({
  vmId: sample.vm_id,
  bootId: sample.boot_id!,
  publisherInstanceId: sample.telemetry!.publisher_instance_id,
  context: sample.telemetry!.context,
});

describe('managed RAM same-payload arithmetic', () => {
  for (const entry of ramOracle) {
    it(`matches the provider ${entry.name} vector without mixing payloads`, () => {
      const raw = wireFixture(entry.name);
      const result = managedRamSummary(
        Array.isArray(raw) ? (raw as unknown as MetricSample) : memoryFixture(entry.name),
      );
      if (entry.expected.status === 'ok') {
        expect(result).toMatchObject({
          status: 'ok',
          reason: null,
          totalBytes: entry.expected.total_bytes,
          committedBytes: entry.expected.committed_bytes,
          freeBytes: entry.expected.free_bytes,
          percent: entry.expected.percent,
        });
      } else {
        // 일부 원 벡터는 서버 schema 단계에서 거절된다. 클라이언트도 이를 정상 비율로 복원하지 않는다.
        expect(result.status).not.toBe('ok');
        expect([result.totalBytes, result.committedBytes, result.freeBytes, result.percent]).toEqual([
          null,
          null,
          null,
          null,
        ]);
      }
    });
  }

  it('does not combine fields from two partial memory messages', () => {
    const left = memoryFixture('ram-committed'),
      right = structuredClone(left);
    left.values = left.values.filter((v) => v.name !== 'memory.page_allocator.free_bytes');
    right.values = right.values.filter((v) => v.name === 'memory.page_allocator.free_bytes');
    const result = buildManagedRamSeries([left, right], left.vm_id, NOW, identity(left));
    expect(result.current.percent).toBeNull();
  });
});

describe('managed RAM observation lease', () => {
  it('splits graph segments on a new source context without changing its publisher session or lease', () => {
    const first = memoryFixture('ram-committed', NOW);
    const next = memoryFixture('ram-reserved-increase', NOW + 1000);
    Object.assign(next.telemetry!, {
      source_sample_seq: '2',
      observed_uptime_ms: '10001',
      sent_uptime_ms: '10003',
      context: { ...next.telemetry!.context, execution_id: 'new-execution', receipt_id: 'new-receipt' },
    });
    Object.assign(next.telemetry!.collection, { start_uptime_ms: '10000', end_uptime_ms: '10002' });
    const view = buildManagedRamSeries([first, next], first.vm_id, NOW + 1000);
    expect(view.current.percent).toBe('50.000');
    expect(view.points.map((point) => point.percent)).toEqual([25, null, 50]);
    expect(view.firstReceivedAt).toBe(NOW + 1000);
    expect(next.telemetry!.context).toMatchObject({
      execution_id: 'new-execution',
      receipt_id: 'new-receipt',
    });
  });

  it('does not lower the observation watermark after a rejected source time and recovers beyond it', () => {
    const first = memoryFixture('ram-committed', NOW);
    const reversed = memoryFixture('ram-committed', NOW + 1000);
    Object.assign(reversed.telemetry!, { source_sample_seq: '2', observed_uptime_ms: '9000' });
    const stillOlder = memoryFixture('ram-committed', NOW + 2000);
    Object.assign(stillOlder.telemetry!, { source_sample_seq: '3', observed_uptime_ms: '9500' });
    expect(buildManagedRamSeries([first, reversed, stillOlder], first.vm_id, NOW + 2000).current.reason).toBe(
      'RAM_SOURCE_TIME_INVALID',
    );
    const recovered = memoryFixture('ram-committed', NOW + 3000);
    Object.assign(recovered.telemetry!, {
      source_sample_seq: '4',
      observed_uptime_ms: '10001',
      sent_uptime_ms: '10003',
    });
    Object.assign(recovered.telemetry!.collection, { start_uptime_ms: '10000', end_uptime_ms: '10002' });
    expect(
      buildManagedRamSeries([first, reversed, stillOlder, recovered], first.vm_id, NOW + 3000).current
        .percent,
    ).toBe('25.000');
  });

  it('uses only the newly observed boot for the ratio and graph and ignores a delayed retired boot', () => {
    const first = memoryFixture('ram-committed', NOW),
      next = memoryFixture('ram-reserved-increase', NOW + 1000);
    next.boot_id = 'new-boot';
    next.telemetry!.publisher_instance_id = 'new-publisher';
    const delayed = structuredClone(first);
    delayed.received_at = new Date(NOW + 2000).toISOString();
    delayed.telemetry!.publisher_instance_id = 'unseen-old-boot-publisher';
    const result = buildManagedRamSeries([first, next, delayed], first.vm_id, NOW + 2000);
    expect(result.current.percent).toBe('50.000');
    expect(result.points.map((point) => point.percent)).toEqual([50]);
    expect(result.firstReceivedAt).toBe(NOW + 1000);
    const failed = structuredClone(next);
    failed.received_at = new Date(NOW + 3000).toISOString();
    failed.values.find((value) => value.name === 'memory.page_allocator.free_bytes')!.status =
      'collection_failed';
    expect(
      buildManagedRamSeries([first, next, failed, delayed], first.vm_id, NOW + 3000).current.percent,
    ).toBeNull();
    expect(buildManagedRamSeries([first, next, delayed], first.vm_id, NOW + 8000).current.reason).toBe(
      'RAM_RECEIPT_EXPIRED',
    );
  });

  it('clears prior RAM when a new boot is observed first in app_memory, then recovers from its own RAM', () => {
    const first = memoryFixture('ram-committed', NOW),
      app = memoryFixture('manual-memory-running-full', NOW + 1000);
    app.vm_id = first.vm_id; // 이 시험에서만 같은 VM의 새 세션 사건으로 구성한다.
    app.boot_id = 'new-boot';
    app.telemetry!.publisher_instance_id = 'new-publisher';
    const unavailable = buildManagedRamSeries([first, app], first.vm_id, NOW + 1000);
    expect(unavailable.current.percent).toBeNull();
    expect(unavailable.points).toEqual([]);
    const next = memoryFixture('ram-reserved-increase', NOW + 2000);
    next.boot_id = app.boot_id;
    next.telemetry!.publisher_instance_id = app.telemetry!.publisher_instance_id;
    expect(buildManagedRamSeries([first, app, next], first.vm_id, NOW + 2000).current.percent).toBe('50.000');
  });

  it('keeps equal-receipt session order unknown without selecting by array order', () => {
    const first = memoryFixture('ram-committed', NOW),
      next = memoryFixture('ram-reserved-increase', NOW);
    next.boot_id = 'new-boot';
    for (const reports of [
      [first, next],
      [next, first],
    ]) {
      const view = buildManagedRamSeries(reports, first.vm_id, NOW);
      expect(view.current.reason).toBe('RAM_SESSION_AMBIGUOUS');
      expect(view.points).toEqual([]);
    }
  });

  it('keeps same-source context changes conflicting without renewing the receipt lease', () => {
    const first = memoryFixture('ram-committed', NOW),
      changed = structuredClone(first);
    changed.received_at = new Date(NOW + 1000).toISOString();
    changed.telemetry!.context.execution_id = 'different-execution';
    const view = buildManagedRamSeries([first, changed], first.vm_id, NOW + 1000);
    expect(view.current.reason).toBe('RAM_SAMPLE_CONFLICT');
    expect(view.firstReceivedAt).toBe(NOW);
  });

  it('rejects an allocator metric relabelled as a different memory scope', () => {
    const report = memoryFixture('ram-committed');
    report.values.find((value) => value.name === 'memory.page_allocator.free_bytes')!.labels = {
      scope: 'heap',
    };
    expect(managedRamSummary(report)).toMatchObject({
      status: 'unknown',
      reason: 'RAM_SOURCE_UNAVAILABLE',
      percent: null,
    });
    const recovered = memoryFixture('ram-reserved-increase', NOW + 1000);
    recovered.telemetry!.source_sample_seq = '2';
    expect(
      buildManagedRamSeries([report, recovered], report.vm_id, NOW + 1000, identity(report)).current.percent,
    ).toBe('50.000');
  });

  it('displays the latest valid observation by default and supports an optional strict identity', () => {
    const report = memoryFixture('ram-committed', NOW - 1000);
    const unknown = buildManagedRamSeries([report], report.vm_id, NOW);
    expect(unknown.current).toMatchObject({
      status: 'ok',
      reason: null,
      percent: '25.000',
    });
    expect(unknown.points[0]?.percent).toBe(25);
    expect(buildManagedRamSeries([report], report.vm_id, NOW, identity(report)).current.percent).toBe(
      '25.000',
    );
  });

  it('uses the first receipt and original age when a new heap envelope repeats the same source', () => {
    const report = memoryFixture('ram-committed', NOW);
    const duplicate = memoryFixture('ram-heap-changed', NOW + 7000);
    duplicate.telemetry!.source_sample_seq = report.telemetry!.source_sample_seq;
    duplicate.telemetry!.observed_uptime_ms = report.telemetry!.observed_uptime_ms;
    const view = buildManagedRamSeries([report, duplicate], report.vm_id, NOW + 7000, identity(report));
    expect(view.firstReceivedAt).toBe(NOW);
    expect(view.current).toMatchObject({ reason: 'RAM_RECEIPT_EXPIRED', percent: null });
  });

  it('keeps same-source collection failure sticky until a genuinely new source recovers', () => {
    const good = memoryFixture('ram-committed', NOW);
    const bad = structuredClone(good);
    bad.received_at = new Date(NOW + 1000).toISOString();
    const free = bad.values.find((v) => v.name === 'memory.page_allocator.free_bytes')!;
    Object.assign(free, {
      value: null,
      raw_value: null,
      status: 'collection_failed',
      unavailable_reason: 'sample_expired',
      source_rc: -134,
    });
    const replay = structuredClone(good);
    replay.received_at = new Date(NOW + 2000).toISOString();
    const failed = buildManagedRamSeries([good, bad, replay], good.vm_id, NOW + 2000, identity(good));
    expect(failed.current).toMatchObject({ reason: 'RAM_SOURCE_UNAVAILABLE', percent: null });
    expect(failed.current.sourceStatus['memory.page_allocator.free_bytes']?.sourceRc).toBe(-134);
    const next = structuredClone(good);
    next.received_at = new Date(NOW + 3000).toISOString();
    Object.assign(next.telemetry!, {
      source_sample_seq: '2',
      observed_uptime_ms: '10001',
      sent_uptime_ms: '10003',
    });
    Object.assign(next.telemetry!.collection, { start_uptime_ms: '10000', end_uptime_ms: '10002' });
    expect(
      buildManagedRamSeries([good, bad, replay, next], good.vm_id, NOW + 3000, identity(good)).current
        .percent,
    ).toBe('25.000');
  });

  it('does not revive a same-source conflict and separates changed executions', () => {
    const good = memoryFixture('ram-committed', NOW),
      conflict = structuredClone(good);
    conflict.received_at = new Date(NOW + 1000).toISOString();
    const free = conflict.values.find((v) => v.name === 'memory.page_allocator.free_bytes')!;
    Object.assign(free, { raw_value: '8192', value: 8192 });
    const replay = structuredClone(good);
    replay.received_at = new Date(NOW + 2000).toISOString();
    expect(
      buildManagedRamSeries([good, conflict, replay], good.vm_id, NOW + 2000, identity(good)).current.reason,
    ).toBe('RAM_SAMPLE_CONFLICT');
    const changed = {
      ...identity(good),
      context: { ...identity(good).context, execution_id: 'new-execution' },
    };
    expect(buildManagedRamSeries([good], good.vm_id, NOW + 2000, changed).current.reason).toBe(
      'RAM_CONTEXT_CHANGED',
    );
    expect(buildManagedRamSeries([good], good.vm_id, NOW - 1, identity(good)).current.reason).toBe(
      'RAM_RECEIVER_CLOCK_ORDER',
    );
  });

  it('keeps a delayed old source from replacing the newest observation', () => {
    const old = memoryFixture('ram-committed', NOW + 2000),
      newer = memoryFixture('ram-reserved-increase', NOW + 1000);
    newer.telemetry!.source_sample_seq = '2';
    expect(buildManagedRamSeries([newer, old], old.vm_id, NOW + 2000, identity(old)).current.percent).toBe(
      '50.000',
    );
  });

  it('does not show a future UTC observation as current and recovers with a new valid source', () => {
    const report = memoryFixture('ram-committed', NOW);
    Object.assign(report.telemetry!, {
      utc_quality: 'synchronized',
      sent_at: new Date(NOW + 10_000).toISOString(),
    });
    expect(buildManagedRamSeries([report], report.vm_id, NOW, identity(report)).current).toMatchObject({
      reason: 'RAM_SOURCE_TIME_INVALID',
      percent: null,
    });
    const next = memoryFixture('ram-reserved-increase', NOW + 1000);
    next.telemetry!.source_sample_seq = '2';
    expect(
      buildManagedRamSeries([report, next], report.vm_id, NOW + 1000, identity(report)).current.percent,
    ).toBe('50.000');
  });
});
