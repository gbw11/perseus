import { fireEvent, render, screen, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { Template, Vm } from '../api/types';
import { saveVmList } from '../api/vmListCache';
import { VmCreatePage } from './VmCreatePage';
import { finishVmCreation, VM_CREATION_LEAVE_MESSAGE } from './vmCreationProgress';

const PENDING_KEY = 'xenova.vm-create.pending';
const LINUX_TEMPLATE: Template = {
  template_id: 'linux-base',
  version: '1.0.0',
  display_name: 'Linux Base',
  min_memory_mib: 256,
  guest_kind: 'linux',
};

const createMutation = vi.hoisted(() => ({
  data: undefined as unknown,
  error: null as unknown,
  isPending: false,
  mutateAsync: vi.fn(),
  reset: vi.fn(),
}));

vi.mock('../api/queries', () => ({
  useCreateVm: () => createMutation,
  useTemplates: () => ({ data: [LINUX_TEMPLATE], error: null, isPending: false }),
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  createMutation.data = undefined;
  createMutation.error = null;
  createMutation.isPending = false;
  createMutation.mutateAsync.mockReset();
});

afterEach(() => {
  finishVmCreation('drive-vm-a');
});

function renderCreatePage() {
  const router = createMemoryRouter(
    [
      { path: '/control/vms/new', element: <VmCreatePage /> },
      { path: '/control', element: <div>VM 목록 화면</div> },
    ],
    { initialEntries: ['/control/vms/new'] },
  );
  return render(<RouterProvider router={router} />);
}

/** 템플릿 선택 → 이름 입력 → 확인 단계까지 이동 */
function goToReviewStep(name = 'drive-vm-a') {
  fireEvent.click(screen.getByRole('button', { name: /Linux Base/ }));
  fireEvent.click(screen.getByRole('button', { name: '다음' }));
  fireEvent.change(screen.getByLabelText(/VM 이름/), { target: { value: name } });
  fireEvent.click(screen.getByRole('button', { name: '다음' }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function pendingNames(): string[] {
  const parsed = JSON.parse(localStorage.getItem(PENDING_KEY) ?? '[]') as { name: string }[];
  return parsed.map((creation) => creation.name);
}

function pendingExcluded(): string[] | undefined {
  const parsed = JSON.parse(localStorage.getItem(PENDING_KEY) ?? '[]') as { excludedVmIds?: string[] }[];
  return parsed[0]?.excludedVmIds;
}

describe('VM 생성 화면 이탈 안내', () => {
  it('생성 중 화면 이동을 막고 실제 동작을 안내한 뒤 사용자가 선택하면 이동한다', async () => {
    createMutation.isPending = true;
    renderCreatePage();

    fireEvent.click(screen.getByRole('link', { name: /VM 목록/ }));

    const dialog = await screen.findByRole('dialog', { name: 'VM 생성이 진행 중입니다' });
    expect(within(dialog).getByText(VM_CREATION_LEAVE_MESSAGE)).toBeInTheDocument();
    expect(
      within(dialog).getByText('이 화면을 벗어나도 서버에 전달된 생성 요청은 취소되지 않습니다.'),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '이동' }));
    expect(await screen.findByText('VM 목록 화면')).toBeInTheDocument();
  });
});

describe('VM 생성 중 기록 정리', () => {
  it('생성 화면을 벗어난 뒤 생성이 끝나도 생성 중 기록을 지운다', async () => {
    const request = deferred<Vm>();
    createMutation.mutateAsync.mockReturnValue(request.promise);
    const view = renderCreatePage();
    goToReviewStep();

    fireEvent.click(screen.getByRole('button', { name: 'VM 생성' }));
    expect(pendingNames()).toEqual(['drive-vm-a']);

    view.unmount();
    request.resolve({ vm_id: 'vm-new', name: 'drive-vm-a', state: 'stopped' } as Vm);
    await request.promise;
    await Promise.resolve();

    expect(pendingNames()).toEqual([]);
  });

  it('같은 이름의 기존 VM은 생성 기록에서 제외하고, 409로 실패하면 기록을 지운다', async () => {
    saveVmList({
      items: [{ vm_id: 'vm-existing', name: 'drive-vm-a', state: 'stopped' } as Vm],
      observed_at: new Date().toISOString(),
    });
    const request = deferred<Vm>();
    createMutation.mutateAsync.mockReturnValue(request.promise);
    renderCreatePage();
    goToReviewStep();

    fireEvent.click(screen.getByRole('button', { name: 'VM 생성' }));
    expect(pendingExcluded()).toEqual(['vm-existing']);

    request.reject(new ApiError(409, { code: 'VM_NAME_CONFLICT', message: '이미 사용 중인 VM 이름입니다.' }));
    await request.promise.catch(() => undefined);
    await Promise.resolve();

    expect(pendingNames()).toEqual([]);
  });

  it('504 타임아웃이면 실패 대신 진행 중일 수 있음과 재시도 방법을 안내한다', () => {
    createMutation.error = new ApiError(504, {
      code: 'VM_CONTROL_TIMEOUT',
      message: 'VM 제어 서비스의 응답 시간이 초과되었습니다.',
    });
    renderCreatePage();
    goToReviewStep();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const notice = screen.getByRole('status');
    expect(within(notice).getByText('생성 요청의 응답 시간이 초과되었습니다')).toBeInTheDocument();
    expect(within(notice).getByText(/VM 생성이 계속 진행 중일 수 있습니다/)).toBeInTheDocument();
    expect(
      within(notice).getByText(/같은 이름·사양으로 다시 생성하면 이어서 복구됩니다/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '같은 사양으로 다시 생성' })).toBeEnabled();
  });
});
