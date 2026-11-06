import { Card } from '@xenova/ui';
import type { CSSProperties } from 'react';
import type { DriveState } from '../api/drive';
import { useMetrics, useVms } from '../api/queries';
import type { Vm } from '../api/types';
import { buildCpuChartScale, cpuChartY, formatCpuTick } from '../domain/cpuChart';
import {
  buildDiskIoSnapshot,
  buildLinuxMemorySeries,
  buildZephyrHeapSeries,
  cpuPercentOf,
  linuxFreeMemory,
} from '../domain/vmMetrics';
import {
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
import { CpuWorkloadList } from './CpuWorkloadList';
import { MetricErrorNotice } from './MetricErrorNotice';
import { NetworkDashboard } from './NetworkDashboard';
import styles from './pages.module.css';
import { ByteCounterItem } from './resourceCharts';
import { chartPoints, formatKiB, formatMiB, formatMiBOrDash, relativeTime } from './resourceFormat';

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
    id: 'message-board',
    title: ROLES['message-board'].title,
    sub: 'Debian 13',
    color: ROLES['message-board'].color,
    dedicated: false,
  },
];
const CORES = [0, 1, 2, 3];

/** RC카 시나리오 §2: 자율·수동은 전용 1코어씩, 네비게이션은 남은 코어 */
const PINS: Record<Domain, number[]> = { dom0: [0], autonomous: [1], manual: [2], 'message-board': [3] };
const WEIGHT: Record<Domain, number> = { dom0: 512, autonomous: 512, manual: 512, 'message-board': 256 };
const DOM0_MEMORY_MIB = 1024;

export function ResourcesPage({ view = 'all' }: { view?: 'cpu' | 'memory' | 'network' | 'all' }) {
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

  if (view === 'network') {
    return <NetworkDashboard items={vms.data?.items} drive={drive} pending={vms.isPending} />;
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
          <span>Zephyr 최근 5분</span>
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
  const heapMetrics = useMetrics(vm.vm_id, true);
  const heap = buildZephyrHeapSeries(heapMetrics.data?.items ?? []);
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
  const heapCurrent = heap.current;
  const lastUpdated = relativeTime(heapCurrent?.observedAt ?? null);
  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR } as CSSProperties;

  return (
    <Card
      className={styles.memoryVmCard}
      style={style}
      title={
        <span className={styles.memoryVmTitle}>
          <strong>{vm.name}</strong>
          <small>{role?.short ?? '역할 미지정'} · Zephyr · 커널 heap</small>
        </span>
      }
      actions={
        <span className={styles.memoryCardActions}>
          <span className={styles.authority} data-authority={authority.toLowerCase()}>
            {authority}
          </span>
          <small>마지막 갱신 {lastUpdated}</small>
        </span>
      }
    >
      {heapMetrics.error && <MetricErrorNotice />}

      <MemoryUsageSummary
        label="메모리 사용률"
        badge={HEAP_BASIS_BADGE}
        ariaLabel={`${vm.name} 메모리 사용률(커널 heap 기준)`}
        percent={heapCurrent?.percent ?? null}
        usedBytes={heapCurrent?.usedBytes ?? null}
        totalBytes={heapCurrent?.totalBytes ?? null}
        freeLabel="여유"
        freeBytes={heapCurrent?.freeBytes ?? null}
        freeNote={heapCurrent ? null : heapMetrics.isPending ? '수신 대기' : heap.reason}
        format={formatKiB}
      />
      <UsageChart
        title="메모리 사용률"
        badge={HEAP_BASIS_BADGE}
        ariaLabel={`${vm.name} 메모리 사용률(커널 heap 기준) 최근 5분 그래프`}
        points={heap.points}
      />
    </Card>
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
          <strong title="장치 합산 · 같은 부팅 구간에서만 속도를 계산합니다">디스크 I/O</strong>
        </header>
        <div className={styles.counterGrid}>
          <ByteCounterItem label="읽기" counter={disk.read} />
          <ByteCounterItem label="쓰기" counter={disk.write} />
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

/** 최근 5분 사용률(%) 선 그래프. Linux 메모리와 Zephyr 커널 heap 이 함께 쓴다 */
function UsageChart({
  title,
  badge,
  ariaLabel,
  points,
}: {
  title: string;
  badge?: BasisBadge;
  ariaLabel: string;
  points: readonly { observedAt: string; percent: number }[];
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
          <div className={styles.memoryChartEmpty}>데이터 없음</div>
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

      <CpuWorkloadList vm={vm} />
    </Card>
  );
}
