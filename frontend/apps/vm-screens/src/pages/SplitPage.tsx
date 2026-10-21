import { useNavigate } from 'react-router';
import { AppPanel } from '../components/AppPanel';
import { ModeBar } from '../components/ModeBar';
import { APP_IDS } from '../telemetry/types';
import styles from '../components/screens.module.css';

/** 모니터 1: VM 3개 3분할 (RC카 시나리오 §5) */
export function SplitPage() {
  const navigate = useNavigate();
  return (
    <div className={styles.screen}>
      <ModeBar />
      <div className={styles.split}>
        {APP_IDS.map((id) => (
          <AppPanel key={id} id={id} onSelect={(app) => navigate(`/detail/${app}`)} />
        ))}
      </div>
    </div>
  );
}
