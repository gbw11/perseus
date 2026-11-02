import { getSession, refreshSession } from './client';

export type ConsoleState = 'idle' | 'connecting' | 'connected' | 'closing' | 'disconnected' | 'error';
export interface ConsoleCallbacks {
  state: (state: ConsoleState, message: string) => void;
  data: (bytes: Uint8Array) => void;
}

const MAX_MESSAGE = 65536;
const MAX_PENDING_INPUT = 262144;
const CONNECT_TIMEOUT_MS = 15000;
// Spring waits up to 8 seconds for upstream PTY/lease cleanup.
const DISCONNECT_TIMEOUT_MS = 10000;
const encoder = new TextEncoder();

export function consoleUrl(vmId: string): string {
  const base = new URL(import.meta.env.VITE_API_BASE_URL || window.location.origin, window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  base.pathname = `${base.pathname.replace(/\/$/, '')}/ws/v1/vms/${encodeURIComponent(vmId)}/console`;
  base.search = '';
  base.hash = '';
  return base.toString();
}

export function consoleCloseMessage(code: number): string {
  switch (code) {
    case 4401:
      return '로그인이 만료되었거나 인증에 실패했습니다. 다시 연결해 주세요.';
    case 4404:
      return 'VM을 찾을 수 없거나 실행이 종료되었습니다.';
    case 4409:
      return '다른 콘솔이 사용 중이거나 VM 실행 상태가 변경되었습니다.';
    case 4400:
      return '접속할 수 없는 VM입니다.';
    case 1009:
    case 4009:
      return '전송량이 한도를 초과해 연결을 종료했습니다.';
    case 1000:
    case 1001:
      return '콘솔 연결이 종료되었습니다.';
    default:
      return '콘솔에 연결할 수 없습니다. 서버 연결과 VM 상태를 확인해 주세요.';
  }
}

/** No automatic reconnect or input replay. A new connection requires an explicit user action. */
export class ConsoleTransport {
  private socket: WebSocket | null = null;
  private generation = 0;
  private ready = false;
  private closing = false;
  private authenticated = false;
  private waitTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly vmId: string,
    private readonly callbacks: ConsoleCallbacks,
  ) {}

  async connect(): Promise<void> {
    if (this.closing) return;
    const generation = ++this.generation;
    this.stopSocket();
    this.callbacks.state('connecting', '콘솔에 연결하는 중…');
    this.waitFor(
      generation,
      CONNECT_TIMEOUT_MS,
      '콘솔 연결 시간이 초과되었습니다. 서버와 VM 상태를 확인한 뒤 다시 연결해 주세요.',
    );
    let session = getSession();
    try {
      if (!session || !(Date.parse(session.expires_at) > Date.now())) {
        await refreshSession();
        session = getSession();
      }
    } catch {
      this.fail(generation, consoleCloseMessage(4401));
      return;
    }
    if (generation !== this.generation) return;
    if (!session || !(Date.parse(session.expires_at) > Date.now())) {
      this.fail(generation, consoleCloseMessage(4401));
      return;
    }
    const token = session.access_token;
    let ws: WebSocket;
    try {
      ws = new WebSocket(consoleUrl(this.vmId));
    } catch {
      this.fail(generation, consoleCloseMessage(1006));
      return;
    }
    this.socket = ws;
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      if (generation !== this.generation) return;
      try {
        ws.send(JSON.stringify({ type: 'auth', access_token: token }));
        this.authenticated = true;
        if (this.closing) ws.send(JSON.stringify({ type: 'disconnect' }));
      } catch {
        this.fail(generation, consoleCloseMessage(1006));
      }
    };
    ws.onmessage = (event: MessageEvent<unknown>) => {
      if (generation !== this.generation) return;
      if (this.closing) return;
      if (event.data instanceof ArrayBuffer && this.ready) {
        this.callbacks.data(new Uint8Array(event.data));
        return;
      }
      if (typeof event.data === 'string' && !this.ready) {
        try {
          const frame = JSON.parse(event.data) as { type?: string; vm_id?: string };
          if (frame.type === 'connected' && frame.vm_id === this.vmId) {
            this.clearWait();
            this.ready = true;
            this.callbacks.state('connected', '콘솔이 연결되었습니다.');
            return;
          }
        } catch {
          // A malformed control frame must never be rendered as terminal output.
        }
      }
      this.fail(generation, '콘솔 응답 형식이 올바르지 않아 연결을 종료했습니다.', 4003);
    };
    ws.onclose = (event) => {
      if (generation !== this.generation) return;
      ++this.generation;
      this.clearWait();
      this.socket = null;
      this.ready = false;
      this.closing = false;
      this.authenticated = false;
      this.callbacks.state(
        event.code === 1000 || event.code === 1001 ? 'disconnected' : 'error',
        consoleCloseMessage(event.code),
      );
    };
  }

  send(text: string): boolean {
    return this.sendBytes(encoder.encode(text));
  }

  sendBytes(bytes: Uint8Array): boolean {
    const ws = this.socket;
    if (!this.ready || ws?.readyState !== WebSocket.OPEN) return false;
    if (ws.bufferedAmount + bytes.byteLength > MAX_PENDING_INPUT) {
      ws.close(4009, 'input backlog limit');
      return false;
    }
    for (let start = 0; start < bytes.length; start += MAX_MESSAGE) {
      ws.send(bytes.slice(start, start + MAX_MESSAGE));
    }
    return true;
  }

  disconnect(): void {
    if (this.closing) return;
    if (!this.socket) {
      this.dispose();
      this.callbacks.state('disconnected', '콘솔 연결을 해제했습니다.');
      return;
    }
    this.ready = false;
    this.closing = true;
    this.callbacks.state('closing', '콘솔 연결을 정리하는 중…');
    this.waitFor(
      this.generation,
      DISCONNECT_TIMEOUT_MS,
      '연결 정리 응답이 없어 브라우저 연결을 닫았습니다. 서버 점유 해제는 확인되지 않았습니다. 잠시 후 다시 연결해 주세요.',
    );
    if (this.authenticated && this.socket.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify({ type: 'disconnect' }));
      } catch {
        this.fail(this.generation, consoleCloseMessage(1006));
      }
    }
  }

  dispose(): void {
    ++this.generation;
    this.stopSocket();
  }

  private stopSocket(code?: number): void {
    this.clearWait();
    this.ready = false;
    this.closing = false;
    this.authenticated = false;
    const ws = this.socket;
    this.socket = null;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      if (code === undefined) ws.close();
      else ws.close(code, 'unexpected console frame');
    }
  }

  private clearWait(): void {
    if (this.waitTimer !== null) clearTimeout(this.waitTimer);
    this.waitTimer = null;
  }

  private waitFor(generation: number, milliseconds: number, message: string): void {
    this.clearWait();
    this.waitTimer = setTimeout(() => this.fail(generation, message), milliseconds);
  }

  private fail(generation: number, message: string, code?: number): void {
    if (generation !== this.generation) return;
    ++this.generation;
    // Invalidate callbacks before closing: a late frame/close must not undo the error.
    this.stopSocket(code);
    this.callbacks.state('error', message);
  }
}
