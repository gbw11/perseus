import { StatusBadge } from '@xenova/ui';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APPS, type AppId } from '../telemetry/types';
import { mps, rad, seconds } from './format';
import styles from './screens.module.css';

const ACCENT: Record<AppId, string> = {
  autonomous: 'var(--x-app-autonomous)',
  manual: 'var(--x-app-manual)',
  interaction: 'var(--x-app-interaction)',
};

/** 모니터 1 의 한 칸 (RC카 시나리오 §5) */
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
            {meta.guest === 'zephyr' ? 'Zephyr' : 'Debian'} · {meta.topicAppId}
          </span>
        </div>
        <StatusBadge health={status.health} label={badgeLabel} size="lg" />
      </header>

      <div className={styles.panelRole}>{meta.role}</div>
      {active && <div className={styles.activeTag}>▶ 차량 제어 중</div>}

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
        ? '자율주행 중'
        : '운영자 운전 중 (입력 없으면 정지)'
      : id === 'manual'
        ? '대기 — 바로 이어받을 준비'
        : '대기 — 명령 신선도 확인 중';

  return (
    <dl className={styles.kv}>
      <dt>상태</dt>
      <dd>{state}</dd>
      <dt>명령 주기</dt>
      <dd className={styles.mono}>{sending ? `${status.commandHz} Hz` : '—'}</dd>
      <dt>마지막 명령</dt>
      <dd className={styles.mono}>
        {command && sending ? `${mps(command.v_mps)} / ${rad(command.steer_rad)}` : '—'}
      </dd>
      {id === 'autonomous' ? (
        <>
          <dt>인지 (젯슨)</dt>
          <dd className={styles.mono}>
            차선 {t.perception.lane_offset_m.toFixed(2)} m · 장애물 {t.perception.obstacles.length}
          </dd>
        </>
      ) : (
        <>
          <dt>키 입력</dt>
          <dd className={styles.mono}>{t.manualKeys.length ? t.manualKeys.join(' ') : '없음'}</dd>
        </>
      )}
    </dl>
  );
}

function InteractionSummary() {
  const { telemetry: t } = useTelemetry();
  if (t.apps.interaction.health === 'fault' || t.apps.interaction.domain !== 'running') {
    return (
      <p className={styles.dim}>
        화면 없음 — VM {t.apps.interaction.domain === 'booting' ? '재시작 중' : '중지됨'}
      </p>
    );
  }
  return (
    <ul className={styles.feed}>
      {t.events.slice(0, 5).map((event) => (
        <li key={event.id} className={styles[`ev_${event.level}`]}>
          {event.text}
        </li>
      ))}
    </ul>
  );
}
