import { Button, Card, useToast } from '@xenova/ui';
import { useMemo, useState, type CSSProperties } from 'react';
import { Link } from 'react-router';
import { ApiError } from '../api/client';
import {
  useCommandStatus,
  useMetrics,
  useProcesses,
  useStartAutoDelay,
  useStartStress,
  useThreads,
  useVms,
} from '../api/queries';
import type { CommandState, StressRequest, Vm } from '../api/types';
import { cpuPercentOf, latest, memoryPercentOf } from '../domain/vmMetrics';
import { osLabel } from '../domain/roles';
import styles from './pages.module.css';

const MIB = 1024 * 1024;
const terminal = new Set<CommandState>(['SUCCEEDED', 'FAILED', 'REJECTED']);

function errorMessage(error: unknown) {
  return error instanceof ApiError ? `${error.message} (${error.code})` : String(error);
}

function Gauge({ label, value }: { label: string; value: number | null }) {
  const safe = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={styles.stressGauge}>
      <div>
        <span>{label}</span>
        <strong>{value == null ? '—' : `${value.toFixed(1)}%`}</strong>
      </div>
      <div className={styles.metricTrack}>
        <i style={{ width: `${safe}%` }} />
      </div>
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  unit,
  disabled,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  unit: string;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className={styles.stressField}>
      <span>{label}</span>
      <div className={styles.memoryInputGroup}>
        <input
          type="number"
          min={min}
          max={max}
          value={value}
          disabled={disabled}
          aria-invalid={!Number.isInteger(value) || value < min || value > max}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <span>{unit}</span>
      </div>
      <small>
        {min}~{max} {unit}
      </small>
    </label>
  );
}

function ExecutionSnapshot({ vm }: { vm: Vm }) {
  const processes = useProcesses(vm.vm_id, vm.guest_kind === 'linux');
  const threads = useThreads(vm.guest_kind === 'zephyr' ? vm.vm_id : '');
  if (vm.guest_kind === 'linux') {
    const items = processes.data?.items ?? [];
    return (
      <ExecutionTable
        title="프로세스"
        loading={processes.isPending}
        error={processes.isError}
        rows={items.slice(0, 8).map((item) => ({
          id: String(item.pid),
          name: item.name,
          cpu: item.cpu_percent,
          memory: item.rss_bytes == null ? '—' : `${(item.rss_bytes / MIB).toFixed(1)} MiB`,
        }))}
      />
    );
  }
  const items = threads.data?.items ?? [];
  return (
    <ExecutionTable
      title="스레드"
      loading={threads.isPending}
      error={threads.isError}
      rows={items.slice(0, 8).map((item) => ({
        id: item.thread_id,
        name: item.thread_name,
        cpu: item.cpu_percent,
        memory: item.unavailable_reason ?? '—',
      }))}
    />
  );
}

