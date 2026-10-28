import { Card } from '@xenova/ui';
import type { CSSProperties } from 'react';
import { useMetrics, useVms } from '../api/queries';
import type { MetricSample, Vm } from '../api/types';
import { ROLE_ORDER, ROLES, vmOf, type AppId, type Role } from '../domain/roles';
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

  return (
    <div className={styles.page}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>RESOURCE MONITORING</span>
          <h1>Memory Usage</h1>
        </div>
      </header>

      {view !== 'memory' && (
        <Card
          title="코어 배치 (vCPU 핀닝)"
          actions={<span className={styles.dim}>RC카 시나리오 §2 표본</span>}
        >
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
      )}

      <div className={view === 'all' ? styles.cols2 : styles.detail}>
        {view !== 'memory' && (
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
        )}
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
        {ROLE_ORDER.map((app) => (
          <CpuVmChart key={app} role={ROLES[app]} vm={vmOf(app, items, drive)} />
        ))}
      </div>
    </div>
  );
}

function CpuVmChart({ role, vm }: { role: Role; vm: Vm | undefined }) {
  const metrics = useMetrics(vm?.vm_id ?? '', !!vm);
  const samples = metrics.data?.items ?? [];
  const values = samples.map((sample) => cpuValue(sample)).filter((value): value is number => value != null);
  const current = values.at(-1) ?? null;
  const points = samples
    .map((sample, index) => {
      const value = cpuValue(sample);
      if (value == null) return null;
      const x = samples.length <= 1 ? 100 : (index / (samples.length - 1)) * 100;
      const y = 100 - Math.max(0, Math.min(100, value));
      return `${x},${y}`;
    })
    .filter((point): point is string => point != null)
    .join(' ');
  const style = { '--vm-accent': role.color } as CSSProperties;

  return (
    <Card
      className={styles.cpuCard}
      style={style}
      title={
        <span className={styles.cpuCardTitle}>
          <i aria-hidden="true" />
          <span>
            <strong>{role.vmLabel}</strong>
            <small>{role.title}</small>
          </span>
        </span>
      }
      actions={<span className={styles.cpuCurrent}>{current == null ? '—' : `${current.toFixed(1)}%`}</span>}
    >
      <div className={styles.cpuChart}>
        {points ? (
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            role="img"
            aria-label={`${role.vmLabel} CPU 사용률 최근 5분 그래프`}
          >
            {[0, 25, 50, 75, 100].map((value) => (
              <line
                key={value}
                x1="0"
                x2="100"
                y1={100 - value}
                y2={100 - value}
                className={styles.cpuGridLine}
              />
            ))}
            <polyline points={points} className={styles.cpuLine} vectorEffect="non-scaling-stroke" />
          </svg>
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

function cpuValue(sample: MetricSample): number | null {
  return sample.values.find((value) => value.name === 'cpu_percent')?.value ?? null;
}
