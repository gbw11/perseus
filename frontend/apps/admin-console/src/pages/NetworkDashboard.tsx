import { Card, StatusBadge, type Health } from '@xenova/ui';
import type { CSSProperties } from 'react';
import type { DriveState } from '../api/drive';
import { LIVE_POLL_MS, useMetrics } from '../api/queries';
import type { AgentStatus, Vm } from '../api/types';
import { buildPacketRateSeries, bytesToKiB, poolLevel } from '../domain/memoryPools';
import {
  buildLinuxNetworkSeries,
  buildRxByteRateSeries,
  buildZephyrByteSeries,
  interfaceFlagOf,
  mergeRxRateFallback,
  withRateFallback,
  ZEPHYR_CARRIER_METRIC,
  ZEPHYR_INTERFACE_UP_METRIC,
  type InterfaceFlag,
} from '../domain/networkMetrics';
import { networkPoolView, type NetworkPool } from '../domain/networkPools';
import { osLabel, roleOf, sortVms, UNASSIGNED_COLOR } from '../domain/roles';
import { vmAuthorityOf } from '../domain/vmAuthority';
import type { DiskIoCounter } from '../domain/vmMetrics';
import { AGENT_LABEL } from '../format';
import { LiveDataAlert } from '../layout/LiveDataAlert';
import { useNow } from '../useNow';
import { MetricErrorNotice } from './MetricErrorNotice';
import styles from './pages.module.css';
import { NetTable, RxTxChart, type NetCell, type NetRow } from './resourceCharts';
import { bytesParts, formatBytes, formatKiB, latestTime, relativeTime } from './resourceFormat';

const AGENT_HEALTH: Record<AgentStatus, Health> = {
  ONLINE: 'ok',
  STALE: 'recovering',
  OFFLINE: 'fault',
  UNKNOWN: 'stopped',
};

const RX_TX_ACCENTS = {
  '--rx-accent': 'var(--x-primary)',
  '--tx-accent': 'var(--x-pool-tx)',
};

const formatRate = (value: number) => `${formatBytes(value)}/s`;
const formatPercent = (value: number) => `${value.toFixed(1)}%`;

/** W-06 Network 탭. Zephyr 는 netfront Pool·패킷·바이트, Linux 는 송수신 바이트와 Agent 상태 */
export function NetworkDashboard({
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
          <span className={styles.contentEyebrow}>RESOURCE · NETWORK</span>
          <h1>Network</h1>
        </div>
        <div className={styles.memoryHeadingMeta}>
          <span>Linux 최근 5분</span>
          <span>Zephyr 최근 60초</span>
        </div>
      </header>

      {pending && <div className={styles.statePanel}>VM 정보를 불러오는 중입니다…</div>}
      <LiveDataAlert />

      <div className={styles.memoryVmGrid}>
        {vms.map((vm) =>
          vm.guest_kind === 'zephyr' ? (
            <ZephyrNetworkPanel key={vm.vm_id} vm={vm} drive={drive} />
          ) : (
            <LinuxNetworkPanel key={vm.vm_id} vm={vm} drive={drive} />
          ),
        )}
      </div>
    </div>
  );
}

function ZephyrNetworkPanel({ vm, drive }: { vm: Vm; drive: DriveState | null }) {
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true, 1, LIVE_POLL_MS, { keepLastData: true });
  const now = useNow();
  const samples = metrics.data?.items ?? [];
  // Pool 은 Memory 탭(origin/frontend)과 같은 관측 문맥 규칙, 패킷은 팀원 쪽 buildPacketRateSeries
  const pools = networkPoolView(samples, now);
  const packets = buildPacketRateSeries(samples).at(-1);
  const bytes = buildZephyrByteSeries(samples);
  const rxRate = buildRxByteRateSeries(samples);
  // RX 속도는 counter_value 차분을 우선하고, 못 구할 때(첫 표본·세대 바뀜 등)만 플랫폼 계측값을 쓴다
  const rx = withRateFallback(bytes.rx, rxRate.current);
  const ratePoints = mergeRxRateFallback(bytes.rates, rxRate.points);
  const interfaceUp = interfaceFlagOf(samples, ZEPHYR_INTERFACE_UP_METRIC);
  const carrier = interfaceFlagOf(samples, ZEPHYR_CARRIER_METRIC);
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
  const lastUpdated = relativeTime(latestTime(pools.lastObservedAt, bytes.observedAt));
  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR, ...RX_TX_ACCENTS } as CSSProperties;

  return (
    <Card
      className={styles.memoryVmCard}
      style={style}
      title={
        <span className={styles.memoryVmTitle}>
          <strong>{vm.name}</strong>
          <small>{role?.short ?? '역할 미지정'} · Zephyr · netfront</small>
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
        <MetricErrorNotice detail="최근 관측값·이력을 표시하며, Pool 만료 여부는 영역별로 구분합니다." />
      )}

      <NetTable
        label={`${vm.name} 네트워크 수치`}
        rows={[
          ...byteRows(rx, bytes.tx),
          {
            label: '패킷',
            rx: packetCell(packets?.rxPacketsPerSecond),
            tx: packetCell(packets?.txPacketsPerSecond),
          },
          { label: 'Pool', rx: poolCell(pools.rx), tx: poolCell(pools.tx) },
        ]}
      />

      <RxTxChart
        title="송수신 속도"
        unit="B/s"
        ariaLabel={`${vm.name} 송수신 속도 최근 60초`}
        points={ratePoints}
        formatMax={formatRate}
        formatValue={formatRate}
      />
      <RxTxChart
        title="Pool 점유율"
        unit="%"
        ariaLabel={`${vm.name} RX TX Pool 점유율 최근 60초`}
        max={100}
        formatValue={formatPercent}
        points={pools.usage.map((point) => ({
          observedAt: point.observedAt,
          rx: point.rxPercent,
          tx: point.txPercent,
        }))}
      />

      <section className={styles.memoryChartBlock} aria-label={`${vm.name} 인터페이스 상태`}>
        <header>
          <strong>인터페이스 상태</strong>
        </header>
        <div className={styles.counterGrid}>
          <FlagItem label="인터페이스" flag={interfaceUp} on="UP" off="DOWN" />
          <FlagItem label="링크(carrier)" flag={carrier} on="연결" off="끊김" />
        </div>
      </section>
    </Card>
  );
}

