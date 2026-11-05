import { Button, Card, Modal, useToast } from '@xenova/ui';
import { useState, type CSSProperties } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '../api/client';
import { useVm, useVmResources, useVms, type VmAction } from '../api/queries';
import type { Vm } from '../api/types';
import { osLabel, sortVms } from '../domain/roles';
import { STATE_HEALTH, STATE_LABEL } from '../format';
import styles from './pages.module.css';
import { ResourceAdjustmentPanel } from './ResourceAdjustmentPanel';
import { VcpuPinningPanel } from './VcpuPinningPanel';
import { VmOperationNotice } from './VmOperationProgress';
import { useVmOperationProgress } from './useVmOperationProgress';

const VM_ACCENTS = ['var(--x-app-autonomous)', 'var(--x-app-manual)', 'var(--x-app-interaction)'] as const;

const AGENT_LABEL: Record<NonNullable<Vm['agent_status']>, string> = {
  ONLINE: '온라인',
  STALE: '갱신 지연',
  OFFLINE: '오프라인',
  UNKNOWN: '상태 확인 대기',
};

function pinningSummary(mappings: { vcpu: number; pcpus: number[] }[]): string {
  if (!mappings.length) return '조회 대기';
  const labels = mappings.map((mapping) => mapping.pcpus.join(', '));
  return labels.every((label) => label === labels[0]) ? `pCPU ${labels[0]}` : 'vCPU별 설정';
}

