import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ws } from 'msw';
import { setupServer } from 'msw/node';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConsolePanel } from './ConsolePanel';
import type { Vm } from '../api/types';

const auth = vi.hoisted(() => ({ getSession: vi.fn(), refreshSession: vi.fn() }));
vi.mock('../api/client', () => auth);

// Exercise the real panel + transport + WebSocket mock server. Only xterm drawing
// is replaced: jsdom has no canvas. These tests do not prove physical PTY cleanup.
const terminals = vi.hoisted(() => ({
  instances: [] as Array<{
    options: { disableStdin: boolean };
    write: ReturnType<typeof vi.fn>;
    input?: (text: string) => void;
  }>,
}));
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options = { disableStdin: true };
    textarea = document.createElement('textarea');
    input?: (text: string) => void;
    write = vi.fn((_bytes: Uint8Array, done: () => void) => done());
    constructor() {
      terminals.instances.push(this);
    }
    loadAddon() {}
    open(element: HTMLElement) {
      element.append(this.textarea);
    }
    focus() {}
    clear() {}
    dispose() {}
    onData(listener: (text: string) => void) {
      this.input = listener;
      return { dispose() {} };
    }
    onBinary() {
      return { dispose() {} };
    }
  },
}));
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit() {}
  },
}));

const server = setupServer();
const nativeEncode = TextEncoder.prototype.encode;
const vm: Vm = {
  vm_id: '11111111-1111-4111-8111-111111111111',
  name: 'zephyr-drive-a',
  guest_kind: 'zephyr',
  state: 'running',
  template_id: 'offline-fixture',
  template_version: 'test',
  vcpus: 1,
  memory_mib: 16,
};
let closeClients: Array<() => void>;

function mockConsole(options: { reject?: number; quiet?: boolean; delayCleanup?: boolean } = {}) {
  const frames = { auth: [] as unknown[], input: [] as Uint8Array[], opened: 0, closed: 0 };
  let occupied = false;
  const link = ws.link('*/ws/v1/vms/:vmId/console');
  const handler = link.addEventListener('connection', ({ client, params }) => {
    frames.opened++;
    let ownsConsole = false;
    closeClients.push(() => client.close(1000));
    client.addEventListener('close', () => {
      frames.closed++;
      if (ownsConsole) occupied = false;
    });
    client.addEventListener('message', (event) => {
      if (typeof event.data !== 'string') {
        if (ownsConsole) frames.input.push(new Uint8Array(event.data as ArrayBuffer));
        return;
      }
      const frame = JSON.parse(event.data) as { type: string };
      if (frame.type === 'auth') {
        frames.auth.push(JSON.parse(event.data));
        if (options.reject) return client.close(options.reject);
        if (occupied) return client.close(4409);
        occupied = ownsConsole = true;
        client.send(JSON.stringify({ type: 'connected', vm_id: String(params.vmId) }));
        if (!options.quiet)
          client.send(Uint8Array.from(new TextEncoder().encode('mock guest output\r\n')).buffer);
      } else if (frame.type === 'disconnect' && !options.delayCleanup) {
        occupied = false;
        client.close(1000);
      }
    });
  });
  server.use(handler);
  return frames;
}

