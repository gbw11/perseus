export type Role = 'primary_drive' | 'backup_drive' | 'interaction';
export type SourceStatus = 'fresh' | 'stale' | 'disconnected' | 'unknown';
export interface Source {
  status: SourceStatus;
  age_ms: number | null;
}
export interface DisplayVm {
  vm_id: string;
  role: Role;
  run_id: string | null;
  xen_state: string | null;
  app_state: string;
  agent_supported: boolean;
  os: { expected: string; observed: string | null; current_run_verified: boolean };
  sources: { xen: Source; app: Source; agent: Source; inventory: Source };
  screen_source: { kind: string; handle: string | null };
}
export interface Decision {
  kind: string;
  label: string;
  tone: 'muted' | 'warning' | 'danger' | 'ok';
}
export interface DriveAuthority {
  selected_vm_id: string;
  selected_mode: 'AUTO' | 'MANUAL' | 'AUTO_READY' | 'SAFE_STOP';
  generation: number;
  phase: 'DISARMED' | 'PENDING' | 'APPLIED' | 'FAILED';
  output_permitted: boolean;
  storage_uncertain: boolean;
}
export interface Presentation {
  schema_version: 1;
  type: 'display.presentation';
  policy_epoch: string;
  revision: number;
  render_revision: string;
  outputs: {
    output_id: 'monitor-1' | 'monitor-2';
    view: 'overview' | 'detail';
    screen: 'instrument' | 'infotainment';
    selection: 'pinned' | 'follow_active';
    vm_id: string | null;
    reason?: string;
  }[];
  decisions: Record<string, Decision>;
  state: {
    source_epoch: string;
    sequence: number;
    generated_at: string;
    mqtt_connected: boolean;
    vms: DisplayVm[];
    drive: {
      active_vm_id: string | null;
      mode: string;
      ai_status: string;
      source: Source;
      authority?: DriveAuthority | null;
    };
  };
}

const object = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const nullableText = (v: unknown) => v === null || typeof v === 'string';
const roles = ['primary_drive', 'backup_drive', 'interaction'];
function source(v: unknown): boolean {
  return (
    object(v) &&
    ['fresh', 'stale', 'disconnected', 'unknown'].includes(String(v.status)) &&
    (v.age_ms === null || (typeof v.age_ms === 'number' && Number.isFinite(v.age_ms) && v.age_ms >= 0))
  );
}

