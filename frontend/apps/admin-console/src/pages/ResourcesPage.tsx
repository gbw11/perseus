import { Card } from '@xenova/ui';
import { useEffect, useState, type CSSProperties } from 'react';
import type { DriveState } from '../api/drive';
import { useMetrics, useVms } from '../api/queries';
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
import { buildMemoryPoolSeries, bytesToKiB, type PoolSnapshot } from '../domain/memoryPools';
import {
  ago,
  osLabel,
  ROLES,
  roleOf,
  sortVms,
  UNASSIGNED_COLOR,
  vmOf,
  type AppId,
  type Role,
} from '../domain/roles';
import { vmAuthorityOf } from '../domain/vmAuthority';
import { useDrive } from '../layout/DriveContext';
import { AppMemoryPools } from './AppMemoryPools';
import { ManagedRamSummary } from './ManagedRamSummary';
import { sharedMemoryObservation, sharedObservedAt } from '../domain/sharedMemoryObservation';
import { memoryObservationRunKey, selectMemoryObservationRun } from '../domain/memoryObservationRun';
import { MetricErrorNotice } from './MetricErrorNotice';
import styles from './pages.module.css';

/**
 * W-06 자원 (화면 정의서 W-06, UI-06) — 초안, 보기 전용. 자원은 VM 을 만들 때 정해지고 도중에 바꾸지 않는다:
 * 핀닝·스케줄러·메모리는 "지금 이렇게 나뉘어 있다" 를 보여 주는 용도다 (편집·적용 없음).
 * vCPU 수·메모리는 VM 목록(GET /api/v1/vms), 코어 배치·credit2 값은 RC카 시나리오 §2 표본 —
 * 실제 값 조회 API(-107)가 생기면 그 값으로 바꾼다. 화면 완성은 정수환 담당(-113·-114). 참고 시안: init/vcpu.
 */

type Domain = 'dom0' | AppId;

const DOMAINS: { id: Domain; title: string; sub: string; color: string; dedicated: boolean }[] = [
  { id: 'dom0', title: 'dom0', sub: 'Yocto · 관리 도메인', color: 'var(--x-text-muted)', dedicated: false },
  {
    id: 'autonomous',
    title: ROLES.autonomous.title,
    sub: 'Zephyr',
    color: ROLES.autonomous.color,
    dedicated: true,
  },
  { id: 'manual', title: ROLES.manual.title, sub: 'Zephyr', color: ROLES.manual.color, dedicated: true },
  {
    id: 'interaction',
    title: ROLES.interaction.title,
    sub: 'Debian 13',
    color: ROLES.interaction.color,
    dedicated: false,
  },
];
const CORES = [0, 1, 2, 3];

/** RC카 시나리오 §2: 자율·수동은 전용 1코어씩, 네비게이션은 남은 코어 */
const PINS: Record<Domain, number[]> = { dom0: [0], autonomous: [1], manual: [2], interaction: [3] };
const WEIGHT: Record<Domain, number> = { dom0: 512, autonomous: 512, manual: 512, interaction: 256 };
const DOM0_MEMORY_MIB = 1024;
const MIB_BYTES = 1024 * 1024;

