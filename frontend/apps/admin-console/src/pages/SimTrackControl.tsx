import {
  Button,
  Card,
  circuitOf,
  fmtLap,
  isCircuit,
  kmh,
  obstaclesAhead,
  obstacleTopImage,
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
  describeSimResult,
  SIM_DONE_TEXT,
  SimCommandError,
  useSimState,
  type ObstacleKind,
  type SimCommandResult,
} from '../api/sim';
import { useDrive } from '../layout/DriveContext';
import { SimTrackSelect } from './SimTrackSelect';
import { simFlow } from './simFlow';
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
 * 관리자 트랙 조작 (시뮬레이터 구성, -267) — 지도 밖 장애물 그림을 끌어다 지도에 놓기(또는 지도 누르기),
 * 지도 위 장애물을 끌어서 옮기기, 두 번 눌러 치우기.
 * 놓을 수 없는 자리(차와 겹침·벽 밖)는 시뮬레이터가 거부하고 이유를 준다 (MQTT 계약 v1.6 §12.1 배치 검사, MQTT-18).
 * 트랙 바꾸기·다시 출발·E-stop·차 밀기 (REST-31~37 → MQTT-17). 결과는 WS-06 의 같은 request_id 로 받아
 * 적용은 짧게, 거부는 이유를, 3초 안에 없으면 "시뮬레이터 응답 없음" 을 띄운다.
 */
