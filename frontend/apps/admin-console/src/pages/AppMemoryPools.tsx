import { useEffect, useState, type ReactNode } from 'react';
import type { MetricSample } from '../api/types';
import {
  APP_MEMORY_REGIONS,
  buildAppMemorySeries,
  formatAppBytes,
  type AppMemoryPoint,
  type AppMemorySnapshot,
  type AppMemoryRegion,
} from '../domain/appMemory';
import styles from './pages.module.css';

/** 앱 내부 점유와 원 업무 상태를 보여준다. RAM 확보량과 합산하지 않는다. */
export function AppMemoryPools({
  vmId,
  vmName,
  samples,
  children,
}: {
  vmId: string;
  vmName: string;
  samples: MetricSample[];
  children?: ReactNode;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const series = buildAppMemorySeries(samples, vmId, now);
  return (
    <section aria-label={`${vmName} 앱 버퍼 점유`} className={styles.appMemorySection}>
      <header>
        <h3>메모리 상세</h3>
        <small>최근 메모리 관측</small>
      </header>
      <p className={styles.dim}>
        고정 버퍼 안의 점유입니다. 정적 예약량에 포함되며 VM 전체 메모리 사용률로 합산하지 않습니다.
      </p>
      {(['자동', '공통', '수동'] as const).map((group) => (
        <section key={group} className={styles.memoryGroup} aria-label={`${vmName} ${group} 메모리`}>
          <h4>
            <span className={styles.memoryGroupBadge}>{group}</span>
          </h4>
          <div className={styles.appMemoryGrid}>
            {APP_MEMORY_REGIONS.filter((region) => region.group === group).map(({ id, label }) => (
              <PoolPanel
                key={id}
                region={id}
                label={label}
                vmName={vmName}
                current={series[id].current}
                points={series[id].points}
                now={now}
              />
            ))}
            {group === '공통' && children}
          </div>
          {group === '수동' && (
            <p className={styles.dim}>
              대기·실행·결과는 별도 슬롯입니다. 세 카드의 경합 누계는 같은 원천이며 합산하지 않습니다.
            </p>
          )}
        </section>
      ))}
    </section>
  );
}

function PoolPanel({
  region,
  label,
  vmName,
  current,
  points,
  now,
}: {
  region: AppMemoryRegion;
  label: string;
  vmName: string;
  current: AppMemorySnapshot;
  points: AppMemoryPoint[];
  now: number;
}) {
  const usable = current.state === 'ok';
  const expired = usable && current.stale;
  const display = (value: bigint | null) => formatAppBytes(usable ? value : null);
  const buffer = usable ? current.buffer : null;
  return (
    <section className={styles.appMemoryPool} aria-label={`${vmName} ${label}`}>
      <header>
        <strong>{label}</strong>
        <b>{usable ? `${current.usagePercent!.toFixed(1)}%` : '—'}</b>
      </header>
      <div className={styles.appMemoryAmount}>
        <strong title={usable ? rawBytes(current.used_bytes) : undefined}>
          {display(current.used_bytes)}
        </strong>
        <span title={usable ? rawBytes(current.capacity_bytes) : undefined}>
          / {display(current.capacity_bytes)}
        </span>
      </div>
      <div
        className={styles.poolBar}
        role="progressbar"
        aria-label={`${vmName} ${label} 점유율`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={usable && !expired ? current.usagePercent! : undefined}
        aria-valuetext={expired ? `최근 관측 ${current.usagePercent!.toFixed(1)}% · 관측 만료` : undefined}
      >
        <i style={{ width: `${usable ? current.usagePercent : 0}%` }} />
      </div>
      <p
        className={styles.appMemoryNotice}
        title={`${current.reason ?? ''}${current.sourceRc == null ? '' : ` (${current.sourceRc})`}`}
      >
        {expired ? '관측 만료 · 최근 관측값' : usable ? '최근 관측값' : reasonLabel(current)}
      </p>
      {buffer && (
        <div className={styles.bufferState}>
          <strong>
            {expired ? '관측 당시 · ' : ''}
            {phaseLabel(buffer.phase)}
          </strong>
          <span title={`input_status=${buffer.input_status}, source_rc=${buffer.source_rc}`}>
            입력: {inputLabel(buffer.input_status)}
          </span>
          {buffer.revoked && <span>철회 관측 · 반환 여부는 점유로 확인</span>}
          {region === 'manual_result' && (
            <span title="계산 수행은 성공·출력 허가·장치 적용을 뜻하지 않습니다.">
              {buffer.performed ? '계산 수행 보고 있음' : '계산 수행 보고 없음'}
            </span>
          )}
          {region === 'manual_result' && (
            <span>
              {buffer.last_read_sequence == null
                ? '읽기 보고 없음'
                : buffer.last_read_sequence === buffer.sequence
                  ? '이 결과 읽음 · 사본 보관 유지'
                  : '이전 결과 읽음'}
            </span>
          )}
          {buffer.source_rc < 0 && <span>입력·작업 오류 보고 · {buffer.source_rc}</span>}
          <small
            title={
              buffer.source_uptime_ms == null
                ? undefined
                : `원 입력 수신 시각: 부팅 후 ${buffer.source_uptime_ms} ms`
            }
          >
            내용 {formatAppBytes(BigInt(buffer.content_bytes))} · 원 입력 순번 {buffer.sequence ?? '미확인'}
          </small>
          {buffer.phase !== 'empty' && buffer.source_uptime_ms == null && <small>원 입력 시각 미확인</small>}
        </div>
      )}
      <dl>
        <div>
          <dt>{expired ? '관측 당시 여유' : '현재 여유'}</dt>
          <dd title={usable ? rawBytes(current.free_bytes) : undefined}>{display(current.free_bytes)}</dd>
        </div>
        <div>
          <dt>최고 점유 · 부팅 후</dt>
          <dd title={usable ? rawBytes(current.high_water_bytes) : undefined}>
            {display(current.high_water_bytes)}
          </dd>
        </div>
        <div>
          <dt>최소 여유 · 부팅 후</dt>
          <dd title={usable ? rawBytes(current.min_free_bytes) : undefined}>
            {display(current.min_free_bytes)}
          </dd>
        </div>
        <div>
          <dt>정적 예약</dt>
          <dd title={usable ? rawBytes(current.reserved_bytes) : undefined}>
            {display(current.reserved_bytes)}
          </dd>
        </div>
        <div>
          <dt>공간 부족 누계</dt>
          <dd>{count(usable ? current.capacity_failure_total : null)}</dd>
        </div>
        <div>
          <dt title={region === 'manual_input' ? '원 32비트 누계이며 감소 시 불연속입니다.' : undefined}>
            {region.startsWith('manual_') && region !== 'manual_input'
              ? '공유 경합 누계'
              : '소유자 경합 누계'}
          </dt>
          <dd>{count(usable ? current.contention_total : null)}</dd>
        </div>
      </dl>
      <MemoryPercentChart points={points} label={`${vmName} ${label} 점유율 최근 60초`} now={now} />
      <small
        className={styles.appMemoryTime}
        title={
          current.timeBasis === 'source_utc'
            ? '송신 UTC와 원 uptime 차이로 환산한 시각이며 UTC 불확실도를 포함합니다.'
            : undefined
        }
      >
        {current.observedAt == null ? '원 관측 없음' : `원 관측 약 ${age(current.observedAt, now)}`}
        {current.receivedAt && ` · 최초 수신 ${age(Date.parse(current.receivedAt), now)}`}
        {current.timeBasis === 'receipt_estimate' && ' (수신 기준 추정)'}
      </small>
    </section>
  );
}

function phaseLabel(phase: NonNullable<AppMemorySnapshot['buffer']>['phase']): string {
  return {
    empty: '비어 있음',
    stored: '입력 보관',
    pending: '작업 대기',
    running: '계산 실행 중',
    retained: '결과 보관',
  }[phase];
}
function inputLabel(status: NonNullable<AppMemorySnapshot['buffer']>['input_status']): string {
  return {
    missing: '없음',
    unclassified: '분류 전',
    accepted: '수락',
    invalid: '무효',
    expired: '만료',
    context_changed: '문맥 변경',
  }[status];
}

export function MemoryPercentChart({
  points,
  label,
  now,
}: {
  points: AppMemoryPoint[];
  label: string;
  now: number;
}) {
  const segments: string[][] = [];
  let segment: string[] = [];
  for (const point of points) {
    if (point.percent == null) {
      if (segment.length) segments.push(segment);
      segment = [];
    } else {
      const x = (point.observedAt - (now - 60_000)) / 600;
      const y = 100 - point.percent;
      segment.push(`${x.toFixed(2)},${y.toFixed(2)}`);
    }
  }
  if (segment.length) segments.push(segment);
  return (
    <div className={styles.appPoolChart}>
      {segments.length ? (
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={label}>
          {[0, 50, 100].map((y) => (
            <line key={y} x1="0" x2="100" y1={y} y2={y} className={styles.memoryGridLine} />
          ))}
          {segments.map((line, index) =>
            line.length > 1 ? (
              <polyline
                key={index}
                points={line.join(' ')}
                className={styles.linuxMemoryLine}
                vectorEffect="non-scaling-stroke"
              />
            ) : (
              <circle
                key={index}
                cx={line[0]!.split(',')[0]}
                cy={line[0]!.split(',')[1]}
                r="1"
                className={styles.appMemoryDot}
              />
            ),
          )}
        </svg>
      ) : (
        <div className={styles.appPoolChartEmpty}>표본 없음</div>
      )}
      <div>
        <span>60초 전</span>
        <span>현재</span>
      </div>
    </div>
  );
}

function rawBytes(value: bigint | null): string | undefined {
  return value == null ? undefined : `${value.toString()} bytes`;
}
function count(value: bigint | null): string {
  return value == null ? '—' : `${value.toLocaleString('ko-KR')} 회`;
}
function age(at: number, now: number): string {
  return `${Math.max(0, Math.floor((now - at) / 1000))}초 전`;
}
function reasonLabel(snapshot: AppMemorySnapshot): string {
  if (snapshot.reason === 'owner_busy') return '소유자 사용 중 · 이번 표본 미측정';
  if (snapshot.reason === 'sample_expired') return '원 표본 만료 · 미측정';
  if (snapshot.reason === 'unsupported') return '미지원';
  return snapshot.reason ?? '미측정';
}
