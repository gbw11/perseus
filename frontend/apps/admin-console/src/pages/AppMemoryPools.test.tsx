import { act, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MetricSample } from '../api/types';
import { AppMemoryPools } from './AppMemoryPools';

const NOW = Date.parse('2026-11-04T10:00:00Z');
function fixture(seq: number, used: number, peak = used): MetricSample {
  const fields = {
    reserved_bytes: 12288,
    used_bytes: used,
    free_bytes: 8192 - used,
    capacity_bytes: 8192,
    high_water_bytes: peak,
    min_free_bytes: 8192 - peak,
    capacity_failure_total: 1,
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

describe('app memory panels', () => {
  it('ages an unchanged sample without more network requests and cleans up its timer', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const page = render(<AppMemoryPools vmId="vm-a" vmName="Zephyr A" samples={[fixture(4, 4096)]} />);
    const maps = within(screen.getByRole('region', { name: 'Zephyr A 지도 풀' }));
    expect(maps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(maps.getByText(/원 관측 약 1초 전/)).toBeInTheDocument();
    const timers = vi.getTimerCount();
    act(() => {
      vi.advanceTimersByTime(61_000);
    });
    expect(maps.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(maps.getByText('최근 60초 표본 없음')).toBeInTheDocument();
    expect(maps.getByText(/원 관측 약 62초 전/)).toBeInTheDocument();
    page.unmount();
    expect(vi.getTimerCount()).toBe(timers - 1);
  });
  it('shows actual zero, provider peak, reservation and separate failure counts without filling missing regions', () => {
    show([fixture(1, 6144), fixture(2, 0, 6144)]);
    const maps = within(screen.getByRole('region', { name: 'Zephyr A 지도 풀' }));
    expect(maps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    expect(maps.getByText('0 KiB')).toBeInTheDocument();
    expect(maps.getByText('최고 점유 · 부팅 후').nextElementSibling).toHaveTextContent('6.00 KiB');
    expect(maps.getByText('최소 여유 · 부팅 후').nextElementSibling).toHaveTextContent('2.00 KiB');
    expect(maps.getByText('정적 예약').nextElementSibling).toHaveTextContent('12.00 KiB');
    expect(maps.getByText('공간 부족 누계').nextElementSibling).toHaveTextContent('1 회');
    expect(maps.getByText('소유자 경합 누계').nextElementSibling).toHaveTextContent('0 회');
    expect(maps.getByText(/원 관측 약 3초 전 · 최초 수신 3초 전/)).toBeInTheDocument();
    expect(maps.getByRole('img')).toHaveAccessibleName('Zephyr A 지도 풀 점유율 최근 60초');
    const paths = within(screen.getByRole('region', { name: 'Zephyr A 경로 풀' }));
    expect(paths.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(paths.getByText('미수신')).toBeInTheDocument();
    expect(screen.getByText('최근 메모리 관측')).toBeInTheDocument();
    expect(screen.getByText(/VM 전체 메모리 사용률로 합산하지 않습니다/)).toBeInTheDocument();
  });

  it('marks busy as unmeasured, breaks the graph and returns to normal after a real valid sample', () => {
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
    let maps = within(screen.getByRole('region', { name: 'Zephyr A 지도 풀' }));
    expect(maps.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(maps.getByText('소유자 사용 중 · 이번 표본 미측정')).toHaveAttribute('title', 'owner_busy (-16)');
    expect(maps.queryByText('0 KiB')).not.toBeInTheDocument();
    page.rerender(
      <AppMemoryPools
        vmId="vm-a"
        vmName="Zephyr A"
        samples={[fixture(1, 6144), busy, fixture(3, 0, 6144)]}
      />,
    );
    maps = within(screen.getByRole('region', { name: 'Zephyr A 지도 풀' }));
    expect(maps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    expect(maps.queryByText('소유자 사용 중 · 이번 표본 미측정')).not.toBeInTheDocument();
    // Valid observations separated by a null point are not connected.
    expect(maps.getByRole('img').querySelectorAll('polyline')).toHaveLength(0);
    expect(maps.getByRole('img').querySelectorAll('circle')).toHaveLength(2);
  });

  it('renders the exact failure count above 2^53 instead of a rounded numeric projection', () => {
    const report = fixture(1, 0);
    const failures = report.values.find((value) => value.name.endsWith('capacity_failure_total'))!;
    failures.raw_value = '9007199254740993';
    failures.value = null;
    show([report]);
    const maps = within(screen.getByRole('region', { name: 'Zephyr A 지도 풀' }));
    expect(maps.getByText('공간 부족 누계').nextElementSibling).toHaveTextContent('9,007,199,254,740,993 회');
  });
});
