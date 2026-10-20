/**
 * fetch 래퍼. Access Token 을 붙이고, 401 이면 한 번 재발급(쿠키) 후 다시 시도한다.
 * 인증 흐름은 노션 API 명세 "로그인"·"토큰 재발급"을 따른다.
 */
import type { ApiErrorBody, TokenResponse } from './types';

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: ApiErrorBody['errors'];

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message || `요청이 실패했습니다 (${status})`);
    this.status = status;
    this.code = body.code || (status === 0 ? 'NETWORK_ERROR' : 'UNKNOWN_ERROR');
    this.fieldErrors = body.errors;
  }
}

// ---- 세션 (Access Token 은 메모리에만 둔다. 새로고침하면 refresh 쿠키로 다시 받는다) ----

type Listener = (session: TokenResponse | null) => void;
let session: TokenResponse | null = null;
const listeners = new Set<Listener>();

export function getSession(): TokenResponse | null {
  return session;
}

export function setSession(next: TokenResponse | null): void {
  session = next;
  listeners.forEach((listener) => listener(next));
}

export function onSessionChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ---- 요청 -----------------------------------------------------------------------------

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  /** 인증 API(/api/v1/auth/**)는 토큰을 붙이지 않고 재발급도 하지 않는다 */
  auth?: boolean;
  signal?: AbortSignal;
}

async function parse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.auth !== false && session) headers.Authorization = `Bearer ${session.access_token}`;
  try {
    return await fetch(`${BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'include', // refresh 쿠키 (Path=/api/v1/auth)
      signal: options.signal,
    });
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error;
    throw new ApiError(0, { message: '서버에 연결할 수 없습니다.' });
  }
}

let refreshing: Promise<boolean> | null = null;

/** 동시에 여러 요청이 401 을 받아도 재발급은 한 번만 (서버가 토큰을 회전시키므로) */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const response = await send('/api/v1/auth/refresh', { method: 'POST', auth: false });
      if (!response.ok) {
        setSession(null);
        return false;
      }
      setSession(await parse<TokenResponse>(response));
      return true;
    } catch {
      setSession(null);
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response = await send(path, options);
  if (response.status === 401 && options.auth !== false && (await refreshSession())) {
    response = await send(path, options);
  }
  if (!response.ok) {
    const body = await parse<Partial<ApiErrorBody>>(response).catch(() => ({}));
    if (response.status === 401 && options.auth !== false) setSession(null);
    throw new ApiError(response.status, body ?? {});
  }
  return parse<T>(response);
}
