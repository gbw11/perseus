import { useState } from 'react';
import { courseOffset, insideSlot, LAP, OBSTACLE_SEEDS, SLOTS, VEHICLE } from './course';
import type { Pose } from './useSmoothPose';

/**
 * 시연 시나리오 4단계 (한 바퀴): ① 장애물 회피 ② 자동 → 수동 전환 ③ VM1 장애 → VM2 승계 ④ P1 주차.
 * 단계는 수집기 값(주행 모드·운전 VM·코스 위치)으로 판단하고, 한 번 끝난 단계는 다시 출발 전까지 끝난 채로 둔다.
 * 화면을 중간에 새로 열어도 맞도록 지금 값만으로 알 수 있는 것은 지금 값으로 본다
 * (콘을 지난 위치면 회피 끝, VM2 가 자율 운전 중이면 승계까지 끝).
 */
export type StepKey = 'avoid' | 'manual' | 'failover' | 'park';
export type StepState = 'done' | 'active' | 'pending';

export interface ScenarioStep {
  key: StepKey;
  title: string;
  state: StepState;
  /** 지금 상황 한 줄 */
  detail: string;
}

export interface StepInput {
  mode: string | null;
  /** 운전 중인 VM 역할 (없으면 null) */
  activeRole: 'primary_drive' | 'backup_drive' | 'interaction' | null;
  pose: Pose | null;
  speed: number | null;
  /** 출발 전 정차 화면 (다시 출발하면 단계를 처음부터) */
  parked: boolean;
}

interface Latch {
  avoid: boolean;
  manualSeen: boolean;
  manual: boolean;
  failSeen: boolean;
  failover: boolean;
  park: boolean;
}

const EMPTY: Latch = {
  avoid: false,
  manualSeen: false,
  manual: false,
  failSeen: false,
  failover: false,
  park: false,
};

/** 차체가 마지막 콘을 다 지난 진행 거리: 출발선부터 가장 먼 콘 + 콘 반폭 + 후륜축 뒤 돌출 */
const PAST_CONES_U =
  Math.max(...OBSTACLE_SEEDS.map((o) => courseOffset(o.x, o.y).u + o.w / 2)) + VEHICLE.rearOverhang;
/** 출발 자리(출발선 뒤)는 한 바퀴 끝(u ≈ LAP)으로 잡히므로 그 앞까지만 */
const passedCones = (p: Pose) => {
  const { u } = courseOffset(p.x, p.y);
  return u > PAST_CONES_U && u < LAP - 0.5;
};

function next(l: Latch, i: StepInput): Latch {
  if (i.parked) return EMPTY;
  const manualSeen = l.manualSeen || i.mode === 'MANUAL';
  const failSeen = l.failSeen || i.mode === 'SAFE_STOP' || i.mode === 'AUTO_READY';
  // VM2 가 자율 운전 중이면 VM1 은 이미 장애로 넘겨준 것이다
  const failover = l.failover || (i.mode === 'AUTO' && i.activeRole === 'backup_drive');
  const park = l.park || (i.pose !== null && (i.speed ?? 1) < 0.02 && insideSlot(i.pose, SLOTS[0]!));
  return {
    avoid: l.avoid || (i.pose !== null && passedCones(i.pose)) || manualSeen || failSeen || failover,
    manualSeen,
    manual: l.manual || (manualSeen && i.mode !== 'MANUAL') || failSeen || failover,
    failSeen: failSeen || failover,
    failover,
    park,
  };
}

const same = (a: Latch, b: Latch) => (Object.keys(a) as (keyof Latch)[]).every((k) => a[k] === b[k]);

export function useScenarioSteps(input: StepInput): ScenarioStep[] {
  const [latch, setLatch] = useState<Latch>(EMPTY);
  const now = next(latch, input);
  // 이전 값에서 이어 가는 상태: 렌더 중에 바뀐 것만 반영한다
  if (!same(now, latch)) setLatch(now);

  const done: Record<StepKey, boolean> = {
    avoid: now.avoid,
    manual: now.manual,
    failover: now.failover,
    park: now.park,
  };
  const order: StepKey[] = ['avoid', 'manual', 'failover', 'park'];
  const active = order.find((k) => !done[k]) ?? null;
  const { mode, activeRole } = input;
  const detail: Record<StepKey, string> = {
    avoid: now.avoid ? '콘 A·B 통과' : '콘 A·B · 차로를 넘나들어 지나가기',
    manual: now.manual
      ? '수동 운전 후 자동 복귀'
      : mode === 'MANUAL'
        ? '수동 운전 중'
        : '자율주행 중 · 수동 전환 대기',
    failover: now.failover
      ? 'VM2 가 이어서 운전'
      : mode === 'SAFE_STOP'
        ? 'VM1 응답 없음 · 안전 정지'
        : mode === 'AUTO_READY'
          ? 'VM2 승계 준비'
          : activeRole === 'primary_drive'
            ? 'VM1 운전 중 · 장애 대비 VM2 대기'
            : 'VM2 대기',
    park: now.park ? 'P1 주차 완료' : '마당을 지나 P1 으로',
  };
  const titles: Record<StepKey, string> = {
    avoid: '장애물 회피',
    manual: '자동 → 수동 전환',
    failover: 'VM1 장애 → VM2 승계',
    park: 'P1 주차',
  };
  return order.map((key) => ({
    key,
    title: titles[key],
    state: done[key] ? 'done' : key === active ? 'active' : 'pending',
    detail: detail[key],
  }));
}
