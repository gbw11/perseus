import { useNavigate } from 'react-router';
import { AppPanel } from '../components/AppPanel';
import { ModeBar } from '../components/ModeBar';
import { SimPanel } from '../components/SimPanel';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APP_IDS, simLive } from '../telemetry/types';
import styles from '../components/screens.module.css';

/**
 * 모니터 1: VM 3개 3분할 (RC카 시나리오 §5). 시뮬레이터 구성이면 세 번째 칸(네비게이션)이 시뮬레이터로 바뀐다 —
 * 시뮬레이터 위치가 1초 안에 왔을 때만, 배치 조작 없이 (화면 배치 계약 v1.1 §5.6)
 */
export function SplitPage() {
  const navigate = useNavigate();
  const { telemetry } = useTelemetry();
  const sim = simLive(telemetry);
  return (
    <div className={styles.screen}>
      <ModeBar />
      <div className={styles.split}>
        {APP_IDS.map((id) =>
          id === 'interaction' && sim ? (
            <SimPanel key="simulator" onSelect={() => navigate('/sim')} />
          ) : (
            <AppPanel key={id} id={id} onSelect={(app) => navigate(`/detail/${app}`)} />
          ),
        )}
      </div>
    </div>
  );
}
