import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DriveState } from '../api/drive';
import { VM_IDS } from './data';
import {
  forceKill,
  getDisplays,
  resetScenario,
  resume,
  RouteError,
  scenarioEvents,
  setKeys,
  setRoute,
  snapshot,
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

  it('강제 종료 → MANUAL 인계 → 복구 → AUTO_READY → 재개 3·2·1 → AUTO, 매번 gen +1', () => {
    expect(resume()).toBe(false); // AUTO_READY 가 아니면 무시
    forceKill('autonomous');
    expect(last.control).toMatchObject({ mode: 'MANUAL', generation: 5, active_app: 'manual' });
    expect(vmStateOf(VM_IDS.autonomous)).toBe('crashed');
    vi.advanceTimersByTime(3200);
    expect(vmStateOf(VM_IDS.autonomous)).toBe('running');
    expect(last.apps.autonomous?.command_fresh).toBe(false); // 재시작 중
    vi.advanceTimersByTime(5000 + 3200);
    expect(last.control).toMatchObject({ mode: 'AUTO_READY', generation: 6 });
    expect(resume()).toBe(true);
    expect(resume()).toBe(false); // 카운트다운 중 재요청은 무시
    expect(scenarioEvents()[0]).toMatchObject({
      type: 'drive.resume.accepted',
      payload: { switch_in_ms: 3000 },
    });
    expect(snapshot().control?.mode).toBe('AUTO_READY'); // 바로 바뀌지 않는다
    expect(snapshot().resume?.remaining_ms).toBe(3000);
    vi.advanceTimersByTime(2000);
    expect(last.resume?.remaining_ms).toBeLessThanOrEqual(1000);
    vi.advanceTimersByTime(1100);
    expect(last.control).toMatchObject({ mode: 'AUTO', generation: 7, active_app: 'autonomous' });
    expect(last.resume).toBeNull();
  });

  it('카운트다운 중 자율주행이 다시 죽으면 재개 취소, 수동주행 유지', () => {
    forceKill('autonomous');
    vi.advanceTimersByTime(3000 + 5000 + 3200);
    expect(resume()).toBe(true);
    vi.advanceTimersByTime(1000);
    forceKill('autonomous');
    expect(scenarioEvents().some((e) => e.type === 'drive.resume.cancelled')).toBe(true);
    expect(last.resume).toBeNull();
    vi.advanceTimersByTime(3000);
    expect(last.control?.mode).toBe('MANUAL');
  });

  it('인터랙션을 죽여도 주행은 그대로 (격리), 수동주행이 제어 중에 죽으면 SAFE_STOP', () => {
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

  it('MANUAL 이 아니면 키 입력을 버린다 (계약 §7)', () => {
    expect(setKeys(['up'])).toBe('NOT_MANUAL');
    forceKill('autonomous');
    expect(setKeys(['up'])).toBe('SENT');
    vi.advanceTimersByTime(2000);
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
