import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MetricSample } from '../api/types';
import { AppMemoryPools } from './AppMemoryPools';

const NOW = Date.parse('2026-11-04T10:00:00Z');
function fixture(seq: number, used: number, peak = used, capacityFailures = 1): MetricSample {
  const fields = {
    reserved_bytes: 12288,
    used_bytes: used,
    free_bytes: 8192 - used,
    capacity_bytes: 8192,
    high_water_bytes: peak,
    min_free_bytes: 8192 - peak,
    capacity_failure_total: capacityFailures,
    contention_total: 0,
  };
  return {
    vm_id: 'vm-a',
    sample_id: `sample-${seq}`,
    boot_id: 'boot-a',
    source: 'zephyr_telemetry',
    observed_at: new Date(NOW - (5 - seq) * 1000).toISOString(),
    received_at: new Date(NOW - (5 - seq) * 1000).toISOString(),
    telemetry: {
      family: 'app_memory',
      publisher_instance_id: 'publisher-a',
      source_sample_seq: String(seq),
      observed_uptime_ms: String(seq * 1000 - 100),
      sent_uptime_ms: String(seq * 1000),
      sent_at: null,
      utc_quality: 'unavailable',
      utc_source: null,
      uncertainty_ms: null,
      collection: {
        sample_id: `source-${seq}`,
        start_uptime_ms: String(seq * 1000 - 50),
        end_uptime_ms: String(seq * 1000),
        elapsed_us: '50',
        status: 'ok',
        source_rc: 0,
      },
      context: { binding: 'bound', host_boot_id: 'host-a', execution_id: 'exec-a', receipt_id: 'receipt-a' },
    },
    values: Object.entries(fields).map(([name, value]) => ({
      name: `memory.app.${name}`,
      value,
      raw_value: String(value),
      status: 'ok',
      unavailable_reason: null,
      source_rc: 0,
      labels: { region: 'drive_maps' },
      unit: name.endsWith('_total') ? 'count' : 'bytes',
    })),
  };
}
function show(samples: MetricSample[]) {
  vi.spyOn(Date, 'now').mockReturnValue(NOW);
  return render(<AppMemoryPools vmId="vm-a" vmName="Zephyr A" samples={samples} />);
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** 영역 행(아코디언)과 펼친 내용 */
const rowOf = (name: string) => screen.getByRole('group', { name: `Zephyr A ${name}` });
const toggleOf = (name: string) => within(rowOf(name)).getByRole('button');
const panelOf = (name: string) => document.getElementById(toggleOf(name).getAttribute('aria-controls')!)!;
/** 행을 펼치고 펼친 내용의 "라벨 → 값" 을 돌려준다 */
function expand(name: string): Record<string, string> {
  const toggle = toggleOf(name);
  if (toggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(toggle);
  return Object.fromEntries(
    [...panelOf(name).querySelectorAll('dl > div')].map((item) => [
      item.querySelector('dt')!.textContent,
      item.querySelector('dd')!.textContent,
    ]),
  );
}
const notesOf = (name: string) =>
  [...panelOf(name).querySelectorAll('small')].map((note) => note.textContent);
/** 펼친 내용의 주의 색 이상 신호 문구 */
const problemsOf = (name: string) =>
  [...panelOf(name).querySelectorAll('li')].map((item) => item.textContent);
/** 접힌 행의 ⚠ 아이콘 (문제 내용은 aria-label) */
const alertOf = (name: string) => within(rowOf(name)).queryByRole('img');

describe('app memory panels', () => {
  it('ages an unchanged sample without more network requests and cleans up its timer', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const page = render(
      <AppMemoryPools vmId="vm-a" vmName="Zephyr A" samples={[fixture(4, 4096, 4096, 0)]} />,
    );
    const maps = within(rowOf('지도 풀'));
    expect(maps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    // 정상 관측이면 행 끝 신호 없이 이름·막대·% 만
    expect(alertOf('지도 풀')).toBeNull();
    expect(expand('지도 풀')['현재 여유']).toBe('4.00 KiB');
    const timers = vi.getTimerCount();
    act(() => vi.advanceTimersByTime(6000));
    expect(maps.getByText('50.0%')).toBeInTheDocument();
    expect(maps.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(alertOf('지도 풀')).toHaveAccessibleName('이상: 관측 만료 · 최근 관측값');
    expect(expand('지도 풀')['관측 당시 여유']).toBe('4.00 KiB');
    expect(problemsOf('지도 풀')).toEqual(['관측 만료 · 최근 관측값']);
    act(() => {
      vi.advanceTimersByTime(55_000);
    });
    expect(maps.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(maps.getByText('—')).toBeInTheDocument();
    expect(notesOf('지도 풀')).toEqual(['최근 60초 표본 없음']);
    page.unmount();
    expect(vi.getTimerCount()).toBe(timers - 1);
  });

  it('shows one collapsible row per region with usage, an alert slot and expandable details', () => {
    show([fixture(1, 6144), fixture(2, 0, 6144)]);
    const row = rowOf('지도 풀');
    const maps = within(row);
    expect(maps.getByText('지도 풀')).toBeInTheDocument();
    expect(maps.getByText('0.0%')).toBeInTheDocument();
    expect(maps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    // 최고 점유 눈금 없음 (최고 점유는 펼친 내용의 숫자로만)
    expect(row.querySelector('b')).toBeNull();
    // 툴팁 없음
    expect(row.querySelector('[title]')).toBeNull();
    // 평소 접힘: 펼침 표시 ▸, 세부 내용 숨김
    const toggle = toggleOf('지도 풀');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(panelOf('지도 풀')).not.toBeVisible();
    expect(maps.getByText('▸')).toBeInTheDocument();
    // 공간 부족은 접힌 상태에서 ⚠ 아이콘만 (내용은 aria-label), 글자는 없다
    expect(alertOf('지도 풀')).toHaveTextContent('⚠');
    expect(alertOf('지도 풀')).toHaveAccessibleName('이상: 공간 부족 1회');
    expect(maps.queryByText(/공간 부족 1/)).not.toBeVisible();
    // 펼치면 세부 항목 + 주의 색 이상 신호 문구
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(panelOf('지도 풀')).toBeVisible();
    expect(maps.getByText('▾')).toBeInTheDocument();
    expect(expand('지도 풀')).toEqual({
      '사용 / 용량': '0 KiB / 8.00 KiB',
      '현재 여유': '8.00 KiB',
      '최고 점유 · 부팅 후': '6.00 KiB',
      '정적 예약': '12.00 KiB',
    });
    expect(problemsOf('지도 풀')).toEqual(['공간 부족 1회']);
    // 정적 예약 설명 문장은 없다
    expect(notesOf('지도 풀')).toEqual([]);
    expect(within(panelOf('지도 풀')).queryByText(/영역 구조체/)).not.toBeInTheDocument();
    expect(within(panelOf('지도 풀')).queryByText(/최소 여유|경합 누계|원 관측/)).not.toBeInTheDocument();
    // 다시 누르면 접힘. 여러 행을 함께 펼칠 수 있다
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    fireEvent.click(toggleOf('경로 풀'));
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggleOf('경로 풀')).toHaveAttribute('aria-expanded', 'true');
    // 값이 없는 영역은 막대를 비우고 "—", 사유는 펼친 내용에
    const paths = within(rowOf('경로 풀'));
    expect(paths.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(paths.getByText('—')).toBeInTheDocument();
    expect(notesOf('경로 풀')).toEqual(['미수신']);
    // 그래프(svg)는 없다. ⚠ 아이콘은 공간 부족이 있는 지도 풀 하나뿐
    expect(document.querySelector('svg')).toBeNull();
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByText('메모리 상세')).toBeInTheDocument();
  });

  it('hides the capacity failure signal while it is zero and colours the bar by the Pool thresholds', () => {
    const page = show([fixture(1, 2048, 2048, 0)]);
    expect(expand('지도 풀')).not.toHaveProperty('공간 부족 누계');
    expect(within(rowOf('지도 풀')).queryByText(/공간 부족/)).not.toBeInTheDocument();
    expect(within(rowOf('지도 풀')).getByRole('progressbar')).not.toHaveAttribute('data-level');
    page.rerender(<AppMemoryPools vmId="vm-a" vmName="Zephyr A" samples={[fixture(1, 6600, 6600, 0)]} />);
    expect(within(rowOf('지도 풀')).getByRole('progressbar')).toHaveAttribute('data-level', 'warn');
    page.rerender(<AppMemoryPools vmId="vm-a" vmName="Zephyr A" samples={[fixture(1, 7800, 7800, 0)]} />);
    expect(within(rowOf('지도 풀')).getByRole('progressbar')).toHaveAttribute('data-level', 'fault');
  });

  it('marks busy as unmeasured and returns to normal after a real valid sample', () => {
    const busy = fixture(2, 0);
    busy.values = busy.values.map((value) => ({
      ...value,
      value: null,
      raw_value: null,
      status: 'collection_failed',
      unavailable_reason: 'owner_busy',
      source_rc: -16,
    }));
    const page = show([fixture(1, 6144), busy]);
    let maps = within(rowOf('지도 풀'));
    expect(maps.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(notesOf('지도 풀')).toEqual(['소유자 사용 중 · 이번 표본 미측정 (-16)']);
    expect(maps.getByText('—')).toBeInTheDocument();
    page.rerender(
      <AppMemoryPools
        vmId="vm-a"
        vmName="Zephyr A"
        samples={[fixture(1, 6144), busy, fixture(3, 0, 6144)]}
      />,
    );
    maps = within(rowOf('지도 풀'));
    expect(maps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    expect(notesOf('지도 풀').join()).not.toContain('소유자 사용 중');
  });

  it('renders the exact failure count above 2^53 instead of a rounded numeric projection', () => {
    const report = fixture(1, 0);
    const failures = report.values.find((value) => value.name.endsWith('capacity_failure_total'))!;
    failures.raw_value = '9007199254740993';
    failures.value = null;
    show([report]);
    expand('지도 풀');
    expect(problemsOf('지도 풀')).toEqual(['공간 부족 9,007,199,254,740,993회']);
  });
});
