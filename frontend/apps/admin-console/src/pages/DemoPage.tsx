import { Button, Card, Modal, StatusBadge, useToast } from '@xenova/ui';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { ApiError } from '../api/client';
import {
  KEY_OF,
  mockDemo,
  cancelResume,
  pressKey,
  requestResume,
  useManualInput,
  type DriveKey,
  type InputResult,
} from '../api/drive';
import { useDisplays, useRouteDisplay, useVms } from '../api/queries';
import type { Display, DisplayStatus, DisplayView } from '../api/types';
import {
  appHealth,
  appHealthLabel,
  osLabel,
  plain,
  ROLE_ORDER,
  ROLES,
  signed,
  vmOf,
  type AppId,
} from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import { SimTrackControl } from './SimTrackControl';
import styles from './pages.module.css';

/**
 * W-07 시연 제어 (화면 정의서 W-07, UI-08·DS-01) — 공병우 -128·-130, 격리 시연 API 는 정송하 -135.
 * 위: 격리 시연 — 세 VM 을 나란히 두고 각자 [강제 종료]. 다른 둘이 계속 도는 것을 같은 줄에서 본다.
 * 아래: 화면 배치(REST-23·24, -129) / 수동주행 입력 + 자율주행 재개(POST /api/v1/drive/resume, 3·2·1).
 * 강제 종료 API(-135)는 아직 없다 → mock 에서만 동작.
 */
export function DemoPage() {
  const { drive } = useDrive();
  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>시연 제어</div>
          <h1>격리 시연</h1>
        </div>
        <span className={styles.grow} />
        {mockDemo ? (
          <Button variant="ghost" onClick={mockDemo.togglePerception}>
            {drive?.perception?.valid === false ? '인지 복구' : '인지 끊김 (D5)'}
          </Button>
        ) : (
          <span className={styles.draft}>강제 종료 API(-135) 미구현 — mock 모드에서만 동작</span>
        )}
      </section>
      <div className={styles.cols3}>
        {ROLE_ORDER.map((app) => (
          <IsolationCard key={app} app={app} />
        ))}
      </div>
      {/* 시뮬레이터 구성일 때만 (실차 구성에는 없다) */}
      <SimTrackControl />
      <div className={styles.cols2}>
        <ScreenRouting />
        <Card title="수동주행 입력">
          <ManualInput />
          <Resume />
        </Card>
      </div>
    </div>
  );
}

// ---- 격리 시연 (UI-08, -135 미구현) ----

const KILL_EFFECT: Record<AppId, string> = {
  autonomous: '명령이 끊겨 게이트웨이가 수동주행으로 넘긴다 (MANUAL, gen +1). 복구되면 AUTO_READY.',
  manual: '수동주행이 제어 중이면 SAFE_STOP, 아니면 예비만 사라진다.',
  'message-board': '주행과 무관 — 차는 그대로 달린다 (격리).',
};

