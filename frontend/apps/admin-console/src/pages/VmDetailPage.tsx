import { Button, Card, StatusBadge, useToast } from '@xenova/ui';
import { lazy, Suspense, useState, type CSSProperties } from 'react';
import { Link, useParams } from 'react-router';
import { ApiError } from '../api/client';
import { useEvents, useMetrics, useProcesses, useVm, useVmAction, type VmAction } from '../api/queries';
import type { MetricSample, Vm } from '../api/types';
import { STATE_HEALTH, STATE_LABEL } from '../format';
import { clock, describe } from '../domain/events';
import { ago, appHealth, MODE_LABEL, osLabel, roleOf } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import { RuntimeLogsPanel } from '../console/RuntimeLogsPanel';
import styles from './pages.module.css';

const ConsolePanel = lazy(() =>
  import('../console/ConsolePanel').then((module) => ({ default: module.ConsolePanel })),
);

type Tab = 'overview' | 'charts' | 'processes';
const TABS: [Tab, string][] = [
  ['overview', '개요'],
  ['charts', '실시간 차트'],
  ['processes', '프로세스'],
];

/**
 * W-04 VM 상세 (화면 정의서 W-04, UI-04·UI-05) — 초안. 화면 완성은 정수환 담당 작업이 이어받는다.
 * Zephyr 는 에이전트가 없어 차트·프로세스 대신 안내를 보인다. 웹 콘솔은 카드 아래와 Dashboard의 Console 탭에 있다.
 * 참고 시안: stitch_untitled_project/ui_04_vm, ui_05.
 */
export function LegacyVmDetailPage() {
  const { vmId = '' } = useParams();
  const vm = useVm(vmId);
  const { drive } = useDrive();
  const [tab, setTab] = useState<Tab>('overview');
  const linux = vm.data?.guest_kind === 'linux';
  const role = vm.data ? roleOf(vm.data, drive) : null;
  const color = role?.color ?? 'var(--x-primary)';

  return (
    <div className={styles.page}>
      <section className={styles.pageHead} style={{ borderTop: `3px solid ${color}` }}>
        <div>
          <div className={styles.eyebrow}>
            <Link to="/control">VM 목록</Link> / {vm.data?.name ?? vmId}
          </div>
          <h1 style={{ color }}>{role?.title ?? vm.data?.name ?? 'VM 상세'}</h1>
        </div>
        {vm.data && (
          <StatusBadge health={STATE_HEALTH[vm.data.state]} label={STATE_LABEL[vm.data.state]} size="lg" />
        )}
        <span className={styles.grow} />
        {vm.data && <LifecycleButtons vm={vm.data} />}
      </section>

      {vm.isPending && <p className={styles.dim}>불러오는 중…</p>}
      {vm.error && (
        <p className={styles.error} role="alert">
          {vm.error instanceof ApiError ? `${vm.error.message} (${vm.error.code})` : String(vm.error)}
        </p>
      )}

      {vm.data && (
        <Card>
          <div className={styles.tabs} role="tablist">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                role="tab"
                type="button"
                className={styles.tab}
                aria-selected={tab === id}
                disabled={!linux && (id === 'charts' || id === 'processes')}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div style={{ paddingTop: 'var(--x-space-4)' }}>
            {!linux && tab === 'overview' && (
              <p className={styles.draft}>
                Zephyr VM 은 차트·프로세스를 보내지 않는다. 출력은 위쪽 메뉴 [웹 콘솔]에서 본다.
              </p>
            )}
            {tab === 'overview' && <Overview vm={vm.data} />}
            {tab === 'charts' && linux && <Charts vmId={vm.data.vm_id} />}
            {tab === 'processes' && linux && <Processes vmId={vm.data.vm_id} />}
          </div>
        </Card>
      )}
      {vm.data && (
        <Suspense fallback={<p className={styles.dim}>콘솔 준비 중…</p>}>
          <ConsolePanel key={vm.data.vm_id} vm={vm.data} />
          <RuntimeLogsPanel key={`logs-${vm.data.vm_id}`} vmId={vm.data.vm_id} />
        </Suspense>
      )}
    </div>
  );
}

