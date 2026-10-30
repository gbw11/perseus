import { setupWorker } from 'msw/browser';
import { consoleHandlers } from './console';
import { handlers } from './handlers';

/** VITE_MOCK=true 일 때 main.tsx 가 부른다. mock 에 없는 요청(/ws 등)은 그대로 보낸다. 웹 콘솔 WS 는 가짜 셸(./console) */
export function startMock() {
  return setupWorker(...handlers, ...consoleHandlers).start({ onUnhandledFrame: 'bypass', quiet: false });
}
