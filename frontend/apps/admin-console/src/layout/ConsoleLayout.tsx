import { AppShell, Button } from '@xenova/ui';
import { NavLink, Outlet, useNavigate } from 'react-router';
import { useLogout } from '../api/queries';
import { useSession } from '../auth';

export function ConsoleLayout() {
  const session = useSession();
  const logout = useLogout();
  const navigate = useNavigate();
  return (
    <AppShell
      brand="Xenova"
      nav={
        <>
          <NavLink to="/vms">VM</NavLink>
          <NavLink to="/demo">시연 제어</NavLink>
          <NavLink to="/screens">모니터 화면</NavLink>
          <NavLink to="/components">컴포넌트</NavLink>
        </>
      }
      actions={
        <>
          <span style={{ color: 'var(--x-text-muted)', alignSelf: 'center' }}>{session?.user.username}</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/login') })}
          >
            로그아웃
          </Button>
        </>
      }
    >
      <Outlet />
    </AppShell>
  );
}
