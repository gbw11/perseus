import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConsoleTransport, consoleCloseMessage, NO_RETRY_CODES } from './console';

const auth = vi.hoisted(() => ({ getSession: vi.fn(), refreshSession: vi.fn() }));
vi.mock('./client', () => auth);

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  binaryType = '';
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  sent: unknown[] = [];
  close = vi.fn();
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  ready() {
    this.onmessage?.({ data: JSON.stringify({ type: 'connected', vm_id: 'vm-a' }) });
  }
}

describe('console transport', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    auth.getSession.mockReturnValue({ access_token: 'user-jwt', expires_at: '2099-01-01T00:00:00Z' });
    auth.refreshSession.mockReset();
  });
  afterEach(() => vi.unstubAllGlobals());

  function setup() {
    const callbacks = { state: vi.fn(), data: vi.fn() };
    return { transport: new ConsoleTransport('vm-a', callbacks), callbacks };
  }

  it('JWT is sent in the first frame, never in the URL; input waits for upstream readiness', async () => {
    const { transport, callbacks } = setup();
    await transport.connect();
    const ws = FakeSocket.instances[0]!;
    expect(ws.url).not.toContain('user-jwt');
    expect(transport.send('ls\r')).toBe(false);
    ws.onopen?.();
    expect(JSON.parse(ws.sent[0] as string)).toEqual({ type: 'auth', access_token: 'user-jwt' });
    ws.ready();
    expect(callbacks.state).toHaveBeenLastCalledWith('connected', expect.any(String));
    expect(transport.send('한글\r\x03')).toBe(true);
    expect(ws.sent[1]).toEqual(new TextEncoder().encode('한글\r\x03'));
    const bytes = new Uint8Array([0xe3, 0x81]).buffer;
    ws.onmessage?.({ data: bytes });
    expect(callbacks.data).toHaveBeenCalledWith(new Uint8Array(bytes));
    transport.dispose();
  });

  it('large paste is split at frame limit, while an excessive backlog sends nothing', async () => {
    const { transport } = setup();
    await transport.connect();
    const ws = FakeSocket.instances[0]!;
    ws.ready();
    expect(transport.send('x'.repeat(65537))).toBe(true);
    expect((ws.sent[0] as Uint8Array).length).toBe(65536);
    expect((ws.sent[1] as Uint8Array).length).toBe(1);
    const count = ws.sent.length;
    ws.bufferedAmount = 262144;
    expect(transport.send('do not partially paste')).toBe(false);
    expect(ws.sent).toHaveLength(count);
    expect(ws.close).toHaveBeenCalledWith(4009, expect.any(String));
    transport.dispose();
  });

  it('disconnect during token refresh does not open a late socket', async () => {
    auth.getSession.mockReturnValue(null);
    let resolve!: (value: boolean) => void;
    auth.refreshSession.mockReturnValue(
      new Promise<boolean>((done) => {
        resolve = done;
      }),
    );
    const { transport } = setup();
    const pending = transport.connect();
    transport.dispose();
    resolve(true);
    await pending;
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it('duplicate rejection is shown without automatic reconnect or input replay', async () => {
    const { transport, callbacks } = setup();
    await transport.connect();
    const ws = FakeSocket.instances[0]!;
    ws.onclose?.({ code: 4409 });
    expect(callbacks.state).toHaveBeenLastCalledWith('error', consoleCloseMessage(4409), 4409);
    expect(NO_RETRY_CODES).toContain(4409); // 화면도 다시 붙지 않는다
    expect(transport.send('old input')).toBe(false);
    expect(FakeSocket.instances).toHaveLength(1);
    transport.dispose();
  });

  it('reconnect waits for upstream cleanup acknowledgement, including cancellation during setup', async () => {
    const { transport, callbacks } = setup();
    await transport.connect();
    const ws = FakeSocket.instances[0]!;
    transport.disconnect();
    expect(callbacks.state).toHaveBeenLastCalledWith('closing', expect.any(String));
    ws.onopen?.();
    expect(ws.sent.map((frame) => JSON.parse(frame as string).type)).toEqual(['auth', 'disconnect']);
    ws.ready(); // A connected frame already in flight must not re-enable terminal input.
    expect(transport.send('do not send')).toBe(false);
    await transport.connect();
    expect(FakeSocket.instances).toHaveLength(1);
    expect(ws.close).not.toHaveBeenCalled();
    ws.onclose?.({ code: 1000 }); // Server closes only after releasing PTY and VM lease.
    expect(callbacks.state).toHaveBeenLastCalledWith('disconnected', expect.any(String), 1000);
    await transport.connect();
    expect(FakeSocket.instances).toHaveLength(2);
    transport.dispose();
  });

  it('disposing releases callbacks and closes the socket; stale frames cannot update UI', async () => {
    const { transport, callbacks } = setup();
    await transport.connect();
    const ws = FakeSocket.instances[0]!;
    const stale = ws.onmessage;
    transport.dispose();
    stale?.({ data: JSON.stringify({ type: 'connected', vm_id: 'vm-a' }) });
    expect(ws.close).toHaveBeenCalled();
    expect(callbacks.state).not.toHaveBeenCalledWith('connected', expect.any(String));
  });
});
