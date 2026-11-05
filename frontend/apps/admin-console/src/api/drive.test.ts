import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setSession } from './client';
import { cancelResume, KEY_OF, pressKey, requestResume, type DriveKey } from './drive';

describe('pressKey', () => {
  it('누르고 떼면 집합이 바뀌고, 계약 §7 순서로 정렬한다', () => {
    let held: DriveKey[] = [];
    held = pressKey(held, 'left', true);
    held = pressKey(held, 'up', true);
    expect(held).toEqual(['up', 'left']);
    held = pressKey(held, 'left', false);
    expect(held).toEqual(['up']);
  });

  it('키 반복(keydown 연타)이나 이미 뗀 키는 같은 배열을 돌려준다 — 다시 보내지 않는다', () => {
    const held: DriveKey[] = ['up'];
    expect(pressKey(held, 'up', true)).toBe(held);
    expect(pressKey(held, 'down', false)).toBe(held);
  });

  it('화살표·WASD·스페이스를 계약 키로 바꾼다', () => {
    expect(KEY_OF.ArrowUp).toBe('up');
    expect(KEY_OF.a).toBe('left');
    expect(KEY_OF[' ']).toBe('brake');
    expect(KEY_OF.Enter).toBeUndefined();
  });
});

describe('자율주행 재개·취소 (POST·DELETE /api/v1/drive/resume, 계약 v1.7)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    setSession({
      access_token: 't',
      token_type: 'Bearer',
      expires_in: 900,
      expires_at: '2026-11-04T00:15:00Z',
      user: { user_id: 'u-1', username: 'admin', nickname: '관리자' },
    });
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    setSession(null);
  });
  const accepted = () =>
    new Response(JSON.stringify({ accepted: true }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    });

  it('재개는 POST, 취소는 같은 경로 DELETE', async () => {
    fetchMock.mockResolvedValueOnce(accepted()).mockResolvedValueOnce(accepted());
    await requestResume();
    await cancelResume();
    const calls = fetchMock.mock.calls.map(([url, init]) => [String(url), init?.method]);
    expect(calls[0]![0]).toContain('/api/v1/drive/resume');
    expect(calls[0]![1]).toBe('POST');
    expect(calls[1]![0]).toContain('/api/v1/drive/resume');
    expect(calls[1]![1]).toBe('DELETE');
  });

  it('카운트다운이 없으면 409 를 그대로 알린다', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: 'NO_RESUME_COUNTDOWN',
          message: '취소할 자율주행 재개 카운트다운이 없습니다.',
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    await expect(cancelResume()).rejects.toThrow();
  });
});
