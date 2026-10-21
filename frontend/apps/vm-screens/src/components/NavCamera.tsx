import { CameraView } from '@xenova/ui';
import { useTelemetry } from '../telemetry/TelemetryProvider';

/** 세 번째 VM 이 낸 AI 감지 영상 (MJPEG) — dom0 웹 프록시가 같은 origin 으로 넘긴다 (카메라 계약 camera-stream-v1) */
const CAMERA_URL: string = import.meta.env.VITE_CAMERA_URL || '/camera/stream.mjpg';

/**
 * 네비게이션 VM 의 카메라 + AI 장애물 감지 화면. 이 VM 이 영상을 받아 감지 박스를 그려 내므로,
 * VM 이 죽으면 영상도 멈춘다 — 주행은 그대로 (격리 시연). 백엔드가 아니면(mock·콘솔 mock) 시험 영상.
 */
export function NavCamera({ compact = false }: { compact?: boolean }) {
  const { telemetry: t, source } = useTelemetry();
  const app = t.apps.interaction;
  const obstacles = t.obstacleCount ?? t.perception.obstacles.length;
  const down =
    app.domain === 'booting'
      ? 'VM 재시작 중 — 영상 없음 (주행은 그대로)'
      : app.domain !== 'running' || app.health === 'fault'
        ? 'VM 중지 — 영상 없음 (주행은 그대로)'
        : null;
  return (
    <CameraView
      compact={compact}
      mock={source !== 'live'}
      streamUrl={source === 'live' ? CAMERA_URL : undefined}
      down={down}
      motion={{
        v: t.vehicle.v_mps,
        steer: t.vehicle.steer_rad,
        laneOffset: t.perception.lane_offset_m,
        obstacle: t.vehicle.flags.obstacle || obstacles > 0,
      }}
      stats={[
        { label: '장애물', value: String(obstacles), warn: obstacles > 0 },
        {
          label: '차선',
          value: `${t.perception.lane_offset_m >= 0 ? '+' : ''}${t.perception.lane_offset_m.toFixed(2)} m`,
        },
        { label: '정지', value: t.perception.stop ? '예' : '아니오', warn: t.perception.stop },
      ]}
    />
  );
}
