import { beforeEach, describe, expect, it } from 'vitest';
import {
  blockVmCreationUnload,
  estimatedRemainingTime,
  finishVmCreation,
  formatVmCreationTime,
  isVmCreationPending,
  readLastVmCreationDuration,
  saveLastVmCreationDuration,
  startVmCreation,
  VM_CREATION_LEAVE_MESSAGE,
} from './vmCreationProgress';

describe('VM 생성 진행 상태', () => {
  beforeEach(() => {
    localStorage.clear();
    finishVmCreation('create-test');
  });

  it('경과 시간과 직전 생성 기준 남은 시간을 계산한다', () => {
    expect(formatVmCreationTime(65_900)).toBe('01:05');
    expect(estimatedRemainingTime(30_000, 90_000)).toBe(60_000);
    expect(estimatedRemainingTime(100_000, 90_000)).toBe(0);
  });

  it('직전 생성 소요 시간을 저장하고 읽는다', () => {
    expect(readLastVmCreationDuration()).toBeNull();
    saveLastVmCreationDuration(75_250);
    expect(readLastVmCreationDuration()).toBe(75_250);
  });

  it('새로고침 이탈 안내에 실제 동작을 설명한다', () => {
    const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;
    expect(blockVmCreationUnload(event)).toBe(VM_CREATION_LEAVE_MESSAGE);
    expect(event.defaultPrevented).toBe(true);
  });

  it('생성 시작과 종료를 브라우저 저장소에 기록한다', () => {
    startVmCreation('create-test', 1_000);
    expect(localStorage.getItem('xenova.vm-create.pending')).toContain('create-test');
    finishVmCreation('create-test');
    expect(localStorage.getItem('xenova.vm-create.pending')).toBe('[]');
  });

  it('목록에서 기록만 된 VM은 생성 중, 실행된 VM은 완료로 구분한다', () => {
    const creations = [{ name: 'create-test', startedAt: 1_000 }];
    expect(isVmCreationPending('create-test', 'stopped', creations)).toBe(true);
    expect(isVmCreationPending('create-test', 'running', creations)).toBe(false);
    expect(isVmCreationPending('another-vm', 'stopped', creations)).toBe(false);
  });
});
