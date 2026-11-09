import { useState } from 'react';
import {
  APRON,
  BOUNDS,
  carCorners,
  courseLoop,
  courseOffset,
  LAP,
  PAINT_LINES,
  ROAD_HALF,
  SLOTS,
  START_LINE,
  TRACK_NAME,
  type Obstacle,
  type P2,
} from './course';
import type { ScenarioStep } from './scenarioSteps';
import type { Pose } from './useSmoothPose';
import styles from './combined.module.css';

/**
 * 오른쪽 내비: Jetson 시험 코스 전체 탑뷰 (북쪽 위 고정, 4.5 × 3 m). 차가 코스 위를 움직이고,
 * 지나온 길(회색)·주행 계획(파랑)·콘·목적지 P1 을 그린다. 위 배너 = 지금 단계, 아래 = 시나리오 4단계.
 * SVG 단위는 cm, y 를 뒤집어 코스 좌표(y 위)를 화면(y 아래)에 맞춘다.
 */
const CM = 100;
const sx = (x: number) => x * CM;
const sy = (y: number) => (BOUNDS.h - y) * CM;
const pts = (list: P2[]) => list.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(' ');
const loopPath = (list: P2[]) => `M ${pts(list)} Z`;

const ROAD = `${loopPath(courseLoop(-ROAD_HALF))} ${loopPath(courseLoop(ROAD_HALF))}`;
const OUTER = pts([...courseLoop(-ROAD_HALF + 0.01), courseLoop(-ROAD_HALF + 0.01)[0]!]);
const INNER = pts([...courseLoop(ROAD_HALF - 0.01), courseLoop(ROAD_HALF - 0.01)[0]!]);
const DIVIDER = pts([...courseLoop(0), courseLoop(0)[0]!]);
const P1 = SLOTS[0]!;

/** 지나온 길: 2 cm 넘게 움직일 때마다 한 점, 크게 뛰면(처음부터 다시) 새로 */
function useTrail(pose: Pose | null) {
  const [trail, setTrail] = useState<P2[]>([]);
  if (pose) {
    // 이전 값에서 이어 가는 상태: 렌더 중에 바뀐 것만 반영한다
    const last = trail[trail.length - 1];
    const step = last ? Math.hypot(pose.x - last[0], pose.y - last[1]) : Infinity;
    if (step > 0.8) setTrail([[pose.x, pose.y]]);
    else if (step >= 0.02) setTrail([...trail.slice(-800), [pose.x, pose.y]]);
  }
  return pose ? trail : [];
}

