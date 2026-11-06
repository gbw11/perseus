import { StatusBadge } from '@xenova/ui';
import { ROLE, type Presentation } from './model';
import styles from './notices.module.css';

export function Notices({ p, connected }: { p: Presentation | null; connected: boolean }) {
  const notices =
    connected && p
      ? p.state.vms.flatMap((v) => {
          const d = p.decisions[v.vm_id];
          return d &&
            (d.tone === 'danger' ||
              ['operation', 'stale', 'stopped', 'stopping', 'paused', 'waiting', 'os_mismatch'].includes(
                d.kind,
              ))
            ? [{ id: v.vm_id, text: `${ROLE[v.role].name} · ${d.label}`, tone: d.tone, kind: d.kind }]
            : [];
        })
      : [];
  return (
    <section className={styles.notices} aria-label="오류·복구 알림">
      <span>알림</span>
      {notices.length ? (
        notices.map((n) => (
          <StatusBadge
            key={n.id}
            health={n.tone === 'danger' ? 'fault' : n.kind === 'stopped' ? 'stopped' : 'recovering'}
            label={n.text}
          />
        ))
      ) : (
        <p>{connected ? '오류·복구 이벤트 수신 대기' : '알림 연결 미확인'}</p>
      )}
    </section>
  );
}
