import {
  BLOCKS,
  MAP_H,
  MAP_W,
  PARKS,
  PLACES,
  RIVER,
  ROADS,
  ROUTE,
  ROUTE_LENGTH,
  routePosition,
  laneGuide,
  type LaneGuide,
} from './cityMap';
import styles from './combined.module.css';

/**
 * 회사 → 집 내비 (가상의 시내 지도, 오프라인). 실제 차량 내비처럼 차는 화면 아래쪽 가운데에 위를 향해 고정되고,
 * 지도가 진행 방향에 맞춰 움직이고 회전한다 (진행 방향 위). 글자는 늘 바로 서 있게 반대로 돌린다.
 * 위치 = 트립 거리 × ROUTE_SCALE 을 경로 위에 놓은 추정. 1초마다 오는 트립 사이는 속도로 보간해 부드럽게 흐른다.
 * 트립이 없으면 출발지에 두고 "위치 수신 대기".
 */
const VIEW_W = 1500;
const VIEW_H = 1250;
/** 화면에서 차가 놓이는 자리 (가로 가운데, 세로 70 %) — 앞을 더 넓게 본다 */
const ANCHOR_X = VIEW_W / 2;
const ANCHOR_Y = VIEW_H * 0.7;

/** 모퉁이에서 화면이 한 번에 90° 돌지 않게 앞뒤 점으로 진행 방향을 부드럽게 (deg, 동쪽 0, 시계 +) */
function smoothHeading(d: number) {
  const a = routePosition(d - 25);
  const b = routePosition(d + 25);
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
}

