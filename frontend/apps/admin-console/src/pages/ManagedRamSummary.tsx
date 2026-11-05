import { useEffect, useState } from 'react';
import type { MetricSample } from '../api/types';
import { buildManagedRamSeries } from '../domain/managedRam';
import { MemoryPercentChart } from './AppMemoryPools';
import styles from './pages.module.css';

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
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const { current, points, lastObservationAt, timeBasis } = buildManagedRamSeries(samples, vmId, now);
  const usable = current.status === 'ok';
  return (
    <section aria-label={`${vmName} 메모리 사용률`} className={styles.managedRamSection}>
      <div className={styles.linuxMemorySummary}>
        <div className={styles.linuxMemorySummaryHead}>
          <span>메모리 사용률</span>
          <strong>{usable ? `${current.percent}%` : '—'}</strong>
        </div>
        <div className={styles.linuxMemoryAmount}>
          <strong title={bytesTitle(current.committedBytes)}>{formatRamMiB(current.committedBytes)}</strong>
          <span title={bytesTitle(current.totalBytes)}>/ {formatRamMiB(current.totalBytes)}</span>
        </div>
        <div className={styles.linuxMemoryFree}>
          <span>여유</span>
          <strong title={bytesTitle(current.freeBytes)}>{formatRamMiB(current.freeBytes)}</strong>
        </div>
        <div
          className={styles.linuxMemoryBar}
          role="progressbar"
          aria-label={`${vmName} 메모리 사용률`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={usable ? Number(current.percent) : undefined}
        >
          <i style={{ width: `${usable ? current.percent : 0}%` }} />
        </div>
        {!usable && (
          <p
            className={`${styles.appMemoryNotice} ${styles.managedRamNotice}`}
            title={current.reason ?? undefined}
          >
            {ramReason(current.reason)}
          </p>
        )}
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
function ramReason(reason: string | null): string {
  if (reason === 'RAM_CONTEXT_UNVERIFIED') return '관측 문맥 불완전';
  if (reason === 'RAM_CONTEXT_CHANGED') return '실행 문맥 변경 · 현재 값 미확인';
  if (reason === 'RAM_SESSION_AMBIGUOUS') return '관측 세션 순서 미확인';
  if (reason === 'RAM_NOT_RECEIVED') return '관리 RAM 표본 미수신';
  if (reason === 'RAM_SOURCE_UNIDENTIFIED') return '원 관측 시각 미확인';
  if (reason === 'RAM_SOURCE_EXPIRED' || reason === 'RAM_RECEIPT_EXPIRED') return '관리 RAM 관측 만료';
  if (reason === 'RAM_SAMPLE_CONFLICT') return '같은 원 표본의 RAM 값 충돌';
  if (reason === 'RAM_SOURCE_UNAVAILABLE' || reason === 'RAM_COLLECTION_UNAVAILABLE')
    return '관리 RAM 수집 불가';
  return reason ? '관리 RAM 표본을 확인할 수 없습니다.' : '관리 RAM 표본 미수신';
}
