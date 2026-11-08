import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CombinedPage } from './CombinedPage';
import { maxSpeedOf, RATED_RUNTIME_S, remainingRuntimeS, tripOf, worldObjectsOf } from './driveExtras';
import { presentationFixture } from './fixture';
import type { Presentation } from './model';
import { themeAt } from './theme';
import { useDisplay } from './useDisplay';

vi.mock('./useDisplay', () => ({ useDisplay: vi.fn() }));

const fresh = { status: 'fresh', age_ms: 50 } as const;

function driving(): Presentation {
  const p = presentationFixture();
  const [vm1] = p.state.vms;
  vm1!.app_state = 'ready';
  p.state.drive = {
    active_vm_id: vm1!.vm_id,
    mode: 'AUTO',
    ai_status: 'ok',
    source: fresh,
    authority: {
      selected_vm_id: vm1!.vm_id,
      selected_mode: 'AUTO',
      generation: 4,
      phase: 'APPLIED',
      output_permitted: true,
      storage_uncertain: false,
    },
  };
  Object.assign(p.state, {
    vehicle: {
      v_mps: 0.4,
      steer_rad: 0.05,
      batt_v: 7.6,
      flags: { estop: false, watchdog: false, obstacle: false },
      source: fresh,
    },
    drive_limits: { profile: 'lattice-lab-1', max_v_mps: 0.5 },
    trip: { distance_m: 3.25, driving_s: 75, source: fresh },
    world: { objects: [{ id: 'car-1', kind: 'car', x_m: 0.4, y_m: 1.5 }], source: fresh },
  });
  return p;
}

function show(p: Presentation | null, connected = true) {
  vi.mocked(useDisplay).mockReturnValue({ presentation: p, connected, error: connected ? null : '끊김' });
  return render(<CombinedPage />);
}

beforeEach(() => window.history.replaceState(null, '', '/display/combined?theme=light'));
afterEach(() => vi.clearAllMocks());

describe('CombinedPage 출발 전환', () => {
  afterEach(() => vi.useRealTimers());

  /** 회사에서 정차 (트립 0, 속도 0) 또는 막 출발 (트립 0, 속도 있음) */
  function atStart(speed: number) {
    const p = driving();
    Object.assign(p.state, {
      trip: { distance_m: 0, driving_s: 0, source: fresh },
      vehicle: { v_mps: speed, steer_rad: 0, batt_v: 7.6, flags: {}, source: fresh },
    });
    return p;
  }

  it('출발하면 주행 화면을 아래에 깔고, 위의 정차 화면이 출발 연출 동안 흐려진 뒤 사라진다', () => {
    vi.useFakeTimers({
      toFake: ['setTimeout', 'clearTimeout', 'performance', 'requestAnimationFrame', 'cancelAnimationFrame'],
    });
    const { rerender } = show(atStart(0));
    expect(screen.getByTestId('parked-view')).toBeInTheDocument();
    expect(screen.queryByTestId('speed-value')).toBeNull();

    vi.mocked(useDisplay).mockReturnValue({ presentation: atStart(0.3), connected: true, error: null });
    rerender(<CombinedPage />);
    expect(screen.getByTestId('speed-value')).toBeInTheDocument();
    expect(screen.getByTestId('depart-layer')).toContainElement(screen.getByTestId('parked-view'));

    act(() => vi.advanceTimersByTime(3300));
    expect(screen.queryByTestId('depart-layer')).toBeNull();
    expect(screen.getByTestId('speed-value')).toBeInTheDocument();
  });
});

