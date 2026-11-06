import { Card, StatusBadge } from '@xenova/ui';
import { Fragment, lazy, Suspense, type CSSProperties } from 'react';
import { Link, useParams } from 'react-router';
import { ApiError } from '../api/client';
import { useEvents, useMetrics, useProcesses, useThreads, useVm } from '../api/queries';
import type { Vm } from '../api/types';
import { AGENT_LABEL, STATE_HEALTH, STATE_LABEL } from '../format';
import { clock, describe } from '../domain/events';
import { MODE_LABEL, osLabel, roleOf } from '../domain/roles';
import { observedControl, vmAppObservations } from '../domain/driveObservations';
import { networkRxKiBPerSecond } from '../domain/networkRate';
import { cpuPercentOf, metricValue, ZEPHYR_HEAP_USED_METRIC } from '../domain/vmMetrics';
import { useDrive } from '../layout/DriveContext';
import { PublisherStatus } from './PublisherStatus';
import { StressTestPanel } from './StressTestPanel';
import styles from './pages.module.css';

const ConsolePanel = lazy(() =>
  import('../console/ConsolePanel').then((module) => ({ default: module.ConsolePanel })),
);

const MIB = 1024 * 1024;

function Charts({ vm }: { vm: Vm }) {
  const metrics = useMetrics(vm.vm_id, true);
  const samples = metrics.data?.items ?? [];
  if (metrics.isPending) return <p className={styles.dim}>불러오는 중…</p>;
  if (metrics.isError && samples.length === 0)
    return (
      <p role="alert" className={styles.error}>
        메트릭을 불러오지 못했습니다.
      </p>
    );
  if (samples.length === 0) return <p className={styles.dim}>최근 5분 동안 수신된 메트릭이 없습니다.</p>;
  const cpu = samples
    .filter((s) =>
      s.values.some((v) => v.name === 'cpu.system.percent' || v.name === 'cpu_percent_allocated'),
    )
    .map(cpuPercentOf);
  const heap = vm.guest_kind === 'zephyr';
  const memoryMetric = heap ? ZEPHYR_HEAP_USED_METRIC : 'memory_used_bytes';
  const mem = samples
    .filter((s) => s.values.some((v) => v.name === memoryMetric))
    .map((s) => {
      const v = metricValue(s, memoryMetric);
      return v == null ? null : v / (heap ? 1024 : MIB);
    });
  const rate = networkRxKiBPerSecond(samples, vm.guest_kind);
  return (
    <>
      {metrics.isError && (
        <p role="alert" className={styles.error}>
          메트릭 갱신에 실패했습니다.
        </p>
      )}
      <div className={styles.cols3}>
        <Spark title="CPU" unit="%" values={cpu} color="var(--x-app-autonomous)" />
        <Spark
          title={heap ? '커널 힙 사용량' : '메모리'}
          unit={heap ? 'KiB' : 'MiB'}
          values={mem}
          color="var(--x-app-interaction)"
        />
        <Spark title="네트워크 수신" unit="KiB/s" values={rate} color="var(--x-app-manual)" />
      </div>
    </>
  );
}

