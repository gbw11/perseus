import type { DisplayVm, Presentation, Role } from './model';

/** Test-only fixture, not selected by runtime URL/environment switches. */
export function presentationFixture(): Presentation {
  const roles: Role[] = ['primary_drive', 'backup_drive', 'interaction'];
  const vms: DisplayVm[] = roles.map((role, index) => ({
    vm_id: `00000000-0000-4000-8000-00000000000${index + 1}`,
    role,
    run_id: `run-${index}`,
    xen_state: 'running',
    app_state: 'unknown',
    agent_supported: index === 2,
    os: { expected: index === 2 ? 'debian' : 'zephyr', observed: null, current_run_verified: false },
    sources: {
      xen: { status: 'fresh', age_ms: 10 },
      app: { status: 'unknown', age_ms: null },
      agent: { status: 'unknown', age_ms: null },
      inventory: { status: 'unknown', age_ms: null },
    },
    screen_source: { kind: 'status', handle: null },
  }));
  return {
    schema_version: 1,
    type: 'display.presentation',
    policy_epoch: 'epoch-1',
    revision: 1,
    render_revision: 'render-1',
    outputs: [
      { output_id: 'monitor-1', view: 'overview', selection: 'pinned', vm_id: null },
      { output_id: 'monitor-2', view: 'detail', selection: 'pinned', vm_id: vms[0]!.vm_id },
    ],
    decisions: Object.fromEntries(
      vms.map((vm) => [
        vm.vm_id,
        { kind: 'app_waiting', label: 'VM 실행 중 · 앱 상태 미확인', tone: 'warning' },
      ]),
    ),
    state: {
      source_epoch: 'feed-1',
      sequence: 1,
      generated_at: '2026-10-22T00:00:00Z',
      mqtt_connected: false,
      vms,
      drive: {
        active_vm_id: null,
        mode: 'unknown',
        ai_status: 'unknown',
        source: { status: 'unknown', age_ms: null },
      },
    },
  };
}
