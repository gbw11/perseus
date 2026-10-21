/**
 * 이벤트 표시 규칙 (화면 정의서 W-08). 종류는 계약 이벤트에서 나눈다 — "자동 복귀·재합류·재무장" 같은 분류는 없다.
 * 자율주행 복귀는 항상 AUTO_READY → 사람의 재개 (MQTT 계약 v1.1 §5.2·§6).
 */
import type { EventType, VmEvent } from '../api/types';

export type EventCategory = 'mode' | 'fault' | 'lifecycle' | 'demo';

export const CATEGORY_LABEL: Record<EventCategory, string> = {
  mode: '모드 전환',
  fault: 'VM 장애',
  lifecycle: 'VM 수명주기',
  demo: '시연 조작',
};

const CATEGORY: Record<EventType, EventCategory> = {
  'drive.mode.changed': 'mode',
  'drive.fault.detected': 'fault',
  'recovery.failed': 'fault',
  'vm.created': 'lifecycle',
  'vm.deleted': 'lifecycle',
  'vm.state.changed': 'lifecycle',
  'agent.status.changed': 'lifecycle',
  'recovery.started': 'lifecycle',
  'recovery.completed': 'lifecycle',
  'demo.command.sent': 'demo',
  'demo.command.completed': 'demo',
  'demo.command.failed': 'demo',
};

export const categoryOf = (event: VmEvent): EventCategory => CATEGORY[event.type] ?? 'lifecycle';

const REASON: Record<string, string> = {
  start: '시작',
  command_timeout: '명령 끊김',
  resume_requested: '재개 요청',
  no_fresh_standby: '넘길 앱 없음',
  autonomous_recovered: '자율주행 3초 연속 정상',
};

const str = (value: unknown) => (value == null ? '' : String(value));

/** 한 줄 제목과 보조 설명 */
export function describe(
  event: VmEvent,
  vmName: (vmId: string | null) => string,
): { title: string; detail: string } {
  const p = event.payload ?? {};
  switch (event.type) {
    case 'drive.mode.changed':
      return {
        title: `${str(p.from) || '—'} → ${str(p.to)}`,
        detail: [
          REASON[str(p.reason)] ?? str(p.reason),
          p.generation != null && `gen ${str(p.generation)}`,
          p.handoff_ms != null && `인계 ${str(p.handoff_ms)} ms`,
        ]
          .filter(Boolean)
          .join(' · '),
      };
    case 'drive.fault.detected':
      return {
        title: `${vmName(str(p.vm_id) || event.vm_id)} 장애 감지`,
        detail: REASON[str(p.reason)] ?? str(p.reason),
      };
    case 'vm.state.changed':
      return { title: `${vmName(event.vm_id)} ${str(p.from)} → ${str(p.to)}`, detail: '도메인 상태' };
    case 'recovery.started':
      return { title: `${vmName(event.vm_id)} 재시작`, detail: '복구 서비스' };
    case 'recovery.completed':
      return { title: `${vmName(event.vm_id)} 복구 완료`, detail: '앱 명령 재개' };
    case 'recovery.failed':
      return { title: `${vmName(event.vm_id)} 복구 실패`, detail: str(p.reason) };
    case 'demo.command.sent':
      return {
        title: `${vmName(event.vm_id)} ${str(p.command) === 'force_kill' ? '강제 종료' : str(p.command)}`,
        detail: '운영자 시연 조작',
      };
    case 'agent.status.changed':
      return {
        title: `${vmName(event.vm_id)} 에이전트 ${str(p.from)} → ${str(p.to)}`,
        detail: 'heartbeat 5초 주기',
      };
    default:
      return { title: `${vmName(event.vm_id)} ${event.type}`, detail: '' };
  }
}

/** 14:31:20.112 (화면 시계 기준, 계약 §1) — 로캘마다 모양이 달라지지 않게 직접 만든다 */
export const clock = (iso: string) => {
  const d = new Date(iso);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0')}`;
};
