import { Link } from 'react-router';
import { label } from '../telemetry/scenario';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APP_IDS } from '../telemetry/types';
import styles from './screens.module.css';

const SOURCE_LABEL = { mock: '가짜 시나리오', console: '관리 콘솔 mock', live: '백엔드' } as const;
const CONNECTION_LABEL = {
  mock: '혼자 도는 중',
  connecting: '연결 중',
  live: '연결됨',
  offline: '신호 없음',
  unauthorized: '인증 필요 — 관리 콘솔 로그인 또는 .env.local 계정',
} as const;

/**
 * 개발용 아래 띠: 데이터 출처·연결 상태와 화면 이동 링크만. 키오스크(?kiosk=1)에서는 숨긴다.
 * 조작 버튼은 없다 — 모니터는 보여 주기만 하고, 장애 주입·재개는 관리 콘솔 시연 제어에서 한다 (화면 정의서 §1).
 */
export function DevControls() {
  const { source, connection } = useTelemetry();
  if (new URLSearchParams(window.location.search).has('kiosk')) return null;
  return (
    <footer className={styles.dev}>
      <span className={styles.devTitle}>
        {SOURCE_LABEL[source]}: {CONNECTION_LABEL[connection]}
      </span>
      {source === 'console' && connection === 'offline' && (
        <span className={styles.dim}>관리 콘솔(같은 주소의 /demo)을 다른 탭에 열어 두면 따라간다</span>
      )}
      <nav className={styles.devNav}>
        <Link to="/split">3분할</Link>
        {APP_IDS.map((id) => (
          <Link key={id} to={`/detail/${id}`}>
            {label(id)} 상세
          </Link>
        ))}
      </nav>
    </footer>
  );
}
