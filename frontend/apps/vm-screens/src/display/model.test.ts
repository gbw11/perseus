import { describe, expect, it } from 'vitest';
import { presentationFixture } from './fixture';
import { parsePresentation, SnapshotOrder } from './model';

describe('HDMI presentation contract', () => {
  it('accepts the three roles and two output mapping', () => {
    expect(parsePresentation(presentationFixture()).state.vms[1]!.role).toBe('backup_drive');
  });
  it('rejects missing observations, duplicate roles and invalid output targets', () => {
    expect(() => parsePresentation({ schema_version: 1 })).toThrow();
    const p = presentationFixture();
    p.state.vms[1]!.role = 'primary_drive';
    expect(() => parsePresentation(p)).toThrow();
    const other = presentationFixture();
    other.outputs[1]!.vm_id = 'missing';
    expect(() => parsePresentation(other)).toThrow();
  });
  it('rejects duplicate/reordered snapshots and retired collector epochs', () => {
    const order = new SnapshotOrder();
    const p = presentationFixture();
    expect(order.accept(p)).toBe(true);
    expect(order.accept(p)).toBe(false);
    p.state.sequence = 2;
    expect(order.accept(p)).toBe(true);
    p.state.source_epoch = 'feed-2';
    p.state.sequence = 1;
    expect(order.accept(p)).toBe(true);
    p.state.source_epoch = 'feed-1';
    p.state.sequence = 99;
    expect(order.accept(p)).toBe(false);
  });
});
