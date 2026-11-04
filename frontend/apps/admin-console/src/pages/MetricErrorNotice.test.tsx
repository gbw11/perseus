import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MetricErrorNotice } from './MetricErrorNotice';

describe('메트릭 오류 안내', () => {
  it('오류와 마지막 정상 데이터 유지 안내를 줄을 나눠 표시한다', () => {
    render(<MetricErrorNotice />);

    const notice = screen.getByRole('alert');
    expect(notice).toHaveTextContent('메트릭 연결 오류마지막 정상 데이터가 있으면 그대로 표시합니다.');
    expect(notice.querySelectorAll('br')).toHaveLength(1);
    expect(notice.innerHTML).toBe('메트릭 연결 오류<br>마지막 정상 데이터가 있으면 그대로 표시합니다.');
  });
});
