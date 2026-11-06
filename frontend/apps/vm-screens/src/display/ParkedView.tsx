import { useEffect, useState } from 'react';
import {
  CAR,
  carLamps,
  carSolids,
  carWheels,
  RIM_R,
  WHEEL_L,
  WHEEL_R,
  type C3,
  type CarFace,
} from './carModel';
import { PLACES, ROUTE, ROUTE_LENGTH } from './cityMap';
import { RATED_RUNTIME_S } from './driveExtras';
import styles from './combined.module.css';

/**
 * 부팅 직후·출발 전 정차 화면 (양산차 정차 화면 배치): 가운데 큰 차 외관, 위에 VM 카드 3장이 차에 선으로 이어진다
 * (주행 VM1·VM2 = 엔진룸 본닛, 인포테인먼트 VM3 = 차 가운데). 세 VM 이 모두 준비되면 "출발 준비 완료".
 * 차가 출발하면(departing) 바퀴가 구르며 앞으로 나가고, 잠시 뒤 2분할 주행 화면으로 바뀐다.
 */
export interface ParkedCallout {
  key: string;
  title: string;
  label: string;
  tone: 'drive' | 'ok' | 'warning' | 'danger' | 'muted';
  /** 선이 차에 닿는 곳: 주행 VM = 본닛(엔진룸), 인포테인먼트 = 차 가운데 */
  anchor: 'hood' | 'center';
}

/** 연결되어 준비된 VM (주행 중 또는 대기·준비 완료, 인포테인먼트 실행 중) */
export const calloutReady = (c: ParkedCallout) => c.tone === 'drive' || c.tone === 'ok';

export function ParkedView({
  runtimeS,
  callouts,
  connected,
  time,
  departing = false,
}: {
  /** 예상 주행 가능 시간 (초, 잔량 센서가 없어 완충 기준 시간 − 주행 시간으로 추정). 모르면 null */
  runtimeS: number | null;
  callouts: ParkedCallout[];
  connected: boolean;
  time: string;
  /** 차가 막 출발함: 바퀴가 구르고 차가 앞으로 나간다 */
  departing?: boolean;
}) {
  const readyCount = callouts.filter(calloutReady).length;
  const allReady = connected && callouts.length > 0 && readyCount === callouts.length;
  return (
    <section
      className={styles.parked}
      data-departing={departing}
      aria-label="정차 화면"
      data-testid="parked-view"
    >
      <header className={styles.parkedTop}>
        <div className={styles.parkedBattery}>
          <span className={styles.batteryGlyph} aria-hidden="true">
            <i style={{ width: `${runtimeS === null ? 0 : (runtimeS / RATED_RUNTIME_S) * 100}%` }} />
          </span>
          <b data-testid="parked-runtime">{runtimeS === null ? '—' : `약 ${Math.floor(runtimeS / 60)}분`}</b>
          <small>{runtimeS === null ? '주행 시간 수신 대기' : '예상 주행 가능 시간 · 추정'}</small>
        </div>
        <div className={styles.parkedStatus} data-testid="parked-status">
          <strong>
            {departing
              ? '출발합니다'
              : allReady
                ? `VM ${callouts.length}대 모두 연결됨`
                : `구동 준비 중 · ${readyCount}/${callouts.length} 연결`}
          </strong>
          <span>
            {time} · {connected ? '표시 연결됨' : '표시 연결 끊김'}
          </span>
        </div>
        <MiniRoute />
      </header>

      <div className={styles.parkedStage}>
        <CarStage callouts={callouts} departing={departing} />
      </div>

      <footer className={styles.parkedCards}>
        <article className={styles.parkedCard} aria-label="인포테인먼트">
          <span className={styles.albumArt} aria-hidden="true">
            ♪
          </span>
          <div>
            <small>VM3 · 인포테인먼트</small>
            <b>재생 정보 수신 대기</b>
          </div>
          <span className={styles.mediaControls} aria-hidden="true">
            ⏮ ▶ ⏭
          </span>
        </article>
        <article className={styles.parkedCard} aria-label="경로">
          <span className={styles.routeIcon} aria-hidden="true">
            ⌂
          </span>
          <div>
            <small>
              {PLACES.origin.name} → {PLACES.destination.name}
            </small>
            <b>{(ROUTE_LENGTH / 1000).toFixed(1)} km · 출발하면 안내를 시작합니다</b>
          </div>
        </article>
      </footer>

      {/* 모든 VM 연결 확인: 화면을 덮고(뒤는 살짝 흐리게) 출발 준비 완료. 차가 움직이면 걷힌다 */}
      {allReady && (
        <div className={styles.readyOverlay} data-leaving={departing} data-testid="ready-overlay">
          <div className={styles.readyBadge} aria-hidden="true">
            ✓
          </div>
          <strong>출발 준비 완료</strong>
          <span>
            {callouts.map((c) => c.key).join(' · ')} 연결됨 · {PLACES.origin.name} → {PLACES.destination.name}
          </span>
        </div>
      )}
    </section>
  );
}

