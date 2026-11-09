import type { ReactNode } from 'react';
import type { MetricSample } from '../api/types';
import {
  APP_MEMORY_REGIONS,
  buildAppMemorySeries,
  formatAppBytes,
  type AppMemorySnapshot,
} from '../domain/appMemory';
import { MemoryBarRow, type MemoryRowDetail } from './MemoryBarRow';
import styles from './pages.module.css';
import { useNow } from '../useNow';

const GROUP_KEY = { 자동: 'auto', 공통: 'common', 수동: 'manual' } as const;

/** 앱 내부 점유와 원 업무 상태를 보여준다. RAM 확보량과 합산하지 않는다. */
export function AppMemoryPools({
  vmId,
  vmName,
  samples,
  children,
}: {
  vmId: string;
  vmName: string;
  samples: MetricSample[];
  children?: ReactNode;
}) {
  // 화면 전체가 같이 쓰는 1초 시계 — 관측 만료(lease) 판정에 쓴다
  const now = useNow();
  const series = buildAppMemorySeries(samples, vmId, now);
  return (
    <section aria-label={`${vmName} 앱 버퍼 점유`} className={styles.appMemorySection}>
      <header>
        <h3>메모리 상세</h3>
      </header>
      {(['자동', '공통', '수동'] as const).map((group) => (
        <section key={group} className={styles.memoryGroup} aria-label={`${vmName} ${group} 메모리`}>
          {/* 그룹 구분: 제목 왼쪽 얇은 세로선 + 제목 글자색만. 영역 행·숫자는 중립색 */}
          <h4 className={styles.memoryGroupTitle} data-group={GROUP_KEY[group]}>
            {group}
          </h4>
          <div className={styles.memRows}>
            {APP_MEMORY_REGIONS.filter((region) => region.group === group).map(({ id, label }) => (
              <PoolRow key={id} label={label} vmName={vmName} current={series[id].current} />
            ))}
            {group === '공통' && children}
          </div>
        </section>
      ))}
    </section>
  );
}

function PoolRow({ label, vmName, current }: { label: string; vmName: string; current: AppMemorySnapshot }) {
  const usable = current.state === 'ok';
  const expired = usable && current.stale;
  const buffer = usable ? current.buffer : null;
  const failures = usable ? current.capacity_failure_total : null;
  const bytes = (value: bigint | null) => formatAppBytes(value);
  // 펼친 내용: 작은 회색 라벨 + 고정폭 값
  const details: MemoryRowDetail[] = usable
    ? [
        { label: '사용 / 용량', value: `${bytes(current.used_bytes)} / ${bytes(current.capacity_bytes)}` },
        { label: expired ? '관측 당시 여유' : '현재 여유', value: bytes(current.free_bytes) },
        { label: '최고 점유 · 부팅 후', value: bytes(current.high_water_bytes) },
        { label: '정적 예약', value: bytes(current.reserved_bytes) },
        // 단계는 실제로 보관·대기·실행 중일 때만 (비어 있음은 표시하지 않는다). 한글 문구라 일반 글꼴
        ...(buffer && buffer.phase !== 'empty'
          ? [
              {
                label: '단계',
                value: `${expired ? '관측 당시 · ' : ''}${phaseLabel(buffer.phase)}`,
                text: true,
              },
            ]
          : []),
      ]
    : [];
  // 이상 신호: 접힌 행에는 ⚠ 만, 펼친 내용에 주의 색 문구
  const problems = [
    ...(failures != null && failures > 0n ? [`공간 부족 ${failures.toLocaleString('ko-KR')}회`] : []),
    // 이상 입력(무효·만료·문맥 변경)만. 없음·분류 전·수락은 정상
    ...(buffer && INPUT_PROBLEM[buffer.input_status] ? [INPUT_PROBLEM[buffer.input_status]!] : []),
    ...(buffer?.revoked ? ['철회 관측 · 반환 여부는 점유로 확인'] : []),
    ...(buffer && buffer.source_rc < 0 ? [`입력·작업 오류 ${buffer.source_rc}`] : []),
    ...(expired ? ['관측 만료 · 최근 관측값'] : []),
  ];
  const notes = usable
    ? []
    : [`${reasonLabel(current)}${current.sourceRc == null ? '' : ` (${current.sourceRc})`}`];
  return (
    <MemoryBarRow
      name={label}
      label={`${vmName} ${label}`}
      barLabel={`${vmName} ${label} 점유율`}
      percent={usable ? current.usagePercent : null}
      expired={expired}
      details={details}
      notes={notes}
      problems={problems}
    />
  );
}

/** 이상으로 보는 입력 상태와 펼친 내용에 쓰는 문구 */
const INPUT_PROBLEM: Partial<Record<NonNullable<AppMemorySnapshot['buffer']>['input_status'], string>> = {
  invalid: '입력 무효',
  expired: '입력 만료',
  context_changed: '입력 문맥 변경',
};

function phaseLabel(phase: NonNullable<AppMemorySnapshot['buffer']>['phase']): string {
  return {
    empty: '비어 있음',
    stored: '입력 보관',
    pending: '작업 대기',
    running: '계산 실행 중',
    retained: '결과 보관',
  }[phase];
}
function reasonLabel(snapshot: AppMemorySnapshot): string {
  if (snapshot.reason === 'owner_busy') return '소유자 사용 중 · 이번 표본 미측정';
  if (snapshot.reason === 'sample_expired') return '원 표본 만료 · 미측정';
  if (snapshot.reason === 'unsupported') return '미지원';
  return snapshot.reason ?? '미측정';
}
