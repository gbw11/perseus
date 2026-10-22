/**
 * 시뮬레이터 상태 구독 (시뮬레이터 구성, MQTT 계약 v1.3 §11.2).
 *   WS /ws/v1/sim (WS-06) → {"type":"sim.snapshot","available","pose","world"} 10 Hz + 트랙이 바뀔 때
 * 시뮬레이터가 없으면(실차 구성) available=false 이고 화면은 원래 네비게이션 칸을 그린다.
 * 백엔드가 아직 WS-06 을 열지 않았으면(S15P31E110-270 전) 연결이 실패해 계속 available=false 다.
 */
import { useEffect, useRef, useState } from 'react';
import type { SimPose, SimWorld } from '@xenova/ui';
import { useAuthedSocket } from './drive';

const MOCK = import.meta.env.VITE_MOCK === 'true';
/** 마지막 위치가 이보다 오래되면 시뮬레이터가 없는 것으로 본다 (계약 §11.2 available 과 같은 1초) */
export const SIM_STALE_MS = 1000;
const TRAIL_POINTS = 120;

export interface SimView {
  available: boolean;
  pose: SimPose | null;
  world: SimWorld | null;
  /** 최근 위치 (오래된 것부터, 최대 120개 ≈ 12초) */
  trail: [number, number][];
}

const EMPTY: SimView = { available: false, pose: null, world: null, trail: [] };

interface Frame {
  available?: boolean;
  pose?: SimPose | null;
  world?: SimWorld | null;
}

/** 받은 프레임을 화면 상태로. 위치가 오면 꼬리에 붙인다 */
export function applySimFrame(view: SimView, frame: Frame): SimView {
  const pose = frame.pose ?? null;
  const last = view.trail[view.trail.length - 1];
  // 시뮬레이터를 다시 켜거나 차를 옮겨 위치가 튀면 지나온 길을 새로 그린다
  const jumped = !!pose && !!last && Math.hypot(pose.x - last[0], pose.y - last[1]) > 0.6;
  const trail: [number, number][] = pose
    ? [...(jumped ? [] : view.trail), [pose.x, pose.y] as [number, number]].slice(-TRAIL_POINTS)
    : view.trail;
  return { available: !!frame.available && !!pose, pose, world: frame.world ?? view.world, trail };
}

export function useSimState(): SimView {
  const [view, setView] = useState<SimView>(EMPTY);
  const lastAt = useRef(0);
  const onFrame = (frame: Frame) => {
    lastAt.current = performance.now();
    setView((v) => applySimFrame(v, frame));
  };

  // 프레임이 끊기면 1초 뒤 available=false (시뮬레이터를 끄면 세 번째 칸이 네비게이션으로 돌아간다)
  useEffect(() => {
    const id = window.setInterval(() => {
      if (performance.now() - lastAt.current > SIM_STALE_MS)
        setView((v) => (v.available ? { ...v, available: false } : v));
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  useMockSim(MOCK ? onFrame : null);
  useLiveSim(MOCK ? null : onFrame);
  return view;
}

function useMockSim(onFrame: ((frame: Frame) => void) | null) {
  const ref = useRef(onFrame);
  useEffect(() => {
    ref.current = onFrame;
  });
  const on = onFrame !== null;
  useEffect(() => {
    if (!on) return;
    let off: (() => void) | undefined;
    let stopped = false;
    void import('../mocks/scenario').then((s) => {
      if (!stopped) off = s.subscribeSim((snapshot) => ref.current?.(snapshot));
    });
    return () => {
      stopped = true;
      off?.();
    };
  }, [on]);
}

function useLiveSim(onFrame: ((frame: Frame) => void) | null) {
  // 훅은 조건 없이 부른다. mock 이면 소켓 주소를 비워 연결하지 않는다
  useAuthedSocket(onFrame ? '/ws/v1/sim' : '', (frame) => {
    if (onFrame && frame.type === 'sim.snapshot') onFrame(frame as Frame);
  });
}
