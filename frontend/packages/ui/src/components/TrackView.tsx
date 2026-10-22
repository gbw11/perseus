import type { CSSProperties } from 'react';
import styles from './TrackView.module.css';

/** MQTT 계약 v1.3 §11 sim.pose — 트랙 고정 좌표 (원점 = 타원 중심, x 오른쪽, y 위쪽, yaw 반시계) */
export interface SimPose {
  x: number;
  y: number;
  yaw: number;
  v_mps: number;
  steer_rad: number;
  world_revision: number;
}

/** MQTT 계약 v1.3 §11 sim.world */
export interface SimWorld {
  revision: number;
  track: { shape: 'ellipse'; a_m: number; b_m: number; lane_width_m: number };
  obstacles: { id: string; x: number; y: number; w: number }[];
  estop: boolean;
}

export interface TrackViewProps {
  pose: SimPose | null;
  world: SimWorld | null;
  /** 지나온 길 [x, y] (오래된 것부터) */
  trail?: [number, number][];
  /** 차 색 — 보통 지금 제어 중인 앱 색 (자율 / 수동, SAFE_STOP 이면 정지 색) */
  carColor?: string;
  /** 차 앞 근거리 감지(ESP32 ToF 흉내)에 장애물이 걸렸다 */
  sensing?: boolean;
  /** 좁은 칸용: 눈금을 줄인다 */
  compact?: boolean;
  /** 화면 낭독기용 설명 */
  label?: string;
  /** 크기를 바깥에서 정할 때 (예: 화면 높이에 맞추기). 그림은 비율을 지키며 가운데 맞춘다 */
  className?: string;
}

const DEFAULT_TRACK: SimWorld['track'] = { shape: 'ellipse', a_m: 3, b_m: 1.5, lane_width_m: 0.4 };
/** 시뮬레이터 감지 범위 (xenova_simulator SimSettings: 0.5 m, ±30°) */
const SENSE_M = 0.5;
const SENSE_HALF = Math.PI / 6;

/**
 * 시뮬레이터 화면 — 트랙을 위에서 내려다본 그림(탑뷰). 실차가 없는 시뮬레이터 구성에서 관객이 차 움직임을 보는 곳
 * (시뮬레이터 시연 시나리오 §4). SVG 는 y 가 아래로 자라므로 트랙 좌표를 뒤집은 그룹 안에 그린다.
 */
export function TrackView({
  pose,
  world,
  trail = [],
  carColor,
  sensing,
  compact,
  label,
  className,
}: TrackViewProps) {
  const track = world?.track ?? DEFAULT_TRACK;
  const { a_m: a, b_m: b, lane_width_m: lw } = track;
  const margin = lw / 2 + 0.5;
  const w = 2 * (a + margin);
  const h = 2 * (b + margin);
  const ticks = Array.from({ length: Math.floor(a) * 2 + 1 }, (_, i) => i - Math.floor(a));
  const deg = pose ? (pose.yaw * 180) / Math.PI : 0;

  return (
    <svg
      className={[styles.track, compact ? styles.compact : '', className ?? ''].join(' ')}
      viewBox={`${-w / 2} ${-h / 2} ${w} ${h}`}
      role="img"
      aria-label={label ?? '시뮬레이터 트랙 탑뷰'}
      style={carColor ? ({ '--car': carColor } as CSSProperties) : undefined}
    >
      <g transform="scale(1,-1)">
        <ellipse className={styles.road} cx="0" cy="0" rx={a + lw / 2 + 0.1} ry={b + lw / 2 + 0.1} />
        <ellipse className={styles.infield} cx="0" cy="0" rx={a - lw / 2 - 0.1} ry={b - lw / 2 - 0.1} />
        <ellipse className={styles.edge} cx="0" cy="0" rx={a + lw / 2} ry={b + lw / 2} />
        <ellipse className={styles.edge} cx="0" cy="0" rx={a - lw / 2} ry={b - lw / 2} />
        <ellipse className={styles.center} cx="0" cy="0" rx={a} ry={b} />
        {trail.length > 1 && (
          <polyline className={styles.trail} points={trail.map(([x, y]) => `${x},${y}`).join(' ')} />
        )}
        {world?.obstacles.map((o) => (
          <circle key={o.id} className={styles.obstacle} cx={o.x} cy={o.y} r={o.w / 2} />
        ))}
        {pose && (
          <g transform={`translate(${pose.x} ${pose.y}) rotate(${deg})`}>
            <path
              className={[styles.sense, sensing ? styles.senseOn : ''].join(' ')}
              d={`M0.2,0 L${0.2 + SENSE_M * Math.cos(SENSE_HALF)},${SENSE_M * Math.sin(SENSE_HALF)} A${SENSE_M},${SENSE_M} 0 0 0 ${0.2 + SENSE_M * Math.cos(SENSE_HALF)},${-SENSE_M * Math.sin(SENSE_HALF)} Z`}
            />
            <rect className={styles.car} x="-0.2" y="-0.09" width="0.4" height="0.18" rx="0.04" />
            <polygon className={styles.car} points="0.2,-0.07 0.29,0 0.2,0.07" />
          </g>
        )}
      </g>
      {!compact &&
        ticks.map((x) => (
          <text key={x} className={styles.tick} x={x} y={h / 2 - 0.12} textAnchor="middle">
            {x} m
          </text>
        ))}
      {world?.estop && (
        <text className={styles.estop} x="0" y="0.1" textAnchor="middle">
          E-STOP
        </text>
      )}
      {!pose && (
        <text className={styles.tick} x="0" y="0.05" textAnchor="middle">
          시뮬레이터 위치 없음
        </text>
      )}
    </svg>
  );
}
