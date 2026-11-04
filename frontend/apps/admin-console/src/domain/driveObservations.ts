import type { DriveSource, DriveState, SocketState } from '../api/drive';
import { ago } from './roles';

const PHASES = {
  WAITING: '대기',
  ACTIVATING: '활성화 준비',
  EXECUTING: '실행 중',
  STOPPING: '종료 중',
  RESIDUAL: '정리 미확정',
};
const READINESS = { QUALIFIED: '대기 자격 확인', READY: '계산 준비 확인', NOT_READY: '준비 미충족' };
const HEARTBEAT = { active: '동작 중', standby: '대기', idle: '유휴', error: '오류' };

function unavailable(status: DriveSource['status']) {
  if (status === 'stale') return '관측 만료';
  if (status === 'disconnected') return '연결 끊김';
  return '미수신';
}

export function observedControl(drive: DriveState | null, socket: SocketState) {
  return socket === 'open' && drive?.control?.source?.status === 'fresh' ? drive.control : null;
}

/** Scoped reports win per app. Legacy reports are usable only for the matching VM. */
export function vmAppObservations(vmId: string, drive: DriveState | null, socket: SocketState) {
  if (!drive || socket !== 'open' || (drive.source && drive.source.status !== 'fresh')) return [];
  const scoped = drive.vm_apps?.[vmId] ?? {};
  return [
    ...Object.entries(scoped).map(([appId, report]) => {
      const lifecycle = report.lifecycle_source.status === 'fresh' ? report.lifecycle : null;
      const heartbeat = report.heartbeat_source.status === 'fresh' ? report.heartbeat : null;
      const phaseLabel = lifecycle ? PHASES[lifecycle.phase] : unavailable(report.lifecycle_source.status);
      const heartbeatLabel = heartbeat
        ? HEARTBEAT[heartbeat.state]
        : unavailable(report.heartbeat_source.status);
      return {
        appId,
        phaseLabel,
        heartbeatLabel,
        state: lifecycle
          ? [phaseLabel, READINESS[lifecycle.readiness], lifecycle.reason].filter(Boolean).join(' · ')
          : phaseLabel,
        heartbeat: heartbeat ? `${heartbeatLabel} · ${ago(report.heartbeat_source.age_ms)}` : heartbeatLabel,
      };
    }),
    ...Object.entries(drive.apps)
      .filter(([appId, report]) => report.vm_id === vmId && !scoped[appId])
      .map(([appId, report]) => {
        const age = report.heartbeat_ago_ms;
        const fresh = age != null && age <= 3000 ? report.heartbeat_state : null;
        const heartbeatLabel = fresh ? HEARTBEAT[fresh] : age != null && age > 3000 ? '관측 만료' : '미수신';
        return {
          appId,
          phaseLabel: '미수신',
          heartbeatLabel,
          state: '미수신',
          heartbeat: fresh ? `${heartbeatLabel} · ${ago(age)}` : heartbeatLabel,
        };
      }),
  ];
}
