import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// 실제 연결(VITE_TELEMETRY=live)일 때 개발 서버가 /api·/ws 를 백엔드로 넘긴다 (관리 콘솔과 같은 방식).
// VITE_BASE=/monitor/ 로 띄우면 관리 콘솔 개발 서버가 /monitor/ 로 넘겨 받아 같은 origin 이 된다 (콘솔 mock 따라가기).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const backend = env.VITE_BACKEND_ORIGIN || 'http://localhost:8080';
  return {
    base: env.VITE_BASE || '/',
    plugins: [react()],
    server: {
      proxy: {
        '/api': { target: backend, changeOrigin: true },
        '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
      },
    },
  };
});
