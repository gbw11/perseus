import { Card } from '@xenova/ui';
import styles from './pages.module.css';

/** 개발 서버가 /monitor/ 를 모니터 화면으로 넘긴다 (vite.config.ts). 같은 origin 이라 콘솔 mock 시나리오를 따라간다 */
const SCREENS_BASE = import.meta.env.VITE_SCREENS_ORIGIN || '/monitor';

/**
 * 모니터 화면 바로가기. 모니터 2 에 띄울 VM 을 고르는 화면 라우터 제어는
 * S15P31E110-129(API)·-130(UI) 에서 이 자리를 채운다.
 */
export function ScreensPage() {
  const links = [
    ['모니터 1 — 3분할', '/split'],
    ['모니터 2 — 자율주행 상세', '/detail/autonomous'],
    ['모니터 2 — 수동주행 상세', '/detail/manual'],
    ['모니터 2 — 네비게이션 상세', '/detail/interaction'],
    ['모니터 2 — 시뮬레이터 (시뮬레이터 구성)', '/sim'],
  ] as const;
  return (
    <Card title="모니터 화면 (RC카 시나리오 §5)">
      <p className={styles.dim}>
        라즈베리파이 HDMI 모니터에 뜨는 화면이다. 조작은 없다 — 시연 제어에서 강제 종료·재개를 하면 모니터가
        따라간다. 실제 모니터에는 ?kiosk=1 로 띄운다. 모니터 2 선택은 화면 라우터 제어(-129·-130)가 생기면
        여기서 한다.
      </p>
      <ul className={styles.linkList}>
        {links.map(([label, path]) => (
          <li key={path}>
            <a href={`${SCREENS_BASE}${path}`} target="_blank" rel="noreferrer">
              {label}
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}
