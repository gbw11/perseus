import '@xenova/ui/tokens.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '@xenova/ui';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router';
import { ApiError } from './api/client';
import { RequireAuth } from './auth';
import { ConsoleLayout } from './layout/ConsoleLayout';
import { DashboardLayout } from './layout/DashboardLayout';
import { ConsolePage } from './pages/ConsolePage';
import { ControlVmDetailPage } from './pages/ControlVmDetailPage';
import { DashboardPage } from './pages/DashboardPage';
import { DemoPage } from './pages/DemoPage';
import { EventsPage } from './pages/EventsPage';
import { LoginPage } from './pages/LoginPage';
import { ResourcesPage } from './pages/ResourcesPage';
import { ScreensPage } from './pages/ScreensPage';
import { SignupPage } from './pages/SignupPage';
import { ShowcasePage } from './pages/ShowcasePage';
import { VmCreatePage } from './pages/VmCreatePage';
import { VmDetailPage } from './pages/VmDetailPage';
import { VmListPage } from './pages/VmListPage';
import { VisionPage } from './pages/VisionPage';

// 관리자 콘솔에만 XENOVA 브랜드 테마를 적용한다. HDMI 화면은 기존 테마를 유지한다.
document.documentElement.dataset.theme = 'xenova';

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
  { path: '/signup', element: <SignupPage /> },
  {
    element: (
      <RequireAuth>
        <ConsoleLayout />
      </RequireAuth>
    ),
    children: [
      { path: '/', element: <Navigate to="/dashboard/overview" replace /> },
      {
        path: '/dashboard',
        element: <DashboardLayout />,
        children: [
          { index: true, element: <Navigate to="/dashboard/overview" replace /> },
          { path: 'overview', element: <DashboardPage /> },
          { path: 'cpu', element: <ResourcesPage view="cpu" /> },
          { path: 'memory', element: <ResourcesPage view="memory" /> },
          { path: 'network', element: <ResourcesPage view="network" /> },
          { path: 'console', element: <ConsolePage /> },
        ],
      },
      { path: '/dashboard/vms/:vmId', element: <VmDetailPage /> },
      { path: '/vision', element: <VisionPage /> },
      { path: '/control', element: <VmListPage /> },
      { path: '/control/vms/new', element: <VmCreatePage /> },
      { path: '/control/vms/:vmId', element: <ControlVmDetailPage /> },
      { path: '/simulator', element: <DemoPage /> },

      // 기존 북마크와 외부 링크를 깨지 않도록 새 정보구조로 보낸다.
      { path: '/vms', element: <Navigate to="/control" replace /> },
      { path: '/vms/new', element: <Navigate to="/control/vms/new" replace /> },
      { path: '/vms/:vmId', element: <VmDetailPage /> },
      { path: '/resources', element: <Navigate to="/dashboard/cpu" replace /> },
      { path: '/demo', element: <Navigate to="/simulator" replace /> },
      { path: '/console', element: <Navigate to="/dashboard/console" replace /> },
      { path: '/events', element: <EventsPage /> },
      { path: '/screens', element: <ScreensPage /> },
      { path: '/components', element: <ShowcasePage /> },
    ],
  },
  // 디자인 확인은 로그인 없이도 볼 수 있게
  { path: '/components-public', element: <ShowcasePage /> },
  { path: '*', element: <Navigate to="/dashboard/overview" replace /> },
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
