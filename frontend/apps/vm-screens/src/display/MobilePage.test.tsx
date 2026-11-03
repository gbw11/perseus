import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MobilePage } from './MobilePage';
import { presentationFixture } from './fixture';
import { useDisplay } from './useDisplay';

vi.mock('./useDisplay', () => ({ useDisplay: vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

function feed(p = presentationFixture(), connected = true) {
  vi.mocked(useDisplay).mockReturnValue({
    presentation: p,
    connected,
    error: connected ? null : '연결 끊김',
  });
}

describe('mobile combined vehicle display', () => {
  it('combines both views without posting HDMI acknowledgement or adding controls', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    feed();
    render(<MobilePage />);
    for (const name of ['계기판', '인포테인먼트 · 게스트 데이터', '네비게이션', '음악']) {
      expect(
        screen.getByRole(name === '네비게이션' || name === '음악' ? 'article' : 'region', { name }),
      ).toBeInTheDocument();
    }
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.getByTestId('battery-value')).toHaveTextContent('—');
    expect(screen.getByTestId('vm-VM2')).toHaveTextContent('백업 · 미러링');
    expect(screen.getByTestId('vm-VM3')).toHaveTextContent('Debian');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('distinguishes saved mode, permitted output, disconnection and fresh recovery', () => {
    const p = presentationFixture();
    p.state.drive.mode = 'AUTO';
    p.state.drive.source = { status: 'fresh', age_ms: 0 };
    p.state.drive.authority = {
      selected_vm_id: p.state.vms[0]!.vm_id,
      selected_mode: 'AUTO',
      generation: 1,
      phase: 'PENDING',
      output_permitted: false,
      storage_uncertain: false,
    };
    feed(p);
    const view = render(<MobilePage />);
    expect(screen.getByText('자율 모드 선택 · 구동 준비 중')).toBeInTheDocument();
    expect(screen.queryByText('현재 구동 VM1')).not.toBeInTheDocument();
    p.state.drive.authority.phase = 'APPLIED';
    p.state.drive.authority.output_permitted = true;
    p.state.drive.active_vm_id = p.state.vms[0]!.vm_id;
    feed(p);
    view.rerender(<MobilePage />);
    expect(screen.getByText('현재 구동 VM1')).toBeInTheDocument();
    feed(p, false);
    view.rerender(<MobilePage />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('주행 모드 미확인')).toBeInTheDocument();
    expect(screen.getByTestId('vm-VM1')).toHaveTextContent('실행 미확인');
    expect(screen.queryByText('현재 구동 VM1')).not.toBeInTheDocument();
    feed(p);
    view.rerender(<MobilePage />);
    expect(screen.getByText('현재 구동 VM1')).toBeInTheDocument();
  });

  it('keeps stale sources unknown and renders confirmed faults without declaring recovery', () => {
    const p = presentationFixture();
    p.state.drive.mode = 'AUTO';
    p.state.drive.source.status = 'stale';
    p.state.vms[1]!.sources.xen.status = 'stale';
    p.decisions[p.state.vms[0]!.vm_id] = { kind: 'app_error', label: '앱 오류', tone: 'danger' };
    feed(p);
    render(<MobilePage />);
    expect(screen.getByText('주행 모드 미확인')).toBeInTheDocument();
    expect(screen.getByTestId('vm-VM2')).toHaveTextContent('실행 미확인');
    expect(screen.getByRole('status')).toHaveTextContent('VM1 · 앱 오류');
    expect(screen.queryByText('복구 완료')).not.toBeInTheDocument();
  });

  it('uses the policy guest without opening its supplied graphics URL', () => {
    const p = presentationFixture();
    p.outputs[1]!.vm_id = p.state.vms[2]!.vm_id;
    p.state.vms[2]!.screen_source = { kind: 'graphics', handle: 'https://untrusted.invalid' };
    feed(p);
    render(<MobilePage />);
    const guest = screen.getByRole('article', { name: '게스트 콘텐츠' });
    expect(guest).toHaveTextContent('VM3');
    expect(guest).toHaveTextContent('OS 보고 미수신');
    expect(document.querySelector('iframe,video')).toBeNull();
  });
});
