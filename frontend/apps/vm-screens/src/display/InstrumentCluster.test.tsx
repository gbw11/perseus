import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { InstrumentCluster } from './InstrumentCluster';
import { presentationFixture } from './fixture';
import type { Presentation } from './model';

const fresh = { status: 'fresh', age_ms: 50 } as const;

/** AUTO 로 VM1 이 주행 중인 표시 상태 + 제안 확장 필드 (vehicle, drive_apps) */
function driving(extra: { vehicle?: unknown; drive_apps?: unknown } = {}): Presentation {
  const p = presentationFixture();
  const [vm1, vm2] = p.state.vms;
  p.state.drive = {
    active_vm_id: vm1!.vm_id,
    mode: 'AUTO',
    ai_status: 'ok',
    source: fresh,
    authority: {
      selected_vm_id: vm1!.vm_id,
      selected_mode: 'AUTO',
      generation: 4,
      phase: 'APPLIED',
      output_permitted: true,
      storage_uncertain: false,
    },
  };
  const lifecycle = (phase: string, readiness: string) => ({
    profile: 'warm-drive-1',
    applied_generation: 4,
    check_seq: '1',
    progress_seq: '0',
    phase,
    readiness,
    reason: null,
  });
  Object.assign(p.state, {
    vehicle: {
      v_mps: 1.25,
      steer_rad: 0.1,
      flags: { estop: false, watchdog: false, obstacle: false },
      source: fresh,
    },
    drive_apps: {
      [vm1!.vm_id]: {
        autonomous: {
          heartbeat: { state: 'active', uptime_s: 10 },
          lifecycle: lifecycle('EXECUTING', 'READY'),
          command: { generation: 4, v_mps: 1.4, steer_rad: 0.1 },
          perception: { kind: 'clear', detail: null },
          source: fresh,
        },
        manual: null,
      },
      [vm2!.vm_id]: {
        autonomous: {
          heartbeat: { state: 'standby', uptime_s: 10 },
          lifecycle: lifecycle('WAITING', 'QUALIFIED'),
          command: null,
          perception: null,
          source: fresh,
        },
        manual: null,
      },
    },
    ...extra,
  });
  return p;
}

function show(p: Presentation, connected = true) {
  return render(<InstrumentCluster p={p} connected={connected} error={connected ? null : '끊김'} mapped />);
}

describe('InstrumentCluster', () => {
  it('차량 실측 속도·목표 속도·구동 VM·주행 앱 상태를 보인다', () => {
    show(driving());
    expect(screen.getByTestId('speed-value')).toHaveTextContent('4.5');
    expect(screen.getByLabelText('목표 속도')).toHaveTextContent('5.0');
    expect(screen.getByTestId('active-vm')).toHaveTextContent('VM1');
    expect(screen.getByTestId('drive-app-VM1')).toHaveTextContent('주행 계산 중');
    expect(screen.getByTestId('drive-app-VM2')).toHaveTextContent('대기 · 준비 완료');
    expect(screen.getByLabelText('자율주행 작동')).toBeInTheDocument();
  });

  it('차량 값이 신선하지 않으면 속도를 지어내지 않는다', () => {
    show(
      driving({
        vehicle: { v_mps: 3, steer_rad: 0, flags: null, source: { status: 'stale', age_ms: 4000 } },
      }),
    );
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.getByTestId('lamp-estop')).toHaveAttribute('data-unknown', 'true');
  });

  it('확장 필드가 없으면 앱 보고 미수신으로 둔다', () => {
    const p = driving();
    delete (p.state as Record<string, unknown>).drive_apps;
    delete (p.state as Record<string, unknown>).vehicle;
    show(p);
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.getByTestId('drive-app-VM1')).toHaveTextContent('앱 보고 미수신');
  });

  it('장애물은 경고등과 안내로 보인다', () => {
    const p = driving({
      vehicle: {
        v_mps: 0.2,
        steer_rad: 0,
        flags: { estop: false, watchdog: false, obstacle: true },
        source: fresh,
      },
    });
    show(p);
    expect(screen.getByTestId('lamp-obstacle')).toHaveAttribute('data-on', 'true');
    expect(screen.getByTestId('obstacle-note')).toBeInTheDocument();
  });

  it('VM1 장애 → 안전 정지: 구동 VM 없음, VM1 장애, 정지 모드 강조', () => {
    const p = driving();
    const [vm1] = p.state.vms;
    vm1!.xen_state = 'crashed';
    p.state.drive = {
      active_vm_id: null,
      mode: 'SAFE_STOP',
      ai_status: 'ok',
      source: fresh,
      authority: {
        selected_vm_id: vm1!.vm_id,
        selected_mode: 'SAFE_STOP',
        generation: 5,
        phase: 'APPLIED',
        output_permitted: false,
        storage_uncertain: false,
      },
    };
    show(p);
    expect(screen.getByTestId('active-vm')).toHaveTextContent('—');
    expect(screen.getByTestId('drive-app-VM1')).toHaveTextContent('VM 장애');
    const gears = screen.getByRole('list', { name: '주행 모드 선택' });
    expect(within(gears).getByText('정지')).toHaveAttribute('data-active', 'true');
    expect(screen.getByLabelText('자율주행 꺼짐')).toBeInTheDocument();
  });

  it('자율 재개 대기: 넘겨받을 VM 을 준비 중으로 보인다', () => {
    const p = driving();
    const [, vm2] = p.state.vms;
    p.state.drive = {
      active_vm_id: null,
      mode: 'AUTO_READY',
      ai_status: 'ok',
      source: fresh,
      authority: {
        selected_vm_id: vm2!.vm_id,
        selected_mode: 'AUTO_READY',
        generation: 6,
        phase: 'PENDING',
        output_permitted: false,
        storage_uncertain: false,
      },
    };
    show(p);
    expect(screen.getByTestId('active-vm')).toHaveTextContent('VM2 준비');
    expect(screen.getByText('구동 승계 준비 중')).toBeInTheDocument();
  });

  it('표시 연결이 끊기면 값을 지우고 알린다', () => {
    show(driving(), false);
    expect(screen.getByRole('alert')).toHaveTextContent('현재 값은 미확인');
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.getByTestId('drive-app-VM1')).toHaveTextContent('표시 연결 미확인');
  });
});
