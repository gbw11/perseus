import { describe, expect, it } from 'vitest';
import { build, IDS, SCENARIOS, setScenario, setTripForTest } from '../../scripts/fake-display-feed.mjs';
import { insideSlot, SCENARIO_MARKS, SLOTS } from './course';
import { parsePresentation } from './model';

interface Course {
  pose: { x: number; y: number; yaw: number };
  obstacles: unknown[];
  plan: [number, number][];
}
type Feed = {
  state: {
    drive: { mode: string; active_vm_id: string | null };
    vehicle: { v_mps: number };
    drive_apps: Record<string, { autonomous: { command: unknown } }>;
    trip: { distance_m: number };
    course: Course;
  };
};

/** 노트북 미리보기 가짜 수집기가 실제 화면 규격 검사를 통과하는지 (scripts/fake-display-feed.mjs) */
describe('fake display feed', () => {
  const now = Date.now();
  const fixed = SCENARIOS.filter((name) => name !== 'cycle' && name !== 'feed-down');

  it.each(fixed)('%s 시나리오는 parsePresentation 을 통과한다', (name) => {
    setScenario(name);
    for (const offset of [0, 1500, 3500]) {
      expect(() => parsePresentation(build(now + offset))).not.toThrow();
    }
  });

  it('cycle 은 40초 동안 모든 단계가 규격을 통과한다', () => {
    setScenario('cycle');
    for (let s = 0; s < 40; s += 0.5) {
      expect(() => parsePresentation(build(now + s * 1000))).not.toThrow();
    }
  });

  it('제안 확장 필드: 주행 중인 VM 의 앱만 명령을 낸다', () => {
    setScenario('cruise');
    setTripForTest(1.5, now);
    const p = build(now + 200) as Feed;
    expect(p.state.vehicle.v_mps).toBeGreaterThan(0);
    expect(p.state.drive_apps[IDS.primary]!.autonomous.command).not.toBeNull();
    expect(p.state.drive_apps[IDS.backup]!.autonomous.command).toBeNull();
  });

  it('safe-stop 은 활성 VM 이 없고 VM1 보고가 끊긴다', () => {
    setScenario('safe-stop');
    const p = parsePresentation(build(now));
    expect(p.state.drive.mode).toBe('SAFE_STOP');
    expect(p.state.drive.active_vm_id).toBeNull();
    expect(p.state.vms.find((vm) => vm.vm_id === IDS.primary)!.xen_state).toBe('crashed');
  });

  it('한 바퀴 시연: VM1 자율 → 수동 → 자율 → 안전 정지 → VM2 승계 준비 → VM2 자율 → P1 에 선다', () => {
    setScenario('cycle');
    setTripForTest(0, now);
    const order: string[] = [];
    const who = (id: string | null) => (id === IDS.primary ? 'VM1' : id === IDS.backup ? 'VM2' : '-');
    let last: Feed | null = null;
    for (let t = 0.25; t < 120; t += 0.25) {
      const p = build(now + t * 1000) as Feed;
      expect(() => parsePresentation(p)).not.toThrow();
      const step = `${p.state.drive.mode}:${who(p.state.drive.active_vm_id)}`;
      if (order[order.length - 1] !== step) order.push(step);
      last = p;
      if (p.state.vehicle.v_mps === 0 && insideSlot(p.state.course.pose, SLOTS[0]!)) break;
    }
    expect(order).toEqual(['AUTO:VM1', 'MANUAL:VM1', 'AUTO:VM1', 'SAFE_STOP:-', 'AUTO_READY:-', 'AUTO:VM2']);
    expect(insideSlot(last!.state.course.pose, SLOTS[0]!)).toBe(true);
    expect(last!.state.course.plan).toEqual([]);
  });

  it('자율 운전 중에만 주행 계획을 보내고, 수동 운전 중에는 비운다', () => {
    setScenario('cycle');
    setTripForTest(0.5, now);
    const auto = build(now + 100) as Feed;
    expect(auto.state.course.plan.length).toBeGreaterThan(10);
    expect(auto.state.course.obstacles).toHaveLength(2);
    setTripForTest((SCENARIO_MARKS.manualOn + SCENARIO_MARKS.manualOff) / 2, now);
    const manual = build(now + 100) as Feed;
    expect(manual.state.drive.mode).toBe('MANUAL');
    expect(manual.state.course.plan).toEqual([]);
  });
});
