import { useEffect, useRef, type ReactNode } from 'react';
import { CircuitPov } from '../sim/SimCanvas';
import { isCircuit, type CircuitTrack, type EllipseTrack, type SimPose, type SimWorld } from '../sim/types';
import styles from './DriverView.module.css';

export interface DriverViewProps {
  pose: SimPose | null;
  world: SimWorld | null;
  /** 차 앞 근거리 감지에 장애물이 걸렸다 — 장애물 테두리를 강조한다 */
  sensing?: boolean;
  /** 화면 위에 겹칠 것 (누가 운전 중인지, 조작 키 등) */
  overlay?: ReactNode;
  /** 시점이 흐리게 (이 앱이 운전하지 않을 때) */
  dimmed?: boolean;
  label?: string;
  /** v1.4 실제 서킷: 장애물 인식 박스 (자율주행 칸) */
  boxes?: boolean;
  /** v1.4 실제 서킷: 'low' 면 나무·관중·구름을 뺀다 (보드 브라우저가 느릴 때) */
  quality?: 'high' | 'low';
}

const DEFAULT_TRACK: EllipseTrack = { shape: 'ellipse', a_m: 3, b_m: 1.5, lane_width_m: 0.4 };
/** 카메라: 1/10 RC카 앞범퍼 위 0.22 m, 아래로 9°, 수평 화각 75° */
const CAM_H = 0.22;
const CAM_PITCH = (9 * Math.PI) / 180;
const HFOV = (75 * Math.PI) / 180;
const CAM_AHEAD = 0.12;
const FAR_M = 7;
const OBSTACLE_H = 0.18;
const STEPS = 720;

type Pt = { u: number; v: number; z: number };

/**
 * 시뮬레이터 차의 1인칭 시점 — 운전석에서 앞을 본 그림 (시뮬레이터 시연 시나리오 §4).
 * 시뮬레이터 위치(sim.pose)와 트랙·장애물(sim.world)로 지면을 원근 투영해 캔버스에 그린다. 실제 카메라 영상이 아니다.
 */
export function DriverView({
  pose,
  world,
  sensing,
  overlay,
  dimmed,
  label,
  boxes,
  quality,
}: DriverViewProps) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext('2d');
    if (!el || !ctx) return;
    const rect = el.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.max(1, Math.round(rect.width * dpr));
    const H = Math.max(1, Math.round(rect.height * dpr));
    if (el.width !== W || el.height !== H) {
      el.width = W;
      el.height = H;
    }
    draw(
      ctx,
      W,
      H,
      pose,
      (world?.track as EllipseTrack | undefined) ?? DEFAULT_TRACK,
      world?.obstacles ?? [],
      !!sensing,
      getComputedStyle(el),
    );
  }, [pose, world, sensing]);

  // v1.4 실제 서킷이면 하늘·나무·출발 신호까지 그리는 1인칭
  if (pose && world && isCircuit(world.track))
    return (
      <div
        className={[styles.frame, dimmed ? styles.dimmed : ''].join(' ')}
        role="img"
        aria-label={label ?? '시뮬레이터 1인칭 시점'}
      >
        <CircuitPov
          pose={pose}
          world={world as SimWorld & { track: CircuitTrack }}
          boxes={boxes}
          quality={quality}
        />
        {overlay && <div className={styles.overlay}>{overlay}</div>}
      </div>
    );
  return (
    <div className={[styles.frame, dimmed ? styles.dimmed : ''].join(' ')}>
      <canvas
        ref={canvas}
        className={styles.canvas}
        role="img"
        aria-label={label ?? '시뮬레이터 1인칭 시점'}
      />
      {!pose && <div className={styles.empty}>시뮬레이터 위치 없음</div>}
      {overlay && <div className={styles.overlay}>{overlay}</div>}
    </div>
  );
}

