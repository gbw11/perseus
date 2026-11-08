import { describe, expect, it } from 'vitest';
import {
  bytesParts,
  formatBytes,
  latestValue,
  nearestPointIndex,
  pointXPercent,
  tooltipSide,
  tooltipTime,
} from './resourceFormat';

describe('바이트 표시', () => {
  it('B·KiB·MiB·GiB·TiB 로 단위를 올린다', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1.5 KiB');
    expect(formatBytes(48 * 1024 ** 2)).toBe('48.0 MiB');
    expect(formatBytes(3 * 1024 ** 3)).toBe('3.00 GiB');
    expect(formatBytes(2.5 * 1024 ** 4)).toBe('2.50 TiB');
    // 2^53 바이트(8 PiB)도 GiB 에 머물지 않고 TiB 로 보인다
    expect(formatBytes(2 ** 53)).toBe('8192.00 TiB');
  });
});

describe('그래프 범례·툴팁 값', () => {
  const at = (seconds: number) => new Date(Date.UTC(2026, 10, 6, 1, 0, seconds)).toISOString();
  const points = [
    { observedAt: at(0), rx: 10, tx: 1 },
    { observedAt: at(10), rx: 20, tx: null },
    { observedAt: at(40), rx: null, tx: null },
  ];

  it('범례는 선마다 비어 있지 않은 가장 최근 값을 쓴다', () => {
    expect(latestValue(points, 'rx')).toBe(20);
    expect(latestValue(points, 'tx')).toBe(1);
    expect(latestValue([], 'rx')).toBeNull();
  });

  it('가로 위치에 가장 가까운 표본을 고르고, 표본의 가로 위치는 시간 비례다', () => {
    expect(nearestPointIndex(points, 0)).toBe(0);
    expect(nearestPointIndex(points, 0.2)).toBe(1);
    expect(nearestPointIndex(points, 0.9)).toBe(2);
    expect(nearestPointIndex(points, 2)).toBe(2);
    expect(nearestPointIndex([], 0.5)).toBeNull();
    expect(pointXPercent(points, 1)).toBe(25);
    expect(pointXPercent(points.slice(0, 1), 0)).toBe(100);
  });

  it('툴팁은 오른쪽 끝 근처에서 왼쪽으로 펼치고, 시각은 상대 시간과 시:분:초로 쓴다', () => {
    expect(tooltipSide(30)).toBe('right');
    expect(tooltipSide(80)).toBe('left');
    const observed = new Date(2026, 10, 6, 13, 4, 5).toISOString();
    expect(tooltipTime(observed, Date.parse(observed) + 12_000)).toBe('12초 전 · 13:04:05');
    expect(tooltipTime(observed, Date.parse(observed) + 75_000)).toBe('1분 15초 전 · 13:04:05');
  });
});

describe('숫자·단위 분리', () => {
  it('바이트 표시를 숫자와 단위로 나눈다', () => {
    expect(bytesParts(12_492.8)).toEqual({ number: '12.2', unit: 'KiB' });
    expect(bytesParts(512)).toEqual({ number: '512', unit: 'B' });
    expect(bytesParts(3 * 1024 ** 3)).toEqual({ number: '3.00', unit: 'GiB' });
  });
});
