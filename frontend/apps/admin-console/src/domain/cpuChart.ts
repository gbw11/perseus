const CPU_SCALE_MIN = 10;
const CPU_SCALE_MAX = 100;
const CPU_SCALE_STEP = 10;
const CPU_SCALE_HEADROOM = 1.5;

export interface CpuChartScale {
  max: number;
  ticks: number[];
}

/**
 * Keeps low CPU activity readable while preserving percentage semantics.
 * Ten-point buckets prevent the axis from changing for every small sample fluctuation.
 */
export function buildCpuChartScale(values: readonly number[]): CpuChartScale {
  const peak = Math.max(0, ...values.filter((value) => Number.isFinite(value)));
  const target = Math.min(CPU_SCALE_MAX, Math.max(CPU_SCALE_MIN, peak * CPU_SCALE_HEADROOM));
  const max = Math.min(CPU_SCALE_MAX, Math.ceil(target / CPU_SCALE_STEP) * CPU_SCALE_STEP);

  return {
    max,
    ticks: [max, max * 0.75, max * 0.5, max * 0.25, 0],
  };
}

export function cpuChartY(value: number, scaleMax: number): number {
  const safeMax = Math.max(CPU_SCALE_MIN, scaleMax);
  const bounded = Math.max(0, Math.min(safeMax, value));
  return 100 - (bounded / safeMax) * 100;
}

export function formatCpuTick(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
