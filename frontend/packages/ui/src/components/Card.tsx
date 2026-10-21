import type { HTMLAttributes, ReactNode } from 'react';
import styles from './Card.module.css';

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode;
  actions?: ReactNode;
  /** 왼쪽 테두리 강조색 (예: 앱 역할색 var(--x-app-autonomous)) */
  accent?: string;
}

export function Card({ title, actions, accent, className, children, style, ...rest }: CardProps) {
  return (
    <section
      className={[styles.card, className].filter(Boolean).join(' ')}
      style={accent ? { ...style, borderLeftColor: accent, borderLeftWidth: 4 } : style}
      {...rest}
    >
      {(title || actions) && (
        <header className={styles.header}>
          <h3 className={styles.title}>{title}</h3>
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      <div className={styles.body}>{children}</div>
    </section>
  );
}
