import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 모든 워크스페이스의 *.test.ts(x) 를 한 번에 돌린다 (npm test)
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['packages/*/src/**/*.test.{ts,tsx}', 'apps/*/src/**/*.test.{ts,tsx}'],
    setupFiles: ['./vitest.setup.ts'],
  },
});
