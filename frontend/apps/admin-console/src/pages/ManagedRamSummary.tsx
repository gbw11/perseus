import type { MetricSample } from '../api/types';
import { buildManagedRamSeries, managedRamReason } from '../domain/managedRam';
import { MemoryPercentChart } from './AppMemoryPools';
import styles from './pages.module.css';
import { useNow } from '../useNow';

/** Linux와 같은 요약 형태로 최근 Zephyr 관리 RAM 관측과 그 시각을 표시한다. */
export function ManagedRamSummary({
  vmId,
  vmName,
  samples,
}: {
  vmId: string;
  vmName: string;
  samples: MetricSample[];
}) {
  // 화면 전체가 같이 쓰는 1초 시계 — 같은 카드 안의 "n초 전"이 서로 다른 시계로 계산되지 않게
  const now = useNow();
  const { current, recentObservation, points, lastObservationAt, timeBasis } = buildManagedRamSeries(
    samples,
    vmId,
    now,
  );
  const display = recentObservation ?? current;
  const usable = display.status === 'ok';
  const expired = usable && current.status !== 'ok';
  return (
    <section aria-label={`${vmName} 메모리 사용률`} className={styles.managedRamSection}>
      <div className={styles.linuxMemorySummary}>
        <div className={styles.linuxMemorySummaryHead}>
          <span>메모리 사용률</span>
          <strong>{usable ? `${display.percent}%` : '—'}</strong>
        </div>
        <div className={styles.linuxMemoryAmount}>
          <strong title={bytesTitle(display.committedBytes)}>{formatRamMiB(display.committedBytes)}</strong>
          <span title={bytesTitle(display.totalBytes)}>/ {formatRamMiB(display.totalBytes)}</span>
        </div>
        <div className={styles.linuxMemoryFree}>
          <span>{expired ? '관측 당시 여유' : '여유'}</span>
          <strong title={bytesTitle(display.freeBytes)}>{formatRamMiB(display.freeBytes)}</strong>
        </div>
        <div
          className={styles.linuxMemoryBar}
          role="progressbar"
          aria-label={`${vmName} 메모리 사용률`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={current.status === 'ok' ? Number(current.percent) : undefined}
          aria-valuetext={expired ? `최근 관측 ${display.percent}% · 관측 만료` : undefined}
        >
          <i style={{ width: `${usable ? display.percent : 0}%` }} />
        </div>
        <p
          className={`${styles.appMemoryNotice} ${styles.managedRamNotice}`}
          title={current.reason ?? undefined}
        >
          {expired ? '관측 만료 · 최근 관측값' : usable ? '최근 관측값' : managedRamReason(current.reason)}
        </p>
      </div>
      <p className={styles.dim} title="펌웨어 관리 RAM 기준이며 고정 힙·버퍼·스택의 정적 예약을 포함합니다.">
        정적 예약 포함
      </p>
      <p className={styles.appMemoryTime}>
        최근 관측{timeBasis === 'receipt_estimate' ? ' · 수신 기준 추정' : ''}:{' '}
        {lastObservationAt == null ? (
          '미수신'
        ) : (
          <time dateTime={new Date(lastObservationAt).toISOString()}>
            {new Date(lastObservationAt).toLocaleTimeString('ko-KR')}
          </time>
        )}
      </p>
      <section className={styles.appMemoryPool} aria-label={`${vmName} 메모리 사용률 관측 이력`}>
        <header>
          <strong>메모리 사용률 · 최근 60초</strong>
          <small>원 표본 관측 이력</small>
        </header>
        <MemoryPercentChart points={points} label={`${vmName} 메모리 사용률 관측 이력`} now={now} />
        <small className={styles.appMemoryTime}>
          최근 수신 표본 기준 · 새 부팅·발행자 관측 시 이력을 분리합니다.
        </small>
      </section>
    </section>
  );
}

function formatRamMiB(value: string | null): string {
  if (value == null) return '—';
  const divisor = 1_048_576n;
  const thousandths = (BigInt(value) * 1000n + divisor / 2n) / divisor;
  return `${(thousandths / 1000n).toLocaleString('ko-KR')}.${(thousandths % 1000n).toString().padStart(3, '0')} MiB`;
}
function bytesTitle(value: string | null): string | undefined {
  return value == null ? undefined : `${value} bytes`;
}
