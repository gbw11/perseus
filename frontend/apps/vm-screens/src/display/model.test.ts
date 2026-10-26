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
  it('accepts optional selection reasons and rejects malformed reasons', () => {
    const p = presentationFixture();
    p.outputs[1]!.reason = '활성 구동 VM 미확인 · 마지막 선택 유지';
    expect(parsePresentation(p).outputs[1]!.reason).toBe(p.outputs[1]!.reason);
    expect(() =>
      parsePresentation({ ...p, outputs: [p.outputs[0], { ...p.outputs[1], reason: 42 }] }),
    ).toThrow();
  });
});
