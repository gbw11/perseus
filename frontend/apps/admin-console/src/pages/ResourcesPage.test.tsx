import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render as rtlRender, screen, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialVms, VM_IDS } from '../mocks/data';
import { mockZephyrThreads } from '../mocks/memoryMetrics';
import { ResourcesPage } from './ResourcesPage';

const state = vi.hoisted(() => ({ metricsError: null as Error | null }));

vi.mock('../layout/DriveContext', () => ({ useDrive: () => ({ drive: null }) }));

vi.mock('../api/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/queries')>();
  const data = await import('../mocks/data');
  const memory = await import('../mocks/memoryMetrics');
  const linuxSample = (index: number) => {
    const at = new Date(1_000_000 + index * 10_000).toISOString();
    return {
      sample_id: `linux-${index}`,
      vm_id: data.VM_IDS.interaction,
      boot_id: 'boot-linux',
      source: 'guest_agent',
      observed_at: at,
      values: [
        {
          name: 'memory_used_bytes',
          value: 128 * 1024 * 1024,
          unit: 'bytes',
          labels: null,
          unavailable_reason: null,
        },
        {
          name: 'net_rx_bytes_total',
          value: 10_000 + index * 2_000,
          unit: 'bytes',
          labels: { iface: 'eth0' },
          unavailable_reason: null,
        },
        {
          name: 'net_tx_bytes_total',
          value: 5_000 + index * 1_000,
          unit: 'bytes',
          labels: { iface: 'eth0' },
          unavailable_reason: null,
        },
      ],
    };
  };
  return {
    ...actual,
    useVms: () => ({ data: { items: data.initialVms() }, isPending: false }),
    useMetrics: (vmId: string) => ({
      data: {
        items:
          vmId === data.VM_IDS.interaction
            ? [linuxSample(0), linuxSample(1)]
            : [
                ...memory.mockKernelHeapSamples(vmId),
                ...memory.mockMemoryPoolSamples(vmId),
                ...memory.mockZephyrNetworkSamples(vmId),
              ],
      },
      error: state.metricsError,
      isPending: false,
    }),
    useProcesses: () => ({
      data: {
        vm_id: data.VM_IDS.interaction,
        observed_at: new Date().toISOString(),
        stale: false,
        items: [{ pid: 1, name: 'systemd', cpu_percent: 0.5, rss_bytes: 1024 * 1024 }],
        process_collection: {
          status: 'ok',
          discovered: 42,
          read_ok: 42,
          vanished: 0,
          failed: 0,
          truncated: false,
          error: null,
        },
      },
      error: null,
      isPending: false,
    }),
    useThreads: (vmId: string) => ({ data: memory.mockZephyrThreads(vmId), error: null, isPending: false }),
  };
});

// LiveDataAlert 가 조회 캐시를 구독하므로 QueryClient 안에서 그린다
const render = (ui: ReactElement) =>
  rtlRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

beforeEach(() => {
  state.metricsError = null;
});

describe('Memory 탭', () => {
  it('RX/TX Pool 요약과 Pool 점유율 그래프는 Network 탭으로 옮기고, 나머지 Memory 구성은 그대로 둔다', () => {
    render(<ResourcesPage view="memory" />);

    // Network 탭으로 옮긴 것
    expect(screen.queryByText('RX Pool')).not.toBeInTheDocument();
    expect(screen.queryByText('TX Pool')).not.toBeInTheDocument();
    expect(screen.queryByText('Pool 점유율')).not.toBeInTheDocument();
    expect(screen.queryByText('MQTT 포함 공통 RX/TX 풀')).not.toBeInTheDocument();
    // origin/frontend Memory 구성 (정리는 별도 이슈)
    expect(screen.getAllByText('커널 힙 사용률').length).toBeGreaterThan(0);
    expect(screen.getAllByText('패킷 처리율').length).toBeGreaterThan(0);
    expect(screen.getByText('디스크 I/O')).toBeInTheDocument();
  });
});

const rowLabels = (table: HTMLElement) =>
  within(table)
    .getAllByRole('rowheader')
    .map((cell) => cell.textContent);

