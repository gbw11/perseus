import { describe, expect, it } from 'vitest';
import { applySimFrame, type SimView } from '../api/sim';
import {
  advance,
  initialSim,
  place,
  restartRace,
  selectTrack,
  setEstop,
  snapshotOf,
  type SimState,
} from './sim';
import { atT } from './simEngine';
import { TRACKS } from './tracks';

/** 출발 신호가 다 꺼질 때까지 (최대 0.7 + 4 + 3 초) 기다린 뒤 시각 */
const GO_AT = 8000;

function run(
  s: SimState,
  from: number,
  ms: number,
  driver: 'autonomous' | 'manual' | null,
  keys: string[] = [],
) {
  let t = from;
  for (; t < from + ms; t += 100) s = advance(s, t, 0.1, driver, keys);
  return { s, t };
}

describe('시뮬레이터 mock (계약 v1.4 §11)', () => {
  it('레드불링 출발선 뒤에서 서 있고, 출발 신호 중이며, 장애물 셋이 놓여 있다', () => {
    const s = initialSim(0);
    const snap = snapshotOf(s);
    expect(snap.world?.track).toMatchObject({ id: 'spielberg', real_m: 4315, start_index: s.T.S0 });
    expect(snap.world?.obstacles).toHaveLength(3);
    expect(snap.world?.time_scale).toBe(3);
    expect(snap.pose?.v_mps).toBe(0);
    expect(snap.pose?.race).toMatchObject({ phase: 'lights', lap: 0, jump_start: false });
  });

  it('자율주행은 불이 꺼질 때까지 기다렸다가 출발하고 서서히 빨라진다', () => {
    const waiting = run(initialSim(0), 0, 3000, 'autonomous');
    expect(waiting.s.car.v).toBe(0);
    expect(snapshotOf(waiting.s).pose?.race?.lights_on).toBeGreaterThan(0);
    const going = run(waiting.s, waiting.t, GO_AT - waiting.t + 1000, 'autonomous');
    expect(going.s.race.phase).toBe('go');
    expect(going.s.car.v).toBeGreaterThan(0);
    expect(going.s.car.v).toBeLessThan(1); // 가속은 서서히 — 1초(시뮬레이터 3초) 만에 최고 속도가 아니다
  });

  it('수동으로 불이 꺼지기 전에 출발하면 부정 출발', () => {
    const { s } = run(initialSim(0), 0, 2500, 'manual', ['up']);
    expect(snapshotOf(s).pose?.race?.jump_start).toBe(true);
  });

  it('장애물을 피해 한 바퀴를 닿지 않고 돈다', () => {
    let { s, t } = run(initialSim(0), 0, GO_AT, 'autonomous');
    for (; s.race.lap < 1 && t < 120_000; t += 100) s = advance(s, t, 0.1, 'autonomous');
    expect(s.race.lap).toBe(1);
    expect(s.car.touchAt).toBe(-9);
    expect(s.car.wallAt).toBe(-9);
  });

  it.each(TRACKS.filter((t) => !t.layout).map((t) => [t.id]))(
    '%s: 처음 장애물을 두고 한 바퀴를 벽·장애물에 닿지 않고 돈다',
    (id) => {
      let { s, t } = run(initialSim(0, id), 0, GO_AT, 'autonomous');
      expect(s.obstacles.length).toBeGreaterThan(0);
      for (; s.race.lap < 1 && t < 200_000; t += 100) s = advance(s, t, 0.1, 'autonomous');
      expect(s.race.lap).toBe(1);
      expect(s.car.touchAt).toBe(-9);
      expect(s.car.wallAt).toBe(-9);
    },
  );

  it('차가 지나갈 틈이 없는 자리는 놓지 않고 이유를 준다 (MQTT-18)', () => {
    const s = initialSim(0);
    // 보통 구간 (양쪽 벽 0.70 m): 박스 셋으로 틈을 모두 0.26 m 아래로
    const i = s.T.WL.findIndex(
      (w, k) => Math.abs(w - 0.7) < 0.005 && Math.abs(s.T.WR[k]! - 0.7) < 0.005 && k > s.T.S0 + 200,
    );
    expect(i).toBeGreaterThan(0);
    expect(place(s, 'box', ...atT(s.T, i, 0.45)).ok).toBe(true);
    expect(place(s, 'box', ...atT(s.T, i, -0.45)).ok).toBe(true);
    const r = place(s, 'box', ...atT(s.T, i, 0));
    expect(r).toMatchObject({ ok: false, code: 'NO_GAP' });
    expect(r.message).toContain('틈');
  });

  it('트랙을 바꾸면 몬차로, revision 이 오르고 출발 신호가 처음부터', () => {
    const { s, t } = run(initialSim(0), 0, GO_AT, 'autonomous');
    const m = selectTrack(s, 'monza', t);
    expect(m.T.id).toBe('monza');
    expect(m.revision).toBeGreaterThan(s.revision);
    expect(m.race.phase).toBe('lights');
    expect(restartRace(s, t).race.phase).toBe('lights');
  });

  it('E-stop 은 바뀔 때만 revision 을 올린다', () => {
    const s = setEstop(initialSim(0), true);
    expect(snapshotOf(s).world?.estop).toBe(true);
    expect(setEstop(s, true).revision).toBe(s.revision);
  });

  it('WS-06 프레임: 위치가 없으면 available 이 아니고, 꼬리는 120개까지', () => {
    let v: SimView = { available: false, pose: null, world: null, trail: [] };
    v = applySimFrame(v, { available: true, pose: null, world: null });
    expect(v.available).toBe(false);
    const p = snapshotOf(initialSim(0)).pose;
    for (let i = 0; i < 130; i++) v = applySimFrame(v, { available: true, pose: p, world: null });
    expect(v.available).toBe(true);
    expect(v.trail).toHaveLength(120);
  });
});
