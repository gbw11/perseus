import { useTelemetry } from '../telemetry/TelemetryProvider';
import styles from './screens.module.css';

/**
 * 자율주행 복귀 3·2·1 (MQTT 계약 v1.5 §5.2·§6). 자율주행이 복구되면 자동으로, 또는 운영자가 콘솔에서 재개를 누르면
 * 두 모니터 모두 가운데에 남은 초를 크게 띄운다 — 관객·운전자가 전환 순간을 미리 안다. 조작 요소는 없다.
 */
export function ResumeOverlay() {
  const { telemetry: t } = useTelemetry();
  if (t.resumeAt == null) return null;
  const remaining = t.resumeAt - t.now;
  if (remaining <= 0) return null;
  const count = Math.ceil(remaining / 1000);
  return (
    <div className={styles.resumeOverlay} role="status" aria-live="assertive">
      <span className={styles.resumeOverlayLabel}>
        {t.resumeAuto ? '자율주행 자동 복귀' : '자율주행으로 전환'}
      </span>
      <span key={count} className={styles.resumeOverlayCount}>
        {count}
      </span>
      <span className={styles.resumeOverlayHint}>
        수동주행 → 자율주행{t.resumeAuto ? ' · 관리 콘솔에서 취소할 수 있다' : ''}
      </span>
    </div>
  );
}
