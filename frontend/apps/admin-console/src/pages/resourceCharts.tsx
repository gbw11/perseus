/** Memory·Network 탭 공통 블록: 텍스트 수치 격자, RX/TX 2선 그래프, 누적·속도 타일 */
import type { DiskIoCounter } from '../domain/vmMetrics';
import styles from './pages.module.css';
import { useState, type MouseEvent } from 'react';
import {
  chartPoints,
  formatBytes,
  latestValue,
  nearestPointIndex,
  pointXPercent,
  tooltipSide,
  tooltipTime,
  type ChartPoint,
} from './resourceFormat';

/** RX/TX 표의 한 칸. 숫자는 크게, 단위는 작고 흐리게 */
export interface NetCell {
  /** 값이 없으면 null → "—" */
  number: string | null;
  unit?: string;
  /** 숫자 아래 한 줄 (값이 없으면 사유) */
  note?: string | null;
  /** Pool 경고 단계 */
  level?: 'ok' | 'warn' | 'fault';
}

export interface NetRow {
  label: string;
  rx: NetCell;
  tx: NetCell;
}

/**
 * 작업관리자처럼 행 이름(작은 회색) | RX 수신 | TX 송신 표. 값은 각 선과 같은 색.
 * 열: 행 이름(고정 폭) | 빈 간격(남는 너비) | RX(고정 폭) | TX(고정 폭). 열 너비는 CSS 간격 변수로만 정해져
 * 값과 무관하게 같은 너비의 카드에서는 RX·TX 열 시작 위치가 항상 같다. 제목·값·보조 줄은 열 안에서 모두 왼쪽 정렬.
 */
export function NetTable({ rows, label }: { rows: readonly NetRow[]; label: string }) {
  return (
    <div className={styles.netTableBlock}>
      <table className={styles.netTable} aria-label={label}>
        <colgroup>
          <col className={styles.netLabelCol} />
          <col />
          <col className={styles.netValueCol} />
          <col className={styles.netValueCol} />
        </colgroup>
        <thead>
          <tr>
            <td />
            <td className={styles.netGap} aria-hidden="true" />
            <th scope="col" data-tone="rx">
              RX 수신
            </th>
            <th scope="col" data-tone="tx">
              TX 송신
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td className={styles.netGap} aria-hidden="true" />
              <NetValue cell={row.rx} tone="rx" />
              <NetValue cell={row.tx} tone="tx" />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NetValue({ cell, tone }: { cell: NetCell; tone: 'rx' | 'tx' }) {
  return (
    <td data-tone={tone} data-level={cell.level && cell.level !== 'ok' ? cell.level : undefined}>
      <span className={styles.netNumber}>
        {cell.number ?? '—'}
        {cell.number != null && cell.unit && <small>{cell.unit}</small>}
      </span>
      {cell.note && (
        <small className={styles.netNote} title={cell.note}>
          {cell.note}
        </small>
      )}
    </td>
  );
}

/**
 * RX·TX 두 선 그래프. 범례에 각 선의 가장 최근 값을, 그래프 위에 올리면 가장 가까운 표본의 시각과 값을 보여 준다.
 * max 를 주지 않으면 값에 맞춰 위쪽 여유를 둔다.
 */
export function RxTxChart({
  title,
  unit,
  ariaLabel,
  points,
  max,
  span = '60초',
  formatMax,
  formatValue,
  rxOnly = false,
}: {
  title: string;
  unit: string;
  ariaLabel: string;
  points: readonly ChartPoint[];
  max?: number;
  /** 가로축 시작 라벨 ("60초", "5분") */
  span?: string;
  formatMax?: (value: number) => string;
  /** 범례·툴팁 값 표시. 없으면 "{정수} {unit}" */
  formatValue?: (value: number) => string;
  rxOnly?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const values = points
    .flatMap((point) => [point.rx, point.tx])
    .filter((value): value is number => value != null);
  const yMax = max ?? Math.max(10, Math.ceil((Math.max(0, ...values) * 1.15) / 10) * 10);
  const rxPoints = chartPoints(points, 'rx', yMax);
  const txPoints = rxOnly ? '' : chartPoints(points, 'tx', yMax);
  const format = (value: number | null) =>
    value == null ? '—' : formatValue ? formatValue(value) : `${value.toFixed(0)} ${unit}`;
  const hovered = hover == null ? null : points[hover];
  const hoverX = hover == null ? 0 : pointXPercent(points, hover);

  const track = (event: MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    setHover(nearestPointIndex(points, (event.clientX - rect.left) / rect.width));
  };

  return (
    <section className={styles.memoryChartBlock}>
      <header>
        <strong>{title}</strong>
        <span className={styles.memoryLegend} data-values="">
          <span data-tone="rx">
            <i />
            RX <b>{format(latestValue(points, 'rx'))}</b>
          </span>
          {!rxOnly && (
            <span data-tone="tx">
              <i />
              TX <b>{format(latestValue(points, 'tx'))}</b>
            </span>
          )}
        </span>
      </header>
      <div className={styles.memoryChart}>
        {rxPoints || txPoints ? (
          <div className={styles.chartPlot} onMouseMove={track} onMouseLeave={() => setHover(null)}>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={ariaLabel}>
              {[0, 25, 50, 75, 100].map((value) => (
                <line
                  key={value}
                  x1="0"
                  x2="100"
                  y1={100 - value}
                  y2={100 - value}
                  className={styles.memoryGridLine}
                />
              ))}
              {rxPoints && (
                <polyline
                  points={rxPoints}
                  className={styles.memoryRxLine}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              {txPoints && (
                <polyline
                  points={txPoints}
                  className={styles.memoryTxLine}
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </svg>
            {hovered && (
              <>
                <span className={styles.chartCursor} style={{ left: `${hoverX}%` }} aria-hidden="true" />
                <div
                  className={styles.chartTooltip}
                  data-side={tooltipSide(hoverX)}
                  style={{ left: `${hoverX}%` }}
                  role="tooltip"
                >
                  <time dateTime={hovered.observedAt}>{tooltipTime(hovered.observedAt)}</time>
                  <span data-tone="rx">
                    RX <b>{format(hovered.rx)}</b>
                  </span>
                  {!rxOnly && (
                    <span data-tone="tx">
                      TX <b>{format(hovered.tx)}</b>
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        ) : (
          <div className={styles.memoryChartEmpty}>데이터 없음</div>
        )}
        <span className={styles.memoryChartMax}>
          {formatMax ? formatMax(yMax) : `${yMax.toFixed(0)} ${unit}`}
        </span>
        <div className={styles.memoryChartAxis} aria-hidden="true">
          <span>{span} 전</span>
          <span>현재</span>
        </div>
      </div>
    </section>
  );
}

/** 누적 바이트와 현재 속도 타일 (디스크 읽기·쓰기, 네트워크 수신·송신) */
export function ByteCounterItem({
  label,
  counter,
  waiting = '속도 계산 대기',
}: {
  label: string;
  counter: DiskIoCounter;
  waiting?: string;
}) {
  return (
    <div className={styles.counterItem}>
      <span>{label} 누적</span>
      <strong>{counter.totalBytes == null ? '—' : formatBytes(counter.totalBytes)}</strong>
      <small>
        {counter.totalBytes == null
          ? (counter.reason ?? '데이터 없음')
          : counter.bytesPerSecond == null
            ? waiting
            : `${formatBytes(counter.bytesPerSecond)}/s`}
      </small>
    </div>
  );
}
