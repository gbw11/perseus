import { AppShell, Button } from '@xenova/ui';
import logo from '@xenova/ui/assets/xenova-logo.png';
import { NavLink, Outlet, useNavigate } from 'react-router';
import { useLogout } from '../api/queries';
import { useSession } from '../auth';
import { DriveProvider } from './DriveContext';
import { ModeStrip } from './ModeStrip';
import { ResumeNotice } from './ResumeNotice';
import styles from './layout.module.css';

/** 웹 공통 틀 (화면 정의서 §2): 위쪽 메뉴 [대시보드 · VM · 자원 · 시연 제어 · 웹 콘솔 · 이벤트] + 주행 모드 띠 */
export function ConsoleLayout() {
  const session = useSession();
  const logout = useLogout();
  const navigate = useNavigate();
  const name = session?.user.username ?? '';
  return (
    <DriveProvider>
      <AppShell
        brand={
          <>
            <img src={logo} alt="Xenova" className={styles.logo} />
            <small className={styles.brandText}>관리 콘솔</small>
          </>
        }
        nav={
          <>
            <NavLink to="/" end>
              대시보드
            </NavLink>
            <NavLink to="/vms">VM</NavLink>
            <NavLink to="/resources">자원</NavLink>
            <NavLink to="/demo">시연 제어</NavLink>
            <NavLink to="/console">웹 콘솔</NavLink>
            <NavLink to="/events">이벤트</NavLink>
          </>
        }
        actions={
          <>
            <span className={styles.devLinks}>
              <NavLink to="/screens">모니터 화면</NavLink>
              <NavLink to="/components">컴포넌트</NavLink>
            </span>
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
        strip={<ModeStrip />}
      >
        <ResumeNotice />
        <Outlet />
      </AppShell>
    </DriveProvider>
  );
}