function ExecutionTable({
  title,
  rows,
  loading,
  error,
}: {
  title: string;
  loading: boolean;
  error: boolean;
  rows: { id: string; name: string; cpu: number | null; memory: string }[];
}) {
  return (
    <div className={styles.stressExecution}>
      <div className={styles.stressSubhead}>
        <strong>{title}</strong>
        <span>상위 {Math.min(rows.length, 8)}개</span>
      </div>
      {loading ? (
        <p className={styles.dim}>불러오는 중…</p>
      ) : error ? (
        <p className={styles.error}>정보를 불러오지 못했습니다.</p>
      ) : (
        <div className={styles.stressTableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{title === '프로세스' ? 'PID' : 'ID'}</th>
                <th>이름</th>
                <th>CPU %</th>
                <th>{title === '프로세스' ? 'RSS' : '상태'}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className={styles.mono}>{row.id}</td>
                  <td>{row.name || '—'}</td>
                  <td className={styles.mono}>{row.cpu?.toFixed(1) ?? '—'}</td>
                  <td className={styles.mono}>{row.memory}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={4} className={styles.dim}>
                    보고된 항목이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** 시뮬레이터 VM에 부하를 주고 같은 자리에서 telemetry와 실행 단위를 확인한다. */
export function SimulatorStressLab() {
  const vms = useVms();
  const [vmId, setVmId] = useState('');
  const vm = useMemo(
    () => vms.data?.items.find((item) => item.vm_id === vmId) ?? vms.data?.items[0],
    [vms.data, vmId],
  );
  const stress = useStartStress(vm?.vm_id ?? '');
  const autoDelay = useStartAutoDelay(vm?.vm_id ?? '');
  const metrics = useMetrics(vm?.vm_id ?? '', Boolean(vm), 5);
  const [commandId, setCommandId] = useState<string | null>(null);
  const command = useCommandStatus(vm?.vm_id ?? '', commandId);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [cpuAmount, setCpuAmount] = useState(80);
  const [cpuDuration, setCpuDuration] = useState(30);
  const [memoryMib, setMemoryMib] = useState(4);
  const [memoryDuration, setMemoryDuration] = useState(30);
  const [delayMs, setDelayMs] = useState(20);
  const [delayDuration, setDelayDuration] = useState(30);
  const toast = useToast();

  function selectVm(nextVmId: string) {
    const nextVm = vms.data?.items.find((item) => item.vm_id === nextVmId);
    setVmId(nextVmId);
    setCommandId(null);
    setFeedback(null);
    setCpuAmount(nextVm?.guest_kind === 'zephyr' ? 80 : 1);
    setMemoryMib(nextVm?.guest_kind === 'zephyr' ? 4 : 256);
  }

  const samples = metrics.data?.items ?? [];
  const cpu = latest(samples, cpuPercentOf);
  const memory = vm ? latest(samples, (sample) => memoryPercentOf(sample, vm)) : null;
  const ready = Boolean(
    vm && vm.state === 'running' && (vm.guest_kind === 'zephyr' || vm.agent_status === 'ONLINE'),
  );
  const busy =
    stress.isPending || autoDelay.isPending || Boolean(command.data && !terminal.has(command.data.status));
  const cpuMin = vm?.guest_kind === 'zephyr' ? 10 : 1;
  const cpuMax = vm?.guest_kind === 'zephyr' ? 100 : 4;
  const memoryMax = vm?.guest_kind === 'zephyr' ? 8 : 512;

  async function sendStress(input: StressRequest) {
    if (!vm) return;
    try {
      setFeedback(null);
      const result = await stress.mutateAsync(input);
      setCommandId(result.command_id);
      setFeedback('명령을 전송했습니다. telemetry 변화를 확인하세요.');
      toast.show(`${vm.name}: 부하 명령 전송`, 'ok');
    } catch (error) {
      setFeedback(errorMessage(error));
      toast.show(errorMessage(error), 'fault', 5000);
    }
  }

  async function sendDelay() {
    if (!vm) return;
    try {
      setFeedback(null);
      const result = await autoDelay.mutateAsync({ delay_ms: delayMs, duration_s: delayDuration });
      setCommandId(result.command_id);
      setFeedback('주행 연산 지연 명령을 전송했습니다.');
      toast.show(`${vm.name}: 연산 지연 명령 전송`, 'ok');
    } catch (error) {
      setFeedback(errorMessage(error));
      toast.show(errorMessage(error), 'fault', 5000);
    }
  }

  return (
    <div className={styles.page} style={{ '--vm-accent': 'var(--x-primary)' } as CSSProperties}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>Simulator / Load test</div>
          <h1>VM 부하 테스트</h1>
          <p className={styles.dim}>부하를 실행하고 같은 VM의 telemetry와 실행 단위를 함께 관찰합니다.</p>
        </div>
      </section>
      <nav className={styles.simulatorTabs} aria-label="Simulator 하위 메뉴">
        <Link to="/simulator">시뮬레이션 제어</Link>
        <Link to="/simulator/stress" aria-current="page">
          부하 테스트
        </Link>
      </nav>

      <section className={styles.stressVmSection} aria-labelledby="stress-vm-title">
        <div className={styles.stressSectionTitle}>
          <div>
            <span>01</span>
            <div>
              <h2 id="stress-vm-title">대상 VM 선택</h2>
              <p>운영체제에 맞는 부하 항목만 표시됩니다.</p>
            </div>
          </div>
          {vm && <small>{ready ? '명령 가능' : '연결 확인 필요'}</small>}
        </div>
        <div className={styles.stressVmGrid}>
          {(vms.data?.items ?? []).map((item) => (
            <button
              key={item.vm_id}
              type="button"
              data-selected={item.vm_id === vm?.vm_id || undefined}
              onClick={() => selectVm(item.vm_id)}
            >
              <span className={styles.stressOsBadge} data-kind={item.guest_kind}>
                {item.guest_kind}
              </span>
              <strong>{item.name}</strong>
              <small>
                {osLabel(item)} · {item.vcpus} vCPU · {item.memory_mib} MiB
              </small>
              <i>{item.state === 'running' ? 'RUNNING' : item.state.toUpperCase()}</i>
            </button>
          ))}
        </div>
      </section>

      {!vm ? (
        <Card>
          <p className={styles.dim}>부하를 줄 VM이 없습니다.</p>
        </Card>
      ) : (
        <>
          <section aria-labelledby="stress-actions-title">
            <div className={styles.stressSectionTitle}>
              <div>
                <span>02</span>
                <div>
                  <h2 id="stress-actions-title">부하 시나리오</h2>
                  <p>
                    {vm.guest_kind === 'zephyr' ? 'Zephyr 전용 3종' : 'Linux Agent 2종'} · 한 번에 하나씩 실행
                  </p>
                </div>
              </div>
            </div>
            <div className={styles.stressScenarioGrid} data-count={vm.guest_kind === 'zephyr' ? 3 : 2}>
              <div className={styles.stressControlCard}>
                <div className={styles.stressSubhead}>
                  <div className={styles.stressScenarioIcon}>CPU</div>
                  <div>
                    <strong>CPU 부하</strong>
                    <p>
                      {vm.guest_kind === 'zephyr' ? '단일 vCPU duty cycle 점유' : '워커 프로세스로 CPU 점유'}
                    </p>
                  </div>
                </div>
                <div className={styles.stressFields}>
                  <NumberField
                    label={vm.guest_kind === 'zephyr' ? '부하 정도' : '워커 수'}
                    value={cpuAmount}
                    min={cpuMin}
                    max={cpuMax}
                    unit={vm.guest_kind === 'zephyr' ? '%' : '개'}
                    disabled={!ready || busy}
                    onChange={setCpuAmount}
                  />
                  <NumberField
                    label="지속 시간"
                    value={cpuDuration}
                    min={1}
                    max={120}
                    unit="초"
                    disabled={!ready || busy}
                    onChange={setCpuDuration}
                  />
                </div>
                <Button
                  disabled={
                    !ready ||
                    busy ||
                    cpuAmount < cpuMin ||
                    cpuAmount > cpuMax ||
                    cpuDuration < 1 ||
                    cpuDuration > 120
                  }
                  onClick={() =>
                    void sendStress({
                      kind: 'cpu',
                      body: {
                        workers: vm.guest_kind === 'zephyr' ? 1 : cpuAmount,
                        load_percent: vm.guest_kind === 'zephyr' ? cpuAmount : undefined,
                        duration_s: cpuDuration,
                      },
                    })
                  }
                >
                  CPU 부하 시작
                </Button>
              </div>
              <div className={styles.stressControlCard}>
                <div className={styles.stressSubhead}>
                  <div className={styles.stressScenarioIcon}>MEM</div>
                  <div>
                    <strong>메모리 부하</strong>
                    <p>
                      {vm.guest_kind === 'zephyr'
                        ? '커널 heap에 메모리 할당'
                        : 'Agent 프로세스에 메모리 할당'}
                    </p>
                  </div>
                </div>
                <div className={styles.stressFields}>
                  <NumberField
                    label="할당량"
                    value={memoryMib}
                    min={1}
                    max={memoryMax}
                    unit="MiB"
                    disabled={!ready || busy}
                    onChange={setMemoryMib}
                  />
                  <NumberField
                    label="지속 시간"
                    value={memoryDuration}
                    min={1}
                    max={120}
                    unit="초"
                    disabled={!ready || busy}
                    onChange={setMemoryDuration}
                  />
                </div>
                <Button
                  disabled={
                    !ready ||
                    busy ||
                    memoryMib < 1 ||
                    memoryMib > memoryMax ||
                    memoryDuration < 1 ||
                    memoryDuration > 120
                  }
                  onClick={() =>
                    void sendStress({
                      kind: 'memory',
                      body: { memory_mib: memoryMib, duration_s: memoryDuration },
                    })
                  }
                >
                  메모리 부하 시작
                </Button>
              </div>
              {vm.guest_kind === 'zephyr' && (
                <div className={styles.stressControlCard}>
                  <div className={styles.stressSubhead}>
                    <div className={styles.stressScenarioIcon}>DELAY</div>
                    <div>
                      <strong>주행 연산 지연</strong>
                      <p>주행 연산 루프에 지정한 지연 추가</p>
                    </div>
                  </div>
                  <div className={styles.stressFields}>
                    <NumberField
                      label="지연"
                      value={delayMs}
                      min={1}
                      max={100}
                      unit="ms"
                      disabled={!ready || busy}
                      onChange={setDelayMs}
                    />
                    <NumberField
                      label="지속 시간"
                      value={delayDuration}
                      min={1}
                      max={120}
                      unit="초"
                      disabled={!ready || busy}
                      onChange={setDelayDuration}
                    />
                  </div>
                  <Button
                    disabled={
                      !ready ||
                      busy ||
                      delayMs < 1 ||
                      delayMs > 100 ||
                      delayDuration < 1 ||
                      delayDuration > 120
                    }
                    onClick={() => void sendDelay()}
                  >
                    연산 지연 시작
                  </Button>
                </div>
              )}
            </div>
            {(feedback || command.data) && (
              <div
                className={styles.stressCommand}
                data-tone={
                  command.data?.status === 'FAILED' || command.data?.status === 'REJECTED' ? 'fault' : 'ok'
                }
              >
                <strong>{command.data ? `명령 ${command.data.status}` : '명령 전송'}</strong>
                <span>{command.data?.message || feedback}</span>
                {commandId && <code>{commandId}</code>}
              </div>
            )}
          </section>

          <section aria-labelledby="stress-observe-title">
            <div className={styles.stressSectionTitle}>
              <div>
                <span>03</span>
                <div>
                  <h2 id="stress-observe-title">실시간 관측</h2>
                  <p>선택한 {vm.name}의 최근 5분 데이터 · 2초마다 갱신</p>
                </div>
              </div>
            </div>
            <div className={styles.stressObservationGrid}>
              <Card
                title="CPU & Memory telemetry"
                actions={<span className={styles.pinningCardTag}>LIVE</span>}
              >
                <div className={styles.stressTelemetry}>
                  <div className={styles.stressSubhead}>
                    <div>
                      <strong>{vm.name}</strong>
                      <p>
                        {vm.guest_kind === 'zephyr'
                          ? 'System CPU · Kernel heap'
                          : 'Allocated CPU · Guest memory'}
                      </p>
                    </div>
                  </div>
                  {metrics.isError && <p className={styles.error}>메트릭을 불러오지 못했습니다.</p>}
                  <Gauge label="CPU" value={cpu} />
                  <Gauge label={vm.guest_kind === 'zephyr' ? 'Kernel heap' : 'Memory'} value={memory} />
                </div>
              </Card>
              <Card
                title={vm.guest_kind === 'zephyr' ? '스레드' : '프로세스'}
                actions={<span className={styles.pinningCardTag}>{vm.guest_kind.toUpperCase()}</span>}
              >
                <ExecutionSnapshot vm={vm} />
              </Card>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
