import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DriveState } from '../api/drive';
import type { MetricSample } from '../api/types';
import { memoryFixture } from '../domain/memoryContractFixtures';
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
  const page = render(
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
  return { ...page, client };
}

afterEach(() => vi.unstubAllGlobals());

describe('VM telemetry dashboard', () => {
  it('shows VM / APP / LINK and uses Agent for Linux and telemetry heartbeat for Zephyr', async () => {
    const fetch = fixture();
    renderPage('/dashboard/overview');
    const autonomous = within(await screen.findByRole('link', { name: /autonomous/ }));
    expect(autonomous.getByText('대기')).toBeInTheDocument();
    const aLink = within(autonomous.getByTitle('Zephyr Telemetry heartbeat (v2)').parentElement!);
    expect(await aLink.findByText('연결됨')).toBeInTheDocument();
    expect(aLink.getByText('Telemetry')).toBeInTheDocument();
    expect(autonomous.queryByText('앱 HB')).not.toBeInTheDocument();
    const manual = within(screen.getByRole('link', { name: /manual/ }));
    expect(within(manual.getByText('APP').parentElement!).getByText('실행 중')).toBeInTheDocument();
    expect(await within(manual.getByText('LINK').parentElement!).findByText('미수신')).toBeInTheDocument();
    const linux = within(screen.getByRole('link', { name: /interaction/ }));
    const agentLink = within(linux.getByTitle('Linux Agent heartbeat (v1)').parentElement!);
    expect(agentLink.getByText('연결됨')).toBeInTheDocument();
    expect(agentLink.getByText('Agent')).toBeInTheDocument();
    for (const card of [autonomous, manual, linux]) {
      const rows = card.getByText('VM', { exact: true }).parentElement!.parentElement!.children;
      expect(Array.from(rows).map((row) => row.firstElementChild?.textContent)).toEqual([
        'VM',
        'APP',
        'LINK',
      ]);
    }
    const summary = within(screen.getByRole('region', { name: '주행 및 관측 요약' }));
    expect(summary.getByText('DRIVE MODE')).toBeInTheDocument();
    expect(summary.getByText('CONTROL AUTHORITY')).toBeInTheDocument();
    expect(summary.getByText('LAST OBSERVED')).toBeInTheDocument();
    expect(summary.queryByText('상태 스트림')).not.toBeInTheDocument();
    expect(summary.queryByText('TELEMETRY')).not.toBeInTheDocument();
    expect(
      fetch.mock.calls.some(
        ([url]) => url.includes(VM_IDS.interaction) && url.endsWith('/telemetry/heartbeat'),
      ),
    ).toBe(false);
  });

  it.each([
    ['degraded', '수집 저하'],
    ['stopping', '종료 중'],
  ])(
    'keeps a fresh %s publisher connected while retaining detailed collection diagnostics',
    async (state, label) => {
      const fetch = fixture();
      const original = fetch.getMockImplementation()!;
      fetch.mockImplementation(async (url: string) => {
        const response = await original(url);
        if (!url.endsWith(`/vms/${VM_IDS.autonomous}/telemetry/heartbeat`)) return response;
        return Response.json({
          ...(await response.json()),
          publisher_state: state,
          collection_failures_total: 7,
        });
      });
      const page = renderPage('/dashboard/overview');
      const card = within(await screen.findByRole('link', { name: /autonomous/ }));
      const row = card.getByText('LINK').parentElement!;
      expect(await within(row).findByText('연결됨')).toBeInTheDocument();
      expect(row).toHaveAttribute('data-tone', 'ok');
      expect(card.queryByText(label)).not.toBeInTheDocument();
      page.unmount();
      renderPage(`/dashboard/vms/${VM_IDS.autonomous}`);
      const diagnostics = within(await screen.findByRole('region', { name: '텔레메트리 수집기 상태' }));
      expect(await diagnostics.findByText(label)).toBeInTheDocument();
      expect(diagnostics.getByText('7')).toBeInTheDocument();
      expect(screen.getByText('앱 heartbeat · drive')).toBeInTheDocument();
    },
  );

  it.each([
    ['STALE', '관측 만료'],
    ['UNKNOWN', '미수신'],
    ['unavailable', '조회 불가'],
    ['other-vm', '미수신'],
  ])('keeps Zephyr LINK %s distinct from a live VM and waiting app', async (state, label) => {
    const fetch = fixture();
    const original = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (url: string) => {
      const response = await original(url);
      if (!url.endsWith(`/vms/${VM_IDS.autonomous}/telemetry/heartbeat`)) return response;
      if (state === 'unavailable') return Response.json({}, { status: 503 });
      const report = await response.json();
      return Response.json(
        state === 'other-vm' ? { ...report, vm_id: VM_IDS.manual } : { ...report, status: state },
      );
    });
    renderPage('/dashboard/overview');
    const card = within(await screen.findByRole('link', { name: /autonomous/ }));
    expect(within(card.getByText('VM').parentElement!).getByText('실행 중')).toBeInTheDocument();
    expect(within(card.getByText('APP').parentElement!).getByText('대기')).toBeInTheDocument();
    const link = within(card.getByText('LINK').parentElement!);
    expect(await link.findByText(label)).toBeInTheDocument();
    expect(link.queryByText('연결됨')).not.toBeInTheDocument();
  });

  it('keeps an expired app report separate from a fresh telemetry link', async () => {
    fixture();
    const stale = structuredClone(drive);
    stale.vm_apps![VM_IDS.autonomous]!.drive!.lifecycle_source.status = 'stale';
    useDrive.mockReturnValue({ drive: stale, socket: 'open' });
    renderPage('/dashboard/overview');
    const card = within(await screen.findByRole('link', { name: /autonomous/ }));
    expect(within(card.getByText('APP').parentElement!).getByText('관측 만료')).toBeInTheDocument();
    expect(await within(card.getByText('LINK').parentElement!).findByText('연결됨')).toBeInTheDocument();
  });

  it('does not present cached Linux Agent status as a current connection when VM lookup fails', async () => {
    const fetch = fixture();
    const original = fetch.getMockImplementation()!;
    let failList = false;
    fetch.mockImplementation(async (url: string) =>
      failList && url === '/api/v1/vms' ? Response.json({}, { status: 503 }) : original(url),
    );
    const page = renderPage('/dashboard/overview');
    const card = within(await screen.findByRole('link', { name: /interaction/ }));
    const link = within(card.getByText('LINK').parentElement!);
    expect(await link.findByText('연결됨')).toBeInTheDocument();
    failList = true;
    await act(() => page.client.invalidateQueries({ queryKey: ['vms'], exact: true }));
    expect(await link.findByText('조회 불가')).toBeInTheDocument();
    expect(link.queryByText('연결됨')).not.toBeInTheDocument();
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
        await screen.findByRole('img', { name: `${name} 커널 힙 사용률 최근 5분 그래프` }),
      ).toBeInTheDocument();
      expect(screen.getByRole('progressbar', { name: `${name} 커널 힙 사용률` })).toHaveAttribute(
        'aria-valuenow',
        '50',
      );
      expect(screen.queryByRole('progressbar', { name: `${name} VM 메모리 사용률` })).not.toBeInTheDocument();
      expect(screen.getByRole('region', { name: `${name} 앱 버퍼 점유` })).toBeVisible();
      for (const group of ['자동', '공통', '수동'])
        expect(screen.getByRole('region', { name: `${name} ${group} 메모리` })).toBeVisible();
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

  it('preserves Linux MemAvailable and disk I/O beside Zephyr RAM, accounted heap and manual buffers', async () => {
    const fetch = fixture();
    const original = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (url: string) => {
      const response = await original(url);
      if (!new URL(url, 'http://fixture').pathname.endsWith('/metrics')) return response;
      const body: { items: MetricSample[] } = await response.json();
      const base = body.items[0]!;
      if (url.includes(VM_IDS.interaction)) {
        body.items = [0, 1].map((index) => ({
          ...base,
          boot_id: 'linux-boot',
          observed_at: new Date(Date.now() - 2000 + index * 1000).toISOString(),
          values: [
            ...base.values,
            {
              name: 'memory_available_bytes',
              value: 2 * 1024 * 1024,
              labels: {},
              unit: 'bytes',
              unavailable_reason: null,
            },
            {
              name: 'disk_read_bytes_total',
              value: 1024 + index * 2048,
              labels: { device: 'xvda' },
              unit: 'bytes',
              unavailable_reason: null,
            },
            {
              name: 'disk_write_bytes_total',
              value: 0,
              labels: { device: 'xvda' },
              unit: 'bytes',
              unavailable_reason: null,
            },
          ],
        }));
      } else {
        const ram = memoryFixture('ram-committed');
        base.values.push(...ram.values.filter((value) => !value.name.startsWith('memory.kernel_heap.')), {
          ...base.values[0]!,
          name: 'memory.kernel_heap.accounted_bytes',
          value: 4096,
          raw_value: '4096',
        });
        const result = memoryFixture('manual-memory-result-revoked', Date.now() - 1000);
        body.items.push({
          ...result,
          vm_id: base.vm_id,
          boot_id: base.boot_id,
          telemetry: {
            ...result.telemetry!,
            publisher_instance_id: base.telemetry!.publisher_instance_id,
            context: base.telemetry!.context,
          },
        });
      }
      return Response.json(body);
    });
    renderPage();
    const ram = within(await screen.findByRole('region', { name: 'autonomous 메모리 사용률' }));
    expect(await ram.findByText('25.000%')).toBeInTheDocument();
    expect(ram.getByText('정적 예약 포함')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'autonomous 커널 힙 사용률' })).toHaveAttribute(
      'aria-valuenow',
      '13',
    );
    expect(
      within(screen.getByRole('region', { name: 'autonomous 결과 보관' })).getByText(
        '철회 관측 · 반환 여부는 점유로 확인',
      ),
    ).toBeInTheDocument();
    const linux = screen.getByText('interaction', { selector: 'strong' }).closest('section')!;
    expect(within(linux).getByText('2 MiB')).toBeInTheDocument();
    expect(within(linux).getByTitle('여유 = MemAvailable (memory_available_bytes)')).toBeInTheDocument();
    const disk = within(screen.getByRole('region', { name: 'interaction 디스크 I/O' }));
    expect(disk.getByText('3.0 KiB')).toBeInTheDocument();
    expect(disk.getByText('2.0 KiB/s')).toBeInTheDocument();
    expect(screen.queryByText('스레드 스택 스냅샷')).not.toBeInTheDocument();
  });

  it('does not resurrect cached five-minute heap data after a new boot in the buffer query', async () => {
    const fetch = fixture();
    const original = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (url: string) => {
      const response = await original(url);
      const parsed = new URL(url, 'http://fixture');
      if (!parsed.pathname.endsWith(`/vms/${VM_IDS.autonomous}/metrics`)) return response;
      const from = Date.parse(parsed.searchParams.get('from')!);
      if (Date.now() - from > 90_000) return response;
      const next = memoryFixture('manual-memory-pending-full', Date.now() - 500);
      next.vm_id = VM_IDS.autonomous;
      next.boot_id = 'new-boot';
      return Response.json({ items: [next] });
    });
    renderPage();
    expect(
      await screen.findByRole('img', { name: 'manual 커널 힙 사용률 최근 5분 그래프' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'autonomous 커널 힙 사용률' })).not.toHaveAttribute(
      'aria-valuenow',
    );
    expect(
      screen.queryByRole('img', { name: 'autonomous 커널 힙 사용률 최근 5분 그래프' }),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('region', { name: 'autonomous 대기 작업' })).getByText('작업 대기'),
    ).toBeInTheDocument();
  });

  it('retains heap history while expiring the current value rather than displaying zero', async () => {
    const fetch = fixture();
    const original = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (url: string) => {
      const response = await original(url);
      if (!new URL(url, 'http://fixture').pathname.endsWith(`/vms/${VM_IDS.autonomous}/metrics`))
        return response;
      const body: { items: MetricSample[] } = await response.json();
      body.items[0]!.received_at = new Date(Date.now() - 20_000).toISOString();
      return Response.json(body);
    });
    renderPage();
    expect(
      await screen.findByRole('img', { name: 'autonomous 커널 힙 사용률 최근 5분 그래프' }),
    ).toBeInTheDocument();
    const heap = within(screen.getByRole('region', { name: 'autonomous 공통 커널 힙' }));
    expect(heap.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(heap.queryByText('0.0%')).not.toBeInTheDocument();
    expect(heap.getAllByText(/관측 만료 · 이력만 표시/).length).toBeGreaterThan(0);
  });
});
