import { Card } from '@xenova/ui';
import styles from './pages.module.css';

const SCREENS_ORIGIN = import.meta.env.VITE_SCREENS_ORIGIN || 'http://localhost:5174';

/**
 * 모니터 화면 바로가기. 모니터 2 에 띄울 VM 을 고르는 화면 라우터 제어는
 * S15P31E110-129(API)·-130(UI) 에서 이 자리를 채운다.
 */
export function ScreensPage() {
  const links = [
    ['모니터 1 — 3분할', '/split'],
    ['모니터 2 — 자율주행 상세', '/detail/autonomous'],
    ['모니터 2 — 수동주행 상세', '/detail/manual'],
    ['모니터 2 — 인터랙션 상세', '/detail/interaction'],
  ] as const;
  return (
    <Card title="모니터 화면 (RC카 시나리오 §5)">
      <p className={styles.dim}>
        지금은 mock 화면으로 연결한다. 모니터 2 선택은 화면 라우터 제어(-129·-130)가 생기면 여기서 한다.
      </p>
      <ul className={styles.linkList}>
        {links.map(([label, path]) => (
          <li key={path}>
            <a href={`${SCREENS_ORIGIN}${path}`} target="_blank" rel="noreferrer">
              {label}
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}
