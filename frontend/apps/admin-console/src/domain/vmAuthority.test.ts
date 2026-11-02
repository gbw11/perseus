import { describe, expect, it } from 'vitest';
import { vmAuthorityOf } from './vmAuthority';

const driving = { driving: true };
const infotainment = { driving: false };
const vmA = { vm_id: 'vm-a' };
const vmB = { vm_id: 'vm-b' };

describe('vmAuthorityOf', () => {
  it('active_vm_id와 일치하는 주행 VM만 ACTIVE로 표시한다', () => {
    expect(vmAuthorityOf(driving, vmA, 'vm-a')).toBe('ACTIVE');
    expect(vmAuthorityOf(driving, vmB, 'vm-a')).toBe('STANDBY');
  });

  it('active_vm_id가 없으면 주행 VM을 추정하지 않는다', () => {
    expect(vmAuthorityOf(driving, vmA, null)).toBe('UNKNOWN');
  });

  it('비주행 VM은 INFOTAINMENT로 표시한다', () => {
    expect(vmAuthorityOf(infotainment, undefined, null)).toBe('INFOTAINMENT');
  });

  it('역할이나 VM을 확인할 수 없으면 UNKNOWN으로 표시한다', () => {
    expect(vmAuthorityOf(null, vmA, 'vm-a')).toBe('UNKNOWN');
    expect(vmAuthorityOf(driving, undefined, 'vm-a')).toBe('UNKNOWN');
  });
});