describe('CombinedPage (모니터 2대를 합친 화면)', () => {
  it('속도(m/s)·최대 속도·예상 주행 가능 시간·트립·VM 상태를 한 화면에 보인다', () => {
    show(driving());
    expect(screen.getByTestId('speed-value')).toHaveTextContent('0.40');
    expect(screen.getByLabelText('최대 속도')).toHaveTextContent('0.5');
    // 완충 60분 기준 − 주행 75초 = 58분
    expect(screen.getByTestId('runtime-value')).toHaveTextContent('58');
    expect(screen.getByText('3.3')).toBeInTheDocument();
    expect(screen.getByTestId('active-vm')).toHaveTextContent('VM1 운전 중');
    expect(screen.getByTestId('dock-VM3')).toHaveTextContent('인포테인먼트');
    // 트립 3.25 m × 축척 60 = 지도 195 m: 은행나무길 동쪽으로, 다음은 한빛로 우회전
    expect(screen.getByTestId('nav-banner')).toHaveTextContent('우회전 · 한빛로');
    expect(screen.getByTestId('combined')).toHaveAttribute('data-theme', 'light');
  });

  it('앞 신호등이 빨간불이면 정지 안내와 신호 정지 경고등을 켠다', () => {
    const p = driving();
    (p.state as unknown as { world: { objects: unknown[] } }).world.objects.push({
      id: 'signal-1',
      kind: 'traffic_light',
      x_m: 0,
      y_m: 0.5,
      state: 'red',
    });
    show(p);
    expect(screen.getByTestId('signal-note')).toHaveTextContent('빨간불 인식 · 정지');
    expect(screen.getByTestId('signal-note')).toHaveTextContent('30 m');
    expect(screen.getByTestId('lamp-signal')).toHaveAttribute('data-on', 'true');
  });

  it('신호등이 없으면 신호 안내를 띄우지 않는다', () => {
    show(driving());
    expect(screen.queryByTestId('signal-note')).toBeNull();
    expect(screen.getByTestId('lamp-signal')).toHaveAttribute('data-on', 'false');
  });

  it('?theme=dark 면 어두운 테마로 고정된다', () => {
    window.history.replaceState(null, '', '/display/combined?theme=dark');
    show(driving());
    expect(screen.getByTestId('combined')).toHaveAttribute('data-theme', 'dark');
  });

  it('주행 중 표시 연결이 끊기면 정차 화면으로 돌아가지 않고 값을 지우고 알린다', () => {
    const { rerender } = show(driving());
    vi.mocked(useDisplay).mockReturnValue({ presentation: driving(), connected: false, error: '끊김' });
    rerender(<CombinedPage />);
    expect(screen.queryByTestId('parked-view')).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('현재 값은 미확인');
    expect(screen.getByTestId('speed-value')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-value')).toHaveTextContent('—');
    expect(screen.getByLabelText('최대 속도')).toHaveTextContent('—');
  });

  it('트립·속도를 아직 못 받았으면 빈 주행 화면 대신 정차 화면에 둔다', () => {
    show(presentationFixture());
    expect(screen.getByTestId('parked-view')).toBeInTheDocument();
    expect(screen.queryByTestId('speed-value')).toBeNull();
  });

  it('처음부터 연결이 안 되면 정차 화면에 둔다', () => {
    show(driving(), false);
    expect(screen.getByTestId('parked-view')).toBeInTheDocument();
  });
});

describe('driveExtras 제안 필드', () => {
  it('최대 속도·트립·주변 물체를 읽고, 신선하지 않으면 버린다', () => {
    const p = driving();
    expect(maxSpeedOf(p)).toBe(0.5);
    expect(tripOf(p)).toEqual({ distance_m: 3.25, driving_s: 75 });
    expect(worldObjectsOf(p)).toHaveLength(1);
    (p.state as unknown as { world: { source: unknown } }).world.source = { status: 'stale', age_ms: 900 };
    expect(worldObjectsOf(p)).toBeNull();
  });

  it('잔량 센서가 없어 예상 주행 가능 시간은 완충 기준 시간에서 주행 시간을 빼서 추정한다', () => {
    expect(remainingRuntimeS(0)).toBe(RATED_RUNTIME_S);
    expect(remainingRuntimeS(600)).toBe(RATED_RUNTIME_S - 600);
    expect(remainingRuntimeS(RATED_RUNTIME_S + 10)).toBe(0);
  });

  it('테마는 낮 07~19시 밝게, 그 밖에는 어둡게', () => {
    expect(themeAt(new Date(2026, 10, 5, 12))).toBe('light');
    expect(themeAt(new Date(2026, 10, 5, 21))).toBe('dark');
    expect(themeAt(new Date(2026, 10, 5, 6))).toBe('dark');
  });
});
