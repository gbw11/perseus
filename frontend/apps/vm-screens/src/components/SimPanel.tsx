import { DriverView, StatusBadge, TrackView } from '@xenova/ui';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { carColor, mps, rad, seconds, shownKeys } from './format';
import styles from './screens.module.css';

/** 시뮬레이터 칸·화면의 상태 배지 */
export function SimBadge({ size = 'lg' }: { size?: 'sm' | 'lg' }) {
  const { telemetry: t } = useTelemetry();
  const f = t.vehicle.flags;
  const moving = (t.sim?.pose?.v_mps ?? 0) > 0.02;
  return (
    <StatusBadge
      health={f.estop || f.obstacle || f.watchdog ? 'fault' : moving ? 'ok' : 'stopped'}
      label={
        f.estop
          ? 'E-stop'
          : f.obstacle
            ? '장애물 정지'
            : f.watchdog
              ? '와치독 정지'
              : moving
                ? '주행 중'
                : '정지'
      }
      size={size}
    />
  );
}

const KEYS = [
  ['left', '◀'],
  ['up', '▲'],
  ['down', '▼'],
  ['right', '▶'],
  ['brake', '정지'],
] as const;

/** 조작 키 — 웹(관리 콘솔 시연 제어)에서 들어온 눌린 키만 밝게. 모니터는 보여 주기만 한다 */
export function KeyCaps({ mini }: { mini?: boolean }) {
  const { telemetry: t } = useTelemetry();
  const keys = shownKeys(t);
  return (
    <div
      className={mini ? styles.keysMini : styles.keys}
      style={{ ['--accent' as string]: 'var(--x-app-manual)' }}
    >
      {KEYS.map(([key, text]) => (
        <span key={key} className={[styles.keyCap, keys.includes(key) ? styles.keyCapOn : ''].join(' ')}>
          {text}
        </span>
      ))}
    </div>
  );
}

/**
 * 자율·수동 칸의 시뮬레이터 1인칭 시점. 차는 한 대라 두 칸의 시야는 같고, 지금 운전하는 쪽만 또렷하다.
 * 자율 칸은 인지 값, 수동 칸은 조작 키를 겹친다.
 */
export function SimDriverView({ id, large }: { id: 'autonomous' | 'manual'; large?: boolean }) {
  const { telemetry: t } = useTelemetry();
  const driving = t.activeApp === id;
  const obstacles = t.obstacleCount ?? t.perception.obstacles.length;
  return (
    <DriverView
      pose={t.sim?.pose ?? null}
      world={t.sim?.world ?? null}
      sensing={t.vehicle.flags.obstacle}
      dimmed={!driving}
      label={`${id === 'autonomous' ? '자율주행' : '수동주행'} 시점 — 시뮬레이터 1인칭`}
      overlay={
        <>
          <div className={styles.povTop} style={{ ['--accent' as string]: `var(--x-app-${id})` }}>
            <span className={styles.povTag}>{driving ? '▶ 운전 중' : '대기'}</span>
            <span className={styles.povHud}>{mps(t.vehicle.v_mps)}</span>
          </div>
          {id === 'autonomous' ? (
            <div className={styles.povBottom}>
              <span className={styles.povHud}>
                차선 {t.perception.lane_offset_m >= 0 ? '+' : ''}
                {t.perception.lane_offset_m.toFixed(2)} m · 장애물 {obstacles || '없음'}
                {t.vehicle.flags.obstacle ? ' · 정지' : ''}
              </span>
            </div>
          ) : (
            <KeyCaps mini={!large} />
          )}
        </>
      }
    />
  );
}

/** 네비게이션 VM 알림 띠 — 모니터 1 칸에서 빠진 대신 여기 한 줄로 (그 VM 이 죽어도 차는 계속 — D8 격리) */
export function InteractionStrip() {
  const { telemetry: t } = useTelemetry();
  const down = t.apps.interaction.health === 'fault' || t.apps.interaction.domain !== 'running';
  const latest = t.events[0];
  return (
    <div className={[styles.strip, down ? styles.stripDown : ''].join(' ')}>
      <span className={styles.stripLabel}>네비게이션</span>
      {down ? (
        <span>응답 없음 — 주행은 그대로</span>
      ) : latest ? (
        <span>
          <span className={styles.mono}>{seconds(t.now - latest.at)} 전</span> {latest.text}
        </span>
      ) : (
        <span className={styles.dim}>알림 없음</span>
      )}
    </div>
  );
}

/**
 * 모니터 1 세 번째 칸 — 시뮬레이터 (시뮬레이터 시연 시나리오 §4). 시뮬레이터 위치가 1초 안에 오면 네비게이션 칸
 * 대신 이 칸이 뜬다 (화면 배치 계약 v1.1 §5.6). 다른 두 칸과 같은 틀(머리·역할·본문)을 쓴다.
 */
export function SimPanel({ onSelect }: { onSelect?: () => void }) {
  const { telemetry: t } = useTelemetry();
  const sim = t.sim;
  const pose = sim?.pose ?? null;
  return (
    <section
      className={[styles.panel, styles.health_ok].join(' ')}
      style={{ ['--accent' as string]: 'var(--x-app-simulator)' }}
      onClick={onSelect}
    >
      <header className={styles.panelHead}>
        <div>
          <h2 className={styles.panelTitle}>시뮬레이터</h2>
          <span className={styles.guest}>노트북 · ESP32 자리</span>
        </div>
        <SimBadge />
      </header>
      <div className={styles.panelRole}>트랙 탑뷰 — 실차 대신 화면 속 차</div>
      <div className={styles.panelBody}>
        <TrackView
          compact
          pose={pose}
          world={sim?.world ?? null}
          trail={sim?.trail}
          carColor={carColor(t)}
          sensing={t.vehicle.flags.obstacle}
        />
        <dl className={styles.kv}>
          <dt>속도 · 조향</dt>
          <dd className={styles.mono}>{pose ? `${mps(pose.v_mps)} · ${rad(pose.steer_rad)}` : '—'}</dd>
          <dt>장애물</dt>
          <dd>{sim?.world?.obstacles.length ? `${sim.world.obstacles.length}개 — 트랙 위` : '없음'}</dd>
        </dl>
        <InteractionStrip />
      </div>
    </section>
  );
}