describe('Network 탭', () => {
  it('세 카드 모두 RX/TX 표 → 그래프 순서이고, Zephyr 만 맨 아래 인터페이스 상태 박스가 있다', () => {
    render(<ResourcesPage view="network" />);

    expect(screen.getByText('Linux 최근 5분')).toBeInTheDocument();
    expect(screen.getByText('Zephyr 최근 60초')).toBeInTheDocument();
    expect(screen.getAllByText('송수신 속도')).toHaveLength(3);
    expect(screen.getAllByText('Pool 점유율')).toHaveLength(2);
    expect(screen.queryByText('패킷 처리율')).not.toBeInTheDocument();
    // Network 탭에는 막대가 없다
    expect(screen.queryAllByRole('progressbar')).toHaveLength(0);

    const zephyr = screen.getByLabelText('autonomous 네트워크 수치');
    expect(
      within(zephyr)
        .getAllByRole('columnheader')
        .map((cell) => cell.textContent),
    ).toEqual(['RX 수신', 'TX 송신']);
    expect(rowLabels(zephyr)).toEqual(['속도', '누적', '패킷', 'Pool']);
    // Pool: 숫자와 단위를 나눠 크게 "87.5" + 작게 "%", 아래 "사용 / 전체 KiB". 80% 이상이라 주의 색
    const pool = within(zephyr).getByText('87.5').closest('td')!;
    expect(pool).toHaveTextContent('87.5%7.00 / 8.00 KiB');
    expect(pool).toHaveAttribute('data-level', 'warn');
    expect(pool).toHaveAttribute('data-tone', 'rx');
    expect(within(zephyr).getAllByText('KiB/s').length).toBeGreaterThan(0);

    const linux = screen.getByLabelText('interaction 네트워크 수치');
    expect(rowLabels(linux)).toEqual(['속도', '누적']);

    // 표가 카드의 첫 블록이고, 그래프는 그 뒤에 온다
    for (const table of [zephyr, linux]) {
      const block = table.parentElement!;
      expect(block.parentElement?.firstElementChild).toBe(block);
      const chart = within(block.parentElement!).getAllByText('송수신 속도')[0]!;
      expect(block.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }

    // 인터페이스·링크는 Zephyr 카드 맨 아래 별도 박스 2칸
    const interfaces = screen.getAllByRole('region', { name: /인터페이스 상태/ });
    expect(interfaces).toHaveLength(2);
    for (const section of interfaces) {
      expect(section.parentElement?.lastElementChild).toBe(section);
      expect(within(section).getByText('인터페이스')).toBeInTheDocument();
      expect(within(section).getByText('링크(carrier)')).toBeInTheDocument();
      expect(within(section).getAllByText('데이터 없음 (백엔드 미지원)')).toHaveLength(2);
    }
    expect(screen.queryByText('ok')).not.toBeInTheDocument();
    expect(screen.queryByText(/직접 계측|rx_bytes_per_s/)).not.toBeInTheDocument();
    expect(screen.getByText('Agent 연결됨')).toBeInTheDocument();
  });

  it('메트릭 오류면 두 줄 안내를 카드마다 보여 준다', () => {
    state.metricsError = new Error('boom');
    render(<ResourcesPage view="network" />);

    expect(screen.getAllByRole('alert')).toHaveLength(initialVms().length);
  });
});

describe('CPU 탭', () => {
  it('Linux 카드에 상위 프로세스와 수집 상태, Zephyr 카드에 상위 10개 스레드를 그린다', () => {
    render(<ResourcesPage view="cpu" />);

    const processes = screen.getByRole('region', { name: 'interaction 상위 프로세스' });
    expect(within(processes).getByText('42개 중 1개 · CPU 높은 순')).toBeInTheDocument();
    expect(within(processes).getByText('수집 정상')).toBeInTheDocument();
    expect(within(processes).getByText('systemd')).toBeInTheDocument();

    const threads = screen.getByRole('region', { name: 'autonomous 스레드' });
    expect(within(threads).getByText('12개 중 10개 · CPU 높은 순')).toBeInTheDocument();
    expect(within(threads).getByText('계측 구간 1.0초')).toBeInTheDocument();
    expect(within(threads).getAllByRole('row')).toHaveLength(11);
    expect(within(threads).getAllByRole('cell')[0]).toHaveTextContent('idle');
    expect(mockZephyrThreads(VM_IDS.autonomous).items).toHaveLength(12);
  });
});
