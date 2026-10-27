import { Card, StatusBadge } from '@xenova/ui';
import styles from './pages.module.css';

/** Jetson 카메라·인지 현황 페이지의 자리. 실제 스트림 연동은 S15P31E110-306에서 진행한다. */
export function VisionPage() {
  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>VISION · JETSON</div>
          <h1>카메라·인지 모니터링</h1>
        </div>
        <span className={styles.grow} />
        <StatusBadge health="stopped" label="연동 준비 중" size="sm" />
      </section>
      <div className={styles.cols2}>
        <Card title="카메라 스트림">
          <div className={styles.placeholder}>
            <strong>영상 영역</strong>
            <span>Jetson 스트림 계약이 확정되면 실시간 영상을 연결합니다.</span>
          </div>
        </Card>
        <Card title="인지 상태">
          <div className={styles.rows}>
            <div>
              <span>Jetson 연결</span>
              <span>확인 대기</span>
            </div>
            <div>
              <span>AI 인식 프로세스</span>
              <span>확인 대기</span>
            </div>
            <div>
              <span>CPU · GPU · Memory</span>
              <span>메트릭 연동 예정</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