function draw(
  ctx: CanvasRenderingContext2D,
  W: number,
  H: number,
  pose: SimPose | null,
  track: EllipseTrack,
  obstacles: SimWorld['obstacles'],
  sensing: boolean,
  css: CSSStyleDeclaration,
) {
  const color = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  const F = W / 2 / Math.tan(HFOV / 2);
  const cx = W / 2;
  const cy = H * 0.42;
  const horizon = cy - F * Math.tan(CAM_PITCH);

  // 하늘·지면
  const sky = ctx.createLinearGradient(0, 0, 0, Math.max(1, horizon));
  sky.addColorStop(0, color('--x-surface-sunken', '#0b1016'));
  sky.addColorStop(1, color('--x-surface-3', '#252a33'));
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, W, Math.max(0, horizon));
  ctx.fillStyle = color('--x-surface-2', '#1b2027');
  ctx.fillRect(0, Math.max(0, horizon), W, H);
  if (!pose) return;

  const cos = Math.cos(pose.yaw);
  const sin = Math.sin(pose.yaw);
  const camX = pose.x + CAM_AHEAD * cos;
  const camY = pose.y + CAM_AHEAD * sin;
  /** 지면(트랙 좌표) → 화면. 높이 h 의 점도 */
  const project = (x: number, y: number, h = 0): Pt | null => {
    const dx = x - camX;
    const dy = y - camY;
    const fwd = dx * cos + dy * sin;
    const left = -dx * sin + dy * cos;
    const up = h - CAM_H;
    const z = fwd * Math.cos(CAM_PITCH) - up * Math.sin(CAM_PITCH);
    const down = -up * Math.cos(CAM_PITCH) - fwd * Math.sin(CAM_PITCH);
    if (z < 0.04 || fwd > FAR_M) return null;
    return { u: cx - (F * left) / z, v: cy + (F * down) / z, z };
  };

  const { a_m: a, b_m: b, lane_width_m: lw } = track;
  const ring = (offset: number, theta: number) => {
    // 타원 위 점에서 바깥(+)으로 offset 만큼
    const px = a * Math.cos(theta);
    const py = b * Math.sin(theta);
    const nx = b * Math.cos(theta);
    const ny = a * Math.sin(theta);
    const n = Math.hypot(nx, ny);
    return [px + (offset * nx) / n, py + (offset * ny) / n] as const;
  };

  // 노면을 조각(사다리꼴)으로 나눠 먼 것부터 그린다
  const road = color('--x-track-road', '#2b313b');
  const edge = color('--x-track-edge', '#59606b');
  const lane = color('--x-track-lane', '#e8b23a');
  type Quad = [Pt, Pt, Pt, Pt];
  const quads: { z: number; pts: Quad; dash: boolean; mid: [Pt, Pt] | null }[] = [];
  const pad = lw / 2 + 0.06;
  for (let i = 0; i < STEPS; i++) {
    const t0 = (i / STEPS) * Math.PI * 2;
    const t1 = ((i + 1) / STEPS) * Math.PI * 2;
    const o0 = ring(pad, t0);
    const o1 = ring(pad, t1);
    const n0 = ring(-pad, t0);
    const n1 = ring(-pad, t1);
    const a0 = project(...o0);
    const a1 = project(...o1);
    const b1 = project(...n1);
    const b0 = project(...n0);
    if (!a0 || !a1 || !b1 || !b0) continue;
    const pts: Quad = [a0, a1, b1, b0];
    const c0 = project(...ring(0, t0));
    const c1 = project(...ring(0, t1));
    quads.push({
      z: (pts[0].z + pts[2].z) / 2,
      pts,
      dash: Math.floor(i / 6) % 2 === 0,
      mid: c0 && c1 ? [c0, c1] : null,
    });
  }
  quads.sort((q1, q2) => q2.z - q1.z);
  for (const q of quads) {
    ctx.beginPath();
    q.pts.forEach((p, k) => (k ? ctx.lineTo(p.u, p.v) : ctx.moveTo(p.u, p.v)));
    ctx.closePath();
    ctx.fillStyle = road;
    ctx.fill();
    ctx.strokeStyle = road; // 조각 사이 틈을 메운다
    ctx.lineWidth = 1;
    ctx.stroke();
    // 가장자리 선
    ctx.strokeStyle = edge;
    ctx.lineWidth = Math.max(1, (W / 400) * (1.5 / q.z));
    ctx.beginPath();
    ctx.moveTo(q.pts[0].u, q.pts[0].v);
    ctx.lineTo(q.pts[1].u, q.pts[1].v);
    ctx.moveTo(q.pts[3].u, q.pts[3].v);
    ctx.lineTo(q.pts[2].u, q.pts[2].v);
    ctx.stroke();
    if (q.dash && q.mid) {
      ctx.strokeStyle = lane;
      ctx.lineWidth = Math.max(1, (W / 300) * (1.2 / q.z));
      ctx.beginPath();
      ctx.moveTo(q.mid[0].u, q.mid[0].v);
      ctx.lineTo(q.mid[1].u, q.mid[1].v);
      ctx.stroke();
    }
  }

  // 장애물: 폭 w, 높이 0.18 m 상자 (먼 것부터)
  const fault = color('--x-fault', '#ef4444');
  const boxes = obstacles
    .map((o) => {
      const dx = o.x - camX;
      const dy = o.y - camY;
      const sideX = -sin * (o.w / 2);
      const sideY = cos * (o.w / 2);
      const bl = project(o.x + sideX, o.y + sideY);
      const br = project(o.x - sideX, o.y - sideY);
      const tl = project(o.x + sideX, o.y + sideY, OBSTACLE_H);
      return bl && br && tl ? { z: dx * cos + dy * sin, bl, br, tl } : null;
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((p, q) => q.z - p.z);
  for (const o of boxes) {
    const x0 = Math.min(o.bl.u, o.br.u);
    const w = Math.abs(o.br.u - o.bl.u);
    const h = o.bl.v - o.tl.v;
    ctx.fillStyle = fault;
    ctx.globalAlpha = 0.85;
    ctx.fillRect(x0, o.tl.v, w, h);
    ctx.globalAlpha = 1;
    if (sensing && o.z < 1.2) {
      // 감지 박스 (ESP32 근거리 감지 흉내)
      ctx.strokeStyle = fault;
      ctx.lineWidth = Math.max(2, W / 250);
      ctx.strokeRect(x0 - w * 0.15, o.tl.v - h * 0.25, w * 1.3, h * 1.4);
    }
  }

  // 차 앞코 (화면 아래)
  ctx.fillStyle = color('--x-surface', '#171c24');
  ctx.beginPath();
  ctx.moveTo(W * 0.18, H);
  ctx.quadraticCurveTo(W * 0.5, H * 0.86, W * 0.82, H);
  ctx.closePath();
  ctx.fill();
}
