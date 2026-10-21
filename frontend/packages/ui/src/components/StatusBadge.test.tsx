import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HEALTH_LABEL, StatusBadge, type Health } from './StatusBadge';

describe('StatusBadge', () => {
  it.each(Object.keys(HEALTH_LABEL) as Health[])('%s 상태를 시나리오 라벨로 보여준다', (health) => {
    render(<StatusBadge health={health} />);
    expect(screen.getByRole('status')).toHaveTextContent(HEALTH_LABEL[health]);
  });

  it('라벨을 바꿀 수 있다', () => {
    render(<StatusBadge health="recovering" label="복구 중 12초" />);
    expect(screen.getByRole('status')).toHaveTextContent('복구 중 12초');
  });
});
