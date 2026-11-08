/** TanStack Query 훅. 경로·응답은 VM 관리 API 계약 §5, 인증은 노션 API 명세 */
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useState } from 'react';
import { request, setSession } from './client';
import type {
  CreateVmRequest,
  Display,
  DisplayListResponse,
  EventListResponse,
  LoginRequest,
  MetricHistoryResponse,
  ProcessesResponse,
  RouteRequest,
  SignupRequest,
  SignupResponse,
  StressCommand,
  StressRequest,
  TelemetryHeartbeatResponse,
  Template,
  ThreadsResponse,
  TokenResponse,
  UpdateVcpuPinningRequest,
  Vm,
  VmListResponse,
  VmResourceUpdate,
  VmResources,
} from './types';
import { clearVmList, loadVmList, saveVmList } from './vmListCache';

export const keys = {
  vms: ['vms'] as const,
  vm: (id: string) => ['vms', id] as const,
  vmResources: (id: string) => ['vms', id, 'resources'] as const,
  templates: ['templates'] as const,
  events: (query: string) => ['events', query] as const,
  metrics: (id: string, minutes = 5) => ['vms', id, 'metrics', minutes] as const,
  processes: (id: string) => ['vms', id, 'processes'] as const,
  threads: (id: string) => ['vms', id, 'threads'] as const,
  telemetryHeartbeat: (id: string) => ['vms', id, 'telemetry-heartbeat'] as const,
  displays: ['displays'] as const,
};

/**
 * 실시간 화면은 모두 이 주기 이하로 다시 읽는다. 에이전트 metrics·heartbeat 송신 주기(2초)와 같아
 * 새 값이 들어오면 늦어도 한 주기 안에 화면에 보인다.
 */
export const LIVE_POLL_MS = 2000;

/** 마지막 조회 결과가 실패인가. 성공 시각보다 실패 시각이 늦으면 실패 중이다 */
export function lastFetchFailed(state: { errorUpdatedAt: number; dataUpdatedAt: number }): boolean {
  return state.errorUpdatedAt > state.dataUpdatedAt;
}

/**
 * 마지막 조회가 실패했으면 이전 값을 지우고, 다시 성공할 때까지 실패 상태를 그대로 유지한다.
 * 보드가 꺼져도 직전 목록("실행 중")이 정상처럼 남지 않게 한다.
 * TanStack Query 는 값이 없는 조회를 다시 읽기 시작할 때마다 status 를 pending, error 를 null 로 되돌려
 * 오류 표시가 주기마다 사라졌다 나타나므로, 실패 여부는 성공·실패 시각으로 판정하고 마지막 오류를 기억해 둔다.
 * 한 번 실패는 재시도(main.tsx retry)가 먼저 흡수하므로 일시적인 끊김에는 값이 사라지지 않는다.
 */
function useWithoutStaleData<TData, TError>(
  result: UseQueryResult<TData, TError>,
  keepLastData = false,
): UseQueryResult<TData, TError> {
  // 렌더 중 이전 값 갱신 (React 문서 "Storing information from previous renders")
  const [lastError, setLastError] = useState<TError | null>(null);
  if (result.error && result.error !== lastError) setLastError(result.error);
  if (!lastFetchFailed(result)) return result;
  return {
    ...result,
    data: keepLastData ? result.data : undefined,
    error: result.error ?? lastError,
    status: 'error',
    isError: true,
    isPending: false,
    isLoading: false,
    isLoadingError: false,
    isSuccess: false,
  } as UseQueryResult<TData, TError>;
}

/**
 * pollMs: VM Control 은 1초 (관측 시각이 초 단위로 바뀌게), 나머지는 LIVE_POLL_MS.
 * keepLastData: 실패해도 마지막 목록을 남긴다 — 화면이 직접 "조회 불가" 로 표시할 때만 (대시보드 Overview 카드)
 */
export function useVms(pollMs = LIVE_POLL_MS, { keepLastData = false }: { keepLastData?: boolean } = {}) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.vms,
      queryFn: async ({ signal }) => {
        try {
          const list = await request<VmListResponse>('/api/v1/vms', { signal });
          saveVmList(list);
          return list;
        } catch (error) {
          // 새로고침해도 실패 전 목록을 다시 그리지 않는다
          clearVmList();
          throw error;
        }
      },
      refetchInterval: pollMs,
      // 새로고침 직후에는 이 탭이 마지막으로 받은 목록을 먼저 보여 주고 곧바로 다시 읽는다 (S15P31E110-345)
      initialData: () => loadVmList()?.data,
      initialDataUpdatedAt: () => loadVmList()?.savedAt,
    }),
    keepLastData,
  );
}

export function useVm(vmId: string) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.vm(vmId),
      queryFn: ({ signal }) => request<Vm>(`/api/v1/vms/${encodeURIComponent(vmId)}`, { signal }),
      refetchInterval: LIVE_POLL_MS,
    }),
  );
}

export function useVmResources(vmId: string) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.vmResources(vmId),
      queryFn: ({ signal }) =>
        request<VmResources>(`/api/v1/vms/${encodeURIComponent(vmId)}/resources`, { signal }),
      enabled: Boolean(vmId),
      refetchInterval: LIVE_POLL_MS,
    }),
  );
}

export function useUpdateVcpuPinning(vmId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateVcpuPinningRequest) =>
      request<VmResources>(`/api/v1/vms/${encodeURIComponent(vmId)}/resources/vcpu-pinning`, {
        method: 'PUT',
        body,
      }),
    onSettled: () => client.invalidateQueries({ queryKey: keys.vmResources(vmId) }),
  });
}

