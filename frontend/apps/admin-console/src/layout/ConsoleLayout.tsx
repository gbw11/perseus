import { AppShell, Button } from '@xenova/ui';
import logo from '@xenova/ui/assets/xenova-logo.png';
import { Link, NavLink, Outlet, useNavigate } from 'react-router';
import { useLogout } from '../api/queries';
import { useSession } from '../auth';
import { DriveProvider } from './DriveContext';
import { LiveDataAlertHost } from './LiveDataAlert';
import { MockBoardSwitch } from './MockBoardSwitch';
import { ResumeNotice } from './ResumeNotice';
import styles from './layout.module.css';

/** 관리자 웹 공통 틀: 핵심 업무 4개 영역과 계정 동작. */
export function ConsoleLayout() {
  const session = useSession();
  const logout = useLogout();
  const navigate = useNavigate();
  const name = session?.user.nickname || session?.user.username || '';
  return (
    <DriveProvider>
      <AppShell
        brand={
          <Link
            to="/dashboard/overview"
            className={styles.brandLink}
            aria-label="VM Dashboard Overview로 이동"
          >
            <img src={logo} alt="Xenova" className={styles.logo} />
            <small className={styles.brandText}>Admin Console</small>
          </Link>
        }
        nav={
          <>
            <NavLink to="/dashboard">VM Dashboard</NavLink>
            <NavLink to="/vision">Vision</NavLink>
            <NavLink to="/control">VM Control</NavLink>
            <NavLink to="/simulator">Simulator</NavLink>
          </>
        }
        actions={
          <>
            <span className={styles.user}>
              <span className={styles.avatar} aria-hidden="true">
                {name.slice(0, 1).toUpperCase()}
              </span>
              {name}
            </span>
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
        <ResumeNotice />
        <LiveDataAlertHost />
        <Outlet />
        <MockBoardSwitch />
      </AppShell>
    </DriveProvider>
  );
}
