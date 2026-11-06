import { describe, expect, it } from 'vitest';
import { driveAppStatus, vmLineStatus } from './driveExtras';
import { presentationFixture } from './fixture';

describe('VM 한 줄 상태 — Xen 신선도', () => {
  it('Xen 수신이 끊기면 마지막 xen_state(crashed)를 그대로 쓰지 않는다', () => {
    const p = presentationFixture();
    const vm = { ...p.state.vms[0]!, xen_state: 'crashed', sources: { ...p.state.vms[0]!.sources } };
    expect(driveAppStatus(null, vm, true).label).toBe('VM 장애');
    vm.sources.xen = { status: 'stale', age_ms: 9000 };
    expect(driveAppStatus(null, vm, true)).toEqual({ label: 'VM 상태 미확인', tone: 'muted' });
  });

  it('paused·stopping 은 꺼짐이 아니라 그 상태로 보인다', () => {
    const p = presentationFixture();
    const vm = p.state.vms[0]!;
    expect(driveAppStatus(null, { ...vm, xen_state: 'paused' }, true).label).toBe('일시 정지');
    expect(driveAppStatus(null, { ...vm, xen_state: 'stopping' }, true).label).toBe('종료 중');
    expect(driveAppStatus(null, { ...vm, xen_state: 'stopped' }, true).label).toBe('VM 꺼짐');
  });

  it('인포테인먼트는 running·신선할 때만 켜짐으로 본다', () => {
    const p = presentationFixture();
    const vm = p.state.vms[2]!;
    expect(vmLineStatus(p, vm, true)).toEqual({ label: '인포테인먼트', tone: 'ok' });
    expect(vmLineStatus(p, vm, false).label).toBe('연결 미확인');
    expect(vmLineStatus(p, { ...vm, xen_state: 'stopped' }, true).label).toBe('VM 꺼짐');
    expect(
      vmLineStatus(p, { ...vm, sources: { ...vm.sources, xen: { status: 'stale', age_ms: 9000 } } }, true)
        .label,
    ).toBe('VM 상태 미확인');
  });
});
