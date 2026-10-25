import { useRef, type DragEvent, type MouseEvent, type PointerEvent } from 'react';
import { circuitOf, fmtLap } from './circuit';
import { drawPov } from './renderPov';
import { drawTop, fitTop } from './renderTop';
import type { CircuitTrack, SimPose, SimWorld } from './types';
import { extrapolate, useCanvasLoop, usePoseClock } from './useCanvasLoop';
import styles from './SimCanvas.module.css';

const DEFAULT_CAR = { length_m: 0.42, width_m: 0.2 };

/** 탑뷰를 누르거나 끌 때 — 트랙 좌표 (모의 m) */
export interface TrackPointer {
  type: 'down' | 'move' | 'up' | 'double';
  x: number;
  y: number;
  /** 손가락·마우스를 누른 채인가 (move 에서) */
  pressed: boolean;
}

export interface CircuitTopProps {
  pose: SimPose | null;
  world: SimWorld & { track: CircuitTrack };
  trail: [number, number][];
  carColor: string;
  label?: string;
  className?: string;
  fps?: number;
  /** 관리자 조작용: 누르기·끌기·두 번 누르기를 트랙 좌표로 */
  onPointer?: (e: TrackPointer) => void;
  /** 강조할 장애물 id (끌고 있는 것) */
  selectedId?: string | null;
  /** 못 놓은 자리 표시 (빨간 X) */
  rejectAt?: { x: number; y: number } | null;
  /** 지도 밖에서 끌어 온 것 (HTML 끌어 놓기) — 위에 있는 동안 트랙 좌표, 놓으면 트랙 좌표 */
  onDragTrack?: (e: { type: 'over' | 'leave' | 'drop'; x: number; y: number }) => void;
  /** 끌어 오는 동안 놓일 자리 미리 보기 (폭 m) */
  preview?: { x: number; y: number; w: number } | null;
}