export function CourseNav({
  pose,
  obstacles,
  plan,
  steps,
}: {
  /** 내 차 후륜축 (보간한 값). 모르면 차를 그리지 않는다 */
  pose: Pose | null;
  obstacles: Obstacle[];
  plan: P2[];
  steps: ScenarioStep[];
}) {
  const trail = useTrail(pose);
  const active = steps.find((s) => s.state === 'active') ?? null;
  const finished = steps.length > 0 && steps.every((s) => s.state === 'done');
  const where = pose ? courseOffset(pose.x, pose.y) : null;
  const lane =
    where === null
      ? '—'
      : Math.abs(where.d) > ROAD_HALF
        ? '주차 마당'
        : where.d > 0
          ? '안쪽 차로'
          : '바깥 차로';
  const toP1 = pose ? Math.hypot((P1.x0 + P1.x1) / 2 - pose.x, (P1.y0 + P1.y1) / 2 - pose.y) : null;
  const corners = pose ? carCorners(pose) : null;
  const nose = pose
    ? ([
        [pose.x + Math.cos(pose.yaw) * 0.18, pose.y + Math.sin(pose.yaw) * 0.18],
        [pose.x + Math.cos(pose.yaw + 2.5) * 0.07, pose.y + Math.sin(pose.yaw + 2.5) * 0.07],
        [pose.x + Math.cos(pose.yaw - 2.5) * 0.07, pose.y + Math.sin(pose.yaw - 2.5) * 0.07],
      ] as P2[])
    : null;

  return (
    <div className={styles.map}>
      <div className={styles.courseMap}>
        <svg
          viewBox={`-10 -10 ${BOUNDS.w * CM + 20} ${BOUNDS.h * CM + 20}`}
          role="img"
          aria-label="Jetson 시험 코스 지도"
        >
          <rect
            className={styles.courseMapFloor}
            x="0"
            y="0"
            width={BOUNDS.w * CM}
            height={BOUNDS.h * CM}
            rx="6"
          />
          <path className={styles.courseMapRoad} d={ROAD} fillRule="evenodd" />
          <rect
            className={styles.courseMapRoad}
            x={sx(APRON.x0)}
            y={sy(APRON.y1)}
            width={(APRON.x1 - APRON.x0) * CM}
            height={(APRON.y1 - APRON.y0) * CM}
          />
          {SLOTS.map((s) => (
            <rect
              key={s.id}
              className={styles.courseMapRoad}
              x={sx(s.x0)}
              y={sy(s.y1)}
              width={(s.x1 - s.x0) * CM}
              height={(s.y1 - s.y0) * CM}
            />
          ))}
          <polyline className={styles.courseMapEdge} points={OUTER} />
          <polyline className={styles.courseMapEdge} points={INNER} />
          <polyline className={styles.courseMapDivider} points={DIVIDER} />
          {PAINT_LINES.map((l, i) => (
            <polyline key={i} className={styles.courseMapPaint} points={pts(l)} />
          ))}
          {/* 목적지 P1 */}
          <rect
            className={styles.courseMapTarget}
            data-done={finished}
            x={sx(P1.x0) + 3}
            y={sy(P1.y1) + 3}
            width={(P1.x1 - P1.x0) * CM - 6}
            height={(P1.y1 - P1.y0) * CM - 6}
            rx="3"
          />
          {SLOTS.map((s) => (
            <text key={s.id} className={styles.courseMapSlot} x={sx((s.x0 + s.x1) / 2)} y={sy(s.y1 - 0.14)}>
              {s.id}
            </text>
          ))}
          <polyline className={styles.courseMapStart} points={pts(START_LINE)} />
          <text className={styles.courseMapLabel} x={sx(START_LINE[0][0])} y={sy(0.02)}>
            출발
          </text>
          {trail.length > 1 && <polyline className={styles.courseMapTrail} points={pts(trail)} />}
          {plan.length > 1 && (
            <polyline className={styles.courseMapPlan} points={pts(plan)} data-testid="nav-plan" />
          )}
          {obstacles.map((o) => (
            <g key={o.id} data-testid={`nav-cone-${o.id}`}>
              <polygon
                className={styles.courseMapCone}
                points={pts([
                  [o.x, o.y + o.h / 2],
                  [o.x + o.w / 2, o.y - o.h / 2],
                  [o.x - o.w / 2, o.y - o.h / 2],
                ])}
              />
              <text className={styles.courseMapLabel} x={sx(o.x)} y={sy(o.y + o.h / 2 + 0.05)}>
                {o.id}
              </text>
            </g>
          ))}
          {pose && corners && nose && (
            <g data-testid="nav-car">
              <circle className={styles.mapHalo} cx={sx(pose.x)} cy={sy(pose.y)} r="24" />
              <polygon className={styles.courseMapCar} points={pts(corners)} />
              <polygon className={styles.courseMapNose} points={pts(nose)} />
            </g>
          )}
        </svg>
      </div>

      <div className={styles.navBanner} data-testid="nav-banner">
        <span className={styles.navStep} aria-hidden="true">
          {finished ? 'P' : active ? steps.indexOf(active) + 1 : '—'}
        </span>
        <div>
          <strong>{!pose ? '위치 수신 대기' : finished ? '주차 완료' : (active?.title ?? '—')}</strong>
          <span>
            {!pose ? '코스 위 차 위치를 아직 받지 못했습니다' : finished ? 'P1 에 섰습니다' : active?.detail}
          </span>
        </div>
      </div>

      <dl className={styles.navStats}>
        <div>
          <dt>코스</dt>
          <dd>{TRACK_NAME}</dd>
        </div>
        <div>
          <dt>한 바퀴</dt>
          <dd>{LAP.toFixed(1)} m</dd>
        </div>
        <div>
          <dt>지금</dt>
          <dd data-testid="nav-lane">{lane}</dd>
        </div>
        <div>
          <dt>P1 까지</dt>
          <dd>{toP1 === null ? '—' : finished ? '도착' : `${toP1.toFixed(1)} m`}</dd>
        </div>
      </dl>

      <ol className={styles.steps} aria-label="시연 시나리오" data-testid="scenario-steps">
        {steps.map((s, i) => (
          <li key={s.key} data-state={s.state} data-testid={`step-${s.key}`}>
            <b aria-hidden="true">{s.state === 'done' ? <CheckIcon /> : i + 1}</b>
            <div>
              <strong>{s.title}</strong>
              <small>{s.detail}</small>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** 보드 글꼴에 ✓ 가 없어 SVG 로 그린다 */
function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" className={styles.stepCheck}>
      <path d="M5 12.5 L10 17.5 L19 7" />
    </svg>
  );
}
