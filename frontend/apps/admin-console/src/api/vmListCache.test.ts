import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VmListResponse } from './types';
import { VM_LIST_CACHE_MAX_AGE_MS, clearVmList, loadVmList, saveVmList } from './vmListCache';

const LIST: VmListResponse = {
  items: [
    {
      vm_id: 'vm-1',
      name: 'debian-dev',
      guest_kind: 'linux',
      state: 'running',
      template_id: 'debian',
      template_version: '13',
      vcpus: 1,
      memory_mib: 512,
    } as VmListResponse['items'][number],
  ],
  observed_at: '2026-11-02T08:00:00Z',
};

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe('vmListCache', () => {
  it('저장한 목록과 저장 시각을 돌려준다', () => {
    saveVmList(LIST, 1_000);

    expect(loadVmList(2_000)).toEqual({ savedAt: 1_000, data: LIST });
  });

  it('10분이 지난 목록은 버린다', () => {
    saveVmList(LIST, 1_000);

    expect(loadVmList(1_000 + VM_LIST_CACHE_MAX_AGE_MS + 1)).toBeNull();
    expect(loadVmList(1_000)).toBeNull();
  });

  it('로그아웃하면 지운다', () => {
    saveVmList(LIST, 1_000);
    clearVmList();

    expect(loadVmList(1_000)).toBeNull();
  });

  it('깨진 값은 없는 것으로 본다', () => {
    sessionStorage.setItem('xenova.vm-list.v1', '{not json');
    expect(loadVmList()).toBeNull();
    sessionStorage.setItem('xenova.vm-list.v1', JSON.stringify({ savedAt: 1, data: { items: 'x' } }));
    expect(loadVmList(2)).toBeNull();
  });

  it('저장소를 쓸 수 없어도 예외를 던지지 않는다', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    expect(() => saveVmList(LIST)).not.toThrow();
    expect(loadVmList()).toBeNull();
  });
});