/** 오른쪽 위 작은 경로 지도 (회사 → 집) */
function MiniRoute() {
  const points = ROUTE.map((p) => `${p.x},${p.y}`).join(' ');
  return (
    <svg className={styles.miniRoute} viewBox="200 100 2800 1800" aria-label="경로 미리보기" role="img">
      <rect x="200" y="100" width="2800" height="1800" className={styles.miniGround} />
      <polyline points={points} className={styles.miniLine} />
      <circle cx={PLACES.origin.x} cy={PLACES.origin.y} r="60" className={styles.miniOrigin} />
      <circle cx={PLACES.destination.x} cy={PLACES.destination.y} r="70" className={styles.miniDestination} />
    </svg>
  );
}

/** 정차 화면 그림 크기 (SVG 단위): 위 카드 줄 + 아래 차 */
const STAGE = { w: 1200, h: 640 };
const CARD = { w: 330, h: 150, top: 12 };
/** 정차 화면 카메라: 처음에는 차를 앞 왼쪽 위에서 본다 (앞이 화면 왼쪽 아래, yaw 125°) */
const EXT = {
  yaw: (125 * Math.PI) / 180,
  pitch: (16 * Math.PI) / 180,
  dist: 22,
  focal: 3700,
  cx: 610,
  cy: 405,
};
/** 출발 연출 (ms): 시계 방향으로 돌아 12시(화면 안쪽)를 향한 뒤 앞으로 나가며 멀어지고, 끝에 흐려진다 */
const TURN_MS = 1400;
export const DEPART_MS = 3200;

/** 차 자세: yaw = 카메라에서 본 차 방향 (0 = 앞이 화면 안쪽 12시), forward = 앞으로 나간 거리 (장면 단위) */
interface Pose {
  yaw: number;
  forward: number;
}

/** 차 자세에 맞춘 투영기 (차 좌표 [u, l, y] → 정차 화면 좌표) */
function projector({ yaw, forward }: Pose) {
  /** 차 좌표 방향 → 카메라 좌표 [가로, 위, 깊이] (돌리기만) */
  const rotate = ([u, l, y]: C3): C3 => {
    const X = u * Math.cos(yaw) - l * Math.sin(yaw);
    const Z = u * Math.sin(yaw) + l * Math.cos(yaw);
    // 위에서 내려다본다: 먼 곳은 화면 위로, 높은 곳은 카메라에 가깝게
    return [
      X,
      y * Math.cos(EXT.pitch) + Z * Math.sin(EXT.pitch),
      Z * Math.cos(EXT.pitch) - y * Math.sin(EXT.pitch),
    ];
  };
  /** 차 좌표 점 → 카메라 좌표 (차 가운데를 카메라 앞 dist 에, 앞으로 나간 만큼 옮긴다. 카메라는 원점) */
  const camera = ([u, l, y]: C3): C3 => {
    const [X, up, D] = rotate([u, l - CAR.len / 2 + forward, y - 0.7]);
    return [X, up, D + EXT.dist];
  };
  const project = (p: C3) => {
    const [X, up, D] = camera(p);
    return { x: EXT.cx + (EXT.focal * X) / D, y: EXT.cy - (EXT.focal * up) / D, depth: D };
  };
  /** 법선이 카메라 쪽을 보는가 (점에서 카메라로 향하는 방향과 법선의 내적) */
  const facing = (n: C3, at: C3) => {
    const nc = rotate(n);
    const pc = camera(at);
    return -(nc[0] * pc[0] + nc[1] * pc[1] + nc[2] * pc[2]) > 0;
  };
  const points = (corners: C3[]) =>
    corners
      .map((c) => {
        const p = project(c);
        return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
      })
      .join(' ');
  /** 보이는 면만 먼 것부터 */
  const solid = (faces: CarFace[]) =>
    faces
      .map((f) => {
        const mid = f.corners
          .reduce<C3>((s, c) => [s[0] + c[0], s[1] + c[1], s[2] + c[2]], [0, 0, 0])
          .map((v) => v / f.corners.length) as C3;
        return { ...f, facing: facing(f.normal, mid), depth: project(mid).depth };
      })
      .filter((f) => f.facing)
      .sort((a, b) => b.depth - a.depth);
  return { project, points, solid };
}

