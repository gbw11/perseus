import { StatusBadge } from '@xenova/ui';
import { MODE_HEALTH, ROLES } from '../domain/roles';
import { useDrive } from './DriveContext';
import styles from './layout.module.css';

const MOCK = import.meta.env.VITE_MOCK === 'true';
const SOCKET_LABEL = { connecting: '연결 중', open: '실시간', closed: '끊김' } as const;

/**
 * 주행 모드 띠 — 모든 웹 화면 위에 고정 (화면 정의서 §2).
 * "모드 · gen | 활성 | 예비 | 안전" 순서. 값은 WS /ws/v1/drive/state.
 */
export function ModeStrip() {
  const { drive, socket } = useDrive();
  const control = drive?.control;
  const flags = drive?.vehicle?.flags;
  const raised = flags
    ? [
        flags.estop && '비상정지 버튼',
        flags.watchdog && '명령 끊김 정지',
        flags.obstacle && '근거리 장애물',
      ].filter(Boolean)
    : [];
  const manual = drive?.apps.manual;
  const active = control?.active_app ? ROLES[control.active_app] : null;

  return (
    <>
      {control?.mode ? (
        <StatusBadge
          health={MODE_HEALTH[control.mode]}
          label={`${control.mode} · gen ${control.generation}`}
          size="sm"
        />
      ) : (
        <StatusBadge health="stopped" label="게이트웨이 신호 없음" size="sm" />
      )}
      <span className={styles.sep}>|</span>
      <span>
        활성:{' '}
        {active ? (
          <b style={{ color: active.color }}>{active.title}</b>
        ) : (
          <b className={styles.fault}>없음</b>
        )}
      </span>
      <span className={styles.sep}>|</span>
      <span>
        예비:{' '}
        {control?.active_app === 'manual' ? (
          <b className={styles.dim}>— (수동주행이 제어 중)</b>
        ) : manual?.command_fresh ? (
          <b style={{ color: ROLES.manual.color }}>준비됨</b>
        ) : (
          <b className={styles.warn}>명령 없음</b>
        )}
      </span>
      <span className={styles.sep}>|</span>
      <span>
        안전:{' '}
        {raised.length ? (
          <b className={styles.fault}>{raised.join(' · ')}</b>
        ) : (
          <b className={styles.ok}>플래그 없음</b>
        )}
      </span>
      <span className={styles.spacer} />
      <span className={styles.dim}>{MOCK ? 'mock 시나리오' : `주행 상태: ${SOCKET_LABEL[socket]}`}</span>
    </>
  );
}
