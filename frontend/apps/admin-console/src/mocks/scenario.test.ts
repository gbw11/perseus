import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DriveState } from '../api/drive';
import { VM_IDS } from './data';
import {
  forceKill,
  resetScenario,
  resume,
  setKeys,
  snapshot,
  subscribe,
  togglePerception,
  vmStateOf,
} from './scenario';

describe('mock 시연 시나리오 (MQTT 계약 v1.1 §5.2)', () => {
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

  it('강제 종료 → MANUAL 인계 → 복구 → AUTO_READY → 재개 → AUTO, 매번 gen +1', () => {
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
    expect(snapshot().control).toMatchObject({ mode: 'AUTO', generation: 7, active_app: 'autonomous' });
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
