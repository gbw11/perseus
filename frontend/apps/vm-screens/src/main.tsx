import '@xenova/ui/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, Outlet, RouterProvider } from 'react-router';
import { DevControls } from './components/DevControls';
import { DetailPage } from './pages/DetailPage';
import { SplitPage } from './pages/SplitPage';
import { LiveTelemetryProvider, MockTelemetryProvider } from './telemetry/TelemetryProvider';
import { MonitorPage } from './display/MonitorPage';

function Root() {
  // VITE_TELEMETRY=live 면 백엔드에 붙는다 (.env.example)
  const Provider = import.meta.env.VITE_TELEMETRY === 'live' ? LiveTelemetryProvider : MockTelemetryProvider;
  return (
    <Provider>
      <Outlet />
      <DevControls />
    </Provider>
  );
}

const router = createBrowserRouter(
  [
    { path: '/', element: <Navigate to="/display/monitor-1" replace /> },
    { path: '/display/:outputId', element: <MonitorPage /> },
    {
      element: <Root />,
      children: [
        { path: '/split', element: <SplitPage /> },
        { path: '/detail/:app', element: <DetailPage /> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
