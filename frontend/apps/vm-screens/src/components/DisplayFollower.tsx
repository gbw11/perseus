import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { accessToken, openSocket } from '../telemetry/backend';
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
 * 관리 콘솔 화면 배치(REST-24)를 따라간다. 모니터를 `?display=display-0` 처럼 자기 번호로 열면:
 *   1) WS /ws/v1/displays 에 display.hello  (콘솔 mock 이면 같은 이름의 브라우저 채널)
 *   2) display.snapshot 의 desired_route 로 화면 이동 — split → /split, os-face → vm_id 의 앱 상세
 *   3) 옮긴 뒤 route.applied {revision} — 콘솔에 "적용됨" 으로 보인다
 * display 가 없으면 아무것도 하지 않는다 (손으로 연 개발 화면).
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
    if (!displayId || source === 'mock') return;
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
    if (source === 'live') {
      return openSocket({
        path: '/ws/v1/displays',
        getToken: accessToken,
        onConnection: () => {},
        afterAuth: (send) => {
          sendRef.current = send;
          appliedRef.current = 0;
          send(hello);
        },
        onFrame: (frame) => {
          if (frame.type === 'display.snapshot') onSnapshot(frame.items);
        },
      });
    }
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
