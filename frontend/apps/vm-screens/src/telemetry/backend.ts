/**
 * 백엔드 연결 — 토큰 받기와 인증 WebSocket.
 *   로그인·토큰 재발급: 노션 API 명세 "로그인"·"토큰 재발급" (refresh 쿠키, Path=/api/v1/auth)
 *   WebSocket: 첫 프레임 {"type":"auth","access_token"}, 실패하면 4401 로 닫힌다 (WS-01·WS-02 와 같은 규칙)
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export const UNAUTHORIZED = 4401;

/** API_BASE 가 비면 같은 origin (개발 서버 프록시) */
export function wsUrl(path: string): string {
  const origin = API_BASE || window.location.origin;
  return origin.replace(/^http/, 'ws') + path;
}

async function token(path: string, body?: unknown): Promise<string | null> {
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include',
    });
    if (!response.ok) return null;
    const json = (await response.json()) as { access_token?: string };
    return json.access_token ?? null;
  } catch {
    return null;
  }
}

/**
 * 모니터 화면은 로그인 화면이 없다. 같은 브라우저에서 관리 콘솔에 로그인했으면 refresh 쿠키로 받고,
 * 없으면 개발용 계정(.env.local 의 VITE_SCREEN_USERNAME·PASSWORD)으로 로그인한다.
 * 키오스크 장치 인증 방식은 화면 정의서 §7 열린 질문.
 */
export function accessToken(fresh = false): Promise<string | null> {
  // 소켓 여러 개가 동시에 재발급하면 서버가 refresh 토큰을 회전시켜 두 번째가 실패한다 → 한 번만 받아 같이 쓴다
  if (!cached || (fresh && !inflight)) {
    inflight = true;
    cached = fetchToken().finally(() => (inflight = false));
  }
  return cached;
}

let cached: Promise<string | null> | null = null;
let inflight = false;

async function fetchToken(): Promise<string | null> {
  const refreshed = await token('/api/v1/auth/refresh');
  if (refreshed) return refreshed;
  const username = import.meta.env.VITE_SCREEN_USERNAME;
  const password = import.meta.env.VITE_SCREEN_PASSWORD;
  if (!username || !password) return null;
  return token('/api/v1/auth/login', { username, password });
}

export type Connection = 'connecting' | 'live' | 'offline' | 'unauthorized';

export interface SocketOptions {
  path: string;
  /** fresh 면 갖고 있던 토큰을 버리고 새로 받는다 (4401 뒤) */
  getToken: (fresh: boolean) => Promise<string | null>;
  /** 인증 뒤 보낼 프레임 (구독 등) */
  afterAuth?: (send: (frame: unknown) => void) => void;
  onFrame: (frame: { type: string } & Record<string, unknown>) => void;
  onConnection: (state: Connection) => void;
}

const RETRY_MS = 2000;

/** 끊기면 2초 뒤 다시 붙는다. 반환값을 부르면 닫고 멈춘다 */
export function openSocket(options: SocketOptions): () => void {
  let socket: WebSocket | null = null;
  let timer: number | undefined;
  let stopped = false;
  let fresh = false;

  const retry = () => {
    if (!stopped) timer = window.setTimeout(connect, RETRY_MS);
  };

  async function connect() {
    options.onConnection('connecting');
    const accessToken = await options.getToken(fresh);
    fresh = false;
    if (stopped) return;
    if (!accessToken) {
      options.onConnection('unauthorized');
      fresh = true;
      retry();
      return;
    }
    const ws = new WebSocket(wsUrl(options.path));
    socket = ws;
    const send = (frame: unknown) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(frame));
    ws.onopen = () => send({ type: 'auth', access_token: accessToken });
    ws.onmessage = (message) => {
      const frame = JSON.parse(String(message.data)) as { type: string } & Record<string, unknown>;
      if (frame.type === 'authenticated') {
        options.onConnection('live');
        options.afterAuth?.(send);
        return;
      }
      options.onFrame(frame);
    };
    ws.onclose = (event) => {
      if (socket === ws) socket = null;
      if (stopped) return;
      fresh = event.code === UNAUTHORIZED;
      options.onConnection(fresh ? 'unauthorized' : 'offline');
      retry();
    };
  }

  void connect();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    socket?.close();
  };
}
