import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import type { SimPose } from './types';

/**
 * 위치는 10 Hz 로 오지만 화면은 매끄럽게 — 마지막 위치에서 속도·조향으로 조금 이어 그린다 (최대 0.3초).
 * time_scale 은 시뮬레이터 시간 배율 (1 = 실제 시간).
 */
export function extrapolate(pose: SimPose, elapsedMs: number, timeScale = 1): SimPose {
  const total = (Math.min(300, Math.max(0, elapsedMs)) / 1000) * timeScale;
  let { x, y, yaw } = pose;
  const steps = 4;
  const dt = total / steps;
  for (let k = 0; k < steps; k++) {
    x += pose.v_mps * Math.cos(yaw) * dt;
    y += pose.v_mps * Math.sin(yaw) * dt;
    yaw += (pose.v_mps / 0.26) * Math.tan(pose.steer_rad) * dt;
  }
  return { ...pose, x, y, yaw };
}

/**
 * 캔버스를 requestAnimationFrame 으로 그린다 (fps 상한). draw 에는 캔버스 크기(기기 픽셀)와 지금 시각이 온다.
 * 보드(RPi4) 브라우저가 느리면 fps 를 낮춘다 — 칸 세 개가 각자 그린다.
 */
export function useCanvasLoop(
  draw: (ctx: CanvasRenderingContext2D, W: number, H: number, now: number) => void,
  fps = 30,
): RefObject<HTMLCanvasElement | null> {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const drawRef = useRef(draw);
  useLayoutEffect(() => {
    drawRef.current = draw;
  });
  useEffect(() => {
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - last < 1000 / fps - 2) return;
      last = now;
      const el = ref.current;
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
      drawRef.current(ctx, W, H, now);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [fps]);
  return ref;
}

/** 새 위치가 올 때마다 받은 시각을 같이 적어 둔다 */
export function usePoseClock(pose: SimPose | null) {
  const ref = useRef<{ pose: SimPose | null; at: number }>({ pose: null, at: 0 });
  useLayoutEffect(() => {
    ref.current = { pose, at: performance.now() };
  }, [pose]);
  return ref;
}