const fmtDistance = (m: number) =>
  m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 10) * 10} m`;

function labelsOf(points: [number, number][]) {
  const [[x0, y0], [x1, y1]] = [points[0]!, points[points.length - 1]!];
  const length = Math.hypot(x1 - x0, y1 - y0);
  const count = Math.max(1, Math.floor(length / 1100));
  return Array.from({ length: count }, (_, i) => {
    const t = (i + 0.5) / count;
    return { x: x0 + (x1 - x0) * t, y: y0 + (y1 - y0) * t, vertical: Math.abs(x1 - x0) < Math.abs(y1 - y0) };
  });
}

export function CityNav({
  tripM,
  mapDistance,
  mapSpeedMps,
  now,
}: {
  tripM: number | null;
  /** 보간한 지도 거리 (useSmoothMapDistance) */
  mapDistance: number;
  mapSpeedMps: number | null;
  now: Date;
}) {
  const known = tripM !== null;
  const smooth = mapDistance;
  const pos = routePosition(smooth);
  const guide = known ? laneGuide(pos) : null;
  const heading = smoothHeading(smooth);
  // 진행 방향이 화면 위(-90°)가 되도록 지도를 돌리고, 차 위치를 고정 자리로 옮긴다
  const spin = -heading - 90;
  const world = `translate(${ANCHOR_X} ${ANCHOR_Y}) rotate(${spin.toFixed(2)}) translate(${-pos.x} ${-pos.y})`;
  const upright = (x: number, y: number) => `rotate(${(-spin).toFixed(2)} ${x} ${y})`;
  const remaining = [`${pos.x},${pos.y}`, ...ROUTE.slice(pos.segment + 1).map((p) => `${p.x},${p.y}`)].join(
    ' ',
  );
  const traveled = [...ROUTE.slice(0, pos.segment + 1).map((p) => `${p.x},${p.y}`), `${pos.x},${pos.y}`].join(
    ' ',
  );
  const eta =
    known && !pos.arrived && mapSpeedMps && mapSpeedMps > 0.5
      ? new Date(now.getTime() + (pos.remaining / mapSpeedMps) * 1000)
      : null;

  return (
    <div className={styles.map}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid slice"
        role="img"
        aria-label="회사에서 집까지 지도"
      >
        <rect className={styles.mapOutside} x="0" y="0" width={VIEW_W} height={VIEW_H} />
        <g transform={world}>
          {/* 회전해도 가장자리가 드러나지 않게 바탕을 지도보다 넓게 */}
          <rect className={styles.mapGround} x={-MAP_W} y={-MAP_H} width={MAP_W * 3} height={MAP_H * 3} />
          <polygon className={styles.mapWater} points={RIVER.map(([x, y]) => `${x},${y}`).join(' ')} />
          {PARKS.map((p) => (
            <rect
              key={`${p.x}`}
              className={styles.mapPark}
              x={p.x}
              y={p.y}
              width={p.w}
              height={p.h}
              rx="40"
            />
          ))}
          {BLOCKS.map((b, i) => (
            <rect
              key={i}
              className={styles.mapBlock}
              x={b.x}
              y={b.y}
              width={Math.max(20, b.w)}
              height={Math.max(20, b.h)}
              rx="6"
            />
          ))}
          {ROADS.map((r, i) => (
            <polyline
              key={`c${i}`}
              className={r.kind === 'major' ? styles.mapCasingMajor : styles.mapCasingMinor}
              points={r.points.map((p) => p.join(',')).join(' ')}
            />
          ))}
          {ROADS.map((r, i) => (
            <polyline
              key={`r${i}`}
              className={r.kind === 'major' ? styles.mapRoadMajor : styles.mapRoadMinor}
              points={r.points.map((p) => p.join(',')).join(' ')}
            />
          ))}
          <polyline className={styles.mapTraveled} points={traveled} />
          <polyline className={styles.mapRoute} points={remaining} />
          {ROADS.filter((r) => r.name).flatMap((r) =>
            labelsOf(r.points).map((l, i) => (
              <text
                key={`${r.name}${i}`}
                className={styles.mapLabel}
                x={l.x}
                y={l.y}
                transform={upright(l.x, l.y)}
              >
                {r.name}
              </text>
            )),
          )}
          {PARKS.filter((p) => p.name).map((p) => (
            <text
              key={p.name}
              className={styles.mapParkLabel}
              x={p.x + p.w / 2}
              y={p.y + p.h / 2}
              transform={upright(p.x + p.w / 2, p.y + p.h / 2)}
            >
              {p.name}
            </text>
          ))}
          <g className={styles.mapOrigin} transform={`translate(${PLACES.origin.x} ${PLACES.origin.y})`}>
            <circle r="22" />
            <text y="-36" transform={upright(0, 0)}>
              {PLACES.origin.name}
            </text>
          </g>
          <g
            className={styles.mapDestination}
            transform={`translate(${PLACES.destination.x} ${PLACES.destination.y})`}
          >
            <g transform={upright(0, 0)}>
              <path d="M 0 0 C -26 -30 -30 -46 -30 -60 A 30 30 0 1 1 30 -60 C 30 -46 26 -30 0 0 Z" />
              <circle cy="-60" r="11" />
              <text y="-104">{PLACES.destination.name}</text>
            </g>
          </g>
        </g>
        {/* 차: 화면 고정, 늘 위를 향한다 */}
        <g transform={`translate(${ANCHOR_X} ${ANCHOR_Y})`} data-testid="nav-car">
          <circle r="60" className={styles.mapHalo} />
          <circle r="34" className={styles.mapDot} />
          <path d="M 0 -20 L -16 14 L 0 6 L 16 14 Z" className={styles.mapArrow} />
        </g>
      </svg>

      <div className={styles.navBanner} data-testid="nav-banner">
        <span className={styles.navTurn} aria-hidden="true">
          {pos.arrived ? '⚑' : pos.next ? (pos.next.turn === 'left' ? '↰' : '↱') : '↑'}
        </span>
        <div>
          <strong>
            {!known
              ? '—'
              : pos.arrived
                ? '도착'
                : pos.next
                  ? fmtDistance(pos.next.inM)
                  : fmtDistance(pos.remaining)}
          </strong>
          <span>
            {!known
              ? '위치 수신 대기'
              : pos.arrived
                ? `목적지 ${PLACES.destination.name}에 도착했습니다`
                : pos.next
                  ? `${pos.next.turn === 'left' ? '좌회전' : '우회전'} · ${pos.next.road}`
                  : `${PLACES.destination.name}까지 직진`}
          </span>
        </div>
      </div>

      {guide && <LaneGuidePanel guide={guide} />}

      <dl className={styles.navStats}>
        <div>
          <dt>목적지</dt>
          <dd>
            {PLACES.origin.name} → {PLACES.destination.name}
          </dd>
        </div>
        <div>
          <dt>남은 거리</dt>
          <dd>{known ? fmtDistance(pos.remaining) : '—'}</dd>
        </div>
        <div>
          <dt>도착 예정</dt>
          <dd>
            {pos.arrived && known
              ? '도착'
              : eta
                ? eta.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
                : '—'}
          </dd>
        </div>
        <div>
          <dt>전체</dt>
          <dd>{fmtDistance(ROUTE_LENGTH)}</dd>
        </div>
      </dl>
    </div>
  );
}

/** 차로 화살표 (위가 진행 방향) */
const ARROWS = {
  straight: 'M 12 3 L 5 11 H 10 V 22 H 14 V 11 H 19 Z',
  left: 'M 3 9 L 10 3 V 7 H 15 Q 18 7 18 10 V 22 H 14 V 11 H 10 V 15 Z',
  right: 'M 21 9 L 14 3 V 7 H 9 Q 6 7 6 10 V 22 H 10 V 11 H 14 V 15 Z',
} as const;

/** 내비 아래 가운데: 회전 전에 들어갈 차로 안내 (좌회전 = 1차로, 우회전 = 가장 바깥 차로) */
function LaneGuidePanel({ guide }: { guide: LaneGuide }) {
  const target = guide.turn === 'left' ? '1차로' : `${guide.lanes.length}차로`;
  return (
    <div className={styles.laneGuide} data-testid="lane-guide" aria-label="차로 안내">
      <div className={styles.laneRow}>
        {guide.lanes.map((lane, i) => (
          <span
            key={i}
            className={styles.lane}
            data-recommended={lane.recommended}
            data-testid={`lane-${i + 1}`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d={ARROWS[lane.arrow]} />
            </svg>
          </span>
        ))}
      </div>
      <small>
        {guide.turn === 'left' ? '좌회전' : '우회전'} 준비 · {target}
      </small>
    </div>
  );
}
