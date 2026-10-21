import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { MOCK_BUS, useTelemetry } from '../telemetry/TelemetryProvider';
import { APP_IDS } from '../telemetry/types';

interface Route {
  revision: number;
  view: 'split' | 'os-face';
  vmId: string | null;
}

interface DisplayDto {
  display_id: string;
  revision: number;
  desired_route: { vm_id: string | null; view: 'split' | 'os-face' };
}

type Send = (frame: unknown) => void;

/**
 * mock 전용 — dom0 표시 서비스 흉내 (화면 배치 계약 display-route-v1).
 * 실제로는 표시 서비스가 백엔드 WS /ws/v1/display-service 에서 route.set 을 받아 키오스크 주소를 바꾸고
 * route.applied 로 답한다. 모니터 화면은 주소만 받아 그린다 — 그래서 실서버(live)에서는 아무것도 하지 않는다.
 * 관리 콘솔 mock 을 따라갈 때만, `?display=display-0` 으로 연 모니터가 콘솔 mock 의 배치를 따라 이동하고
 * route.applied 를 브라우저 채널로 보낸다 (콘솔 [화면 배치] 시연용).
 */
export function DisplayFollower() {
  const { telemetry, source } = useTelemetry();
  const location = useLocation();
  const navigate = useNavigate();
  const displayId = new URLSearchParams(location.search).get('display');
  const [route, setRoute] = useState<Route | null>(null);
  const sendRef = useRef<Send | null>(null);
  const appliedRef = useRef(0);

  useEffect(() => {
    if (!displayId || source !== 'console') return;
    const onSnapshot = (items: unknown) => {
      const display = (items as DisplayDto[] | undefined)?.find((d) => d.display_id === displayId);
      if (display) {
        setRoute({
          revision: display.revision,
          view: display.desired_route.view,
          vmId: display.desired_route.vm_id,
        });
      }
    };
    const hello = { type: 'display.hello', display_id: displayId };
    appliedRef.current = 0; // 다시 붙으면 서버가 지금 revision 확인을 다시 받아야 한다
    if (typeof BroadcastChannel === 'undefined') return;
    const bus = new BroadcastChannel(MOCK_BUS);
    sendRef.current = (frame) => bus.postMessage(frame);
    bus.onmessage = (message: MessageEvent<{ type?: string; items?: unknown }>) => {
      if (message.data?.type === 'display.snapshot') onSnapshot(message.data.items);
    };
    bus.postMessage(hello);
    return () => {
      sendRef.current = null;
      bus.close();
    };
  }, [displayId, source]);

  const vmIds = telemetry.appVmIds;
  useEffect(() => {
    if (!route || !displayId || route.revision === appliedRef.current) return;
    let path = '/split';
    if (route.view === 'os-face') {
      const app = APP_IDS.find((id) => vmIds?.[id] === route.vmId);
      if (!app) return; // 아직 주행 상태를 못 받아 vm_id → 앱을 모른다. 받으면 다시 온다
      path = `/detail/${app}`;
    }
    if (location.pathname !== path) navigate(`${path}${location.search}`, { replace: true });
    appliedRef.current = route.revision;
    sendRef.current?.({ type: 'route.applied', display_id: displayId, revision: route.revision });
  }, [route, vmIds, displayId, location.pathname, location.search, navigate]);

  return null;
}
