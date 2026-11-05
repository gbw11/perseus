import { act, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { memoryFixture } from '../domain/memoryContractFixtures';
import { AppMemoryPools } from './AppMemoryPools';
import { ManagedRamSummary } from './ManagedRamSummary';

const NOW = Date.parse('2026-11-04T10:00:00Z');

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('RAM summary and grouped buffer panels', () => {
  it('shows a recent same-payload observation and ages it without more network requests', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const sample = memoryFixture('ram-committed');
    const page = render(<ManagedRamSummary vmId={sample.vm_id} vmName="Zephyr A" samples={[sample]} />);
    const card = within(screen.getByRole('region', { name: 'Zephyr A 메모리 사용률' }));
    expect(card.getByText('25.000%')).toBeInTheDocument();
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    expect(card.getByText('0.004 MiB')).toHaveAttribute('title', '4096 bytes');
    expect(card.getByText('여유')).toBeInTheDocument();
    const timers = vi.getTimerCount();
    act(() => {
      vi.advanceTimersByTime(6001);
    });
    expect(card.getByText('관측 만료 · 최근 관측값')).toBeInTheDocument();
    expect(card.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '최근 관측 25.000% · 관측 만료');
    expect(card.getByText('25.000%')).toBeInTheDocument();
    expect(card.getByText('관측 당시 여유')).toBeInTheDocument();
    expect(card.getByRole('img', { name: 'Zephyr A 메모리 사용률 관측 이력' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(card.queryByText('25.000%')).not.toBeInTheDocument();
    expect(card.getByText('관리 RAM 관측 만료')).toBeInTheDocument();
    page.unmount();
    expect(vi.getTimerCount()).toBe(timers - 1);
  });

  it('displays the latest valid observed RAM and its original observation time without another query', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const sample = memoryFixture('ram-committed');
    render(<ManagedRamSummary vmId={sample.vm_id} vmName="Zephyr A" samples={[sample]} />);
    const card = within(screen.getByRole('region', { name: 'Zephyr A 메모리 사용률' }));
    expect(card.getByText('25.000%')).toBeInTheDocument();
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    expect(card.getByText(/최근 관측 · 수신 기준 추정/)).toBeInTheDocument();
    expect(card.getByText((_, node) => node?.tagName === 'TIME')).toHaveAttribute(
      'datetime',
      new Date(NOW - 2).toISOString(),
    );
    expect(card.getByText('원 표본 관측 이력')).toBeInTheDocument();
  });

  it('keeps valid zero distinct from unsupported RAM and retains the shared display units', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const zero = memoryFixture('ram-valid-zero');
    const page = render(<ManagedRamSummary vmId={zero.vm_id} vmName="Zephyr A" samples={[zero]} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('0.000%')).toBeInTheDocument();
    const unavailable = memoryFixture('ram-unsupported');
    page.rerender(<ManagedRamSummary vmId={unavailable.vm_id} vmName="Zephyr A" samples={[unavailable]} />);
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(screen.queryByText('0.000%')).not.toBeInTheDocument();
  });

  it('groups automatic/common/manual views and distinguishes stored expired input from an empty slot', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const input = memoryFixture('manual-memory-input-expired');
    render(
      <AppMemoryPools vmId={input.vm_id} vmName="Zephyr A" samples={[input]}>
        <section aria-label="shared fixture">공통 힙과 네트워크</section>
      </AppMemoryPools>,
    );
    for (const group of ['자동', '공통', '수동'])
      expect(screen.getByRole('region', { name: `Zephyr A ${group} 메모리` })).toBeInTheDocument();
    const card = within(screen.getByRole('region', { name: 'Zephyr A 조작 입력' }));
    expect(card.getByText('입력 보관')).toBeInTheDocument();
    expect(card.getByText('입력: 만료')).toHaveAttribute('title', 'input_status=expired, source_rc=-62');
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    expect(card.getByText(/원 입력 순번 0/)).toBeInTheDocument();
    const pending = within(screen.getByRole('region', { name: 'Zephyr A 대기 작업' }));
    expect(pending.getByText('미수신')).toBeInTheDocument();
    expect(pending.queryByText('비어 있음')).not.toBeInTheDocument();
    expect(screen.getByText('공통 힙과 네트워크')).toBeInTheDocument();
    expect(screen.queryByText('스레드 스택 스냅샷')).not.toBeInTheDocument();
  });

  it('shows pending/running/result phases separately and retains result ownership after read and revoke', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const names = [
      'manual-memory-pending-full',
      'manual-memory-running-full',
      'manual-memory-result-revoked',
    ];
    const samples = names.map((name) => memoryFixture(name));
    render(<AppMemoryPools vmId={samples[0]!.vm_id} vmName="Zephyr A" samples={samples} />);
    expect(
      within(screen.getByRole('region', { name: 'Zephyr A 대기 작업' })).getByText('작업 대기'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('region', { name: 'Zephyr A 실행 작업' })).getByText('계산 실행 중'),
    ).toBeInTheDocument();
    const result = within(screen.getByRole('region', { name: 'Zephyr A 결과 보관' }));
    expect(result.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(result.getByText('계산 수행 보고 있음')).toBeInTheDocument();
    expect(result.getByText('이 결과 읽음 · 사본 보관 유지')).toBeInTheDocument();
    expect(result.getByText('철회 관측 · 반환 여부는 점유로 확인')).toBeInTheDocument();
    expect(result.queryByText('READY')).not.toBeInTheDocument();
  });
});
