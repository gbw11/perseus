import type { TelemetryHeartbeatResponse } from '../api/types';
import { ago } from '../domain/roles';

/**
 * 배지 글자만으로 무엇의 상태인지 알기 어려운 라벨. Linux 카드에서는 "Agent 연결됨" 배지 옆에 놓이므로
 * 수집기(v2 telemetry heartbeat) 상태라는 것을 앞에 붙인다. "수집 중"·"수집 저하" 는 이미 뜻이 분명하다.
 */
const PREFIXED_LABELS = new Set(['관측 만료', '미수신', '종료 중']);

/** publisherObservation 의 label 을 배지 글자로. 판단은 바꾸지 않고 글자만 붙인다 */
export function collectorBadgeLabel(label: string): string {
  return PREFIXED_LABELS.has(label) ? `수집기 · ${label}` : label;
}

/**
 * 백엔드는 누계를 BigInteger JSON 숫자로 보낸다. 2^53 을 넘으면 브라우저가 받는 순간 정밀도가 깨지므로
 * VM 상세(PublisherStatus)와 같이 "표시 범위 초과" 로 쓴다
 */
export function heartbeatCount(value: number | null | undefined): string {
  if (value == null) return '—';
  return Number.isSafeInteger(value) ? value.toLocaleString('ko-KR') : '표시 범위 초과';
}

/**
 * 마지막 수집 성공 경과. last_collection_success_age_ms 는 heartbeat 를 보낸 시점 기준이므로
 * 받은 뒤 흐른 시간을 더한다
 */
export function lastCollectionSuccess(report: TelemetryHeartbeatResponse | undefined, now: number): string {
  const age = report?.last_collection_success_age_ms;
  if (age == null) return '—';
  if (!Number.isSafeInteger(age) || age < 0) return '표시 범위 초과';
  const received = Date.parse(report?.received_at ?? '');
  const sinceReceived = Number.isFinite(received) ? Math.max(0, now - received) : 0;
  const text = ago(age + sinceReceived);
  return report?.status === 'STALE' ? `${text} (마지막 heartbeat 기준)` : text;
}

/** 수집 상태 배지 툴팁. 값이 없으면 "—" */
export function collectorTooltip(report: TelemetryHeartbeatResponse | undefined, now: number): string {
  return [
    '텔레메트리 수집기 (v2 heartbeat)',
    `마지막 수집 성공: ${lastCollectionSuccess(report, now)}`,
    `수집 실패 누계: ${heartbeatCount(report?.collection_failures_total)}`,
    `전송 실패 누계: ${heartbeatCount(report?.publish_submit_failures_total)}`,
  ].join('\n');
}