function IsolationCard({ app }: { app: AppId }) {
  const { drive } = useDrive();
  const vms = useVms();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const role = ROLES[app];
  const vm = vmOf(app, vms.data?.items, drive);
  const state = drive?.apps[app];
  const health = appHealth(role, vm, state);
  const linux = vm?.guest_kind === 'linux';
  const down = vm?.state !== 'running' || health === 'recovering' || health === 'fault';
  const active = !!vm && drive?.control?.active_vm_id === vm.vm_id && drive.control.received_ago_ms < 3000;
  return (
    <Card>
      <div className={styles.appCard} style={{ borderTop: `3px solid ${role.color}` }}>
        <div className={styles.row}>
          <b style={{ color: role.color, fontSize: 'var(--x-text-lg)' }}>{role.title}</b>
          <span className={styles.grow} />
          <StatusBadge health={health} label={appHealthLabel(vm, state)} size="sm" />
        </div>
        <div className={styles.eyebrow}>
          {vm ? `${vm.name} · ${osLabel(vm)}` : app}
          {active ? ' · 제어 중' : ''}
        </div>
        <div className={styles.rows}>
          <div>
            <span>{role.driving ? '명령' : '생존 신호'}</span>
            <span className={styles.mono}>
              {role.driving
                ? state?.command_fresh
                  ? `${state.command_hz} Hz`
                  : '없음'
                : state?.heartbeat_ago_ms != null
                  ? `${(state.heartbeat_ago_ms / 1000).toFixed(1)}초 전`
                  : '없음'}
            </span>
          </div>
        </div>
        <p className={styles.dim}>강제 종료 시: {KILL_EFFECT[app]}</p>
        <div className={styles.row}>
          <Button
            variant="danger"
            style={{ flex: 1 }}
            disabled={!mockDemo || down}
            title={down ? '이미 꺼져 있거나 복구 중' : undefined}
            onClick={() => setConfirm(true)}
          >
            강제 종료
          </Button>
          <Button
            variant="secondary"
            disabled
            title={
              linux ? '에이전트 부하 명령(-123) 미구현' : 'Zephyr VM 은 에이전트가 없어 부하를 줄 수 없다'
            }
          >
            CPU 부하
          </Button>
        </div>
      </div>
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
                mockDemo?.forceKill(app);
                setConfirm(false);
                toast.show(`${role.title} 강제 종료 — 복구 서비스가 다시 시작한다`, 'fault', 3000);
              }}
            >
              강제 종료
            </Button>
          </>
        }
      >
        <p>
          <b style={{ color: role.color }}>{role.title}</b> 도메인을 강제 종료한다. 디스크는 지우지 않고 복구
          서비스가 다시 시작한다.
        </p>
        <p className={styles.dim}>{KILL_EFFECT[app]}</p>
      </Modal>
    </Card>
  );
}

// ---- 화면 배치 (DS-01, REST-23·REST-24 -129) ----

const STATUS_LABEL: Record<DisplayStatus, string> = {
  DISCONNECTED: '모니터 꺼짐',
  IDLE: '대기',
  SWITCHING: '바꾸는 중',
  ACTIVE: '적용됨',
  ERROR: '오류',
};
const STATUS_HEALTH = {
  DISCONNECTED: 'stopped',
  IDLE: 'stopped',
  SWITCHING: 'recovering',
  ACTIVE: 'ok',
  ERROR: 'fault',
} as const;

interface Choice {
  key: string;
  label: string;
  view: DisplayView;
  app: AppId | null;
}
const CHOICES: Choice[] = [
  { key: 'split', label: '3분할', view: 'split', app: null },
  ...ROLE_ORDER.map((app) => ({
    key: app,
    label: `${ROLES[app].short} 상세`,
    view: 'os-face' as const,
    app,
  })),
];

/** 모니터마다 무엇을 띄울지. 고르면 바로 PUT — 표시 서비스가 route.applied 로 답하면 "적용됨" */
function ScreenRouting() {
  const displays = useDisplays();
  return (
    <Card title="화면 배치">
      {displays.error && (
        <p className={styles.error} role="alert">
          {displays.error instanceof ApiError ? displays.error.message : String(displays.error)}
        </p>
      )}
      {displays.isPending && <p className={styles.dim}>불러오는 중…</p>}
      <div className={styles.form}>
        {displays.data?.items.map((d) => (
          <DisplayRow key={d.display_id} display={d} />
        ))}
      </div>
      <p className={styles.dim}>
        고르면 백엔드가 dom0 표시 서비스에 전달하고, 키오스크가 화면을 다 띄우면 "적용됨" 이 된다 (화면 배치
        계약 display-route-v1). mock 에서는 <code>/monitor/?display=display-0</code> 으로 연 모니터가 표시
        서비스 대신 따라간다.
      </p>
    </Card>
  );
}

