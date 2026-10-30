import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ApiError, getSession, refreshSession, request, setSession } from '../api/client';
import {
  VM_CPU_PERCENT_METRIC,
  type MetricHistoryResponse,
  type TokenResponse,
  type Vm,
  type VmListResponse,
} from '../api/types';
import { MOCK_PASSWORD, VM_IDS } from './data';
import { handlers, resetMockState } from './handlers';

// 화면이 쓰는 실제 클라이언트(api/client.ts)로 mock 흐름을 돌린다
const server = setupServer(...handlers);

beforeAll(() => {
  server.listen({ onUnhandledFrame: 'error' });
  // Node fetch 는 상대 경로를 못 받는다. 브라우저처럼 현재 origin 을 붙인다 (MSW 가 감싼 fetch 위에)
  const mocked = globalThis.fetch;
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
    mocked(typeof input === 'string' ? new URL(input, window.location.origin) : input, init),
  );
});
afterEach(() => {
  resetMockState();
  setSession(null);
});
afterAll(() => {
  vi.unstubAllGlobals();
  server.close();
});

async function login(password = MOCK_PASSWORD) {
  const token = await request<TokenResponse>('/api/v1/auth/login', {
    method: 'POST',
    body: { username: 'admin', password },
    auth: false,
  });
  setSession(token);
  return token;
}

describe('MSW mock API', () => {
  it('로그인 → 목록 → 상세', async () => {
    const token = await login();
    expect(token.user.username).toBe('admin');

    const list = await request<VmListResponse>('/api/v1/vms');
    expect(list.items.map((vm) => vm.name)).toEqual(['autonomous', 'manual', 'interaction']);

    const vm = await request<Vm>(`/api/v1/vms/${list.items[2]!.vm_id}`);
    expect(vm.guest_kind).toBe('linux');
    expect(vm.agent_status).toBe('ONLINE');
  });

  it('틀린 비밀번호는 INVALID_CREDENTIALS, 토큰 없이는 401', async () => {
    await expect(login('wrong')).rejects.toMatchObject({ status: 401, code: 'INVALID_CREDENTIALS' });
    await expect(request('/api/v1/vms', { auth: false })).rejects.toMatchObject({ status: 401 });
  });

  it('refresh 쿠키로 새 토큰을 받는다 (새로고침 복원)', async () => {
    const first = await login();
    setSession(null);
    expect(await refreshSession()).toBe(true);
    expect(getSession()?.access_token).not.toBe(first.access_token);
  });

  it('정지 → 시작, 같은 상태로 다시 보내면 INVALID_VM_STATE', async () => {
    await login();
    const id = '11111111-1111-4111-8111-111111111111';
    expect((await request<Vm>(`/api/v1/vms/${id}/stop`, { method: 'POST' })).state).toBe('stopped');
    const again = request(`/api/v1/vms/${id}/stop`, { method: 'POST' });
    await expect(again).rejects.toBeInstanceOf(ApiError);
    await expect(again).rejects.toMatchObject({ status: 409, code: 'INVALID_VM_STATE' });
    expect((await request<Vm>(`/api/v1/vms/${id}/start`, { method: 'POST' })).state).toBe('running');
  });

  it('생성은 입력값을 검사하고, 같은 이름은 VM_NAME_CONFLICT', async () => {
    await login();
    const body = {
      name: 'extra',
      template_id: 'debian-13-arm64',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 256,
    };
    const created = await request<Vm>('/api/v1/vms', { method: 'POST', body });
    expect(created.name).toBe('extra');
    await expect(request('/api/v1/vms', { method: 'POST', body })).rejects.toMatchObject({
      status: 409,
      code: 'VM_NAME_CONFLICT',
    });
    await expect(
      request('/api/v1/vms', { method: 'POST', body: { ...body, name: ' x', vcpus: 9 } }),
    ).rejects.toMatchObject({
      status: 422,
      fieldErrors: [
        { field: 'name', reason: expect.any(String) },
        { field: 'vcpus', reason: '1~4' },
      ],
    });
  });

  it('삭제 뒤 상세는 VM_NOT_FOUND', async () => {
    await login();
    const id = '22222222-2222-4222-8222-222222222222';
    await request(`/api/v1/vms/${id}`, { method: 'DELETE' });
    await expect(request(`/api/v1/vms/${id}`)).rejects.toMatchObject({ status: 404, code: 'VM_NOT_FOUND' });
  });

  it('VM CPU 메트릭은 할당 vCPU 기준 필드명을 사용한다', async () => {
    await login();
    const metrics = await request<MetricHistoryResponse>(
      `/api/v1/vms/${VM_IDS.interaction}/metrics?from=${encodeURIComponent(new Date(0).toISOString())}`,
    );
    expect(metrics.items.at(-1)?.values).toContainEqual(
      expect.objectContaining({ name: VM_CPU_PERCENT_METRIC, unit: '%' }),
    );
    expect(
      metrics.items.flatMap((sample) => sample.values).some((value) => value.name === 'cpu_percent'),
    ).toBe(false);
  });
});
