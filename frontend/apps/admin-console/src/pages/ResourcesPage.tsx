import { Card } from '@xenova/ui';
import type { CSSProperties } from 'react';
import type { DriveState } from '../api/drive';
import { useMetrics, useVms } from '../api/queries';
import type { Vm } from '../api/types';
import { buildCpuChartScale, cpuChartY, formatCpuTick } from '../domain/cpuChart';
import {
  buildLinuxMemorySeries,
  buildZephyrHeapSeries,
  cpuPercentOf,
  type LinuxMemoryPoint,
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
          <span>Zephyr 최근 60초 · 힙·Pool</span>
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
  const samples = metrics.data?.items ?? [];
  const series = buildMemoryPoolSeries(samples);
  const heap = buildZephyrHeapSeries(samples);
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
  const lastUpdated = relativeTime(series.lastObservedAt);
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
          <small>{role?.short ?? '역할 미지정'} · Zephyr · 네트워크 데이터 Pool</small>
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
      {metrics.error && (
        <div className={styles.memoryNotice} data-tone="fault">
          메트릭 연결 오류
        </div>
      )}
      <MemoryUsageSummary
        vmName={vm.name}
        label="VM 메모리 사용률"
        current={null}
        capacity={vm.memory_mib * MIB_BYTES}
        freeBytes={null}
        format={formatMiB}
      />
      <MemoryUsageChart
        vmName={vm.name}
        title="VM 메모리 사용률"
        points={[]}
        range="60초"
        emptyMessage="Zephyr VM 전체 메모리 사용량은 현재 계측되지 않습니다."
      />
      <MemoryUsageSummary
        vmName={vm.name}
        label="커널 힙 사용률"
        current={heap.current}
        capacity={heap.current ? heap.current.usedBytes + heap.current.freeBytes : null}
        freeBytes={heap.current?.freeBytes ?? null}
        format={formatKiB}
      />
      <MemoryUsageChart vmName={vm.name} title="커널 힙 사용률" points={heap.points} range="60초" />
      <div className={styles.poolSummaryGrid}>
        <PoolSummary label="RX Pool" pool={series.rx} tone="rx" />
        <PoolSummary label="TX Pool" pool={series.tx} tone="tx" />
      </div>

      <MemoryChart
        title="패킷 처리율"
        unit="packets/s"
        ariaLabel={`${vm.name} RX TX 패킷 처리율 최근 60초`}
        points={series.packetRates.map((point) => ({
          observedAt: point.observedAt,
          rx: point.rxPacketsPerSecond,
          tx: point.txPacketsPerSecond,
        }))}
      />
      <MemoryChart
        title="Pool 점유율"
        unit="%"
        ariaLabel={`${vm.name} RX TX Pool 점유율 최근 60초`}
        max={100}
        points={series.usage.map((point) => ({
          observedAt: point.observedAt,
          rx: point.rxPercent,
          tx: point.txPercent,
        }))}
      />

      <div className={styles.memoryObservation}>
        {observationOf(series, metrics.isPending, !!metrics.error)}
      </div>
    </Card>
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

function LinuxMemoryPanel({ vm, drive }: { vm: Vm; drive: DriveState | null }) {
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true);
  const series = buildLinuxMemorySeries(metrics.data?.items ?? [], vm);
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
  const style = {
    '--vm-accent': role?.color ?? UNASSIGNED_COLOR,
  } as CSSProperties;
  const current = series.current;
  const freeBytes = current ? Math.max(0, series.totalBytes - current.usedBytes) : null;
  const notice = metrics.error
    ? '메트릭 연결 오류 · 마지막 정상 데이터가 있으면 그대로 표시합니다.'
    : metrics.isPending && !current
      ? '메모리 메트릭 수신 대기 중입니다.'
      : !current
        ? '메모리 데이터 없음'
        : null;

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
      {notice && (
        <div className={styles.memoryNotice} data-tone={metrics.error ? 'fault' : undefined}>
          {notice}
        </div>
      )}

      <MemoryUsageSummary
        vmName={vm.name}
        label="메모리 사용률"
        current={current}
        capacity={series.totalBytes}
        freeBytes={freeBytes}
        format={formatMiB}
      />
      <MemoryUsageChart vmName={vm.name} title="메모리 사용률" points={series.points} range="5분" />

      <div className={styles.memoryObservation}>
        {current
          ? `할당 ${vm.memory_mib.toLocaleString('ko-KR')} MiB 중 ${formatMiB(current.usedBytes)} 사용 중`
          : 'Linux guest-agent의 memory_used_bytes 표본을 기다리고 있습니다.'}
      </div>
    </Card>
  );
}

