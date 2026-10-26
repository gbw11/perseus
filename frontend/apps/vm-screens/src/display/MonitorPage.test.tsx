import { render, screen } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router';
import { MonitorPage } from './MonitorPage';
import { presentationFixture } from './fixture';
import { useDisplay } from './useDisplay';

vi.mock('./useDisplay', () => ({ useDisplay: vi.fn() }));

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => undefined);
});
afterEach(() => vi.unstubAllGlobals());

function show(output = 'monitor-1', connected = true, p = presentationFixture()) {
  vi.mocked(useDisplay).mockReturnValue({
    presentation: p,
    connected,
    error: connected ? null : '수집 연결 실패',
  });
  return render(
    <MemoryRouter initialEntries={[`/display/${output}`]}>
      <Routes>
        <Route path="/display/:outputId" element={<MonitorPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('local HDMI screens', () => {
  it('does not carry a stale app error or current Xen claim into the facts panel', () => {
    const p = presentationFixture();
    p.state.vms[0]!.app_state = 'failed';
    p.state.vms[0]!.sources.app.status = 'stale';
    p.state.vms[0]!.sources.xen.status = 'disconnected';
    p.decisions[p.state.vms[0]!.vm_id] = {
      kind: 'stale',
      label: 'VM 상태 수신 지연 / 연결 끊김',
      tone: 'warning',
    };
    show('monitor-2', true, p);
    expect(screen.queryByText('failed')).not.toBeInTheDocument();
    expect(screen.getByText('Xen 상태 (마지막 관측)')).toBeInTheDocument();
  });
  it('shows two Zephyr roles and Debian without assigning manual-only to VM2', () => {
    show();
    expect(screen.getByRole('heading', { name: 'VM 전체 상태' })).toBeInTheDocument();
    expect(screen.getAllByText('Zephyr RTOS')).toHaveLength(2);
    expect(screen.getByText('Debian GNU/Linux')).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByTestId('card-VM2')).toHaveTextContent('백업 · 미러링');
  });
  it('keeps mode and active VM distinct', () => {
    const p = presentationFixture();
    p.state.drive = {
      active_vm_id: p.state.vms[0]!.vm_id,
      mode: 'MANUAL',
      ai_status: 'unknown',
      source: { status: 'fresh', age_ms: 0 },
    };
    show('monitor-1', true, p);
    expect(screen.getByText('MANUAL')).toBeInTheDocument();
    expect(screen.getByTestId('card-VM1')).toHaveTextContent('활성 구동');
    expect(screen.getByTestId('card-VM2')).not.toHaveTextContent('활성 구동');
  });
  it('removes live claims when the display connection fails', () => {
    const p = presentationFixture();
    p.state.drive = {
      active_vm_id: p.state.vms[0]!.vm_id,
      mode: 'AUTO',
      ai_status: 'unknown',
      source: { status: 'fresh', age_ms: 0 },
    };
    show('monitor-1', false, p);
    expect(screen.getByRole('alert')).toHaveTextContent('마지막 수신 값');
    expect(screen.queryByText('활성 구동')).not.toBeInTheDocument();
    expect(screen.queryByText('AUTO')).not.toBeInTheDocument();
  });
  it('uses the server selected UUID for detail, not its own role/mode guess', () => {
    const p = presentationFixture();
    p.outputs[1]!.vm_id = p.state.vms[1]!.vm_id;
    show('monitor-2', true, p);
    expect(screen.getByTestId('card-VM2')).toBeInTheDocument();
    expect(screen.queryByTestId('card-VM1')).not.toBeInTheDocument();
    expect(screen.getByText('선택 VM 유지')).toBeInTheDocument();
    expect(screen.getByText('현재는 상태 화면을 표시합니다.')).toBeInTheDocument();
  });
  it('renders a changed run without carrying a previous app visual', () => {
    const p = presentationFixture();
    p.state.vms[0]!.run_id = 'new-run-2';
    p.decisions[p.state.vms[0]!.vm_id] = { kind: 'crashed', label: 'VM 장애', tone: 'danger' };
    show('monitor-2', true, p);
    expect(screen.getByText('VM 장애')).toBeInTheDocument();
    expect(screen.getByText('new-run-2')).toBeInTheDocument();
  });
  it('explains why the last detail target is retained after control reports expire', () => {
    const p = presentationFixture();
    p.outputs[1]!.selection = 'follow_active';
    p.outputs[1]!.vm_id = p.state.vms[1]!.vm_id;
    p.outputs[1]!.reason = '활성 구동 VM 미확인 · 마지막 선택 유지';
    p.state.drive.source.status = 'stale';
    show('monitor-2', true, p);
    expect(screen.getByTestId('card-VM2')).toBeInTheDocument();
    expect(screen.getByText('활성 구동 VM 미확인 · 마지막 선택 유지')).toBeInTheDocument();
    expect(screen.queryByText('활성 구동')).not.toBeInTheDocument();
  });
  it('does not present a previous selection reason as current after display connection loss', () => {
    const p = presentationFixture();
    p.outputs[1]!.reason = '제어 담당이 보고한 활성 구동 VM 따라가기';
    show('monitor-2', false, p);
    expect(screen.queryByText(p.outputs[1]!.reason)).not.toBeInTheDocument();
    expect(screen.getByText('화면 연결 끊김 · 마지막 화면 선택 유지')).toBeInTheDocument();
  });
});