/** 0 → 1 부드럽게 */
const smooth = (k: number) => {
  const c = Math.max(0, Math.min(1, k));
  return c * c * (3 - 2 * c);
};

/** 멀어지는 차가 빨려 들어가는 소실점 (정차 화면 SVG 좌표, 차 위 지평선 쪽) */
const VANISH = { x: 610, y: 190 };

/**
 * 출발 후 시간(ms) → 차 자세·바퀴 각도·크기·투명도. 돌면서 살짝 나가고, 12시를 보면 점점 빨리 멀어진다.
 * 정차 화면 카메라는 망원이라 3D 로 앞으로 보내면 화면 위로 빠져나가므로, 멀어지는 건 소실점으로 줄어드는 것으로 그린다
 */
export function departPose(ms: number) {
  const turn = smooth(ms / TURN_MS);
  const yaw = EXT.yaw * (1 - turn);
  const run = Math.max(0, ms - TURN_MS * 0.7) / 1000;
  const forward = 1.2 * turn;
  return {
    pose: { yaw, forward },
    // 앞으로 구른다 (나간 거리만큼, 바퀴 반지름 0.36 m = 3.6 단위)
    wheel: -(forward + 30 * run * run) / 3.6,
    // 멀어짐: 소실점 쪽으로 점점 빨리 작아진다
    scale: 1 / (1 + 3 * run * run),
    opacity: 1 - smooth((ms - (DEPART_MS - 600)) / 600),
  };
}

/** 바퀴 살 5개 (보이는 옆면 평면, 각도 angle 만큼 돈 상태) */
function wheelSpokes(side: 'left' | 'right', angle: number): C3[][] {
  const u = (side === 'left' ? -1 : 1) * (CAR.w / 2 + 0.02);
  return WHEEL_L.flatMap((l0) =>
    Array.from({ length: 5 }, (_, i) => {
      const a = angle + (i * 2 * Math.PI) / 5;
      const [c, s] = [Math.cos(a), Math.sin(a)];
      const w = 0.03;
      const at = (r: number, off: number): C3 => [u, l0 + r * c - off * s, WHEEL_R + r * s + off * c];
      return [at(0.05, -w), at(RIM_R - 0.02, -w), at(RIM_R - 0.02, w), at(0.05, w)];
    }),
  );
}

/** 출발 후 지난 시간 (ms, 출발 전 0) — 출발 중에만 프레임마다 다시 그린다 */
function useDepartClock(departing: boolean) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!departing) {
      setElapsed(0);
      return;
    }
    const start = performance.now();
    let frame = requestAnimationFrame(function tick(t) {
      setElapsed(t - start);
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [departing]);
  return elapsed;
}

/**
 * 차 외관 + VM 카드. 차는 주행 화면의 내 차와 같은 3D 모델·같은 색 (앞 3/4 시점).
 * 카드에서 차로 선이 이어진다: 주행 VM 은 본닛(엔진룸), 인포테인먼트는 차 가운데. 준비된 VM 의 선은 색이 들어가고 흐른다
 */