/** Reject partial/incompatible snapshots instead of turning missing fields into normal status. */
export function parsePresentation(v: unknown): Presentation {
  if (
    !object(v) ||
    v.schema_version !== 1 ||
    v.type !== 'display.presentation' ||
    typeof v.policy_epoch !== 'string' ||
    !Number.isSafeInteger(v.revision) ||
    typeof v.render_revision !== 'string' ||
    !object(v.state) ||
    typeof v.state.source_epoch !== 'string' ||
    !Number.isSafeInteger(v.state.sequence) ||
    typeof v.state.generated_at !== 'string' ||
    typeof v.state.mqtt_connected !== 'boolean' ||
    !Array.isArray(v.state.vms) ||
    v.state.vms.length !== 3 ||
    !object(v.decisions) ||
    !Array.isArray(v.outputs) ||
    v.outputs.length !== 2 ||
    !object(v.state.drive) ||
    !source(v.state.drive.source) ||
    !nullableText(v.state.drive.active_vm_id) ||
    typeof v.state.drive.mode !== 'string' ||
    typeof v.state.drive.ai_status !== 'string'
  ) {
    throw new Error('표시 상태 규격을 확인할 수 없습니다.');
  }
  const ids = new Set<string>();
  const seenRoles = new Set<string>();
  for (const vm of v.state.vms) {
    if (
      !object(vm) ||
      typeof vm.vm_id !== 'string' ||
      ids.has(vm.vm_id) ||
      !roles.includes(String(vm.role)) ||
      seenRoles.has(String(vm.role)) ||
      !nullableText(vm.run_id) ||
      !nullableText(vm.xen_state) ||
      typeof vm.app_state !== 'string' ||
      typeof vm.agent_supported !== 'boolean' ||
      !object(vm.os) ||
      typeof vm.os.expected !== 'string' ||
      vm.os.expected !== (vm.role === 'interaction' ? 'debian' : 'zephyr') ||
      !nullableText(vm.os.observed) ||
      typeof vm.os.current_run_verified !== 'boolean' ||
      !object(vm.sources) ||
      !['xen', 'app', 'agent', 'inventory'].every((key) =>
        source((vm.sources as Record<string, unknown>)[key]),
      ) ||
      !object(vm.screen_source) ||
      typeof vm.screen_source.kind !== 'string' ||
      !nullableText(vm.screen_source.handle)
    ) {
      throw new Error('VM 표시 데이터를 확인할 수 없습니다.');
    }
    ids.add(vm.vm_id);
    seenRoles.add(String(vm.role));
    const decision = v.decisions[vm.vm_id];
    if (
      !object(decision) ||
      typeof decision.kind !== 'string' ||
      typeof decision.label !== 'string' ||
      !['muted', 'warning', 'danger', 'ok'].includes(String(decision.tone))
    )
      throw new Error('표시 결정이 없습니다.');
  }
  for (const [index, output] of v.outputs.entries()) {
    if (
      !object(output) ||
      output.output_id !== `monitor-${index + 1}` ||
      output.view !== (index === 0 ? 'overview' : 'detail') ||
      output.screen !== (index === 0 ? 'instrument' : 'infotainment') ||
      !['pinned', 'follow_active'].includes(String(output.selection)) ||
      !(output.vm_id === null || ids.has(String(output.vm_id))) ||
      (index === 0 && output.vm_id !== null) ||
      (index === 1 && output.vm_id === null) ||
      (output.reason !== undefined && typeof output.reason !== 'string')
    )
      throw new Error('출력 매핑이 올바르지 않습니다.');
  }
  const drive = v.state.drive;
  const authority = drive.authority;
  if (authority !== undefined && authority !== null) {
    if (
      !object(authority) ||
      !v.state.vms.some((vm) => vm.vm_id === authority.selected_vm_id && vm.role !== 'interaction') ||
      !['AUTO', 'MANUAL', 'AUTO_READY', 'SAFE_STOP'].includes(String(authority.selected_mode)) ||
      !Number.isSafeInteger(authority.generation) ||
      (authority.generation as number) < 1 ||
      (authority.generation as number) > 0xffffffff ||
      !['DISARMED', 'PENDING', 'APPLIED', 'FAILED'].includes(String(authority.phase)) ||
      typeof authority.output_permitted !== 'boolean' ||
      typeof authority.storage_uncertain !== 'boolean' ||
      (drive.source as Record<string, unknown>).status !== 'fresh' ||
      (authority.storage_uncertain
        ? drive.mode !== 'unknown' || drive.active_vm_id !== null
        : drive.mode !== authority.selected_mode) ||
      (drive.active_vm_id !== null &&
        (drive.active_vm_id !== authority.selected_vm_id ||
          authority.phase !== 'APPLIED' ||
          !authority.output_permitted ||
          !['AUTO', 'MANUAL'].includes(String(drive.mode))))
    )
      throw new Error('Gateway 주행 선택·출력 허가를 확인할 수 없습니다.');
  }
  if (
    !['AUTO', 'MANUAL', 'AUTO_READY', 'SAFE_STOP', 'unknown'].includes(String(drive.mode)) ||
    (drive.active_vm_id !== null &&
      !v.state.vms.some((vm) => vm.vm_id === drive.active_vm_id && vm.role !== 'interaction')) ||
    ((drive.source as Record<string, unknown>).status !== 'fresh' &&
      (drive.active_vm_id !== null || drive.mode !== 'unknown'))
  )
    throw new Error('활성 구동 VM·주행 모드를 확인할 수 없습니다.');
  return v as unknown as Presentation;
}

export const ROLE = {
  primary_drive: { name: 'VM1', title: '주 구동', note: '자동 · 수동 주행' },
  backup_drive: { name: 'VM2', title: '백업 · 미러링', note: '구동 승계 대기 역할' },
  interaction: { name: 'VM3', title: '인터랙션', note: '사용자 인터랙션 앱' },
} satisfies Record<Role, { name: string; title: string; note: string }>;

export const SOURCE_LABEL: Record<SourceStatus, string> = {
  fresh: '수신 정상',
  stale: '수신 지연',
  disconnected: '연결 끊김',
  unknown: '미수신',
};

/** A live HTTP connection cannot keep a frozen collector snapshot alive. */
export class SnapshotOrder {
  private epoch: string | null = null;
  private sequence = -1;
  private retired = new Set<string>();
  accept(p: Presentation): boolean {
    const { source_epoch: epoch, sequence } = p.state;
    if (this.retired.has(epoch)) return false;
    if (epoch === this.epoch && sequence <= this.sequence) return false;
    if (epoch !== this.epoch && this.epoch !== null) this.retired.add(this.epoch);
    this.epoch = epoch;
    this.sequence = sequence;
    return true;
  }
}
