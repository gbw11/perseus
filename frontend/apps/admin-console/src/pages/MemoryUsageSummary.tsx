import styles from './pages.module.css';

/**
 * 메모리 사용률 요약 (Zephyr 관리 RAM·Linux 공통). 카드 첫 섹션으로 박스 없이:
 * 큰 제목 + 큰 %, 사용량 / 전체, 여유, 막대. 만료·미측정이면 아래에 주의 색 사유
 */
export function MemoryUsageSummary({
  label,
  ariaLabel,
  percent,
  usedText,
  totalText,
  freeLabel,
  freeText,
  freeTitle,
  expired = false,
  notice = null,
}: {
  /** 섹션 접근 이름 (예: "autonomous 메모리 사용률"). 막대 이름도 같다 */
  ariaLabel: string;
  label: string;
  percent: number | null;
  usedText: string;
  totalText: string;
  freeLabel: string;
  freeText: string;
  /** 여유 값의 기준 설명 */
  freeTitle?: string;
  expired?: boolean;
  notice?: string | null;
}) {
  const clamped = percent == null ? null : Math.max(0, Math.min(100, percent));
  const text = percent == null ? '—' : `${percent.toFixed(1)}%`;
  return (
    <section className={styles.memUsage} aria-label={ariaLabel}>
      <div className={styles.memUsageHead}>
        <span>{label}</span>
        <strong>{text}</strong>
      </div>
      <div className={styles.memUsageAmount}>
        <strong>{usedText}</strong>
        <span>/ {totalText}</span>
      </div>
      <div className={styles.memUsageFree} title={freeTitle}>
        <span>{freeLabel}</span>
        <strong>{freeText}</strong>
      </div>
      <div
        className={styles.memUsageBar}
        role="progressbar"
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped == null || expired ? undefined : Math.round(clamped)}
        aria-valuetext={expired && percent != null ? `최근 관측 ${text} · 관측 만료` : undefined}
      >
        <i style={{ width: `${clamped ?? 0}%` }} />
      </div>
      {notice && (
        <p className={styles.memUsageNotice} data-tone="warn">
          {notice}
        </p>
      )}
    </section>
  );
}
