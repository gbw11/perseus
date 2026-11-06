/** Memory·Network 탭이 함께 쓰는 표시 형식과 그래프 좌표 계산 */
import { bytesToKiB } from '../domain/memoryPools';
import { ago } from '../domain/roles';

export interface ChartPoint {
  observedAt: string;
  rx: number | null;
  tx: number | null;
}

/** 표본 시각 기준 0~100 좌표. 값이 없는 점은 건너뛴다 */
export function chartPoints(points: readonly ChartPoint[], key: 'rx' | 'tx', max: number): string {
  if (!points.length) return '';
  const start = Date.parse(points[0]!.observedAt);
  const end = Date.parse(points.at(-1)!.observedAt);
  const span = Math.max(1, end - start);
  return points
    .map((point) => {
      const value = point[key];
      if (value == null) return null;
      const x = ((Date.parse(point.observedAt) - start) / span) * 100;
      const y = 100 - (Math.max(0, Math.min(max, value)) / max) * 100;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .filter((point): point is string => point != null)
    .join(' ');
}

export function latestTime(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) >= Date.parse(b) ? a : b;
}

export function formatBytes(value: number): string {
  if (value >= 1024 ** 4) return `${(value / 1024 ** 4).toFixed(2)} TiB`;
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  if (value >= 1024 * 1024) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${Math.round(value)} B`;
}

/** formatBytes 를 숫자와 단위로 나눈다 ("12.2 KiB" → { number: "12.2", unit: "KiB" }) */
export function bytesParts(value: number): { number: string; unit: string } {
  const [number = '', unit = ''] = formatBytes(value).split(' ');
  return { number, unit };
}

export function formatKiB(value: number | null): string {
  if (value == null) return '—';
  return `${bytesToKiB(value).toLocaleString('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: value ? 2 : 0 })} KiB`;
}

export function formatMiB(value: number): string {
  return `${(value / (1024 * 1024)).toLocaleString('ko-KR', { maximumFractionDigits: 1 })} MiB`;
}

export function relativeTime(value: string | null): string {
  if (!value) return '—';
  const elapsed = Math.max(0, Date.now() - Date.parse(value));
  return ago(elapsed);
}

/** 그 선의 가장 최근 값. 끝쪽 표본이 비어 있으면 그 앞의 마지막 값을 쓴다 */
export function latestValue(points: readonly ChartPoint[], key: 'rx' | 'tx'): number | null {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const value = points[index]![key];
    if (value != null) return value;
  }
  return null;
}

/** 그래프 가로 위치(0~1)에 가장 가까운 표본 index. chartPoints 와 같은 시간축을 쓴다 */
export function nearestPointIndex(points: readonly ChartPoint[], fraction: number): number | null {
  if (!points.length) return null;
  const start = Date.parse(points[0]!.observedAt);
  const end = Date.parse(points.at(-1)!.observedAt);
  const target = start + Math.max(0, Math.min(1, fraction)) * Math.max(0, end - start);
  let best = 0;
  for (let index = 1; index < points.length; index += 1) {
    const distance = Math.abs(Date.parse(points[index]!.observedAt) - target);
    if (distance < Math.abs(Date.parse(points[best]!.observedAt) - target)) best = index;
  }
  return best;
}

/** 표본의 가로 위치(%). chartPoints 와 같은 계산 */
export function pointXPercent(points: readonly ChartPoint[], index: number): number {
  if (points.length <= 1) return 100;
  const start = Date.parse(points[0]!.observedAt);
  const span = Math.max(1, Date.parse(points.at(-1)!.observedAt) - start);
  return ((Date.parse(points[index]!.observedAt) - start) / span) * 100;
}

/**
 * 툴팁을 기준선의 어느 쪽에 붙일지. 오른쪽 끝 근처면 왼쪽으로, 왼쪽 끝 근처면 오른쪽으로 펼쳐
 * 그래프 밖으로 잘리지 않게 한다.
 */
export function tooltipSide(xPercent: number): 'left' | 'right' {
  return xPercent > 55 ? 'left' : 'right';
}

/** 툴팁 시각: "12초 전 · 13:04:05" */
export function tooltipTime(observedAt: string, now = Date.now()): string {
  const at = new Date(observedAt);
  const clock = [at.getHours(), at.getMinutes(), at.getSeconds()]
    .map((part) => String(part).padStart(2, '0'))
    .join(':');
  const seconds = Math.max(0, Math.round((now - at.getTime()) / 1000));
  const relative = seconds < 60 ? `${seconds}초 전` : `${Math.floor(seconds / 60)}분 ${seconds % 60}초 전`;
  return `${relative} · ${clock}`;
}
