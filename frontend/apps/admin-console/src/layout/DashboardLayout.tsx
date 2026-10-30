import { NavLink, Outlet } from 'react-router';
import styles from './layout.module.css';

const DASHBOARD_TABS = [
  ['/dashboard/overview', 'Overview'],
  ['/dashboard/cpu', 'CPU'],
  ['/dashboard/memory', 'Memory'],
  ['/dashboard/console', 'Console'],
] as const;

/** VM 관측 화면의 공통 제목과 2차 탐색. 각 탭은 후속 이슈에서 독립적으로 확장한다. */
export function DashboardLayout() {
  return (
    <section className={styles.dashboardSection} aria-labelledby="dashboard-title">
      <header className={styles.dashboardHead}>
        <div>
          <span className={styles.sectionEyebrow}>OBSERVE · VM MONITORING</span>
          <h1 id="dashboard-title">VM Dashboard</h1>
        </div>
      </header>
      <nav className={styles.dashboardTabs} aria-label="VM Dashboard 메뉴">
        {DASHBOARD_TABS.map(([to, label]) => (
          <NavLink key={to} to={to}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.dashboardBody}>
        <Outlet />
      </div>
    </section>
  );
}
