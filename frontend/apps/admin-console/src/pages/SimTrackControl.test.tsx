import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SimView } from '../api/sim';
import { initialSim, snapshotOf } from '../mocks/sim';
import { SimTrackControl } from './SimTrackControl';

const state = vi.hoisted(() => ({ view: null as unknown as SimView }));

vi.mock('../api/sim', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/sim')>()),
  useSimState: () => state.view,
}));
vi.mock('../layout/DriveContext', () => ({ useDrive: () => ({ drive: null }) }));
vi.mock('@xenova/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@xenova/ui')>()),
  useToast: () => ({ show: vi.fn() }),
  TrackView: () => <div data-testid="track" />,
}));

const snap = snapshotOf(initialSim(0));
const live: SimView = { available: true, pose: snap.pose ?? null, world: snap.world ?? null, trail: [] };

describe('시뮬레이터 탭 트랙 조작 카드 — 시뮬레이터 없음 (S15P31E110-262)', () => {
  beforeEach(() => {
    state.view = live;
  });

  it('위치를 받는 동안은 조작 버튼이 켜져 있고 없음 알림이 없다', () => {
    render(<SimTrackControl />);
    expect(screen.queryByText(/위치를 받지 못했다/)).toBeNull();
    expect(screen.getByRole('button', { name: /다시 출발/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /E-stop/ })).toBeEnabled();
    expect(screen.getByLabelText('트랙 고르기')).toBeEnabled();
  });

  it('끊기면(available=false) 카드를 지우지 않고 "시뮬레이터 없음" 을 띄우고 조작을 모두 막는다', () => {
    state.view = { ...live, available: false };
    render(<SimTrackControl />);
    expect(screen.getByText(/시뮬레이터 없음 — 1초 넘게 위치를 받지 못했다/)).toBeInTheDocument();
    for (const name of [/다시 출발/, /E-stop/, /왼쪽으로 밀기/, /오른쪽으로 밀기/, /무작위/])
      expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(screen.getByLabelText('트랙 고르기')).toBeDisabled();
    expect(screen.getByLabelText('주행 흐름')).toHaveTextContent('시뮬레이터 없음');
  });

  it('트랙을 한 번도 받지 못했어도 카드는 남고 버튼은 꺼져 있다', () => {
    state.view = { available: false, pose: null, world: null, trail: [] };
    render(<SimTrackControl />);
    expect(screen.getByText(/시뮬레이터 없음 — 1초 넘게 위치를 받지 못했다/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /다시 출발/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /E-stop/ })).toBeDisabled();
  });
});
