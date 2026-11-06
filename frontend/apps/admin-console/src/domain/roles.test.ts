import { describe, expect, it } from 'vitest';
import { initialVms } from '../mocks/data';
import {
  ago,
  appHealth,
  appHealthLabel,
  appReportOf,
  controlLabel,
  ROLES,
  roleOf,
  sortVms,
  vmOf,
} from './roles';

describe('VM roles from vm.role', () => {
  it('maps the backend role independently of VM name', () => {
    const [primary, backup, interaction] = initialVms();
    expect(roleOf({ ...primary!, name: 'manual-but-primary' })?.app).toBe('autonomous');
    expect(roleOf(backup!)?.title).toBe('백업 VM');
    expect(roleOf(interaction!)?.driving).toBe(false);
    expect(vmOf('manual', initialVms())?.vm_id).toBe(backup!.vm_id);
  });
  it('does not derive a role from legacy names or app IDs when role is missing', () => {
    for (const vm of initialVms()) {
      expect(roleOf({ ...vm, role: null })).toBeNull();
      expect(roleOf({ ...vm, role: undefined })).toBeNull();
    }
  });
  it('orders VMs by role, then unassigned VMs by name', () => {
    const [primary, backup, interaction] = initialVms();
    const zeta = { ...interaction!, vm_id: 'z', name: 'zeta', role: null };
    const alpha = { ...interaction!, vm_id: 'a', name: 'alpha', role: null };
    expect(sortVms([zeta, interaction!, alpha, backup!, primary!]).map((vm) => vm.name)).toEqual([
      'autonomous',
      'manual',
      'interaction',
      'alpha',
      'zeta',
    ]);
  });
  it('labels control authority by the VM id', () => {
    const [primary, backup] = initialVms();
    const control = {
      mode: 'MANUAL' as const,
      generation: 1,
      active_app: 'manual' as const,
      active_vm_id: primary!.vm_id,
      reason: null,
      received_ago_ms: 0,
    };
    const drive = { version: 1, observed_at: '', control, vehicle: null, apps: {}, perception: null };
    expect(controlLabel(ROLES.autonomous, primary, drive)).toContain('활성 구동');
    expect(controlLabel(ROLES.manual, backup, drive)).not.toContain('▶');
    expect(controlLabel(ROLES.manual, undefined, drive)).toBe('활성 구동 VM 미확인');
    drive.control.received_ago_ms = 3000;
    expect(controlLabel(ROLES.autonomous, primary, drive)).toBe('활성 구동 VM 미확인');
  });
  it('does not treat a standby command gap or another VM report as this VM failing or ready', () => {
    const [primary, vm] = initialVms();
    const report = {
      vm_id: vm!.vm_id,
      command_fresh: false,
      command_ago_ms: null,
      command_hz: 0,
      last_command: null,
      heartbeat_state: 'standby' as const,
      heartbeat_ago_ms: 100,
    };
    expect(appHealth(ROLES.manual, vm, report)).toBe('ok');
    expect(appHealthLabel(vm, report)).toContain('주행 준비는 별도 확인');
    expect(appHealth(ROLES.manual, vm, { ...report, vm_id: primary!.vm_id })).not.toBe('ok');
    expect(appHealthLabel(vm, { ...report, vm_id: primary!.vm_id })).toBe('앱 보고 UUID 미확인');
  });
  it('finds the app report for a VM by vm_id', () => {
    const [primary, backup] = initialVms();
    const report = { vm_id: backup!.vm_id, heartbeat_ago_ms: 100 };
    const drive = { apps: { autonomous: report } } as unknown as Parameters<typeof appReportOf>[1];
    expect(appReportOf(backup!, drive)).toBe(report);
    expect(appReportOf(primary!, drive)).toBeUndefined();
  });
});

describe('ago', () => {
  it('60초 이상은 분, 60분 이상은 시간·분으로 쓴다', () => {
    expect(ago(null)).toBe('—');
    expect(ago(400)).toBe('0.4초 전');
    expect(ago(12_000)).toBe('12초 전');
    expect(ago(59_400)).toBe('59초 전');
    expect(ago(60_000)).toBe('1분 전');
    expect(ago(125_000)).toBe('2분 전');
    expect(ago(59 * 60_000)).toBe('59분 전');
    expect(ago(60 * 60_000)).toBe('1시간 0분 전');
    expect(ago(3 * 3_600_000 + 5 * 60_000)).toBe('3시간 5분 전');
  });
});
