import { useEffect, useState } from 'react';
import { obstacleAhead } from './course';
import { CourseNav } from './CourseNav';
import { CourseScene } from './CourseScene';
import {
  activeCommand,
  courseOf,
  maxSpeedOf,
  RATED_RUNTIME_S,
  remainingRuntimeS,
  tripOf,
  vmLineStatus,
  vehicleOf,
} from './driveExtras';
import { ROLE } from './model';
import { DEPART_MS, ParkedView, type ParkedCallout } from './ParkedView';
import { driveSummary } from './presentation';
import { useDisplayTheme } from './theme';
import { useScenarioSteps } from './scenarioSteps';
import { useDisplay } from './useDisplay';
import { useSmoothPose } from './useSmoothPose';
import styles from './combined.module.css';

/**
 * 모니터 2대(계기판·인포테인먼트)를 한 화면(1920×1080)으로 합친 차량 화면. 양산차 중앙 화면 배치:
 * 왼쪽 = 주행 화면 (속도·최대 속도·Jetson 시험 코스 3D·예상 주행 가능 시간·트립),
 * 오른쪽 = 내비게이션 (코스 탑뷰·시연 시나리오 4단계·VM 상태).
 * 값은 /api/v1/display/presentation 하나에서 온다. 신선하지 않거나 없는 값은 "—" 로 둔다.
 * 속도는 실제 RC 로봇과 맞대어 보도록 m/s 로 표시한다.
 */
const mps = (v: number) => v.toFixed(2);
/** 최대 속도 표지: 0.5 처럼 짧게 */
const mpsShort = (v: number) => String(Number(v.toFixed(2)));

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

/** 트립이 출발점을 벗어난 적이 있는지. 한 번 주행한 뒤에는 수신이 끊겨도 정차 화면으로 돌아가지 않는다 */
function useHasDriven(trip: { distance_m: number } | null) {
  const [driven, setDriven] = useState(false);
  if (!driven && trip !== null && trip.distance_m >= 0.05) setDriven(true);
  return driven;
}

/** 정차 → 출발: 출발한 뒤 DEPART_MS 동안은 정차 화면에서 바퀴가 구르며 나가는 모습을 보여 준다 */
function useDeparture(parked: boolean) {
  const [departing, setDeparting] = useState(false);
  const [wasParked, setWasParked] = useState(parked);
  // 정차 → 움직임 전환은 렌더 중에 알아챈다 (이전 값에서 이어 가는 React 권장 방식). 다시 서면 연출을 끝낸다
  if (wasParked !== parked) {
    setWasParked(parked);
    setDeparting(!parked);
  }
  useEffect(() => {
    if (!departing) return;
    const timer = window.setTimeout(() => setDeparting(false), DEPART_MS);
    return () => window.clearTimeout(timer);
  }, [departing]);
  return departing;
}

