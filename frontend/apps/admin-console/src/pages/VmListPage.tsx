import { Button, Card, Modal, StatusBadge, useToast } from '@xenova/ui';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router';
import { useVms, type VmAction } from '../api/queries';
import type { Vm } from '../api/types';
import { osLabel, sortVms } from '../domain/roles';
import { STATE_HEALTH, STATE_LABEL } from '../format';
import styles from './pages.module.css';
import { VmOperationNotice } from './VmOperationProgress';
import { useVmOperationProgress } from './useVmOperationProgress';
import {
  finishVmCreation,
  isVmCreationPending,
  matchesVmCreation,
  usePendingVmCreations,
} from './vmCreationProgress';
import { LiveDataAlert } from '../layout/LiveDataAlert';

const VM_ACCENTS = ['var(--x-app-autonomous)', 'var(--x-app-manual)', 'var(--x-app-interaction)'] as const;

/** VM 생명주기와 할당 자원을 관리하는 목록. 주행 모드·제어권은 Overview에서만 다룬다. */
export function VmListPage() {
  const vms = useVms(1000);
  const vmOperations = useVmOperationProgress();
  const toast = useToast();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState<Vm | null>(null);
  const pendingCreations = usePendingVmCreations();

  const run = (vm: Vm, kind: VmAction) =>
    vmOperations.run(vm, kind, {
      onSuccess: () =>
        toast.show(
          `${vm.name}: ${kind === 'start' ? '시작' : kind === 'stop' ? '정지' : '삭제'} 요청 완료`,
          'ok',
        ),
      onError: (_error, message) => toast.show(message, 'fault', 5000),
    });

  const items = useMemo(() => sortVms(vms.data?.items ?? []), [vms.data?.items]);
  const running = items.filter((vm) => vm.state === 'running').length;
  const vcpus = items.reduce((sum, vm) => sum + vm.vcpus, 0);
  const memory = items.reduce((sum, vm) => sum + vm.memory_mib, 0);
  const visiblePendingCreations = useMemo(
    () =>
      pendingCreations.filter(
        (creation) => !items.some((vm) => matchesVmCreation(vm, creation) && vm.state === 'running'),
      ),
    [items, pendingCreations],
  );

  useEffect(() => {
    pendingCreations.forEach((creation) => {
      if (items.some((vm) => matchesVmCreation(vm, creation) && vm.state === 'running')) {
        finishVmCreation(creation.name);
      }
    });
  }, [items, pendingCreations]);

  return (
    <div className={styles.controlPage}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>VM CONTROL</span>
          <h2>VM Management</h2>
          <p className={styles.headingDescription}>VM 생성과 생명주기, 할당 자원을 관리합니다.</p>
        </div>
        <div className={styles.controlHeadingActions}>
          {vms.data && (
            <ObservedClock
              at={observedAt(vms.data.observed_at, vms.dataUpdatedAt)}
              receivedAt={vms.dataUpdatedAt}
              refreshing={!vms.isFetchedAfterMount}
            />
          )}
          <Button onClick={() => navigate('/control/vms/new')}>+ VM 생성</Button>
        </div>
      </header>

      <section className={styles.controlSummary} aria-label="VM 자원 요약">
        <SummaryItem label="TOTAL VMS" value={`${items.length}대`} />
        <SummaryItem label="RUNNING" value={`${running}대`} tone="ok" />
        <SummaryItem label="ALLOCATED VCPU" value={`${vcpus} / 4 cores`} />
        <SummaryItem label="ALLOCATED MEMORY" value={`${memory} MiB`} />
      </section>

      <LiveDataAlert />
      {vms.isPending && <div className={styles.statePanel}>VM 목록을 불러오는 중입니다…</div>}
      {vms.data && items.length === 0 && (
        <div className={styles.controlEmpty}>
          <strong>등록된 VM이 없습니다.</strong>
          <span>새 VM을 생성해 관리 목록에 추가할 수 있습니다.</span>
          <Button onClick={() => navigate('/control/vms/new')}>VM 생성</Button>
        </div>
      )}

      {visiblePendingCreations.length > 0 && (
        <div className={styles.pendingCreationSummary} role="status">
          <i aria-hidden="true" />
          <div>
            <strong>VM 생성 작업 진행 중</strong>
            <span>{visiblePendingCreations.map((creation) => creation.name).join(', ')}</span>
          </div>
          <small>목록을 자동으로 갱신해 완료 상태를 확인합니다.</small>
        </div>
      )}

      <div className={styles.controlGrid}>
        {items.map((vm, index) => {
          const accent = VM_ACCENTS[index % VM_ACCENTS.length];
          const operation = vmOperations.operations[vm.vm_id];
          const operationPending = operation?.status === 'pending';
          const creationPending = isVmCreationPending(vm, pendingCreations);
          const controlsDisabled = operationPending || creationPending;
          return (
            <Card
              key={vm.vm_id}
              className={`${styles.controlVmCard} ${creationPending ? '' : styles.clickCard}`}
              style={{ '--vm-accent': accent } as CSSProperties}
              role={creationPending ? undefined : 'link'}
              tabIndex={creationPending ? -1 : 0}
              aria-label={`${vm.name} ${creationPending ? '생성 중' : '상세'}`}
              onClick={() => {
                if (!creationPending) navigate(`/control/vms/${vm.vm_id}`);
              }}
              onKeyDown={(event) => {
                if (!creationPending && event.key === 'Enter' && event.target === event.currentTarget) {
                  navigate(`/control/vms/${vm.vm_id}`);
                }
              }}
            >
              <div className={styles.controlCardBody}>
                <div className={styles.controlCardTop}>
                  <div>
                    <span className={styles.controlCardKicker}>VIRTUAL MACHINE</span>
                    <h3>{vm.name}</h3>
                    <p>{osLabel(vm)}</p>
                  </div>
                  <StatusBadge
                    health={creationPending ? 'recovering' : STATE_HEALTH[vm.state]}
                    label={creationPending ? '생성 중' : STATE_LABEL[vm.state]}
                    size="sm"
                  />
                </div>

                <dl className={styles.controlFacts}>
                  <div>
                    <dt>vCPU</dt>
                    <dd>{vm.vcpus}</dd>
                  </div>
                  <div>
                    <dt>메모리</dt>
                    <dd>{vm.memory_mib} MiB</dd>
                  </div>
                  <div className={styles.controlFactWide}>
                    <dt>템플릿</dt>
                    <dd>
                      {vm.template_id} <small>v{vm.template_version}</small>
                    </dd>
                  </div>
                  <div className={styles.controlFactWide}>
                    <dt>예약 IPv4</dt>
                    <dd>{vm.assigned_ipv4 ?? '—'}</dd>
                  </div>
                </dl>

                <VmOperationNotice operation={operation} now={vmOperations.now} />
                {creationPending && (
                  <div className={styles.vmCreationCardNotice} role="status">
                    <i aria-hidden="true" />
                    <span>dom0에서 VM을 생성하고 있습니다.</span>
                  </div>
                )}

                <div
                  className={styles.controlCardActions}
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => event.stopPropagation()}
                >
                  <div>
                    <Button
                      size="sm"
                      onClick={() => run(vm, 'start')}
                      disabled={vm.state === 'running' || controlsDisabled}
                    >
                      시작
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => run(vm, 'stop')}
                      disabled={vm.state !== 'running' || controlsDisabled}
                    >
                      정지
                    </Button>
                  </div>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => setDeleting(vm)}
                    disabled={controlsDisabled}
                  >
                    삭제
                  </Button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      <Modal
        open={deleting !== null}
        title="VM 삭제"
        onClose={() => setDeleting(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              취소
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (deleting) run(deleting, 'delete');
                setDeleting(null);
              }}
            >
              VM 삭제
            </Button>
          </>
        }
      >
        {deleting && (
          <div className={styles.deleteConfirm}>
            <span aria-hidden="true">!</span>
            <div>
              <strong>‘{deleting.name}’ VM을 삭제하시겠습니까?</strong>
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

function SummaryItem({ label, value, tone }: { label: string; value: string; tone?: 'ok' }) {
  return (
    <div className={styles.controlSummaryItem} data-tone={tone}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

/** 서버가 dom0 목록을 받은 시각(observed_at). 읽을 수 없으면 화면이 응답을 받은 시각 */
function observedAt(iso: string | undefined, fallback: number): number {
  const parsed = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** at: 서버가 dom0 목록을 받은 시각, receivedAt: 화면이 그 응답을 받은 시각 (둘의 차이가 서버 목록의 나이) */
function ObservedClock({
  at,
  receivedAt,
  refreshing,
}: {
  at: number;
  receivedAt: number;
  refreshing: boolean;
}) {
  const time = new Date(at).toTimeString().slice(0, 8);
  const ageSeconds = Math.max(0, Math.round((receivedAt - at) / 1000));
  return (
    <span className={styles.observedTime}>
      마지막 확인
      <b key={time} className={styles.tick}>
        {time}
      </b>
      {refreshing ? ' · 갱신 중' : ageSeconds >= 3 ? ` · ${ageSeconds}초 전` : ''}
    </span>
  );
}
