import {
  circuitOf,
  fmtLap,
  isCircuit,
  kmh,
  obstaclesAhead,
  realM,
  TrackView,
  type Circuit,
} from '@xenova/ui';
import { useTelemetry } from '../telemetry/TelemetryProvider';
import type { Telemetry } from '../telemetry/types';
import { carColor } from './format';
import styles from './screens.module.css';

/**
 * 모니터 2 상세 화면의 시뮬레이터 부분 (계약 v1.4 실제 서킷일 때만). 값은 sim.pose·sim.world 에서 화면이 계산한다 —
 * 인지 대역(-268)이 시뮬레이터 위치로 계산하는 방식과 같다 (시나리오 §2). 거리·속도는 실제 서킷 단위.
 */
const NAME: Record<string, string> = { cone: '콘', box: '박스', tires: '타이어' };
/** 인지 대역 인식 거리 (모의 m) */
const LOOK_M = 4;

function circuitOfT(t: Telemetry): Circuit | null {
  const track = t.sim?.world?.track;
  return isCircuit(track) ? circuitOf(track) : null;
}

/** 인식한 장애물 (차 앞 4 m = 실제 약 200 m 안) — 종류, 앞 거리, 차 기준 왼쪽·오른쪽 */
export function SimObstacles() {
  const { telemetry: t } = useTelemetry();
  const g = circuitOfT(t);
  const pose = t.sim?.pose;
  if (!g || !pose) return null;
  const seen = obstaclesAhead(g, pose, t.sim?.world?.obstacles ?? [], LOOK_M);
  return (
    <>
      <h2>인식한 장애물 (앞 {Math.round(realM(g, LOOK_M))} m 안)</h2>
      {seen.length ? (
        <table className={styles.simTable}>
          <thead>
            <tr>
              <th>종류</th>
              <th>앞 거리</th>
              <th>차 기준 옆</th>
            </tr>
          </thead>
          <tbody>
            {seen.map(({ o, ahead, side }) => (
              <tr key={o.id}>
                <td>{NAME[o.kind ?? 'cone']}</td>
                <td className={styles.mono}>{ahead > 0 ? `${Math.round(realM(g, ahead))} m` : '옆'}</td>
                <td className={styles.mono}>
                  {Math.abs(side) < 0.03
                    ? '정면'
                    : `${side > 0 ? '왼쪽' : '오른쪽'} ${realM(g, Math.abs(side)).toFixed(1)} m`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className={styles.dim}>없음 — 차선 중앙을 따라 달린다</p>
      )}
    </>
  );
}

/** 지금 판단 (화면 추정) — 인식한 장애물과 차 위치·명령으로 */
export function SimJudgement() {
  const { telemetry: t } = useTelemetry();
  const g = circuitOfT(t);
  const pose = t.sim?.pose;
  if (!g || !pose) return null;
  const race = pose.race;
  const seen = obstaclesAhead(g, pose, t.sim?.world?.obstacles ?? [], LOOK_M).filter((s) => s.ahead > -0.3);
  const first = seen[0];
  const text =
    race?.phase === 'lights'
      ? `출발 신호 기다림 (${race.lights_on}/5)`
      : t.vehicle.flags.estop
        ? 'E-stop — 정지'
        : t.perception.stop || t.vehicle.flags.obstacle
          ? '정지 — 지나갈 틈 없음'
          : first
            ? `회피 — ${first.side > 0 ? '오른쪽' : '왼쪽'} 틈으로 비켜 감 (${NAME[first.o.kind ?? 'cone']} ${Math.round(realM(g, Math.max(0, first.ahead)))} m 앞)`
            : Math.abs(pose.steer_rad) > 0.12
              ? '코너 — 속도를 줄이고 차선을 따라 꺾음'
              : '직선 — 최고 속도로 차선 중앙';
  return (
    <div className={styles.simJudge}>
      <small>지금 판단</small>
      <strong>{text}</strong>
    </div>
  );
}

/** 출발·랩 */
export function SimRace() {
  const { telemetry: t } = useTelemetry();
  const g = circuitOfT(t);
  const pose = t.sim?.pose;
  const race = pose?.race;
  if (!g || !pose || !race) return null;
  return (
    <dl className={styles.kv}>
      <dt>트랙</dt>
      <dd>
        {g.track.name} · {(g.track.real_m / 1000).toFixed(3)} km
      </dd>
      <dt>출발 · 랩</dt>
      <dd className={styles.mono}>
        {race.phase === 'lights'
          ? `출발 신호 ${race.lights_on}/5 — 모두 꺼지면 출발`
          : `${race.lap + 1}랩 ${fmtLap(race.lap_ms)}`}
        {race.jump_start ? ' · 부정 출발 +5초' : ''}
      </dd>
      <dt>지난 · 최고</dt>
      <dd className={styles.mono}>
        {fmtLap(race.last_lap_ms)} · {fmtLap(race.best_lap_ms)}
      </dd>
      <dt>노면</dt>
      <dd className={pose.surface && pose.surface !== 'road' ? styles.warnText : undefined}>
        {pose.surface === 'grass'
          ? `잔디 — 감속 (최대 ${Math.round(kmh(g, 0.3))} km/h)`
          : pose.surface === 'dirt'
            ? `흙·자갈 — 감속 (최대 ${Math.round(kmh(g, 0.3))} km/h)`
            : '아스팔트'}
      </dd>
    </dl>
  );
}

/** 작은 탑뷰 — 차가 트랙 어디쯤인지 */
export function SimMiniMap() {
  const { telemetry: t } = useTelemetry();
  const sim = t.sim;
  if (!sim?.world || !isCircuit(sim.world.track)) return null;
  return (
    <TrackView
      compact
      pose={sim.pose}
      world={sim.world}
      trail={sim.trail}
      carColor={carColor(t)}
      label="작은 트랙 지도 — 차 위치"
    />
  );
}
