import mark from '@xenova/ui/assets/xenova-mark.png';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APPS } from '../telemetry/types';
import { MODE_LABEL, rad, speedText } from './format';
import styles from './ModeBar.module.css';

const clock = (ms: number) => new Date(ms).toTimeString().slice(0, 8);

/**
 * 두 모니터 위쪽 공통 띠 (화면 정의서 M-01): 주행 모드·세대, 활성 앱, 차량 속도·조향·장애물.
 * 안전 플래그 셋(비상정지·명령 끊김·근거리 장애물)은 띠에서 뺐다 (2026-10-22 요청) — 장애물은 오른쪽 값에 남는다.
 * 배터리 전압은 계약에서 선택 필드라(ESP32 에 측정 회로가 있을 때만) 보이지 않는다.
 * 값은 MQTT 계약 v1.1 §5.2·§5.4 (vehicle.status 는 게이트웨이가 낸다).
 */
export function ModeBar() {
  const { telemetry: t } = useTelemetry();
  const flags = t.vehicle.flags;
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
      </div>
      <dl className={styles.stats}>
        <div>
          <dt>속도</dt>
          <dd>{speedText(t, t.vehicle.v_mps)}</dd>
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
