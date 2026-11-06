import { useEffect, useRef, useState } from 'react';
import { ROUTE_SCALE } from './cityMap';

const FRAME_MS = 50;

/** 마지막 트립 값에서 속도만큼 앞으로 보간한 지도 거리 (최대 1.5초까지만 앞서 간다) */
export function useSmoothMapDistance(tripM: number | null, mapSpeedMps: number | null) {
  const last = useRef({ base: 0, at: 0, speed: 0 });
  const [distance, setDistance] = useState(() => (tripM ?? 0) * ROUTE_SCALE);
  useEffect(() => {
    last.current = { base: (tripM ?? 0) * ROUTE_SCALE, at: performance.now(), speed: mapSpeedMps ?? 0 };
  }, [tripM, mapSpeedMps]);
  useEffect(() => {
    let frame = 0;
    let previous = 0;
    const tick = (t: number) => {
      if (t - previous >= FRAME_MS) {
        previous = t;
        const { base, at, speed } = last.current;
        setDistance(base + speed * Math.min(1.5, Math.max(0, (t - at) / 1000)));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);
  return distance;
}
