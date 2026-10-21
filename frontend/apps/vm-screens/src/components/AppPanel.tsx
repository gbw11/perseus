import { StatusBadge } from '@xenova/ui';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APPS, type AppId } from '../telemetry/types';
import { seconds } from './format';
import { TrackMap } from './TrackMap';
import styles from './screens.module.css';

const ACCENT: Record<AppId, string> = {
  autonomous: 'var(--x-app-autonomous)',
  manual: 'var(--x-app-manual)',
  interaction: 'var(--x-app-interaction)',
};

const DOMAIN_LABEL = {
  running: '실행 중',
  crashed: '중지됨',
  booting: '재시작 중',
  stopped: '꺼짐',
} as const;

/**
 * 모니터 1 의 한 칸 (화면 정의서 M-01): VM 이름(역할), OS·app_id, 상태 배지(복구 중이면 경과 초), 제어 표시,
 * 핵심 값. 조작 요소는 없다 — 모니터는 보여 주기만 한다 (정의서 §1). 참고 시안: stitch_untitled_project/a_1.
 */
export function AppPanel({ id, onSelect }: { id: AppId; onSelect?: (id: AppId) => void }) {
  const { telemetry: t } = useTelemetry();
  const meta = APPS[id];
  const status = t.apps[id];
  const active = t.activeApp === id;
  const badgeLabel =
    status.health === 'recovering' ? `복구 중 ${seconds(t.now - status.healthSince)}` : undefined;

  return (
    <section
      className={[styles.panel, styles[`health_${status.health}`], active ? styles.active : ''].join(' ')}
      style={{ ['--accent' as string]: ACCENT[id] }}
      onClick={() => onSelect?.(id)}
    >
      <header className={styles.panelHead}>
        <div>
          <h2 className={styles.panelTitle}>{meta.title}</h2>
          <span className={styles.guest}>
            {meta.guest === 'zephyr' ? 'Zephyr' : 'Debian 13'} · {meta.topicAppId}
          </span>
        </div>
        <StatusBadge health={status.health} label={badgeLabel} size="lg" />
      </header>

      <div className={styles.panelRole}>{meta.role}</div>
      {active && <div className={styles.activeTag}>▶ 차량 제어 중</div>}
      {!active && id === 'manual' && status.commandHz > 0 && (
        <div className={styles.standbyTag}>예비 — 준비됨</div>
      )}

      <div className={styles.panelBody}>
        {id === 'interaction' ? <InteractionSummary /> : <DriveSummary id={id} />}
      </div>
    </section>
  );
}

function DriveSummary({ id }: { id: 'autonomous' | 'manual' }) {
  const { telemetry: t } = useTelemetry();
  const status = t.apps[id];
  const command = t.commands[id];
  const sending = status.commandHz > 0 && status.health !== 'fault';
  const state = !sending
    ? status.domain === 'crashed'
      ? 'VM 중지됨 — 명령 없음'
      : status.domain === 'booting'
        ? 'VM 재시작 중'
        : '명령 없음'
    : t.activeApp === id
      ? id === 'autonomous'
        ? t.perception.stop
          ? '인지 정지 신호 — 정지 명령 중'
          : '자율주행 중'
        : '운영자 운전 중 (입력 없으면 정지)'
      : id === 'manual'
        ? '대기 — 정지 명령(v=0) 50 Hz'
        : t.mode === 'MANUAL'
          ? '복구 확인 중 — 3초 연속 정상이면 재개 가능'
          : '대기 — 명령 신선도 확인 중';

  return (
    <>
      <div className={styles.tiles}>
        <div className={styles.tile}>
          <small>목표 속도</small>
          <span className={styles.tileValue}>{sending && command ? command.v_mps.toFixed(2) : '—'}</span>
          <span className={styles.tileUnit}>m/s</span>
        </div>
        <div className={styles.tile}>
          <small>목표 조향</small>
          <span className={styles.tileValue}>
            {sending && command ? `${command.steer_rad >= 0 ? '+' : ''}${command.steer_rad.toFixed(2)}` : '—'}
          </span>
          <span className={styles.tileUnit}>rad</span>
        </div>
      </div>
      <dl className={styles.kv}>
        <dt>상태</dt>
        <dd>{state}</dd>
        <dt>명령 주기</dt>
        <dd className={styles.mono}>{sending ? `${status.commandHz} Hz` : '—'}</dd>
        <dt>도메인</dt>
        <dd>{DOMAIN_LABEL[status.domain]}</dd>
        {id === 'autonomous' ? (
          <>
            <dt>인지 (젯슨)</dt>
            <dd className={styles.mono}>
              차선 {t.perception.lane_offset_m >= 0 ? '+' : ''}
              {t.perception.lane_offset_m.toFixed(2)} m · 장애물{' '}
              {t.obstacleCount ?? t.perception.obstacles.length}
            </dd>
          </>
        ) : (
          <>
            <dt>키 입력</dt>
            <dd className={styles.mono}>{t.manualKeys.length ? t.manualKeys.join(' + ') : '없음'}</dd>
          </>
        )}
      </dl>
    </>
  );
}

function InteractionSummary() {
  const { telemetry: t } = useTelemetry();
  if (t.apps.interaction.health === 'fault' || t.apps.interaction.domain !== 'running') {
    return (
      <p className={styles.dim}>
        화면 없음 — VM {t.apps.interaction.domain === 'booting' ? '재시작 중' : '중지됨'}. 주행은 영향 없이
        계속된다.
      </p>
    );
  }
  return (
    <>
      <TrackMap />
      <ResumeState />
      <h3 className={styles.dim}>최근 알림</h3>
      <ul className={styles.feed}>
        {t.events.slice(0, 3).map((event) => (
          <li key={event.id} className={styles[`ev_${event.level}`]}>
            {event.text}
          </li>
        ))}
      </ul>
    </>
  );
}

/** 재개 상태 한 줄 — 카운트다운 중이면 남은 초 */
export function ResumeState() {
  const { telemetry: t } = useTelemetry();
  const counting = t.resumeAt != null && t.resumeAt > t.now;
  return (
    <div
      className={[styles.resumeState, counting || t.mode === 'AUTO_READY' ? styles.resumeReady : ''].join(
        ' ',
      )}
    >
      {counting
        ? `${Math.ceil((t.resumeAt! - t.now) / 1000)}초 뒤 자율주행으로 전환`
        : t.mode === 'AUTO_READY'
          ? '자율주행 재개 가능'
          : t.mode === 'AUTO'
            ? '자율주행 중'
            : '재개 대기'}
    </div>
  );
}