beforeEach(() => {
  // Node's native encoder returns another realm's buffer. MSW/jsdom correctly
  // expect their own ArrayBuffer; a real browser has only one realm here.
  vi.spyOn(TextEncoder.prototype, 'encode').mockImplementation(function (this: TextEncoder, text) {
    return Uint8Array.from(nativeEncode.call(this, text));
  });
  closeClients = [];
  terminals.instances = [];
  auth.getSession.mockReturnValue({ access_token: 'offline-user-token', expires_at: '2099-01-01T00:00:00Z' });
  auth.refreshSession.mockReset();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  server.listen();
});
afterEach(() => {
  cleanup();
  closeClients.forEach((close) => close());
  server.resetHandlers();
  server.close();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('web console with an offline WebSocket server', () => {
  it('receives bytes, disconnects and reconnects without replaying old input', async () => {
    const frames = mockConsole();
    render(<ConsolePanel vm={vm} />);
    fireEvent.click(screen.getByRole('button', { name: '연결' }));
    await waitFor(() => expect(screen.getByText('연결됨', { exact: true })).toBeInTheDocument());
    const terminal = terminals.instances[0]!;
    await waitFor(() => expect(terminal.write).toHaveBeenCalled());
    expect(Array.from(terminal.write.mock.calls[0]![0])).toEqual(
      Array.from(new TextEncoder().encode('mock guest output\r\n')),
    );
    expect(terminal.options.disableStdin).toBe(false);
    expect(frames.input).toHaveLength(0); // Connecting to Zephyr sends no guest input.
    act(() => terminal.input?.('offline-input'));
    await waitFor(() => expect(frames.input).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: '연결 해제' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '연결' })).toBeEnabled());
    expect(terminal.options.disableStdin).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '연결' }));
    await waitFor(() => expect(frames.auth).toHaveLength(2));
    await waitFor(() => expect(screen.getByText('연결됨', { exact: true })).toBeInTheDocument());
    expect(frames.input).toHaveLength(1);
  });

  it.each([4401, 4404, 4409, 1011])('shows rejection %s and leaves manual retry available', async (code) => {
    const frames = mockConsole({ reject: code });
    render(<ConsolePanel vm={vm} />);
    fireEvent.click(screen.getByRole('button', { name: '연결' }));
    await waitFor(() => expect(screen.getByText('연결 오류', { exact: true })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '연결' })).toBeEnabled();
    expect(terminals.instances[0]!.options.disableStdin).toBe(true);
    expect(frames.opened).toBe(1);
    expect(frames.input).toHaveLength(0);
  });

  it('keeps reconnect disabled while the mock server has not acknowledged cleanup', async () => {
    mockConsole({ delayCleanup: true });
    render(<ConsolePanel vm={vm} />);
    fireEvent.click(screen.getByRole('button', { name: '연결' }));
    await waitFor(() => expect(screen.getByText('연결됨', { exact: true })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '연결 해제' }));
    expect(screen.getByText('연결 정리 중', { exact: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '연결' })).toBeDisabled();
    expect(terminals.instances[0]!.options.disableStdin).toBe(true);
  });

  it('distinguishes a connected quiet console from receiving output', async () => {
    const frames = mockConsole({ quiet: true });
    render(<ConsolePanel vm={vm} />);
    fireEvent.click(screen.getByRole('button', { name: '연결' }));
    await waitFor(() => expect(screen.getByText('연결됨', { exact: true })).toBeInTheDocument());
    expect(terminals.instances[0]!.write).not.toHaveBeenCalled();
    expect(screen.getByText(/새 로그가 없으면 화면이 비어/)).toBeInTheDocument();
    expect(frames.input).toHaveLength(0);
  });

  it('rejects a second connection without disturbing the original output context', async () => {
    const frames = mockConsole();
    const first = render(<ConsolePanel vm={vm} displayName="first" />);
    fireEvent.click(screen.getByRole('button', { name: '연결' }));
    await waitFor(() => expect(screen.getByText('연결됨', { exact: true })).toBeInTheDocument());
    const second = render(<ConsolePanel vm={vm} displayName="second" />);
    const buttons = screen.getAllByRole('button', { name: '연결' });
    fireEvent.click(buttons[1]!);
    await waitFor(() => expect(screen.getByText('연결 오류', { exact: true })).toBeInTheDocument());
    expect(terminals.instances[0]!.options.disableStdin).toBe(false);
    expect(terminals.instances[1]!.options.disableStdin).toBe(true);
    expect(frames.opened).toBe(2);
    first.unmount();
    second.unmount();
  });
});
