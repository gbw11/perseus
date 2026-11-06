import { useEffect, useState } from 'react';
import type { WorldObject } from './driveExtras';

const FRAME_MS = 50;
/** 마지막 값에서 속도로 이만큼까지만 앞서 그린다 (초) */
const MAX_AHEAD_S = 1.2;
/** 새 값이 오면 그리던 위치와의 차이를 이 시간에 걸쳐 없앤다 (초) */
const BLEND_S = 0.35;
/** 한 번에 이보다 많이 움직이면 (화면 밖으로 나갔다 다시 나타남 등) 이어 그리지 않고 바로 옮긴다 (RC m) */
const JUMP_M = 0.8;

interface Track {
  x: number;
  y: number;
  vx: number;
  vy: number;
  at: number;
  ox: number;
  oy: number;
}

function predict(t: Track, now: number) {
  const dt = Math.min(MAX_AHEAD_S, Math.max(0, (now - t.at) / 1000));
  const fade = Math.max(0, 1 - dt / BLEND_S);
  return { x: t.x + t.vx * dt + t.ox * fade, y: t.y + t.vy * dt + t.oy * fade };
}

/** 50 ms 마다 다시 그리게 하는 시계 */
function useFrameClock() {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    let frame = 0;
    let previous = 0;
    const tick = (t: number) => {
      if (t - previous >= FRAME_MS) {
        previous = t;
        setNow(t);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);
  return now;
}

/**
 * 주변 차 위치 보간: 수집기 값은 1초마다 오므로 그 사이를 물체별 속도(최근 두 값)로 이어 그린다.
 * 새 값이 오면 그리던 위치에서 새 궤적으로 BLEND_S 동안 섞어 넘어간다 (뚝 끊기지 않게). 차가 아닌 물체는 그대로 둔다
 */
export function useSmoothObjects(objects: WorldObject[]): WorldObject[] {
  const now = useFrameClock();
  // 새 값이 오면(배열이 바뀌면) 렌더 중에 궤적을 갱신한다 (이전 값에서 이어 가는 React 권장 방식)
  const [state, setState] = useState(() => ({ objects, tracks: nextTracks(new Map(), objects, now) }));
  let tracks = state.tracks;
  if (state.objects !== objects) {
    tracks = nextTracks(state.tracks, objects, now);
    setState({ objects, tracks });
  }
  return objects.map((o) => {
    const t = o.kind === 'car' ? tracks.get(o.id) : undefined;
    if (!t) return o;
    const p = predict(t, now);
    return { ...o, x_m: p.x, y_m: p.y };
  });
}

/** 새로 온 값으로 궤적 갱신: 직전 궤적과 새 위치로 속도를 구하고, 그리던 위치와의 차이는 섞어 없앤다 */
function nextTracks(tracks: Map<string, Track>, objects: WorldObject[], at: number) {
  const next = new Map<string, Track>();
  for (const o of objects) {
    if (o.kind !== 'car') continue;
    const prev = tracks.get(o.id);
    const shown = prev ? predict(prev, at) : null;
    const dt = prev ? (at - prev.at) / 1000 : 0;
    const jumped = !prev || Math.hypot(o.x_m - prev.x, o.y_m - prev.y) > JUMP_M || dt <= 0.05;
    next.set(o.id, {
      x: o.x_m,
      y: o.y_m,
      vx: jumped ? 0 : (o.x_m - prev.x) / dt,
      vy: jumped ? 0 : (o.y_m - prev.y) / dt,
      at,
      ox: jumped || !shown ? 0 : shown.x - o.x_m,
      oy: jumped || !shown ? 0 : shown.y - o.y_m,
    });
  }
  return next;
}

/**
 * 값이 계단식으로 올 때(예: 내 차로 1초마다 반 차로) 초당 rate 만큼씩 따라가 부드럽게 (넘치지 않는다).
 * resetKey 가 바뀌면(예: 회전으로 새 도로에 들어섬) 따라가지 않고 바로 그 값으로 맞춘다
 */
export function useChase(value: number | null, rate: number, resetKey?: string): number | null {
  const now = useFrameClock();
  const [s, setS] = useState<{ v: number; at: number; key?: string } | null>(null);
  if (value === null) {
    if (s !== null) setS(null);
    return null;
  }
  if (!s || s.key !== resetKey) {
    setS({ v: value, at: now, key: resetKey });
    return value;
  }
  // 이번 프레임에 이미 따라갔으면 그대로 (렌더 중 갱신이 한 프레임에 한 번만 일어나게)
  if (s.at === now) return s.v;
  const step = (rate * Math.max(0, now - s.at)) / 1000;
  const v = s.v + Math.max(-step, Math.min(step, value - s.v));
  setS({ v, at: now, key: resetKey });
  return v;
}
