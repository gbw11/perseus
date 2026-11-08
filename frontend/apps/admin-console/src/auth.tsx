import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router';
import { getSession, onSessionChange, refreshSession } from './api/client';

export function useSession() {
  return useSyncExternalStore(onSessionChange, getSession, getSession);
}

/**
 * 보호된 화면. Access Token 은 메모리에만 있으므로 새로고침 직후엔 refresh 쿠키로 한 번 복원을 시도하고,
 * 실패하면 로그인 화면으로 보낸다 (노션 API 명세 "토큰 재발급": INVALID_REFRESH_TOKEN → 로그인).
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession();
  const location = useLocation();
  const [restoring, setRestoring] = useState(session === null);

  useEffect(() => {
    if (session !== null) return;
    let alive = true;
    refreshSession().finally(() => alive && setRestoring(false));
    return () => {
      alive = false;
    };
    // 첫 진입 때만 복원한다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (session) return children;
  if (restoring) return <p style={{ padding: 24 }}>세션 확인 중…</p>;
  return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
}