export function useUpdateVmResource(vmId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, body }: VmResourceUpdate) =>
      request<VmResources>(`/api/v1/vms/${encodeURIComponent(vmId)}/resources/${kind}`, {
        method: 'PUT',
        body,
      }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.vmResources(vmId) });
      void client.invalidateQueries({ queryKey: keys.vm(vmId) });
      void client.invalidateQueries({ queryKey: keys.vms });
    },
  });
}

export function useStartStress(vmId: string) {
  return useMutation({
    mutationFn: ({ kind, body }: StressRequest) =>
      request<StressCommand>(`/api/v1/vms/${encodeURIComponent(vmId)}/stress/${kind}`, {
        method: 'POST',
        body,
      }),
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: ({ signal }) => request<Template[]>('/api/v1/templates', { signal }),
    staleTime: Infinity, // 설정 파일 고정 데이터 (계약 §2.3)
  });
}

/** 이벤트 목록. 실시간 추가는 WS /ws/v1/events 대신 짧은 주기 재조회로 한다 (초안) */
export function useEvents(params: { vm_id?: string; type?: string; severity?: string; limit?: number } = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v != null && v !== '') as [string, string][],
  ).toString();
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.events(query),
      queryFn: ({ signal }) =>
        request<EventListResponse>(`/api/v1/events${query ? `?${query}` : ''}`, { signal }),
      refetchInterval: LIVE_POLL_MS,
    }),
  );
}

/** 최근 n 분 이력. keepLastData는 조회 오류와 원 표본 만료를 직접 표시하는 관측 화면에서만 쓴다. */
export function useMetrics(
  vmId: string,
  enabled: boolean,
  minutes = 5,
  pollMs = LIVE_POLL_MS,
  { keepLastData = false }: { keepLastData?: boolean } = {},
) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.metrics(vmId, minutes),
      queryFn: ({ signal }) => {
        const from = new Date(Date.now() - minutes * 60_000).toISOString();
        return request<MetricHistoryResponse>(
          `/api/v1/vms/${encodeURIComponent(vmId)}/metrics?from=${encodeURIComponent(from)}`,
          { signal },
        );
      },
      enabled,
      refetchInterval: pollMs,
    }),
    keepLastData,
  );
}

export function useProcesses(vmId: string, enabled: boolean) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.processes(vmId),
      queryFn: ({ signal }) =>
        request<ProcessesResponse>(`/api/v1/vms/${encodeURIComponent(vmId)}/processes`, { signal }),
      enabled,
      refetchInterval: LIVE_POLL_MS,
    }),
  );
}

export function useThreads(vmId: string) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.threads(vmId),
      queryFn: ({ signal }) =>
        request<ThreadsResponse>(`/api/v1/vms/${encodeURIComponent(vmId)}/threads`, { signal }),
      enabled: Boolean(vmId),
      refetchInterval: LIVE_POLL_MS,
    }),
  );
}

export function useTelemetryHeartbeat(vmId: string) {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.telemetryHeartbeat(vmId),
      queryFn: ({ signal }) =>
        request<TelemetryHeartbeatResponse>(`/api/v1/vms/${encodeURIComponent(vmId)}/telemetry/heartbeat`, {
          signal,
        }),
      enabled: Boolean(vmId),
      refetchInterval: LIVE_POLL_MS,
      retry: false,
    }),
  );
}

/**
 * 화면 배치 (REST-23). 모니터가 바꾼 결과(SWITCHING → ACTIVE)를 빨리 보이게 짧게 다시 읽는다.
 * 실시간 WS /ws/v1/displays 는 모니터 브라우저용이라 콘솔은 조회로 충분하다.
 */
export function useDisplays() {
  return useWithoutStaleData(
    useQuery({
      queryKey: keys.displays,
      queryFn: ({ signal }) => request<DisplayListResponse>('/api/v1/displays', { signal }),
      refetchInterval: 1000,
    }),
  );
}

/** 화면 배치 바꾸기 (REST-24). 202 — 모니터가 route.applied 로 답해야 ACTIVE. 409 면 다른 사람이 먼저 바꿈 */
export function useRouteDisplay() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ displayId, body }: { displayId: string; body: RouteRequest }) =>
      request<Display>(`/api/v1/displays/${encodeURIComponent(displayId)}/route`, { method: 'PUT', body }),
    onSettled: () => client.invalidateQueries({ queryKey: keys.displays }),
  });
}

export type VmAction = 'start' | 'stop' | 'delete';

export function useVmAction() {
  const client = useQueryClient();
  return useMutation({
    /** start·stop 은 VM, delete 는 204 (계약 §5) */
    mutationFn: ({ vmId, action }: { vmId: string; action: VmAction }): Promise<Vm | void> =>
      action === 'delete'
        ? request<void>(`/api/v1/vms/${encodeURIComponent(vmId)}`, { method: 'DELETE' })
        : request<Vm>(`/api/v1/vms/${encodeURIComponent(vmId)}/${action}`, { method: 'POST' }),
    onSettled: () => client.invalidateQueries({ queryKey: keys.vms }),
  });
}

export function useCreateVm() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateVmRequest) => request<Vm>('/api/v1/vms', { method: 'POST', body }),
    onSettled: () => client.invalidateQueries({ queryKey: keys.vms }),
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: (body: LoginRequest) =>
      request<TokenResponse>('/api/v1/auth/login', { method: 'POST', body, auth: false }),
    onSuccess: setSession,
  });
}

export function useSignup() {
  return useMutation({
    mutationFn: (body: SignupRequest) =>
      request<SignupResponse>('/api/v1/auth/signup', { method: 'POST', body, auth: false }),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<void>('/api/v1/auth/logout', { method: 'POST' }),
    onSettled: () => {
      setSession(null);
      clearVmList();
      client.clear();
    },
  });
}
