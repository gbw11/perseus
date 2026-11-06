import { VM_ACTION_LABEL as ACTION_LABEL } from '../format';
import styles from './pages.module.css';
import { formatElapsedTime, type VmOperation } from './useVmOperationProgress';

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
