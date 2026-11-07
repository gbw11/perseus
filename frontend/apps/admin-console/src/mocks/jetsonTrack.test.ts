/** 시험 코스 metadata·노면·차량 기준점 회귀. 실차 인지·자동주차 수락 시험이 아니다. */
import { describe, expect, it } from 'vitest';
import { circuitOf, isCircuit } from '@xenova/ui';
import { initialSim, place, snapshotOf } from './sim';
import { boxIsPaved, buildTrack, decide, pavedAt, perceive, physics, steerLimit } from './simEngine';
import { TRACKS, type TrackSource } from './tracks';

const demo = TRACKS.find((t) => t.id === 'jetson-demo')!;

describe('Jetson 시험 코스', () => {
  it('기존10개를 보존하고 같은 외곽에서 반경을 비교하는6개 시험 코스를 제공한다', () => {
    expect(TRACKS.filter((t) => !t.layout)).toHaveLength(10);
    expect(TRACKS.filter((t) => t.layout).map((t) => t.id)).toEqual([
      'jetson-demo',
      'jetson-r45',
      'jetson-r55',
      'jetson-r65',
      'jetson-r75',
      'jetson-r80',
    ]);
    for (const source of TRACKS.filter((t) => t.layout)) {
      const layout = source.layout!;
      expect(layout.bounds).toEqual([0, 0, 4.5, 3]);
      expect(source.centerline[0]).toEqual([2.56, 0.5]);
      expect(layout.start_pose.slice(0, 2)).toEqual([2.81, 0.7]);
      expect(layout.start_pose[2]).toBeCloseTo(Math.PI, 12);
      expect(layout.obstacle_seeds.map(({ id, x, y }) => ({ id, x, y }))).toEqual([
        { id: 'A', x: 1.92, y: 0.7 },
        { id: 'B', x: 1.4, y: 0.3 },
      ]);
      expect(layout.lane_count).toBe(2);
      expect(source.lane_width_m! / layout.lane_count).toBe(0.4);
      expect(source.curb_m).toBe(0);
      const parking = layout.parking_slots.flatMap((slot) => slot.outline);
      expect([Math.min(...parking.map(([x]) => x)), Math.max(...parking.map(([x]) => x))]).toEqual([
        2.7, 3.5,
      ]);
      const parkingY = source.id === 'jetson-r75' || source.id === 'jetson-r80' ? [1.2, 1.8] : [1.1, 1.7];
      expect([Math.min(...parking.map(([, y]) => y)), Math.max(...parking.map(([, y]) => y))]).toEqual(
        parkingY,
      );
      for (const slot of layout.parking_slots) {
        expect(
          Math.max(...slot.outline.map(([x]) => x)) - Math.min(...slot.outline.map(([x]) => x)),
        ).toBeCloseTo(0.4, 12);
        expect(
          Math.max(...slot.outline.map(([, y]) => y)) - Math.min(...slot.outline.map(([, y]) => y)),
        ).toBeCloseTo(0.6, 12);
      }
      const points = [
        ...source.centerline,
        ...layout.paved_areas.flat(),
        ...layout.paint_lines.flat(),
        ...parking,
      ];
      expect(points.flat().every(Number.isFinite)).toBe(true);
      expect(source.centerline.length).toBeGreaterThan(3);
      for (let i = 0; i < source.centerline.length; i++) {
        const a = source.centerline[i]!;
        const b = source.centerline[(i + 1) % source.centerline.length]!;
        expect(Math.hypot(b[0] - a[0], b[1] - a[1])).toBeGreaterThan(1e-8);
      }
      const track = buildTrack(source);
      expect([...track.turn, ...track.tan.flat(), ...track.vProf].every(Number.isFinite)).toBe(true);
    }
  });

  it('후륜축 start pose·seed·폭·물리 축척을 snapshot에 보존한다', () => {
    const s = initialSim(0, 'jetson-demo');
    const snap = snapshotOf(s);
    expect([s.car.x, s.car.y, s.car.yaw]).toEqual(demo.layout!.start_pose);
    expect(snap.world?.car).toEqual({ length_m: 0.3, width_m: 0.22 });
    expect(snap.world?.time_scale).toBe(1);
    expect(snap.world?.obstacles.map(({ id, x, y, w }) => ({ id, x, y, w }))).toEqual(
      demo.layout!.obstacle_seeds.map(({ id, x, y, w }) => ({ id, x, y, w })),
    );
    const track = snap.world!.track;
    expect(isCircuit(track)).toBe(true);
    if (!isCircuit(track)) throw Error('Expected circuit');
    expect(track.layout).toEqual(demo.layout);
    expect(track.lane_width_m).toBe(0.8);
    expect(track.curb_m).toBe(0);
    expect(track.start_index).toBe(0);
    expect(track.centerline).toEqual(demo.centerline);
    const g = circuitOf(track);
    expect(g.scale).toBe(1);
    expect(g.deco).toEqual([]);
    expect(g.box).toEqual({ x0: 0, y0: 0, x1: 4.5, y1: 3 });
  });

  it('주차 지면에서는 단일고리 벽으로 차를 밀어내지 않고 안쪽 lane을 기본 목표로 쓴다', () => {
    const s = initialSim(0, 'jetson-demo');
    const T = s.T;
    expect(decide(T, perceive(T, { ...s.car }, []), null).target).toBe(-0.2);
    expect(steerLimit(T)).toBeCloseTo(Math.atan(0.2 / 0.55), 12);
    const car = { ...s.car, x: 3.1, y: 1.4, yaw: 0 };
    physics(T, car, 0, 0, 0.01, [], 1);
    expect([car.x, car.y, car.wallAt]).toEqual([3.1, 1.4, -9]);
    expect(place(s, 'cone', 3.3, 1.4).ok).toBe(true);
    expect(place(s, 'cone', 0, 0).code).toBe('OUT_OF_TRACK');
    expect(
      place(s, 'cone', s.car.x + 0.2 * Math.cos(s.car.yaw), s.car.y + 0.2 * Math.sin(s.car.yaw)).code,
    ).toBe('ON_CAR');
  });

  it('새 차량의 조향 한도와 후륜축 yaw 변화가 반경0.55m 가정을 따른다', () => {
    const s = initialSim(0, 'jetson-demo');
    const limit = steerLimit(s.T);
    physics(s.T, s.car, 0, 1, 0.2, [], 1);
    expect(s.car.steer).toBeCloseTo(limit, 12);
    const yaw = s.car.yaw;
    s.car.v = 0.1;
    physics(s.T, s.car, 0.1, 1, 0.01, [], 1.01);
    expect(s.car.yaw - yaw).toBeCloseTo((0.1 / 0.55) * 0.01, 12);
    expect(s.car.wallAt).toBe(-9);
  });

  it('네 꼭짓점이 지면이어도 가운데 hole을 가로지르는 box는 거절한다', () => {
    const rect = (x0: number, y0: number, x1: number, y1: number): [number, number][] => [
      [x0, y0],
      [x1, y0],
      [x1, y1],
      [x0, y1],
    ];
    const src: TrackSource = {
      ...demo,
      centerline: [
        [100, 100],
        [100, 101],
        [101, 101],
        [101, 100],
      ],
      layout: {
        ...demo.layout!,
        paved_areas: [
          rect(-1, -1, 1, -0.5),
          rect(-1, 0.5, 1, 1),
          rect(-1, -0.5, -0.5, 0.5),
          rect(0.5, -0.5, 1, 0.5),
        ],
      },
    };
    const T = buildTrack(src);
    for (const [x, y] of rect(-0.75, -0.75, 0.75, 0.75)) expect(pavedAt(T, x, y)).toBe(true);
    expect(boxIsPaved(T, 0, 0, 1.5)).toBe(false);
    expect(boxIsPaved(T, 0.75, 0, 0.4)).toBe(true);
  });
});
