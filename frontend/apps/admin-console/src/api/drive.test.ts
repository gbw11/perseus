import { describe, expect, it } from 'vitest';
import { KEY_OF, pressKey, type DriveKey } from './drive';

describe('pressKey', () => {
  it('누르고 떼면 집합이 바뀌고, 계약 §7 순서로 정렬한다', () => {
    let held: DriveKey[] = [];
    held = pressKey(held, 'left', true);
    held = pressKey(held, 'up', true);
    expect(held).toEqual(['up', 'left']);
    held = pressKey(held, 'left', false);
    expect(held).toEqual(['up']);
  });

  it('키 반복(keydown 연타)이나 이미 뗀 키는 같은 배열을 돌려준다 — 다시 보내지 않는다', () => {
    const held: DriveKey[] = ['up'];
    expect(pressKey(held, 'up', true)).toBe(held);
    expect(pressKey(held, 'down', false)).toBe(held);
  });

  it('화살표·WASD·스페이스를 계약 키로 바꾼다', () => {
    expect(KEY_OF.ArrowUp).toBe('up');
    expect(KEY_OF.a).toBe('left');
    expect(KEY_OF[' ']).toBe('brake');
    expect(KEY_OF.Enter).toBeUndefined();
  });
});
