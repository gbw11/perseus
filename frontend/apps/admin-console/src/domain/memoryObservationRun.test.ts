import { describe, expect, it } from 'vitest';
import type { MetricSample } from '../api/types';
import { memoryFixture } from './memoryContractFixtures';
import { memoryObservationRunKey, selectMemoryObservationRun } from './memoryObservationRun';

const NOW = Date.parse('2026-11-04T10:00:00Z');
function report(boot: string, publisher: string, offset: number): MetricSample {
  const sample = memoryFixture('ram-committed', NOW + offset);
  sample.boot_id = boot;
  sample.telemetry!.publisher_instance_id = publisher;
  return sample;
}

describe('bounded memory observation session selection', () => {
  it('keeps a new boot selected when a retired boot returns with an unseen publisher and a newer envelope', () => {
    const first = report('a', 'p1', 0),
      next = report('b', 'p1', 1000),
      delayed = report('a', 'p2', 2000);
    delayed.telemetry!.message_seq = '9999';
    const result = selectMemoryObservationRun([delayed, next, first], first.vm_id);
    expect(result).toEqual({ key: memoryObservationRunKey(next), state: 'selected' });
  });

  it('allows a publisher restart in the observed boot without returning to its retired publisher', () => {
    const first = report('a', 'p1', 0),
      next = report('a', 'p2', 1000),
      delayed = report('a', 'p1', 2000);
    expect(selectMemoryObservationRun([first, next, delayed], first.vm_id).key).toBe(
      memoryObservationRunKey(next),
    );
  });

  it('does not sort different sessions by source sequence, uptime, or envelope UTC', () => {
    const first = report('a', 'p1', 0),
      next = report('b', 'p2', 1000);
    first.telemetry!.source_sample_seq = '999999';
    next.telemetry!.source_sample_seq = '1';
    next.telemetry!.sent_at = new Date(NOW - 10000).toISOString();
    expect(selectMemoryObservationRun([next, first], first.vm_id).key).toBe(memoryObservationRunKey(next));
  });

  it('keeps equal-receipt competing sessions ambiguous in either array order and recovers with a later sole session', () => {
    const a = report('a', 'p1', 0),
      b = report('b', 'p2', 0);
    expect(selectMemoryObservationRun([a, b], a.vm_id)).toEqual({ key: null, state: 'ambiguous' });
    expect(selectMemoryObservationRun([b, a], a.vm_id)).toEqual({ key: null, state: 'ambiguous' });
    const next = report('b', 'p2', 1000),
      delayed = report('a', 'p3', 2000);
    expect(selectMemoryObservationRun([a, b, next, delayed], a.vm_id).key).toBe(
      memoryObservationRunKey(next),
    );
  });

  it('does not split same boot/publisher context changes into a new lease session', () => {
    const first = report('a', 'p1', 0),
      changed = report('a', 'p1', 1000);
    changed.telemetry!.context.execution_id = 'changed';
    expect(memoryObservationRunKey(first)).toBe(memoryObservationRunKey(changed));
  });

  it('does not turn a newer incomplete memory envelope into a reason to reuse old values', () => {
    const first = report('a', 'p1', 0),
      missing = report('b', 'p2', 1000);
    missing.telemetry = undefined;
    expect(selectMemoryObservationRun([first, missing], first.vm_id).state).toBe('unverified');
  });
});