function MemoryUsageSummary({
  vmName,
  label,
  current,
  capacity,
  freeBytes,
  format,
}: {
  vmName: string;
  label: string;
  current: LinuxMemoryPoint | null;
  capacity: number | null;
  freeBytes: number | null;
  format: (bytes: number) => string;
}) {
  const percent = current ? Math.max(0, Math.min(100, current.percent)) : null;
  return (
    <section className={styles.linuxMemorySummary}>
      <div className={styles.linuxMemorySummaryHead}>
        <span>{label}</span>
        <strong>{current ? `${current.percent.toFixed(1)}%` : '—'}</strong>
      </div>
      <div className={styles.linuxMemoryAmount}>
        <strong>{current ? format(current.usedBytes) : '—'}</strong>
        <span>
          {current || capacity == null ? '/ ' : '할당 '}
          {capacity == null ? '—' : format(capacity)}
        </span>
      </div>
      <div className={styles.linuxMemoryFree}>
        <span>여유</span>
        <strong>{freeBytes == null ? '—' : format(freeBytes)}</strong>
      </div>
      <div
        className={styles.linuxMemoryBar}
        role="progressbar"
        aria-label={`${vmName} ${label}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent == null ? undefined : Math.round(percent)}
      >
        <i style={{ width: `${percent ?? 0}%` }} />
      </div>
    </section>
  );
}

function MemoryUsageChart({
  vmName,
  title,
  points,
  range,
  emptyMessage = '데이터 없음',
}: {
  vmName: string;
  title: string;
  points: LinuxMemoryPoint[];
  range: '5분' | '60초';
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
        <strong>{title}</strong>
        <span className={styles.linuxMemoryLegend}>
          <i />
          Used %
        </span>
      </header>
      <div className={styles.memoryChart}>
        {line ? (
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            role="img"
            aria-label={`${vmName} ${title} 최근 ${range} 그래프`}
          >
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
          <span>{range} 전</span>
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
  if (failed) return '메트릭을 가져오지 못했습니다. 마지막 정상 표본이 있으면 그대로 유지합니다.';
  if (pending && !series.lastObservedAt) return '메트릭 수신 대기 중입니다.';
  if (series.rx.state === 'invalid' || series.tx.state === 'invalid')
    return '잘못된 Pool 데이터가 감지되었습니다.';
  if (series.rx.state !== 'ok' || series.tx.state !== 'ok') return 'RX/TX Pool 데이터가 없습니다.';
  if (series.rx.freeBytes === 0) return 'RX Pool 여유 없음 · 고갈이 관측되었습니다.';
  if ((series.counters.rxDrop ?? 0) > 0) {
    return `RX Pool 점유 ${series.rx.usagePercent!.toFixed(1)}% · 최근 구간 RX drop ${series.counters.rxDrop}회`;
  }
  if ((series.counters.txDrop ?? 0) > 0) return `최근 구간 TX drop ${series.counters.txDrop}회`;
  if ((series.counters.poolBusy ?? 0) > 0) return `최근 구간 TX 페이지 부족 ${series.counters.poolBusy}회`;
  if ((series.counters.protocolErrors ?? 0) > 0)
    return `최근 구간 프로토콜 오류 ${series.counters.protocolErrors}회`;
  const latestRate = series.packetRates.at(-1);
  if ((latestRate?.rxPacketsPerSecond ?? 0) > 0 || (latestRate?.txPacketsPerSecond ?? 0) > 0) {
    return '패킷이 처리되고 버퍼가 반환되고 있습니다.';
  }
  return '관측된 Pool 상태를 표시하고 있습니다.';
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
