import {
  Button,
  Card,
  circuitOf,
  fmtLap,
  isCircuit,
  kmh,
  obstaclesAhead,
  realM,
  StatusBadge,
  TrackView,
  useToast,
  type TrackPointer,
} from '@xenova/ui';
import { useEffect, useRef, useState } from 'react';
import {
  moveSimObstacle,
  placeSimObstacle,
  pushSimCar,
  randomSimObstacles,
  removeSimObstacles,
  restartSimRace,
  selectSimTrack,
  setSimEstop,
  useSimState,
  type ObstacleKind,
  type SimCommandResult,
} from '../api/sim';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

const KINDS: { kind: ObstacleKind; name: string; w: number }[] = [
  { kind: 'cone', name: '콘', w: 0.18 },
  { kind: 'box', name: '박스', w: 0.3 },
  { kind: 'tires', name: '타이어', w: 0.26 },
];
const NAME: Record<string, string> = { cone: '콘', box: '박스', tires: '타이어' };
const CAR_COLOR = { autonomous: 'var(--x-app-autonomous)', manual: 'var(--x-app-manual)' } as const;
/** 차 밀기 한 번 (모의 m) — 시나리오 §4-1 "30 cm" */
const PUSH_M = 0.3;

/**
 * 관리자 트랙 조작 (시뮬레이터 구성, -267) — 지도를 눌러 장애물 놓기, 끌어서 옮기기, 두 번 눌러 치우기.
 * 이 차가 지나가지 못하는 자리는 시뮬레이터가 거부하고 이유를 준다 (MQTT 계약 v1.4 배치 검사, MQTT-18).
 * 트랙 바꾸기·다시 출발·E-stop·차 밀기 (REST-31~37 → MQTT-17).
 */
