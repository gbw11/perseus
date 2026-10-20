import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, getSession, request, setSession } from './client';
import type { TokenResponse } from './types';

const token = (value: string): TokenResponse => ({
  access_token: value,
  token_type: 'Bearer',
  expires_in: 900,
  expires_at: '2026-10-20T00:15:00Z',
  user: { user_id: 'u-1', username: 'admin' },
});

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('API 클라이언트', () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    setSession(token('old'));
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('Access Token 을 Bearer 로 붙인다', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [], observed_at: 'x' }));
    await request('/api/v1/vms');
    const headers = fetchMock.mock.calls[0]![1]!.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer old');
  });

  it('401 이면 refresh 로 재발급하고 한 번 다시 보낸다', async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { code: 'INVALID_TOKEN', message: '만료' }))
      .mockResolvedValueOnce(json(200, token('new')))
      .mockResolvedValueOnce(json(200, { ok: true }));
    await expect(request('/api/v1/vms')).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/v1/auth/refresh');
    expect(getSession()?.access_token).toBe('new');
    const retry = fetchMock.mock.calls[2]![1]!.headers as Record<string, string>;
    expect(retry.Authorization).toBe('Bearer new');
  });

  it('재발급도 실패하면 세션을 지우고 오류 코드를 그대로 낸다', async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { code: 'INVALID_TOKEN', message: '만료' }))
      .mockResolvedValueOnce(json(401, { code: 'INVALID_REFRESH_TOKEN', message: '재로그인' }))
      .mockResolvedValueOnce(json(401, { code: 'INVALID_TOKEN', message: '만료' }));
    await expect(request('/api/v1/vms')).rejects.toMatchObject({ status: 401, code: 'INVALID_TOKEN' });
    expect(getSession()).toBeNull();
  });

  it('계약 §8.2 오류 본문 {code, message, errors} 를 ApiError 로 바꾼다', async () => {
    fetchMock.mockResolvedValueOnce(
      json(422, {
        code: 'VALIDATION_ERROR',
        message: '입력 오류',
        errors: [{ field: 'name', reason: '빈 값' }],
      }),
    );
    const error = await request('/api/v1/vms', { method: 'POST', body: {} }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: 'VALIDATION_ERROR', fieldErrors: [{ field: 'name' }] });
  });

  it('204 는 본문 없이 끝난다', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(request('/api/v1/vms/x', { method: 'DELETE' })).resolves.toBeUndefined();
  });

  it('연결 실패는 NETWORK_ERROR', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(request('/api/v1/vms')).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' });
  });
});
