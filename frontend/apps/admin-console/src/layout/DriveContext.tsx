import { createContext, useContext, type ReactNode } from 'react';
import { useDriveState, type DriveState, type SocketState } from '../api/drive';

/** 주행 통합 상태 구독 하나를 모드 띠와 화면이 같이 쓴다 (WS 연결을 화면마다 따로 열지 않게) */
const DriveContext = createContext<{ drive: DriveState | null; socket: SocketState }>({
  drive: null,
  socket: 'connecting',
});

export function DriveProvider({ children }: { children: ReactNode }) {
  const value = useDriveState();
  return <DriveContext.Provider value={value}>{children}</DriveContext.Provider>;
}

export const useDrive = () => useContext(DriveContext);
