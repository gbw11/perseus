import { Card, StatusBadge, type Health } from '@xenova/ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  KEY_OF,
  pressKey,
  useDriveState,
  useManualInput,
  type DriveApp,
  type DriveKey,
  type DriveState,
  type InputResult,
} from '../api/drive';
import styles from './pages.module.css';

/**
 * W-07 시연 제어 — 주행 상태와 수동주행 입력 (화면 정의서 W-07, 공병우 -128·-130).
 * 격리 시연(-135)·화면 배치(-129) 칸은 담당 작업에서 채운다.
 */
export function DemoPage() {
  const { drive, socket } = useDriveState();
  return (
    <div className={styles.grid}>
      <Card title="주행 상태" actions={<span className={styles.dim}>{SOCKET_LABEL[socket]}</span>}>
        <DriveSummary drive={drive} />
      </Card>
      <Card title="수동주행 입력">
        <ManualInput mode={drive?.control?.mode ?? null} />
      </Card>
      <Card title="앱별 명령">
        <AppTable drive={drive} />
      </Card>
      <Card title="격리 시연 · 화면 배치">
        <p className={styles.dim}>장애 주입(-135)과 모니터 화면 배치(-129)는 담당 작업에서 붙는다.</p>
      </Card>
    </div>
  );
}

const SOCKET_LABEL = { connecting: '연결 중', open: '실시간', closed: '끊김' } as const;

const MODE_LABEL: Record<string, string> = {
  AUTO: '자율주행',
  MANUAL: '수동주행',
  AUTO_READY: '자율 재개 가능',
  SAFE_STOP: '안전 정지',
};

const num = (value: number | null | undefined, digits = 2, unit = '') =>
  value == null ? '—' : `${value.toFixed(digits)}${unit}`;

function DriveSummary({ drive }: { drive: DriveState | null }) {
  if (!drive) return <p className={styles.dim}>주행 상태를 기다리는 중…</p>;
  const { control, vehicle, perception } = drive;
  const flags = vehicle?.flags;
  const raised = flags
    ? [flags.estop && '비상 정지', flags.watchdog && '워치독', flags.obstacle && '장애물'].filter(Boolean)
    : [];
  return (
    <dl className={styles.kv}>
      <dt>모드</dt>
      <dd>
        {control?.mode
          ? `${MODE_LABEL[control.mode] ?? control.mode} (${control.mode})`
          : '게이트웨이 신호 없음'}
        {control?.reason && <span className={styles.dim}> · {control.reason}</span>}
      </dd>
      <dt>세대 · 활성 앱</dt>
      <dd className={styles.mono}>
        {control?.generation ?? '—'} · {control?.active_app ?? '없음'}
      </dd>
      <dt>차량</dt>
      <dd className={styles.mono}>
        {vehicle
          ? `${num(vehicle.v_mps, 2, ' m/s')} · ${num(vehicle.steer_rad, 2, ' rad')} · ${num(vehicle.batt_v, 1, ' V')}`
          : '신호 없음'}
      </dd>
      <dt>안전 플래그</dt>
      <dd>{flags ? raised.join(' · ') || '정상' : '—'}</dd>
      <dt>인지 (젯슨)</dt>
      <dd className={styles.mono}>
        {perception?.valid
          ? `${perception.hz} Hz · 차선 ${num(perception.lane_offset_m)} m · 장애물 ${perception.obstacle_count}`
          : '유효하지 않음'}
      </dd>
    </dl>
  );
}

/** 주행 앱은 명령 신선도(백엔드 100 ms 판정), 인터랙션 앱은 생존 신호(1초 주기, 3초까지)로 본다 */
function appHealth(app: DriveApp | undefined, driving: boolean): Health {
  if (!app) return 'stopped';
  if (driving) return app.command_fresh ? 'ok' : 'fault';
  return app.heartbeat_ago_ms != null && app.heartbeat_ago_ms <= 3000 ? 'ok' : 'fault';
}

const ROWS: [appId: string, title: string, driving: boolean][] = [
  ['autonomous', '자율주행', true],
  ['manual', '수동주행', true],
  ['message-board', '인터랙션', false],
];

