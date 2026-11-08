import { describe, expect, it } from 'vitest';
import type { MetricSample } from '../api/types';
import { buildAppMemorySeries, type AppMemoryRegion } from './appMemory';
import { memoryFixture, memoryWireNames } from './memoryContractFixtures';

const NOW = Date.parse('2026-11-04T10:00:00Z');
function regionOf(sample: MetricSample): AppMemoryRegion {
  return sample.values[0]!.labels!.region as AppMemoryRegion;
}
const current = (sample: MetricSample, now = NOW) =>
  buildAppMemorySeries([sample], sample.vm_id, now)[regionOf(sample)].current;

describe('provider MANUAL buffer observations', () => {
  for (const name of memoryWireNames.filter((name) => name.startsWith('manual-memory-'))) {
    it(`consumes ${name} while preserving the separate owner state`, () => {
      const sample = memoryFixture(name),
        result = current(sample);
      if (name === 'manual-memory-busy' || name === 'manual-memory-sample-expired') {
        expect(result.state).toBe('unavailable');
        expect(result.used_bytes).toBeNull();
        expect(result.buffer).toBeNull();
      } else {
        expect(result.state).toBe('ok');
        expect(result.buffer).toEqual(sample.telemetry!.buffer);
        expect(result.used_bytes?.toString()).toBe(
          sample.values.find((v) => v.name === 'memory.app.used_bytes')!.raw_value,
        );
      }
    });
  }

  for (const region of ['manual_input', 'manual_pending', 'manual_running', 'manual_result']) {
    it(`reads actual native ${region} without manufacturing a shared VM/snapshot`, () => {
      const sample = memoryFixture(`native-${region}`),
        result = current(sample);
      expect(result.state).toBe('ok');
      expect(result.buffer).toEqual(sample.telemetry!.buffer);
      expect(result.observedUptimeMs).toBe(sample.telemetry!.observed_uptime_ms);
      expect(result.sourceSampleSeq).toBe(sample.telemetry!.source_sample_seq);
      expect(result.stale).toBe(false);
    });
  }

  it('keeps zero source sequence/time, invalid stored input and retained reads distinct', () => {
    const accepted = current(memoryFixture('manual-memory-input-seq-zero'));
    expect(accepted.buffer).toMatchObject({ sequence: '0', source_uptime_ms: '0', input_status: 'accepted' });
    const invalid = current(memoryFixture('manual-memory-input-invalid'));
    expect(invalid).toMatchObject({ state: 'ok', used_bytes: 256n });
    expect(invalid.buffer?.input_status).toBe('invalid');
    const read = current(memoryFixture('manual-memory-result-read'));
    expect(read.buffer).toMatchObject({ phase: 'retained', performed: true, last_read_sequence: '9' });
    expect(read.used_bytes).not.toBe(0n);
    const emptyResult = current(memoryFixture('manual-memory-result-empty'));
    expect(emptyResult.buffer?.performed).toBe(false);
    expect(current(memoryFixture('manual-memory-running-empty')).buffer?.performed).toBeNull();
  });

  it('does not combine a pending slot with a different source or invent unsupported MANUAL regions', () => {
    const pending = memoryFixture('manual-memory-pending-full');
    const series = buildAppMemorySeries([pending], pending.vm_id, NOW);
    expect(series.manual_pending.current.used_bytes).not.toBeNull();
    expect(series.manual_running.current.state).toBe('missing');
    expect(series.manual_result.current.state).toBe('missing');
    const corrupt = structuredClone(pending);
    corrupt.telemetry!.buffer = null;
    expect(current(corrupt).state).toBe('invalid');
  });

  it('uses the first source receipt rather than renewed envelope arrival for each region lease', () => {
    const first = memoryFixture('manual-memory-running-full', NOW);
    const again = structuredClone(first);
    again.received_at = new Date(NOW + 5000).toISOString();
    const series = buildAppMemorySeries([first, again], first.vm_id, NOW + 6100);
    expect(series.manual_running.current.stale).toBe(true);
    expect(series.manual_running.current.receivedAt).toBe(first.received_at);
    expect(series.manual_pending.current.state).toBe('missing');
  });

  it('keeps a same-source observation failure over old valid replay and recovers only with a new source', () => {
    const first = memoryFixture('manual-memory-running-full', NOW),
      failed = structuredClone(first);
    failed.received_at = new Date(NOW + 1000).toISOString();
    failed.telemetry!.buffer = null;
    failed.values = failed.values.map((value) => ({
      ...value,
      raw_value: null,
      value: null,
      status: 'collection_failed',
      unavailable_reason: 'owner_busy',
      source_rc: -134,
    }));
    const replay = structuredClone(first);
    replay.received_at = new Date(NOW + 2000).toISOString();
    expect(
      buildAppMemorySeries([first, failed, replay], first.vm_id, NOW + 2000).manual_running.current,
    ).toMatchObject({ state: 'unavailable', used_bytes: null, buffer: null, sourceRc: -134 });
    const recovered = structuredClone(first);
    recovered.received_at = new Date(NOW + 3000).toISOString();
    recovered.telemetry!.source_sample_seq = (BigInt(first.telemetry!.source_sample_seq!) + 1n).toString();
    expect(
      buildAppMemorySeries([first, failed, replay, recovered], first.vm_id, NOW + 3000).manual_running.current
        .state,
    ).toBe('ok');
  });
});
