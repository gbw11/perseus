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
  return render(monitorTree(output));
}

function monitorTree(output: string) {
  return (
    <MemoryRouter initialEntries={[`/display/${output}`]}>
      <Routes>
        <Route path="/display/:outputId" element={<MonitorPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('vehicle HDMI screens', () => {
  describe.each(['monitor-1', 'monitor-2'])('%s Gateway drive context', (output) => {
    it('shows saved AUTO selection as preparing until output is permitted and clears it on disconnect', () => {
      const p = presentationFixture();
      p.state.drive.mode = 'AUTO';
      p.state.drive.source = { status: 'fresh', age_ms: 100 };
      p.state.drive.authority = {
        selected_vm_id: p.state.vms[0]!.vm_id,
        selected_mode: 'AUTO',
        generation: 4,
        phase: 'DISARMED',
        output_permitted: false,
        storage_uncertain: false,
      };
      const view = show(output, true, p);
      expect(screen.getByText('자율 모드 선택 · 구동 준비 중')).toBeInTheDocument();
      expect(screen.queryByText('현재 구동 VM1')).not.toBeInTheDocument();
      const applied = structuredClone(p);
      applied.state.drive.authority!.phase = 'APPLIED';
      applied.state.drive.authority!.output_permitted = true;
      applied.state.drive.active_vm_id = p.state.vms[0]!.vm_id;
      vi.mocked(useDisplay).mockReturnValue({ presentation: applied, connected: true, error: null });
      view.rerender(monitorTree(output));
      expect(screen.getByText('자율주행')).toBeInTheDocument();
      expect(screen.queryByText('자율 모드 선택 · 구동 준비 중')).not.toBeInTheDocument();
      vi.mocked(useDisplay).mockReturnValue({ presentation: applied, connected: false, error: '끊김' });
      view.rerender(monitorTree(output));
      expect(screen.getByText('주행 모드 미확인')).toBeInTheDocument();
      expect(screen.queryByText('자율주행')).not.toBeInTheDocument();
    });
  });
  describe.each(['monitor-1', 'monitor-2'])('%s state transitions', (output) => {
    it.each([
      ['stopped', '정지', 'muted'],
      ['paused', '일시 정지', 'warning'],
      ['operation', 'VM 작업 진행 중', 'warning'],
      ['waiting', '상태 수신 대기', 'muted'],
      ['stale', 'VM 상태 수신 지연 / 연결 끊김', 'warning'],
    ] as const)('shows %s and removes its notice only after a normal decision', (kind, label, tone) => {
      const p = presentationFixture();
      const vm = p.state.vms[0]!;
      vm.xen_state = kind === 'stopped' || kind === 'paused' ? kind : 'running';
      p.decisions[vm.vm_id] = { kind, label, tone };
      const view = show(output, true, p);
      expect(screen.getByRole('status')).toHaveTextContent(`VM1 · ${label}`);
      expect(screen.queryByText('복구 완료')).not.toBeInTheDocument();
      if (output === 'monitor-2' && (kind === 'stopped' || kind === 'paused')) {
        expect(screen.getByTestId('vm-VM1')).toHaveAttribute('data-running', 'false');
      }
      const normal = presentationFixture();
      normal.state.vms[0]!.app_state = 'ready';
      normal.state.vms[0]!.sources.app.status = 'fresh';
      normal.decisions[vm.vm_id] = { kind: 'status', label: '앱 상태 수신 정상', tone: 'ok' };
      vi.mocked(useDisplay).mockReturnValue({ presentation: normal, connected: true, error: null });
      view.rerender(monitorTree(output));
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
      expect(screen.queryByText(`VM1 · ${label}`)).not.toBeInTheDocument();
      if (output === 'monitor-2') {
        expect(screen.getByTestId('vm-VM1')).toHaveAttribute('data-running', 'true');
        expect(screen.getByRole('article', { name: '게스트 콘텐츠' })).toHaveTextContent('앱 상태 수신 정상');
      }
    });
  });
  it('separates configured OS from a missing OS report despite fresh app heartbeat', () => {
    const p = presentationFixture();
    p.state.vms[0]!.sources.app.status = 'fresh';
    show('monitor-2', true, p);
    expect(screen.getByTestId('guest-os')).toHaveTextContent('설정 OS: Zephyr · OS 보고 미수신');
  });
  it('shows only the selected guest OS report and keeps unverified run binding explicit', () => {
    const p = presentationFixture();
    p.outputs[1]!.vm_id = p.state.vms[2]!.vm_id;
    p.state.vms[2]!.os.observed = 'debian';
    p.state.vms[2]!.sources.inventory.status = 'fresh';
    show('monitor-2', true, p);
    expect(screen.getByTestId('guest-os')).toHaveTextContent(
      '설정 OS: Debian · OS 보고: Debian · 실행 연결 미검증',
    );
    expect(screen.getByTestId('guest-os')).not.toHaveTextContent('Zephyr');
  });
  it.each(['stale', 'disconnected'] as const)('does not present %s OS inventory as current', (status) => {
    const p = presentationFixture();
    p.state.vms[0]!.os.observed = 'zephyr';
    p.state.vms[0]!.sources.inventory.status = status;
    show('monitor-2', true, p);
    expect(screen.getByTestId('guest-os')).toHaveTextContent('OS 보고 지연 · 현재 OS 미확인');
    expect(screen.getByTestId('guest-os')).not.toHaveTextContent('OS 보고: Zephyr');
  });
  it('withholds the current OS report when display transport is disconnected', () => {
    const p = presentationFixture();
    p.state.vms[0]!.os.observed = 'zephyr';
    p.state.vms[0]!.sources.inventory.status = 'fresh';
    show('monitor-2', false, p);
    expect(screen.getByTestId('guest-os')).toHaveTextContent('OS 보고 연결 미확인');
    expect(screen.getByTestId('guest-os')).not.toHaveTextContent('OS 보고: Zephyr');
  });
  it('does not carry a stale app error or current Xen claim into the vehicle screen', () => {
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
    expect(screen.getByTestId('vm-VM1')).toHaveTextContent('실행 미확인');
    expect(screen.getByRole('article', { name: '게스트 콘텐츠' })).not.toHaveTextContent('앱 상태 수신 정상');
  });
  it('shows an instrument cluster with unknown sensor values rather than a VM dashboard', () => {
    show();
    expect(screen.getByRole('heading', { name: '드라이버 계기판' })).toBeInTheDocument();
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.queryByTestId('battery-value')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'VM 전체 상태' })).not.toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });
  it('shows manual mode without treating the backup role as manual driving', () => {
    const p = presentationFixture();
    p.state.drive = {
      active_vm_id: p.state.vms[0]!.vm_id,
      mode: 'MANUAL',
      ai_status: 'unknown',
      source: { status: 'fresh', age_ms: 0 },
    };
    show('monitor-2', true, p);
    expect(screen.getByText('수동주행')).toBeInTheDocument();
    expect(screen.getByText('현재 구동 VM1')).toBeInTheDocument();
    expect(screen.getByTestId('vm-VM2')).toHaveTextContent('백업 · 미러링');
    expect(screen.getByRole('article', { name: '네비게이션' })).toBeInTheDocument();
  });
  it('does not show stale mode or running VMs when display connection fails', () => {
    const p = presentationFixture();
    p.state.drive.mode = 'AUTO';
    p.state.drive.source.status = 'fresh';
    show('monitor-2', false, p);
    expect(screen.getByRole('alert')).toHaveTextContent('현재 값은 미확인');
    expect(screen.queryByText('자율주행')).not.toBeInTheDocument();
    expect(screen.getByTestId('vm-VM1')).toHaveTextContent('실행 미확인');
    expect(screen.getByRole('article', { name: '게스트 콘텐츠' })).toHaveTextContent('표시 중단');
  });
  it('uses server selected guest and shows no arbitrary playable graphics URL', () => {
    const p = presentationFixture();
    p.outputs[1]!.vm_id = p.state.vms[2]!.vm_id;
    p.state.vms[2]!.screen_source = { kind: 'graphics', handle: 'https://untrusted.invalid/view' };
    show('monitor-2', true, p);
    expect(screen.getByRole('article', { name: '게스트 콘텐츠' })).toHaveTextContent('VM3');
    expect(screen.getByText('게스트 영상 어댑터 연결 대기')).toBeInTheDocument();
    expect(document.querySelector('iframe,video')).toBeNull();
    expect(screen.queryByText('수신 근거')).not.toBeInTheDocument();
  });
  it('reports confirmed faults and work without declaring recovery success', () => {
    const p = presentationFixture();
    p.decisions[p.state.vms[0]!.vm_id] = { kind: 'crashed', label: 'VM 장애', tone: 'danger' };
    show('monitor-2', true, p);
    expect(screen.getByRole('status')).toHaveTextContent('VM1 · VM 장애');
    expect(screen.queryByText('복구 완료')).not.toBeInTheDocument();
  });
  it('suppresses stale Xen running indicators independently from the HTTP connection', () => {
    const p = presentationFixture();
    p.state.vms[1]!.sources.xen.status = 'stale';
    show('monitor-2', true, p);
    expect(screen.getByTestId('vm-VM2')).toHaveAttribute('data-running', 'false');
    expect(screen.getByTestId('vm-VM2')).toHaveTextContent('실행 미확인');
  });
  it('explains why the last detail target is retained after control reports expire', () => {
    const p = presentationFixture();
    p.outputs[1]!.selection = 'follow_active';
    p.outputs[1]!.vm_id = p.state.vms[1]!.vm_id;
    p.outputs[1]!.reason = '활성 구동 VM 미확인 · 마지막 선택 유지';
    p.state.drive.source.status = 'stale';
    show('monitor-2', true, p);
    expect(screen.getByRole('article', { name: '게스트 콘텐츠' })).toHaveTextContent('VM2');
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
