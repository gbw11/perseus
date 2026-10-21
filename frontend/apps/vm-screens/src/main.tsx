import '@xenova/ui/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, Outlet, RouterProvider } from 'react-router';
import { DevControls } from './components/DevControls';
import { DetailPage } from './pages/DetailPage';
import { SplitPage } from './pages/SplitPage';
import {
  ConsoleBusTelemetryProvider,
  LiveTelemetryProvider,
  MockTelemetryProvider,
} from './telemetry/TelemetryProvider';

/** VITE_TELEMETRY: live(백엔드) / scenario(혼자 도는 가짜) / 그 밖(기본) = 관리 콘솔 mock 을 따라감 (.env.example) */
const PROVIDERS = { live: LiveTelemetryProvider, scenario: MockTelemetryProvider };

function Root() {
  const mode = import.meta.env.VITE_TELEMETRY as keyof typeof PROVIDERS | undefined;
  const Provider = (mode && PROVIDERS[mode]) || ConsoleBusTelemetryProvider;
  return (
    <Provider>
      <Outlet />
      <DevControls />
    </Provider>
  );
}

const router = createBrowserRouter(
  [
    {
      element: <Root />,
      children: [
        { path: '/', element: <Navigate to="/split" replace /> },
        { path: '/split', element: <SplitPage /> },
        { path: '/detail/:app', element: <DetailPage /> },
      ],
    },
  ],
  // 관리 콘솔 개발 서버의 /monitor/ 아래로 열 때 (VITE_BASE=/monitor/)
  { basename: import.meta.env.BASE_URL.replace(/\/$/, '') || '/' },
);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
