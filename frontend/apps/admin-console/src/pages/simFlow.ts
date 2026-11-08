import type { Health } from '@xenova/ui';
import type { DriveState } from '../api/drive';
import { MODE_HEALTH, MODE_LABEL } from '../domain/roles';

/** 이보다 느리면 선 것으로 본다 (m/s, 모의 속도) */
export const STOPPED_MPS = 0.02;

export interface SimFlow {
  /** 확정 주행 모드 (drive.control.mode) */
  mode: { label: string; health: Health } | null;
  /** 지금 차를 모는 앱 */
  app: string;
  /** 차가 하는 일 — 자율 주행 → 장애물 앞 정지 → 수동 인계 → 재개 흐름을 한 줄로 */
  motion: { label: string; health: Health };
}

const APP_LABEL: Record<'autonomous' | 'manual', string> = {
  autonomous: '자율주행 앱',
  manual: '수동주행 앱',
};

/**
 * 시뮬레이터 탭 상태 칸 (S15P31E110-262). 모드·앱은 백엔드 drive.state 그대로, 차 움직임은
 * 시뮬레이터 속도(pose.v_mps)와 앞 장애물 거리로 정한다. 자율 앱의 정지 사유는 계약에 없어 추측하지 않고,
 * "장애물 앞 정지" 는 인지 stop·차 obstacle 플래그·앞 4 m 안 장애물 중 하나가 있을 때만 쓴다.
 */
export function simFlow(drive: DriveState | null, vMps: number | null, aheadM: number | null): SimFlow {
  const control = drive?.control ?? null;
  const mode = control?.mode ? { label: MODE_LABEL[control.mode], health: MODE_HEALTH[control.mode] } : null;
  const app = control?.active_app ? APP_LABEL[control.active_app] : '없음';
  const remaining = drive?.resume?.remaining_ms;

  let motion: SimFlow['motion'];
  if (drive?.vehicle?.flags?.estop) motion = { label: 'E-stop 정지', health: 'fault' };
  else if (remaining != null && remaining > 0)
    motion = { label: `${Math.ceil(remaining / 1000)}초 뒤 자율주행 재개`, health: 'recovering' };
  else if (vMps == null) motion = { label: '—', health: 'stopped' };
  else if (Math.abs(vMps) >= STOPPED_MPS) motion = { label: '달리는 중', health: 'ok' };
  else if (
    drive?.perception?.stop ||
    drive?.vehicle?.flags?.obstacle ||
    (aheadM != null && aheadM >= 0 && aheadM < 4)
  )
    motion = { label: '장애물 앞 정지', health: 'recovering' };
  else motion = { label: '정지', health: 'stopped' };
  return { mode, app, motion };
}
