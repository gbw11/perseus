/** Deployment-owned UUID bindings. App IDs and VM names are not proof of a driving role. */
export interface VmBindings {
  primary_drive: string;
  backup_drive: string;
  interaction: string;
}

export function parseVmBindings(raw: string | undefined): Partial<VmBindings> {
  if (!raw?.trim()) return {};
  const value: unknown = JSON.parse(raw);
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error('VM 역할 매핑 형식 오류');
  const record = value as Record<string, unknown>;
  const roles = ['primary_drive', 'backup_drive', 'interaction'];
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (
    Object.keys(record).length !== 3 ||
    roles.some((role) => typeof record[role] !== 'string' || !uuid.test(String(record[role])))
  )
    throw new Error('세 역할의 VM UUID가 필요합니다.');
  if (new Set(Object.values(record).map((id) => String(id).toLowerCase())).size !== 3)
    throw new Error('VM 역할 UUID 중복');
  return Object.fromEntries(Object.entries(record).map(([role, id]) => [role, String(id).toLowerCase()]));
}

// The mock IDs identify synthetic VMs only. They are never a fallback for a live deployment.
const mockBindings: VmBindings = {
  primary_drive: '11111111-1111-4111-8111-111111111111',
  backup_drive: '22222222-2222-4222-8222-222222222222',
  interaction: '33333333-3333-4333-8333-333333333333',
};

export function configuredBindings(): Partial<VmBindings> {
  if (import.meta.env.VITE_MOCK === 'true') return mockBindings;
  try {
    return parseVmBindings(import.meta.env.VITE_VM_ROLE_BINDINGS);
  } catch {
    return {};
  } // Invalid/missing deployment config means unknown, never a name-based guess.
}
