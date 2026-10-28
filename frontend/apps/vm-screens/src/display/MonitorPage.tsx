import { StatusBadge } from '@xenova/ui';
import mark from '@xenova/ui/assets/xenova-mark.png';
import { useEffect } from 'react';
import { useParams } from 'react-router';
import { ROLE, type Presentation } from './model';
import { useDisplay } from './useDisplay';
import styles from './monitor.module.css';
const MODES: Record<string, string> = {
  AUTO: '자율주행',
  MANUAL: '수동주행',
  AUTO_READY: '자율 재개 대기',
  SAFE_STOP: '안전 정지',
};
export function MonitorPage() {
  const { outputId } = useParams();
  const { presentation: p, connected, error } = useDisplay();
  const output = p?.outputs.find((o) => o.output_id === outputId);
  useEffect(() => {
    if (!p || !connected || !output) return;
    const abort = new AbortController();
    let second = 0;
    // A DOM render acknowledgement is deliberately separate from physical HDMI/capture readiness.
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        void fetch(`/api/v1/display/outputs/${output.output_id}/ack`, {
          method: 'POST',
          credentials: 'same-origin',
          signal: abort.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            policy_epoch: p.policy_epoch,
            revision: p.revision,
            render_revision: p.render_revision,
          }),
        }).catch(() => undefined);
      });
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
      abort.abort();
    };
  }, [p, connected, output]);

  if (outputId !== 'monitor-1' && outputId !== 'monitor-2') return <main>알 수 없는 출력입니다.</main>;
  const instrument = outputId === 'monitor-1';
  const mapped = output?.screen === (instrument ? 'instrument' : 'infotainment');
  const fresh = connected && mapped && p?.state.drive.source.status === 'fresh';
  const mode = fresh ? p.state.drive.mode : 'unknown';
  const modeLabel = MODES[mode] ?? '주행 모드 미확인';
  const selected = p?.state.vms.find((v) => v.vm_id === output?.vm_id);
  const active = fresh ? p.state.vms.find((v) => v.vm_id === p.state.drive.active_vm_id) : null;
  return (
    <main className={styles.screen} data-theme="dark" data-testid={outputId} data-connected={connected}>
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>
            <img src={mark} alt="Xenova" className={styles.brand} /> XENOVA / VEHICLE DISPLAY
          </span>
          <h1>{instrument ? '드라이버 계기판' : '인포테인먼트 · 게스트 데이터'}</h1>
        </div>
        <div className={styles.headerMeta}>
          <span>HDMI {instrument ? '1' : '2'}</span>
          <strong className={connected ? styles.live : styles.warning}>
            {connected ? '표시 연결됨' : '표시 연결 끊김'}
          </strong>
        </div>
      </header>
      {!connected && (
        <div role="alert" className={styles.alert}>
          {error ?? '연결 대기'} · 현재 값은 미확인입니다.
        </div>
      )}
      {!mapped ? (
        <section className={styles.empty}>차량 화면 정책 수신 대기</section>
      ) : instrument ? (
        <>
          <section className={styles.instrument} aria-label="차량 계기판">
            <article className={styles.speed} aria-label="실제 차량 속도">
              <span className={styles.caption}>VEHICLE SPEED</span>
              <strong data-testid="speed-value">—</strong>
              <span className={styles.unit}>km/h</span>
              <p>속도 데이터 수신 대기</p>
            </article>
            <div className={styles.driveLane} aria-hidden="true">
              <span />
              <span />
              <span />
              <i />
            </div>
            <article className={styles.driveMode} aria-label="주행 모드">
              <span className={styles.caption}>DRIVE MODE</span>
              <strong>{modeLabel}</strong>
              <span className={styles.modeCode}>{mode === 'unknown' ? '—' : mode}</span>
              <p>확정된 주행 모드를 표시합니다.</p>
            </article>
            <article className={styles.battery} aria-label="차량 배터리">
              <div>
                <span>배터리 잔량</span>
                <strong data-testid="battery-value">
                  — <small>%</small>
                </strong>
              </div>
              <div className={styles.batteryTrack} />
              <p>배터리 데이터 수신 대기</p>
            </article>
          </section>
          <Notices p={p} connected={connected} />
        </>
      ) : (
        <>
          <section className={styles.modeStrip} aria-label="주행 안내">
            <strong>{modeLabel}</strong>
            <span>현재 구동 {active ? ROLE[active.role].name : '미확인'}</span>
          </section>
          <section className={styles.vmStrip} aria-label="현재 켜진 VM">
            {p?.state.vms.map((v) => {
              const known = connected && v.sources.xen.status === 'fresh';
              const running = known && v.xen_state === 'running';
              const label = !known
                ? '실행 미확인'
                : running
                  ? '켜짐'
                  : ((
                      {
                        stopped: '꺼짐',
                        crashed: '오류',
                        paused: '일시 정지',
                        stopping: '종료 중',
                      } as Record<string, string>
                    )[v.xen_state ?? ''] ?? '실행 미확인');
              return (
                <div key={v.vm_id} data-testid={`vm-${ROLE[v.role].name}`} data-running={running}>
                  <span className={styles.dot} />
                  <strong>{ROLE[v.role].name}</strong>
                  <span>{ROLE[v.role].title}</span>
                  <em>{label}</em>
                </div>
              );
            })}
          </section>
          <section className={styles.contentGrid}>
            <article className={styles.navigation} aria-label="네비게이션">
              <div className={styles.panelHead}>
                <h2>네비게이션</h2>
                <span>안내 수신 대기</span>
              </div>
              <div className={styles.mapPlaceholder}>
                <span className={styles.location}>⌖</span>
                <h3>목적지 안내를 기다리고 있어요</h3>
                <p>게스트 앱의 경로 데이터가 아직 연결되지 않았습니다.</p>
              </div>
            </article>
            <div className={styles.rightColumn}>
              <article className={styles.music} aria-label="음악">
                <span className={styles.caption}>MUSIC</span>
                <div className={styles.album}>♪</div>
                <h2>재생 정보 수신 대기</h2>
                <p>음악 앱 연결 대기 · 재생 중인 곡 미확인</p>
              </article>
              <article
                className={styles.guest}
                aria-label="게스트 콘텐츠"
                key={(selected?.vm_id ?? '') + (selected?.run_id ?? '')}
              >
                <span className={styles.caption}>GUEST CONTENT</span>
                <h2>
                  {selected
                    ? `${ROLE[selected.role].name} · ${ROLE[selected.role].title}`
                    : '선택 게스트 대기'}
                </h2>
                <p>
                  {!connected
                    ? '연결 끊김 · 게스트 콘텐츠 표시 중단'
                    : selected
                      ? p?.decisions[selected.vm_id]?.label
                      : '선택된 게스트 없음'}
                </p>
                <small>
                  {selected?.screen_source.kind === 'graphics'
                    ? '게스트 영상 어댑터 연결 대기'
                    : 'domU 앱 화면·데이터 수신 대기'}
                </small>
                {output.reason && (
                  <p>{connected ? output.reason : '화면 연결 끊김 · 마지막 화면 선택 유지'}</p>
                )}
              </article>
            </div>
          </section>
          <Notices p={p} connected={connected} />
        </>
      )}
      <footer className={styles.footer}>
        <span>읽기 전용 · {instrument ? '시연 연출: 대기 배경' : '차량 인포테인먼트'}</span>
        <span>수신되지 않은 값은 대기로 표시합니다.</span>
      </footer>
    </main>
  );
}
function Notices({ p, connected }: { p: Presentation | null; connected: boolean }) {
  const notices =
    connected && p
      ? p.state.vms.flatMap((v) => {
          const d = p.decisions[v.vm_id];
          return d && (d.tone === 'danger' || d.kind === 'operation' || d.kind === 'stale')
            ? [{ id: v.vm_id, text: `${ROLE[v.role].name} · ${d.label}`, tone: d.tone }]
            : [];
        })
      : [];
  return (
    <section className={styles.notices} aria-label="오류·복구 알림">
      <span>알림</span>
      {notices.length ? (
        notices.map((n) => (
          <StatusBadge key={n.id} health={n.tone === 'danger' ? 'fault' : 'recovering'} label={n.text} />
        ))
      ) : (
        <p>{connected ? '오류·복구 이벤트 수신 대기' : '알림 연결 미확인'}</p>
      )}
    </section>
  );
}
