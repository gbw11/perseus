import { Button, Card, Modal, StatusBadge, useToast } from '@xenova/ui';
import { useState, type CSSProperties } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useVmAction, useVms, type VmAction } from '../api/queries';
import type { Vm } from '../api/types';
import { AGENT_LABEL, STATE_LABEL } from '../format';
import { appHealth, controlLabel, osLabel, roleOf } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

/**
 * W-03 VM 목록 (화면 정의서 W-03, UI-02) — 초안. 화면 완성은 S15P31E110-55 (정송하) 가 이어받는다.
 * 참고 시안: stitch_untitled_project_init/vm_xenova.
 */
export function VmListPage() {
  const vms = useVms();
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
          <div className={styles.eyebrow}>VM 목록</div>
          <h1>
            VM {items.length}개 · <span style={{ color: 'var(--x-ok)' }}>실행 중 {running}</span>
          </h1>
        </div>
        <span className={styles.mono}>
          할당 vCPU {vcpus} / 4 코어 · 메모리 {memory} MiB
        </span>
        <span className={styles.grow} />
        {vms.data && (
          <span className={styles.dim}>관측 {new Date(vms.data.observed_at).toLocaleTimeString()}</span>
        )}
        <Button onClick={() => navigate('/vms/new')}>+ VM 만들기</Button>
      </section>

      {vms.isPending && <p className={styles.dim}>불러오는 중…</p>}
      {vms.error && (
        <p className={styles.error} role="alert">
          {vms.error instanceof ApiError ? `${vms.error.message} (${vms.error.code})` : String(vms.error)}
        </p>
      )}

      <div className={styles.cols3}>
        {items.map((vm) => {
          const role = roleOf(vm, drive);
          const app = role ? drive?.apps[role.app] : undefined;
          const color = role?.color ?? 'var(--x-border)';
          const control = role ? controlLabel(role, drive) : null;
          const active = role && drive?.control?.active_app === role.app;
          return (
            <Card
              key={vm.vm_id}
              className={styles.appCard}
              style={{ '--accent': color } as CSSProperties}
              title={
                <Link to={`/vms/${vm.vm_id}`} style={{ color }}>
                  {role?.title ?? vm.name}
                </Link>
              }
              actions={
                <StatusBadge
                  health={role ? appHealth(role, vm, app) : vm.state === 'running' ? 'ok' : 'stopped'}
                  label={vm.state === 'running' ? undefined : STATE_LABEL[vm.state]}
                  size="sm"
                />
              }
            >
              <div className={styles.eyebrow}>
                {role ? `app_id: ${role.app} · ` : ''}VM 이름: {vm.name}
              </div>
              {control && (
                <div className={[styles.controlBar, active ? styles.controlOn : ''].join(' ')}>{control}</div>
              )}
              <div className={styles.rows}>
                <div>
                  <span>운영체제</span>
                  <span>{osLabel(vm)}</span>
                </div>
                <div>
                  <span>에이전트</span>
                  <span>
                    {vm.guest_kind === 'zephyr'
                      ? '없음 (Zephyr)'
                      : vm.agent_status
                        ? `${AGENT_LABEL[vm.agent_status]} · heartbeat 5초`
                        : '—'}
                  </span>
                </div>
                <div>
                  <span>통신</span>
                  <span>{vm.guest_kind === 'zephyr' ? 'vchan 중계 (dom0)' : (vm.ipv4 ?? '—')}</span>
                </div>
                <div>
                  <span>자원</span>
                  <span>
                    {vm.vcpus} vCPU / {vm.memory_mib} MiB{vm.guest_kind === 'zephyr' ? ' 고정' : ''}
                  </span>
                </div>
                <div>
                  <span>템플릿</span>
                  <span>
                    {vm.template_id} {vm.template_version}
                  </span>
                </div>
              </div>
              <div className={styles.rowActions}>
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
                <Button size="sm" variant="ghost" onClick={() => navigate(`/vms/${vm.vm_id}`)}>
                  상세
                </Button>
                <Button size="sm" variant="danger" onClick={() => setDeleting(vm)}>
                  삭제
                </Button>
              </div>
            </Card>
          );
        })}
      </div>
      {vms.data && items.length === 0 && <p className={styles.dim}>VM 이 없습니다.</p>}

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
