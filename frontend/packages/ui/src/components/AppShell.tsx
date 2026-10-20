import type { ReactNode } from 'react';
import styles from './AppShell.module.css';

export interface AppShellProps {
  brand: ReactNode;
  nav?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

/** 상단 바 + 본문 레이아웃. 관리 콘솔의 기본 틀. */
export function AppShell({ brand, nav, actions, children }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <header className={styles.top}>
        <div className={styles.brand}>{brand}</div>
        {nav && <nav className={styles.nav}>{nav}</nav>}
        <div className={styles.actions}>{actions}</div>
      </header>
      <main className={styles.main}>{children}</main>
    </div>
  );
}
