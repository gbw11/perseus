import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { Button, Card } from '@xenova/ui';
import { useEffect, useRef, useState } from 'react';
import { ConsoleTransport, type ConsoleState } from '../api/console';
import type { Vm } from '../api/types';
import { capitalizeFirst } from '../format';
import styles from './ConsolePanel.module.css';

const LABEL: Record<ConsoleState, string> = {
  idle: '연결 대기',
  connecting: '연결 중',
  connected: '연결됨',
  closing: '연결 정리 중',
  disconnected: '연결 해제',
  error: '연결 오류',
};

export function ConsolePanel({ vm, displayName }: { vm: Vm; displayName?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const transport = useRef<ConsoleTransport | null>(null);
  const terminal = useRef<Terminal | null>(null);
  const consoleName = displayName ?? capitalizeFirst(vm.name);
  const [status, setStatus] = useState<{ state: ConsoleState; message: string }>({
    state: 'idle',
    message: '연결 버튼을 눌러 이 VM의 콘솔을 여세요.',
  });
  const available = vm.state === 'running' || vm.state === 'paused';

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
    term.textarea?.setAttribute('aria-label', `${consoleName} 터미널 입력`);
    terminal.current = term;
    let pendingOutput = 0;
    let disposed = false;
    const connection = new ConsoleTransport(vm.vm_id, {
      state: (state, message) => {
        if (disposed) return;
        term.options.disableStdin = state !== 'connected';
        setStatus({ state, message });
        if (state === 'connected') {
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
      connection.dispose();
      observer.disconnect();
      input.dispose();
      binary.dispose();
      term.dispose();
      transport.current = null;
      terminal.current = null;
    };
  }, [vm.vm_id, vm.name, vm.guest_kind, consoleName]);

  useEffect(() => {
    if (!available) transport.current?.disconnect();
  }, [available]);

  const active = status.state === 'connected' || status.state === 'connecting';
  const closing = status.state === 'closing';
  return (
    <Card title="웹 콘솔">
      <section aria-label={`${consoleName} 콘솔`} className={styles.panel}>
        <div className={styles.toolbar}>
          <div className={styles.identity}>
            <strong>{consoleName}</strong>
            <span className={styles.badge} data-state={status.state}>
              {LABEL[status.state]}
            </span>
          </div>
          <div className={styles.actions}>
            <Button
              size="sm"
              disabled={!available || active || closing}
              onClick={() => void transport.current?.connect()}
            >
              연결
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={!active}
              onClick={() => transport.current?.disconnect()}
            >
              연결 해제
            </Button>
            <Button size="sm" variant="ghost" onClick={() => terminal.current?.clear()}>
              화면 지우기
            </Button>
          </div>
        </div>
        <p className={status.state === 'error' ? styles.error : styles.hint} role="status">
          {available ? status.message : 'VM을 실행한 뒤 콘솔에 연결할 수 있습니다.'}
        </p>
        {vm.guest_kind === 'zephyr' && (
          <p className={styles.hint}>
            Zephyr VM 콘솔은 로그 전용입니다. 새 로그가 없으면 화면이 비어 있을 수 있습니다.
          </p>
        )}
        <div data-theme="xenova" className={styles.screen}>
          <div ref={host} className={styles.terminal} />
        </div>
        <p className={styles.hint}>로그인은 게스트 계정을 사용합니다. 연결 해제는 VM을 종료하지 않습니다.</p>
      </section>
    </Card>
  );
}
