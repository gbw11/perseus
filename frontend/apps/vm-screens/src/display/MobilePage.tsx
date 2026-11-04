import logo from '@xenova/ui/assets/xenova-logo.png';
import { ROLE } from './model';
import { Notices } from './Notices';
import { driveSummary, osName, osReport } from './presentation';
import { useDisplay } from './useDisplay';
import styles from './mobile.module.css';

/** A data-rendered companion view, never a third HDMI output or render ACK. */
export function MobilePage() {
  const { presentation: p, connected, error } = useDisplay();
  const { mode, modeLabel, preparing, active, selectedDrive, authority } = driveSummary(p, connected);
  const output = p?.outputs.find((o) => o.output_id === 'monitor-2');
  const guest = p?.state.vms.find((v) => v.vm_id === output?.vm_id);
  return (
    <main className={styles.screen} data-theme="xenova" data-testid="mobile-display">
      <header className={styles.header}>
        <div className={styles.brand}>
          <img src={logo} alt="Xenova" />
          <h1>
            차량 디스플레이 <span>MOBILE DISPLAY</span>
          </h1>
        </div>
        <span className={styles.connection} data-connected={connected}>
          <i />
          {connected ? '표시 연결됨' : '표시 연결 대기'}
        </span>
      </header>
      {!connected && (
        <div role="alert" className={styles.alert}>
          {error ?? '상태 수신 대기'} · 현재 값은 미확인입니다.
        </div>
      )}
      <div className={styles.layout}>
        <section className={styles.cluster} aria-label="계기판">
          <div className={styles.caption}>DRIVER INSTRUMENT</div>
          <div className={styles.sensors}>
            <article aria-label="실제 차량 속도" className={styles.speed}>
              <strong data-testid="speed-value">—</strong>
              <span>km/h</span>
              <small>속도 수신 대기</small>
            </article>
            <article aria-label="차량 배터리" className={styles.battery}>
              <span>배터리</span>
              <strong data-testid="battery-value">
                — <small>%</small>
              </strong>
              <small>잔량 수신 대기</small>
            </article>
          </div>
          <article className={styles.drive} aria-label="주행 모드">
            <span className={styles.caption}>{mode === 'unknown' ? 'DRIVE MODE' : mode}</span>
            <h2>{modeLabel}</h2>
            <p>
              {preparing && selectedDrive
                ? `선택 ${ROLE[selectedDrive.role].name} · ${authority?.phase === 'FAILED' ? '구동 확인 필요' : '출력 허가 대기'}`
                : `현재 구동 ${active ? ROLE[active.role].name : '미확인'}`}
            </p>
            <small>제어 상태 표시 · 실제 차량 이동과 구분</small>
          </article>
        </section>
        <section className={styles.infotainment} aria-label="인포테인먼트 · 게스트 데이터">
          <div className={styles.caption}>INFOTAINMENT / GUEST</div>
          <div className={styles.vmList} aria-label="현재 켜진 VM">
            {p?.state.vms.map((vm) => {
              const known = connected && vm.sources.xen.status === 'fresh';
              const running = known && vm.xen_state === 'running';
              const label = !known
                ? '실행 미확인'
                : ((
                    {
                      running: '켜짐',
                      stopped: '꺼짐',
                      paused: '일시 정지',
                      stopping: '종료 중',
                      crashed: '오류',
                    } as Record<string, string>
                  )[vm.xen_state ?? ''] ?? '실행 미확인');
              return (
                <div
                  key={vm.vm_id}
                  data-role={vm.role}
                  data-testid={`vm-${ROLE[vm.role].name}`}
                  data-running={running}
                >
                  <strong>{ROLE[vm.role].name}</strong>
                  <span>{vm.role === 'interaction' ? 'Debian' : ROLE[vm.role].title}</span>
                  <em>{label}</em>
                </div>
              );
            })}
            {!p && <p>VM 상태 수신 대기</p>}
          </div>
          <div className={styles.apps}>
            <article aria-label="네비게이션">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m12 3 8 18-8-5-8 5 8-18Z" />
              </svg>
              <div>
                <h2>네비게이션</h2>
                <p>경로 안내 수신 대기</p>
              </div>
            </article>
            <article aria-label="음악">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M10 17V5l10-2v12M10 9l10-2" />
                <ellipse cx="7" cy="18" rx="3" ry="2" />
                <ellipse cx="17" cy="16" rx="3" ry="2" />
              </svg>
              <div>
                <h2>음악</h2>
                <p>재생 정보 수신 대기</p>
              </div>
            </article>
          </div>
          <article className={styles.guest} data-role={guest?.role} aria-label="게스트 콘텐츠">
            <h2>{guest ? `${ROLE[guest.role].name} · ${ROLE[guest.role].title}` : '게스트 콘텐츠 대기'}</h2>
            <p>
              {!connected
                ? '연결 끊김 · 게스트 콘텐츠 표시 중단'
                : guest
                  ? p?.decisions[guest.vm_id]?.label
                  : '선택된 게스트 없음'}
            </p>
            {guest && (
              <small>
                설정 OS: {osName(guest.os.expected)} · {osReport(guest, connected)}
              </small>
            )}
            {guest?.screen_source.kind === 'graphics' && <small>게스트 영상 어댑터 연결 대기</small>}
            {output?.reason && (
              <small>{connected ? output.reason : '화면 연결 끊김 · 마지막 화면 선택 유지'}</small>
            )}
          </article>
        </section>
      </div>
      <div className={styles.notices}>
        <Notices p={p} connected={connected} />
      </div>
      <footer className={styles.footer}>읽기 전용 · 미수신 값은 대기로 표시합니다.</footer>
    </main>
  );
}