function CarStage({ callouts, departing }: { callouts: ParkedCallout[]; departing: boolean }) {
  const elapsed = useDepartClock(departing);
  // 출발: 시계 방향으로 돌아 12시(화면 안쪽)를 향하고, 바퀴가 구르며 앞으로 나가 멀어진다
  const { pose, wheel, scale, opacity } = departPose(departing ? elapsed : 0);
  const ext = projector(pose);
  const { body, glass } = carSolids();
  const bodyFaces = ext.solid(body);
  const glassFaces = ext.solid(glass);
  const side = bodyFaces.find((f) => f.tag === 'left' || f.tag === 'right')?.tag as
    'left' | 'right' | undefined;
  const end = bodyFaces.find((f) => f.tag === 'front' || f.tag === 'rear')?.tag as
    'front' | 'rear' | undefined;
  const shadow = ext.points([
    [-1.25, -0.4, 0],
    [1.25, -0.4, 0],
    [1.25, CAR.len + 0.4, 0],
    [-1.25, CAR.len + 0.4, 0],
  ]);

  // 선이 닿는 곳: 본닛 위 두 점(주행 VM 두 대), 지붕 가운데(인포테인먼트)
  const hoodPoints = [-0.4, 0.4].map((u) => ext.project([u, CAR.len - 0.75, 0.87])).sort((a, b) => a.x - b.x);
  const centerPoint = ext.project([0, 1.3, 1.5]);
  const gap = (STAGE.w - 3 * CARD.w) / 4;
  let hoodUsed = 0;
  const cards = callouts.map((c, i) => {
    const x = gap + i * (CARD.w + gap);
    const anchor =
      c.anchor === 'hood' ? hoodPoints[Math.min(hoodUsed++, hoodPoints.length - 1)]! : centerPoint;
    return { ...c, x, anchor, ready: calloutReady(c) };
  });

  return (
    <svg
      className={styles.carExterior}
      viewBox={`0 0 ${STAGE.w} ${STAGE.h}`}
      role="img"
      aria-label="차량 외관과 VM 연결 상태"
    >
      <g
        className={styles.selfCar}
        opacity={opacity}
        transform={
          scale < 1
            ? `translate(${VANISH.x} ${VANISH.y}) scale(${scale.toFixed(4)}) translate(${-VANISH.x} ${-VANISH.y})`
            : undefined
        }
        data-testid="parked-car"
        data-yaw={Math.round((pose.yaw * 180) / Math.PI)}
        data-scale={scale.toFixed(3)}
      >
        <polygon className={styles.shadow} points={shadow} />
        {bodyFaces.map((f, i) => (
          <polygon key={`b${i}`} className={styles[f.key]} points={ext.points(f.corners)} />
        ))}
        {side && (
          <g data-testid="wheels" data-angle={wheel.toFixed(2)}>
            {carWheels(side).map((w, i) => (
              <g key={`w${i}`}>
                <polygon className={styles.tire} points={ext.points(w.tire)} />
                <polygon className={styles.rim} points={ext.points(w.rim)} />
              </g>
            ))}
            {wheelSpokes(side, wheel).map((s, i) => (
              <polygon key={`s${i}`} className={styles.spoke} points={ext.points(s)} />
            ))}
          </g>
        )}
        {glassFaces.map((f, i) => (
          <polygon key={`g${i}`} className={styles[`cabin_${f.key}`]} points={ext.points(f.corners)} />
        ))}
        {end &&
          carLamps(end).map((l, i) => (
            <polygon
              key={`l${i}`}
              className={end === 'front' ? styles.headLight : styles.tailLight}
              points={ext.points(l)}
            />
          ))}
      </g>

      {/* 카드 → 차 연결선 (카드 아래 가운데에서 차의 닿는 곳까지). 출발하면 카드와 함께 사라진다 */}
      <g className={styles.vmCards} data-departing={departing}>
        {cards.map((c) => {
          const sx = c.x + CARD.w / 2;
          const sy = CARD.top + CARD.h;
          const { x: ax, y: ay } = c.anchor;
          return (
            <g key={`link-${c.key}`} className={styles.vmLink} data-ready={c.ready} data-tone={c.tone}>
              <path d={`M ${sx} ${sy} C ${sx} ${sy + 110}, ${ax} ${ay - 140}, ${ax} ${ay}`} />
              <circle className={styles.vmAnchorHalo} cx={ax} cy={ay} r={16} />
              <circle className={styles.vmAnchor} cx={ax} cy={ay} r={7} />
            </g>
          );
        })}

        {/* VM 카드: 연결·준비 상태를 크게 */}
        {cards.map((c) => (
          <foreignObject key={`card-${c.key}`} x={c.x} y={CARD.top} width={CARD.w} height={CARD.h}>
            <div
              className={styles.vmCard}
              data-ready={c.ready}
              data-tone={c.tone}
              data-testid={`callout-${c.key}`}
            >
              <small>{c.title}</small>
              <b>{c.ready ? '연결됨 · 준비 완료' : '연결 대기'}</b>
              <span>{c.label}</span>
            </div>
          </foreignObject>
        ))}
      </g>
    </svg>
  );
}
