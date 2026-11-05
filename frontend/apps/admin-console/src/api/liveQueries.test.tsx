import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ToastProvider } from '@xenova/ui';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LiveDataAlert, LiveDataAlertHost } from '../layout/LiveDataAlert';
import { useNow } from '../useNow';
import { useTelemetryHeartbeat, useVms } from './queries';
import type { VmListResponse } from './types';
import { loadVmList } from './vmListCache';

const LIST: VmListResponse = {
  items: [
    {
      vm_id: 'vm-1',
      name: 'debian-dev',
      guest_kind: 'linux',
      state: 'running',
      template_id: 'debian',
      template_version: '13',
      vcpus: 1,
      memory_mib: 512,
    } as VmListResponse['items'][number],
  ],
  observed_at: '2026-11-05T02:01:00Z',
};

const BOARD_DOWN = {
  code: 'VM_CONTROL_BAD_RESPONSE',
  message: 'VM 제어 서비스와의 연결 설정이 올바르지 않습니다.',
};

/** online 이 true 면 목록을, false 면 보드가 꺼졌을 때처럼 502 를 돌려준다. heartbeat 는 항상 404 */
function stubBackend() {
  const state: { online: boolean; hold: Promise<void> | null } = { online: true, hold: null };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (state.hold) await state.hold;
      const path = new URL(url, 'http://fixture').pathname;
      if (path.endsWith('/telemetry/heartbeat'))
        return Response.json({ code: 'NOT_FOUND', message: '없음' }, { status: 404 });
      return state.online ? Response.json(LIST) : Response.json(BOARD_DOWN, { status: 502 });
    }),
  );
  return state;
}

/** 화면처럼 렌더 중에 값을 읽는다 (TanStack Query 는 렌더 중에 읽은 속성이 바뀔 때만 다시 그린다) */
function useVmsInRender() {
  const { data, isError, isPending, error, refetch } = useVms();
  return { data, isError, isPending, error, refetch };
}

function testClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
}

function wrapper(client = testClient()) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}

/** 다음 응답을 붙잡아 두었다가 release() 로 보낸다 */
function holdResponses(backend: { hold: Promise<void> | null }) {
  let release = () => {};
  backend.hold = new Promise<void>((resolve) => {
    release = () => {
      backend.hold = null;
      resolve();
    };
  });
  return () => release();
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  sessionStorage.clear();
});

describe('실시간 조회 실패', () => {
  it('VM 목록 조회가 실패하면 이전 목록을 지우고 저장해 둔 목록도 지운다', async () => {
    const backend = stubBackend();
    const { result } = renderHook(() => useVmsInRender(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.data?.items).toHaveLength(1));
    expect(loadVmList()).not.toBeNull();

    backend.online = false;
    await act(() => result.current.refetch());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    expect(loadVmList()).toBeNull();
  });

  it('실패한 뒤 다시 읽는 동안에도 실패 상태를 유지한다 (불러오는 중으로 돌아가지 않는다)', async () => {
    const backend = stubBackend();
    backend.online = false;
    const { result } = renderHook(() => useVmsInRender(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));

    const release = holdResponses(backend);
    act(() => {
      void result.current.refetch();
    });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(result.current.isError).toBe(true);
    expect(result.current.isPending).toBe(false);
    expect(result.current.error).toBeTruthy();
    release();
  });

  it('다시 성공하면 목록이 돌아온다', async () => {
    const backend = stubBackend();
    backend.online = false;
    const { result } = renderHook(() => useVmsInRender(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));

    backend.online = true;
    await act(() => result.current.refetch());

    await waitFor(() => expect(result.current.data?.items).toHaveLength(1));
  });
});

describe('LiveDataAlert', () => {
  /** 레이아웃(Host) + 페이지. withSlot 이면 페이지가 알림 자리를 둔다 */
  function Screen({ withSlot = false }: { withSlot?: boolean }) {
    useVms();
    useTelemetryHeartbeat('vm-1');
    return (
      <>
        <LiveDataAlertHost />
        <div data-testid="page">{withSlot && <LiveDataAlert />}</div>
      </>
    );
  }

  it('실패한 조회를 이름과 오류 코드로 보이고, 404 는 넣지 않는다', async () => {
    const backend = stubBackend();
    backend.online = false;
    const Wrapper = wrapper();
    render(
      <Wrapper>
        <Screen />
      </Wrapper>,
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'VM 목록: VM 제어 서비스와의 연결 설정이 올바르지 않습니다. (VM_CONTROL_BAD_RESPONSE)',
    );
    expect(alert).not.toHaveTextContent('에이전트 상태');
  });

  it('실패한 동안은 다시 읽는 중에도 알림이 계속 보인다', async () => {
    const backend = stubBackend();
    backend.online = false;
    const client = testClient();
    const Wrapper = wrapper(client);
    render(
      <Wrapper>
        <Screen />
      </Wrapper>,
    );
    await screen.findByRole('alert');

    const release = holdResponses(backend);
    act(() => {
      void client.refetchQueries({ queryKey: ['vms'], exact: true });
    });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(screen.getByRole('alert')).toHaveTextContent('VM 목록');
    release();
  });

  it('다시 성공하면 알림을 내리고 복구 알림을 한 번 띄운다', async () => {
    const backend = stubBackend();
    backend.online = false;
    const client = testClient();
    const Wrapper = wrapper(client);
    render(
      <Wrapper>
        <Screen />
      </Wrapper>,
    );
    await screen.findByRole('alert');

    backend.online = true;
    await act(() => client.refetchQueries({ queryKey: ['vms'], exact: true }));

    await waitFor(() => expect(screen.queryByText('실시간 데이터를 불러오지 못하고 있습니다')).toBeNull());
    expect(await screen.findByText('실시간 데이터 연결이 복구되었습니다')).toBeInTheDocument();
  });

  it('페이지에 알림 자리가 있으면 그 자리에만 한 번 보이고, 없으면 페이지 위에 보인다', async () => {
    const backend = stubBackend();
    backend.online = false;
    const Wrapper = wrapper();
    const { rerender } = render(
      <Wrapper>
        <Screen withSlot />
      </Wrapper>,
    );

    const alert = await screen.findByRole('alert');
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByTestId('page')).toContainElement(alert);

    rerender(
      <Wrapper>
        <Screen />
      </Wrapper>,
    );
    await waitFor(() => expect(screen.getByTestId('page')).toBeEmptyDOMElement());
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });

  it('처음부터 연결돼 있으면 복구 알림을 띄우지 않는다', async () => {
    stubBackend();
    const client = testClient();
    const Wrapper = wrapper(client);
    render(
      <Wrapper>
        <Screen />
      </Wrapper>,
    );
    await waitFor(() => expect(client.getQueryState(['vms'])?.status).toBe('success'));

    expect(screen.queryByText('실시간 데이터 연결이 복구되었습니다')).toBeNull();
    expect(screen.queryByText('실시간 데이터를 불러오지 못하고 있습니다')).toBeNull();
  });
});

describe('useNow', () => {
  it('데이터가 그대로여도 1초마다 바뀐다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-05T02:00:00Z'));
    const { result } = renderHook(() => useNow());
    const first = result.current;

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(result.current - first).toBe(1000);
  });
});