/** Dashboard의 관측 상세와 분리된 VM Control 전용 관리 상세. */
export function ControlVmDetailPage() {
  const { vmId = '' } = useParams();
  const vm = useVm(vmId);
  const resources = useVmResources(vmId);
  const vms = useVms();
  const vmOperations = useVmOperationProgress();
  const toast = useToast();
  const navigate = useNavigate();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const index = sortVms(vms.data?.items ?? []).findIndex((item) => item.vm_id === vmId);
  const accent = (index >= 0 ? VM_ACCENTS[index % VM_ACCENTS.length] : undefined) ?? 'var(--x-primary)';

  const run = (target: Vm, kind: VmAction) =>
    vmOperations.run(target, kind, {
      onSuccess: () => {
        toast.show(
          `${target.name}: ${kind === 'start' ? '시작' : kind === 'stop' ? '정지' : '삭제'} 요청 완료`,
          'ok',
        );
        if (kind === 'delete') navigate('/control');
      },
      onError: (_error, message) => toast.show(message, 'fault', 5000),
    });

  const operation = vmOperations.operations[vmId];
  const operationPending = operation?.status === 'pending';

  return (
    <div className={styles.controlDetailPage} style={{ '--vm-accent': accent } as CSSProperties}>
      <header className={styles.controlDetailHero}>
        <div className={styles.controlDetailTop}>
          <Link className={styles.controlBackLink} to="/control">
            <span aria-hidden="true">←</span>
            <strong>VM Management</strong>
          </Link>
          {vm.data && (
            <span className={styles.controlDetailStatus} data-health={STATE_HEALTH[vm.data.state]}>
              <i aria-hidden="true" />
              {STATE_LABEL[vm.data.state]}
            </span>
          )}
        </div>
        <div className={styles.controlDetailTitle}>
          <span className={styles.controlCardKicker}>VIRTUAL MACHINE CONTROL</span>
          <h1>{vm.data?.name ?? 'VM 관리 상세'}</h1>
          <p>{vm.data ? `${osLabel(vm.data)} · ${vm.data.template_id}` : 'VM 정보를 불러오는 중입니다.'}</p>
        </div>
      </header>

      {vm.isPending && <div className={styles.statePanel}>VM 관리 정보를 불러오는 중입니다…</div>}
      {vm.error && (
        <div className={styles.statePanel} role="alert">
          {vm.error instanceof ApiError ? `${vm.error.message} (${vm.error.code})` : String(vm.error)}
        </div>
      )}

      {vm.data && (
        <>
          <div className={styles.controlDetailGrid}>
            <Card title="VM 기본 정보" accent={accent}>
              <div className={styles.identityGrid}>
                <div className={styles.identityWide}>
                  <span className={styles.identityLabel}>VM ID</span>
                  <div className={styles.identityId}>
                    <code>{vm.data.vm_id}</code>
                  </div>
                </div>
                <div className={styles.identityFact}>
                  <span className={styles.identityLabel}>운영체제</span>
                  <strong>{osLabel(vm.data)}</strong>
                </div>
                <div className={styles.identityFact}>
                  <span className={styles.identityLabel}>예약 IPv4</span>
                  <strong>{vm.data.assigned_ipv4 ?? '—'}</strong>
                </div>
                <div className={styles.identityFact}>
                  <span className={styles.identityLabel}>템플릿</span>
                  <strong>{osLabel(vm.data)}</strong>
                  <code>
                    {vm.data.template_id} · v{vm.data.template_version}
                  </code>
                </div>
                <div className={styles.identityFact}>
                  <span className={styles.identityLabel}>Agent</span>
                  <span
                    className={styles.identityStatus}
                    data-tone={
                      vm.data.agent_status === 'ONLINE'
                        ? 'ok'
                        : vm.data.agent_status === 'STALE'
                          ? 'warning'
                          : vm.data.agent_status === 'OFFLINE'
                            ? 'fault'
                            : 'waiting'
                    }
                  >
                    <i aria-hidden="true" />
                    {AGENT_LABEL[vm.data.agent_status ?? 'UNKNOWN']}
                  </span>
                </div>
              </div>
            </Card>

            <Card title="생명주기 제어">
              <div className={styles.lifecycleState}>
                <span>현재 상태</span>
                <strong>{STATE_LABEL[vm.data.state]}</strong>
                <small>명령 실행 후 서버에서 다시 조회한 상태가 화면에 반영됩니다.</small>
              </div>
              <VmOperationNotice operation={operation} now={vmOperations.now} />
              <div className={styles.lifecycleActions}>
                <Button
                  onClick={() => run(vm.data, 'start')}
                  disabled={vm.data.state === 'running' || operationPending}
                >
                  VM 시작
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => run(vm.data, 'stop')}
                  disabled={vm.data.state !== 'running' || operationPending}
                >
                  VM 정지
                </Button>
              </div>
            </Card>
          </div>

          <Card title="할당 자원">
            <div className={styles.controlResourceSummary}>
              <div data-available="true">
                <span>vCPU</span>
                <strong>{resources.data?.vcpus ?? vm.data.vcpus}</strong>
                <small>개</small>
              </div>
              <div data-available="true">
                <span>메모리</span>
                <strong>{resources.data?.memory_mib ?? vm.data.memory_mib}</strong>
                <small>MiB</small>
              </div>
              <div data-available={resources.data ? 'true' : 'false'}>
                <span>vCPU 핀닝</span>
                <strong>{resources.data ? pinningSummary(resources.data.vcpu_pinning) : '—'}</strong>
                <small>{resources.data ? 'Xen hard affinity' : '설정 미조회'}</small>
              </div>
            </div>
          </Card>

          <ResourceAdjustmentPanel key={`tuning-${vm.data.vm_id}`} vm={vm.data} accent={accent} />
          <VcpuPinningPanel key={`pinning-${vm.data.vm_id}`} vm={vm.data} accent={accent} />

          <section className={styles.dangerZone} aria-labelledby="danger-zone-title">
            <div>
              <span>DANGER ZONE</span>
              <h2 id="danger-zone-title">VM 삭제</h2>
              <p>VM을 종료하고 연결된 디스크를 삭제합니다. 삭제한 VM은 복구할 수 없습니다.</p>
            </div>
            <Button variant="danger" onClick={() => setDeleteOpen(true)} disabled={operationPending}>
              VM 삭제
            </Button>
          </section>
        </>
      )}

      <Modal
        open={deleteOpen}
        title="VM 삭제"
        onClose={() => setDeleteOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              취소
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (vm.data) run(vm.data, 'delete');
                setDeleteOpen(false);
              }}
            >
              VM 삭제
            </Button>
          </>
        }
      >
        {vm.data && (
          <div className={styles.deleteConfirm}>
            <span aria-hidden="true">!</span>
            <div>
              <strong>‘{vm.data.name}’ VM을 삭제하시겠습니까?</strong>
              <p>
                VM을 종료하고 연결된 디스크를 삭제합니다.
                <span className={styles.deleteIrreversible}>이 작업은 되돌릴 수 없습니다.</span>
              </p>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
