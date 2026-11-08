import { Card } from '@xenova/ui';
import type { CSSProperties } from 'react';
import type { DriveState } from '../api/drive';
import { LIVE_POLL_MS, useMetrics, useVms } from '../api/queries';
import type { MetricSample, Vm } from '../api/types';
import { buildCpuChartScale, cpuChartY, formatCpuTick } from '../domain/cpuChart';
import {
  buildDiskIoSnapshot,
  buildLinuxMemorySeries,
  buildZephyrHeapSeries,
  cpuPercentOf,
  linuxFreeMemory,
  type DiskIoCounter,
} from '../domain/vmMetrics';
import { buildPacketRateSeries, bytesToKiB } from '../domain/memoryPools';
import { ago, osLabel, roleOf, sortVms, UNASSIGNED_COLOR, type Role } from '../domain/roles';
import { vmAuthorityOf } from '../domain/vmAuthority';
import { observedControl } from '../domain/driveObservations';
import { useDrive } from '../layout/DriveContext';
import { AppMemoryPools } from './AppMemoryPools';
import { ManagedRamSummary } from './ManagedRamSummary';
import { sharedMemoryObservation, sharedObservedAt } from '../domain/sharedMemoryObservation';
import { memoryObservationRunKey, selectMemoryObservationRun } from '../domain/memoryObservationRun';
import { LiveDataAlert } from '../layout/LiveDataAlert';
import { useNow } from '../useNow';
import { MetricErrorNotice } from './MetricErrorNotice';
import { NetworkDashboard } from './NetworkDashboard';
import styles from './pages.module.css';

const MIB_BYTES = 1024 * 1024;

/** Dashboard CPU·Memory·Network 탭 (/dashboard/cpu·/dashboard/memory·/dashboard/network) */
export function ResourcesPage({ view }: { view: 'cpu' | 'memory' | 'network' }) {
  const vms = useVms(LIVE_POLL_MS, { keepLastData: view === 'memory' });
  const { drive } = useDrive();
  if (view === 'cpu') {
    return <CpuDashboard items={vms.data?.items} drive={drive} pending={vms.isPending} />;
  }
  if (view === 'memory') {
    return <MemoryDashboard items={vms.data?.items} drive={drive} pending={vms.isPending} />;
  }
  return <NetworkDashboard items={vms.data?.items} drive={drive} pending={vms.isPending} />;
}

function MemoryDashboard({
  items,
  drive,
  pending,
}: {
  items: Vm[] | undefined;
  drive: DriveState | null;
  pending: boolean;
}) {
  const vms = sortVms(items ?? []);

  return (
    <div className={styles.page}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>RESOURCE · MEMORY</span>
          <h1>Memory</h1>
        </div>
        <div className={styles.memoryHeadingMeta}>
          <span>Linux 최근 5분</span>
          <span>Zephyr RAM·버퍼 최근 60초 · 힙 최근 5분</span>
        </div>
      </header>

      {pending && <div className={styles.statePanel}>VM 정보를 불러오는 중입니다…</div>}
      <LiveDataAlert />

      <div className={styles.memoryVmGrid}>
        {vms.map((vm) =>
          vm.guest_kind === 'zephyr' ? (
            <MemoryVmPanel key={vm.vm_id} vm={vm} drive={drive} />
          ) : (
            <LinuxMemoryPanel key={vm.vm_id} vm={vm} drive={drive} />
          ),
        )}
      </div>
    </div>
  );
}

