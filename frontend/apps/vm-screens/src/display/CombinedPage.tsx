import { useEffect, useMemo, useState } from 'react';
import { CityNav } from './CityNav';
import { ROUTE_SCALE, routePosition, turnTravel } from './cityMap';
import { DriveScene } from './DriveScene';
import {
  activeCommand,
  driveAppStatus,
  driveAppsOf,
  maxSpeedOf,
  RATED_RUNTIME_S,
  remainingRuntimeS,
  roadOf,
  signalAhead,
  tripOf,
  vehicleOf,
  worldObjectsOf,
} from './driveExtras';
import { ROLE } from './model';
import { DEPART_MS, ParkedView, type ParkedCallout } from './ParkedView';
import { driveSummary } from './presentation';
import { useDisplayTheme } from './theme';
import { useDisplay } from './useDisplay';
import { useSmoothMapDistance } from './useSmoothDistance';
import { useChase, useSmoothObjects } from './useSmoothObjects';
import styles from './combined.module.css';

/**
 * 모니터 2대(계기판·인포테인먼트)를 한 화면(1920×1080)으로 합친 차량 화면. 양산차 중앙 화면 배치:
 * 왼쪽 = 주행 화면 (속도·최대 속도·3D 주변·예상 주행 가능 시간·트립), 오른쪽 = 내비게이션 (회사 → 집 지도·다음 안내·VM 상태).
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
  // 수집기 값이 바뀔 때만 새 배열 (주변 차 보간이 새 값이 온 때를 알 수 있게)
  const feedObjects = useMemo(() => (connected && worldObjectsOf(p)) || [], [connected, p]);
  // 주변 차: 1초마다 오는 위치 사이를 차마다 속도로 이어 그린다
  const smoothObjects = useSmoothObjects(feedObjects);
  const stopped = mode === 'SAFE_STOP';
  const autopilot = mode === 'AUTO' && Boolean(active);
  const handover = !active && (preparing || mode === 'AUTO_READY') ? selectedDrive : null;
  // 1초마다 오는 트립 사이를 속도로 보간한 지도 거리: 내비 이동과 주행 장면 회전·차선 흐름이 같이 부드럽게 움직인다
  const mapDistance = useSmoothMapDistance(
    trip?.distance_m ?? null,
    speed === null ? null : speed * ROUTE_SCALE,
  );
  // 보간으로 앞서 간 거리 (RC m): 서 있는 것(사거리·신호등·가로지르는 차의 앞뒤 위치)은 그만큼 당겨 그린다.
  // 1초마다 한 번에 뛰지 않게. 수집기가 아직 앞이라고 한 사거리는 지나간 것으로 바꾸지 않는다 (회전 전/후 판단은 수집기 기준)
  const lead = trip ? Math.max(0, mapDistance / ROUTE_SCALE - trip.distance_m) : 0;
  const feedRoad = connected ? roadOf(p) : null;
  const feedCross = feedRoad?.intersection ?? null;
  // 내 차로: 1초마다 반 차로씩 오는 값을 초당 0.8 차로로 따라가 차로 변경이 미끄러지듯 보이게.
  // 회전으로 새 도로에 들어서면(도로 이름이 바뀜) 원호가 닿은 차로로 바로 맞춘다
  const lane = useChase(feedRoad?.lane ?? null, 0.8, feedRoad?.name);
  const road = feedRoad && {
    ...feedRoad,
    lane: lane ?? feedRoad.lane,
    intersection:
      feedCross && lead > 0
        ? {
            ...feedCross,
            y_m: feedCross.y_m > 0 ? Math.max(0.001, feedCross.y_m - lead) : feedCross.y_m - lead,
          }
        : feedCross,
  };
  // 신호등은 서 있으므로 보간한 만큼 당긴다 (차는 위에서 차마다 보간)
  const objects =
    lead > 0
      ? smoothObjects.map((o) => (o.kind === 'traffic_light' ? { ...o, y_m: o.y_m - lead } : o))
      : smoothObjects;
  // 앞 신호등 인식: 빨간·노란불이면 정지 대기로 본다 (거리는 내비와 같은 지도 축척)
  const signal = signalAhead(objects);
  const signalStop = signal?.state === 'red' || signal?.state === 'yellow';
  // 출발 전 정차: 트립이 시작점이고 멈춰 있으면 차 외관 화면. 움직이기 시작하면 바퀴가 구르며 나가는 모습을
  // 잠깐 보여 준 뒤 2분할 주행 화면
  const parked = trip !== null && trip.distance_m < 0.05 && (speed ?? 0) < 0.02;
  const departing = useDeparture(parked);
  const vmStatus = (vm: NonNullable<typeof p>['state']['vms'][number]) =>
    vm.role === 'interaction'
      ? connected && vm.xen_state === 'running'
        ? { label: '인포테인먼트', tone: 'ok' as const }
        : { label: connected ? 'VM 꺼짐' : '연결 미확인', tone: 'muted' as const }
      : driveAppStatus(driveAppsOf(p, vm.vm_id), vm.xen_state, connected);
  const callouts: ParkedCallout[] = (p?.state.vms ?? []).map((vm) => ({
    key: ROLE[vm.role].name,
    title: `${ROLE[vm.role].name} · ${ROLE[vm.role].title}`,
    // 주행 VM 은 엔진룸(본닛), 인포테인먼트는 차 가운데로 선이 이어진다
    anchor: vm.role === 'interaction' ? ('center' as const) : ('hood' as const),
    ...vmStatus(vm),
  }));
  // 집까지 150 m 안이면 주행 장면 오른쪽에 집을 세운다 (남은 지도 거리 → RC m)
  const remainingMap = trip ? routePosition(mapDistance).remaining : null;
  const homeAheadM = remainingMap !== null && remainingMap < 150 ? remainingMap / ROUTE_SCALE : null;
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
    { key: 'signal', label: '신호 정지', on: signalStop },
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
          <DriveScene
            steer={live?.steer_rad ?? 0}
            objects={objects}
            distanceM={mapDistance / ROUTE_SCALE}
            turnTravelM={trip ? turnTravel(mapDistance, trip.distance_m * ROUTE_SCALE) : null}
            homeAheadM={homeAheadM}
            stopped={stopped}
            road={road}
            rainbow={Boolean(active) && !stopped}
            braking={stopped || (signalStop && (speed ?? 0) < 0.2)}
          />
          {signal && (
            <p className={styles.signalNote} data-state={signal.state ?? 'unknown'} data-testid="signal-note">
              <span className={styles.signalIcon} aria-hidden="true">
                <i data-color="red" data-on={signal.state === 'red'} />
                <i data-color="yellow" data-on={signal.state === 'yellow'} />
                <i data-color="green" data-on={signal.state === 'green'} />
              </span>
              {signal.state === 'red'
                ? '빨간불 인식 · 정지'
                : signal.state === 'yellow'
                  ? '노란불 인식 · 감속'
                  : signal.state === 'green'
                    ? '초록불 인식 · 통과'
                    : '신호등 인식 · 불빛 미확인'}
              <b>{Math.round((signal.y_m * ROUTE_SCALE) / 5) * 5} m</b>
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
        <CityNav
          tripM={trip?.distance_m ?? null}
          mapDistance={mapDistance}
          mapSpeedMps={speed === null ? null : speed * ROUTE_SCALE}
          now={now}
        />
        <footer className={styles.dock} aria-label="VM 상태">
          {p?.state.vms.map((vm) => {
            const status =
              vm.role === 'interaction'
                ? connected && vm.xen_state === 'running'
                  ? { label: '인포테인먼트', tone: 'ok' as const }
                  : { label: connected ? 'VM 꺼짐' : '연결 미확인', tone: 'muted' as const }
                : driveAppStatus(driveAppsOf(p, vm.vm_id), vm.xen_state, connected);
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
