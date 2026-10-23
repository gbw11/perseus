import { useEffect, useState } from 'react';
import { parsePresentation, SnapshotOrder, type Presentation } from './model';

export interface DisplayConnection {
  presentation: Presentation | null;
  connected: boolean;
  error: string | null;
}

/** Same-origin dom0 HTTP only. No user password or service token is embedded in the page. */
export function useDisplay(): DisplayConnection {
  const [state, setState] = useState<DisplayConnection>({
    presentation: null,
    connected: false,
    error: null,
  });
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | null = null;
    const order = new SnapshotOrder();
    async function poll() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 2500);
      try {
        const response = await fetch('/api/v1/display/presentation', {
          credentials: 'same-origin',
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error(
            response.status === 401 ? '화면 인증이 필요합니다.' : '수집기에 연결할 수 없습니다.',
          );
        const presentation = parsePresentation(await response.json());
        if (!order.accept(presentation)) throw new Error('중복되거나 이전 세대의 상태가 수신되었습니다.');
        if (!stopped) setState({ presentation, connected: true, error: null });
      } catch (error) {
        if (!stopped)
          setState((previous) => ({
            ...previous,
            connected: false,
            error: error instanceof Error ? error.message : '상태 수신이 중단되었습니다.',
          }));
      } finally {
        clearTimeout(timeout);
        if (!stopped) timer = setTimeout(() => void poll(), 1000);
      }
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, []);
  return state;
}