function MemoryVmPanel({ vm, drive }: { vm: Vm; drive: DriveState | null }) {
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true, 1, LIVE_POLL_MS, { keepLastData: true });
  const heapMetrics = useMetrics(vm.vm_id, true, 5, LIVE_POLL_MS, { keepLastData: true });
  const samples = metrics.data?.items ?? [];
  const { socket } = useDrive();
  // Overview·VM 상세와 같이 소켓이 열려 있고 control 이 신선할 때만 ACTIVE/STANDBY 를 판정한다
  const authority = vmAuthorityOf(role, vm, observedControl(drive, socket)?.active_vm_id);
  const style = {
    '--vm-accent': role?.color ?? UNASSIGNED_COLOR,
    '--rx-accent': 'var(--x-primary)',
    '--tx-accent': 'var(--x-pool-tx)',
  } as CSSProperties;

  return (
    <Card
      className={styles.memoryVmCard}
      style={style}
      title={
        <span className={styles.memoryVmTitle}>
          <strong>{vm.name}</strong>
          <small>{role?.short ?? '역할 미지정'} · Zephyr · 앱 버퍼·힙·네트워크 풀</small>
        </span>
      }
      actions={
        <span className={styles.memoryCardActions}>
          <span className={styles.authority} data-authority={authority.toLowerCase()}>
            {authority}
          </span>
          <small>관측 시각과 만료는 영역별로 확인</small>
        </span>
      }
    >
      {(metrics.error || heapMetrics.error) && (
        <MetricErrorNotice detail="최근 관측값·이력을 표시하며, 만료 여부는 영역별로 구분합니다." />
      )}
      <ManagedRamSummary vmId={vm.vm_id} vmName={vm.name} samples={samples} />
      <p className={styles.dim}>
        VM 구성 {vm.memory_mib.toLocaleString('ko-KR')} MiB · 관리 RAM과 내부 버퍼 점유를 분리합니다.
      </p>
      <AppMemoryPools vmId={vm.vm_id} vmName={vm.name} samples={samples}>
        <SharedMemoryPanels vmName={vm.name} samples={samples} heapSamples={heapMetrics.data?.items ?? []} />
      </AppMemoryPools>
    </Card>
  );
}

function SharedMemoryPanels({
  vmName,
  samples,
  heapSamples,
}: {
  vmName: string;
  samples: MetricSample[];
  heapSamples: MetricSample[];
}) {
  // 화면 전체가 같이 쓰는 1초 시계 — 같은 카드 안의 "n초 전"이 서로 다른 시계로 계산되지 않게
  const now = useNow();
  // The 5-minute heap query must also respect a new session first seen in the 60-second buffer query.
  const allSamples = [...heapSamples, ...samples];
  const run = selectMemoryObservationRun(allSamples, allSamples[0]?.vm_id ?? '');
  const projectRun = (history: MetricSample[]) =>
    (run.state === 'selected'
      ? history.filter((sample) => memoryObservationRunKey(sample) === run.key)
      : []
    ).map((sample) => {
      const family = sample.telemetry?.family;
      const at = family === 'memory' || family === 'memory_regions' ? sharedObservedAt(sample, family) : null;
      return at ? { ...sample, observed_at: at } : sample;
    });
  // Network reports do not carry the memory observation context.
  const packetRates = buildPacketRateSeries(samples);
  const heap = buildZephyrHeapSeries(projectRun(heapSamples));
  const heapObservation = sharedMemoryObservation(
    allSamples,
    'memory',
    ['memory.kernel_heap.used_bytes', 'memory.kernel_heap.free_bytes'],
    now,
  );
  const currentHeapView =
    (heapObservation.state === 'fresh' || heapObservation.state === 'expired') && heapObservation.sample
      ? buildZephyrHeapSeries([heapObservation.sample])
      : null;
  const currentHeap = currentHeapView?.current ?? null;
  return (
    <>
      <section className={styles.appMemoryPool} aria-label={`${vmName} 공통 커널 힙`}>
        <MemoryUsageSummary
          label="커널 힙 사용률"
          badge={HEAP_BASIS_BADGE}
          ariaLabel={`${vmName} 커널 힙 사용률`}
          percent={currentHeap?.percent ?? null}
          usedBytes={currentHeap?.usedBytes ?? null}
          totalBytes={currentHeap?.totalBytes ?? null}
          freeLabel={heapObservation.state === 'expired' ? '관측 당시 여유' : '여유'}
          expired={heapObservation.state === 'expired' && currentHeap != null}
          freeBytes={currentHeap?.freeBytes ?? null}
          freeNote={currentHeapView?.reason ?? heapObservation.reason}
          format={formatKiB}
        />
        <p className={styles.dim}>
          {heapObservation.reason}
          {heapObservation.observedAt &&
            ` · 원 관측 약 ${ago(Math.max(0, now - Date.parse(heapObservation.observedAt)))}`}
        </p>
        <UsageChart
          title="커널 힙 사용률"
          ariaLabel={`${vmName} 커널 힙 사용률 최근 5분 그래프`}
          points={heap.points}
        />
      </section>
      <section
        className={`${styles.appMemoryPool} ${styles.memorySharedNetwork}`}
        aria-label={`${vmName} 공통 네트워크 패킷`}
      >
        {/* RX/TX Pool 요약·Pool 점유율은 Network 탭으로 옮겼다 (S15P31E110-367). 패킷 처리율은 Memory 탭 정리 이슈에서 다룬다 */}
        <h4>공통 네트워크 패킷</h4>

        <MemoryChart
          title="패킷 처리율"
          unit="packets/s"
          ariaLabel={`${vmName} RX TX 패킷 처리율 최근 60초`}
          points={packetRates.map((point) => ({
            observedAt: point.observedAt,
            rx: point.rxPacketsPerSecond,
            tx: point.txPacketsPerSecond,
          }))}
        />
      </section>
    </>
  );
}

