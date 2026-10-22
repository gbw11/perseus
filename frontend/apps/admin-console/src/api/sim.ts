/**
 * 시뮬레이터 상태 구독 (시뮬레이터 구성, MQTT 계약 v1.4 §11.2).
 *   WS /ws/v1/sim (WS-06) → {"type":"sim.snapshot","available","pose","world"} 10 Hz + 트랙이 바뀔 때
 * 시뮬레이터가 없으면(실차 구성) available=false 이고 화면은 원래 네비게이션 칸을 그린다.
 * 백엔드가 아직 WS-06 을 열지 않았으면(S15P31E110-270 전) 연결이 실패해 계속 available=false 다.
 */
import { useEffect, useRef, useState } from 'react';
import type { SimPose, SimWorld } from '@xenova/ui';
import { request } from './client';
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

/** 트랙 바꾸기 (REST-36 → MQTT-17 track.select, 계약 v1.4). 장애물을 새로 놓고 출발 신호가 처음부터 */
export async function selectSimTrack(trackId: string): Promise<void> {
  if (MOCK) {
    (await import('../mocks/scenario')).simSelectTrack(trackId);
    return;
  }
  await request('/api/v1/sim/track', { method: 'POST', body: { track_id: trackId } });
}

/** 다시 출발 — 차를 출발선에 세우고 출발 신호를 처음부터 (REST-37 → MQTT-17 race.start, 계약 v1.4) */
export async function restartSimRace(): Promise<void> {
  if (MOCK) {
    (await import('../mocks/scenario')).simRestart();
    return;
  }
  await request('/api/v1/sim/race/start', { method: 'POST' });
}

/** MQTT-18 sim.command.result (계약 v1.4). 실제 연결에서는 WS-06 으로 따로 오므로 여기서는 null */
export interface SimCommandResult {
  request_id: string;
  op: string;
  ok: boolean;
  code: string | null;
  message: string | null;
}
export type ObstacleKind = 'cone' | 'box' | 'tires';
const scenario = () => import('../mocks/scenario');

/** 장애물 놓기 (REST-31). mock 은 배치 검사 결과를 바로 돌려준다 */
export async function placeSimObstacle(
  kind: ObstacleKind,
  x: number,
  y: number,
): Promise<SimCommandResult | null> {
  if (MOCK) return (await scenario()).simPlace(kind, x, y);
  await request('/api/v1/sim/obstacles', { method: 'POST', body: { kind, x, y } });
  return null;
}

/** 장애물 옮기기 (REST-32) */
export async function moveSimObstacle(id: string, x: number, y: number): Promise<SimCommandResult | null> {
  if (MOCK) return (await scenario()).simMove(id, x, y);
  await request(`/api/v1/sim/obstacles/${encodeURIComponent(id)}`, { method: 'PUT', body: { x, y } });
  return null;
}

/** 장애물 치우기 (REST-33). ids 를 하나씩 */
export async function removeSimObstacles(ids: string[]): Promise<void> {
  if (MOCK) {
    const s = await scenario();
    if (ids.length > 1) s.simRemove();
    else if (ids[0]) s.simRemove(ids[0]);
    return;
  }
  for (const id of ids)
    await request(`/api/v1/sim/obstacles/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** 무작위 3개 — mock 만 (실제 연결은 2단계에서 REST-31 세 번으로) */
export async function randomSimObstacles(): Promise<void> {
  if (!MOCK) throw new Error('무작위 놓기는 아직 mock 에서만 동작합니다');
  (await scenario()).simRandom();
}

/** E-stop 걸기·풀기 (REST-34) */
export async function setSimEstop(engaged: boolean): Promise<void> {
  if (MOCK) return (await scenario()).simEstop(engaged);
  await request('/api/v1/sim/estop', { method: 'POST', body: { engaged } });
}

/** 차 밀기 (REST-35) — 진행 방향 기준 옆으로 (+ 왼쪽, 모의 m, |dy| ≤ 0.5) */
export async function pushSimCar(dy_m: number): Promise<void> {
  if (MOCK) return (await scenario()).simPush(dy_m);
  await request('/api/v1/sim/push', { method: 'POST', body: { dy_m } });
}
