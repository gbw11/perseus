import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseVmBindings } from './vmBindings';

const bindings = {
  primary_drive: '11111111-1111-4111-8111-111111111111',
  backup_drive: '22222222-2222-4222-8222-222222222222',
  interaction: '33333333-3333-4333-8333-333333333333',
};
afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('deployment UUID roles', () => {
  it('requires explicit unique UUIDs for all three roles', () => {
    expect(parseVmBindings(undefined)).toEqual({});
    expect(parseVmBindings(JSON.stringify(bindings))).toEqual(bindings);
    for (const bad of [
      [],
      { primary_drive: bindings.primary_drive },
      { ...bindings, backup_drive: bindings.primary_drive },
      { ...bindings, interaction: 'manual' },
    ])
      expect(() => parseVmBindings(JSON.stringify(bad))).toThrow();
  });
  it('does not derive a primary or backup VM from legacy names or app IDs', async () => {
    vi.stubEnv('VITE_MOCK', 'false');
    vi.stubEnv('VITE_VM_ROLE_BINDINGS', '');
    const { roleOf } = await import('./roles');
    const { initialVms } = await import('../mocks/data');
    for (const vm of initialVms()) expect(roleOf(vm, null)).toBeNull();
  });
  it('maps confirmed UUIDs independently of VM name and active app', async () => {
    vi.stubEnv('VITE_MOCK', 'false');
    vi.stubEnv('VITE_VM_ROLE_BINDINGS', JSON.stringify(bindings));
    const { roleOf, controlLabel, ROLES } = await import('./roles');
    const { initialVms } = await import('../mocks/data');
    const vm = { ...initialVms()[0]!, name: 'manual-but-primary' };
    expect(roleOf(vm, null)?.title).toBe('VM1 · 주 구동');
    const control = {
      mode: 'MANUAL' as const,
      generation: 1,
      active_app: 'manual' as const,
      active_vm_id: vm.vm_id,
      reason: null,
      received_ago_ms: 0,
    };
    const drive = { version: 1, observed_at: '', control, vehicle: null, apps: {}, perception: null };
    expect(controlLabel(ROLES.autonomous, drive)).toContain('활성 구동');
    expect(controlLabel(ROLES.manual, drive)).not.toContain('▶');
    drive.control.received_ago_ms = 3000;
    expect(controlLabel(ROLES.autonomous, drive)).toBe('활성 구동 VM 미확인');
  });
  it('does not treat a standby command gap or another VM report as this VM failing or ready', async () => {
    const { appHealth, appHealthLabel, ROLES } = await import('./roles');
    const { initialVms } = await import('../mocks/data');
    const vm = initialVms()[1]!;
    const report = {
      vm_id: vm.vm_id,
      command_fresh: false,
      command_ago_ms: null,
      command_hz: 0,
      last_command: null,
      heartbeat_state: 'standby' as const,
      heartbeat_ago_ms: 100,
    };
    expect(appHealth(ROLES.manual, vm, report)).toBe('ok');
    expect(appHealthLabel(vm, report)).toContain('주행 준비는 별도 확인');
    expect(appHealth(ROLES.manual, vm, { ...report, vm_id: bindings.primary_drive })).not.toBe('ok');
    expect(appHealthLabel(vm, { ...report, vm_id: bindings.primary_drive })).toBe('앱 보고 UUID 미확인');
  });
});
