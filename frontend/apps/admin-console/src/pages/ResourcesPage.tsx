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
import { bytesToKiB } from '../domain/memoryPools';
import { buildManagedRamSeries } from '../domain/managedRam';
import { ago, osLabel, roleOf, sortVms, UNASSIGNED_COLOR, type Role } from '../domain/roles';
import { vmAuthorityOf } from '../domain/vmAuthority';
import { observedControl } from '../domain/driveObservations';
import { useDrive } from '../layout/DriveContext';
import { AppMemoryPools } from './AppMemoryPools';
import { ManagedRamSummary } from './ManagedRamSummary';
import { sharedMemoryObservation } from '../domain/sharedMemoryObservation';
import { LiveDataAlert } from '../layout/LiveDataAlert';
import { useNow } from '../useNow';
import { MemoryBarRow } from './MemoryBarRow';
import { MemoryUsageSummary } from './MemoryUsageSummary';
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
  const now = useNow();
  // 마지막 갱신 = 관리 RAM 원 관측 시각 (Linux 카드의 메모리 표본 시각과 같은 자리)
  const { lastObservationAt } = buildManagedRamSeries(samples, vm.vm_id, now);
  const { socket } = useDrive();
  // Overview·VM 상세와 같이 소켓이 열려 있고 control 이 신선할 때만 ACTIVE/STANDBY 를 판정한다
  const authority = vmAuthorityOf(role, vm, observedControl(drive, socket)?.active_vm_id);
  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR } as CSSProperties;

  return (
    <Card
      className={styles.memoryVmCard}
      style={style}
      title={
        <span className={styles.memoryVmTitle}>
          <strong>{vm.name}</strong>
          <small>{role?.short ?? '역할 미지정'} · Zephyr</small>
        </span>
      }
      actions={
        <span className={styles.memoryCardActions}>
          <span className={styles.authority} data-authority={authority.toLowerCase()}>
            {authority}
          </span>
          <small>
            마지막 갱신 {lastObservationAt == null ? '—' : ago(Math.max(0, now - lastObservationAt))}
          </small>
        </span>
      }
    >
      <div className={styles.memoryCardBody}>
        {(metrics.error || heapMetrics.error) && <MetricErrorNotice />}
        <ManagedRamSummary vmId={vm.vm_id} vmName={vm.name} samples={samples} />
        <AppMemoryPools vmId={vm.vm_id} vmName={vm.name} samples={samples}>
          <SharedMemoryPanels
            vmName={vm.name}
            samples={samples}
            heapSamples={heapMetrics.data?.items ?? []}
          />
        </AppMemoryPools>
      </div>
    </Card>
  );
}

/**
 * 공통 그룹의 커널 힙. 차량 관측 링 등 다른 앱 버퍼 영역과 같은 형태(이름 + %, 사용/전체, 얇은 막대, 현재 여유)로 보인다.
 * 현재값은 관측 문맥·만료 규칙(sharedMemoryObservation)으로 구한다
 */
function SharedMemoryPanels({
  vmName,
  samples,
  heapSamples,
}: {
  vmName: string;
  samples: MetricSample[];
  heapSamples: MetricSample[];
}) {
  const now = useNow();
  // 5분 힙 이력과 60초 표본을 함께 넘겨, 60초 쪽에서 처음 보인 새 관측 세션도 존중한다
  const allSamples = [...heapSamples, ...samples];
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
  const expired = heapObservation.state === 'expired' && currentHeap != null;
  const details = currentHeap
    ? [
        {
          label: '사용 / 전체',
          value: `${formatKiB(currentHeap.usedBytes)} / ${formatKiB(currentHeap.totalBytes)}`,
        },
        { label: expired ? '관측 당시 여유' : '현재 여유', value: formatKiB(currentHeap.freeBytes) },
        ...(currentHeap.highWaterBytes != null
          ? [{ label: '최고 점유 · 부팅 후', value: formatKiB(currentHeap.highWaterBytes) }]
          : []),
      ]
    : [];
  // 정상(fresh) 관측이 아니면 사유를 펼친 내용에 (관측 만료 · 최근 관측값 등)
  const notes =
    heapObservation.state !== 'fresh' || !currentHeap
      ? [currentHeap ? heapObservation.reason : (currentHeapView?.reason ?? heapObservation.reason)]
      : [];
  return (
    <MemoryBarRow
      name="커널 힙"
      label={`${vmName} 공통 커널 힙`}
      barLabel={`${vmName} 커널 힙 사용률`}
      percent={currentHeap?.percent ?? null}
      expired={expired}
      details={details}
      notes={expired ? [] : notes}
      problems={expired ? ['관측 만료 · 최근 관측값'] : []}
    />
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
      <div className={styles.memoryCardBody}>
        {metrics.error && <MetricErrorNotice />}
        {notice && <div className={styles.memoryNotice}>{notice}</div>}

        <MemoryUsageSummary
          label="메모리 사용률"
          ariaLabel={`${vm.name} 메모리 사용률`}
          percent={current?.percent ?? null}
          usedText={formatMiBOrDash(current?.usedBytes ?? null)}
          totalText={formatMiBOrDash(series.totalBytes)}
          freeLabel={free.basis === 'estimated' ? '여유 (추정)' : '여유'}
          freeText={formatMiBOrDash(free.bytes)}
          freeTitle={freeTitle}
        />

        <section className={styles.memoryChartBlock} aria-label={`${vm.name} 디스크 I/O`}>
          <header>
            <strong>디스크 I/O</strong>
          </header>
          <div className={styles.diskIoGrid}>
            <DiskIoItem label="읽기" counter={disk.read} />
            <DiskIoItem label="쓰기" counter={disk.write} />
          </div>
        </section>
      </div>
    </Card>
  );
}

/** Network 탭 표처럼 작은 회색 라벨 + 큰 숫자(단위는 작게) + 아래 속도 */
function DiskIoItem({ label, counter }: { label: string; counter: DiskIoCounter }) {
  const [number, unit] = counter.totalBytes == null ? ['—', ''] : formatBytes(counter.totalBytes).split(' ');
  return (
    <div className={styles.diskIoItem}>
      <span>{label} 누적</span>
      <strong>
        {number}
        {unit && <small>{unit}</small>}
      </strong>
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
