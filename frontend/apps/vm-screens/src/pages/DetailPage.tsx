import { Button, StatusBadge } from '@xenova/ui';
import { useEffect } from 'react';
import { Navigate, useParams } from 'react-router';
import { ModeBar } from '../components/ModeBar';
import { mps, rad, seconds } from '../components/format';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APPS, APP_IDS, type AppId } from '../telemetry/types';
import styles from '../components/screens.module.css';

/** 모니터 2: 관리자가 고른 1개를 크게 (RC카 시나리오 §5) */
export function DetailPage() {
  const { app } = useParams();
  if (!APP_IDS.includes(app as AppId)) return <Navigate to="/split" replace />;
  const id = app as AppId;
  return (
    <div className={styles.screen}>
      <ModeBar />
      <DetailHeader id={id} />
      <div className={styles.detail}>
        {id === 'autonomous' && <AutonomousDetail />}
        {id === 'manual' && <ManualDetail />}
        {id === 'interaction' && <InteractionDetail />}
      </div>
    </div>
  );
}

function DetailHeader({ id }: { id: AppId }) {
  const { telemetry: t } = useTelemetry();
  const status = t.apps[id];
  const meta = APPS[id];
  return (
    <div className={styles.detailHead} style={{ ['--accent' as string]: `var(--x-app-${id})` }}>
      <h1>{meta.title}</h1>
      <span className={styles.guest}>
        {meta.guest === 'zephyr' ? 'Zephyr' : 'Debian'} · {meta.topicAppId} · {meta.role}
      </span>
      <StatusBadge
        health={status.health}
        size="lg"
        label={status.health === 'recovering' ? `복구 중 ${seconds(t.now - status.healthSince)}` : undefined}
      />
      {t.activeApp === id && <span className={styles.activeTag}>▶ 차량 제어 중</span>}
    </div>
  );
}

interface GaugeProps {
  title: string;
  value: number;
  min: number;
  max: number;
  text: string;
}

function Gauge({ title, value, min, max, text }: GaugeProps) {
  const ratio = Math.min(1, Math.max(0, (value - min) / (max - min)));
  return (
    <div className={styles.gauge}>
      <span className={styles.gaugeTitle}>{title}</span>
      <div className={styles.gaugeTrack}>
        <div className={styles.gaugeFill} style={{ width: `${ratio * 100}%` }} />
      </div>
      <span className={styles.gaugeText}>{text}</span>
    </div>
  );
}

function AutonomousDetail() {
  const { telemetry: t } = useTelemetry();
  const command = t.commands.autonomous;
  const alive = t.apps.autonomous.commandHz > 0 && t.apps.autonomous.health !== 'fault';
  // 위에서 내려다본 도로: 가로 -0.5~0.5 m, 세로 0~2 m
  const x = (m: number) => 200 + m * 300;
  const y = (m: number) => 380 - m * 170;
  const steerDeg = (-(alive ? (command?.steer_rad ?? 0) : 0) * 180) / Math.PI;
  return (
    <>
      <section className={styles.detailMain}>
        <h2>인지 결과 (젯슨 perception.result 15 Hz)</h2>
        <svg viewBox="0 0 400 400" className={styles.road} role="img" aria-label="차선과 장애물">
          <rect x="50" y="0" width="300" height="400" className={styles.roadBg} />
          <line x1="50" y1="0" x2="50" y2="400" className={styles.lane} />
          <line x1="350" y1="0" x2="350" y2="400" className={styles.lane} />
          <line x1="200" y1="0" x2="200" y2="400" className={styles.center} />
          {t.perception.obstacles.map((o, i) => (
            <rect
              key={i}
              x={x(o.x - o.w / 2)}
              y={y(o.y) - 20}
              width={o.w * 300}
              height="40"
              className={styles.obstacle}
            />
          ))}
          <g transform={`translate(${x(t.perception.lane_offset_m)} 340) rotate(${steerDeg})`}>
            <rect x="-22" y="-36" width="44" height="64" rx="8" className={styles.car} />
          </g>
        </svg>
      </section>
      <section className={styles.detailSide}>
        <h2>주행 명령 (drive.command 50 Hz)</h2>
        <Gauge
          title="목표 속도"
          value={alive ? (command?.v_mps ?? 0) : 0}
          min={0}
          max={0.5}
          text={alive && command ? mps(command.v_mps) : '—'}
        />
        <Gauge
          title="목표 조향"
          value={alive ? (command?.steer_rad ?? 0) : 0}
          min={-0.5}
          max={0.5}
          text={alive && command ? rad(command.steer_rad) : '—'}
        />
        <dl className={styles.kv}>
          <dt>명령 주기</dt>
          <dd className={styles.mono}>{alive ? `${t.apps.autonomous.commandHz} Hz` : '명령 없음'}</dd>
          <dt>차선 오프셋</dt>
          <dd className={styles.mono}>{t.perception.lane_offset_m.toFixed(3)} m</dd>
          <dt>방향 오차</dt>
          <dd className={styles.mono}>{t.perception.heading_err_rad.toFixed(3)} rad</dd>
          <dt>장애물</dt>
          <dd>{t.perception.obstacles.length ? `${t.perception.obstacles.length}개 — 감속` : '없음'}</dd>
        </dl>
      </section>
    </>
  );
}

