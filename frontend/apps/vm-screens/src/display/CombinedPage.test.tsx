import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CombinedPage } from './CombinedPage';
import { OBSTACLE_SEEDS } from './course';
import { courseOf, maxSpeedOf, RATED_RUNTIME_S, remainingRuntimeS, tripOf } from './driveExtras';
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
    // 출발 자리 (아래 직선 안쪽 차로, 서쪽으로): 앞범퍼에서 콘 A 까지 55 cm
    course: {
      track_id: 'jetson-demo',
      pose: { x: 2.81, y: 0.7, yaw: Math.PI },
      obstacles: OBSTACLE_SEEDS,
      plan: [
        [2.81, 0.7],
        [2.3, 0.6],
      ],
      source: fresh,
    },
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
    expect(screen.getByTestId('nav-banner')).toHaveTextContent('장애물 회피');
    expect(screen.getByTestId('nav-lane')).toHaveTextContent('안쪽 차로');
    expect(screen.getByTestId('nav-car')).toBeInTheDocument();
    expect(screen.getByTestId('scene-route')).toBeInTheDocument();
    expect(screen.getByTestId('combined')).toHaveAttribute('data-theme', 'light');
  });

  it('차 앞 1 m 안에 콘이 있으면 인식 안내와 장애물 경고등을 켠다', () => {
    show(driving());
    expect(screen.getByTestId('obstacle-note')).toHaveTextContent('콘 A 인식');
    expect(screen.getByTestId('obstacle-note')).toHaveTextContent('55 cm');
    expect(screen.getByTestId('lamp-obstacle')).toHaveAttribute('data-on', 'true');
  });

  it('콘을 다 지나면 콘 안내를 끄고 회피 단계를 끝낸다', () => {
    const p = driving();
    // 위 직선 가운데 (동쪽으로)
    (p.state as unknown as { course: { pose: unknown } }).course.pose = { x: 2.2, y: 2.3, yaw: 0 };
    show(p);
    expect(screen.getByTestId('step-avoid')).toHaveAttribute('data-state', 'done');
    expect(screen.queryByTestId('obstacle-note')).toBeNull();
    expect(screen.getByTestId('lamp-obstacle')).toHaveAttribute('data-on', 'false');
  });

  it('수동 전환을 지나면 회피·수동 단계가 끝나고, VM1 장애 뒤 VM2 가 운전하면 승계 단계도 끝난다', () => {
    const { rerender } = show(driving());
    const step = (key: string) => screen.getByTestId(`step-${key}`);
    expect(step('avoid')).toHaveAttribute('data-state', 'active');

    const manual = driving();
    manual.state.drive.mode = 'MANUAL';
    vi.mocked(useDisplay).mockReturnValue({ presentation: manual, connected: true, error: null });
    rerender(<CombinedPage />);
    expect(step('avoid')).toHaveAttribute('data-state', 'done');
    expect(step('manual')).toHaveAttribute('data-state', 'active');
    expect(step('manual')).toHaveTextContent('수동 운전 중');

    const stop = driving();
    stop.state.drive.mode = 'SAFE_STOP';
    stop.state.drive.active_vm_id = null;
    vi.mocked(useDisplay).mockReturnValue({ presentation: stop, connected: true, error: null });
    rerender(<CombinedPage />);
    expect(step('manual')).toHaveAttribute('data-state', 'done');
    expect(step('failover')).toHaveTextContent('안전 정지');

    const vm2 = driving();
    vm2.state.drive.active_vm_id = vm2.state.vms[1]!.vm_id;
    vi.mocked(useDisplay).mockReturnValue({ presentation: vm2, connected: true, error: null });
    rerender(<CombinedPage />);
    expect(step('failover')).toHaveAttribute('data-state', 'done');
    expect(step('park')).toHaveAttribute('data-state', 'active');
    expect(screen.getByTestId('active-vm')).toHaveTextContent('VM2 운전 중');
  });

  it('코스 위치를 못 받으면 지도에 차를 그리지 않고 위치 수신 대기로 둔다 (실보드 수집기는 아직 안 보냄)', () => {
    const p = driving();
    delete (p.state as unknown as { course?: unknown }).course;
    show(p);
    expect(screen.queryByTestId('nav-car')).toBeNull();
    expect(screen.getByTestId('nav-banner')).toHaveTextContent('위치 수신 대기');
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
  it('최대 속도·트립·코스 위치를 읽고, 신선하지 않으면 버린다', () => {
    const p = driving();
    expect(maxSpeedOf(p)).toBe(0.5);
    expect(tripOf(p)).toEqual({ distance_m: 3.25, driving_s: 75 });
    expect(courseOf(p)).toMatchObject({ trackId: 'jetson-demo', pose: { x: 2.81, y: 0.7, yaw: Math.PI } });
    expect(courseOf(p)!.obstacles.map((o) => o.id)).toEqual(['A', 'B']);
    (p.state as unknown as { course: { source: unknown } }).course.source = { status: 'stale', age_ms: 900 };
    expect(courseOf(p)).toBeNull();
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
