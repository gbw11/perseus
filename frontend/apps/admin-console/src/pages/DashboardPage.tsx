import type { Health } from '@xenova/ui';
import type { CSSProperties } from 'react';
import { Link } from 'react-router';
import type { DriveState, SocketState } from '../api/drive';
import { useEvents, useMetrics, useTelemetryHeartbeat, LIVE_POLL_MS, useVms } from '../api/queries';
import type { Vm, VmEvent } from '../api/types';
import { cpuPercentOf, latest, memoryPercentOf } from '../domain/vmMetrics';
import { observedControl, vmAppObservations } from '../domain/driveObservations';
import { clock, describe } from '../domain/events';
import { publisherObservation } from '../domain/telemetryPublisher';
import { buildManagedRamSeries, managedRamReason } from '../domain/managedRam';
import { vmAuthorityOf } from '../domain/vmAuthority';
import { ago, MODE_LABEL, osLabel, roleOf, sortVms, UNASSIGNED_COLOR } from '../domain/roles';
import { AGENT_LABEL, STATE_HEALTH, STATE_LABEL } from '../format';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';
import { LiveDataAlert } from '../layout/LiveDataAlert';
import { useNow } from '../useNow';

/**
 * VM Dashboard / Overview.
 * API VM 목록을 그대로 카드로 그리고 VM 이름으로 부른다. 역할은 vm.role 이 있을 때만 표시한다.
 * 두 주행 VM은 제어권(Active/Standby)과 주행 모드(AUTO/MANUAL)를 분리한다.
 */
export function DashboardPage() {
  const { drive, socket } = useDrive();
  // 실패해도 카드를 남기고 카드가 '조회 불가' 로 표시한다
  const vms = useVms(LIVE_POLL_MS, { keepLastData: true });
  const events = useEvents({ limit: 30 });
  const now = useNow();
  const items = sortVms(vms.data?.items ?? []);
  const control = observedControl(drive, socket);
  const activeVm = items.find((vm) => vm.vm_id === control?.active_vm_id);
  const authority =
    control && !control.active_vm_id && !control.active_app ? '없음' : (activeVm?.name ?? '미확인');

  return (
    <div className={styles.overviewPage}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>OVERVIEW</span>
          <h2>Virtual Machines</h2>
        </div>
      </header>

      <section className={styles.overviewSummary} aria-label="주행 및 관측 요약">
        <OverviewSummaryItem label="DRIVE MODE" value={control?.mode ? MODE_LABEL[control.mode] : '미확인'} />
        <OverviewSummaryItem label="CONTROL AUTHORITY" value={authority} />
        <OverviewSummaryItem label="LAST OBSERVED" value={overviewObserved(vms.data?.observed_at)} />
      </section>

      {vms.isPending && <div className={styles.statePanel}>VM 상태를 불러오는 중입니다…</div>}
      <LiveDataAlert />

      {vms.isSuccess && items.length === 0 && <div className={styles.statePanel}>등록된 VM이 없습니다.</div>}

      <div className={styles.vmGrid}>
        {items.map((vm) => (
          <VmOverviewCard
            key={vm.vm_id}
            vm={vm}
            drive={drive}
            socket={socket}
            now={now}
            vmStatusError={vms.isError}
            latestEvent={overviewLatestEvent(events.data?.items, vm.vm_id)}
          />
        ))}
      </div>
    </div>
  );
}

