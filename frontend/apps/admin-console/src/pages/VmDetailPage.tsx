import { Card, StatusBadge } from '@xenova/ui';
import { lazy, Suspense } from 'react';
import { Link, useParams } from 'react-router';
import { ApiError } from '../api/client';
import { useVm } from '../api/queries';
import { AGENT_LABEL, GUEST_LABEL, STATE_HEALTH, STATE_LABEL } from '../format';
import styles from './pages.module.css';

const ConsolePanel = lazy(() =>
  import('../console/ConsolePanel').then((module) => ({ default: module.ConsolePanel })),
);

/** 기본형 — 단건 조회 흐름 확인용. VM 상세 화면은 정수환 작업 */
export function VmDetailPage() {
  const { vmId = '' } = useParams();
  const vm = useVm(vmId);

  return (
    <div className={styles.detail}>
      <Card title={vm.data?.name ?? 'VM 상세'} actions={<Link to="/vms">목록으로</Link>}>
        {vm.isPending && <p className={styles.dim}>불러오는 중…</p>}
        {vm.error && (
          <p className={styles.error} role="alert">
            {vm.error instanceof ApiError ? `${vm.error.message} (${vm.error.code})` : String(vm.error)}
          </p>
        )}
        {vm.data && (
          <dl className={styles.kv}>
            <dt>상태</dt>
            <dd>
              <StatusBadge health={STATE_HEALTH[vm.data.state]} label={STATE_LABEL[vm.data.state]} />
            </dd>
            <dt>VM ID</dt>
            <dd className={styles.mono}>{vm.data.vm_id}</dd>
            <dt>종류</dt>
            <dd>{GUEST_LABEL[vm.data.guest_kind]}</dd>
            <dt>템플릿</dt>
            <dd className={styles.mono}>
              {vm.data.template_id} {vm.data.template_version}
            </dd>
            <dt>에이전트</dt>
            <dd>{vm.data.agent_status ? AGENT_LABEL[vm.data.agent_status] : '—'}</dd>
            <dt>IP</dt>
            <dd className={styles.mono}>{vm.data.ipv4 ?? '—'}</dd>
            <dt>자원</dt>
            <dd className={styles.mono}>
              {vm.data.vcpus} vCPU · {vm.data.memory_mib} MiB
            </dd>
          </dl>
        )}
      </Card>
      {vm.data && (
        <Suspense fallback={<p className={styles.dim}>콘솔 준비 중…</p>}>
          <ConsolePanel key={vm.data.vm_id} vm={vm.data} />
        </Suspense>
      )}
    </div>
  );
}