export function ResourcesPage({ view = 'all' }: { view?: 'cpu' | 'memory' | 'all' }) {
  const vms = useVms();
  const { drive } = useDrive();
  const vmFor = (d: Domain) => (d === 'dom0' ? undefined : vmOf(d, vms.data?.items, drive));
  const memory = (d: Domain) => (d === 'dom0' ? DOM0_MEMORY_MIB : (vmFor(d)?.memory_mib ?? 0));
  const vcpus = (d: Domain) => (d === 'dom0' ? 1 : (vmFor(d)?.vcpus ?? 0));
  const totalMemory = DOMAINS.reduce((sum, d) => sum + memory(d.id), 0);
  const guests = DOMAINS.filter((d) => d.id !== 'dom0');
  const totalWeight = guests.reduce((sum, d) => sum + WEIGHT[d.id], 0);

  if (view === 'cpu') {
    return <CpuDashboard items={vms.data?.items} drive={drive} pending={vms.isPending} />;
  }

  if (view === 'memory') {
    return <MemoryDashboard items={vms.data?.items} drive={drive} pending={vms.isPending} />;
  }

  return (
    <div className={styles.page}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>RESOURCE MONITORING</span>
          <h1>Memory Usage</h1>
        </div>
      </header>

      <Card title="코어 배치 (vCPU 핀닝)" actions={<span className={styles.dim}>RC카 시나리오 §2 표본</span>}>
        <table className={styles.matrix}>
          <thead>
            <tr>
              <th>도메인</th>
              {CORES.map((c) => (
                <th key={c}>CORE {c}</th>
              ))}
              <th>vCPU</th>
            </tr>
          </thead>
          <tbody>
            {DOMAINS.map((d) => (
              <tr key={d.id}>
                <td>
                  <b style={{ color: d.color }}>{d.title}</b>
                  <div className={styles.eyebrow}>{d.sub}</div>
                </td>
                {CORES.map((core) => {
                  const on = PINS[d.id].includes(core);
                  return (
                    <td key={core}>
                      <div
                        className={[styles.cell, on ? styles.cellPinned : ''].join(' ')}
                        style={
                          { '--accent': d.color, display: 'grid', placeItems: 'center' } as CSSProperties
                        }
                      >
                        {on ? (d.dedicated ? '전용' : '고정') : '—'}
                      </div>
                    </td>
                  );
                })}
                <td className={styles.mono}>{vcpus(d.id) || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={styles.dim}>
          주행 VM A·B는 코어를 혼자 쓴다 — 인포테인먼트 VM이 바빠져도 주행 명령 주기(50 Hz)가 흔들리지 않게.
        </p>
      </Card>

      <div className={styles.cols2}>
        <Card title="스케줄러 (credit2 weight)">
          <p className={styles.dim}>
            코어가 겹칠 때 CPU 시간을 나누는 비율. 지금은 코어가 겹치지 않아 참고값이다.
          </p>
          {guests.map((d) => {
            const share = WEIGHT[d.id] / totalWeight;
            return (
              <div key={d.id} style={{ marginBottom: 'var(--x-space-3)' }}>
                <div className={styles.row} style={{ justifyContent: 'space-between', marginBottom: 4 }}>
                  <b style={{ color: d.color }}>{d.title}</b>
                  <span className={styles.mono}>
                    weight {WEIGHT[d.id]} · {Math.round(share * 100)}% · cap 없음
                  </span>
                </div>
                <div className={styles.bar}>
                  <i style={{ width: `${share * 100}%`, background: d.color }} />
                </div>
              </div>
            );
          })}
        </Card>
        <Card title="메모리" actions={<span className={styles.mono}>합계 {totalMemory} MiB</span>}>
          <div className={styles.stack}>
            {DOMAINS.map((d) => {
              const share = totalMemory ? memory(d.id) / totalMemory : 0;
              return (
                <i
                  key={d.id}
                  title={`${d.title} ${memory(d.id)} MiB`}
                  style={{ flexGrow: memory(d.id), background: d.color }}
                >
                  {share > 0.15 ? d.title : ''}
                </i>
              );
            })}
          </div>
          <div className={styles.rows}>
            {DOMAINS.map((d) => (
              <div key={d.id}>
                <span className={styles.legend}>
                  <i style={{ background: d.color }} />
                  {d.title}
                </span>
                <span className={styles.mono}>
                  {memory(d.id) ? `${memory(d.id)} MiB` : '—'}
                  {totalMemory && memory(d.id)
                    ? ` · ${((memory(d.id) / totalMemory) * 100).toFixed(1)}%`
                    : ''}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
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
  const metrics = useMetrics(vm.vm_id, true, 1, 2000);
  const heapMetrics = useMetrics(vm.vm_id, true);
  const samples = metrics.data?.items ?? [];
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
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
        <MetricErrorNotice detail="최근 관측 이력을 표시하며, 만료된 현재값은 비웁니다." />
      )}
      <ManagedRamSummary vmId={vm.vm_id} vmName={vm.name} samples={samples} />
      <p className={styles.dim}>
        VM 구성 {vm.memory_mib.toLocaleString('ko-KR')} MiB · 관리 RAM과 내부 버퍼 점유를 분리합니다.
      </p>
      <AppMemoryPools vmId={vm.vm_id} vmName={vm.name} samples={samples}>
        <SharedMemoryPanels
          vmName={vm.name}
          samples={samples}
          heapSamples={heapMetrics.data?.items ?? []}
          pending={metrics.isPending}
          failed={!!metrics.error || !!heapMetrics.error}
        />
      </AppMemoryPools>
    </Card>
  );
}

function SharedMemoryPanels({
  vmName,
  samples,
  heapSamples,
  pending,
  failed,
}: {
  vmName: string;
  samples: MetricSample[];
  heapSamples: MetricSample[];
  pending: boolean;
  failed: boolean;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
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
  const series = buildMemoryPoolSeries(projectRun(samples));
  const heap = buildZephyrHeapSeries(projectRun(heapSamples));
  const heapObservation = sharedMemoryObservation(
    allSamples,
    'memory',
    ['memory.kernel_heap.used_bytes', 'memory.kernel_heap.free_bytes'],
    now,
  );
  const currentHeapView =
    heapObservation.state === 'fresh' && heapObservation.sample
      ? buildZephyrHeapSeries([heapObservation.sample])
      : null;
  const currentHeap = currentHeapView?.current ?? null;
  const pool = (region: 'net_rx_data' | 'net_tx_data'): PoolSnapshot => {
    const view = sharedMemoryObservation(
      allSamples,
      'memory_regions',
      ['memory.region.used_bytes', 'memory.region.free_bytes', 'memory.region.capacity_bytes'],
      now,
      region,
    );
    if (view.state === 'fresh' && view.sample)
      return buildMemoryPoolSeries([view.sample])[region === 'net_rx_data' ? 'rx' : 'tx'];
    return {
      state: 'missing',
      usedBytes: null,
      freeBytes: null,
      capacityBytes: null,
      usagePercent: null,
      reason: view.reason,
    };
  };
  const rx = pool('net_rx_data'),
    tx = pool('net_tx_data');
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
          freeLabel="여유"
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
        aria-label={`${vmName} 공통 네트워크 풀`}
      >
        <h4>MQTT 포함 공통 RX/TX 풀</h4>
        <p className={styles.dim}>네트워크 공용 버퍼이며 MQTT 전용 메모리 사용량이 아닙니다.</p>
        <div className={styles.poolSummaryGrid}>
          <PoolSummary label="RX Pool" pool={rx} tone="rx" />
          <PoolSummary label="TX Pool" pool={tx} tone="tx" />
        </div>

        <MemoryChart
          title="패킷 처리율"
          unit="packets/s"
          ariaLabel={`${vmName} RX TX 패킷 처리율 최근 60초`}
          points={series.packetRates.map((point) => ({
            observedAt: point.observedAt,
            rx: point.rxPacketsPerSecond,
            tx: point.txPacketsPerSecond,
          }))}
        />
        <MemoryChart
          title="Pool 점유율"
          unit="%"
          ariaLabel={`${vmName} RX TX Pool 점유율 최근 60초`}
          max={100}
          points={series.usage.map((point) => ({
            observedAt: point.observedAt,
            rx: point.rxPercent,
            tx: point.txPercent,
          }))}
        />

        <div className={styles.memoryObservation}>
          {observationOf({ ...series, rx, tx }, pending, failed)}
        </div>
      </section>
    </>
  );
}

function PoolSummary({ label, pool, tone }: { label: string; pool: PoolSnapshot; tone: 'rx' | 'tx' }) {
  const usable = pool.state === 'ok';
  return (
    <section className={styles.poolSummary} data-tone={tone}>
      <div className={styles.poolSummaryHead}>
        <span>
          <i aria-hidden="true" />
          {label}
        </span>
        <strong>{usable ? `${pool.usagePercent!.toFixed(1)}%` : '—'}</strong>
      </div>
      <div className={styles.poolAmount}>
        <strong>{usable ? formatKiB(pool.usedBytes) : '—'}</strong>
        <span>/ {usable ? formatKiB(pool.capacityBytes) : '—'}</span>
      </div>
      <div className={styles.poolFree}>
        <span>여유</span>
        <strong>{usable ? formatKiB(pool.freeBytes) : pool.reason}</strong>
      </div>
      <div
        className={styles.poolBar}
        role="progressbar"
        aria-label={`${label} 사용률`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={usable ? Math.round(pool.usagePercent!) : undefined}
      >
        <i style={{ width: `${usable ? Math.min(100, pool.usagePercent!) : 0}%` }} />
      </div>
    </section>
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
        {rxPoints || txPoints ? (
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
              <polyline points={rxPoints} className={styles.memoryRxLine} vectorEffect="non-scaling-stroke" />
            )}
            {txPoints && (
              <polyline points={txPoints} className={styles.memoryTxLine} vectorEffect="non-scaling-stroke" />
            )}
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
        aria-valuenow={clamped == null ? undefined : Math.round(clamped)}
      >
        <i style={{ width: `${clamped ?? 0}%` }} />
      </div>
    </section>
  );
}

function LinuxMemoryPanel({ vm, drive }: { vm: Vm; drive: DriveState | null }) {
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true);
  const samples = metrics.data?.items ?? [];
  const series = buildLinuxMemorySeries(samples, vm);
  const free = linuxFreeMemory(series);
  const disk = buildDiskIoSnapshot(samples);
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
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
          <small>마지막 갱신 {current ? relativeTime(current.observedAt) : '—'}</small>
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
        {line ? (
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
            <polyline points={line} className={styles.linuxMemoryLine} vectorEffect="non-scaling-stroke" />
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

function chartPoints(points: ChartPoint[], key: 'rx' | 'tx', max: number): string {
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

function observationOf(
  series: ReturnType<typeof buildMemoryPoolSeries>,
  pending: boolean,
  failed: boolean,
): string {
  if (failed) return '메트릭 조회 실패 · 관측 이력은 유지하고 만료된 현재값은 비웁니다.';
  if (pending && !series.lastObservedAt) return '메트릭 수신 대기 중입니다.';
  if (series.rx.state === 'invalid' || series.tx.state === 'invalid')
    return '잘못된 Pool 데이터가 감지되었습니다.';
  if (series.rx.state !== 'ok' || series.tx.state !== 'ok') return 'RX/TX Pool 데이터가 없습니다.';
  if (series.rx.freeBytes === 0) return 'RX Pool 여유 없음 · 고갈이 관측되었습니다.';
  return '관측된 Pool 상태를 표시하고 있습니다.';
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

function relativeTime(value: string | null): string {
  if (!value) return '—';
  const elapsed = Math.max(0, Date.now() - Date.parse(value));
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
