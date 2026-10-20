import '@xenova/ui/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, Outlet, RouterProvider } from 'react-router';
import { DevControls } from './components/DevControls';
import { DetailPage } from './pages/DetailPage';
import { SplitPage } from './pages/SplitPage';
import { MockTelemetryProvider } from './telemetry/TelemetryProvider';

function Root() {
  return (
    <MockTelemetryProvider>
      <Outlet />
      <DevControls />
    </MockTelemetryProvider>
  );
}

const router = createBrowserRouter([
  {
    element: <Root />,
    children: [
      { path: '/', element: <Navigate to="/split" replace /> },
      { path: '/split', element: <SplitPage /> },
      { path: '/detail/:app', element: <DetailPage /> },
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
