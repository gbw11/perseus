import { Button, Card, Modal, StatusBadge, useToast } from '@xenova/ui';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { KEY_OF, mockDemo, pressKey, useManualInput, type DriveKey, type InputResult } from '../api/drive';
import { useVms } from '../api/queries';
import { appHealth, plain, ROLE_ORDER, ROLES, signed, vmOf, type AppId } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

/**
 * W-07 시연 제어 (화면 정의서 W-07, UI-08·DS-01) — 공병우 -128·-130, 격리 시연 API 는 정송하 -135.
 * 세 영역: 화면 배치 / 격리 시연 / 수동주행 입력 + 자율주행 재개. 참고 시안: stitch_untitled_project_init (루트).
 * 화면 배치(-129)·격리 시연(-135)·재개 API 는 아직 없다 → mock 에서만 동작하고 live 에서는 비활성.
 */
export function DemoPage() {
  return (
    <div className={styles.page}>
      <div className={styles.cols3}>
        <ScreenRouting />
        <Isolation />
        <Card title="수동주행 입력">
          <ManualInput />
          <Resume />
        </Card>
      </div>
    </div>
  );
}

// ---- 화면 배치 (DS-01, REST-24 -129 미구현) ----

const MONITOR2: { app: AppId; label: string }[] = [
  { app: 'autonomous', label: '자율주행 상세' },
  { app: 'manual', label: '수동주행 상세' },
  { app: 'message-board', label: '인터랙션 상세' },
];

function ScreenRouting() {
  const toast = useToast();
  const [monitor1, setMonitor1] = useState('split');
  const [monitor2, setMonitor2] = useState<AppId>('autonomous');
  const [rev, setRev] = useState(7);
  const [dirty, setDirty] = useState(false);
  return (
    <Card
      title="화면 배치"
      actions={<span className={styles.chip}>{dirty ? '미적용' : `적용됨 (rev ${rev})`}</span>}
    >
      <p className={styles.draft}>화면 배치 API(REST-24, -129) 미구현 — 미리보기</p>
      <div className={styles.form}>
        <label>
          모니터 1
          <select
            className={styles.select}
            value={monitor1}
            onChange={(e) => {
              setMonitor1(e.target.value);
              setDirty(true);
            }}
          >
            <option value="split">3분할</option>
          </select>
        </label>
        <div className={styles.cols3} style={{ gap: 'var(--x-space-1)' }}>
          {ROLE_ORDER.map((app) => (
            <div key={app} className={styles.tile} style={{ borderTop: `3px solid ${ROLES[app].color}` }}>
              <small>{ROLES[app].short}</small>
            </div>
          ))}
        </div>
        <label>
          모니터 2
          <div className={styles.chips}>
            {MONITOR2.map((m) => (
              <button
                key={m.app}
                type="button"
                className={[styles.chip, monitor2 === m.app ? styles.chipOn : ''].join(' ')}
                onClick={() => {
                  setMonitor2(m.app);
                  setDirty(true);
                }}
              >
                {m.label}
              </button>
            ))}
          </div>
        </label>
      </div>
      <Button
        style={{ marginTop: 'var(--x-space-4)', width: '100%' }}
        disabled={!dirty}
        onClick={() => {
          setRev(rev + 1);
          setDirty(false);
          toast.show('화면 배치 API 가 없어 실제 모니터에는 보내지 않았다', 'fault', 3000);
        }}
      >
        배치 적용
      </Button>
    </Card>
  );
}

// ---- 격리 시연 (UI-08, -135 미구현) ----

