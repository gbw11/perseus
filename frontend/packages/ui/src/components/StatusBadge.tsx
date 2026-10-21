import styles from './StatusBadge.module.css';

/** RC카 시나리오 §4.3 표시 상태. VM·앱 화면이 같은 네 가지를 쓴다. */
export type Health = 'ok' | 'fault' | 'recovering' | 'stopped';

export const HEALTH_LABEL: Record<Health, string> = {
  ok: '정상',
  fault: '장애',
  recovering: '복구 중',
  stopped: '정지',
};

export interface StatusBadgeProps {
  health: Health;
  /** 기본 라벨 대신 쓸 글자 (예: '복구 중 12초') */
  label?: string;
  size?: 'sm' | 'md' | 'lg';
}

export function StatusBadge({ health, label, size = 'md' }: StatusBadgeProps) {
  return (
    <span className={[styles.badge, styles[health], styles[size]].join(' ')} role="status">
      <span className={styles.dot} aria-hidden="true" />
      {label ?? HEALTH_LABEL[health]}
    </span>
  );
}
