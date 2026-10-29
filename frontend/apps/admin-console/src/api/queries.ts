/** TanStack Query 훅. 경로·응답은 VM 관리 API 계약 §5, 인증은 노션 API 명세 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  Template,
  TokenResponse,
  UpdateVcpuPinningRequest,
  Vm,
  VmListResponse,
  VmResources,
} from './types';

export const keys = {
  vms: ['vms'] as const,
  vm: (id: string) => ['vms', id] as const,
  vmResources: (id: string) => ['vms', id, 'resources'] as const,
  templates: ['templates'] as const,
  events: (query: string) => ['events', query] as const,
  metrics: (id: string, minutes = 5) => ['vms', id, 'metrics', minutes] as const,
  processes: (id: string) => ['vms', id, 'processes'] as const,
  displays: ['displays'] as const,
};

/** 백엔드가 5초마다 Xen 상태를 확인하므로(계약 §5 끝) 화면도 같은 주기로 다시 읽는다 */
const POLL_MS = 5000;

/** pollMs: VM 화면은 1초 (관측 시각이 초 단위로 바뀌게), 나머지는 5초 */
export function useVms(pollMs = POLL_MS) {
  return useQuery({
    queryKey: keys.vms,
    queryFn: ({ signal }) => request<VmListResponse>('/api/v1/vms', { signal }),
    refetchInterval: pollMs,
  });
}

export function useVm(vmId: string) {
  return useQuery({
    queryKey: keys.vm(vmId),
    queryFn: ({ signal }) => request<Vm>(`/api/v1/vms/${encodeURIComponent(vmId)}`, { signal }),
    refetchInterval: POLL_MS,
  });
}

export function useVmResources(vmId: string) {
  return useQuery({
    queryKey: keys.vmResources(vmId),
    queryFn: ({ signal }) =>
      request<VmResources>(`/api/v1/vms/${encodeURIComponent(vmId)}/resources`, { signal }),
    enabled: Boolean(vmId),
    refetchInterval: POLL_MS,
  });
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
  return useQuery({
    queryKey: keys.events(query),
    queryFn: ({ signal }) =>
      request<EventListResponse>(`/api/v1/events${query ? `?${query}` : ''}`, { signal }),
    refetchInterval: 3000,
  });
}

/** 최근 n 분 메트릭 (Linux VM 만, 에이전트 MQTT-03) */
export function useMetrics(vmId: string, enabled: boolean, minutes = 5, pollMs = POLL_MS) {
  return useQuery({
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
  });
}

export function useProcesses(vmId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.processes(vmId),
    queryFn: ({ signal }) =>
      request<ProcessesResponse>(`/api/v1/vms/${encodeURIComponent(vmId)}/processes`, { signal }),
    enabled,
    refetchInterval: POLL_MS,
  });
}

/**
 * 화면 배치 (REST-23). 모니터가 바꾼 결과(SWITCHING → ACTIVE)를 빨리 보이게 짧게 다시 읽는다.
 * 실시간 WS /ws/v1/displays 는 모니터 브라우저용이라 콘솔은 조회로 충분하다.
 */
export function useDisplays() {
  return useQuery({
    queryKey: keys.displays,
    queryFn: ({ signal }) => request<DisplayListResponse>('/api/v1/displays', { signal }),
    refetchInterval: 1000,
  });
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
      client.clear();
    },
  });
}
