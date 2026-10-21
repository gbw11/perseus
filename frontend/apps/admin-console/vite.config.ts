import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// 개발 서버는 /api·/ws 를 백엔드로 넘긴다 (CORS 없이 쿠키·토큰 그대로)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const backend = env.VITE_BACKEND_ORIGIN || 'http://localhost:8080';
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': { target: backend, changeOrigin: true },
        '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
      },
    },
  };
});
