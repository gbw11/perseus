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
    ['모니터 1 — VM 전체 상태', '/display/monitor-1'],
    ['모니터 2 — 정책이 선택한 VM 상세', '/display/monitor-2'],
  ] as const;
  return (
    <Card title="모니터 화면 (RC카 시나리오 §5)">
      <p className={styles.dim}>
        dom0 표시 수집기와 로컬 정책이 정한 전체·상세 화면입니다. 상세 대상은 UUID로 선택하며 주행 모드와
        주·백업 역할을 구분합니다. 이 링크는 화면 열기이며 HDMI 적용 성공을 뜻하지 않습니다. 로컬 표시 세션이
        필요하고 외부 서버에서 볼 때는 별도 연결이 필요합니다.
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
