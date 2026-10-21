import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// 개발 서버는 /api·/ws 를 백엔드로 넘긴다 (CORS 없이 쿠키·토큰 그대로).
// /monitor/ 는 모니터 화면 개발 서버(VITE_BASE=/monitor/ 로 띄운 vm-screens)로 넘긴다 — 같은 origin 이라
// 모니터가 콘솔 mock 시나리오를 브라우저 채널로 따라갈 수 있다 (README "모니터를 콘솔과 같이 보기").
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const backend = env.VITE_BACKEND_ORIGIN || 'http://localhost:8080';
  const monitor = env.VITE_MONITOR_ORIGIN || 'http://localhost:5174';
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': { target: backend, changeOrigin: true },
        '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
        '/monitor': { target: monitor, ws: true },
      },
    },
  };
});
