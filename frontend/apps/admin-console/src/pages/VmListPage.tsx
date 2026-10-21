import { Button, Card, Modal, StatusBadge, useToast } from '@xenova/ui';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useVmAction, useVms, type VmAction } from '../api/queries';
import type { Vm } from '../api/types';
import { AGENT_LABEL, STATE_HEALTH, STATE_LABEL } from '../format';
import { osLabel, roleOf } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

/**
 * W-03 VM 목록 (화면 정의서 W-03, UI-02) — 초안. 화면 완성은 S15P31E110-55 (정송하) 가 이어받는다.
 * 대시보드(W-02)가 "누가 차를 모나" 라면 이 화면은 "VM 이 잘 떠 있고 자원은 얼마나 쓰나" — 주행 정보는 넣지 않고
 * Xen VM 관리(상태·OS·자원·에이전트·생명주기)만 표로 보인다.
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
        {vms.data && (
          <span className={styles.dim}>
            관측 {new Date(vms.data.observed_at).toLocaleTimeString()} (5초마다)
          </span>
        )}
        <Button onClick={() => navigate('/vms/new')}>+ VM 만들기</Button>
      </section>

      {vms.error && (
        <p className={styles.error} role="alert">
          {vms.error instanceof ApiError ? `${vms.error.message} (${vms.error.code})` : String(vms.error)}
        </p>
      )}

      <Card>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>VM</th>
              <th>상태</th>
              <th>OS · 템플릿</th>
              <th>vCPU</th>
              <th>메모리</th>
              <th>통신</th>
              <th>에이전트</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {vms.isPending && (
              <tr>
                <td colSpan={8} className={styles.dim}>
                  불러오는 중…
                </td>
              </tr>
            )}
            {items.map((vm) => {
              const role = roleOf(vm, drive);
              return (
                <tr key={vm.vm_id}>
                  <td>
                    <Link to={`/vms/${vm.vm_id}`} style={{ color: role?.color, fontWeight: 700 }}>
                      {role?.title ?? vm.name}
                    </Link>
                    <div className={styles.eyebrow}>
                      {vm.name}
                      {role ? ` · ${role.app}` : ''}
                    </div>
                  </td>
                  <td>
                    <StatusBadge health={STATE_HEALTH[vm.state]} label={STATE_LABEL[vm.state]} size="sm" />
                  </td>
                  <td>
                    {osLabel(vm)}
                    <div className={styles.eyebrow}>
                      {vm.template_id} {vm.template_version}
                    </div>
                  </td>
                  <td className={styles.mono}>{vm.vcpus}</td>
                  <td className={styles.mono}>
                    {vm.memory_mib} MiB{vm.guest_kind === 'zephyr' ? ' (고정)' : ''}
                  </td>
                  <td className={styles.mono}>
                    {vm.guest_kind === 'zephyr' ? 'vchan 중계' : (vm.ipv4 ?? '—')}
                  </td>
                  <td>
                    {vm.guest_kind === 'zephyr'
                      ? '없음 (Zephyr)'
                      : vm.agent_status
                        ? AGENT_LABEL[vm.agent_status]
                        : '—'}
                  </td>
                  <td>
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
                      <Button size="sm" variant="danger" onClick={() => setDeleting(vm)}>
                        삭제
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {vms.data && items.length === 0 && (
              <tr>
                <td colSpan={8} className={styles.dim}>
                  VM 이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
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
