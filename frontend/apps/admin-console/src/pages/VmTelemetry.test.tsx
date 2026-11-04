import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DriveState } from '../api/drive';
import { initialVms, VM_IDS } from '../mocks/data';
import { DashboardPage } from './DashboardPage';
import { ResourcesPage } from './ResourcesPage';
import { VmDetailPage } from './VmDetailPage';

const { useDrive } = vi.hoisted(() => ({ useDrive: vi.fn() }));
vi.mock('../layout/DriveContext', () => ({ useDrive }));
vi.mock('../console/ConsolePanel', () => ({ ConsolePanel: () => null }));

const received = '2026-11-03T00:00:00Z';
const vms = initialVms().map((vm, i) => ({ ...vm, assigned_ipv4: `192.168.77.${i + 2}` }));
const drive: DriveState = {
  version: 1,
  observed_at: received,
  control: null,
  vehicle: null,
  perception: null,
  apps: {},
  vm_apps: {
    [VM_IDS.autonomous]: {
      drive: {
        lifecycle: { phase: 'WAITING', readiness: 'NOT_READY', reason: 'awaiting_input' },
        lifecycle_source: { status: 'fresh', age_ms: 100 },
        heartbeat: null,
        heartbeat_source: { status: 'unknown', age_ms: null },
      },
    },
    [VM_IDS.manual]: {
      drive: {
        lifecycle: { phase: 'EXECUTING', readiness: 'READY', reason: null },
        lifecycle_source: { status: 'fresh', age_ms: 100 },
        heartbeat: { state: 'active' },
        heartbeat_source: { status: 'stale', age_ms: 9000 },
      },
    },
  },
};

