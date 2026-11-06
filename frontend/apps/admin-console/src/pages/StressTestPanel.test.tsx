import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { StressRequest, Vm } from '../api/types';
import { StressTestPanel } from './StressTestPanel';

const state = vi.hoisted(() => ({
  mutateAsync: vi.fn<(input: StressRequest) => Promise<unknown>>(),
}));

vi.mock('../api/queries', () => ({
  useStartStress: () => ({ mutateAsync: state.mutateAsync, isPending: false }),
}));
vi.mock('@xenova/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@xenova/ui')>()),
  useToast: () => ({ show: vi.fn() }),
}));

const vm: Vm = {
  vm_id: 'vm-linux',
  name: 'linux-a',
  guest_kind: 'linux',
  state: 'running',
  agent_status: 'ONLINE',
  ipv4: null,
  template_id: 'ubuntu',
  template_version: '1',
  vcpus: 2,
  memory_mib: 1024,
};

const command = (type: 'CPU' | 'MEMORY', amount: number, duration_s: number) => ({
  command_id: 'c',
  operation_id: 'o',
  vm_id: vm.vm_id,
  type,
  amount,
  duration_s,
  submitted_at: '2026-11-06T00:00:00Z',
  expires_at: '2026-11-06T00:00:30Z',
});

describe('VM 상세 부하 테스트 카드', () => {
  beforeEach(() => {
    state.mutateAsync.mockReset();
  });

  it('CPU 워커 수와 시간을 골라 cpu 부하 요청을 보낸다', async () => {
    state.mutateAsync.mockResolvedValue(command('CPU', 3, 45));
    render(<StressTestPanel vm={vm} accent="red" />);
    await userEvent.click(screen.getByRole('button', { name: /3\s*워커/ }));
    const duration = screen.getByLabelText('CPU 부하 시간(초)');
    await userEvent.clear(duration);
    await userEvent.type(duration, '45');
    await userEvent.click(screen.getByRole('button', { name: 'CPU 부하 시작' }));
    expect(state.mutateAsync).toHaveBeenCalledWith({ kind: 'cpu', body: { workers: 3, duration_s: 45 } });
    expect(await screen.findByRole('status')).toHaveTextContent('CPU 워커 3개 · 45초');
  });

  it('메모리 크기를 넣어 memory 부하 요청을 보낸다', async () => {
    state.mutateAsync.mockResolvedValue(command('MEMORY', 128, 30));
    render(<StressTestPanel vm={vm} accent="red" />);
    const size = screen.getByLabelText('메모리 부하 크기(MiB)');
    await userEvent.clear(size);
    await userEvent.type(size, '128');
    await userEvent.click(screen.getByRole('button', { name: '메모리 부하 시작' }));
    expect(state.mutateAsync).toHaveBeenCalledWith({
      kind: 'memory',
      body: { memory_mib: 128, duration_s: 30 },
    });
  });

  it('범위를 넘는 값이면 시작 버튼을 막는다', async () => {
    render(<StressTestPanel vm={vm} accent="red" />);
    const size = screen.getByLabelText('메모리 부하 크기(MiB)');
    await userEvent.clear(size);
    await userEvent.type(size, '513');
    expect(screen.getByRole('button', { name: '메모리 부하 시작' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('허용 범위');
  });

  it('Agent 가 ONLINE 이 아니면 모든 시작 버튼을 막는다', () => {
    render(<StressTestPanel vm={{ ...vm, agent_status: 'STALE' }} accent="red" />);
    expect(screen.getByText('Agent 연결 필요')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'CPU 부하 시작' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '메모리 부하 시작' })).toBeDisabled();
  });

  it('AGENT_UNAVAILABLE 응답을 안내 문구로 보여 준다', async () => {
    state.mutateAsync.mockRejectedValue(new ApiError(409, { code: 'AGENT_UNAVAILABLE', message: 'x' }));
    render(<StressTestPanel vm={vm} accent="red" />);
    await userEvent.click(screen.getByRole('button', { name: 'CPU 부하 시작' }));
    expect(await screen.findByRole('status')).toHaveTextContent('VM Agent가 온라인이 아니라');
  });
});
