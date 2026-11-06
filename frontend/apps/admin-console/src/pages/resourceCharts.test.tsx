import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NetTable, RxTxChart } from './resourceCharts';
import { formatBytes } from './resourceFormat';

const at = (seconds: number) => new Date(Date.UTC(2026, 10, 6, 1, 0, seconds)).toISOString();
const points = [
  { observedAt: at(0), rx: 1024, tx: 512 },
  { observedAt: at(10), rx: 2048, tx: null },
  { observedAt: at(20), rx: 12_492.8, tx: 4505.6 },
];
const formatRate = (value: number) => `${formatBytes(value)}/s`;

function renderChart() {
  render(
    <RxTxChart
      title="송수신 속도"
      unit="B/s"
      ariaLabel="속도 그래프"
      points={points}
      formatValue={formatRate}
    />,
  );
  const plot = screen.getByRole('img', { name: '속도 그래프' }).parentElement!;
  plot.getBoundingClientRect = () =>
    ({ left: 100, width: 200, top: 0, height: 150, right: 300, bottom: 150, x: 100, y: 0 }) as DOMRect;
  return plot;
}

describe('RX/TX 그래프 범례·툴팁', () => {
  it('범례에 각 선의 가장 최근 값을 단위와 함께 보여 준다', () => {
    renderChart();
    const legend = screen.getByText('송수신 속도').nextElementSibling as HTMLElement;
    expect(legend).toHaveTextContent('RX 12.2 KiB/s');
    expect(legend).toHaveTextContent('TX 4.4 KiB/s');
  });

  it('그래프 위에 올리면 가장 가까운 표본의 값을, 벗어나면 툴팁을 숨긴다', () => {
    const plot = renderChart();

    // 가로 45% → 10초 표본이 가장 가깝다. TX 는 값이 없어 "—"
    fireEvent.mouseMove(plot, { clientX: 190 });
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveTextContent('RX 2.0 KiB/s');
    expect(tooltip).toHaveTextContent('TX —');
    expect(tooltip).toHaveAttribute('data-side', 'right');

    // 오른쪽 끝에서는 왼쪽으로 펼친다
    fireEvent.mouseMove(plot, { clientX: 299 });
    expect(screen.getByRole('tooltip')).toHaveAttribute('data-side', 'left');
    expect(screen.getByRole('tooltip')).toHaveTextContent('RX 12.2 KiB/s');

    fireEvent.mouseLeave(plot);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    // 범례는 계속 최신 값
    expect(screen.getByText('송수신 속도').nextElementSibling).toHaveTextContent('RX 12.2 KiB/s');
  });

  it('값이 없으면 범례에 "—" 를 보여 준다', () => {
    render(
      <RxTxChart
        title="Pool 점유율"
        unit="%"
        ariaLabel="점유율"
        max={100}
        points={[{ observedAt: at(0), rx: null, tx: 12.5 }]}
        formatValue={(value) => `${value.toFixed(1)}%`}
      />,
    );
    const legend = screen.getByText('Pool 점유율').nextElementSibling as HTMLElement;
    expect(legend).toHaveTextContent('RX —');
    expect(legend).toHaveTextContent('TX 12.5%');
  });
});

describe('RX/TX 표 열 배치', () => {
  it('행 이름 | 간격 | RX | TX 순서이고, 간격 열은 보조기기에 읽히지 않는다', () => {
    render(
      <NetTable
        label="수치"
        rows={[
          { label: '속도', rx: { number: '12.2', unit: 'KiB/s' }, tx: { number: '4.4', unit: 'KiB/s' } },
          {
            label: 'Pool',
            rx: { number: '87.5', unit: '%', note: '7.00 / 8.00 KiB', level: 'warn' },
            tx: { number: null, note: '데이터 없음' },
          },
        ]}
      />,
    );
    const table = screen.getByRole('table', { name: '수치' });
    for (const row of table.querySelectorAll('tr')) {
      expect(row.children).toHaveLength(4);
      expect(row.children[1]).toHaveAttribute('aria-hidden', 'true');
    }
    // 열 너비는 colgroup(행 이름 | 간격 | RX | TX)으로 고정해 값이 바뀌어도 열 시작 위치가 그대로다
    expect(table.querySelectorAll('col')).toHaveLength(4);
    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'RX 수신',
      'TX 송신',
    ]);
    expect(screen.getByText('87.5').closest('td')).toHaveTextContent('87.5%7.00 / 8.00 KiB');
    expect(screen.getByText('—').closest('td')).toHaveTextContent('—데이터 없음');
    // 보조 줄은 열 너비 안에서 말줄임될 수 있어 전체 문구를 title 로 둔다
    expect(screen.getByText('데이터 없음')).toHaveAttribute('title', '데이터 없음');
  });
});
