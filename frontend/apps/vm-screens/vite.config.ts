import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// 실제 연결(VITE_TELEMETRY=live)일 때 개발 서버가 /api·/ws 를 백엔드로 넘긴다 (관리 콘솔과 같은 방식)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const backend = env.VITE_BACKEND_ORIGIN || 'http://localhost:8080';
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api/v1/display': {
          target: env.VITE_DISPLAY_FEED_ORIGIN || 'http://127.0.0.1:8082',
          changeOrigin: true,
        },
        '/api': { target: backend, changeOrigin: true },
        '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
      },
    },
  };
});