function Isolation() {
  const { drive } = useDrive();
  const vms = useVms();
  const [target, setTarget] = useState<AppId>('autonomous');
  const [confirm, setConfirm] = useState(false);
  const targetVm = vmOf(target, vms.data?.items, drive);
  const linux = targetVm?.guest_kind === 'linux';
  return (
    <Card title="격리 시연">
      {!mockDemo && <p className={styles.draft}>격리 시연 API(-135) 미구현 — mock 모드에서만 동작</p>}
      <div className={styles.form}>
        <label>
          대상 VM
          <select
            className={styles.select}
            value={target}
            onChange={(e) => setTarget(e.target.value as AppId)}
          >
            {ROLE_ORDER.map((app) => (
              <option key={app} value={app}>
                {ROLES[app].title} ({app})
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={styles.row} style={{ marginTop: 'var(--x-space-3)' }}>
        <Button
          variant="secondary"
          disabled
          title={linux ? '에이전트 부하 명령(-123) 미구현' : 'Linux VM 만 가능'}
        >
          CPU 부하
        </Button>
        <Button variant="danger" disabled={!mockDemo} onClick={() => setConfirm(true)}>
          강제 종료
        </Button>
        {mockDemo && (
          <Button variant="ghost" onClick={mockDemo.togglePerception}>
            {drive?.perception?.valid === false ? '인지 복구' : '인지 끊김 (D5)'}
          </Button>
        )}
      </div>
      <p className={styles.dim}>
        {linux
          ? 'CPU 부하는 에이전트 명령(stress)이라 Linux VM 만 된다.'
          : 'Zephyr VM 은 에이전트가 없어 CPU 부하를 줄 수 없다.'}
      </p>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>VM</th>
            <th>상태</th>
            <th>명령 주기</th>
          </tr>
        </thead>
        <tbody>
          {ROLE_ORDER.map((app) => {
            const role = ROLES[app];
            const state = drive?.apps[app];
            return (
              <tr key={app}>
                <td>
                  <b style={{ color: role.color }}>{target === app ? '대상' : '비대상'}</b> · {role.short}
                </td>
                <td>
                  <StatusBadge health={appHealth(role, vmOf(app, vms.data?.items, drive), state)} size="sm" />
                </td>
                <td className={styles.mono}>
                  {role.driving
                    ? state?.command_fresh
                      ? `${state.command_hz} Hz`
                      : '명령 없음'
                    : '에이전트'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <Modal
        open={confirm}
        title="강제 종료"
        onClose={() => setConfirm(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              취소
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                mockDemo?.forceKill(target);
                setConfirm(false);
              }}
            >
              강제 종료
            </Button>
          </>
        }
      >
        <p>
          <b style={{ color: ROLES[target].color }}>{ROLES[target].title}</b> 도메인을 강제 종료한다. 디스크는
          지우지 않고 복구 서비스가 다시 시작한다.
        </p>
      </Modal>
    </Card>
  );
}

// ---- 수동주행 입력 (MQTT 계약 §7) ----

const RESULT_LABEL: Record<InputResult, string> = {
  SENT: '보냄',
  RELEASED: '놓음',
  NOT_MANUAL: 'MANUAL 이 아니라 버려짐',
  NOT_CONNECTED: '백엔드가 브로커에 연결되지 않음',
};

/**
 * 눌린 키 집합이 바뀔 때만 보낸다. 누르고 있는 동안의 반복(20 Hz)은 백엔드가 한다 (MQTT 계약 §7).
 * MANUAL 이 아니면 입력을 받지 않는다 — 백엔드도 같은 이유로 버린다. 손을 떼거나 창을 벗어나면 정지(200 ms).
 */
function ManualInput() {
  const { drive } = useDrive();
  const mode = drive?.control?.mode ?? null;
  const { socket, result, sendKeys } = useManualInput();
  const [held, setHeld] = useState<DriveKey[]>([]);
  const heldRef = useRef<DriveKey[]>([]);
  const enabled = mode === 'MANUAL' && socket === 'open';
  const manual = drive?.apps.manual?.last_command;

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

  const pad = (key: DriveKey, text: string, extra = '') => (
    <button
      type="button"
      className={[styles.key, held.includes(key) ? styles.keyOn : '', extra].join(' ')}
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
      <p className={enabled ? styles.dim : styles.draft}>
        {socket !== 'open'
          ? '입력 소켓 연결 중…'
          : enabled
            ? '화살표·WASD 로 운전, 스페이스는 브레이크. 손을 떼면 0.2초 뒤 정지한다.'
            : `MANUAL 일 때만 입력 전달 — 지금 ${mode ?? '모드 없음'} 이라 입력 무시`}
      </p>
      <div className={styles.pad} style={{ '--accent': ROLES.manual.color } as CSSProperties}>
        <span />
        {pad('up', '▲')}
        <span />
        {pad('left', '◀')}
        {pad('brake', '■')}
        {pad('right', '▶')}
        <span />
        {pad('down', '▼')}
        <span />
      </div>
      <div className={styles.rows}>
        <div>
          <span>눌린 키</span>
          <span>
            {held.length ? held.join(' + ') : '없음'}
            {result && ` · ${RESULT_LABEL[result]}`}
          </span>
        </div>
        <div>
          <span>수동주행 명령</span>
          <span>{manual ? `${plain(manual.v_mps)} m/s · ${signed(manual.steer_rad)} rad` : '—'}</span>
        </div>
      </div>
    </>
  );
}

/** [자율주행 재개] — AUTO_READY 에서만 활성 (MQTT 계약 §6 drive.resume.requested). 웹 → 백엔드 경로는 계약 미정 */
function Resume() {
  const { drive } = useDrive();
  const ready = drive?.control?.mode === 'AUTO_READY';
  return (
    <div style={{ marginTop: 'var(--x-space-4)' }}>
      <Button style={{ width: '100%' }} disabled={!ready || !mockDemo} onClick={() => mockDemo?.resume()}>
        ▶ 자율주행 재개
      </Button>
      <p className={styles.dim}>
        {ready
          ? mockDemo
            ? '자율주행이 3초 연속 정상 — 누르면 AUTO 로 돌아간다.'
            : '재개 API 가 아직 없다 (웹 → 백엔드 → drive.resume.requested 경로 계약 추가 필요).'
          : 'AUTO_READY 에서만 활성. 자율주행 복귀는 자동이 아니라 운영자가 누른다.'}
      </p>
    </div>
  );
}
