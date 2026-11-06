import { ROLE, type Presentation } from './model';
import { activeCommand, driveAppStatus, driveAppsOf, vehicleOf } from './driveExtras';
import { Notices } from './Notices';
import { driveSummary } from './presentation';
import styles from './instrument.module.css';

/**
 * HDMI 1 드라이버 계기판 (1920×1080). 양산차 디지털 계기판의 배치를 따른다:
 * 왼쪽 큰 속도 + 모드 줄, 가운데 원근 도로 위 차·주행 경로, 오른쪽 구동 VM·주행 앱 상태, 위쪽 경고등.
 * 값은 Gateway 주행 상태(state.drive), 차량 실측(state.vehicle), Zephyr 주행 앱 보고(state.drive_apps).
 * 신선하지 않거나 없는 값은 "—" 로 둔다.
 */
const GEARS = [
  { mode: 'AUTO', label: '자율' },
  { mode: 'MANUAL', label: '수동' },
  { mode: 'AUTO_READY', label: '대기' },
  { mode: 'SAFE_STOP', label: '정지' },
] as const;

const kmh = (mps: number) => (mps * 3.6).toFixed(1);

export function InstrumentCluster({
  p,
  connected,
  error,
  mapped,
}: {
  p: Presentation | null;
  connected: boolean;
  error: string | null;
  mapped: boolean;
}) {
  const { mode, modeLabel, preparing, active, selectedDrive } = driveSummary(p, connected, mapped);
  const vehicle = connected ? vehicleOf(p) : null;
  const live = vehicle?.source.status === 'fresh' ? vehicle : null;
  const drive = activeCommand(p, connected);
  const speed = live?.v_mps ?? null;
  const steer = live?.steer_rad ?? drive?.command?.steer_rad ?? 0;
  const target = drive?.command?.v_mps ?? null;
  const obstacle = Boolean(live?.flags?.obstacle) || drive?.perception?.kind === 'obstacle';
  const autopilot = mode === 'AUTO' && Boolean(active);
  const stopped = mode === 'SAFE_STOP';
  // 아직 출력 허가 전: 선택된 VM 이 넘겨받을 준비 중 (AUTO/MANUAL 준비, 자율 재개 대기)
  const handover = !active && (preparing || mode === 'AUTO_READY') ? selectedDrive : null;
  const driveVms = p?.state.vms.filter((vm) => vm.role !== 'interaction') ?? [];
  const lamps = [
    { key: 'estop', label: '비상정지', on: live?.flags?.estop },
    { key: 'watchdog', label: '명령 끊김', on: live?.flags?.watchdog },
    { key: 'obstacle', label: '장애물', on: obstacle },
  ];

  return (
    <section className={styles.cluster} aria-label="차량 계기판" data-mode={mode}>
      <h1 className={styles.srOnly}>드라이버 계기판</h1>
      <header className={styles.topBar}>
        <div className={styles.lamps} aria-label="경고등">
          {lamps.map((lamp) => (
            <span
              key={lamp.key}
              className={styles.lamp}
              data-key={lamp.key}
              data-on={lamp.on === true}
              data-unknown={!live}
              data-testid={`lamp-${lamp.key}`}
            >
              <i aria-hidden="true" />
              {lamp.label}
            </span>
          ))}
        </div>
        <span className={connected ? styles.linkOk : styles.linkLost}>
          HDMI 1 · {connected ? '표시 연결됨' : '표시 연결 끊김'}
        </span>
      </header>

      {!connected && (
        <div role="alert" className={styles.alert}>
          {error ?? '연결 대기'} · 현재 값은 미확인입니다.
        </div>
      )}

      {!mapped ? (
        <p className={styles.waiting}>차량 화면 정책 수신 대기</p>
      ) : (
        <div className={styles.body}>
          <div className={styles.left}>
            <div className={styles.speed} aria-label="실제 차량 속도">
              <strong data-testid="speed-value">{speed === null ? '—' : kmh(speed)}</strong>
              <span>km/h</span>
            </div>
            <ol className={styles.gears} aria-label="주행 모드 선택">
              {GEARS.map((gear) => (
                <li key={gear.mode} data-active={mode === gear.mode}>
                  {gear.label}
                </li>
              ))}
            </ol>
            <p className={styles.modeLabel} data-tone={stopped ? 'danger' : preparing ? 'warning' : 'normal'}>
              {modeLabel}
            </p>
            <div className={styles.setSpeed} aria-label="목표 속도">
              <span className={styles.setRing}>{target === null ? '—' : kmh(target)}</span>
              <small>목표 속도</small>
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
            </div>
          </div>

          <Road steer={steer} obstacle={obstacle} stopped={stopped} moving={speed !== null && speed > 0.05} />

          <aside className={styles.right} aria-label="구동 VM 상태">
            <div className={styles.driver}>
              <small>운전 중</small>
              <strong data-testid="active-vm">
                {active ? ROLE[active.role].name : handover ? `${ROLE[handover.role].name} 준비` : '—'}
              </strong>
              <span>
                {active
                  ? ROLE[active.role].title
                  : handover
                    ? '구동 승계 준비 중'
                    : stopped
                      ? '출력 정지'
                      : '구동 VM 없음'}
              </span>
            </div>
            <ul className={styles.apps}>
              {driveVms.map((vm) => {
                const status = driveAppStatus(driveAppsOf(p, vm.vm_id), vm, connected);
                return (
                  <li key={vm.vm_id} data-tone={status.tone} data-testid={`drive-app-${ROLE[vm.role].name}`}>
                    <span className={styles.appName}>
                      {ROLE[vm.role].name} <small>{ROLE[vm.role].title}</small>
                    </span>
                    <em>{status.label}</em>
                  </li>
                );
              })}
            </ul>
            {obstacle && (
              <p className={styles.obstacleNote} data-testid="obstacle-note">
                {drive?.perception?.detail ?? '전방 장애물 감지'}
              </p>
            )}
          </aside>
        </div>
      )}
      <Notices p={p} connected={connected} />
    </section>
  );
}