function DisplayRow({ display }: { display: Display }) {
  const { drive } = useDrive();
  const vms = useVms();
  const route = useRouteDisplay();
  const toast = useToast();
  const vmIdOf = (app: AppId | null) => (app ? (vmOf(app, vms.data?.items, drive)?.vm_id ?? null) : null);
  const current = CHOICES.find(
    (c) => c.view === display.desired_route.view && vmIdOf(c.app) === display.desired_route.vm_id,
  );
  const pick = (choice: Choice) => {
    const vmId = vmIdOf(choice.app);
    if (choice.app && !vmId) return toast.show(`${ROLES[choice.app].title} VM 을 찾지 못했다`, 'fault', 3000);
    route.mutate(
      {
        displayId: display.display_id,
        body: { vm_id: vmId, view: choice.view, expected_revision: display.revision },
      },
      {
        onError: (error) =>
          toast.show(error instanceof ApiError ? error.message : String(error), 'fault', 5000),
      },
    );
  };
  return (
    <div className={styles.form}>
      <span className={styles.row}>
        <b>{display.label}</b>
        <span className={styles.eyebrow}>{display.display_id}</span>
        <span className={styles.grow} />
        <StatusBadge
          health={STATUS_HEALTH[display.status]}
          label={`${STATUS_LABEL[display.status]} · rev ${display.revision}`}
          size="sm"
        />
      </span>
      <span className={styles.chips}>
        {CHOICES.map((c) => (
          <button
            key={c.key}
            type="button"
            className={[styles.chip, current?.key === c.key ? styles.chipOn : ''].join(' ')}
            disabled={route.isPending || display.status === 'SWITCHING'}
            onClick={() => current?.key !== c.key && pick(c)}
          >
            {c.label}
          </button>
        ))}
      </span>
      {display.last_error && <span className={styles.error}>{display.last_error}</span>}
    </div>
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

/**
 * [자율주행 재개] — AUTO_READY 에서만 활성. 누르면 POST /api/v1/drive/resume (MQTT-13).
 * 바로 바뀌지 않는다: 게이트웨이가 받아들이면 3·2·1 카운트다운 뒤 AUTO (계약 v1.2 §6, 화면 위 알림).
 */
function Resume() {
  const { drive } = useDrive();
  const toast = useToast();
  const [sending, setSending] = useState(false);
  const ready = drive?.control?.mode === 'AUTO_READY';
  const counting = drive?.resume ?? null;
  const auto = counting?.requested_by === 'auto';
  const send = async (job: () => Promise<void> = requestResume) => {
    setSending(true);
    try {
      await job();
    } catch (error) {
      toast.show(error instanceof Error ? error.message : String(error), 'fault', 5000);
    } finally {
      setSending(false);
    }
  };
  return (
    <div style={{ marginTop: 'var(--x-space-4)' }}>
      <Button
        style={{ width: '100%' }}
        disabled={!ready || !!counting || sending}
        onClick={() => void send()}
      >
        {counting
          ? `▶ ${Math.max(1, Math.ceil(counting.remaining_ms / 1000))}초 뒤 자율주행${auto ? ' (자동 복귀)' : ''}`
          : '▶ 자율주행 재개'}
      </Button>
      {counting && (
        <Button
          variant="danger"
          style={{ width: '100%', marginTop: 'var(--x-space-2)' }}
          disabled={sending}
          onClick={() => void send(cancelResume)}
        >
          취소 — 수동주행 유지
        </Button>
      )}
      <p className={styles.dim}>
        {counting
          ? auto
            ? '자율주행이 복구돼 자동으로 돌아가는 중 — [취소]하면 수동주행이 계속 몰고, 그 뒤에는 [재개]로만 돌아간다.'
            : '카운트다운 중 — 그 사이 자율주행이 다시 끊기면 취소되고 수동주행이 유지된다.'
          : ready
            ? '자동 복귀를 취소했다. 누르면 3·2·1 을 센 뒤 AUTO 로 돌아간다.'
            : '자율주행이 복구돼 3초 연속 정상이면(AUTO_READY) 자동으로 3·2·1 뒤 돌아간다.'}
      </p>
    </div>
  );
}
