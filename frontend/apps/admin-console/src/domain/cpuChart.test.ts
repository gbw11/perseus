import { describe, expect, it } from 'vitest';
import { buildCpuChartScale, cpuChartY, formatCpuTick } from './cpuChart';

describe('CPU chart scale', () => {
  it('uses the 10 percent minimum so low activity remains visible', () => {
    const scale = buildCpuChartScale([2, 2.5, 3]);

    expect(scale).toEqual({ max: 10, ticks: [10, 7.5, 5, 2.5, 0] });
    expect(cpuChartY(3, scale.max)).toBe(70);
  });

  it('adds headroom and rounds up to a stable ten-point bucket', () => {
    expect(buildCpuChartScale([12]).max).toBe(20);
    expect(buildCpuChartScale([13]).max).toBe(20);
    expect(buildCpuChartScale([21]).max).toBe(40);
  });

  it('caps the percentage axis at 100', () => {
    expect(buildCpuChartScale([70]).max).toBe(100);
    expect(buildCpuChartScale([125]).max).toBe(100);
    expect(cpuChartY(125, 100)).toBe(0);
  });

  it('ignores non-finite samples and formats fractional ticks', () => {
    expect(buildCpuChartScale([Number.NaN, Number.POSITIVE_INFINITY]).max).toBe(10);
    expect(formatCpuTick(7.5)).toBe('7.5');
    expect(formatCpuTick(20)).toBe('20');
  });
});
