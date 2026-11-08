import { useToast } from '@xenova/ui';
import { useEffect, useRef } from 'react';
import { ROLES } from '../domain/roles';
import { useDrive } from './DriveContext';
import styles from './layout.module.css';

/**
 * 자율주행 재개 3·2·1 알림 (MQTT 계약 v1.2 §6). 수동 → 자율 전환은 바로 하지 않는다:
 * 게이트웨이가 받아들이면(drive.resume.accepted) DriveState.resume 에 남은 시간이 오고, 0 이 되면 AUTO 로 바뀐다.
 * 그 사이 조건이 깨지거나 운영자가 취소하면 resume 이 사라진다 (drive.resume.cancelled). 운영자 취소(계약 v1.7)는
 * DriveState.resume_cancel.reason 으로 구분해 장애 알림을 띄우지 않는다. 콘솔 어느 화면에서나 보인다.
 */
export function ResumeNotice() {
  const { drive } = useDrive();
  const toast = useToast();
  const resume = drive?.resume ?? null;
  const mode = drive?.control?.mode ?? null;
  const cancelReason = drive?.resume_cancel?.reason ?? null;
  const counting = useRef(false);

  useEffect(() => {
    if (resume && !counting.current) {
      counting.current = true;
      toast.show('자율주행 재개 승인 — 3초 뒤 전환합니다. 수동 조작을 멈추세요.', 'ok', 3000);
    } else if (!resume && counting.current) {
      counting.current = false;
      if (mode === 'AUTO') toast.show('자율주행으로 전환됨', 'ok', 3000);
      else if (cancelReason === 'operator_cancelled')
        toast.show('자율주행 재개를 취소했습니다 — 수동주행을 유지합니다', 'ok', 3000);
      else toast.show('자율주행 재개 취소 — 조건이 깨져 수동주행을 유지합니다', 'fault', 5000);
    }
  }, [resume, mode, cancelReason, toast]);

  if (!resume) return null;
  const seconds = Math.max(1, Math.ceil(resume.remaining_ms / 1000));
  return (
    <div className={styles.countdown} role="status" aria-live="assertive">
      <span className={styles.countdownLabel}>
        <b style={{ color: ROLES.autonomous.color }}>자율주행</b> 으로 전환
      </span>
      <span key={seconds} className={styles.countdownNumber}>
        {seconds}
      </span>
      <span className={styles.countdownHint}>수동 입력은 전환 순간부터 무시됩니다</span>
    </div>
  );
}
