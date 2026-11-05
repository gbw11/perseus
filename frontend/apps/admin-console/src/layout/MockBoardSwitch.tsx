import { Button } from '@xenova/ui';
import { useSyncExternalStore } from 'react';
import { mockBoardDown, setMockBoardDown, subscribeMockBoard } from '../mocks/board';
import styles from './layout.module.css';

const MOCK = import.meta.env.VITE_MOCK === 'true';

/**
 * mock 모드에서만: 보드 연결을 끊었다 다시 잇는 시연 스위치 (화면 오른쪽 아래).
 * 실제 보드 없이 실시간 조회 실패 알림과 복구 알림을 확인한다.
 */
export function MockBoardSwitch() {
  const down = useSyncExternalStore(subscribeMockBoard, mockBoardDown);
  if (!MOCK) return null;
  return (
    <div className={styles.mockBoardSwitch}>
      <span>mock 보드: {down ? '끊김' : '연결됨'}</span>
      <Button variant="secondary" size="sm" onClick={() => setMockBoardDown(!down)}>
        {down ? '보드 다시 연결' : '보드 연결 끊기'}
      </Button>
    </div>
  );
}
