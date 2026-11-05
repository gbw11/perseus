import styles from './pages.module.css';

/**
 * Memory 탭 카드의 메트릭 조회 오류 안내. TanStack Query 는 재조회가 실패해도 마지막 성공 응답(data)을
 * 유지한다. 만료 규칙이 다른 관측 화면은 detail로 현재값 처리 방식을 설명한다.
 */
export function MetricErrorNotice({
  detail = '마지막 정상 데이터가 있으면 그대로 표시합니다.',
}: {
  detail?: string;
}) {
  return (
    <div className={styles.memoryNotice} data-tone="fault" role="alert">
      메트릭 연결 오류
      <br />
      {detail}
    </div>
  );
}
