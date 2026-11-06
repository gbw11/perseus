import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WorldObject } from './driveExtras';
import { useChase, useSmoothObjects } from './useSmoothObjects';

const car = (y_m: number, x_m = 0.38): WorldObject => ({
  id: 'same-lane3',
  kind: 'car',
  state: null,
  heading: 'same',
  x_m,
  y_m,
});

describe('주변 차 보간', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['performance', 'requestAnimationFrame', 'cancelAnimationFrame', 'Date'] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('1초마다 오는 위치 사이를 속도로 이어 그린다 (새 값이 와도 뚝 끊기지 않는다)', () => {
    const { result, rerender } = renderHook(({ objs }) => useSmoothObjects(objs), {
      initialProps: { objs: [car(1)] },
    });
    act(() => vi.advanceTimersByTime(1000));
    // 새 값 0.7: 그리던 위치(1)에서 바로 0.7 로 뛰지 않는다
    rerender({ objs: [car(0.7)] });
    expect(result.current[0]!.y_m).toBeCloseTo(1, 1);
    // 0.5초 뒤: 초당 -0.3 m 로 계속 움직여 약 0.55
    act(() => vi.advanceTimersByTime(500));
    expect(result.current[0]!.y_m).toBeGreaterThan(0.5);
    expect(result.current[0]!.y_m).toBeLessThan(0.6);
  });

  it('화면 밖으로 나갔다 다시 나타나면(크게 뜀) 이어 그리지 않고 바로 옮긴다', () => {
    const { result, rerender } = renderHook(({ objs }) => useSmoothObjects(objs), {
      initialProps: { objs: [car(2.1)] },
    });
    act(() => vi.advanceTimersByTime(1000));
    rerender({ objs: [car(-1.2)] });
    expect(result.current[0]!.y_m).toBe(-1.2);
  });

  it('차로 값은 넘치지 않게 초당 rate 만큼 따라간다', () => {
    const { result, rerender } = renderHook(({ v }) => useChase(v, 0.8), { initialProps: { v: 2 } });
    rerender({ v: 1.5 });
    act(() => vi.advanceTimersByTime(300));
    expect(result.current!).toBeGreaterThan(1.5);
    expect(result.current!).toBeLessThan(2);
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current!).toBe(1.5);
  });
});
