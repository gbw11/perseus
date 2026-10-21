import { useTelemetry } from '../telemetry/TelemetryProvider';
import { LAP_M, poseAt, RADIUS_M, SEGMENTS, STRAIGHT_M } from './track';
import styles from './screens.module.css';

/** 네비게이션 그림 — 트랙 모양·위치 계산은 ./track */
const SCALE = 60;
const PAD = 30;
const sx = (x: number) => PAD + (x + RADIUS_M) * SCALE;
const sy = (y: number) => PAD + (RADIUS_M - y) * SCALE;
const W = 2 * PAD + (STRAIGHT_M + 2 * RADIUS_M) * SCALE;
const H = 2 * PAD + 2 * RADIUS_M * SCALE;
const TRACK = [
  `M ${sx(0)} ${sy(-RADIUS_M)}`,
  `L ${sx(STRAIGHT_M)} ${sy(-RADIUS_M)}`,
  `A ${RADIUS_M * SCALE} ${RADIUS_M * SCALE} 0 0 0 ${sx(STRAIGHT_M)} ${sy(RADIUS_M)}`,
  `L ${sx(0)} ${sy(RADIUS_M)}`,
  `A ${RADIUS_M * SCALE} ${RADIUS_M * SCALE} 0 0 0 ${sx(0)} ${sy(-RADIUS_M)}`,
].join(' ');

export function TrackMap({ large = false }: { large?: boolean }) {
  const { telemetry: t } = useTelemetry();
  const distance = t.odometer?.distanceM ?? 0;
  const pose = poseAt(distance);
  const next = SEGMENTS[(pose.segment + 1) % SEGMENTS.length]!;
  const deg = (-pose.heading * 180) / Math.PI;
  const moving = Math.abs(t.vehicle.v_mps) > 0.01;
  return (
    <div className={[styles.nav, large ? styles.navLarge : ''].join(' ')}>
      <svg viewBox={`0 0 ${W} ${H}`} className={styles.navMap} role="img" aria-label="트랙 지도와 추정 위치">
        <path d={TRACK} className={styles.navTrack} />
        <path d={TRACK} className={styles.navLine} />
        <line
          x1={sx(0)}
          y1={sy(-RADIUS_M) - 12}
          x2={sx(0)}
          y2={sy(-RADIUS_M) + 12}
          className={styles.navStart}
        />
        <g transform={`translate(${sx(pose.x)} ${sy(pose.y)}) rotate(${deg})`}>
          <circle r="16" className={styles.navHalo} />
          <path d="M 11 0 L -8 -8 L -4 0 L -8 8 Z" className={styles.navCar} />
        </g>
      </svg>
      <dl className={styles.navStats}>
        <div>
          <dt>다음</dt>
          <dd>
            {next.name} · {pose.toNextM.toFixed(1)} m
          </dd>
        </div>
        <div>
          <dt>지금</dt>
          <dd>
            {SEGMENTS[pose.segment]!.name}
            {moving ? '' : ' (정지)'}
          </dd>
        </div>
        <div>
          <dt>바퀴</dt>
          <dd className={styles.mono}>
            {pose.lap + 1}바퀴째 · {Math.round(pose.progress * 100)}%
          </dd>
        </div>
        <div>
          <dt>추정 거리</dt>
          <dd className={styles.mono}>{distance.toFixed(1)} m</dd>
        </div>
      </dl>
      {large && (
        <p className={styles.dim}>
          위치 센서 없이 차량 속도를 적분한 추정값 — 트랙 {LAP_M.toFixed(1)} m 를 출발점부터 따라 돈다고 본다.
        </p>
      )}
    </div>
  );
}
