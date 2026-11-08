import { describe, expect, it } from 'vitest';
import type { TelemetryHeartbeatResponse } from '../api/types';
import { publisherObservation } from '../domain/telemetryPublisher';
import { VM_IDS } from '../mocks/data';
import { mockTelemetryHeartbeat } from '../mocks/telemetryHeartbeat';
import {
  collectorBadgeLabel,
  collectorTooltip,
  heartbeatCount,
  lastCollectionSuccess,
} from './collectorStatus';

const NOW = Date.parse('2026-11-06T10:00:00Z');
const observe = (report: TelemetryHeartbeatResponse) =>
  publisherObservation(report.vm_id, { data: report, isError: false, isPending: false });

describe('수집 상태 배지 라벨', () => {
  it('관측 만료·미수신·종료 중에만 "수집기 · " 를 붙이고 나머지는 그대로 쓴다', () => {
    expect(collectorBadgeLabel('수집 중')).toBe('수집 중');
    expect(collectorBadgeLabel('수집 저하')).toBe('수집 저하');
    expect(collectorBadgeLabel('관측 만료')).toBe('수집기 · 관측 만료');
    expect(collectorBadgeLabel('미수신')).toBe('수집기 · 미수신');
    expect(collectorBadgeLabel('종료 중')).toBe('수집기 · 종료 중');
    expect(collectorBadgeLabel('조회 불가')).toBe('조회 불가');
  });

  it('mock heartbeat 로 정상·저하·만료·미수신 네 상태와 색을 모두 만든다', () => {
    const states = [VM_IDS.autonomous, VM_IDS.manual, VM_IDS.interaction, 'unknown-vm'].map((id) => {
      const { label, tone } = observe(mockTelemetryHeartbeat(id, NOW));
      return [collectorBadgeLabel(label), tone];
    });
    expect(states).toEqual([
      ['수집 중', 'ok'],
      ['수집 저하', 'recovering'],
      ['수집기 · 관측 만료', 'recovering'],
      ['수집기 · 미수신', 'stopped'],
    ]);
  });
});

describe('수집 상태 툴팁', () => {
  it('마지막 수집 성공(받은 뒤 흐른 시간 포함)·수집 실패·전송 실패 누계를 보인다', () => {
    const report = mockTelemetryHeartbeat(VM_IDS.manual, NOW);
    expect(collectorTooltip(report, NOW + 2_000)).toBe(
      [
        '텔레메트리 수집기 (v2 heartbeat)',
        '마지막 수집 성공: 10초 전',
        '수집 실패 누계: 5',
        '전송 실패 누계: 1',
      ].join('\n'),
    );
  });

  it('관측 만료면 마지막 heartbeat 기준이라고 밝히고, 값이 없으면 "—" 이다', () => {
    expect(lastCollectionSuccess(mockTelemetryHeartbeat(VM_IDS.interaction, NOW), NOW)).toBe(
      '10초 전 (마지막 heartbeat 기준)',
    );
    expect(collectorTooltip(mockTelemetryHeartbeat('unknown-vm', NOW), NOW)).toContain('마지막 수집 성공: —');
    expect(collectorTooltip(undefined, NOW)).toContain('전송 실패 누계: —');
  });

  it('BigInteger 누계가 2^53 을 넘으면 "표시 범위 초과" 로 쓴다', () => {
    expect(heartbeatCount(1234)).toBe('1,234');
    expect(heartbeatCount(2 ** 53)).toBe('표시 범위 초과');
    expect(heartbeatCount(null)).toBe('—');
    expect(heartbeatCount(undefined)).toBe('—');
  });
});
