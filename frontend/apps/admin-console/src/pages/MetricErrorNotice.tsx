import styles from './pages.module.css';

/**
 * Memory 탭 카드의 메트릭 조회 오류 안내. 이 화면은 useMetrics의 keepLastData로 마지막 성공 응답을
 * 유지하고, 원 표본의 만료는 별도로 표시한다. detail로 각 관측 화면의 처리 방식을 설명한다.
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
