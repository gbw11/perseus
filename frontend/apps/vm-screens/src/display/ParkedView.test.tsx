import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEPART_MS, departPose, ParkedView, type ParkedCallout } from './ParkedView';

const vms = (vm2Tone: ParkedCallout['tone'] = 'ok'): ParkedCallout[] => [
  { key: 'VM1', title: 'VM1 · 주 구동', label: '주행 계산 중', tone: 'drive', anchor: 'hood' },
  { key: 'VM2', title: 'VM2 · 백업', label: '대기 · 준비 완료', tone: vm2Tone, anchor: 'hood' },
  { key: 'VM3', title: 'VM3 · 인터랙션', label: '인포테인먼트', tone: 'ok', anchor: 'center' },
];

function show(callouts: ParkedCallout[], departing = false) {
  return render(
    <ParkedView runtimeS={3540} callouts={callouts} connected time="오전 9:00" departing={departing} />,
  );
}

/** VM 연결선이 차에 닿는 점 (경로 끝) */
function linkEnd(container: HTMLElement, i: number) {
  const d = container.querySelectorAll('g[data-ready] path')[i]!.getAttribute('d')!;
  const nums = d.match(/-?\d+(\.\d+)?/g)!.map(Number);
  return { x: nums[nums.length - 2]!, y: nums[nums.length - 1]! };
}

describe('정차 화면 VM 카드', () => {
  afterEach(() => vi.useRealTimers());

  it('세 VM 이 모두 연결·준비되면 카드에 크게 표시하고 화면을 덮어 "출발 준비 완료"', () => {
    show(vms());
    expect(screen.getByTestId('ready-overlay')).toHaveTextContent('출발 준비 완료');
    expect(screen.getByTestId('parked-status')).toHaveTextContent('VM 3대 모두 연결됨');
    // 배터리 % 대신 예상 주행 가능 시간 (잔량 센서 없음)
    expect(screen.getByTestId('parked-runtime')).toHaveTextContent('약 59분');
    for (const key of ['VM1', 'VM2', 'VM3']) {
      expect(screen.getByTestId(`callout-${key}`)).toHaveTextContent('연결됨 · 준비 완료');
    }
  });

  it('하나라도 준비되지 않으면 몇 대 연결됐는지 보여 주고 그 카드는 연결 대기', () => {
    show(vms('warning'));
    expect(screen.getByTestId('parked-status')).toHaveTextContent('구동 준비 중 · 2/3 연결');
    expect(screen.queryByTestId('ready-overlay')).toBeNull();
    expect(screen.getByTestId('callout-VM2')).toHaveTextContent('연결 대기');
  });

  it('주행 VM 선은 본닛(앞, 화면 왼쪽 아래)에, 인포테인먼트 선은 차 가운데에 닿는다', () => {
    const { container } = show(vms());
    const [vm1, vm2, vm3] = [0, 1, 2].map((i) => linkEnd(container, i));
    expect(vm1!.x).toBeLessThan(vm3!.x);
    expect(vm2!.x).toBeLessThan(vm3!.x);
    expect(Math.abs(vm1!.x - vm2!.x)).toBeLessThan(120);
  });

  it('출발하면 덮개가 걷히고, 차가 바퀴를 굴리며 시계 방향으로 돌아 12시를 향한 뒤 멀어진다', () => {
    vi.useFakeTimers({ toFake: ['performance', 'requestAnimationFrame', 'cancelAnimationFrame'] });
    show(vms(), true);
    expect(screen.getByTestId('parked-status')).toHaveTextContent('출발합니다');
    expect(screen.getByTestId('ready-overlay')).toHaveAttribute('data-leaving', 'true');
    const car = () => screen.getByTestId('parked-car');
    const yaw0 = Number(car().getAttribute('data-yaw'));
    const wheel0 = screen.getByTestId('wheels').getAttribute('data-angle');
    act(() => vi.advanceTimersByTime(700));
    // 처음 125° (앞이 왼쪽 아래) 에서 줄어든다 = 시계 방향
    expect(Number(car().getAttribute('data-yaw'))).toBeLessThan(yaw0);
    expect(screen.getByTestId('wheels').getAttribute('data-angle')).not.toBe(wheel0);
    act(() => vi.advanceTimersByTime(1000));
    expect(Number(car().getAttribute('data-yaw'))).toBe(0);
  });

  it('출발 자세: 12시를 향한 뒤 점점 빨리 멀어지고 끝에서 흐려진다', () => {
    const turned = departPose(1400);
    const later = departPose(2600);
    expect(turned.pose.yaw).toBeCloseTo(0);
    expect(later.scale).toBeLessThan(turned.scale * 0.5);
    expect(departPose(DEPART_MS).scale).toBeLessThan(0.1);
    expect(departPose(DEPART_MS).opacity).toBeCloseTo(0);
  });
});
