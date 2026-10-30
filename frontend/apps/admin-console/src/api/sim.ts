/**
 * 시뮬레이터 상태 구독과 조작 (시뮬레이터 구성, MQTT 계약 v1.6 §12.2).
 *   WS /ws/v1/sim (WS-06) → {"type":"sim.snapshot","available","pose","world"} 10 Hz + 트랙이 바뀔 때
 *                         → {"type":"sim.command.result", ...MQTT-18 payload} 명령마다 한 번
 *   REST-31~37 → 202 {request_id} — 적용 여부는 WS-06 의 같은 request_id 결과로 본다 (3초 안에 없으면 응답 없음)
 * 시뮬레이터가 없으면(실차 구성) available=false 이고 화면은 원래 네비게이션 칸을 그린다.
 * 백엔드가 아직 WS-06 을 열지 않았으면(S15P31E110-270 전) 연결이 실패해 계속 available=false 다.
 */
import { useEffect, useRef, useState } from 'react';
import type { SimPose, SimWorld } from '@xenova/ui';
import { request, type RequestOptions } from './client';
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
    if (!onFrame) return;
    if (frame.type === 'sim.snapshot') onFrame(frame as Frame);
    else if (frame.type === 'sim.command.result') simResults.deliver(frame as unknown as SimCommandResult);
  });
}

/** MQTT-18 sim.command.result (계약 v1.6 §12.1). WS-06 은 봉투 없이 payload 필드를 type 옆에 펼쳐 보낸다 */
export interface SimCommandResult {
  request_id: string;
  op: string;
  ok: boolean;
  /** 거부 사유. 3초 안에 결과가 없으면 콘솔이 NO_RESPONSE 로 만든다 (계약 값 아님, 화면 전용) */
  code: string | null;
  message: string | null;
  gap_m?: number | null;
  need_m?: number | null;
}
export type ObstacleKind = 'cone' | 'box' | 'tires';
const scenario = () => import('../mocks/scenario');

/** 202 뒤 결과를 기다리는 시간 (계약 §12.2 "3초 안에 결과가 없으면 시뮬레이터 응답 없음") */
export const SIM_RESULT_TIMEOUT_MS = 3000;
export const SIM_NO_RESPONSE = '시뮬레이터 응답 없음';
/** REST 응답보다 먼저 온 결과를 들고 있는 최대 개수 (다른 콘솔이 보낸 명령 결과도 오므로 넘치면 오래된 것부터 버린다) */
const EARLY_MAX = 32;

/** 거부 사유 → 화면 문구. 시뮬레이터가 message 를 주면 그것을 먼저 띄운다 (계약 §12.1 "화면에 그대로 띄울 한 줄") */
export const SIM_REJECT_TEXT: Record<string, string> = {
  ON_CAR: '차와 겹치는 자리라 놓을 수 없어요',
  OUT_OF_TRACK: '벽 밖이라 놓을 수 없어요',
  INVALID: '시뮬레이터가 받을 수 없는 명령이에요',
  NO_GAP: '차가 지나갈 틈이 없어요',
  TOO_DENSE: '장애물이 너무 촘촘해 차가 지나가지 못해요',
  TOO_CLOSE: '차 바로 앞이라 피할 거리가 부족해요',
  NO_RESPONSE: SIM_NO_RESPONSE,
};

/** 적용 → 화면 문구 (명령 op 별) */
export const SIM_DONE_TEXT: Record<string, string> = {
  'obstacle.add': '장애물을 놓았어요',
  'obstacle.move': '장애물을 옮겼어요',
  'obstacle.remove': '장애물을 치웠어요',
  estop: 'E-stop 을 바꿨어요',
  push: '차를 밀었어요',
  'track.select': '트랙을 바꿨어요',
  'race.start': '출발선에 세웠어요',
};

/** 결과 한 줄 (적용·거부·응답 없음) */
export function describeSimResult(r: SimCommandResult): string {
  if (r.ok) return SIM_DONE_TEXT[r.op] ?? '적용했어요';
  if (r.code === 'NO_RESPONSE') return SIM_NO_RESPONSE;
  return (
    r.message ||
    (r.code && SIM_REJECT_TEXT[r.code]) ||
    `시뮬레이터가 거부했어요${r.code ? ` (${r.code})` : ''}`
  );
}

/**
 * request_id 로 WS-06 결과를 짝짓는다. deliver = 소켓에서 받은 결과, wait = REST 202 의 request_id 로 기다리기.
 * 결과가 REST 응답보다 먼저 올 수 있어 짝이 없는 결과는 timeoutMs 동안 들고 있는다.
 */
export function createSimResultWaiter(timeoutMs = SIM_RESULT_TIMEOUT_MS) {
  const waiting = new Map<string, (r: SimCommandResult) => void>();
  const early = new Map<string, SimCommandResult>();
  return {
    deliver(r: SimCommandResult) {
      if (typeof r?.request_id !== 'string') return;
      const resolve = waiting.get(r.request_id);
      if (resolve) {
        waiting.delete(r.request_id);
        resolve(r);
        return;
      }
      if (early.has(r.request_id)) return; // 소켓이 둘이면 같은 결과가 두 번 온다
      early.set(r.request_id, r);
      if (early.size > EARLY_MAX) early.delete(early.keys().next().value!);
      setTimeout(() => early.delete(r.request_id), timeoutMs);
    },
    wait(requestId: string, op: string): Promise<SimCommandResult> {
      const got = early.get(requestId);
      if (got) {
        early.delete(requestId);
        return Promise.resolve(got);
      }
      return new Promise((resolve) => {
        const timer = setTimeout(() => {
          waiting.delete(requestId);
          resolve({ request_id: requestId, op, ok: false, code: 'NO_RESPONSE', message: SIM_NO_RESPONSE });
        }, timeoutMs);
        waiting.set(requestId, (r) => {
          clearTimeout(timer);
          resolve(r);
        });
      });
    },
  };
}