function Spark({
  title,
  unit,
  values,
  color,
}: {
  title: string;
  unit: string;
  values: (number | null)[];
  color: string;
}) {
  const points = values.filter((v): v is number => v != null);
  const max = Math.max(1, ...points);
  const path = values
    .map((v, i) => {
      if (v == null) return '';
      const x = (i / Math.max(1, values.length - 1)) * 100;
      const y = 40 - (v / max) * 36;
      return values[i - 1] == null ? `M ${x},${y} h 0.25` : `L ${x},${y}`;
    })
    .join(' ');
  const last = values.at(-1);
  return (
    <div className={styles.tile}>
      <small>{title} · 최근 5분</small>
      <span className={styles.big}>{last == null ? '—' : last.toFixed(1)}</span>
      <span className={styles.unit}>{unit}</span>
      <svg
        viewBox="0 0 100 40"
        preserveAspectRatio="none"
        style={{ width: '100%', height: 60 }}
        aria-hidden="true"
      >
        <path d={path} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

function Processes({ vmId }: { vmId: string }) {
  const processes = useProcesses(vmId, true);
  if (processes.isPending) return <p className={styles.dim}>불러오는 중…</p>;
  if (processes.isError)
    return (
      <p role="alert" className={styles.error}>
        프로세스 정보를 불러오지 못했습니다.
      </p>
    );
  const items = processes.data?.items ?? [];
  const collection = processes.data?.process_collection;
  const collectionLabel = collection
    ? {
        first_sample: '첫 표본 · CPU 계측 구간 대기',
        ok: '수집 완료',
        partial: `일부 수집 · 읽기 실패 ${collection.failed}건`,
        read_failed: `프로세스 목록 읽기 실패${collection.error ? ` · ${collection.error}` : ''}`,
        disabled: '프로세스 수집 비활성',
      }[collection.status]
    : null;
  return (
    <>
      {processes.data?.stale && processes.data.observed_at && (
        <p className={styles.draft}>마지막 프로세스 보고가 오래되었습니다.</p>
      )}
      {collectionLabel && items.length > 0 && (
        <p className={styles.dim}>
          {collectionLabel}
          {collection?.truncated ? ' · 상위 목록 일부만 보고됨' : ''}
        </p>
      )}
      <table className={styles.table}>
        <thead>
          <tr>
            <th>PID</th>
            <th>이름</th>
            <th>CPU %</th>
            <th>메모리 (RSS)</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.pid}>
              <td className={styles.mono}>{p.pid}</td>
              <td>{p.name}</td>
              <td className={styles.mono}>{p.cpu_percent?.toFixed(1) ?? '—'}</td>
              <td className={styles.mono}>
                {p.rss_bytes == null ? '—' : `${(p.rss_bytes / MIB).toFixed(1)} MiB`}
              </td>
            </tr>
          ))}
          {items.length === 0 && (
            <tr>
              <td colSpan={4} className={styles.dim}>
                {collection?.status === 'ok'
                  ? '이 표본에 표시할 프로세스 없음'
                  : (collectionLabel ?? '프로세스 보고 없음')}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

function Threads({ vmId }: { vmId: string }) {
  const query = useThreads(vmId);
  if (query.isPending) return <p className={styles.dim}>스레드 정보를 불러오는 중…</p>;
  if (query.isError)
    return (
      <p role="alert" className={styles.error}>
        스레드 정보를 불러오지 못했습니다.
      </p>
    );
  const report = query.data;
  if (!report?.received_at || report.vm_id !== vmId) return <p className={styles.dim}>스레드 보고 없음</p>;
  return (
    <>
      {report.stale && <p className={styles.draft}>마지막 스레드 보고가 오래되었습니다.</p>}
      <p className={styles.dim}>
        {report.window_ms == null ? '계측 구간 미확인' : `계측 구간 ${report.window_ms} ms`}
        {report.conflicting_pages
          ? ' · 페이지 충돌'
          : !report.complete
            ? ` · 페이지 ${report.parts_received}/${report.part_count ?? '—'} 수신`
            : ''}
        {report.threads_status && report.threads_status !== 'ok'
          ? ` · ${report.threads_status === 'truncated' ? '일부 스레드만 수집됨' : report.threads_status}`
          : ''}
      </p>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>스레드 ID</th>
            <th>이름</th>
            <th>CPU %</th>
          </tr>
        </thead>
        <tbody>
          {report.items.map((thread) => (
            <tr key={thread.thread_id}>
              <td className={styles.mono}>{thread.thread_id}</td>
              <td>
                {thread.thread_name || '—'}
                {thread.name_truncated ? ' (잘림)' : ''}
              </td>
              <td className={styles.mono} title={thread.unavailable_reason ?? undefined}>
                {thread.cpu_percent?.toFixed(1) ?? '—'}
              </td>
            </tr>
          ))}
          {report.items.length === 0 && (
            <tr>
              <td colSpan={3} className={styles.dim}>
                이 표본에 표시할 스레드 없음
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

function ExecutionTable({ vm }: { vm: Vm }) {
  return vm.guest_kind === 'zephyr' ? <Threads vmId={vm.vm_id} /> : <Processes vmId={vm.vm_id} />;
}

function AppObservationRows({
  vmId,
  drive,
  socket,
}: {
  vmId: string;
  drive: Parameters<typeof vmAppObservations>[1];
  socket: Parameters<typeof vmAppObservations>[2];
}) {
  const reports = vmAppObservations(vmId, drive, socket);
  return reports.length > 0 ? (
    reports.map(({ appId, state, heartbeat }) => (
      <Fragment key={appId}>
        <div>
          <span>앱 상태 · {appId}</span>
          <span>{state}</span>
        </div>
        <div>
          <span>앱 heartbeat · {appId}</span>
          <span>{heartbeat}</span>
        </div>
      </Fragment>
    ))
  ) : (
    <>
      <div>
        <span>앱 상태</span>
        <span>미확인</span>
      </div>
      <div>
        <span>앱 heartbeat</span>
        <span>미확인</span>
      </div>
    </>
  );
}

/** S15P31E110-299: 제어 작업과 분리한 관측 중심 VM 상세 화면. */
export function VmDetailPage() {
  const { vmId = '' } = useParams();
  const vm = useVm(vmId);
  const events = useEvents({ vm_id: vmId, limit: 8 });
  const { drive, socket } = useDrive();
  const role = vm.data ? roleOf(vm.data, drive) : null;
  const control = observedControl(drive, socket);
  const color = role?.color ?? 'var(--x-primary)';
  const authority = !role?.driving
    ? '해당 없음'
    : !control
      ? '미확인'
      : control.active_vm_id
        ? control.active_vm_id === vm.data?.vm_id
          ? 'ACTIVE'
          : 'STANDBY'
        : control.active_app
          ? '미확인'
          : 'NONE';

  return (
    <div className={styles.vmDetailPage} style={{ '--vm-accent': color } as CSSProperties}>
      <section className={styles.vmDetailHero}>
        <div>
          <div className={styles.eyebrow}>
            <Link to="/dashboard/overview">VM Dashboard</Link> / {vm.data?.name ?? vmId}
          </div>
          <h1>{vm.data?.name ?? 'VM Detail'}</h1>
          <p>
            {role?.title ?? '역할 미지정'} · {vm.data ? osLabel(vm.data) : 'Loading'}
          </p>
        </div>
        <div className={styles.vmDetailBadges}>
          {role?.driving && (
            <span className={styles.authority} data-authority={authority.toLowerCase()}>
              {authority}
            </span>
          )}
          {vm.data && (
            <StatusBadge health={STATE_HEALTH[vm.data.state]} label={STATE_LABEL[vm.data.state]} size="lg" />
          )}
        </div>
      </section>

      {vm.isPending && <div className={styles.statePanel}>VM 상세 정보를 불러오는 중입니다…</div>}
      {vm.error && (
        <div className={styles.statePanel} role="alert">
          {vm.error instanceof ApiError ? `${vm.error.message} (${vm.error.code})` : String(vm.error)}
        </div>
      )}

      {vm.data && (
        <>
          <div className={styles.vmDetailGrid}>
            <Card title="Identity & state" accent={color}>
              <div className={styles.rows}>
                <div>
                  <span>VM ID</span>
                  <span>{vm.data.vm_id}</span>
                </div>
                <div>
                  <span>Role</span>
                  <span>{role?.title ?? 'Unassigned'}</span>
                </div>
                <div>
                  <span>VM state</span>
                  <span>{STATE_LABEL[vm.data.state]}</span>
                </div>
                <AppObservationRows vmId={vm.data.vm_id} drive={drive} socket={socket} />
                <div>
                  <span>Control authority</span>
                  <span>{authority}</span>
                </div>
                {role?.driving && (
                  <div>
                    <span>Drive mode</span>
                    <span>{control?.mode ? MODE_LABEL[control.mode] : '미확인'}</span>
                  </div>
                )}
              </div>
            </Card>

            <Card title="Assigned resources">
              <div className={styles.resourceFacts}>
                <div>
                  <span>vCPU</span>
                  <strong>{vm.data.vcpus}</strong>
                </div>
                <div>
                  <span>Memory</span>
                  <strong>
                    {vm.data.memory_mib}
                    <small> MiB</small>
                  </strong>
                </div>
                <div>
                  <span>예약 IPv4</span>
                  <strong>{vm.data.assigned_ipv4 ?? '—'}</strong>
                </div>
              </div>
              <div className={styles.rows}>
                <div>
                  <span>Template</span>
                  <span>{vm.data.template_id}</span>
                </div>
                <div>
                  <span>Version</span>
                  <span>{vm.data.template_version}</span>
                </div>
                {vm.data.guest_kind === 'linux' && (
                  <div>
                    <span>Linux Agent 생존 (v1)</span>
                    <span>{AGENT_LABEL[vm.data.agent_status ?? 'UNKNOWN']}</span>
                  </div>
                )}
              </div>
              <PublisherStatus vmId={vm.data.vm_id} />
            </Card>
          </div>

          <Card
            title="CPU & Memory telemetry"
            actions={<span className={styles.dim}>최근 5분 · 실제 수신 데이터</span>}
          >
            <Charts vm={vm.data} />
          </Card>

          {vm.data.guest_kind === 'linux' && <StressTestPanel vm={vm.data} accent={color} />}

          <Card title={vm.data.guest_kind === 'zephyr' ? '스레드' : '프로세스'}>
            <ExecutionTable vm={vm.data} />
          </Card>

          <Card title="Recent lifecycle & recovery events">
            <div className={styles.detailEvents}>
              {(events.data?.items ?? []).map((event) => {
                const summary = describe(event, () => vm.data.name);
                return (
                  <div key={event.event_id}>
                    <span className={styles.mono}>{clock(event.occurred_at)}</span>
                    <span className={`${styles.sev} ${styles[`sev${event.severity}`]}`}>
                      {event.severity}
                    </span>
                    <div>
                      <strong>{summary.title}</strong>
                      {summary.detail && <small>{summary.detail}</small>}
                    </div>
                  </div>
                );
              })}
              {!events.isPending && (events.data?.items.length ?? 0) === 0 && (
                <p className={styles.dim}>최근 lifecycle·장애·복구 이벤트가 없습니다.</p>
              )}
              {events.error && <p className={styles.error}>이벤트를 불러오지 못했습니다.</p>}
            </div>
          </Card>

          <Suspense fallback={<div className={styles.statePanel}>콘솔을 준비하는 중입니다…</div>}>
            <ConsolePanel key={vm.data.vm_id} vm={vm.data} />
          </Suspense>

          <div className={styles.controlHandoff}>
            <div>
              <strong>Lifecycle control</strong>
              <span>시작·정지·삭제 같은 작업은 관측 화면과 분리되어 있습니다.</span>
            </div>
            <Link to="/control">VM Control에서 관리 →</Link>
          </div>
        </>
      )}
    </div>
  );
}