function LinuxNetworkPanel({ vm, drive }: { vm: Vm; drive: DriveState | null }) {
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true, 5, LIVE_POLL_MS, { keepLastData: true });
  const series = buildLinuxNetworkSeries(metrics.data?.items ?? []);
  const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
  const agent = vm.agent_status ?? 'UNKNOWN';
  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR, ...RX_TX_ACCENTS } as CSSProperties;

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
          <span className={styles.networkCardBadges}>
            <StatusBadge health={AGENT_HEALTH[agent]} label={`Agent ${AGENT_LABEL[agent]}`} size="sm" />
            <span className={styles.authority} data-authority={authority.toLowerCase()}>
              {authority}
            </span>
          </span>
          <small>마지막 갱신 {relativeTime(series.observedAt)}</small>
        </span>
      }
    >
      {metrics.error && <MetricErrorNotice />}
      {!metrics.error && !series.observedAt && (
        <div className={styles.memoryNotice}>
          {metrics.isPending ? '네트워크 메트릭 수신 대기 중입니다.' : '네트워크 데이터 없음'}
        </div>
      )}

      <NetTable label={`${vm.name} 네트워크 수치`} rows={byteRows(series.rx, series.tx)} />

      <RxTxChart
        title="송수신 속도"
        unit="B/s"
        ariaLabel={`${vm.name} 송수신 속도 최근 5분 그래프`}
        points={series.rates}
        span="5분"
        formatMax={formatRate}
        formatValue={formatRate}
      />
    </Card>
  );
}

/** 속도 / 누적 두 행 */
function byteRows(rx: DiskIoCounter, tx: DiskIoCounter): NetRow[] {
  const speed = (counter: DiskIoCounter): NetCell => {
    if (counter.bytesPerSecond == null) {
      return { number: null, note: counter.totalBytes == null ? counter.reason : '속도 계산 대기' };
    }
    const parts = bytesParts(counter.bytesPerSecond);
    return { number: parts.number, unit: `${parts.unit}/s` };
  };
  const total = (counter: DiskIoCounter): NetCell => {
    if (counter.totalBytes == null) return { number: null, note: counter.reason };
    const parts = bytesParts(counter.totalBytes);
    return { number: parts.number, unit: parts.unit };
  };
  return [
    { label: '속도', rx: speed(rx), tx: speed(tx) },
    { label: '누적', rx: total(rx), tx: total(tx) },
  ];
}

function packetCell(value: number | null | undefined): NetCell {
  return value == null
    ? { number: null, note: '데이터 없음' }
    : { number: value.toLocaleString('ko-KR', { maximumFractionDigits: 1 }), unit: '/s' };
}

/**
 * Pool: % 를 크게, 아래 작게 "사용 / 전체 KiB". 80% 이상 주의, 95% 이상 위험.
 * 관측이 만료됐으면 최근 관측값 앞에 "관측 만료" 를 붙인다 (Memory 탭 규칙과 같음)
 */
function poolCell(pool: NetworkPool): NetCell {
  if (pool.state !== 'ok') return { number: null, note: pool.reason };
  const amount = `${kibNumber(pool.usedBytes!)} / ${formatKiB(pool.capacityBytes)}`;
  return {
    number: pool.usagePercent!.toFixed(1),
    unit: '%',
    note: pool.expired ? `관측 만료 · ${amount}` : amount,
    level: poolLevel(pool.usagePercent),
  };
}

/** 인터페이스 상태 박스 한 칸. 값이 없으면 "—" + 사유 (지금은 "데이터 없음 (백엔드 미지원)") */
function FlagItem({ label, flag, on, off }: { label: string; flag: InterfaceFlag; on: string; off: string }) {
  return (
    <div className={styles.counterItem} data-active={flag.state === 'off' ? 'true' : undefined}>
      <span>{label}</span>
      <strong>{flag.state === 'on' ? on : flag.state === 'off' ? off : '—'}</strong>
      <small>{flag.state === 'missing' ? flag.reason : relativeTime(flag.observedAt)}</small>
    </div>
  );
}

function kibNumber(bytes: number): string {
  return bytesToKiB(bytes).toLocaleString('ko-KR', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
}
