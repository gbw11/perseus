import { describe, expect, it } from 'vitest';
import { build, IDS, SCENARIOS, setScenario, setTripForTest } from '../../scripts/fake-display-feed.mjs';
import { INTERSECTIONS, ROUTE_LENGTH, ROUTE_SCALE, stopLineBefore, turnArc } from './cityMap';
import { parsePresentation } from './model';

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
    // 중앙대로 한가운데 (다음 사거리까지 300 m) — 신호에 걸리지 않는 곳
    setTripForTest(1200, now);
    const p = build(now) as {
      state: {
        vehicle: { v_mps: number; batt_v: number };
        drive_apps: Record<string, { autonomous: { command: unknown } }>;
      };
    };
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

  it('출발 전에는 정차, 사거리 앞에서는 신호등과 가로 도로를 알린다', () => {
    setScenario('cruise');
    const parked = build(Date.now()) as { state: { scenario: string } };
    expect(['parked', 'cruise']).toContain(parked.state.scenario);
    setTripForTest(1300, now);
    const near = build(now + 500) as {
      state: {
        road: { lanes: number; intersection: { cross_road: string } | null };
        world: { objects: { kind: string; heading?: string }[] };
      };
    };
    expect(near.state.road.lanes).toBe(3);
    expect(near.state.road.intersection?.cross_road).toBe('누리길');
    expect(near.state.world.objects.some((o) => o.kind === 'traffic_light')).toBe(true);
    // 가로지르는 차는 진행 방향(왼쪽·오른쪽)을 하나씩 가진다
    const cross = near.state.world.objects.filter((o) => o.heading?.startsWith('cross'));
    expect(cross.map((o) => o.heading).sort()).toEqual(['cross_left', 'cross_right']);
  });

  it('빨간불이면 정지선 앞의 같은 방향 차도 정지선을 넘지 않고 선다', () => {
    setScenario('cruise');
    setTripForTest(1300, now);
    type Obj = { id: string; kind: string; heading?: string; y_m: number; state?: string };
    let before: Map<string, number> | null = null;
    let checked = 0;
    let waiting = 0;
    for (let s = 0.25; s < 60; s += 0.25) {
      const p = build(now + s * 1000) as {
        state: {
          world: { objects: Obj[] };
          road: { intersection: { y_m: number; cross_lanes: number } | null };
        };
      };
      const light = p.state.world.objects.find((o) => o.kind === 'traffic_light');
      const cross = p.state.road.intersection;
      if (light?.state !== 'red' || !cross || cross.y_m <= 0) {
        before = null;
        continue;
      }
      // 정지선 = 사거리 가운데 앞 (가로 도로 반폭 + 횡단보도), 장면이 그리는 정지선과 같다
      const stopLine = cross.y_m - stopLineBefore({ crossLanes: cross.cross_lanes }) / ROUTE_SCALE;
      const same = p.state.world.objects.filter((o) => o.heading === 'same');
      for (const o of same) {
        const prev = before?.get(o.id);
        if (prev !== undefined && prev + 0.46 < stopLine) {
          checked += 1;
          expect(o.y_m + 0.46).toBeLessThanOrEqual(stopLine + 0.03);
          if (Math.abs(o.y_m - prev) < 0.001) waiting += 1;
        }
      }
      before = new Map(same.map((o) => [o.id, o.y_m]));
    }
    expect(checked).toBeGreaterThan(0);
    expect(waiting).toBeGreaterThan(0);
  });

  it('앞에 느린 차가 있으면 옆 차로로 옮겨 추월하고 원래 차로로 돌아온다', () => {
    setScenario('cruise');
    setTripForTest(5, now);
    type Obj = { id: string; x_m: number; y_m: number };
    let seenAhead = false;
    let laneAtSpawn: number | null = null;
    let changed = false;
    let passed = false;
    let backInLane = false;
    for (let s = 0.5; s < 240 && !backInLane; s += 0.5) {
      const p = build(now + s * 1000) as { state: { road: { lane: number }; world: { objects: Obj[] } } };
      const car = p.state.world.objects.find((o) => o.id === 'slow');
      const lane = p.state.road.lane;
      if (car && !seenAhead && Math.abs(car.x_m) < 0.01 && car.y_m > 0) {
        seenAhead = true;
        laneAtSpawn = lane;
      }
      if (seenAhead && laneAtSpawn !== null && Math.abs(lane - laneAtSpawn) >= 1) changed = true;
      // 추월: 느린 차가 다른 차로(옆)에 있는 채로 내 뒤로 지나간다
      if (changed && car && car.y_m < -0.1 && Math.abs(car.x_m) > 0.3) passed = true;
      if (passed && laneAtSpawn !== null && lane === laneAtSpawn) backInLane = true;
    }
    expect(seenAhead).toBe(true);
    expect(changed).toBe(true);
    expect(passed).toBe(true);
    expect(backInLane).toBe(true);
  });

  it('회사 → 집 모든 사거리: 원호 전에 회전 차로에 들어가 있고, 새 도로는 원호가 닿는 차로에서 시작하며 차로가 튀지 않는다', () => {
    setScenario('cruise');
    setTripForTest(1, now);
    type Road = {
      name: string;
      lanes: number;
      lane: number;
      intersection: { turn: string | null; y_m: number } | null;
    };
    const arcs = INTERSECTIONS.filter((o) => o.turn).map((o) => ({ o, arc: turnArc(o)!, checked: false }));
    let prev: Road | null = null;
    let switches = 0;
    for (let s = 0.25; s < 600; s += 0.25) {
      const p = build(now + s * 1000) as { state: { road: Road; trip: { distance_m: number } } };
      const road = p.state.road;
      const d = p.state.trip.distance_m * ROUTE_SCALE;
      if (d > ROUTE_LENGTH - 5) break;
      // 1. 원호가 시작될 때 이미 회전 차로 (좌회전 1차로, 우회전 맨 바깥 차로)
      for (const a of arcs) {
        if (!a.checked && d >= a.arc.start) {
          a.checked = true;
          expect(road.lane).toBe(a.o.turn === 'left' ? 1 : road.lanes);
        }
      }
      if (prev && prev.name !== road.name) {
        // 2. 회전으로 새 도로에 들어서면 원호가 닿는 차로에서 시작
        switches += 1;
        const turn = road.intersection?.turn;
        if (turn) expect(road.lane).toBe(turn === 'left' ? 1 : road.lanes);
      } else if (prev) {
        // 3. 같은 도로에서는 한 번에 반 차로보다 많이 옮기지 않는다
        expect(Math.abs(road.lane - prev.lane)).toBeLessThanOrEqual(0.5 + 1e-9);
      }
      prev = road;
    }
    expect(arcs.every((a) => a.checked)).toBe(true);
    expect(switches).toBeGreaterThan(3);
  });

  it('차로 변경은 시간 기준: 화면이 자주 물어도 초당 반 차로보다 빨리 옮기지 않는다', () => {
    setScenario('cruise');
    // 한빛로 우회전 직후 (3차로 맨 바깥) → 다음 좌회전을 위해 1차로로 옮겨 간다
    setTripForTest(545, now);
    type P = { state: { road: { lane: number } } };
    const start = (build(now) as P).state.road.lane;
    let lane = start;
    for (let ms = 100; ms <= 1000; ms += 100) lane = (build(now + ms) as P).state.road.lane;
    expect(start).toBe(3);
    expect(start - lane).toBeGreaterThan(0);
    expect(start - lane).toBeLessThanOrEqual(0.5 + 1e-9);
  });

  it('1차로에 있으면 왼쪽 두 차로는 반대 방향 차다', () => {
    setScenario('cruise');
    // 한빛로 → 중앙대로 좌회전(900 m) 직전: 1차로로 옮겨 가 있다
    setTripForTest(760, now);
    let p = build(now + 1000) as {
      state: { road: { lane: number }; world: { objects: { x_m: number; heading?: string }[] } };
    };
    for (let i = 2; i < 6; i += 1) p = build(now + i * 1000) as typeof p;
    expect(p.state.road.lane).toBe(1);
    const left = p.state.world.objects.filter((o) => o.x_m < 0 && !o.heading?.startsWith('cross'));
    expect(left).toHaveLength(2);
    expect(left.every((o) => o.heading === 'oncoming')).toBe(true);
  });
});
