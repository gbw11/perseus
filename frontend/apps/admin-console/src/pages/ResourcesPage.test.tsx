import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render as rtlRender, screen, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialVms } from '../mocks/data';
import { ResourcesPage } from './ResourcesPage';

const state = vi.hoisted(() => ({ metricsError: null as Error | null }));

vi.mock('../layout/DriveContext', () => ({ useDrive: () => ({ drive: null }) }));
// 공용 1초 시계는 첫 렌더에 모듈을 불러온 시각을 돌려줄 수 있어, 표본(렌더 때 만든 현재 시각)보다 과거가 된다.
// Pool 현재값은 수신 시각보다 이른 now 를 오류로 보므로 테스트에서는 렌더 시점의 현재 시각을 쓴다
vi.mock('../useNow', () => ({ useNow: () => Date.now() }));

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
  const heartbeat = await import('../mocks/telemetryHeartbeat');
  return {
    ...actual,
    useTelemetryHeartbeat: (vmId: string) => ({
      data: heartbeat.mockTelemetryHeartbeat(vmId),
      error: null,
      isError: false,
      isPending: false,
    }),
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
  };
});

// LiveDataAlert 가 조회 캐시를 구독하므로 QueryClient 안에서 그린다
const render = (ui: ReactElement) =>
  rtlRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

beforeEach(() => {
  state.metricsError = null;
});

describe('Memory 탭', () => {
  it('그래프·안내 문구 없이 세 카드가 요약 → 상세 구조로 보인다 (S15P31E110-409)', () => {
    render(<ResourcesPage view="memory" />);

    // 그래프 없음 (추이는 VM 상세 화면에서 본다)
    expect(document.querySelectorAll('svg')).toHaveLength(0);
    expect(screen.queryByText('패킷 처리율')).not.toBeInTheDocument();
    expect(screen.queryByText(/최근 60초|최근 5분/)).not.toBeInTheDocument();
    // 제거한 안내 문구
    for (const text of [
      '관측 시각과 만료는 영역별로 확인',
      '정적 예약 포함',
      /관리 RAM과 내부 버퍼 점유를 분리합니다/,
      /장치 합산 · 같은 부팅 구간에서만 속도 계산/,
      /원 관측 약/,
    ])
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    // Network 탭으로 옮긴 Pool 은 계속 없다
    expect(screen.queryByText('RX Pool')).not.toBeInTheDocument();

    // Zephyr 카드: 부제 "역할 · Zephyr", 제목 오른쪽 마지막 갱신, 요약 → 상세(커널 힙 요약 유지)
    const zephyr = screen
      .getByRole('region', { name: 'autonomous 메모리 사용률' })
      .closest('[class*="memoryVmCard"]')!;
    expect(within(zephyr as HTMLElement).getByText(/· Zephyr$/)).toBeInTheDocument();
    expect(within(zephyr as HTMLElement).getByText(/^마지막 갱신/)).toBeInTheDocument();
    // 커널 힙은 공통 그룹 안에서 다른 앱 버퍼 영역과 같은 형태 ("커널 heap 기준" 배지 없음)
    const heap = screen.getByRole('group', { name: 'autonomous 공통 커널 힙' });
    expect(within(heap).getByText('커널 힙')).toBeInTheDocument();
    // 커널 힙 행도 아코디언: 누르면 현재 여유 등 세부 항목을 펼친다
    const heapToggle = within(heap).getByRole('button');
    expect(heapToggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(heapToggle);
    expect(heapToggle).toHaveAttribute('aria-expanded', 'true');
    expect(within(heap).getByText('현재 여유')).toBeVisible();
    // 메모리 사용률은 세 카드 모두 같은 요약 섹션 (한 줄 행이 아님), 툴팁 없음
    for (const name of ['autonomous', 'manual', 'interaction'])
      expect(screen.getByRole('region', { name: `${name} 메모리 사용률` })).toBeInTheDocument();
    expect(heap.querySelector('[title]')).toBeNull();
    expect(screen.queryByText('커널 heap 기준')).not.toBeInTheDocument();
    expect(heap.closest('[aria-label="autonomous 공통 메모리"]')).not.toBeNull();
    // 그룹 제목: 자동·공통·수동 색 구분 (세로선 + 글자색), 알약 배지 없음
    expect(
      within(screen.getByRole('region', { name: 'autonomous 수동 메모리' })).getByRole('heading', {
        name: '수동',
      }),
    ).toHaveAttribute('data-group', 'manual');
    expect(screen.getAllByText('메모리 상세').length).toBe(2);
    // Linux 카드: 요약 + 디스크 I/O
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

    // 카드 본문은 상태 줄 → 표 → 그래프 순서
    for (const table of [zephyr, linux]) {
      const block = table.parentElement!;
      const statusLine = block.parentElement!.firstElementChild!;
      expect(within(statusLine as HTMLElement).getAllByRole('status').length).toBeGreaterThan(0);
      expect(statusLine.nextElementSibling).toBe(block);
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

    // 상태 줄: 제목 줄이 아니라 카드 본문 첫 줄. Zephyr 는 수집 상태 하나, Linux 는 Agent + 수집 상태
    expect(screen.getByText('수집 중')).toBeInTheDocument();
    expect(screen.getByText('수집 저하')).toBeInTheDocument();
    const agentItem = screen.getByText('Agent 연결됨');
    const stale = screen.getByText('수집기 · 관측 만료');
    expect(agentItem.parentElement).toBe(stale.parentElement);
    expect(agentItem.nextElementSibling).toBe(stale);
    // 제목 줄(역할 배지·마지막 갱신)에는 상태 항목이 없다
    expect(document.querySelectorAll('[class*="memoryCardActions"]')).toHaveLength(3);
    for (const item of [agentItem, stale]) {
      expect(item.closest('[class*="memoryCardActions"]')).toBeNull();
    }
    expect(stale).toHaveAttribute('title', expect.stringContaining('수집 실패 누계: 0'));
    // 알약(StatusBadge)이 아니라 색 점 + 글자
    expect(stale).toHaveAttribute('data-tone', 'recovering');
  });

  it('메트릭 오류면 두 줄 안내를 카드마다 보여 준다', () => {
    state.metricsError = new Error('boom');
    render(<ResourcesPage view="network" />);

    expect(screen.getAllByRole('alert')).toHaveLength(initialVms().length);
  });
});