/** 실제 서킷 탑뷰 (계약 v1.4) — 잔디·흙·벽·관중석·나무, 장애물, 지나온 길, 실제 크기 차 */
export function CircuitTop({
  pose,
  world,
  trail,
  carColor,
  label,
  className,
  fps = 30,
  onPointer,
  selectedId,
  rejectAt,
  onDragTrack,
  preview,
}: CircuitTopProps) {
  const clock = usePoseClock(pose);
  const ref = useCanvasLoop((ctx, W, H, now) => {
    const g = circuitOf(world.track);
    const p = clock.current.pose;
    // 캔버스는 CSS 변수(var(--x-app-…))를 못 읽는다 → 실제 색으로
    const m = /^var\((--[\w-]+)\)$/.exec(carColor.trim());
    const color = m ? getComputedStyle(ctx.canvas).getPropertyValue(m[1]!).trim() || '#2dd4bf' : carColor;
    drawTop(ctx, W, H, g, {
      pose: p ? extrapolate(p, now - clock.current.at, world.time_scale) : null,
      obstacles: world.obstacles,
      trail,
      carColor: color,
      car: world.car ?? DEFAULT_CAR,
    });
    if (!selectedId && !rejectAt && !preview) return;
    const v = fitTop(g, W, H);
    const px = (x: number, y: number) => [v.ox + x * v.s, v.oy - y * v.s] as const;
    const sel = world.obstacles.find((o) => o.id === selectedId);
    if (sel) {
      const [u, vv] = px(sel.x, sel.y);
      ctx.beginPath();
      ctx.arc(u, vv, Math.max(9, sel.w * v.s), 0, Math.PI * 2);
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    if (preview) {
      const [u, vv] = px(preview.x, preview.y);
      ctx.beginPath();
      ctx.arc(u, vv, Math.max(8, (preview.w / 2) * v.s + 4), 0, Math.PI * 2);
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (rejectAt) {
      const [u, vv] = px(rejectAt.x, rejectAt.y);
      const r = Math.max(8, 0.15 * v.s);
      ctx.strokeStyle = '#ef4444';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(u - r, vv - r);
      ctx.lineTo(u + r, vv + r);
      ctx.moveTo(u + r, vv - r);
      ctx.lineTo(u - r, vv + r);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(u, vv, r * 1.4, 0, Math.PI * 2);
      ctx.stroke();
    }
  }, fps);
  const g = circuitOf(world.track);
  const pressed = useRef(false);
  /** 화면 좌표 → 트랙 좌표 (캔버스 크기에 맞춘 배율로) */
  const toTrack = (e: {
    clientX: number;
    clientY: number;
    currentTarget: EventTarget & HTMLCanvasElement;
  }) => {
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    const k = el.width / Math.max(1, r.width);
    const v = fitTop(g, el.width, el.height);
    return { x: ((e.clientX - r.left) * k - v.ox) / v.s, y: (v.oy - (e.clientY - r.top) * k) / v.s };
  };
  const pointer = onPointer
    ? {
        onPointerDown: (e: PointerEvent<HTMLCanvasElement>) => {
          pressed.current = true;
          try {
            e.currentTarget.setPointerCapture(e.pointerId); // 지도 밖으로 끌어도 계속 받는다
          } catch {
            // 포인터가 이미 끝났으면 무시
          }
          onPointer({ type: 'down', pressed: true, ...toTrack(e) });
        },
        onPointerMove: (e: PointerEvent<HTMLCanvasElement>) =>
          onPointer({ type: 'move', pressed: pressed.current, ...toTrack(e) }),
        onPointerUp: (e: PointerEvent<HTMLCanvasElement>) => {
          pressed.current = false;
          onPointer({ type: 'up', pressed: false, ...toTrack(e) });
        },
        onDoubleClick: (e: MouseEvent<HTMLCanvasElement>) =>
          onPointer({ type: 'double', pressed: false, ...toTrack(e) }),
      }
    : {};
  const drop = onDragTrack
    ? {
        onDragOver: (e: DragEvent<HTMLCanvasElement>) => {
          e.preventDefault(); // 놓을 수 있다고 알린다
          e.dataTransfer.dropEffect = 'copy';
          onDragTrack({ type: 'over', ...toTrack(e) });
        },
        onDragLeave: (e: DragEvent<HTMLCanvasElement>) => onDragTrack({ type: 'leave', ...toTrack(e) }),
        onDrop: (e: DragEvent<HTMLCanvasElement>) => {
          e.preventDefault();
          onDragTrack({ type: 'drop', ...toTrack(e) });
        },
      }
    : {};
  const aspect = (g.box.x1 - g.box.x0) / (g.box.y1 - g.box.y0);
  return (
    <div className={[styles.top, className ?? ''].join(' ')} style={{ aspectRatio: String(aspect) }}>
      <canvas
        ref={ref}
        {...pointer}
        {...drop}
        className={[styles.canvas, onPointer ? styles.editable : ''].join(' ')}
        role="img"
        aria-label={label ?? `${world.track.name} 탑뷰`}
      />
      {world.estop && <div className={styles.estop}>E-STOP</div>}
      {!pose && <div className={styles.empty}>시뮬레이터 위치 없음</div>}
    </div>
  );
}

export interface CircuitPovProps {
  pose: SimPose;
  world: SimWorld & { track: CircuitTrack };
  /** 장애물 인식 박스 (자율주행 칸) */
  boxes?: boolean;
  quality?: 'high' | 'low';
  fps?: number;
}

/** 실제 서킷 1인칭 (계약 v1.4) — 하늘·나무·관중석·출발 신호, 인식 박스. 출발 신호·랩 시간은 위에 겹친다 */
export function CircuitPov({ pose, world, boxes = false, quality = 'high', fps = 30 }: CircuitPovProps) {
  const clock = usePoseClock(pose);
  const race = pose.race;
  const ref = useCanvasLoop((ctx, W, H, now) => {
    const p = clock.current.pose ?? pose;
    drawPov(ctx, W, H, circuitOf(world.track), {
      pose: extrapolate(p, now - clock.current.at, world.time_scale),
      obstacles: world.obstacles,
      lightsOn: p.race?.phase === 'lights' ? p.race.lights_on : 0,
      boxes,
      quality,
    });
  }, fps);
  const ts = world.time_scale ?? 1;
  const waiting = race?.phase === 'lights';
  const justGo = race?.phase === 'go' && race.lap === 0 && race.lap_ms < 1500 * ts;
  return (
    <>
      <canvas ref={ref} className={styles.canvas} />
      {race && (
        <>
          <span className={styles.lap}>
            {race.lap + 1}랩 {fmtLap(race.lap_ms)}
          </span>
          {(waiting || justGo) && (
            <div className={styles.lights} aria-label={`출발 신호 ${race.lights_on}/5`}>
              {[0, 1, 2, 3, 4].map((k) => (
                <span key={k} className={waiting && k < race.lights_on ? styles.on : ''}>
                  <i />
                  <i />
                </span>
              ))}
            </div>
          )}
          {race.jump_start && (waiting || justGo) ? (
            <div className={[styles.go, styles.jump].join(' ')}>부정 출발!</div>
          ) : waiting ? (
            <div className={styles.wait}>모두 꺼지면 출발</div>
          ) : justGo ? (
            <div className={styles.go}>출발!</div>
          ) : null}
        </>
      )}
    </>
  );
}