export function CombinedPage() {
  const theme = useDisplayTheme();
  const now = useClock();
  const { presentation: p, connected, error } = useDisplay();
  const { mode, modeLabel, preparing, active, selectedDrive } = driveSummary(p, connected);
  const vehicle = connected ? vehicleOf(p) : null;
  const live = vehicle?.source.status === 'fresh' ? vehicle : null;
  const drive = activeCommand(p, connected);
  const speed = live?.v_mps ?? null;
  const maxSpeed = connected ? maxSpeedOf(p) : null;
  const target = drive?.command?.v_mps ?? null;
  const trip = connected ? tripOf(p) : null;
  // 코스 위 위치·콘·계획 (제안 필드 state.course). 1초마다 오는 위치 사이를 이어 그린다
  const course = connected ? courseOf(p) : null;
  const pose = useSmoothPose(course?.pose ?? null);
  const obstacles = course?.obstacles ?? [];
  const plan = course?.plan ?? [];
  const stopped = mode === 'SAFE_STOP';
  const autopilot = mode === 'AUTO' && Boolean(active);
  const handover = !active && (preparing || mode === 'AUTO_READY') ? selectedDrive : null;
  // 차 앞 1 m 안 콘
  const cone = pose ? obstacleAhead(pose, obstacles) : null;
  // 출발 전 정차: 트립이 시작점이고 멈춰 있으면 차 외관 화면. 움직이기 시작하면 바퀴가 구르며 나가는 모습을
  // 잠깐 보여 준 뒤 2분할 주행 화면. 트립을 아직 못 받았고 주행한 적도 없으면(수집기가 트립을 안 보내는 보드 등)
  // 빈 주행 화면 대신 정차 화면에 둔다
  const driven = useHasDriven(trip);
  const parked = (speed ?? 0) < 0.02 && (trip !== null ? trip.distance_m < 0.05 : !driven);
  const departing = useDeparture(parked);
  const steps = useScenarioSteps({
    mode: connected ? mode : null,
    activeRole: active?.role ?? null,
    pose,
    speed,
    parked,
  });
  const vmStatus = (vm: NonNullable<typeof p>['state']['vms'][number]) => vmLineStatus(p, vm, connected);
  const callouts: ParkedCallout[] = (p?.state.vms ?? []).map((vm) => ({
    key: ROLE[vm.role].name,
    title: `${ROLE[vm.role].name} · ${ROLE[vm.role].title}`,
    // 주행 VM 은 엔진룸(본닛), 인포테인먼트는 차 가운데로 선이 이어진다
    anchor: vm.role === 'interaction' ? ('center' as const) : ('hood' as const),
    ...vmStatus(vm),
  }));
  const time = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });

  const parkedView = (
    <ParkedView
      runtimeS={trip ? remainingRuntimeS(trip.driving_s) : null}
      callouts={callouts}
      connected={connected}
      time={time}
      departing={departing}
    />
  );
  if (parked) {
    return (
      <main className={styles.screen} data-theme={theme} data-testid="combined" data-connected={connected}>
        <h1 className={styles.srOnly}>차량 화면</h1>
        {parkedView}
      </main>
    );
  }
  const lamps = [
    { key: 'estop', label: '비상정지', on: live?.flags?.estop },
    { key: 'watchdog', label: '명령 끊김', on: live?.flags?.watchdog },
    { key: 'obstacle', label: '장애물 인식', on: cone !== null },
  ];

  return (
    <main className={styles.screen} data-theme={theme} data-testid="combined" data-connected={connected}>
      <h1 className={styles.srOnly}>차량 화면</h1>

      {/* 왼쪽: 주행 화면 */}
      <section className={styles.drivePanel} aria-label="주행 화면" data-mode={mode}>
        <header className={styles.driveTop}>
          <div className={styles.speedBlock}>
            <strong data-testid="speed-value">{speed === null ? '—' : mps(speed)}</strong>
            <span>m/s</span>
          </div>
          <div className={styles.limitBlock}>
            <span className={styles.limitSign} aria-label="최대 속도">
              {maxSpeed === null ? '—' : mpsShort(maxSpeed)}
            </span>
            <small>최대 m/s</small>
            <span className={styles.targetSpeed} aria-label="목표 속도">
              목표 {target === null ? '—' : `${mps(target)} m/s`}
            </span>
          </div>
        </header>
        <div className={styles.statusLine}>
          <svg
            className={styles.wheel}
            data-on={autopilot}
            viewBox="0 0 24 24"
            aria-label={autopilot ? '자율주행 작동' : '자율주행 꺼짐'}
          >
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="2.4" />
            <path d="M3.5 11h6.1M14.4 11h6.1M12 14.4V21" />
          </svg>
          <span className={styles.modeText} data-tone={stopped ? 'danger' : preparing ? 'warning' : 'normal'}>
            {modeLabel}
          </span>
          <span className={styles.driverText} data-testid="active-vm">
            {active
              ? `${ROLE[active.role].name} 운전 중`
              : handover
                ? `${ROLE[handover.role].name} 승계 준비`
                : '구동 VM 없음'}
          </span>
        </div>
        <ul className={styles.lamps} aria-label="경고등">
          {lamps.map((lamp) => (
            <li
              key={lamp.key}
              data-key={lamp.key}
              data-on={lamp.on === true}
              data-unknown={!live}
              data-testid={`lamp-${lamp.key}`}
            >
              <i aria-hidden="true" />
              {lamp.label}
            </li>
          ))}
        </ul>

        <div className={styles.sceneWrap}>
          <CourseScene
            pose={pose}
            obstacles={obstacles}
            plan={plan}
            stopped={stopped}
            braking={stopped || speed === 0}
          />
          {cone && (
            <p className={styles.signalNote} data-state="obstacle" data-testid="obstacle-note">
              <span className={styles.coneIcon} aria-hidden="true" />콘 {cone.obstacle.id} 인식 ·{' '}
              {cone.ahead < 0.05 ? '옆으로 지나는 중' : '피해 가는 중'}
              <b>{Math.max(0, Math.round(cone.ahead * 100))} cm</b>
            </p>
          )}
        </div>

        <footer className={styles.driveStats}>
          <div>
            {/* 배터리 잔량 센서가 없어, 완충 기준 시간에서 이번 주행 시간을 뺀 추정값 */}
            <h2>예상 주행 가능 시간</h2>
            {trip ? (
              <>
                <strong data-testid="runtime-value">
                  {Math.floor(remainingRuntimeS(trip.driving_s) / 60)}
                  <small>분</small>
                </strong>
                <span>
                  완충 {RATED_RUNTIME_S / 60}분 기준 · {Math.floor(trip.driving_s / 60)}분 사용 · 추정
                </span>
              </>
            ) : (
              <>
                <strong data-testid="runtime-value">—</strong>
                <span>주행 시간 수신 대기</span>
              </>
            )}
          </div>
          <div>
            <h2>트립</h2>
            {trip ? (
              <>
                <strong>
                  {trip.distance_m.toFixed(1)}
                  <small>m</small>
                </strong>
                <span>
                  주행 {Math.floor(trip.driving_s / 60)}분 {trip.driving_s % 60}초
                </span>
              </>
            ) : (
              <>
                <strong>—</strong>
                <span>트립 수신 대기</span>
              </>
            )}
          </div>
        </footer>
      </section>

      {/* 오른쪽: 내비게이션 */}
      <section className={styles.navPanel} aria-label="내비게이션">
        <header className={styles.topBar}>
          <span>{now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}</span>
          <span className={connected ? styles.linkOk : styles.linkLost}>
            {connected ? '표시 연결됨' : '표시 연결 끊김'}
          </span>
        </header>
        {!connected && (
          <div role="alert" className={styles.alert}>
            {error ?? '연결 대기'} · 현재 값은 미확인입니다.
          </div>
        )}
        <CourseNav pose={pose} obstacles={obstacles} plan={plan} steps={steps} />
        <footer className={styles.dock} aria-label="VM 상태">
          {p?.state.vms.map((vm) => {
            const status = vmStatus(vm);
            return (
              <span
                key={vm.vm_id}
                className={styles.dockItem}
                data-tone={status.tone}
                data-testid={`dock-${ROLE[vm.role].name}`}
              >
                <b>{ROLE[vm.role].name}</b>
                {status.label}
              </span>
            );
          })}
        </footer>
      </section>

      {/* 출발 연출: 주행 화면을 아래에 미리 깔고, 위의 정차 화면이 차가 멀어지는 동안 서서히 흐려진다 */}
      {departing && (
        <div
          className={styles.departLayer}
          style={{ animationDuration: `${DEPART_MS}ms` }}
          data-testid="depart-layer"
        >
          {parkedView}
        </div>
      )}
    </main>
  );
}
