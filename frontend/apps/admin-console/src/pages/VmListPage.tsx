import { Button, Card, StatusBadge, useToast } from '@xenova/ui';
import { Link } from 'react-router';
import { ApiError } from '../api/client';
import { useVmAction, useVms, type VmAction } from '../api/queries';
import { AGENT_LABEL, GUEST_LABEL, STATE_HEALTH, STATE_LABEL } from '../format';
import styles from './pages.module.css';

/** 기본형 — 목록·조작 흐름 확인용. 배포판별 카드 화면은 S15P31E110-55 */
export function VmListPage() {
  const vms = useVms();
  const action = useVmAction();
  const toast = useToast();

  const run = (vmId: string, name: string, kind: VmAction) =>
    action.mutate(
      { vmId, action: kind },
      {
        onSuccess: () =>
          toast.show(
            `${name}: ${kind === 'start' ? '시작' : kind === 'stop' ? '정지' : '삭제'} 요청 완료`,
            'ok',
          ),
        onError: (error) =>
          toast.show(error instanceof ApiError ? error.message : String(error), 'fault', 5000),
      },
    );

  return (
    <Card
      title="VM 목록"
      actions={
        <span className={styles.dim}>
          {vms.data ? `관측 ${new Date(vms.data.observed_at).toLocaleTimeString()}` : ''}
        </span>
      }
    >
      {vms.isPending && <p className={styles.dim}>불러오는 중…</p>}
      {vms.error && (
        <p className={styles.error} role="alert">
          {vms.error instanceof ApiError ? `${vms.error.message} (${vms.error.code})` : String(vms.error)}
        </p>
      )}
      {vms.data && (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>이름</th>
              <th>종류</th>
              <th>상태</th>
              <th>에이전트</th>
              <th>IP</th>
              <th>자원</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {vms.data.items.map((vm) => (
              <tr key={vm.vm_id}>
                <td>
                  <Link to={`/vms/${vm.vm_id}`}>{vm.name}</Link>
                </td>
                <td>
                  {GUEST_LABEL[vm.guest_kind]} · {vm.template_id}
                </td>
                <td>
                  <StatusBadge health={STATE_HEALTH[vm.state]} label={STATE_LABEL[vm.state]} size="sm" />
                </td>
                <td>{vm.agent_status ? AGENT_LABEL[vm.agent_status] : '—'}</td>
                <td className={styles.mono}>{vm.ipv4 ?? '—'}</td>
                <td className={styles.mono}>
                  {vm.vcpus} vCPU · {vm.memory_mib} MiB
                </td>
                <td className={styles.rowActions}>
                  <Button
                    size="sm"
                    onClick={() => run(vm.vm_id, vm.name, 'start')}
                    disabled={vm.state === 'running'}
                  >
                    시작
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => run(vm.vm_id, vm.name, 'stop')}
                    disabled={vm.state === 'stopped'}
                  >
                    정지
                  </Button>
                </td>
              </tr>
            ))}
            {vms.data.items.length === 0 && (
              <tr>
                <td colSpan={7} className={styles.dim}>
                  VM 이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </Card>
  );
}