const KEY_OF: Record<string, string> = {
  ArrowUp: 'up',
  w: 'up',
  ArrowDown: 'down',
  s: 'down',
  ArrowLeft: 'left',
  a: 'left',
  ArrowRight: 'right',
  d: 'right',
};

function ManualDetail() {
  const { telemetry: t, setManualKeys } = useTelemetry();
  const active = t.activeApp === 'manual';

  // 키보드 화살표·WASD → drive.input 흉내 (MQTT 계약 §7: 실제로는 콘솔 → 백엔드 → MQTT)
  useEffect(() => {
    const pressed = new Set<string>();
    const sync = () => setManualKeys([...pressed]);
    const down = (event: KeyboardEvent) => {
      const key = KEY_OF[event.key];
      if (key && !pressed.has(key)) {
        pressed.add(key);
        sync();
      }
    };
    const up = (event: KeyboardEvent) => {
      const key = KEY_OF[event.key];
      if (key && pressed.delete(key)) sync();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      setManualKeys([]);
    };
  }, [setManualKeys]);

  const release = (key: string) => setManualKeys(t.manualKeys.filter((k) => k !== key));
  const pad = (key: string, text: string) => (
    <button
      type="button"
      className={[styles.key, t.manualKeys.includes(key) ? styles.keyOn : ''].join(' ')}
      onPointerDown={() => setManualKeys([...new Set([...t.manualKeys, key])])}
      onPointerUp={() => release(key)}
      onPointerLeave={() => release(key)}
    >
      {text}
    </button>
  );

  return (
    <>
      <section className={styles.detailMain}>
        <h2>{active ? '운영자 운전 중' : '대기 중 — 자율주행에 문제가 생기면 바로 이어받는다'}</h2>
        <div className={styles.pad}>
          <span />
          {pad('up', '▲')}
          <span />
          {pad('left', '◀')}
          {pad('down', '▼')}
          {pad('right', '▶')}
        </div>
        <p className={styles.dim}>화살표·WASD 또는 버튼. 입력이 없으면 정지 명령(v = 0)을 50 Hz 로 낸다.</p>
      </section>
      <section className={styles.detailSide}>
        <h2>주행 명령</h2>
        <Gauge
          title="목표 속도"
          value={t.commands.manual?.v_mps ?? 0}
          min={-0.15}
          max={0.3}
          text={t.commands.manual ? mps(t.commands.manual.v_mps) : '—'}
        />
        <Gauge
          title="목표 조향"
          value={t.commands.manual?.steer_rad ?? 0}
          min={-0.5}
          max={0.5}
          text={t.commands.manual ? rad(t.commands.manual.steer_rad) : '—'}
        />
        <dl className={styles.kv}>
          <dt>역할</dt>
          <dd>{active ? '활성 (게이트웨이가 이 명령을 차량에 전달)' : '대기 (신선도 확인용)'}</dd>
          <dt>명령 주기</dt>
          <dd className={styles.mono}>
            {t.apps.manual.commandHz ? `${t.apps.manual.commandHz} Hz` : '명령 없음'}
          </dd>
        </dl>
      </section>
    </>
  );
}

function InteractionDetail() {
  const { telemetry: t, resume } = useTelemetry();
  const down = t.apps.interaction.domain !== 'running' || t.apps.interaction.health === 'fault';
  const buttonText =
    t.mode === 'AUTO_READY' ? '자율주행 재개' : t.mode === 'AUTO' ? '자율주행 중' : '재개 대기';
  return (
    <>
      <section className={styles.detailMain}>
        <h2>알림 타임라인 (drive.mode.changed · drive.fault.detected · message.posted)</h2>
        {down ? (
          <p className={styles.dim}>인터랙션 VM 이 내려가 있다. 주행은 영향 없이 계속된다 (격리).</p>
        ) : (
          <ul className={styles.timeline}>
            {t.events.map((event) => (
              <li key={event.id} className={styles[`ev_${event.level}`]}>
                <span className={styles.mono}>{seconds(t.now - event.at)} 전</span>
                <span className={styles.evType}>{event.type}</span>
                {event.text}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className={styles.detailSide}>
        <h2>자율주행 재개</h2>
        <p className={styles.dim}>
          복구된 자율주행 VM 의 명령이 3초 연속 신선하면 AUTO_READY. 그때만 재개를 받는다.
        </p>
        <Button size="lg" onClick={resume} disabled={down || t.mode !== 'AUTO_READY'}>
          {buttonText}
        </Button>
      </section>
    </>
  );
}