/** 원근 도로: 차선, 차, 장애물 (조향만큼 장애물 위치가 옆으로 옮겨진다) */
function Road({
  steer,
  obstacle,
  stopped,
  moving,
}: {
  steer: number;
  obstacle: boolean;
  stopped: boolean;
  moving: boolean;
}) {
  // 조향각(rad, 왼쪽 +)을 화면 가로 휨으로. RC카 조향 범위(약 ±0.4 rad)에서 눈에 띄게
  const bend = Math.max(-1, Math.min(1, steer / 0.4)) * -260;
  return (
    <div className={styles.road} data-moving={moving}>
      <svg viewBox="0 0 1000 720" preserveAspectRatio="xMidYMax meet" aria-hidden="true">
        <defs>
          <linearGradient id="road-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#0b0d10" stopOpacity="1" />
            <stop offset="0.45" stopColor="#0b0d10" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon className={styles.asphalt} points="140,720 860,720 560,170 440,170" />
        <line className={styles.edge} x1="150" y1="720" x2="445" y2="170" />
        <line className={styles.edge} x1="850" y1="720" x2="555" y2="170" />
        <g className={styles.dashes}>
          <line x1="380" y1="720" x2="480" y2="170" />
          <line x1="620" y1="720" x2="520" y2="170" />
        </g>
        {stopped && <line className={styles.stopLine} x1="400" y1="470" x2="600" y2="470" />}
        {obstacle && (
          <g className={styles.obstacle} transform={`translate(${500 + bend * 0.55} 400)`}>
            <polygon points="0,-44 30,22 -30,22" />
            <rect x="-38" y="22" width="76" height="10" rx="3" />
          </g>
        )}
        <rect x="0" y="0" width="1000" height="720" fill="url(#road-fade)" />
        {/* 위에서 본 차 (앞이 위) */}
        <g className={styles.car} transform="translate(500 625)">
          <rect className={styles.wheelBlock} x="-60" y="-50" width="14" height="30" rx="5" />
          <rect className={styles.wheelBlock} x="46" y="-50" width="14" height="30" rx="5" />
          <rect className={styles.wheelBlock} x="-60" y="28" width="14" height="30" rx="5" />
          <rect className={styles.wheelBlock} x="46" y="28" width="14" height="30" rx="5" />
          <path
            className={styles.body}
            d="M -48 -58 Q -50 -82 0 -86 Q 50 -82 48 -58 L 52 52 Q 50 78 0 80 Q -50 78 -52 52 Z"
          />
          <path className={styles.glass} d="M -38 -42 Q 0 -54 38 -42 L 34 -16 Q 0 -22 -34 -16 Z" />
          <rect className={styles.roof} x="-33" y="-12" width="66" height="44" rx="10" />
          <path className={styles.glass} d="M -34 36 Q 0 40 34 36 L 38 56 Q 0 64 -38 56 Z" />
          <path className={styles.mirror} d="M -50 -40 l -14 -4 l 0 9 Z M 50 -40 l 14 -4 l 0 9 Z" />
          <rect
            className={styles.tail}
            x="-40"
            y="73"
            width="80"
            height="6"
            rx="3"
            data-brake={stopped || obstacle}
          />
        </g>
      </svg>
    </div>
  );
}
