import styles from './pages.module.css';

/**
 * Memory 탭 카드의 메트릭 조회 오류 안내. TanStack Query 는 재조회가 실패해도 마지막 성공 응답(data)을
 * 유지하므로, 카드는 그 값을 계속 그린다.
 */
export function MetricErrorNotice() {
  return (
    <div className={styles.memoryNotice} data-tone="fault" role="alert">
      메트릭 연결 오류
      <br />
      마지막 정상 데이터가 있으면 그대로 표시합니다.
    </div>
  );
}
