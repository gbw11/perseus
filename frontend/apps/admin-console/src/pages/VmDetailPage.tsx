import { Button, Card, StatusBadge, useToast } from '@xenova/ui';
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { ApiError } from '../api/client';
import { useMetrics, useProcesses, useVm, useVmAction, type VmAction } from '../api/queries';
import type { MetricSample, Vm } from '../api/types';
import { STATE_HEALTH, STATE_LABEL } from '../format';
import { ago, osLabel, roleOf } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

type Tab = 'overview' | 'charts' | 'processes';
const TABS: [Tab, string][] = [
  ['overview', '개요'],
  ['charts', '실시간 차트'],
  ['processes', '프로세스'],
];

/**
 * W-04 VM 상세 (화면 정의서 W-04, UI-04·UI-05) — 초안. 화면 완성은 정수환 담당 작업이 이어받는다.
 * Zephyr 는 에이전트가 없어 차트·프로세스 대신 안내를 보인다. 웹 콘솔은 위쪽 메뉴 [웹 콘솔](/console)에만 둔다.
 * 참고 시안: stitch_untitled_project/ui_04_vm, ui_05.
 */
export function VmDetailPage() {
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
            <Link to="/vms">VM 목록</Link> / {vm.data?.name ?? vmId}
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
