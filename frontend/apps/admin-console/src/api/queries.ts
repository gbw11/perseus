/** TanStack Query 훅. 경로·응답은 VM 관리 API 계약 §5, 인증은 노션 API 명세 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request, setSession } from './client';
import type { CreateVmRequest, LoginRequest, Template, TokenResponse, Vm, VmListResponse } from './types';

export const keys = {
  vms: ['vms'] as const,
  vm: (id: string) => ['vms', id] as const,
  templates: ['templates'] as const,
};

/** 백엔드가 5초마다 Xen 상태를 확인하므로(계약 §5 끝) 화면도 같은 주기로 다시 읽는다 */
const POLL_MS = 5000;

export function useVms() {
  return useQuery({
    queryKey: keys.vms,
    queryFn: ({ signal }) => request<VmListResponse>('/api/v1/vms', { signal }),
    refetchInterval: POLL_MS,
  });
}

export function useVm(vmId: string) {
  return useQuery({
    queryKey: keys.vm(vmId),
    queryFn: ({ signal }) => request<Vm>(`/api/v1/vms/${encodeURIComponent(vmId)}`, { signal }),
    refetchInterval: POLL_MS,
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: ({ signal }) => request<Template[]>('/api/v1/templates', { signal }),
    staleTime: Infinity, // 설정 파일 고정 데이터 (계약 §2.3)
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
