/**
 * mock 전용: 보드(dom0)와 연결이 끊긴 상황. 켜면 보드에 묻는 API(VM 목록·상세·자원)가
 * 실제 백엔드처럼 502 VM_CONTROL_BAD_RESPONSE 로 답한다 (보드 전원을 껐을 때 EC2 에서 본 응답).
 * 끊김 알림·복구 알림을 보드 없이 확인하는 용도. msw 에 의존하지 않아 화면 코드가 가져다 써도 된다.
 */
let down = false;
const listeners = new Set<() => void>();

export function mockBoardDown(): boolean {
  return down;
}

export function setMockBoardDown(value: boolean): void {
  down = value;
  listeners.forEach((notify) => notify());
}

export function subscribeMockBoard(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
