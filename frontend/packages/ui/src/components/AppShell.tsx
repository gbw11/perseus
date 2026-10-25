import type { ReactNode } from 'react';
import styles from './AppShell.module.css';

export interface AppShellProps {
  brand: ReactNode;
  nav?: ReactNode;
  actions?: ReactNode;
  /** 상단 바 아래 고정 띠 (관리 콘솔: 주행 모드 띠, 화면 정의서 §2) */
  strip?: ReactNode;
  children: ReactNode;
}

/** 상단 바 + 고정 띠 + 본문. 관리 콘솔의 기본 틀. */
export function AppShell({ brand, nav, actions, strip, children }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <header className={styles.top}>
        <div className={styles.brand}>{brand}</div>
        {nav && <nav className={styles.nav}>{nav}</nav>}
        <div className={styles.actions}>{actions}</div>
      </header>
      {strip && <div className={styles.strip}>{strip}</div>}
      <main className={styles.main}>{children}</main>
    </div>
  );
}
