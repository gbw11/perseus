import { Button, Card, useToast } from '@xenova/ui';
import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '../api/client';
import { useAuthedSocket } from '../api/drive';
import { useUpdateVcpuPinning, useVmResources } from '../api/queries';
import type { MetricSample, PcpuStatus, VcpuPinning, Vm } from '../api/types';
import { mappingLabel, normalizePinning, pinningEqual, togglePcpu } from '../domain/vcpuPinning';
import styles from './pages.module.css';

const MOCK = import.meta.env.VITE_MOCK === 'true';
const PCPU_METRIC_NAMES = new Set(['pcpu_percent', 'pcpu_usage_percent']);

type Feedback = { tone: 'ok' | 'fault'; message: string } | null;

function loadMap(pcpus: PcpuStatus[]): Map<number, number | null> {
  return new Map(pcpus.map(({ pcpu, load_percent }) => [pcpu, load_percent]));
}

function usePcpuLoads(vmId: string, initial: PcpuStatus[]) {
  const initialLoads = useMemo(() => loadMap(initial), [initial]);
  const [liveLoadsByVm, setLiveLoadsByVm] = useState<Map<string, Map<number, number>>>(new Map());

  const { state, send } = useAuthedSocket(MOCK ? '' : '/ws/v1/metrics', (frame) => {
    if (frame.type !== 'metrics.sample') return;
    const sample = frame.payload as MetricSample | undefined;
    if (!sample) return;
    const updates = sample.values.flatMap((value) => {
      const rawPcpu = value.labels?.pcpu;
      if (!PCPU_METRIC_NAMES.has(value.name) || rawPcpu == null || value.value == null) return [];
      const pcpu = Number(rawPcpu);
      return Number.isInteger(pcpu) ? ([[pcpu, value.value]] as const) : [];
    });
    if (updates.length) {
      setLiveLoadsByVm((current) => {
        const next = new Map(current);
        next.set(vmId, new Map([...(current.get(vmId) ?? new Map()), ...updates]));
        return next;
      });
    }
  });

  useEffect(() => {
    if (state !== 'open') return;
    send({
      type: 'subscribe',
      request_id: `pcpu-${vmId}`,
      vm_ids: [vmId],
      names: [...PCPU_METRIC_NAMES],
    });
  }, [send, state, vmId]);

  const loads = useMemo(
    () => new Map([...initialLoads, ...(liveLoadsByVm.get(vmId) ?? new Map())]),
    [initialLoads, liveLoadsByVm, vmId],
  );
  return { loads, live: !MOCK && state === 'open' };
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return String(error);
  if (error.status === 404 || error.code === 'VM_CONTROL_NOT_SUPPORTED') {
    return '리소스 제어 API가 아직 연결되지 않았습니다. 백엔드 107 연동 후 실제 매핑이 표시됩니다.';
  }
  return `${error.message} (${error.code})`;
}

interface VcpuPinningPanelProps {
  vm: Vm;
  accent: string;
}

