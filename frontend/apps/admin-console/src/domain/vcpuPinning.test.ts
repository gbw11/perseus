import { describe, expect, it } from 'vitest';
import { normalizePinning, pinningEqual, togglePcpu } from './vcpuPinning';

describe('vCPU pinning draft', () => {
  it('서버 순서와 무관하게 같은 affinity를 같은 값으로 본다', () => {
    expect(
      pinningEqual(
        [
          { vcpu: 1, pcpus: [3, 0] },
          { vcpu: 0, pcpus: [2] },
        ],
        [
          { vcpu: 0, pcpus: [2] },
          { vcpu: 1, pcpus: [0, 3] },
        ],
      ),
    ).toBe(true);
  });

  it('셀 토글 시 중복 없이 정렬하고 마지막 pCPU는 해제하지 않는다', () => {
    const current = [{ vcpu: 0, pcpus: [1] }];
    expect(togglePcpu(current, 0, 1)).toEqual(current);
    expect(togglePcpu(togglePcpu(current, 0, 3), 0, 1)).toEqual([{ vcpu: 0, pcpus: [3] }]);
    expect(normalizePinning([{ vcpu: 0, pcpus: [3, 0, 3] }])).toEqual([{ vcpu: 0, pcpus: [0, 3] }]);
  });
});
