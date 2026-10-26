import { describe, expect, it } from 'vitest';
import { categoryOf, describe as describeEvent } from './events';
import type { EventType, VmEvent } from '../api/types';

function event(type: EventType): VmEvent {
  return {
    event_id: 'event',
    type,
    severity: 'INFO',
    vm_id: 'vm',
    operation_id: null,
    occurred_at: '2026-10-26T00:00:00Z',
    recorded_at: '2026-10-26T00:00:00Z',
    payload: {},
  };
}

describe('recovery outcome labels', () => {
  it('keeps completion separate from app readiness and vehicle control', () => {
    expect(describeEvent(event('recovery.completed'), () => 'VM1').detail).toContain('별도 확인');
    expect(describeEvent(event('recovery.completed'), () => 'VM1').detail).not.toBe('앱 명령 재개');
  });
  it.each(['recovery.recovered', 'recovery.cancelled'] as const)(
    'does not classify %s as failure',
    (type) => {
      expect(categoryOf(event(type))).toBe('lifecycle');
      expect(describeEvent(event(type), () => 'VM1').title).not.toContain('실패');
    },
  );
});
