import { Button, Card, Modal, StatusBadge, useToast } from '@xenova/ui';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useVmAction, useVms, type VmAction } from '../api/queries';
import type { Vm } from '../api/types';
import { STATE_HEALTH, STATE_LABEL } from '../format';
import { osLabel, roleOf } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

/**
 * W-03 VM 목록 (화면 정의서 W-03, UI-02) — 초안. 화면 완성은 S15P31E110-55 (정송하) 가 이어받는다.
 * 대시보드(W-02)가 "누가 차를 모나" 라면 이 화면은 "VM 이 잘 떠 있고 자원은 얼마나 쓰나" — 주행 정보는 넣지 않고
 * Xen VM 관리(상태·OS·자원·에이전트·생명주기)만 3분할 카드로 보인다.
 */
export function VmListPage() {
  const vms = useVms(1000);
  const { drive } = useDrive();
  const action = useVmAction();
  const toast = useToast();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState<Vm | null>(null);

  const run = (vm: Vm, kind: VmAction) =>
    action.mutate(
      { vmId: vm.vm_id, action: kind },
      {
        onSuccess: () =>
          toast.show(
            `${vm.name}: ${kind === 'start' ? '시작' : kind === 'stop' ? '정지' : '삭제'} 요청 완료`,
            'ok',
          ),
        onError: (error) =>
          toast.show(error instanceof ApiError ? error.message : String(error), 'fault', 5000),
      },
    );

  const items = vms.data?.items ?? [];
  const running = items.filter((vm) => vm.state === 'running').length;
  const vcpus = items.reduce((sum, vm) => sum + vm.vcpus, 0);
  const memory = items.reduce((sum, vm) => sum + vm.memory_mib, 0);

  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>VM 관리 · Xen domU</div>
          <h1>
            VM {items.length}개 · <span style={{ color: 'var(--x-ok)' }}>실행 중 {running}</span>
          </h1>
        </div>
        <div className={styles.chips}>
          <span className={styles.chip}>할당 vCPU {vcpus} / 4 코어</span>
          <span className={styles.chip}>메모리 {memory} MiB</span>
        </div>
        <span className={styles.grow} />
        {vms.data && <ObservedClock at={vms.dataUpdatedAt} />}
        <Button onClick={() => navigate('/control/vms/new')}>+ VM 만들기</Button>
      </section>

      {vms.error && (
        <p className={styles.error} role="alert">
          {vms.error instanceof ApiError ? `${vms.error.message} (${vms.error.code})` : String(vms.error)}
        </p>
      )}

      {vms.isPending && <p className={styles.dim}>불러오는 중…</p>}
      {vms.data && items.length === 0 && <p className={styles.dim}>VM 이 없습니다.</p>}
      <div className={styles.cols3}>
        {items.map((vm) => {
          const role = roleOf(vm, drive);
          const zephyr = vm.guest_kind === 'zephyr';
          return (
            <Card
              key={vm.vm_id}
              className={styles.clickCard}
              role="link"
              tabIndex={0}
              aria-label={`${role?.title ?? vm.name} 상세`}
              onClick={() => navigate(`/control/vms/${vm.vm_id}`)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.target === e.currentTarget) navigate(`/control/vms/${vm.vm_id}`);
              }}
            >
              <div
                className={styles.appCard}
                style={{ borderTop: `3px solid ${role?.color ?? 'var(--x-border)'}` }}
              >
                <div className={styles.row}>
                  <b style={{ color: role?.color, fontSize: 'var(--x-text-lg)' }}>{role?.title ?? vm.name}</b>
                  <span className={styles.grow} />
                  <StatusBadge health={STATE_HEALTH[vm.state]} label={STATE_LABEL[vm.state]} size="sm" />
                </div>
                <div className={styles.eyebrow}>{vm.name}</div>
                <dl className={styles.kv}>
                  <dt>OS</dt>
                  <dd>{osLabel(vm)}</dd>
                  <dt>템플릿</dt>
                  <dd className={styles.mono}>
                    {vm.template_id} {vm.template_version}
                  </dd>
                  <dt>vCPU</dt>
                  <dd className={styles.mono}>{vm.vcpus}</dd>
                  <dt>메모리</dt>
                  <dd className={styles.mono}>{vm.memory_mib} MiB</dd>
                  <dt>통신</dt>
                  <dd className={styles.mono}>{zephyr ? 'vchan → dom0 중계' : (vm.ipv4 ?? '—')}</dd>
                </dl>
                {/* 버튼을 눌러도 상세로 넘어가지 않게 */}
                <div
                  className={styles.rowActions}
                  style={{ marginTop: 'var(--x-space-3)' }}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  <Button size="sm" onClick={() => run(vm, 'start')} disabled={vm.state === 'running'}>
                    시작
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => run(vm, 'stop')}
                    disabled={vm.state !== 'running'}
                  >
                    정지
                  </Button>
                  <span className={styles.grow} />
                  <Button size="sm" variant="danger" onClick={() => setDeleting(vm)}>
                    삭제
                  </Button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
      <p className={styles.dim}>
        주행 상태(누가 차를 모는지, 인계 준비)는 대시보드에서 본다. 이 화면은 VM 생명주기와 자원만 다룬다.
      </p>

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
              삭제
            </Button>
          </>
        }
      >
        {deleting && (
          <p>
            <b>{roleOf(deleting, drive)?.title ?? deleting.name}</b> ({deleting.name}) 을 강제 종료하고
            디스크까지 지운다. 되돌릴 수 없다.
          </p>
        )}
      </Modal>
    </div>
  );
}

/** 마지막으로 받은 시각 — 1초마다 다시 읽어 초 단위로 바뀌고, 바뀔 때 잠깐 반짝인다 */
function ObservedClock({ at }: { at: number }) {
  const time = new Date(at).toTimeString().slice(0, 8);
  return (
    <span className={styles.dim}>
      관측{' '}
      <b key={time} className={styles.tick}>
        {time}
      </b>
    </span>
  );
}