function OverviewSummaryItem({ label, value, tone }: { label: string; value: string; tone?: Health }) {
  return (
    <div className={styles.summaryItem} data-tone={tone}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

const AGENT_LINK_TONE = {
  ONLINE: 'ok',
  STALE: 'recovering',
  OFFLINE: 'fault',
  UNKNOWN: 'stopped',
} satisfies Record<NonNullable<Vm['agent_status']>, Health>;

function VmOverviewCard({
  vm,
  drive,
  socket,
  now,
  vmStatusError,
  latestEvent,
}: {
  vm: Vm;
  drive: DriveState | null;
  socket: SocketState;
  now: number;
  vmStatusError: boolean;
  latestEvent: VmEvent | undefined;
}) {
  const role = roleOf(vm, drive);
  const linux = vm.guest_kind === 'linux';
  // 현재값 카드에는 Memory 탭과 같은 짧은 이력·조회 주기를 사용한다.
  const metrics = useMetrics(vm.vm_id, true, linux ? 5 : 1, LIVE_POLL_MS, { keepLastData: true });
  const heartbeat = useTelemetryHeartbeat(linux ? '' : vm.vm_id);
  const publisher = publisherObservation(vm.vm_id, heartbeat);
  // LINK tracks heartbeat freshness; publisher collection health stays in the detailed diagnostics.
  const telemetryFresh = !heartbeat.isError && !heartbeat.isPending && publisher.report?.status === 'FRESH';
  const agent = vm.agent_status ?? 'UNKNOWN';
  const linkLabel = linux
    ? vmStatusError
      ? '조회 불가'
      : AGENT_LABEL[agent]
    : telemetryFresh
      ? '연결됨'
      : publisher.label;
  const linkTone = linux
    ? vmStatusError
      ? 'fault'
      : AGENT_LINK_TONE[agent]
    : telemetryFresh
      ? 'ok'
      : publisher.tone;
  const telemetryAge = !heartbeat.isError && !heartbeat.isPending ? publisher.report?.age_ms : null;
  const samples = metrics.data?.items ?? [];
  const cpu = latest(samples, cpuPercentOf);
  const ram = linux ? null : buildManagedRamSeries(samples, vm.vm_id, now);
  const ramDisplay = ram?.recentObservation ?? ram?.current;
  const memory = linux
    ? latest(samples, (sample) => memoryPercentOf(sample, vm))
    : ramDisplay?.status === 'ok'
      ? Number(ramDisplay.percent)
      : null;
  const apps = vmAppObservations(vm.vm_id, drive, socket);
  const authority = vmAuthorityOf(role, vm, observedControl(drive, socket)?.active_vm_id);
  const eventSummary = latestEvent
    ? `${clock(latestEvent.occurred_at)} · ${describe(latestEvent, () => vm.name).title}`
    : '최근 중요 이벤트 없음';
  const unavailable = overviewMetricUnavailable(metrics.isPending, samples.length);
  const driving = role ? role.driving : vm.guest_kind === 'zephyr';

  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR } as CSSProperties;
  return (
    <Link className={styles.vmCard} style={style} to={`/dashboard/vms/${vm.vm_id}`}>
      <div className={styles.vmCardTop}>
        <div>
          <span className={styles.vmKicker}>{role?.title ?? '역할 미지정'}</span>
          <h3>{vm.name}</h3>
          <p>
            {driving ? 'Drive' : 'Infotainment'} · {osLabel(vm)}
          </p>
        </div>
        <span className={styles.authority} data-authority={authority.toLowerCase()}>
          {authority}
        </span>
      </div>

      <div className={styles.vmStatusRows}>
        <OverviewStatusLine label="VM" value={STATE_LABEL[vm.state]} tone={STATE_HEALTH[vm.state]} />
        <OverviewStatusLine label="APP" labelTitle="앱 상태" value={overviewAppSignal(apps, 'phaseLabel')} />
        <OverviewStatusLine
          label="LINK"
          labelTitle={linux ? 'Linux Agent heartbeat (v1)' : 'Zephyr Telemetry heartbeat (v2)'}
          value={linkLabel}
          tone={linkTone}
          detail={linux ? 'Agent' : `Telemetry${telemetryAge == null ? '' : ` · ${ago(telemetryAge)}`}`}
        />
      </div>

      <div className={styles.resourceBlock}>
        <OverviewMetricBar
          label="CPU"
          value={cpu}
          unavailable={metrics.isError ? '메트릭 조회 불가' : unavailable}
        />
        <OverviewMetricBar
          label="Memory"
          value={memory}
          precision={linux ? 0 : 3}
          unavailable={
            metrics.isError
              ? '메트릭 조회 불가'
              : (unavailable ??
                (ram && ram.current.status !== 'ok'
                  ? ram.recentObservation
                    ? '관측 만료 · 최근 관측값'
                    : managedRamReason(ram.current.reason)
                  : null))
          }
          detail={
            linux
              ? `${vm.memory_mib} MiB allocated`
              : `정적 예약 포함${ram?.lastObservationAt == null ? '' : ` · ${ago(Math.max(0, now - ram.lastObservationAt))}`}`
          }
        />
      </div>

      <div className={styles.vmCardFoot}>
        <div>
          <span>RECENT SIGNAL</span>
          <p>{eventSummary}</p>
        </div>
        <strong>View details ↗</strong>
      </div>
    </Link>
  );
}

function OverviewStatusLine({
  label,
  labelTitle,
  value,
  tone,
  detail,
}: {
  label: string;
  labelTitle?: string;
  value: string;
  tone?: Health;
  detail?: string;
}) {
  return (
    <div className={styles.statusLine} data-tone={tone}>
      <span title={labelTitle}>{label}</span>
      <strong>
        <i aria-hidden="true" /> {value}
      </strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function OverviewMetricBar({
  label,
  value,
  precision = 0,
  unavailable,
  detail,
}: {
  label: string;
  value: number | null;
  precision?: number;
  unavailable: string | null;
  detail?: string;
}) {
  const safeValue = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className={styles.metricRow}>
      <div>
        <span>{label}</span>
        <strong>{value == null ? '—' : `${value.toFixed(precision)}%`}</strong>
      </div>
      <div
        className={styles.metricTrack}
        aria-label={`${label} ${value == null ? '데이터 없음' : `${value.toFixed(precision)}%`}`}
      >
        <i style={{ width: `${safeValue}%` }} />
      </div>
      <small>{unavailable ?? detail ?? '\u00a0'}</small>
    </div>
  );
}

function overviewAppSignal(
  reports: ReturnType<typeof vmAppObservations>,
  field: 'phaseLabel' | 'heartbeatLabel',
): string {
  const labels = new Set(reports.map((report) => report[field]));
  if (labels.size === 0) return '미확인';
  if (labels.size === 1) return reports[0]![field];
  return reports.map((report) => `${report.appId}: ${report[field]}`).join(' · ');
}

function overviewMetricUnavailable(pending: boolean, sampleCount: number): string | null {
  if (pending) return 'Loading metrics';
  if (sampleCount === 0) return 'No metric data';
  return null;
}

function overviewLatestEvent(events: VmEvent[] | undefined, vmId: string | undefined): VmEvent | undefined {
  return vmId ? events?.find((event) => event.vm_id === vmId) : undefined;
}

function overviewObserved(value: string | undefined): string {
  if (!value) return 'Waiting for data';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('ko-KR', { hour12: false });
}
