import { Button, Card, useToast } from '@xenova/ui';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useStartStress } from '../api/queries';
import type { StressCommand, StressRequest, Vm } from '../api/types';
import styles from './pages.module.css';

/** 백엔드 CpuStressRequest·MemoryStressRequest 검증 범위와 같다 */
const STRESS_LIMITS = {
  workersMax: 4,
  memoryMibMax: 512,
  durationSMax: 120,
} as const;

type Feedback = { tone: 'ok' | 'fault'; message: string } | null;

const inRange = (value: number, max: number) => Number.isInteger(value) && value >= 1 && value <= max;

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'AGENT_UNAVAILABLE') return 'VM Agent가 온라인이 아니라 부하 명령을 보낼 수 없습니다.';
    if (error.code === 'MQTT_UNAVAILABLE')
      return 'MQTT 브로커에 연결되어 있지 않아 부하 명령을 보내지 못했습니다.';
    return `${error.message} (${error.code})`;
  }
  return String(error);
}

function describe(command: StressCommand): string {
  const what = command.type === 'CPU' ? `CPU 워커 ${command.amount}개` : `메모리 ${command.amount} MiB`;
  return `${what} · ${command.duration_s}초 부하 명령을 보냈습니다. 위 telemetry 그래프에서 변화를 확인하세요.`;
}

function DurationInput({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <div className={styles.memoryInputGroup}>
      <input
        type="number"
        aria-label={label}
        min={1}
        max={STRESS_LIMITS.durationSMax}
        step={1}
        value={value}
        disabled={disabled}
        aria-invalid={!inRange(value, STRESS_LIMITS.durationSMax)}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <span>초</span>
    </div>
  );
}

/** Linux VM Agent 에 CPU·메모리 부하 명령(MQTT agent.command, action=stress)을 보낸다 */
export function StressTestPanel({ vm, accent }: { vm: Vm; accent: string }) {
  const stress = useStartStress(vm.vm_id);
  const toast = useToast();
  const [workers, setWorkers] = useState(1);
  const [cpuDuration, setCpuDuration] = useState(30);
  const [memoryMib, setMemoryMib] = useState(256);
  const [memoryDuration, setMemoryDuration] = useState(30);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const ready = vm.state === 'running' && vm.agent_status === 'ONLINE';
  const busy = stress.isPending;
  const cpuValid =
    inRange(workers, STRESS_LIMITS.workersMax) && inRange(cpuDuration, STRESS_LIMITS.durationSMax);
  const memoryValid =
    inRange(memoryMib, STRESS_LIMITS.memoryMibMax) && inRange(memoryDuration, STRESS_LIMITS.durationSMax);

  const start = async (input: StressRequest) => {
    setFeedback(null);
    try {
      const command = await stress.mutateAsync(input);
      setFeedback({ tone: 'ok', message: describe(command) });
      toast.show(`${vm.name}: ${command.type === 'CPU' ? 'CPU' : '메모리'} 부하 명령 전송`, 'ok');
    } catch (error) {
      const message = messageOf(error);
      setFeedback({ tone: 'fault', message });
      toast.show(message, 'fault', 5000);
    }
  };

  return (
    <Card title="부하 테스트" accent={accent} actions={<span className={styles.pinningCardTag}>STRESS</span>}>
      <section className={styles.resourceTuningSection} aria-labelledby="stress-title">
        <div className={styles.resourceSectionHeading}>
          <div>
            <span id="stress-title">CPU · 메모리 부하 주기</span>
            <p>VM Agent가 지정한 시간 동안 부하 프로세스를 띄웁니다. 시간이 지나면 스스로 정리됩니다.</p>
          </div>
          <span className={styles.resourceEditState} data-enabled={ready || undefined}>
            {ready ? '실행 가능' : vm.state !== 'running' ? 'VM 실행 필요' : 'Agent 연결 필요'}
          </span>
        </div>

        <div className={styles.resourceCapacityGrid}>
          <div className={styles.resourceCapacityItem}>
            <span>CPU 부하</span>
            <div className={styles.vcpuOptions} role="group" aria-label="CPU 워커 수 선택">
              {Array.from({ length: STRESS_LIMITS.workersMax }, (_, index) => index + 1).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={workers === value}
                  disabled={!ready || busy}
                  onClick={() => setWorkers(value)}
                >
                  <strong>{value}</strong>
                  <small>워커</small>
                </button>
              ))}
            </div>
            <DurationInput
              label="CPU 부하 시간(초)"
              value={cpuDuration}
              disabled={!ready || busy}
              onChange={setCpuDuration}
            />
            <small>
              워커 1~{STRESS_LIMITS.workersMax}개 · 1~{STRESS_LIMITS.durationSMax}초
            </small>
            <Button
              disabled={!ready || busy || !cpuValid}
              onClick={() => void start({ kind: 'cpu', body: { workers, duration_s: cpuDuration } })}
            >
              CPU 부하 시작
            </Button>
          </div>

          <div className={styles.resourceCapacityItem}>
            <span>메모리 부하</span>
            <div className={styles.memoryInputGroup}>
              <input
                type="number"
                aria-label="메모리 부하 크기(MiB)"
                min={1}
                max={STRESS_LIMITS.memoryMibMax}
                step={1}
                value={memoryMib}
                disabled={!ready || busy}
                aria-invalid={!inRange(memoryMib, STRESS_LIMITS.memoryMibMax)}
                onChange={(event) => setMemoryMib(Number(event.target.value))}
              />
              <span>MiB</span>
            </div>
            <DurationInput
              label="메모리 부하 시간(초)"
              value={memoryDuration}
              disabled={!ready || busy}
              onChange={setMemoryDuration}
            />
            <small>
              1~{STRESS_LIMITS.memoryMibMax} MiB · 1~{STRESS_LIMITS.durationSMax}초 · 할당 메모리{' '}
              {vm.memory_mib} MiB
            </small>
            <Button
              disabled={!ready || busy || !memoryValid}
              onClick={() =>
                void start({ kind: 'memory', body: { memory_mib: memoryMib, duration_s: memoryDuration } })
              }
            >
              메모리 부하 시작
            </Button>
          </div>
        </div>

        {(!cpuValid || !memoryValid) && ready && (
          <p className={styles.resourceValidation} role="alert">
            부하 크기와 시간의 허용 범위를 확인해 주세요.
          </p>
        )}
        {!ready && (
          <p className={styles.dim}>
            VM이 실행 중이고 Linux Agent가 연결됨 상태일 때만 부하 명령을 보낼 수 있습니다.
          </p>
        )}
        {feedback && (
          <div className={styles.pinningFeedback} data-tone={feedback.tone} role="status">
            <i aria-hidden="true" />
            {feedback.message}
          </div>
        )}
      </section>
    </Card>
  );
}