export function VcpuPinningPanel({ vm, accent }: VcpuPinningPanelProps) {
  const resources = useVmResources(vm.vm_id);
  const update = useUpdateVcpuPinning(vm.vm_id);
  const toast = useToast();
  const [draftOverride, setDraftOverride] = useState<VcpuPinning[] | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const current = useMemo(
    () => normalizePinning(resources.data?.vcpu_pinning ?? []),
    [resources.data?.vcpu_pinning],
  );
  const draft = draftOverride ?? current;

  const pcpuStatuses = resources.data?.pcpus ?? [];
  const { loads, live } = usePcpuLoads(vm.vm_id, pcpuStatuses);
  const dirty = !pinningEqual(current, draft);
  const changedVcpus = draft.filter((mapping) => {
    const before = current.find((item) => item.vcpu === mapping.vcpu);
    return !before || !pinningEqual([before], [mapping]);
  });

  const apply = async () => {
    const requested = normalizePinning(draft);
    setFeedback(null);
    try {
      await update.mutateAsync({ mappings: requested });
      const refreshed = await resources.refetch();
      if (!refreshed.data || !pinningEqual(refreshed.data.vcpu_pinning, requested)) {
        setDraftOverride(null);
        const message = '적용 결과가 요청한 매핑과 달라 서버에서 다시 조회한 값으로 복원했습니다.';
        setFeedback({ tone: 'fault', message });
        toast.show(message, 'fault', 5000);
        return;
      }
      setDraftOverride(null);
      setFeedback({ tone: 'ok', message: '실제 매핑을 다시 조회해 적용 결과를 확인했습니다.' });
      toast.show(`${vm.name}: vCPU 핀닝 적용 완료`, 'ok');
    } catch (error) {
      setDraftOverride(null);
      const message = `적용에 실패해 서버 설정으로 복원했습니다. ${errorMessage(error)}`;
      setFeedback({ tone: 'fault', message });
      toast.show(message, 'fault', 5000);
    }
  };

  return (
    <Card
      title="vCPU 핀닝"
      accent={accent}
      actions={<span className={styles.pinningCardTag}>HARD AFFINITY</span>}
    >
      <div className={styles.pinningIntro}>
        <div>
          <strong>해당 VM의 vCPU가 사용할 물리 CPU 코어를 선택합니다.</strong>
          <p>
            선택한 pCPU는 해당 vCPU의 Xen hard affinity로 적용됩니다. 각 vCPU에는 하나 이상의 pCPU를 지정해야
            합니다.
          </p>
        </div>
        {resources.data && (
          <div className={styles.pinningObservation}>
            <span>{resources.data.pcpu_count} pCPU</span>
            <span>{live ? '실시간 부하 연결됨' : MOCK ? 'Mock 부하' : '최근 조회 부하'}</span>
          </div>
        )}
      </div>

      {resources.isPending && <div className={styles.pinningState}>실제 CPU 매핑을 조회하는 중입니다…</div>}
      {resources.error && (
        <div className={styles.pinningState} data-tone="waiting" role="status">
          <strong>vCPU 핀닝 정보를 불러올 수 없습니다.</strong>
          <span>{errorMessage(resources.error)}</span>
        </div>
      )}

      {resources.data && (
        <>
          <div className={styles.pinningTableWrap}>
            <table className={styles.pinningTable}>
              <thead>
                <tr>
                  <th scope="col">
                    <span>VM CPU (vCPU)</span>
                  </th>
                  {Array.from({ length: resources.data.pcpu_count }, (_, pcpu) => {
                    const exclusive = resources.data.exclusive_pcpus.includes(pcpu);
                    const load = loads.get(pcpu);
                    return (
                      <th key={pcpu} scope="col" data-exclusive={exclusive || undefined}>
                        <span>pCPU {pcpu}</span>
                        <small>{load == null ? '부하 미수신' : `${load.toFixed(1)}%`}</small>
                        {exclusive && <em>전용 코어</em>}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {draft.map((mapping) => {
                  const before = current.find((item) => item.vcpu === mapping.vcpu);
                  return (
                    <tr key={mapping.vcpu}>
                      <th scope="row">
                        <span>vCPU {mapping.vcpu}</span>
                        <small>허용 코어 {mapping.pcpus.length}개</small>
                      </th>
                      {Array.from({ length: resources.data.pcpu_count }, (_, pcpu) => {
                        const selected = mapping.pcpus.includes(pcpu);
                        const wasSelected = before?.pcpus.includes(pcpu) ?? false;
                        const lastSelected = selected && mapping.pcpus.length === 1;
                        return (
                          <td key={pcpu}>
                            <button
                              type="button"
                              className={styles.pinningCell}
                              aria-label={`vCPU ${mapping.vcpu} · pCPU ${pcpu}`}
                              aria-pressed={selected}
                              data-selected={selected || undefined}
                              data-changed={selected !== wasSelected || undefined}
                              disabled={update.isPending || lastSelected}
                              title={lastSelected ? '각 vCPU에는 최소 한 개의 pCPU가 필요합니다.' : undefined}
                              onClick={() => {
                                setFeedback(null);
                                setDraftOverride((value) => togglePcpu(value ?? current, mapping.vcpu, pcpu));
                              }}
                            >
                              <span aria-hidden="true">{selected ? '✓' : ''}</span>
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className={styles.pinningReview} data-dirty={dirty || undefined}>
            <div>
              <span>{dirty ? '변경 미리보기' : '현재 적용값'}</span>
              {dirty ? (
                <ul>
                  {changedVcpus.map((mapping) => {
                    const before = current.find((item) => item.vcpu === mapping.vcpu);
                    return (
                      <li key={mapping.vcpu}>
                        <strong>vCPU {mapping.vcpu}</strong>
                        <span>{before ? mappingLabel(before) : '미설정'}</span>
                        <b aria-hidden="true">→</b>
                        <span>{mappingLabel(mapping)}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p>서버에서 조회한 실제 매핑과 화면 선택값이 일치합니다.</p>
              )}
            </div>
            <div className={styles.pinningActions}>
              <Button
                variant="secondary"
                disabled={!dirty || update.isPending}
                onClick={() => {
                  setDraftOverride(null);
                  setFeedback(null);
                }}
              >
                변경 취소
              </Button>
              <Button disabled={!dirty || update.isPending} onClick={() => void apply()}>
                {update.isPending ? '적용 중…' : '핀닝 적용'}
              </Button>
            </div>
          </div>

          {feedback && (
            <div
              className={styles.pinningFeedback}
              data-tone={feedback.tone}
              role={feedback.tone === 'fault' ? 'alert' : 'status'}
            >
              <i aria-hidden="true" />
              {feedback.message}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