function LifecycleButtons({ vm }: { vm: Vm }) {
  const action = useVmAction();
  const toast = useToast();
  const run = (kind: VmAction) =>
    action.mutate(
      { vmId: vm.vm_id, action: kind },
      {
        onSuccess: () => toast.show(`${vm.name}: ${kind === 'start' ? '시작' : '정지'} 요청 완료`, 'ok'),
        onError: (error) =>
          toast.show(error instanceof ApiError ? error.message : String(error), 'fault', 5000),
      },
    );
  return (
    <div className={styles.rowActions}>
      <Button onClick={() => run('start')} disabled={vm.state === 'running'}>
        시작
      </Button>
      <Button variant="secondary" onClick={() => run('stop')} disabled={vm.state !== 'running'}>
        정지
      </Button>
    </div>
  );
}

function Overview({ vm }: { vm: Vm }) {
  const { drive } = useDrive();
  const role = roleOf(vm, drive);
  const app = role ? drive?.apps[role.app] : undefined;
  const zephyr = vm.guest_kind === 'zephyr';
  return (
    <div className={styles.cols2}>
      <div className={styles.rows}>
        <div>
          <span>VM ID</span>
          <span>{vm.vm_id}</span>
        </div>
        <div>
          <span>운영체제 · 템플릿</span>
          <span>
            {osLabel(vm)} · {vm.template_id} {vm.template_version}
          </span>
        </div>
        <div>
          <span>자원</span>
          <span>
            {vm.vcpus} vCPU / {vm.memory_mib} MiB
          </span>
        </div>
        <div>
          <span>통신</span>
          <span>{zephyr ? 'vchan 중계 (dom0)' : (vm.ipv4 ?? '—')}</span>
        </div>
      </div>
      <div className={styles.rows}>
        {role && (
          <>
            <div>
              <span>앱 (app_id)</span>
              <span style={{ color: role.color }}>{role.app}</span>
            </div>
            <div>
              <span>app.heartbeat</span>
              <span>
                {app?.heartbeat_state ? `${app.heartbeat_state} · ${ago(app.heartbeat_ago_ms)}` : '—'}
              </span>
            </div>
            {role.driving && (
              <div>
                <span>주행 명령</span>
                <span>{app?.command_fresh ? `${app.command_hz} Hz` : '명령 없음'}</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

const MIB = 1024 * 1024;
const valueOf = (sample: MetricSample, name: string) =>
  sample.values.find((v) => v.name === name)?.value ?? null;

function Charts({ vmId }: { vmId: string }) {
  const metrics = useMetrics(vmId, true);
  const samples = metrics.data?.items ?? [];
  if (metrics.isPending) return <p className={styles.dim}>불러오는 중…</p>;
  if (samples.length === 0)
    return <p className={styles.dim}>최근 5분 메트릭이 없다 (에이전트 metrics 미수신).</p>;
  const cpu = samples.map((s) => valueOf(s, 'cpu_percent'));
  const mem = samples.map((s) => {
    const v = valueOf(s, 'memory_used_bytes');
    return v == null ? null : v / MIB;
  });
  const rx = samples.map((s) => valueOf(s, 'net_rx_bytes_total'));
  const rate = rx.map((v, i) => {
    const prev = i === 0 ? null : rx[i - 1];
    return v == null || prev == null ? null : (v - prev) / 1024 / 10;
  });
  return (
    <div className={styles.cols3}>
      <Spark title="CPU" unit="%" values={cpu} color="var(--x-app-autonomous)" />
      <Spark title="메모리" unit="MiB" values={mem} color="var(--x-app-interaction)" />
      <Spark title="네트워크 수신" unit="KiB/s" values={rate} color="var(--x-app-manual)" />
    </div>
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
    .map((v, i) =>
      v == null ? null : `${(i / Math.max(1, values.length - 1)) * 100},${40 - (v / max) * 36}`,
    )
    .filter(Boolean)
    .join(' ');
  const last = points.at(-1);
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
        <polyline
          points={path}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

function Processes({ vmId }: { vmId: string }) {
  const processes = useProcesses(vmId, true);
  const items = processes.data?.items ?? [];
  return (
    <>
      {processes.data?.stale && <p className={styles.draft}>마지막 보고가 오래됐다 (stale)</p>}
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
                프로세스 보고 없음
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

/** S15P31E110-299: 제어 작업과 분리한 관측 중심 VM 상세 화면. */
export function VmDetailPage() {
  const { vmId = '' } = useParams();
  const vm = useVm(vmId);
  const events = useEvents({ vm_id: vmId, limit: 8 });
  const { drive } = useDrive();
  const role = vm.data ? roleOf(vm.data, drive) : null;
  const app = role ? drive?.apps[role.app] : undefined;
  const color = role?.color ?? 'var(--x-primary)';
  const authority =
    !role?.driving || !vm.data || vm.data.state !== 'running'
      ? 'NONE'
      : drive?.control?.active_vm_id
        ? drive.control.active_vm_id === vm.data.vm_id
          ? 'ACTIVE'
          : 'STANDBY'
        : drive?.control?.active_app === role.app
          ? 'ACTIVE'
          : drive?.control?.active_app
            ? 'STANDBY'
            : 'NONE';
  const health = vm.data && role ? appHealth(role, vm.data, app) : 'stopped';
  const appLabel = !app
    ? 'Unknown'
    : health === 'ok'
      ? 'Ready'
      : health === 'recovering'
        ? 'Degraded'
        : health === 'fault'
          ? 'Failed'
          : 'Unknown';

  return (
    <div className={styles.vmDetailPage} style={{ '--vm-accent': color } as CSSProperties}>
      <section className={styles.vmDetailHero}>
        <div>
          <div className={styles.eyebrow}>
            <Link to="/dashboard/overview">VM Dashboard</Link> / {role?.vmLabel ?? vm.data?.name ?? vmId}
          </div>
          <h1>{role?.vmLabel ?? vm.data?.name ?? 'VM Detail'}</h1>
          <p>
            {role?.title ?? 'Virtual Machine'} · {vm.data ? osLabel(vm.data) : 'Loading'}
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
                <div>
                  <span>App state</span>
                  <span>{appLabel}</span>
                </div>
                <div>
                  <span>Control authority</span>
                  <span>{authority}</span>
                </div>
                {role?.driving && (
                  <div>
                    <span>Drive mode</span>
                    <span>{drive?.control?.mode ? MODE_LABEL[drive.control.mode] : 'Unknown'}</span>
                  </div>
                )}
                <div>
                  <span>Heartbeat</span>
                  <span>{app?.heartbeat_ago_ms != null ? ago(app.heartbeat_ago_ms) : 'Unknown'}</span>
                </div>
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
                  <span>Network</span>
                  <strong>{vm.data.guest_kind === 'zephyr' ? 'vchan' : (vm.data.ipv4 ?? '—')}</strong>
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
                <div>
                  <span>Agent</span>
                  <span>{vm.data.agent_status ?? 'UNKNOWN'}</span>
                </div>
              </div>
            </Card>
          </div>

          <Card
            title="CPU & Memory telemetry"
            actions={<span className={styles.dim}>최근 5분 · 실제 수신 데이터</span>}
          >
            {vm.data.guest_kind === 'linux' ? (
              <Charts vmId={vm.data.vm_id} />
            ) : (
              <div className={styles.telemetryEmpty}>
                <strong>Guest metrics not reported</strong>
                <span>Zephyr VM은 현재 메트릭 API를 제공하지 않습니다. 0%로 표시하지 않습니다.</span>
              </div>
            )}
          </Card>

          <Card title="Recent lifecycle & recovery events">
            <div className={styles.detailEvents}>
              {(events.data?.items ?? []).map((event) => {
                const summary = describe(event, () => role?.vmLabel ?? vm.data.name);
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