interface ChartPoint {
  observedAt: string;
  rx: number | null;
  tx: number | null;
}

function MemoryChart({
  title,
  unit,
  ariaLabel,
  points,
  max,
}: {
  title: string;
  unit: string;
  ariaLabel: string;
  points: ChartPoint[];
  max?: number;
}) {
  const values = points
    .flatMap((point) => [point.rx, point.tx])
    .filter((value): value is number => value != null);
  const yMax = max ?? Math.max(10, Math.ceil((Math.max(0, ...values) * 1.15) / 10) * 10);
  const rxPoints = chartPoints(points, 'rx', yMax);
  const txPoints = chartPoints(points, 'tx', yMax);

  return (
    <section className={styles.memoryChartBlock}>
      <header>
        <strong>{title}</strong>
        <span className={styles.memoryLegend}>
          <span data-tone="rx">
            <i />
            RX {unit}
          </span>
          <span data-tone="tx">
            <i />
            TX {unit}
          </span>
        </span>
      </header>
      <div className={styles.memoryChart}>
        {rxPoints.length || txPoints.length ? (
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
            {rxPoints.map((segment, index) => (
              <polyline
                key={`rx-${index}`}
                points={segment}
                className={styles.memoryRxLine}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {txPoints.map((segment, index) => (
              <polyline
                key={`tx-${index}`}
                points={segment}
                className={styles.memoryTxLine}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
        ) : (
          <div className={styles.memoryChartEmpty}>데이터 없음</div>
        )}
        <span className={styles.memoryChartMax}>
          {yMax.toFixed(0)} {unit}
        </span>
        <div className={styles.memoryChartAxis} aria-hidden="true">
          <span>60초 전</span>
          <span>현재</span>
        </div>
      </div>
    </section>
  );
}

interface BasisBadge {
  label: string;
  title: string;
}

/** Zephyr 메모리 사용률은 VM 전체 RAM 이 아니라 커널 heap 기준이다 */
const HEAP_BASIS_BADGE: BasisBadge = {
  label: '커널 heap 기준',
  title: 'VM 전체 RAM이 아닌 커널 heap 기준 · 전체 = accounted_bytes(없으면 used+free)',
};

function BasisBadgeTag({ badge }: { badge: BasisBadge }) {
  return (
    <small className={styles.memoryBasisBadge} title={badge.title}>
      {badge.label}
    </small>
  );
}

/** Linux 메모리·Zephyr 커널 heap 이 함께 쓰는 사용률 요약 (사용량 / 전체, 여유, 막대) */
function MemoryUsageSummary({
  label,
  badge,
  ariaLabel,
  percent,
  usedBytes,
  totalBytes,
  freeLabel,
  freeTitle,
  freeBytes,
  freeNote,
  format,
  expired = false,
}: {
  label: string;
  badge?: BasisBadge;
  ariaLabel: string;
  percent: number | null;
  usedBytes: number | null;
  totalBytes: number | null;
  freeLabel: string;
  /** 여유 값의 기준 설명 (툴팁) */
  freeTitle?: string;
  freeBytes: number | null;
  freeNote: string | null;
  format: (bytes: number | null) => string;
  expired?: boolean;
}) {
  const clamped = percent == null ? null : Math.max(0, Math.min(100, percent));
  return (
    <section className={styles.linuxMemorySummary}>
      <div className={styles.linuxMemorySummaryHead}>
        <span>
          {label}
          {badge && <BasisBadgeTag badge={badge} />}
        </span>
        <strong>{percent == null ? '—' : `${percent.toFixed(1)}%`}</strong>
      </div>
      <div className={styles.linuxMemoryAmount}>
        <strong>{format(usedBytes)}</strong>
        <span>/ {format(totalBytes)}</span>
      </div>
      <div className={styles.linuxMemoryFree} title={freeTitle}>
        <span>{freeLabel}</span>
        <strong>{freeBytes == null ? (freeNote ?? '—') : format(freeBytes)}</strong>
      </div>
      <div
        className={styles.linuxMemoryBar}
        role="progressbar"
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped == null || expired ? undefined : Math.round(clamped)}
        aria-valuetext={
          expired && percent != null ? `최근 관측 ${percent.toFixed(1)}% · 관측 만료` : undefined
        }
      >
        <i style={{ width: `${clamped ?? 0}%` }} />
      </div>
    </section>
  );
}

function LinuxMemoryPanel({ vm, drive }: { vm: Vm; drive: DriveState | null }) {
  const now = useNow();
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true, 5, LIVE_POLL_MS, { keepLastData: true });
  const samples = metrics.data?.items ?? [];
  const series = buildLinuxMemorySeries(samples, vm);
  const free = linuxFreeMemory(series);
  const disk = buildDiskIoSnapshot(samples);
  const { socket } = useDrive();
  // Overview·VM 상세와 같이 소켓이 열려 있고 control 이 신선할 때만 ACTIVE/STANDBY 를 판정한다
  const authority = vmAuthorityOf(role, vm, observedControl(drive, socket)?.active_vm_id);
  const style = {
    '--vm-accent': role?.color ?? UNASSIGNED_COLOR,
  } as CSSProperties;
  const current = series.current;
  const notice = metrics.error
    ? null
    : metrics.isPending && !current
      ? '메모리 메트릭 수신 대기 중입니다.'
      : !current
        ? '메모리 데이터 없음'
        : null;
  const freeTitle =
    free.basis === 'available'
      ? '여유 = MemAvailable (memory_available_bytes)'
      : free.basis === 'estimated'
        ? `MemAvailable ${free.reason ?? '데이터 없음'} · 할당량 − 사용량으로 추정`
        : undefined;

  return (
    <Card
      className={styles.memoryVmCard}
      style={style}
      title={
        <span className={styles.memoryVmTitle}>
          <strong>{vm.name}</strong>
          <small>
            {role?.short ?? '역할 미지정'} · {osLabel(vm)}
          </small>
        </span>
      }
      actions={
        <span className={styles.memoryCardActions}>
          <span className={styles.authority} data-authority={authority.toLowerCase()}>
            {authority}
          </span>
          <small>마지막 갱신 {current ? relativeTime(current.observedAt, now) : '—'}</small>
        </span>
      }
    >
      {metrics.error && <MetricErrorNotice />}
      {notice && <div className={styles.memoryNotice}>{notice}</div>}

      <MemoryUsageSummary
        label="메모리 사용률"
        ariaLabel={`${vm.name} 메모리 사용률`}
        percent={current?.percent ?? null}
        usedBytes={current?.usedBytes ?? null}
        totalBytes={series.totalBytes}
        freeLabel={free.basis === 'estimated' ? '여유 (추정)' : '여유'}
        freeTitle={freeTitle}
        freeBytes={free.bytes}
        freeNote={null}
        format={formatMiBOrDash}
      />

      <UsageChart
        title="메모리 사용률"
        ariaLabel={`${vm.name} 메모리 사용률 최근 5분 그래프`}
        points={series.points}
      />

      <section className={styles.memoryChartBlock} aria-label={`${vm.name} 디스크 I/O`}>
        <header>
          <strong>디스크 I/O</strong>
          <span className={styles.memoryBasisNote}>장치 합산 · 같은 부팅 구간에서만 속도 계산</span>
        </header>
        <div className={styles.counterGrid}>
          <DiskIoItem label="읽기" counter={disk.read} />
          <DiskIoItem label="쓰기" counter={disk.write} />
        </div>
      </section>

      <div className={styles.memoryObservation}>
        {current
          ? `할당 ${vm.memory_mib.toLocaleString('ko-KR')} MiB 중 ${formatMiB(current.usedBytes)} 사용 중`
          : 'Linux guest-agent의 memory_used_bytes 표본을 기다리고 있습니다.'}
      </div>
    </Card>
  );
}

function DiskIoItem({ label, counter }: { label: string; counter: DiskIoCounter }) {
  return (
    <div className={styles.counterItem}>
      <span>{label} 누적</span>
      <strong>{counter.totalBytes == null ? '—' : formatBytes(counter.totalBytes)}</strong>
      <small>
        {counter.totalBytes == null
          ? (counter.reason ?? '데이터 없음')
          : counter.bytesPerSecond == null
            ? '속도 계산 대기'
            : `${formatBytes(counter.bytesPerSecond)}/s`}
      </small>
    </div>
  );
}

/** 최근 5분 사용률(%) 선 그래프. Linux 메모리와 Zephyr 커널 heap 이 함께 쓴다 */
function UsageChart({
  title,
  badge,
  ariaLabel,
  points,
  emptyMessage = '데이터 없음',
}: {
  title: string;
  badge?: BasisBadge;
  ariaLabel: string;
  points: readonly { observedAt: string; percent: number }[];
  emptyMessage?: string;
}) {
  const line = chartPoints(
    points.map((point) => ({ observedAt: point.observedAt, rx: point.percent, tx: null })),
    'rx',
    100,
  );

  return (
    <section className={styles.memoryChartBlock}>
      <header>
        <strong>
          {title}
          {badge && <BasisBadgeTag badge={badge} />}
        </strong>
        <span className={styles.linuxMemoryLegend}>
          <i />
          Used %
        </span>
      </header>
      <div className={styles.memoryChart}>
        {line.length ? (
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
            {line.map((segment, index) => (
              <polyline
                key={index}
                points={segment}
                className={styles.linuxMemoryLine}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
        ) : (
          <div className={styles.memoryChartEmpty}>{emptyMessage}</div>
        )}
        <span className={styles.memoryChartMax}>100%</span>
        <div className={styles.memoryChartAxis} aria-hidden="true">
          <span>5분 전</span>
          <span>현재</span>
        </div>
      </div>
    </section>
  );
}

function chartPoints(points: ChartPoint[], key: 'rx' | 'tx', max: number): string[] {
  if (!points.length) return [];
  const start = Date.parse(points[0]!.observedAt);
  const end = Date.parse(points.at(-1)!.observedAt);
  const span = Math.max(1, end - start);
  const segments: string[][] = [];
  let segment: string[] = [];
  for (const point of points) {
    const value = point[key];
    // 미측정·카운터 초기화 경계에 선을 이어 연속 측정처럼 보이지 않게 한다.
    if (value == null || !Number.isFinite(value)) {
      if (segment.length) segments.push(segment);
      segment = [];
      continue;
    }
    const x = ((Date.parse(point.observedAt) - start) / span) * 100;
    const y = 100 - (Math.max(0, Math.min(max, value)) / max) * 100;
    segment.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  }
  if (segment.length) segments.push(segment);
  return segments.map((part) => part.join(' '));
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024 * 1024) return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  if (value >= 1024 * 1024) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${Math.round(value)} B`;
}

function formatMiBOrDash(value: number | null): string {
  return value == null ? '—' : formatMiB(value);
}

function formatKiB(value: number | null): string {
  if (value == null) return '—';
  return `${bytesToKiB(value).toLocaleString('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: value ? 2 : 0 })} KiB`;
}

function formatMiB(value: number): string {
  return `${(value / MIB_BYTES).toLocaleString('ko-KR', { maximumFractionDigits: 1 })} MiB`;
}

function relativeTime(value: string | null, now: number): string {
  if (!value) return '—';
  const elapsed = Math.max(0, now - Date.parse(value));
  return ago(elapsed);
}

function CpuDashboard({
  items,
  drive,
  pending,
}: {
  items: Vm[] | undefined;
  drive: ReturnType<typeof useDrive>['drive'];
  pending: boolean;
}) {
  return (
    <div className={styles.page}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>RESOURCE MONITORING</span>
          <h1>CPU Usage</h1>
        </div>
      </header>

      {pending && <div className={styles.statePanel}>VM 정보를 불러오는 중입니다…</div>}
      <LiveDataAlert />

      <div className={styles.cpuGrid}>
        {sortVms(items ?? []).map((vm) => (
          <CpuVmChart key={vm.vm_id} role={roleOf(vm, drive)} vm={vm} />
        ))}
      </div>
    </div>
  );
}

function CpuVmChart({ role, vm }: { role: Role | null; vm: Vm }) {
  const metrics = useMetrics(vm.vm_id, true);
  // Zephyr 는 family 별로 표본이 나뉘므로 CPU 값이 있는 표본만 그린다
  const values = (metrics.data?.items ?? [])
    .map((sample) => cpuPercentOf(sample))
    .filter((value): value is number => value != null);
  const current = values.at(-1) ?? null;
  const scale = buildCpuChartScale(values);
  const points = values
    .map((value, index) => {
      const x = values.length <= 1 ? 100 : (index / (values.length - 1)) * 100;
      const y = cpuChartY(value, scale.max);
      return `${x},${y}`;
    })
    .join(' ');
  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR } as CSSProperties;

  return (
    <Card
      className={styles.cpuCard}
      style={style}
      title={
        <span className={styles.cpuCardTitle}>
          <i aria-hidden="true" />
          <span>
            <strong>{vm.name}</strong>
            <small>{role?.title ?? '역할 미지정'}</small>
          </span>
        </span>
      }
      actions={<span className={styles.cpuCurrent}>{current == null ? '—' : `${current.toFixed(1)}%`}</span>}
    >
      <div className={styles.cpuChart}>
        {points ? (
          <div className={styles.cpuPlot}>
            <div className={styles.cpuYAxis} aria-hidden="true">
              {scale.ticks.map((value) => (
                <span key={value} style={{ top: `${100 - (value / scale.max) * 100}%` }}>
                  {formatCpuTick(value)}%
                </span>
              ))}
            </div>
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              role="img"
              aria-label={`${vm.name} CPU 사용률 최근 5분 그래프, 세로축 최대 ${scale.max}%`}
            >
              {scale.ticks.map((value) => {
                const y = 100 - (value / scale.max) * 100;
                return <line key={value} x1="0" x2="100" y1={y} y2={y} className={styles.cpuGridLine} />;
              })}
              <polyline points={points} className={styles.cpuLine} vectorEffect="non-scaling-stroke" />
            </svg>
          </div>
        ) : (
          <div className={styles.cpuEmpty}>
            <strong>{metrics.isPending ? '메트릭 수신 대기 중' : 'CPU 데이터 없음'}</strong>
            <span>실제 측정값이 도착하면 최근 5분 그래프가 표시됩니다.</span>
          </div>
        )}
        <div className={styles.cpuAxis} aria-hidden="true">
          <span>5분 전</span>
          <span>현재</span>
        </div>
      </div>

      <div className={styles.cpuMeta}>
        <span>
          {vm ? `${vm.vcpus} vCPU · ${vm.guest_kind === 'zephyr' ? 'Zephyr' : 'Linux'}` : 'VM 연결 대기'}
        </span>
        <span>
          {metrics.error ? '메트릭 연결 오류' : values.length ? `${values.length} samples` : 'No samples'}
        </span>
      </div>
    </Card>
  );
}
