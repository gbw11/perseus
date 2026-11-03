import { fireEvent, render, screen, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { VmCreatePage } from './VmCreatePage';
import { VM_CREATION_LEAVE_MESSAGE } from './vmCreationProgress';

const createMutation = vi.hoisted(() => ({
  data: undefined,
  error: null,
  isPending: true,
  mutateAsync: vi.fn(),
  reset: vi.fn(),
}));

vi.mock('../api/queries', () => ({
  useCreateVm: () => createMutation,
  useTemplates: () => ({ data: [], error: null, isPending: false }),
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});

describe('VM 생성 화면 이탈 안내', () => {
  it('생성 중 화면 이동을 막고 실제 동작을 안내한 뒤 사용자가 선택하면 이동한다', async () => {
    const router = createMemoryRouter(
      [
        { path: '/control/vms/new', element: <VmCreatePage /> },
        { path: '/control', element: <div>VM 목록 화면</div> },
      ],
      { initialEntries: ['/control/vms/new'] },
    );
    render(<RouterProvider router={router} />);

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