function AppTable({ drive }: { drive: DriveState | null }) {
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>앱</th>
          <th>상태</th>
          <th>명령 주기</th>
          <th>마지막 명령</th>
          <th>생존 신호</th>
        </tr>
      </thead>
      <tbody>
        {ROWS.map(([id, title, driving]) => {
          const app = drive?.apps[id];
          const command = app?.last_command;
          return (
            <tr key={id}>
              <td>{title}</td>
              <td>
                <StatusBadge health={appHealth(app, driving)} />
              </td>
              <td className={styles.mono}>{driving && app ? `${app.command_hz} Hz` : '—'}</td>
              <td className={styles.mono}>
                {command ? `${num(command.v_mps)} m/s · ${num(command.steer_rad)} rad` : '—'}
              </td>
              <td className={styles.mono}>
                {app?.heartbeat_state ? `${app.heartbeat_state} · ${app.heartbeat_ago_ms} ms 전` : '—'}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const RESULT_LABEL: Record<InputResult, string> = {
  SENT: '보냄',
  RELEASED: '놓음',
  NOT_MANUAL: '수동주행 모드가 아니라 버려짐',
  NOT_CONNECTED: '백엔드가 브로커에 연결되지 않음',
};

/**
 * 눌린 키 집합이 바뀔 때만 보낸다. 누르고 있는 동안의 반복(20 Hz)은 백엔드가 한다 (MQTT 계약 §7).
 * MANUAL 이 아니면 입력을 받지 않는다 — 백엔드도 같은 이유로 버린다.
 */
function ManualInput({ mode }: { mode: string | null }) {
  const { socket, result, sendKeys } = useManualInput();
  const [held, setHeld] = useState<DriveKey[]>([]);
  const heldRef = useRef<DriveKey[]>([]);
  const enabled = mode === 'MANUAL' && socket === 'open';

  const press = useCallback(
    (key: DriveKey, down: boolean) => {
      const next = pressKey(heldRef.current, key, down);
      if (next === heldRef.current) return;
      heldRef.current = next;
      setHeld(next);
      sendKeys(next);
    },
    [sendKeys],
  );

  const releaseAll = useCallback(() => {
    if (heldRef.current.length === 0) return;
    heldRef.current = [];
    setHeld([]);
    sendKeys([]);
  }, [sendKeys]);

  useEffect(() => {
    if (!enabled) {
      releaseAll();
      return;
    }
    const onKey = (down: boolean) => (event: KeyboardEvent) => {
      const key = KEY_OF[event.key];
      if (!key || (event.target as HTMLElement | null)?.closest?.('input, textarea, select')) return;
      event.preventDefault(); // 화살표·스페이스로 페이지가 스크롤되지 않게
      press(key, down);
    };
    const down = onKey(true);
    const up = onKey(false);
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', releaseAll); // 창을 떠나면 keyup 을 못 받는다
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', releaseAll);
    };
  }, [enabled, press, releaseAll]);

  const pad = (key: DriveKey, text: string) => (
    <button
      type="button"
      className={[styles.key, held.includes(key) ? styles.keyOn : ''].join(' ')}
      disabled={!enabled}
      aria-pressed={held.includes(key)}
      onPointerDown={() => press(key, true)}
      onPointerUp={() => press(key, false)}
      onPointerLeave={() => press(key, false)}
    >
      {text}
    </button>
  );

  return (
    <>
      <p className={styles.dim}>
        {socket !== 'open'
          ? '입력 소켓 연결 중…'
          : mode === 'MANUAL'
            ? '화살표·WASD 로 운전, 스페이스는 브레이크. 손을 떼면 정지한다.'
            : '수동주행(MANUAL) 모드에서만 입력을 받는다. 자율주행에 문제가 생기면 게이트웨이가 넘겨준다.'}
      </p>
      <div className={styles.pad}>
        <span />
        {pad('up', '▲')}
        <span />
        {pad('left', '◀')}
        {pad('down', '▼')}
        {pad('right', '▶')}
      </div>
      <div className={styles.row}>
        {pad('brake', '브레이크')}
        <span className={styles.mono}>
          {held.length ? held.join(' + ') : '입력 없음'}
          {result && ` · ${RESULT_LABEL[result]}`}
        </span>
      </div>
    </>
  );
}
