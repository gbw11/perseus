import mark from '@xenova/ui/assets/xenova-mark.png';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APPS } from '../telemetry/types';
import { MODE_LABEL, mps, rad } from './format';
import styles from './ModeBar.module.css';

const clock = (ms: number) => new Date(ms).toTimeString().slice(0, 8);

/**
 * 두 모니터 위쪽 공통 띠 (화면 정의서 M-01): 주행 모드·세대, 활성 앱, 안전 플래그, 차량 속도·조향·장애물.
 * 배터리 전압은 계약에서 선택 필드라(ESP32 에 측정 회로가 있을 때만) 보이지 않는다.
 * 값은 MQTT 계약 v1.1 §5.2·§5.4 (vehicle.status 는 게이트웨이가 낸다).
 */
export function ModeBar() {
  const { telemetry: t } = useTelemetry();
  const flags = t.vehicle.flags;
  const flag = (on: boolean, text: string) => (
    <span className={[styles.flag, on ? styles.flagOn : ''].join(' ')}>{text}</span>
  );
  return (
    <header className={[styles.bar, styles[t.mode]].join(' ')}>
      <img src={mark} alt="Xenova" className={styles.mark} />
      <div className={styles.mode}>
        <span className={styles.modeLabel}>{MODE_LABEL[t.mode]}</span>
        <span className={styles.modeCode}>
          {t.mode} · gen {t.generation}
        </span>
      </div>
      <div className={styles.center}>
        <span>
          활성:{' '}
          {t.activeApp ? (
            <b style={{ color: `var(--x-app-${t.activeApp})` }}>{APPS[t.activeApp].title}</b>
          ) : (
            <b style={{ color: 'var(--x-fault)' }}>없음</b>
          )}
        </span>
        {flag(flags.estop, flags.estop ? '비상정지 버튼 눌림' : '비상정지 버튼 안 눌림')}
        {flag(flags.watchdog, flags.watchdog ? '명령 끊김 정지 작동' : '명령 끊김 정지 아님')}
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
          <dt>장애물</dt>
          <dd style={flags.obstacle ? { color: 'var(--x-fault)' } : undefined}>
            {flags.obstacle ? '감지' : '없음'}
          </dd>
        </div>
        <div>
          <dt>시각</dt>
          <dd className={styles.clock}>{clock(t.now)}</dd>
        </div>
      </dl>
    </header>
  );
}
