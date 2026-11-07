/**
 * 시뮬레이터 메시지 모양 — MQTT 계약 v1.4 §11 sim.pose·sim.world (WS-06 sim.snapshot 으로 받는다).
 * 좌표는 트랙 고정 좌표 (원점 = 트랙 중심, x 오른쪽, y 위쪽, yaw 반시계 rad), 값은 모의 단위 m·m/s.
 */

/** 출발 신호·랩 (v1.4). F1 방식: 빨간 불 다섯 개가 켜지고 모두 꺼지면 go */
export interface SimRace {
  phase: 'lights' | 'go';
  lights_on: number;
  lap: number;
  lap_ms: number;
  last_lap_ms: number | null;
  best_lap_ms: number | null;
  jump_start: boolean;
}

export interface SimPose {
  x: number;
  y: number;
  yaw: number;
  v_mps: number;
  steer_rad: number;
  world_revision: number;
  /** v1.4: 차 중심이 있는 노면 */
  surface?: 'road' | 'grass' | 'dirt';
  /** v1.4 */
  race?: SimRace;
}

/** v1.3 타원 트랙 (예전 시뮬레이터) */
export interface EllipseTrack {
  shape: 'ellipse';
  a_m: number;
  b_m: number;
  lane_width_m: number;
}

/** v1.4 실제 서킷 트랙 — 중심선과 점마다 벽까지 거리 */
export interface CircuitTrack {
  shape?: 'centerline';
  id: string;
  name: string;
  /** 실제 서킷 한 바퀴 길이 (m) — 화면 단위 변환에 쓴다 */
  real_m: number;
  lane_width_m: number;
  curb_m: number;
  centerline: [number, number][];
  wall_left_m: number[];
  wall_right_m: number[];
  start_index: number;
  /** 실측 전 시험 코스의 명시 지면·선·차량·카메라 가정. 기존 서킷에는 없다. */
  layout?: {
    bounds: [number, number, number, number];
    lane_count: 2;
    /** 후륜축 기준 x/y/yaw, 세계 좌표는 y 위쪽. */
    start_pose: [number, number, number];
    paved_areas: [number, number][][];
    paint_lines: [number, number][][];
    parking_slots: { id: string; outline: [number, number][] }[];
    obstacle_seeds: { id: string; kind: 'cone'; x: number; y: number; w: number; h: number }[];
    vehicle: { length_m: number; width_m: number; wheelbase_m: number; min_turn_radius_m: number };
    camera: { height_m: number; pitch_rad: number; hfov_rad: number; mount_x_m: number };
    assumptions: string;
  };
}

export type SimTrack = EllipseTrack | CircuitTrack;

export type ObstacleKind = 'cone' | 'box' | 'tires';

export interface SimObstacle {
  id: string;
  /** v1.4 (없으면 콘으로 그린다) */
  kind?: ObstacleKind;
  x: number;
  y: number;
  w: number;
}

export interface SimWorld {
  revision: number;
  track: SimTrack;
  /** v1.4: 고를 수 있는 트랙 */
  tracks?: { id: string; name: string; real_m: number }[];
  /** v1.4: 판정에 쓰는 차 몸 크기 */
  car?: { length_m: number; width_m: number };
  /** 시뮬레이터 시간 배율 (1 = 실제 시간). 화면이 위치를 이어 그릴 때 쓴다 */
  time_scale?: number;
  obstacles: SimObstacle[];
  estop: boolean;
}

export function isCircuit(track: SimTrack | null | undefined): track is CircuitTrack {
  return !!track && track.shape !== 'ellipse' && Array.isArray((track as CircuitTrack).centerline);
}
