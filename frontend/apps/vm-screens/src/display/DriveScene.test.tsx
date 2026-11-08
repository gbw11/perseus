import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DriveScene } from './DriveScene';
import type { RoadInfo, WorldObject } from './driveExtras';

const road: RoadInfo = {
  name: '중앙대로',
  lanes: 3,
  lane: 2,
  oncomingLanes: 3,
  intersection: { y_m: 2, crossRoad: '누리길', crossLanes: 2, turn: null },
  plan: null,
};
const red: WorldObject = { id: 's', kind: 'traffic_light', state: 'red', heading: 'same', x_m: 0, y_m: 2.1 };

function scene(extra: Partial<Parameters<typeof DriveScene>[0]> = {}) {
  return render(
    <DriveScene
      steer={0}
      objects={[red]}
      distanceM={0}
      stopped={false}
      braking={false}
      rainbow
      road={road}
      {...extra}
    />,
  );
}

describe('DriveScene', () => {
  it('사거리를 그리고, 빨간불이어도 정지선에 빨간 표시를 하지 않는다', () => {
    const { container } = scene();
    expect(screen.getByTestId('scene-intersection')).toBeInTheDocument();
    expect(container.querySelector('[data-red]')).toBeNull();
  });

  it('집이 가까우면 도로 오른쪽에 집을 그린다', () => {
    scene({ homeAheadM: 1.5 });
    expect(screen.getByTestId('scene-home')).toHaveTextContent('집');
  });

  it('집이 멀면 그리지 않는다', () => {
    scene({ homeAheadM: null });
    expect(screen.queryByTestId('scene-home')).toBeNull();
  });

  /** 무지개 조각 가운데들의 화면 x (가까운 것부터) */
  function routeXs(container: HTMLElement) {
    return [...container.querySelectorAll('[data-testid="scene-route"] polygon')].map((p) => {
      const xs = p
        .getAttribute('points')!
        .split(' ')
        .map((pt) => Number(pt.split(',')[0]));
      return xs.reduce((s, x) => s + x, 0) / xs.length;
    });
  }

  it('직진 사거리에서는 무지개 경로가 곧게 뻗는다', () => {
    const { container } = scene();
    expect(screen.getByTestId('scene-route')).toHaveAttribute('data-turn', 'none');
    expect(Math.max(...routeXs(container).map((x) => Math.abs(x - 400)))).toBeLessThan(5);
  });

  it.each([
    ['right', 1],
    ['left', -1],
  ] as const)('%s 회전 사거리에서는 무지개 경로가 그쪽으로 꺾인다', (turn, sign) => {
    const { container } = scene({ road: { ...road, intersection: { ...road.intersection!, turn } } });
    expect(screen.getByTestId('scene-route')).toHaveAttribute('data-turn', turn);
    const far = routeXs(container).slice(-5);
    expect(far.every((x) => Math.sign(x - 400) === sign && Math.abs(x - 400) > 100)).toBe(true);
  });

  it('추월 계획이 있으면 무지개가 옆 차로로 옮겨 갔다가 느린 차를 지나 원래 차로로 돌아온다', () => {
    const plain = { ...road, intersection: null };
    const overtaking = { ...plain, plan: { lane: 1, thenLane: 2, thenAfterM: 1.2 } };
    const { container } = scene({ road: overtaking, objects: [] });
    const xs = routeXs(container);
    // 차 바로 앞은 내 차로, 중간은 왼쪽(1차로)으로 옮겨 가 있고, 먼 곳은 다시 내 차로 쪽
    expect(Math.abs(xs[0]! - 400)).toBeLessThan(25);
    const shiftedLeft = xs.slice(0, Math.floor(xs.length / 3)).some((x) => x < 330);
    expect(shiftedLeft).toBe(true);
    expect(Math.abs(xs[xs.length - 1]! - 400)).toBeLessThan(15);
  });

  it('회전 사거리가 앞에 있어도 원호 전 직진 구간에서는 추월 계획대로 옆 차로로 옮겨 그린다', () => {
    const far = { ...road, intersection: { ...road.intersection!, y_m: 6, turn: 'right' as const } };
    const { container } = scene({
      road: { ...far, plan: { lane: 1, thenLane: 2, thenAfterM: 1.5 } },
      objects: [],
    });
    const xs = routeXs(container);
    expect(xs.slice(0, Math.floor(xs.length / 3)).some((x) => x < 330)).toBe(true);
  });

  it('다른 차는 진행 방향에 따라 미등(뒤)·전조등(앞)이 보인다', () => {
    const car = (id: string, heading: WorldObject['heading'], x_m: number): WorldObject => ({
      id,
      kind: 'car',
      state: null,
      heading,
      x_m,
      y_m: 1,
    });
    const { container } = scene({ objects: [car('a', 'same', 0.4), car('b', 'oncoming', -0.4)] });
    const lamp = (heading: string) =>
      container.querySelector(`[data-heading="${heading}"] [data-lamp]`)?.getAttribute('data-lamp');
    expect(lamp('same')).toBe('rear');
    expect(lamp('oncoming')).toBe('front');
  });

  /** 무지개 조각 꼭짓점들의 화면 y */
  function routeYs(container: HTMLElement) {
    return [...container.querySelectorAll('[data-testid="scene-route"] polygon')].flatMap((p) =>
      p
        .getAttribute('points')!
        .split(' ')
        .map((pt) => Number(pt.split(',')[1])),
    );
  }

  it('무지개 경로는 내 차 밑(뒤 범퍼, 화면 높이 450)까지 깔리고 그보다 뒤로는 그리지 않는다', () => {
    const { container } = scene();
    const bottom = Math.max(...routeYs(container));
    expect(bottom).toBeGreaterThan(440);
    expect(bottom).toBeLessThanOrEqual(451);
  });

  const turning = { ...road, intersection: { ...road.intersection!, turn: 'right' as const } };

  // 우회전 원호 반지름 0.45 m: 40° = 0.314 m 달린 곳, 남은 30° = 원호 끝 0.236 m 전
  const arcM = (deg: number) => ((deg * Math.PI) / 180) * 0.45;

  it('원호를 도는 중에도 장면이 비지 않고, 무지개 경로는 내 차 바로 앞에서 이어진다', () => {
    const { container } = scene({ road: turning, turnTravelM: arcM(40) });
    expect(container.querySelector('svg')).toHaveAttribute('data-yaw', '40');
    expect(container.querySelectorAll('polygon').length).toBeGreaterThan(20);
    // 내 차 밑에서 시작해 차 앞부터 원호를 따라 오른쪽으로 휜다
    const xs = routeXs(container);
    expect(Math.abs(xs[0]! - 400)).toBeLessThan(5);
    expect(xs.slice(0, 10).some((x) => x > 410)).toBe(true);
  });

  it('회전 교차로를 지난 뒤(도로 정보가 새 도로)에는 남은 각도만큼 반대로 돌아 있다', () => {
    const passed = { ...turning, intersection: { ...turning.intersection, y_m: -0.2 } };
    const { container } = scene({ road: passed, turnTravelM: arcM(60) });
    expect(container.querySelector('svg')).toHaveAttribute('data-yaw', '-30');
    expect(Math.abs(routeXs(container)[0]! - 400)).toBeLessThan(25);
  });

  it('도로 정보는 회전 뒤인데 원호 거리가 원호 전(음수)이면 어긋난 입력으로 보고 돌리지 않는다 (무지개 고리 방지)', () => {
    const passed = { ...turning, intersection: { ...turning.intersection, y_m: -1.2 } };
    const { container } = scene({ road: passed, turnTravelM: -21 });
    expect(container.querySelector('svg')).toHaveAttribute('data-yaw', '0');
    expect(Math.max(...routeYs(container))).toBeLessThanOrEqual(451);
  });

  it('원호 전에는 장면을 돌리거나 밀지 않는다 (무지개는 내 차 밑부터)', () => {
    const { container } = scene({ road: turning, turnTravelM: -0.3 });
    expect(container.querySelector('svg')).toHaveAttribute('data-yaw', '0');
    expect(Math.max(...routeYs(container))).toBeLessThanOrEqual(451);
  });
});