/** WS-06 소켓(useSimState)이 결과를 넣는 곳. 트랙 조작 화면은 useSimState 안에서 그려지므로 소켓이 열려 있다 */
const simResults = createSimResultWaiter();

/** REST-31~37 을 보내고 같은 request_id 결과를 기다린다. 4xx·5xx 는 ApiError 로 던진다 */
async function command(op: string, path: string, options: RequestOptions): Promise<SimCommandResult> {
  const { request_id } = await request<{ request_id: string }>(path, options);
  return simResults.wait(request_id, op);
}

/**
 * 거부·응답 없음을 오류로 던진다 — 결과 대신 Promise<void> 만 받는 화면(대시보드 시뮬레이터 칸)도
 * 기존 catch 로 이유를 띄우게. result 에 원래 결과가 있다
 */
export class SimCommandError extends Error {
  constructor(readonly result: SimCommandResult) {
    super(describeSimResult(result));
    this.name = 'SimCommandError';
  }
}

function orThrow(r: SimCommandResult): void {
  if (!r.ok) throw new SimCommandError(r);
}

/** 트랙 바꾸기 (REST-36 → MQTT-17 track.select). 장애물을 치우고 차를 출발선에 세운다. 거부되면 SimCommandError */
export async function selectSimTrack(trackId: string): Promise<void> {
  if (MOCK) {
    (await scenario()).simSelectTrack(trackId);
    return;
  }
  orThrow(
    await command('track.select', '/api/v1/sim/track', { method: 'POST', body: { track_id: trackId } }),
  );
}

/** 다시 출발 — 차를 출발선에 세운다 (REST-37 → MQTT-17 race.start). 거부되면 SimCommandError */
export async function restartSimRace(): Promise<void> {
  if (MOCK) {
    (await scenario()).simRestart();
    return;
  }
  orThrow(await command('race.start', '/api/v1/sim/race/start', { method: 'POST' }));
}

/** 장애물 놓기 (REST-31). mock 은 배치 검사 결과를 바로 돌려준다 */
export async function placeSimObstacle(
  kind: ObstacleKind,
  x: number,
  y: number,
): Promise<SimCommandResult | null> {
  if (MOCK) return (await scenario()).simPlace(kind, x, y);
  return command('obstacle.add', '/api/v1/sim/obstacles', { method: 'POST', body: { kind, x, y } });
}

/** 장애물 옮기기 (REST-32) */
export async function moveSimObstacle(id: string, x: number, y: number): Promise<SimCommandResult | null> {
  if (MOCK) return (await scenario()).simMove(id, x, y);
  return command('obstacle.move', `/api/v1/sim/obstacles/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: { x, y },
  });
}

/**
 * 장애물 치우기 (REST-33). ids 를 하나씩 보내고 결과는 함께 기다린다.
 * 거부·응답 없음이 있으면 첫 번째 것을, 모두 적용이면 마지막 결과를 돌려준다.
 */
export async function removeSimObstacles(ids: string[]): Promise<SimCommandResult | null> {
  if (MOCK) {
    const s = await scenario();
    if (ids.length > 1) s.simRemove();
    else if (ids[0]) s.simRemove(ids[0]);
    return null;
  }
  const pending: Promise<SimCommandResult>[] = [];
  for (const id of ids) {
    const { request_id } = await request<{ request_id: string }>(
      `/api/v1/sim/obstacles/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    );
    pending.push(simResults.wait(request_id, 'obstacle.remove'));
  }
  const results = await Promise.all(pending);
  return results.find((r) => !r.ok) ?? results[results.length - 1] ?? null;
}

/** 무작위 3개 — mock 만 (실제 연결은 2단계에서 REST-31 세 번으로) */
export async function randomSimObstacles(): Promise<void> {
  if (!MOCK) throw new Error('무작위 놓기는 아직 mock 에서만 동작합니다');
  (await scenario()).simRandom();
}

/** E-stop 걸기·풀기 (REST-34) */
export async function setSimEstop(engaged: boolean): Promise<SimCommandResult | null> {
  if (MOCK) {
    (await scenario()).simEstop(engaged);
    return null;
  }
  return command('estop', '/api/v1/sim/estop', { method: 'POST', body: { engaged } });
}

/** 차 밀기 (REST-35) — 진행 방향 기준 옆으로 (+ 왼쪽, 모의 m, |dy| ≤ 0.5) */
export async function pushSimCar(dy_m: number): Promise<SimCommandResult | null> {
  if (MOCK) {
    (await scenario()).simPush(dy_m);
    return null;
  }
  return command('push', '/api/v1/sim/push', { method: 'POST', body: { dy_m } });
}
