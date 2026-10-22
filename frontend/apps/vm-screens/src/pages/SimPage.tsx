import { fmtLap, isCircuit, TrackView } from '@xenova/ui';
import { InteractionStrip, SimBadge } from '../components/SimPanel';
import { ModeBar } from '../components/ModeBar';
import { carColor, distText, rad, speedText } from '../components/format';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import { simLive } from '../telemetry/types';
import styles from '../components/screens.module.css';

/**
 * 모니터 2 — 시뮬레이터 크게 (화면 배치 view `sim`, screen_path `/sim`). 상세 화면과 같은 틀:
 * 위 모드 띠, 머리(이름·배지), 왼쪽 큰 그림, 오른쪽 값.
 */
export function SimPage() {
  const { telemetry: t } = useTelemetry();
  const sim = t.sim;
  const pose = sim?.pose ?? null;
  const live = simLive(t);
  const track = sim?.world?.track;
  const race = pose?.race;
  return (
    <div className={styles.screen}>
      <ModeBar />
      <div className={styles.detailHead} style={{ ['--accent' as string]: 'var(--x-app-simulator)' }}>
        <h1>시뮬레이터</h1>
        <span className={styles.guest}>
          노트북 · 게이트웨이 시리얼 뒤 (ESP32 자리) ·{' '}
          {isCircuit(track) ? `${track.name} ${(track.real_m / 1000).toFixed(3)} km (1/50)` : '트랙 6 × 3 m'}
        </span>
        <SimBadge />
        {t.activeApp && (
          <span className={styles.activeTag} style={{ ['--accent' as string]: carColor(t) }}>
            ▶ {t.activeApp === 'autonomous' ? '자율주행' : '수동주행'} 제어 중
          </span>
        )}
      </div>
      <div className={styles.detail}>
        <section className={styles.detailMain}>
          <h2>트랙 탑뷰 (sim.pose 20 Hz · sim.world)</h2>
          <TrackView
            className={styles.simTrack}
            pose={live ? pose : null}
            world={sim?.world ?? null}
            trail={sim?.trail}
            carColor={carColor(t)}
            sensing={t.vehicle.flags.obstacle}
          />
          {!live && (
            <p className={styles.dim}>
              시뮬레이터 위치가 들어오지 않는다 — 실차 구성이거나 시뮬레이터가 꺼져 있다.
            </p>
          )}
        </section>
        <section className={styles.detailSide}>
          <h2>차 (시뮬레이터)</h2>
          <dl className={styles.kv}>
            <dt>위치 x, y</dt>
            <dd className={styles.mono}>{pose ? `${pose.x.toFixed(2)}, ${pose.y.toFixed(2)} m` : '—'}</dd>
            <dt>방향</dt>
            <dd className={styles.mono}>{pose ? `${Math.round((pose.yaw * 180) / Math.PI)}°` : '—'}</dd>
            <dt>속도 · 조향</dt>
            <dd className={styles.mono}>
              {pose ? `${speedText(t, pose.v_mps)} · ${rad(pose.steer_rad)}` : '—'}
            </dd>
            {race && (
              <>
                <dt>출발 · 랩</dt>
                <dd className={styles.mono}>
                  {race.phase === 'lights'
                    ? `출발 신호 ${race.lights_on}/5 — 모두 꺼지면 출발`
                    : `${race.lap + 1}랩 ${fmtLap(race.lap_ms)} · 지난 ${fmtLap(race.last_lap_ms)} · 최고 ${fmtLap(race.best_lap_ms)}${race.jump_start ? ' · 부정 출발 +5초' : ''}`}
                </dd>
                <dt>노면</dt>
                <dd>
                  {pose?.surface === 'grass'
                    ? '잔디 (감속)'
                    : pose?.surface === 'dirt'
                      ? '흙·자갈 (감속)'
                      : '아스팔트'}
                </dd>
              </>
            )}
          </dl>
          <h2>트랙 (sim.world)</h2>
          <dl className={styles.kv}>
            <dt>revision</dt>
            <dd className={styles.mono}>{sim?.world?.revision ?? '—'}</dd>
            <dt>장애물</dt>
            <dd>
              {sim?.world?.obstacles.length
                ? sim.world.obstacles.map((o) => `${o.kind ?? o.id}`).join(', ')
                : '없음'}
            </dd>
            <dt>E-stop</dt>
            <dd>{sim?.world?.estop ? '걸림' : '풀림'}</dd>
          </dl>
          <h2>자율주행 판단 (인지 대역)</h2>
          <dl className={styles.kv}>
            <dt>차선 오프셋</dt>
            <dd className={styles.mono}>{distText(t, t.perception.lane_offset_m)}</dd>
            <dt>앞 장애물</dt>
            <dd>{(t.obstacleCount ?? t.perception.obstacles.length) || '없음'}</dd>
          </dl>
          <InteractionStrip />
          <p className={styles.dim}>
            장애물 놓기·차 밀기는 관리 콘솔 시연 제어에서 한다 (-267). 모니터는 보여 주기만 한다.
          </p>
        </section>
      </div>
    </div>
  );
}
