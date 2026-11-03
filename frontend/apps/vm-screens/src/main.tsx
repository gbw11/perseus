import '@xenova/ui/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from 'react-router';
import { DevControls } from './components/DevControls';
import { DisplayFollower } from './components/DisplayFollower';
import { ResumeOverlay } from './components/ResumeOverlay';
import { DetailPage } from './pages/DetailPage';
import { SimPage } from './pages/SimPage';
import { SplitPage } from './pages/SplitPage';
import {
  ConsoleBusTelemetryProvider,
  LiveTelemetryProvider,
  MockTelemetryProvider,
} from './telemetry/TelemetryProvider';

/** Default/display = local HDMI. Explicit live/console/scenario = legacy app preview. */
const PROVIDERS = { live: LiveTelemetryProvider, scenario: MockTelemetryProvider };
import { MonitorPage } from './display/MonitorPage';

// Production HDMI uses the local collector/policy. Legacy app views are explicit previews.
const legacyPreview = ['console', 'scenario', 'live', 'legacy-live'].includes(import.meta.env.VITE_TELEMETRY);

function ScreenEntry({ detail = false }: { detail?: boolean }) {
  if (legacyPreview) return detail ? <DetailPage /> : <SplitPage />;
  return <Navigate to={`/display/${detail ? 'monitor-2' : 'monitor-1'}`} replace />;
}

function Root() {
  if (!legacyPreview) return <Outlet />;
  const configured = import.meta.env.VITE_TELEMETRY;
  const mode = (configured === 'legacy-live' ? 'live' : configured) as keyof typeof PROVIDERS | undefined;
  const Provider = (mode && PROVIDERS[mode]) || ConsoleBusTelemetryProvider;
  return (
    <Provider>
      {legacyPreview && <div role="note">앱 시연 화면 · HDMI 실제 상태/주·백업 승계 검증용이 아닙니다.</div>}
      <DisplayFollower />
      <Outlet />
      <ResumeOverlay />
      <DevControls />
    </Provider>
  );
}

/** / → /split. ?display=·?kiosk= 는 남긴다 (모니터 번호를 잃으면 화면 배치를 못 따라간다) */
function ToSplit() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: '/split', search }} replace />;
}

const router = createBrowserRouter(
  [
    // dom0 표시 수집기 kiosk 화면 (S15P31E110-250, display-pipeline 계약)
    {
      path: '/display/mobile',
      lazy: async () => ({ Component: (await import('./display/MobilePage')).MobilePage }),
    },
    { path: '/display/:outputId', element: <MonitorPage /> },
    {
      element: <Root />,
      children: [
        { path: '/', element: <ToSplit /> },
        { path: '/split', element: <ScreenEntry /> },
        { path: '/detail/:app', element: <ScreenEntry detail /> },
        // 화면 배치 view sim (screen_path /sim, 화면 배치 계약 v1.1)
        { path: '/sim', element: legacyPreview ? <SimPage /> : <Navigate to="/display/monitor-1" replace /> },
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
