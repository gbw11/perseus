import type { VmAction } from '../api/queries';
import styles from './pages.module.css';
import { formatElapsedTime, type VmOperation } from './useVmOperationProgress';

const ACTION_LABEL: Record<VmAction, string> = {
  start: '시작',
  stop: '정지',
  delete: '삭제',
};

export function VmOperationNotice({ operation, now }: { operation?: VmOperation; now: number }) {
  if (!operation) return null;
  const label = ACTION_LABEL[operation.action];

  if (operation.status === 'failed') {
    return (
      <div className={styles.vmOperationNotice} data-tone="fault" role="alert">
        <strong>{label} 실패</strong>
        <span>{operation.error}</span>
      </div>
    );
  }

  return (
    <div className={styles.vmOperationNotice} data-tone="pending" role="status" aria-live="polite">
      <i aria-hidden="true" />
      <strong>{label} 중…</strong>
      <span>경과 {formatElapsedTime(now - operation.startedAt)}</span>
    </div>
  );
}
