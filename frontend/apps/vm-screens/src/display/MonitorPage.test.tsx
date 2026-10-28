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

describe('vehicle HDMI screens', () => {
  it('shows an instrument cluster with unknown sensor values rather than a VM dashboard', () => {
    show();
    expect(screen.getByRole('heading', { name: '드라이버 계기판' })).toBeInTheDocument();
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.getByTestId('battery-value')).toHaveTextContent('—');
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
});