export function SimTrackControl() {
  const sim = useSimState();
  const { drive } = useDrive();
  const toast = useToast();
  const [kind, setKind] = useState<ObstacleKind>('cone');
  const [selected, setSelected] = useState<string | null>(null);
  const [reject, setReject] = useState<{ x: number; y: number } | null>(null);
  /** 지도 밖 그림을 끌어 오는 중인 종류와, 놓일 자리 미리 보기 */
  const [dragKind, setDragKind] = useState<ObstacleKind | null>(null);
  const [preview, setPreview] = useState<{ x: number; y: number; w: number } | null>(null);
  const drag = useRef<{ id: string; moved: boolean; lastAt: number; warnAt: number } | null>(null);
  /** 지도 상자 — 끌 때 그림 크기를 지도 축척에 맞추려고 */
  const mapBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!reject) return;
    const id = window.setTimeout(() => setReject(null), 1400);
    return () => window.clearTimeout(id);
  }, [reject]);

  const world = sim.world;
  // 시뮬레이터가 없거나 끊기면 카드를 지우지 않고 "없음" 으로 두고 조작을 막는다 (-262 완료 조건 2)
  const off = !sim.available;
  if (!world || !isCircuit(world.track)) return <SimUnavailable />;
  const g = circuitOf(world.track);
  const pose = off ? null : sim.pose;
  const race = pose?.race;
  const active = drive?.control?.active_app;
  const carColor = active === 'autonomous' || active === 'manual' ? CAR_COLOR[active] : 'var(--x-fault)';
  const estop = world.estop;
  const ahead = pose ? obstaclesAhead(g, pose, world.obstacles, 1e9) : [];
  const firstAhead = ahead.find(({ ahead: a }) => a >= 0)?.ahead ?? null;
  const flow = simFlow(drive, pose?.v_mps ?? null, firstAhead == null ? null : realM(g, firstAhead));
  /**
   * 명령 결과 (MQTT-18, WS-06 으로 같은 request_id). 거부·응답 없음은 이유를, 적용은 짧게 띄운다.
   * null = 결과를 따로 돌려주지 않는 mock 명령
   */
  const shown = (r: SimCommandResult | null, at?: { x: number; y: number }) => {
    if (!r) return true;
    if (!r.ok) {
      const noResponse = r.code === 'NO_RESPONSE';
      toast.show(describeSimResult(r), noResponse ? 'recovering' : 'fault');
      if (at && !noResponse) setReject(at);
    } else toast.show(describeSimResult(r), 'ok', 1500);
    return r.ok;
  };
  const report = (r: SimCommandResult | null) => void shown(r);
  /** 결과를 돌려주지 않는 명령(트랙 바꾸기·다시 출발)의 적용 알림 */
  const done = (op: string) => () => toast.show(SIM_DONE_TEXT[op] ?? '적용했어요', 'ok', 1500);
  const fail = (e: unknown) =>
    e instanceof SimCommandError
      ? void shown(e.result)
      : toast.show(e instanceof Error ? e.message : String(e), 'fault');
  const hit = (x: number, y: number) =>
    world.obstacles.find((o) => Math.hypot(o.x - x, o.y - y) < o.w / 2 + 0.12);

  const onPointer = (e: TrackPointer) => {
    if (off) return;
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
      if (o) void removeSimObstacles([o.id]).then(report).catch(fail);
    }
  };

  // 지도 밖 그림을 끌어다 놓기 (HTML 끌어 놓기)
  const onDragTrack = (e: { type: 'over' | 'leave' | 'drop'; x: number; y: number }) => {
    const k = KINDS.find((q) => q.kind === dragKind);
    if (!k || off) return;
    if (e.type === 'over') setPreview({ x: e.x, y: e.y, w: k.w });
    else if (e.type === 'leave') setPreview(null);
    else {
      setPreview(null);
      setDragKind(null);
      void placeSimObstacle(k.kind, e.x, e.y)
        .then((r) => shown(r, e))
        .catch(fail);
    }
  };

  return (
    <Card
      title={<span style={{ color: 'var(--x-app-simulator)' }}>트랙 조작 — 시뮬레이터</span>}
      actions={
        <SimTrackSelect
          disabled={off}
          tracks={world.tracks ?? []}
          current={g.track.id}
          onPick={(id) => void selectSimTrack(id).then(done('track.select')).catch(fail)}
        />
      }
    >
      {off && <SimOffNotice />}
      <div className={styles.simControl} aria-disabled={off}>
        <div className={styles.simMap}>
          {/* 지도 밖 장애물 그림 — 끌어다 지도에 놓는다. 누르면 "지도 누르기"로 놓을 종류로 고른다 */}
          <div className={styles.palette} aria-label="장애물 — 끌어다 트랙에 놓기">
            <span className={styles.paletteHint}>끌어다 트랙에 놓기</span>
            {KINDS.map((k) => (
              <button
                key={k.kind}
                type="button"
                draggable={!off}
                disabled={off}
                className={[styles.paletteItem, kind === k.kind ? styles.paletteOn : ''].join(' ')}
                onClick={() => setKind(k.kind)}
                onDragStart={(e) => {
                  e.dataTransfer.setData('text/plain', k.kind);
                  e.dataTransfer.effectAllowed = 'copy';
                  // 끄는 동안 커서에 붙는 그림 = 지도 축척에 맞춘 실제 크기의 위에서 본 모양
                  const w = mapBox.current?.getBoundingClientRect().width ?? 0;
                  if (w > 0) {
                    const img = obstacleTopImage(k.kind, (k.w * w) / (g.box.x1 - g.box.x0));
                    img.style.position = 'fixed';
                    img.style.left = '-1000px';
                    document.body.append(img); // 그림이 문서에 있어야 끌기 그림으로 쓰인다
                    e.dataTransfer.setDragImage(img, img.offsetWidth / 2, img.offsetHeight / 2);
                    window.setTimeout(() => img.remove(), 0);
                  }
                  setDragKind(k.kind);
                  setKind(k.kind);
                }}
                onDragEnd={() => {
                  setDragKind(null);
                  setPreview(null);
                }}
                title={`${k.name} — 끌어다 트랙에 놓기`}
              >
                <ObstacleIcon kind={k.kind} />
                <span>{k.name}</span>
                <small>폭 {realM(g, k.w).toFixed(1)} m</small>
              </button>
            ))}
          </div>
          <div ref={mapBox}>
            <TrackView
              pose={pose}
              world={world}
              trail={sim.trail}
              carColor={carColor}
              onPointer={onPointer}
              selectedId={selected}
              rejectAt={reject}
              onDragTrack={onDragTrack}
              preview={preview}
              label="트랙 조작 지도 — 장애물 그림을 끌어다 놓기, 끌어서 옮기기, 두 번 눌러 치우기"
            />
          </div>
          <p className={styles.dim}>
            위 그림을 끌어다 트랙에 놓는다 (그림을 고른 뒤 지도를 눌러도 된다). 지도 위 장애물은 끌면 옮겨지고
            두 번 누르면 치워진다. 차(폭 {realM(g, 0.2).toFixed(1)} m)가 지나갈 틈({realM(g, 0.26).toFixed(1)}{' '}
            m)이 없거나 너무 촘촘하면 놓이지 않고 이유가 뜬다.
          </p>
        </div>
        <div className={styles.simSide}>
          <div className={styles.simGroup}>
            <small>주행 흐름</small>
            <div className={styles.rows} aria-label="주행 흐름">
              <div>
                <span>모드</span>
                {flow.mode ? (
                  <StatusBadge health={flow.mode.health} label={flow.mode.label} size="sm" />
                ) : (
                  <span>—</span>
                )}
              </div>
              <div>
                <span>모는 앱</span>
                <span>{flow.app}</span>
              </div>
              <div>
                <span>차</span>
                <StatusBadge
                  health={off ? 'stopped' : flow.motion.health}
                  label={off ? '시뮬레이터 없음' : flow.motion.label}
                  size="sm"
                />
              </div>
            </div>
          </div>

          <div className={styles.simGroup}>
            <small>장애물 한꺼번에</small>
            <span className={styles.simTracks}>
              <Button
                size="sm"
                variant="secondary"
                disabled={off}
                onClick={() => void randomSimObstacles().catch(fail)}
              >
                무작위 3개
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={off || !world.obstacles.length}
                onClick={() =>
                  void removeSimObstacles(world.obstacles.map((o) => o.id))
                    .then(report)
                    .catch(fail)
                }
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
            <Button
              size="sm"
              variant="secondary"
              disabled={off}
              onClick={() => void restartSimRace().then(done('race.start')).catch(fail)}
            >
              다시 출발 (출발 신호)
            </Button>
          </div>

          <div className={styles.simGroup}>
            <small>차</small>
            <span className={styles.simTracks}>
              <Button
                size="sm"
                variant="secondary"
                disabled={off}
                onClick={() => void pushSimCar(PUSH_M).then(report).catch(fail)}
              >
                ◀ 왼쪽으로 밀기 {realM(g, PUSH_M).toFixed(0)} m
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={off}
                onClick={() => void pushSimCar(-PUSH_M).then(report).catch(fail)}
              >
                오른쪽으로 밀기 ▶
              </Button>
            </span>
            <Button
              size="sm"
              variant={estop ? 'secondary' : 'danger'}
              disabled={off}
              onClick={() => void setSimEstop(!estop).then(report).catch(fail)}
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
                      disabled={off}
                      onClick={() => void removeSimObstacles([o.id]).then(report).catch(fail)}
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

/** 시뮬레이터 위치가 1초 넘게 안 오면 (SIM_STALE_MS). 조작은 막고 마지막 트랙은 그대로 둔다 */
function SimOffNotice() {
  return (
    <p className={styles.simOff} role="status">
      시뮬레이터 없음 — 1초 넘게 위치를 받지 못했다. 시뮬레이터를 켜면 다시 이어지고 조작 버튼이 켜진다.
    </p>
  );
}

/** 한 번도 트랙을 받지 못했을 때 (시뮬레이터를 안 켰거나 실차 구성) */
function SimUnavailable() {
  return (
    <Card title={<span style={{ color: 'var(--x-app-simulator)' }}>트랙 조작 — 시뮬레이터</span>}>
      <SimOffNotice />
      <span className={styles.simTracks}>
        <Button size="sm" variant="secondary" disabled>
          다시 출발 (출발 신호)
        </Button>
        <Button size="sm" variant="secondary" disabled>
          차 밀기
        </Button>
        <Button size="sm" variant="danger" disabled>
          E-stop 걸기
        </Button>
      </span>
    </Card>
  );
}

/** 장애물 그림 (지도 밖 고르기 판) — 콘·박스·타이어 */
function ObstacleIcon({ kind }: { kind: ObstacleKind }) {
  if (kind === 'cone')
    return (
      <svg viewBox="0 0 48 48" width="44" height="44" aria-hidden="true">
        <rect x="8" y="38" width="32" height="5" rx="1.5" fill="#d97706" />
        <path d="M24 5 L37 38 H11 Z" fill="#f59e0b" />
        <path d="M18.6 19 H29.4 L31.4 24 H16.6 Z" fill="#f8fafc" />
        <path d="M15.4 28 H32.6 L34.2 32 H13.8 Z" fill="#f8fafc" />
      </svg>
    );
  if (kind === 'box')
    return (
      <svg viewBox="0 0 48 48" width="44" height="44" aria-hidden="true">
        <path d="M8 16 L24 8 L40 16 L24 24 Z" fill="#c9ced6" />
        <path d="M8 16 V34 L24 42 V24 Z" fill="#8b919d" />
        <path d="M40 16 V34 L24 42 V24 Z" fill="#6b7280" />
        <path d="M8 24 L24 32 L40 24" stroke="#dee2ed" strokeWidth="2" fill="none" />
      </svg>
    );
  return (
    <svg viewBox="0 0 48 48" width="44" height="44" aria-hidden="true">
      {[34, 25, 16].map((y) => (
        <g key={y}>
          <ellipse cx="24" cy={y} rx="17" ry="6.5" fill="#0b0d10" stroke="#3a3f46" strokeWidth="1.5" />
          <ellipse cx="24" cy={y - 1} rx="7" ry="2.4" fill="#1f242b" />
        </g>
      ))}
    </svg>
  );
}
