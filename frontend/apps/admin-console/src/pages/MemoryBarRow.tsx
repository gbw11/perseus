import { useId, useState } from 'react';
import { poolLevel } from '../domain/memoryPools';
import styles from './pages.module.css';

/** 펼친 내용의 한 칸: 작은 회색 라벨 + 고정폭 값 */
export interface MemoryRowDetail {
  label: string;
  value: string;
  tone?: 'warn';
  /** 한글 상태 문구(단계 등)는 일반 글꼴. 기본은 숫자 값이라 고정폭 */
  text?: boolean;
}

/**
 * Memory 탭 영역 한 줄 아코디언: [이름] [막대 — 남는 너비] [⚠ 고정 자리] [사용률 %] [▸]
 * - 막대 색은 Network 탭 Pool 경고와 같은 기준(poolLevel: 80% 주의, 95% 위험), 그 밖에는 중립
 * - 이상이 있으면 접힌 행에는 ⚠ 아이콘만(모든 행 같은 폭 자리라 막대 끝이 같은 세로선), 내용은 펼친 내용에 주의 색으로
 * - 행(button)을 누르거나 Enter/Space 로 세부 항목을 펼치고 접는다. 여러 행을 함께 펼칠 수 있고 상태는 저장하지 않는다
 */
export function MemoryBarRow({
  name,
  label,
  barLabel,
  percent,
  expired = false,
  details,
  notes = [],
  problems = [],
}: {
  /** 행 이름 (화면 글자) */
  name: string;
  /** 행 묶음의 접근 이름 (예: "autonomous 지도 풀") */
  label: string;
  /** 막대(progressbar)의 접근 이름 */
  barLabel: string;
  /** 사용률(%). 값이 없으면 null → 막대 비움, "—" */
  percent: number | null;
  /** 관측 만료: 최근 관측값을 보이되 현재값(aria-valuenow)으로 알리지 않는다 */
  expired?: boolean;
  /** 펼친 내용 (2열) */
  details: readonly MemoryRowDetail[];
  /** 펼친 내용 아래 작은 회색 글자 (값이 없는 사유 등) */
  notes?: readonly string[];
  /** 이상 신호 (공간 부족 N회, 입력 만료, 관측 만료 등). 접힌 행 ⚠ 의 aria-label, 펼친 내용의 주의 색 글자 */
  problems?: readonly string[];
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const clamped = percent == null ? null : Math.max(0, Math.min(100, percent));
  const level = poolLevel(percent);
  const text = percent == null ? '—' : `${percent.toFixed(1)}%`;

  return (
    <div role="group" aria-label={label}>
      <button
        type="button"
        className={styles.memRowButton}
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.memRowName}>{name}</span>
        <span
          className={styles.memRowBar}
          role="progressbar"
          aria-label={barLabel}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={clamped == null || expired ? undefined : Math.round(clamped)}
          aria-valuetext={expired && percent != null ? `최근 관측 ${text} · 관측 만료` : undefined}
          data-level={level === 'ok' ? undefined : level}
        >
          <i style={{ width: `${clamped ?? 0}%` }} />
        </span>
        <span className={styles.memRowAlert}>
          {problems.length > 0 && (
            <span role="img" aria-label={`이상: ${problems.join(', ')}`}>
              ⚠
            </span>
          )}
        </span>
        <span className={styles.memRowValue}>{text}</span>
        <span className={styles.memRowChevron} aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
      </button>
      <div id={detailsId} className={styles.memRowDetails} hidden={!open}>
        {details.length > 0 && (
          <dl>
            {details.map((detail) => (
              <div key={detail.label} data-tone={detail.tone} data-kind={detail.text ? 'text' : undefined}>
                <dt>{detail.label}</dt>
                <dd>{detail.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {problems.length > 0 && (
          <ul className={styles.memRowProblems}>
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        )}
        {notes.map((note) => (
          <small key={note}>{note}</small>
        ))}
      </div>
    </div>
  );
}
