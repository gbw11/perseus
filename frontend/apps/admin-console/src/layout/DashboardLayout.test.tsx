import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';
import { DashboardLayout } from './DashboardLayout';

function renderDashboard(path = '/dashboard/overview') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/dashboard" element={<DashboardLayout />}>
          <Route path="overview" element={<p>overview content</p>} />
          <Route path="cpu" element={<p>cpu content</p>} />
          <Route path="memory" element={<p>memory content</p>} />
          <Route path="console" element={<p>console content</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('DashboardLayout', () => {
  it('대시보드 4개 탭과 현재 탭을 표시한다', () => {
    renderDashboard('/dashboard/memory');

    expect(screen.getByRole('navigation', { name: 'VM Dashboard 메뉴' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Overview' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'CPU' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Memory' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Console' })).toBeInTheDocument();
    expect(screen.getByText('memory content')).toBeInTheDocument();
  });

  it('탭을 누르면 해당 하위 화면으로 이동한다', async () => {
    const user = userEvent.setup();
    renderDashboard();

    await user.click(screen.getByRole('link', { name: 'CPU' }));

    expect(screen.getByText('cpu content')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'CPU' })).toHaveAttribute('aria-current', 'page');
  });
});
