import { describe, expect, it } from 'vitest';
import { presentationFixture } from './fixture';
import { parsePresentation, SnapshotOrder } from './model';

describe('HDMI presentation contract', () => {
  it('validates Gateway permission separately from selected AUTO mode', () => {
    const p = presentationFixture();
    p.state.drive.mode = 'AUTO';
    p.state.drive.source.status = 'fresh';
    p.state.drive.authority = {
      selected_vm_id: p.state.vms[0]!.vm_id,
      selected_mode: 'AUTO',
      generation: 1,
      phase: 'DISARMED',
      output_permitted: false,
      storage_uncertain: false,
    };
    expect(parsePresentation(p).state.drive.active_vm_id).toBeNull();
    p.state.drive.active_vm_id = p.state.vms[0]!.vm_id;
    expect(() => parsePresentation(p)).toThrow();
    p.state.drive.authority.phase = 'APPLIED';
    p.state.drive.authority.output_permitted = true;
    expect(parsePresentation(p).state.drive.active_vm_id).toBe(p.state.vms[0]!.vm_id);
    p.state.drive.authority.storage_uncertain = true;
    expect(() => parsePresentation(p)).toThrow();
    p.state.drive.active_vm_id = null;
    p.state.drive.mode = 'unknown';
    expect(parsePresentation(p).state.drive.mode).toBe('unknown');
  });
  it('rejects incompatible OS roles, missing detail targets and unsupported active driving targets', () => {
    const wrongOs = presentationFixture();
    wrongOs.state.vms[1]!.os.expected = 'debian';
    expect(() => parsePresentation(wrongOs)).toThrow();
    const missingTarget = presentationFixture();
    missingTarget.outputs[1]!.vm_id = null;
    expect(() => parsePresentation(missingTarget)).toThrow();
    const invalidActive = presentationFixture();
    invalidActive.state.drive.source.status = 'fresh';
    invalidActive.state.drive.active_vm_id = invalidActive.state.vms[2]!.vm_id;
    expect(() => parsePresentation(invalidActive)).toThrow();
    const expired = presentationFixture();
    expired.state.drive.source.status = 'stale';
    expired.state.drive.mode = 'AUTO';
    expect(() => parsePresentation(expired)).toThrow();
  });
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
  it('rejects legacy or swapped vehicle screen mappings', () => {
    const p = presentationFixture();
    p.outputs[0]!.screen = 'infotainment';
    expect(() => parsePresentation(p)).toThrow();
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
