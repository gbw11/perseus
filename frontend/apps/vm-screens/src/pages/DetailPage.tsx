import { StatusBadge } from '@xenova/ui';
import { Navigate, useParams } from 'react-router';
import { ResumeState } from '../components/AppPanel';
import { ModeBar } from '../components/ModeBar';
import { TrackMap } from '../components/TrackMap';
import { mps, rad, seconds } from '../components/format';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { APPS, APP_IDS, type AppId, type Telemetry } from '../telemetry/types';
import styles from '../components/screens.module.css';

const obstacles = (t: Telemetry) => t.obstacleCount ?? t.perception.obstacles.length;

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
        {meta.guest === 'zephyr' ? 'Zephyr' : 'Debian 13'} · {meta.topicAppId} · {meta.role}
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
          max={1}
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
          <dd>{obstacles(t) ? `${obstacles(t)}개 — 감속` : '없음'}</dd>
        </dl>
      </section>
    </>
  );
}

function ManualDetail() {
  const { telemetry: t } = useTelemetry();
  const active = t.activeApp === 'manual';

  // 모니터는 보여 주기만 한다 (화면 정의서 §1) — 웹에서 들어온 눌린 키만 밝게
  const cap = (key: string, text: string) => (
    <span className={[styles.keyCap, t.manualKeys.includes(key) ? styles.keyCapOn : ''].join(' ')}>
      {text}
    </span>
  );

  return (
    <>
      <section className={styles.detailMain}>
        <h2>{active ? '운영자 운전 중' : '대기 중 — 자율주행에 문제가 생기면 바로 이어받는다'}</h2>
        <div className={styles.keys} style={{ ['--accent' as string]: 'var(--x-app-manual)' }}>
          {cap('left', '◀')}
          {cap('up', '▲')}
          {cap('down', '▼')}
          {cap('right', '▶')}
          {cap('brake', '브레이크')}
        </div>
        <p className={styles.dim}>
          입력은 관리 콘솔 시연 제어에서 들어온다. 200 ms 동안 없으면 정지 명령(v = 0)을 50 Hz 로 낸다.
        </p>
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
  const { telemetry: t } = useTelemetry();
  const down = t.apps.interaction.domain !== 'running' || t.apps.interaction.health === 'fault';
  return (
    <>
      <section className={styles.detailMain}>
        <h2>내비게이션 — 트랙 지도 · 추정 위치</h2>
        {down ? (
          <p className={styles.dim}>인터랙션 VM 이 내려가 있다. 주행은 영향 없이 계속된다 (격리).</p>
        ) : (
          <TrackMap large />
        )}
      </section>
      <section className={styles.detailSide}>
        <h2>자율주행 재개 상태</h2>
        <ResumeState />
        <p className={styles.dim}>
          복구된 자율주행 VM 이 3초 연속 정상이면 AUTO_READY. 운영자가 관리 콘솔에서 재개를 누르면 3·2·1 을 센
          뒤 바뀐다 — 모니터에는 버튼이 없다.
        </p>
        <h2>알림</h2>
        {down ? (
          <p className={styles.dim}>—</p>
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
    </>
  );
}
