import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RuntimeLogsPanel } from './RuntimeLogsPanel';
import { fetchLogs, fetchLogSources, type LogPage } from '../api/logs';

vi.mock('../api/logs', async (original) => ({
  ...(await original<typeof import('../api/logs')>()),
  fetchLogs: vi.fn(),
  fetchLogSources: vi.fn(),
}));
function page(message = '<script>plain log</script>', epoch = 'one'): LogPage {
  return {
    epoch,
    next_cursor: 1,
    has_more: false,
    history_truncated: false,
    items: [
      {
        id: 1,
        event_id: 'one',
        source_id: 'dom0',
        vm_id: null,
        component: 'api.service',
        boot_id: 'boot',
        observed_at: '2026-10-28T00:00:00Z',
        received_at: 1,
        level: 'INFO',
        message,
      },
    ],
  };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(fetchLogs).mockResolvedValue(page());
  vi.mocked(fetchLogSources).mockResolvedValue({ epoch: 'one', items: [] });
});
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe('execution logs beside the terminal', () => {
  it('renders plain text and retains records while paused', async () => {
    await act(async () => {
      render(<RuntimeLogsPanel />);
    });
    expect(screen.getByLabelText('수집된 로그')).toHaveTextContent('<script>plain log</script>');
    expect(document.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByLabelText('조회 일시정지'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(fetchLogs).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('수집된 로그')).toHaveTextContent('plain log');
  });
  it('marks existing records as old when polling fails', async () => {
    await act(async () => {
      render(<RuntimeLogsPanel />);
    });
    vi.mocked(fetchLogs).mockRejectedValue(new Error('offline'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole('status')).toHaveTextContent('연결 끊김 · 이전 기록');
    expect(screen.getByLabelText('수집된 로그')).toHaveTextContent('plain log');
  });
  it('resets the cursor after a replaced log store', async () => {
    await act(async () => {
      render(<RuntimeLogsPanel />);
    });
    vi.mocked(fetchLogs).mockResolvedValue(page('new history', 'two'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole('alert')).toHaveTextContent('기록 저장소 변경');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(vi.mocked(fetchLogs).mock.lastCall?.[0].after).toBeUndefined();
    expect(screen.getByLabelText('수집된 로그')).toHaveTextContent('new history');
  });
});
