import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

/** VITE_MOCK=true 일 때 main.tsx 가 부른다. mock 에 없는 요청(/ws 등)은 그대로 보낸다 */
export function startMock() {
  return setupWorker(...handlers).start({ onUnhandledFrame: 'bypass', quiet: false });
}
