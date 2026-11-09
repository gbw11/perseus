import { useEffect, useRef, useState } from 'react';

export interface Pose {
  x: number;
  y: number;
  yaw: number;
}

const FRAME_MS = 50;
/** 수집기 조회 주기 (useDisplay 1초) — 앞 값에서 새 값까지 이만큼에 걸쳐 옮긴다 */
const BLEND_MS = 1000;
/** 이보다 멀리 뛰면 (처음부터 다시 등) 보간하지 않고 바로 옮긴다 */
const JUMP_M = 0.8;

const angleDiff = (a: number, b: number) => Math.atan2(Math.sin(b - a), Math.cos(b - a));
const same = (a: Pose | null, b: Pose | null) =>
  a === b || (a !== null && b !== null && a.x === b.x && a.y === b.y && a.yaw === b.yaw);

/**
 * 1초마다 오는 코스 위치 사이를 이어 그린다: 새 값이 오면 지금 그리고 있는 위치에서 새 위치까지 1초에 걸쳐 옮긴다
 * (한 주기 늦지만 끊기지 않는다). 위치가 없으면 null
 */
export function useSmoothPose(target: Pose | null): Pose | null {
  const latest = useRef<Pose | null>(target);
  const [pose, setPose] = useState<Pose | null>(target);

  useEffect(() => {
    latest.current = target;
  }, [target]);

  useEffect(() => {
    let frame = 0;
    let previous = 0;
    let shown: Pose | null = latest.current;
    let anim: { from: Pose; to: Pose; at: number } | null = null;
    const tick = (t: number) => {
      if (t - previous >= FRAME_MS) {
        previous = t;
        const to = latest.current;
        if (!to) {
          anim = null;
        } else if (!anim || !same(anim.to, to)) {
          // 새 값: 처음이거나 크게 뛰었으면 바로, 아니면 지금 자리에서 이어서
          const from = shown && Math.hypot(to.x - shown.x, to.y - shown.y) <= JUMP_M ? shown : to;
          anim = { from, to, at: t };
        }
        let next: Pose | null = null;
        if (anim) {
          const k = Math.min(1, Math.max(0, (t - anim.at) / BLEND_MS));
          next = {
            x: anim.from.x + (anim.to.x - anim.from.x) * k,
            y: anim.from.y + (anim.to.y - anim.from.y) * k,
            yaw: anim.from.yaw + angleDiff(anim.from.yaw, anim.to.yaw) * k,
          };
        }
        if (!same(shown, next)) {
          shown = next;
          setPose(next);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);
  // 위치가 끊기면 다음 프레임을 기다리지 않고 바로 지우고, 처음 오면 바로 그린다
  return target ? (pose ?? target) : null;
}
