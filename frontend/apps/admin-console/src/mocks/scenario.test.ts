import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DriveState } from '../api/drive';
import { VM_IDS } from './data';
import {
  cancelResumeCountdown,
  forceKill,
  getDisplays,
  resetScenario,
  resume,
  RouteError,
  scenarioEvents,
  setKeys,
  setRoute,
  startVm,
  stopVm,
  subscribe,
  togglePerception,
  vmStateOf,
} from './scenario';

describe('mock 시연 시나리오 (MQTT 계약 v1.2 §5.2·§6)', () => {
  let off: () => void;
  let last: DriveState;

  beforeEach(() => {
    vi.useFakeTimers();
    resetScenario();
    off = subscribe((state) => (last = state));
  });
  afterEach(() => {
    off();
    vi.useRealTimers();
  });

  it('시작은 AUTO · gen 4, 자율주행이 제어하고 수동주행은 v=0 예비', () => {
    expect(last.control).toMatchObject({ mode: 'AUTO', generation: 4, active_app: 'autonomous' });
    expect(last.apps.manual?.last_command?.v_mps).toBe(0);
    expect(last.apps.manual?.command_fresh).toBe(true);
  });

  it('강제 종료 → MANUAL 인계 → 복구 → AUTO_READY → 자동 3·2·1 → AUTO, 매번 gen +1 (계약 v1.5)', () => {
    expect(resume()).toBe(false); // AUTO_READY 가 아니면 무시
    forceKill('autonomous');
    expect(last.control).toMatchObject({ mode: 'MANUAL', generation: 5, active_app: 'manual' });
    expect(vmStateOf(VM_IDS.autonomous)).toBe('crashed');
    vi.advanceTimersByTime(3200);
    expect(vmStateOf(VM_IDS.autonomous)).toBe('running');
    expect(last.apps.autonomous?.command_fresh).toBe(false); // 재시작 중
    vi.advanceTimersByTime(5000 + 3100);
    // AUTO_READY 가 되자마자 운영자 조작 없이 자동 복귀 카운트다운
    expect(last.control).toMatchObject({ mode: 'AUTO_READY', generation: 6 });
    expect(last.resume?.requested_by).toBe('auto');
    expect(last.resume?.remaining_ms).toBeLessThanOrEqual(3000);
    expect(scenarioEvents().find((e) => e.type === 'drive.resume.accepted')).toMatchObject({
      payload: { switch_in_ms: 3000, requested_by: 'auto' },
    });
    expect(resume()).toBe(false); // 카운트다운 중 재요청은 무시
    vi.advanceTimersByTime(3100);
    expect(last.control).toMatchObject({
      mode: 'AUTO',
      generation: 7,
      active_app: 'autonomous',
      reason: 'auto_resumed',
    });
    expect(last.resume).toBeNull();
  });

  it('자동 복귀를 취소하면 수동주행 유지, 그 뒤에는 [재개]로만 돌아간다', () => {
    forceKill('autonomous');
    vi.advanceTimersByTime(3000 + 5000 + 3100);
    expect(last.resume?.requested_by).toBe('auto');
    expect(cancelResumeCountdown()).toBe(true);
    expect(scenarioEvents()[0]).toMatchObject({
      type: 'drive.resume.cancelled',
      payload: { reason: 'operator_cancelled' },
    });
    expect(last.resume).toBeNull();
    vi.advanceTimersByTime(10_000);
    expect(last.control?.mode).toBe('AUTO_READY'); // 다시 자동으로 돌아가지 않는다
    expect(cancelResumeCountdown()).toBe(false);
    expect(resume()).toBe(true);
    expect(last.resume?.requested_by).toBe('admin');
    vi.advanceTimersByTime(3100);
    expect(last.control).toMatchObject({ mode: 'AUTO', reason: 'resume_requested' });
  });

  it('카운트다운 중 자율주행이 다시 죽으면 복귀 취소, 수동주행 유지', () => {
    forceKill('autonomous');
    vi.advanceTimersByTime(3000 + 5000 + 3100);
    expect(last.resume).not.toBeNull();
    forceKill('autonomous');
    expect(scenarioEvents().some((e) => e.type === 'drive.resume.cancelled')).toBe(true);
    expect(last.resume).toBeNull();
    vi.advanceTimersByTime(3000);
    expect(last.control?.mode).toBe('MANUAL');
  });

  it('네비게이션을 죽여도 주행은 그대로 (격리), 수동주행이 제어 중에 죽으면 SAFE_STOP', () => {
    forceKill('message-board');
    expect(last.control).toMatchObject({ mode: 'AUTO', generation: 4 });
    expect(last.apps['message-board']?.heartbeat_ago_ms).toBeNull();
    vi.advanceTimersByTime(3000 + 5000 + 200);
    expect(last.apps['message-board']?.heartbeat_ago_ms).not.toBeNull();
    forceKill('autonomous');
    forceKill('manual');
    expect(last.control).toMatchObject({ mode: 'SAFE_STOP', active_app: null });
    vi.advanceTimersByTime(3000 + 5000 + 200);
    expect(last.control?.mode).toBe('MANUAL');
  });

  it('app.heartbeat 경과 시간은 0.1초 단위로 계속 바뀐다', () => {
    const seen = new Set<number | null>();
    for (let i = 0; i < 10; i++) {
      vi.advanceTimersByTime(100);
      seen.add(last.apps['message-board']!.heartbeat_ago_ms);
    }
    expect(seen.size).toBe(10);
    for (const ms of seen) expect((ms ?? 0) % 100).toBe(0);
  });

  it('화면 배치: revision 이 맞아야 바뀌고, 모니터가 답이 없어도 잠시 뒤 적용된다', () => {
    const [first] = getDisplays();
    expect(() =>
      setRoute(first!.display_id, { vm_id: VM_IDS.manual, view: 'os-face', expected_revision: 99 }),
    ).toThrow(RouteError);
    const next = setRoute(first!.display_id, {
      vm_id: VM_IDS.manual,
      view: 'os-face',
      expected_revision: first!.revision,
    });
    expect(next).toMatchObject({ status: 'SWITCHING', revision: first!.revision + 1 });
    vi.advanceTimersByTime(1600);
    expect(getDisplays()[0]).toMatchObject({
      status: 'ACTIVE',
      observed_route: { vm_id: VM_IDS.manual, view: 'os-face' },
    });
  });

  it('VM 화면 [정지]로 자율주행 VM 을 멈춰도 바로 MANUAL 로 인계, [시작]하면 다시 AUTO_READY', () => {
    stopVm(VM_IDS.autonomous);
    expect(last.control).toMatchObject({ mode: 'MANUAL', active_app: 'manual' });
    expect(vmStateOf(VM_IDS.autonomous)).toBe('stopped');
    vi.advanceTimersByTime(20_000);
    expect(last.control?.mode).toBe('MANUAL'); // 운영자가 멈춘 VM 은 복구 서비스가 다시 띄우지 않는다
    startVm(VM_IDS.autonomous);
    vi.advanceTimersByTime(5_000 + 3_000 + 500);
    expect(last.control?.mode).toBe('AUTO_READY');
  });

  it('MANUAL 이 아니면 키 입력을 버린다 (계약 §7)', () => {
    expect(setKeys(['up'])).toBe('NOT_MANUAL');
    forceKill('autonomous');
    expect(setKeys(['up'])).toBe('SENT');
    vi.advanceTimersByTime(1000); // 전진 키를 누르는 동안 서서히 빨라진다 (2초면 첫 장애물에 닿는다)
    expect(last.vehicle?.v_mps).toBeGreaterThan(0.3);
    expect(setKeys([])).toBe('RELEASED');
  });

  it('인지 끊김은 모드를 바꾸지 않고 자율주행이 정지 명령을 낸다 (D5)', () => {
    togglePerception();
    vi.advanceTimersByTime(2000);
    expect(last.control?.mode).toBe('AUTO');
    expect(last.perception?.valid).toBe(false);
    expect(last.apps.autonomous?.last_command?.v_mps).toBe(0);
    expect(last.vehicle?.v_mps).toBe(0);
  });
});
