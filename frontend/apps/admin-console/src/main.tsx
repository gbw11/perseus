import '@xenova/ui/tokens.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '@xenova/ui';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router';
import { ApiError } from './api/client';
import { RequireAuth } from './auth';
import { ConsoleLayout } from './layout/ConsoleLayout';
import { LoginPage } from './pages/LoginPage';
import { ScreensPage } from './pages/ScreensPage';
import { ShowcasePage } from './pages/ShowcasePage';
import { VmDetailPage } from './pages/VmDetailPage';
import { VmListPage } from './pages/VmListPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 인증·검증 오류는 다시 시도해도 같다. 연결·서버 오류만 한 번 더
      retry: (count, error) =>
        count < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
    },
  },
});

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: (
      <RequireAuth>
        <ConsoleLayout />
      </RequireAuth>
    ),
    children: [
      { path: '/', element: <Navigate to="/vms" replace /> },
      { path: '/vms', element: <VmListPage /> },
      { path: '/vms/:vmId', element: <VmDetailPage /> },
      { path: '/screens', element: <ScreensPage /> },
      { path: '/components', element: <ShowcasePage /> },
    ],
  },
  // 디자인 확인은 로그인 없이도 볼 수 있게
  { path: '/components-public', element: <ShowcasePage /> },
  { path: '*', element: <Navigate to="/" replace /> },
]);

async function start() {
  // VITE_MOCK=true 면 MSW 가 /api 를 대신 답한다 (src/mocks, 사용법은 frontend/README.md)
  if (import.meta.env.VITE_MOCK === 'true') {
    const { startMock } = await import('./mocks/browser');
    await startMock();
  }
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}

void start();
