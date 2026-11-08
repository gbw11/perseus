import { describe, expect, it } from 'vitest';
import {
  INTERSECTIONS,
  laneGuide,
  passedTurn,
  PLACES,
  ROUTE,
  ROUTE_LENGTH,
  routePosition,
  turnAngle,
  turnArc,
} from './cityMap';

describe('회사 → 집 경로', () => {
  it('출발점은 회사, 첫 안내는 500 m 뒤 우회전 · 한빛로', () => {
    const p = routePosition(0);
    expect([p.x, p.y]).toEqual([PLACES.origin.x, PLACES.origin.y]);
    expect(p.next).toEqual({ turn: 'right', inM: 500, road: '한빛로' });
  });

  it('교차로마다 회전 방향이 지도와 맞다 (우·좌·우·좌·우)', () => {
    const turns: string[] = [];
    let acc = 0;
    for (let i = 0; i < ROUTE.length - 2; i += 1) {
      acc += Math.hypot(ROUTE[i + 1]!.x - ROUTE[i]!.x, ROUTE[i + 1]!.y - ROUTE[i]!.y);
      turns.push(routePosition(acc - 1).next!.turn);
    }
    expect(turns).toEqual(['right', 'left', 'right', 'left', 'right']);
  });

  it('끝까지 가면 집에 도착하고 그 뒤로 넘어가지 않는다', () => {
    const p = routePosition(ROUTE_LENGTH + 500);
    expect(p.arrived).toBe(true);
    expect(p.remaining).toBe(0);
    expect([p.x, p.y]).toEqual([PLACES.destination.x, PLACES.destination.y]);
  });
});

describe('차로 안내', () => {
  it('교차로 600 m 밖에서는 보이지 않는다', () => {
    expect(laneGuide(routePosition(0), 400)).toBeNull();
  });

  it('우회전 전에는 가장 바깥(오른쪽) 차로를 권한다 — 은행나무길(골목 2차로) → 한빛로', () => {
    const guide = laneGuide(routePosition(300))!;
    expect(guide.turn).toBe('right');
    expect(guide.lanes.map((l) => l.recommended)).toEqual([false, true]);
    expect(guide.lanes[1]!.arrow).toBe('right');
  });

  it('좌회전 전에는 1차로를 권한다 — 한빛로(큰길 3차로) → 중앙대로', () => {
    const guide = laneGuide(routePosition(700))!;
    expect(guide.turn).toBe('left');
    expect(guide.lanes.map((l) => l.recommended)).toEqual([true, false, false]);
    expect(guide.lanes[0]!.arrow).toBe('left');
  });

  it('도착 직전 직진 구간에서는 보이지 않는다', () => {
    expect(laneGuide(routePosition(ROUTE_LENGTH - 50))).toBeNull();
  });
});

describe('회전할 때 원호를 도는 각도', () => {
  it('우회전 교차로(500 m): 원호 전 0, 원호를 따라 0 → 90, 원호가 끝나면 90 (가로 도로 바깥 차로에서 끝나 가운데 전에 다 돈다)', () => {
    const o = INTERSECTIONS.find((i) => Math.abs(i.at - 500) < 1)!;
    const arc = turnArc(o)!;
    expect(arc.sign).toBe(1);
    expect(arc.start + arc.length).toBeLessThan(500);
    expect(turnAngle(300)).toBe(0);
    expect(turnAngle(arc.start + arc.length / 2)).toBeCloseTo(45, 0);
    expect(turnAngle(499)).toBe(90);
  });

  it('좌회전(900 m)은 반대 부호이고 원호가 크다 (교차로 가운데를 지나서 끝난다)', () => {
    const o = INTERSECTIONS.find((i) => Math.abs(i.at - 900) < 1)!;
    const arc = turnArc(o)!;
    expect(arc.sign).toBe(-1);
    expect(arc.start).toBeLessThan(900);
    expect(arc.start + arc.length).toBeGreaterThan(900);
    expect(turnAngle(900)).toBeLessThan(0);
    expect(turnAngle(900)).toBeGreaterThan(-90);
  });

  it('원호가 끝날 때까지 지나온 도로를 알려 준다', () => {
    expect(passedTurn(510)).toMatchObject({ fromRoad: '은행나무길', behindM: 10, turn: 'right' });
    const left = turnArc(INTERSECTIONS.find((i) => Math.abs(i.at - 900) < 1)!)!;
    expect(passedTurn(left.start + left.length - 1)).not.toBeNull();
    expect(passedTurn(580)).toBeNull();
  });
});
