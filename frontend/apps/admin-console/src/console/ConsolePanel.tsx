import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { Button, Card } from '@xenova/ui';
import { useEffect, useRef, useState } from 'react';
import { ConsoleTransport, type ConsoleState } from '../api/console';
import type { Vm } from '../api/types';
import styles from './ConsolePanel.module.css';

/** 끊기면 다시 붙는 간격. 연속으로 실패하면 늘린다 (입력은 다시 보내지 않는다) */
const RETRY_MS = 2000;
const MAX_RETRY_MS = 10000;

const LABEL: Record<ConsoleState, string> = {
  idle: '연결 대기',
  connecting: '연결 중',
  connected: '연결됨',
  closing: '연결 정리 중',
  disconnected: '끊김',
  error: '연결 오류',
};

/**
 * 화면을 열면 바로 연결하고, 끊기면 2초 뒤(실패가 이어지면 최대 10초) 저절로 다시 붙는다.
 * 끊긴 동안 친 키는 버린다 — 다시 붙어도 명령을 재전송하지 않는다. VM 이 꺼져 있으면 기다렸다가 켜지면 붙는다.
 */
export function ConsolePanel({ vm }: { vm: Vm }) {
  const host = useRef<HTMLDivElement>(null);
  const transport = useRef<ConsoleTransport | null>(null);
  const terminal = useRef<Terminal | null>(null);
  const [status, setStatus] = useState<{ state: ConsoleState; message: string }>({
    state: 'connecting',
    message: '콘솔에 연결하는 중…',
  });
  const available = vm.state === 'running' || vm.state === 'paused';
  const availableRef = useRef(available);
  useEffect(() => {
    availableRef.current = available;
  });

  useEffect(() => {
    if (!host.current) return;
    const element = host.current;
    const tokens = getComputedStyle(element);
    const term = new Terminal({
      cursorBlink: true,
      disableStdin: true,
      fontFamily: tokens.getPropertyValue('--x-font-mono').trim(),
      fontSize: 14,
      rows: 24,
      scrollback: 2000,
      screenReaderMode: true,
      theme: {
        background: tokens.getPropertyValue('--x-bg').trim(),
        foreground: tokens.getPropertyValue('--x-text').trim(),
        cursor: tokens.getPropertyValue('--x-primary').trim(),
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(element);
    term.textarea?.setAttribute('aria-label', `${vm.name} 터미널 입력`);
    terminal.current = term;
    let pendingOutput = 0;
    let disposed = false;
    let retry: number | undefined;
    let delay = RETRY_MS;
    const connection = new ConsoleTransport(vm.vm_id, {
      state: (state, message) => {
        if (disposed) return;
        term.options.disableStdin = state !== 'connected';
        if ((state === 'disconnected' || state === 'error') && availableRef.current) {
          window.clearTimeout(retry);
          retry = window.setTimeout(() => void connection.connect(), delay);
          setStatus({ state, message: `${message} ${delay / 1000}초 뒤 다시 연결합니다.` });
          delay = Math.min(delay * 2, MAX_RETRY_MS);
          return;
        }
        setStatus({ state, message });
        if (state === 'connected') {
          delay = RETRY_MS;
          term.focus();
          if (vm.guest_kind === 'linux') connection.send('\r');
        }
      },
      data: (bytes) => {
        if (disposed) return;
        if (pendingOutput + bytes.byteLength > 1048576) {
          connection.disconnect();
          setStatus({
            state: 'error',
            message: '출력을 처리하는 속도보다 데이터가 많아 연결을 종료했습니다.',
          });
          return;
        }
        pendingOutput += bytes.byteLength;
        // xterm's byte decoder retains partial UTF-8 sequences across chunks.
        term.write(bytes, () => {
          pendingOutput -= bytes.byteLength;
        });
      },
    });
    transport.current = connection;
    if (availableRef.current) void connection.connect();
    const input = term.onData((data) => connection.send(data));
    const binary = term.onBinary((data) =>
      connection.sendBytes(Uint8Array.from(data, (char) => char.charCodeAt(0) & 255)),
    );
    const resize = () => {
      if (element.clientWidth > 0 && element.clientHeight > 0) fit.fit();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => {
      disposed = true;
      window.clearTimeout(retry);
      connection.dispose();
      observer.disconnect();
      input.dispose();
      binary.dispose();
      term.dispose();
      transport.current = null;
      terminal.current = null;
    };
  }, [vm.vm_id, vm.name, vm.guest_kind]);

  // VM 이 꺼지면 끊고, 다시 켜지면 붙는다
  const idle = status.state === 'disconnected' || status.state === 'error' || status.state === 'idle';
  useEffect(() => {
    if (!available) transport.current?.disconnect();
    else if (idle) void transport.current?.connect();
  }, [available]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card title="웹 콘솔">
      <section aria-label={`${vm.name} 콘솔`} className={styles.panel}>
        <div className={styles.toolbar}>
          <div className={styles.identity}>
            <strong>{vm.name}</strong>
            <span className={styles.badge} data-state={status.state}>
              {LABEL[status.state]}
            </span>
          </div>
          <div className={styles.actions}>
            <Button size="sm" variant="ghost" onClick={() => terminal.current?.clear()}>
              화면 지우기
            </Button>
          </div>
        </div>
        <p className={status.state === 'error' ? styles.error : styles.hint} role="status">
          {available ? status.message : 'VM 이 꺼져 있습니다. 켜지면 저절로 연결합니다.'}
        </p>
        {vm.guest_kind === 'zephyr' && (
          <p className={styles.hint}>
            Zephyr Starter는 부팅 안내 후 대기합니다. 새 로그가 없으면 화면이 비어 있을 수 있으며 현재
            펌웨어에는 셸이 없습니다.
          </p>
        )}
        <div data-theme="dark" className={styles.screen}>
          <div ref={host} className={styles.terminal} />
        </div>
        <p className={styles.hint}>
          로그인은 게스트 계정을 사용합니다. 끊기면 저절로 다시 연결하며, 끊긴 동안 입력한 키는 보내지
          않습니다.
        </p>
      </section>
    </Card>
  );
}
