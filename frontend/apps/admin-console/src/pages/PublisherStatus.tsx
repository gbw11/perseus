import { useTelemetryHeartbeat } from '../api/queries';
import { publisherObservation } from '../domain/telemetryPublisher';
import styles from './pages.module.css';

/** Publisher diagnostics are independent from VM, v1 Agent and application liveness. */
export function PublisherStatus({ vmId }: { vmId: string }) {
  const query = useTelemetryHeartbeat(vmId);
  const { report, label } = publisherObservation(vmId, query);
  const received = report?.received_at;
  const successes = report?.last_collection_success_uptime_ms;
  const failures = report?.collection_failures_total;
  return (
    <section className={styles.publisherStatus} aria-label="텔레메트리 수집기 상태">
      <div className={styles.rows}>
        <div>
          <span>수집기 상태 (v2)</span>
          <strong>{label}</strong>
        </div>
        <div>
          <span>마지막 heartbeat 수신</span>
          <span title={received ?? undefined}>
            {received ? new Date(received).toLocaleTimeString('ko-KR') : '—'}
          </span>
        </div>
        <div>
          <span>마지막 수집 성공 (부팅 후)</span>
          <span>{successes == null ? '—' : `${successes} ms`}</span>
        </div>
        <div>
          <span>수집 실패 누계 · 참고</span>
          <span>
            {failures == null
              ? '—'
              : Number.isSafeInteger(failures)
                ? failures.toLocaleString('ko-KR')
                : '표시 범위 초과'}
          </span>
        </div>
      </div>
    </section>
  );
}
