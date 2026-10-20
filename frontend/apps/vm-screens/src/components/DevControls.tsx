import { Button } from '@xenova/ui';
import { Link } from 'react-router';
import { label } from '../telemetry/scenario';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APP_IDS } from '../telemetry/types';
import styles from './screens.module.css';

/** 개발·데모 조작 바. 키오스크(?kiosk=1)에서는 숨긴다. 실제 장애 주입은 관리 콘솔이 맡는다. */
export function DevControls() {
  const { telemetry: t, crash, toggleStress, resume } = useTelemetry();
  if (new URLSearchParams(window.location.search).has('kiosk')) return null;
  return (
    <footer className={styles.dev}>
      <span className={styles.devTitle}>mock 조작</span>
      {APP_IDS.map((id) => (
        <span key={id} className={styles.devGroup}>
          {label(id)}
          <Button
            size="sm"
            variant="danger"
            onClick={() => crash(id)}
            disabled={t.apps[id].domain !== 'running'}
          >
            강제 종료
          </Button>
          <Button size="sm" variant="secondary" onClick={() => toggleStress(id)}>
            부하 {t.apps[id].cpuStress ? '해제' : ''}
          </Button>
        </span>
      ))}
      <Button size="sm" onClick={resume} disabled={t.mode !== 'AUTO_READY'}>
        자율주행 재개
      </Button>
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
