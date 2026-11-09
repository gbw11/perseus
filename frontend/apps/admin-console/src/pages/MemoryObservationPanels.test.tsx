import { act, fireEvent, render, screen, within } from '@testing-library/react';
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
    // 메모리 사용률은 카드 첫 섹션 요약: 큰 제목·%, 사용량 / 전체, 여유, 막대
    const section = screen.getByRole('region', { name: 'Zephyr A 메모리 사용률' });
    const card = within(section);
    expect(card.getByText('메모리 사용률')).toBeInTheDocument();
    expect(card.getByText('25.0%')).toBeInTheDocument();
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    expect(card.getByText('0.004 MiB')).toBeInTheDocument();
    expect(card.getByText('여유')).toBeInTheDocument();
    expect(section.querySelector('[title]')).toBeNull();
    expect(card.queryByText('관측 만료 · 최근 관측값')).not.toBeInTheDocument();
    const timers = vi.getTimerCount();
    act(() => {
      vi.advanceTimersByTime(6001);
    });
    expect(card.getByText('관측 만료 · 최근 관측값')).toHaveAttribute('data-tone', 'warn');
    expect(card.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '최근 관측 25.0% · 관측 만료');
    expect(card.getByText('25.0%')).toBeInTheDocument();
    expect(card.getByText('관측 당시 여유')).toBeInTheDocument();
    expect(card.queryByRole('img')).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(card.queryByText('25.0%')).not.toBeInTheDocument();
    expect(card.getByText('관리 RAM 관측 만료')).toBeInTheDocument();
    page.unmount();
    expect(vi.getTimerCount()).toBe(timers - 1);
  });

  it('shows the latest valid RAM as a summary only, without guidance text, time line or history chart', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const sample = memoryFixture('ram-committed');
    render(<ManagedRamSummary vmId={sample.vm_id} vmName="Zephyr A" samples={[sample]} />);
    const card = within(screen.getByRole('region', { name: 'Zephyr A 메모리 사용률' }));
    expect(card.getByText('25.0%')).toBeInTheDocument();
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    // S15P31E110-409: 관측 시각은 카드 제목의 "마지막 갱신"으로 옮겼다
    for (const text of ['정적 예약 포함', /최근 관측/, '원 표본 관측 이력', /메모리 사용률 · 최근 60초/])
      expect(card.queryByText(text)).not.toBeInTheDocument();
    expect(card.queryByText((_, node) => node?.tagName === 'TIME')).not.toBeInTheDocument();
    expect(card.queryByRole('img')).not.toBeInTheDocument();
  });

  it('keeps valid zero distinct from unsupported RAM and retains the shared display units', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const zero = memoryFixture('ram-valid-zero');
    const page = render(<ManagedRamSummary vmId={zero.vm_id} vmName="Zephyr A" samples={[zero]} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('0.0%')).toBeInTheDocument();
    const unavailable = memoryFixture('ram-unsupported');
    page.rerender(<ManagedRamSummary vmId={unavailable.vm_id} vmName="Zephyr A" samples={[unavailable]} />);
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
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
    const card = within(screen.getByRole('group', { name: 'Zephyr A 조작 입력' }));
    // 접힌 상태: ⚠ 아이콘만 (문제 내용은 aria-label). 단계·문제 문구는 펼친 내용에
    expect(card.getByRole('img')).toHaveAccessibleName('이상: 입력 만료, 입력·작업 오류 -62');
    expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    fireEvent.click(card.getByRole('button'));
    expect(card.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    expect(card.getByText('단계').nextElementSibling).toHaveTextContent('입력 보관');
    // 한글 상태 문구는 일반 글꼴 (숫자 값만 고정폭)
    expect(card.getByText('단계').parentElement).toHaveAttribute('data-kind', 'text');
    expect(card.getByText('정적 예약').parentElement).not.toHaveAttribute('data-kind');
    expect(card.getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '입력 만료',
      '입력·작업 오류 -62',
    ]);
    expect(card.queryByText(/원 입력 순번/)).not.toBeInTheDocument();
    const pending = within(screen.getByRole('group', { name: 'Zephyr A 대기 작업' }));
    expect(pending.getAllByText('—').length).toBeGreaterThan(0);
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
    // 단계는 펼친 내용의 "단계" 칸
    for (const [name, phase] of [
      ['대기 작업', '작업 대기'],
      ['실행 작업', '계산 실행 중'],
    ]) {
      const row = within(screen.getByRole('group', { name: `Zephyr A ${name}` }));
      fireEvent.click(row.getByRole('button'));
      expect(row.getByText('단계').nextElementSibling).toHaveTextContent(phase!);
    }
    const result = within(screen.getByRole('group', { name: 'Zephyr A 결과 보관' }));
    expect(result.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    // 계산 수행·읽음 여부는 평소 숨기고, 철회는 이상 상태라 주의 색으로 보인다
    expect(result.queryByText(/계산 수행 보고/)).not.toBeInTheDocument();
    expect(result.queryByText(/결과 읽음|읽기 보고 없음/)).not.toBeInTheDocument();
    // 철회는 접힌 상태에서 ⚠ 아이콘(aria-label), 펼친 내용에 주의 색 문구
    expect(result.getByRole('img')).toHaveAccessibleName(/철회 관측/);
    expect(result.getByText('철회 관측 · 반환 여부는 점유로 확인', { selector: 'li' })).toBeInTheDocument();
    expect(result.queryByText('READY')).not.toBeInTheDocument();
  });

  it('does not show the phase when a manual slot is empty', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const empty = memoryFixture('manual-memory-pending-empty');
    render(<AppMemoryPools vmId={empty.vm_id} vmName="Zephyr A" samples={[empty]} />);
    const pending = within(screen.getByRole('group', { name: 'Zephyr A 대기 작업' }));
    fireEvent.click(pending.getByRole('button'));
    expect(pending.getByText('사용 / 용량')).toBeVisible();
    expect(pending.queryByText('단계')).not.toBeInTheDocument();
    expect(pending.queryByText('비어 있음')).not.toBeInTheDocument();
  });
});