function fixture(stackSupported = true) {
  useDrive.mockReturnValue({ drive, socket: 'open' });
  const fetch = vi.fn(async (url: string) => {
    const path = new URL(url, 'http://fixture').pathname;
    const [, , , , id, endpoint] = path.split('/');
    const vm = vms.find((item) => item.vm_id === id);
    if (path === '/api/v1/events') return Response.json({ items: [] });
    if (path === '/api/v1/vms') return Response.json({ items: vms });
    if (!vm) return Response.json({}, { status: 404 });
    const linux = vm.guest_kind === 'linux';
    if (endpoint === 'telemetry' && path.endsWith('/heartbeat')) {
      const status = linux ? 'STALE' : id === VM_IDS.autonomous ? 'FRESH' : 'UNKNOWN';
      return Response.json({
        vm_id: id,
        status,
        publisher_state: status === 'UNKNOWN' ? null : 'running',
        last_collection_success_uptime_ms: status === 'UNKNOWN' ? null : '12000',
        collection_failures_total: status === 'UNKNOWN' ? null : 2,
        received_at: status === 'UNKNOWN' ? null : received,
      });
    }
    if (endpoint === 'metrics') {
      const metric = (name: string, value: number, unit: string) => ({
        name,
        value,
        unit,
        raw_value: String(value),
        status: 'ok',
        source_rc: 0,
        labels: {},
        unavailable_reason: null,
      });
      return Response.json({
        items: [
          {
            observed_at: received,
            ...(!linux
              ? {
                  vm_id: id,
                  boot_id: `boot-${id}`,
                  sample_id: `sample-${id}`,
                  source: 'zephyr_telemetry',
                  received_at: new Date(Date.now() - 1000).toISOString(),
                  telemetry: {
                    family: 'memory',
                    publisher_instance_id: `publisher-${id}`,
                    source_sample_seq: '1',
                    observed_uptime_ms: '1000',
                    sent_uptime_ms: '1002',
                    sent_at: null,
                    utc_quality: 'unavailable',
                    utc_source: null,
                    uncertainty_ms: null,
                    collection: {
                      sample_id: 'heap-collection',
                      start_uptime_ms: '999',
                      end_uptime_ms: '1000',
                      elapsed_us: '20',
                      status: 'ok',
                      source_rc: 0,
                    },
                    context: {
                      binding: 'host_provisioned',
                      host_boot_id: 'host',
                      execution_id: `execution-${id}`,
                      receipt_id: `receipt-${id}`,
                    },
                  },
                }
              : {}),
            values: linux
              ? [metric('memory_used_bytes', 1048576, 'bytes')]
              : [
                  metric('memory.kernel_heap.used_bytes', 512, 'bytes'),
                  metric('memory.kernel_heap.free_bytes', 512, 'bytes'),
                  metric('rx_bytes_per_s', 2048, 'bytes/s'),
                ],
          },
        ],
      });
    }
    if (endpoint === 'threads' && !linux) {
      return Response.json({
        vm_id: id,
        window_ms: 1000,
        received_at: received,
        stale: false,
        threads_status: 'ok',
        parts_received: 1,
        part_count: 2,
        complete: false,
        conflicting_pages: false,
        items: [{ thread_id: '1', thread_name: 'telemetry', cpu_percent: 0 }],
        stack_snapshot: {
          snapshot_id: 'stack-snapshot',
          source_status: stackSupported ? 'ok' : 'unsupported',
          rows_seen: stackSupported ? 2 : null,
          received_at: received,
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          items: stackSupported
            ? [
                {
                  row_id: 0,
                  name: 'telemetry',
                  name_truncated: false,
                  reserved_bytes: 8192,
                  peak_used_bytes: 4096,
                  status: 'ok',
                },
                {
                  row_id: 1,
                  name: 'telemetry',
                  name_truncated: false,
                  reserved_bytes: 4096,
                  peak_used_bytes: null,
                  status: 'scan_limit',
                },
              ]
            : [],
        },
      });
    }
    if (endpoint === 'processes' && linux) {
      return Response.json({
        vm_id: id,
        observed_at: received,
        stale: false,
        items: [{ pid: 1, name: 'systemd', cpu_percent: 0, rss_bytes: 1048576 }],
      });
    }
    if (!endpoint) return Response.json(vm);
    return Response.json({}, { status: 404 });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

function renderPage(path = '/dashboard/memory') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/dashboard/overview" element={<DashboardPage />} />
          <Route path="/dashboard/vms/:vmId" element={<VmDetailPage />} />
          <Route path="/dashboard/memory" element={<ResourcesPage view="memory" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('VM telemetry dashboard', () => {
  it('shows scoped app observations and publisher freshness separately on Overview', async () => {
    fixture();
    renderPage('/dashboard/overview');
    const autonomous = within(await screen.findByRole('link', { name: /autonomous/ }));
    expect(autonomous.getByText('대기')).toBeInTheDocument();
    expect(autonomous.getByText('미수신')).toBeInTheDocument();
    expect(await autonomous.findByText('수집 중')).toBeInTheDocument();
    const manual = within(screen.getByRole('link', { name: /manual/ }));
    expect(manual.queryByText('대기')).not.toBeInTheDocument();
    expect(manual.getByText('관측 만료')).toBeInTheDocument();
    expect(manual.queryByText('수집 중')).not.toBeInTheDocument();
    const linux = within(screen.getByRole('link', { name: /interaction/ }));
    expect(linux.getByText('Linux Agent')).toBeInTheDocument();
    expect(linux.getByText('연결됨')).toBeInTheDocument();
    expect(await linux.findByText('관측 만료')).toBeInTheDocument();
  });

  it('shows Zephyr thread pages, heap, RX rate, reserved IP and only its own app reports', async () => {
    const fetch = fixture();
    renderPage(`/dashboard/vms/${VM_IDS.autonomous}`);
    expect(await screen.findByRole('columnheader', { name: '스레드 ID' })).toBeInTheDocument();
    const cpuTable = screen.getByRole('columnheader', { name: '스레드 ID' }).closest('table')!;
    expect(within(cpuTable).getByText('telemetry')).toBeInTheDocument();
    expect(within(cpuTable).queryByText('8.00 KiB')).not.toBeInTheDocument();
    expect(screen.queryByRole('table', { name: '스레드 스택 스냅샷' })).not.toBeInTheDocument();
    expect(screen.queryByText('스레드 스택 스냅샷')).not.toBeInTheDocument();
    expect(screen.getByText(/페이지 1\/2 수신/)).toBeInTheDocument();
    expect(screen.getByText(/대기 · 준비 미충족 · awaiting_input/)).toBeInTheDocument();
    expect(screen.queryByText(/실행 중 · 계산 준비 확인/)).not.toBeInTheDocument();
    expect(screen.getByText('192.168.77.2')).toBeInTheDocument();
    expect(await screen.findByText('KiB/s')).toBeInTheDocument();
    expect(screen.getByText('2.0')).toBeInTheDocument();
    expect(screen.getByText('0.5')).toBeInTheDocument();
    expect(await screen.findByText('수집 중')).toBeInTheDocument();
    expect(screen.getByText('12000 ms')).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/processes'))).toBe(false);
  });

  it('defers stack diagnostics display while preserving CPU rows', async () => {
    fixture(false);
    renderPage(`/dashboard/vms/${VM_IDS.autonomous}`);
    expect(await screen.findByText('telemetry')).toBeInTheDocument();
    expect(screen.queryByText('현재 Zephyr 펌웨어에서 스택 진단이 꺼져 있습니다.')).not.toBeInTheDocument();
    expect(screen.queryByRole('table', { name: '스레드 스택 스냅샷' })).not.toBeInTheDocument();
  });

  it('keeps Linux on the process API and does not show stale publisher state as current', async () => {
    const fetch = fixture();
    renderPage(`/dashboard/vms/${VM_IDS.interaction}`);
    expect(await screen.findByText('systemd')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '메모리 (RSS)' })).toBeInTheDocument();
    expect(screen.getByText('Linux Agent 생존 (v1)')).toBeInTheDocument();
    const status = within(screen.getByRole('region', { name: '텔레메트리 수집기 상태' }));
    expect(await status.findByText('관측 만료')).toBeInTheDocument();
    expect(status.queryByText('수집 중')).not.toBeInTheDocument();
    expect(status.getByText('2')).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/threads'))).toBe(false);
  });

  it('shows Zephyr allocation and heap separately without heartbeat or counter cards in Memory', async () => {
    const fetch = fixture();
    renderPage();
    for (const name of ['autonomous', 'manual']) {
      expect(
        await screen.findByRole('img', { name: `${name} 커널 힙 사용률 최근 60초 그래프` }),
      ).toBeInTheDocument();
      expect(screen.getByRole('progressbar', { name: `${name} 커널 힙 사용률` })).toHaveAttribute(
        'aria-valuenow',
        '50',
      );
      expect(screen.queryByRole('progressbar', { name: `${name} VM 메모리 사용률` })).not.toBeInTheDocument();
      expect(screen.getByRole('region', { name: `${name} 앱 버퍼 점유` })).toBeInTheDocument();
    }
    expect(
      await screen.findByRole('img', { name: 'interaction 메모리 사용률 최근 5분 그래프' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('VM 구성 16 MiB · 관리 RAM과 내부 버퍼 점유를 분리합니다.')).toHaveLength(2);
    expect(
      screen.queryByText('Zephyr VM 전체 메모리 사용량은 현재 계측되지 않습니다.'),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText('0.50 KiB')).toHaveLength(4);
    expect(screen.queryByText('수집기 상태 (v2)')).not.toBeInTheDocument();
    expect(screen.queryByText('TX drop')).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/telemetry/heartbeat'))).toBe(false);
  });
});
