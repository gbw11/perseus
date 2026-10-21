import { useTelemetry } from '../telemetry/TelemetryProvider';
import styles from './screens.module.css';

/**
 * 자율주행 재개 3·2·1 (MQTT 계약 v1.2 §6). 운영자가 콘솔에서 재개를 누르고 게이트웨이가 받아들이면
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
      <span className={styles.resumeOverlayLabel}>자율주행으로 전환</span>
      <span key={count} className={styles.resumeOverlayCount}>
        {count}
      </span>
      <span className={styles.resumeOverlayHint}>수동주행 → 자율주행</span>
    </div>
  );
}
