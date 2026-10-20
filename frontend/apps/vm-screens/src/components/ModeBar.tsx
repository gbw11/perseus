import { useTelemetry } from '../telemetry/TelemetryProvider';
import { MODE_LABEL, mps, rad } from './format';
import styles from './ModeBar.module.css';

/** 두 모니터 위쪽 공통 띠: 주행 모드·세대·차량 상태 (MQTT 계약 §5.2, §5.4) */
export function ModeBar() {
  const { telemetry: t } = useTelemetry();
  return (
    <header className={[styles.bar, styles[t.mode]].join(' ')}>
      <div className={styles.mode}>
        <span className={styles.modeLabel}>{MODE_LABEL[t.mode]}</span>
        <span className={styles.modeCode}>
          {t.mode} · gen {t.generation}
        </span>
      </div>
      <dl className={styles.stats}>
        <div>
          <dt>속도</dt>
          <dd>{mps(t.vehicle.v_mps)}</dd>
        </div>
        <div>
          <dt>조향</dt>
          <dd>{rad(t.vehicle.steer_rad)}</dd>
        </div>
        <div>
          <dt>배터리</dt>
          <dd>{t.vehicle.batt_v.toFixed(2)} V</dd>
        </div>
        <div>
          <dt>장애물</dt>
          <dd>{t.vehicle.flags.obstacle ? '감지' : '없음'}</dd>
        </div>
      </dl>
    </header>
  );
}
