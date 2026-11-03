export type VmAuthority = 'ACTIVE' | 'STANDBY' | 'UNKNOWN' | 'INFOTAINMENT';

/** 주행 VM(주 구동·백업)의 제어권은 active_vm_id만 신뢰하고, 비주행 VM은 역할을 그대로 표시한다. */
export function vmAuthorityOf(
  role: { driving: boolean } | null,
  vm: { vm_id: string } | undefined,
  activeVmId: string | null | undefined,
): VmAuthority {
  if (!role) return 'UNKNOWN';
  if (!role.driving) return 'INFOTAINMENT';
  if (!vm || !activeVmId) return 'UNKNOWN';
  return activeVmId === vm.vm_id ? 'ACTIVE' : 'STANDBY';
}