export function SimTrackControl() {
  const sim = useSimState();
  const { drive } = useDrive();
  const toast = useToast();
  const [kind, setKind] = useState<ObstacleKind>('cone');
  const [selected, setSelected] = useState<string | null>(null);
  const [reject, setReject] = useState<{ x: number; y: number } | null>(null);
  const drag = useRef<{ id: string; moved: boolean; lastAt: number; warnAt: number } | null>(null);
  useEffect(() => {
    if (!reject) return;
    const id = window.setTimeout(() => setReject(null), 1400);
    return () => window.clearTimeout(id);
  }, [reject]);

  const world = sim.world;
  if (!sim.available || !world || !isCircuit(world.track)) return null;
  const g = circuitOf(world.track);
  const pose = sim.pose;
  const race = pose?.race;
  const active = drive?.control?.active_app;
  const carColor = active === 'autonomous' || active === 'manual' ? CAR_COLOR[active] : 'var(--x-fault)';
  const estop = world.estop;
  const ahead = pose ? obstaclesAhead(g, pose, world.obstacles, 1e9) : [];
  const fail = (e: unknown) => toast.show(e instanceof Error ? e.message : String(e), 'fault');
  const shown = (r: SimCommandResult | null, at?: { x: number; y: number }) => {
    if (r && !r.ok) {
      toast.show(r.message ?? '시뮬레이터가 거부했어요', 'fault');
      if (at) setReject(at);
    }
    return !r || r.ok;
  };
  const hit = (x: number, y: number) =>
    world.obstacles.find((o) => Math.hypot(o.x - x, o.y - y) < o.w / 2 + 0.12);

  const onPointer = (e: TrackPointer) => {
    if (e.type === 'down') {
      const o = hit(e.x, e.y);
      drag.current = o ? { id: o.id, moved: false, lastAt: 0, warnAt: 0 } : null;
      setSelected(o?.id ?? null);
    } else if (e.type === 'move' && e.pressed && drag.current) {
      const d = drag.current;
      const now = performance.now();
      if (now - d.lastAt < 120) return; // 끄는 동안 너무 자주 보내지 않는다
      d.lastAt = now;
      d.moved = true;
      void moveSimObstacle(d.id, e.x, e.y)
        .then((r) => {
          // 못 지나가는 자리로는 안 끌려간다 — 알림은 1.5초에 한 번
          if (r && !r.ok && now - d.warnAt > 1500) {
            d.warnAt = now;
            shown(r);
          }
        })
        .catch(fail);
    } else if (e.type === 'up') {
      if (!drag.current)
        void placeSimObstacle(kind, e.x, e.y)
          .then((r) => shown(r, e))
          .catch(fail);
      drag.current = null;
      setSelected(null);
    } else if (e.type === 'double') {
      const o = hit(e.x, e.y);
      if (o) void removeSimObstacles([o.id]).catch(fail);
    }
  };

  return (
    <Card
      title={<span style={{ color: 'var(--x-app-simulator)' }}>트랙 조작 — 시뮬레이터</span>}
      actions={
        <span className={styles.simTracks}>
          {(world.tracks ?? []).map((tr) => (
            <Button
              key={tr.id}
              size="sm"
              variant={tr.id === g.track.id ? 'primary' : 'secondary'}
              onClick={() => void selectSimTrack(tr.id).catch(fail)}
            >
              {tr.name.split(' ')[0]} {(tr.real_m / 1000).toFixed(3)} km
            </Button>
          ))}
        </span>
      }
    >
      <div className={styles.simControl}>
        <div className={styles.simMap}>
          <TrackView
            pose={pose}
            world={world}
            trail={sim.trail}
            carColor={carColor}
            onPointer={onPointer}
            selectedId={selected}
            rejectAt={reject}
            label="트랙 조작 지도 — 눌러서 장애물 놓기, 끌어서 옮기기, 두 번 눌러 치우기"
          />
          <p className={styles.dim}>
            지도를 누르면 고른 장애물을 놓고, 장애물을 끌면 옮기고, 두 번 누르면 치운다. 차(폭{' '}
            {realM(g, 0.2).toFixed(1)} m)가 지나갈 틈({realM(g, 0.26).toFixed(1)} m)이 없거나 너무 촘촘하면
            놓이지 않고 이유가 뜬다.
          </p>
        </div>
        <div className={styles.simSide}>
          <div className={styles.simGroup}>
            <small>놓을 장애물</small>
            <span className={styles.simTracks}>
              {KINDS.map((k) => (
                <Button
                  key={k.kind}
                  size="sm"
                  variant={kind === k.kind ? 'primary' : 'secondary'}
                  onClick={() => setKind(k.kind)}
                >
                  {k.name} {realM(g, k.w).toFixed(1)} m
                </Button>
              ))}
            </span>
            <span className={styles.simTracks}>
              <Button size="sm" variant="secondary" onClick={() => void randomSimObstacles().catch(fail)}>
                무작위 3개
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={!world.obstacles.length}
                onClick={() => void removeSimObstacles(world.obstacles.map((o) => o.id)).catch(fail)}
              >
                모두 치우기
              </Button>
            </span>
          </div>

          <div className={styles.simGroup}>
            <small>출발</small>
            <div className={styles.rows}>
              <div>
                <span>상태</span>
                <span className={styles.mono}>
                  {!race
                    ? '—'
                    : race.phase === 'lights'
                      ? `출발 신호 ${race.lights_on}/5 — 모두 꺼지면 출발`
                      : `${race.lap + 1}랩 ${fmtLap(race.lap_ms)}`}
                </span>
              </div>
              <div>
                <span>지난 · 최고</span>
                <span className={styles.mono}>
                  {fmtLap(race?.last_lap_ms)} · {fmtLap(race?.best_lap_ms)}
                  {race?.jump_start ? ' · 부정 출발 +5초' : ''}
                </span>
              </div>
              <div>
                <span>속도</span>
                <span className={styles.mono}>
                  {pose ? `${Math.abs(kmh(g, pose.v_mps)).toFixed(0)} km/h` : '—'}
                </span>
              </div>
            </div>
            <Button size="sm" variant="secondary" onClick={() => void restartSimRace().catch(fail)}>
              다시 출발 (출발 신호)
            </Button>
          </div>

          <div className={styles.simGroup}>
            <small>차</small>
            <span className={styles.simTracks}>
              <Button size="sm" variant="secondary" onClick={() => void pushSimCar(PUSH_M).catch(fail)}>
                ◀ 왼쪽으로 밀기 {realM(g, PUSH_M).toFixed(0)} m
              </Button>
              <Button size="sm" variant="secondary" onClick={() => void pushSimCar(-PUSH_M).catch(fail)}>
                오른쪽으로 밀기 ▶
              </Button>
            </span>
            <Button
              size="sm"
              variant={estop ? 'secondary' : 'danger'}
              onClick={() => void setSimEstop(!estop).catch(fail)}
            >
              {estop ? 'E-stop 풀기' : 'E-stop 걸기'}
            </Button>
          </div>

          <div className={styles.simGroup}>
            <small>트랙 위 장애물 {world.obstacles.length}/8 (차 앞 거리)</small>
            {ahead.length ? (
              <ul className={styles.simList}>
                {ahead.map(({ o, ahead: a }) => (
                  <li key={o.id}>
                    <StatusBadge
                      health={a > 0 && a < 4 ? 'recovering' : 'ok'}
                      label={NAME[o.kind ?? 'cone']}
                      size="sm"
                    />
                    <span className={styles.mono}>
                      {a >= 0 ? `앞 ${Math.round(realM(g, a))} m` : '옆을 지나는 중'}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void removeSimObstacles([o.id]).catch(fail)}
                    >
                      치우기
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.dim}>없음 — 지도를 눌러 놓는다</p>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
