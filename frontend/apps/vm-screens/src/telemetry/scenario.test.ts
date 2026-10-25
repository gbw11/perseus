import { describe, expect, it } from 'vitest';
import {
  AUTO_READY_FRESH_MS,
  BOOT_MS,
  RESTART_DELAY_MS,
  crash,
  initialTelemetry,
  resume,
  step,
  toggleStress,
} from './scenario';
import type { Telemetry } from './types';

/** dt 간격으로 duration 동안 시간을 흘린다 */
function run(t: Telemetry, duration: number, dt = 10): Telemetry {
  let next = t;
  const end = t.now + duration;
  while (next.now < end) next = step(next, next.now + dt);
  return next;
}

const start = () => run(initialTelemetry(0), 200);

describe('가짜 시나리오 (MQTT 계약 §5, RC카 시나리오 §4)', () => {
  it('정상: AUTO 에서 자율주행 명령으로 달린다', () => {
    const t = run(start(), 2000);
    expect(t.mode).toBe('AUTO');
    expect(t.activeApp).toBe('autonomous');
    expect(t.vehicle.v_mps).toBeGreaterThan(0.2);
    expect(t.apps.autonomous.commandHz).toBe(50);
  });

  it('자율주행 VM 이 죽으면 100 ms 남짓 안에 수동주행으로 인계한다', () => {
    const before = start();
    const crashed = crash(before, 'autonomous');
    const after = run(crashed, 150);
    expect(after.mode).toBe('MANUAL');
    expect(after.activeApp).toBe('manual');
    expect(after.generation).toBe(before.generation + 1);
    expect(after.apps.autonomous.health).toBe('fault');
    expect(after.events.some((e) => e.type === 'drive.fault.detected')).toBe(true);
  });

  it('복구 → 3초 신선 → AUTO_READY → 재개로 AUTO 복귀', () => {
    let t = run(crash(start(), 'autonomous'), 150);
    t = run(t, RESTART_DELAY_MS + 50);
    expect(t.apps.autonomous.health).toBe('recovering');
    t = run(t, BOOT_MS + AUTO_READY_FRESH_MS + 200);
    expect(t.mode).toBe('AUTO_READY');
    expect(t.apps.autonomous.health).toBe('ok');
    t = resume(t);
    expect(t.mode).toBe('AUTO');
    expect(t.activeApp).toBe('autonomous');
  });

  it('재개 요청은 AUTO_READY 가 아니면 무시한다', () => {
    const t = start();
    expect(resume(t)).toBe(t);
  });

  it('자율·수동 둘 다 죽으면 SAFE_STOP 으로 멈춘다', () => {
    let t = crash(crash(start(), 'manual'), 'autonomous');
    t = run(t, 1000);
    expect(t.mode).toBe('SAFE_STOP');
    expect(t.activeApp).toBeNull();
    expect(t.vehicle.v_mps).toBe(0);
  });

  it('네비게이션 VM 장애는 주행에 영향이 없다 (격리)', () => {
    let t = crash(start(), 'interaction');
    t = run(t, 1000);
    expect(t.mode).toBe('AUTO');
    expect(t.apps.interaction.health).toBe('fault');
    t = run(t, RESTART_DELAY_MS + BOOT_MS + 100);
    expect(t.apps.interaction.health).toBe('ok');
  });

  it('자율주행에 CPU 부하가 걸려 명령 간격이 100 ms 를 넘으면 인계한다 (F2)', () => {
    const t = run(toggleStress(start(), 'autonomous'), 400);
    expect(t.mode).toBe('MANUAL');
  });
});
