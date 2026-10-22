import { useEffect } from 'react';
import { useParams } from 'react-router';
import { ROLE, SOURCE_LABEL, type DisplayVm, type Presentation } from './model';
import { useDisplay } from './useDisplay';
import styles from './monitor.module.css';

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

  if (outputId !== 'monitor-1' && outputId !== 'monitor-2')
    return <main className={styles.screen}>알 수 없는 출력입니다.</main>;
  const vm = p?.state.vms.find((v) => v.vm_id === output?.vm_id);
  const controlFresh = connected && p?.state.drive.source.status === 'fresh';
  const mode = controlFresh ? p.state.drive.mode : 'unknown';
  return (
    <main className={styles.screen} data-testid={outputId} data-connected={connected}>
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>PERSEUS / MULTI OS PLATFORM</span>
          <h1>{outputId === 'monitor-1' ? 'VM 전체 상태' : 'VM 상세 화면'}</h1>
        </div>
        <div className={styles.headerMeta}>
          <span>{outputId === 'monitor-1' ? 'MONITOR 01' : 'MONITOR 02'}</span>
          <strong className={connected ? styles.live : styles.warning}>
            {connected ? '상태 피드 연결' : '연결 대기 / 중단'}
          </strong>
        </div>
      </header>
      {!connected && (
        <div className={styles.alert} role="alert">
          {error ?? '표시 상태를 기다리고 있습니다.'} {p && '아래 정보는 마지막 수신 값입니다.'}
        </div>
      )}
      <section className={styles.drive} aria-label="주행 상태">
        <div>
          <small>주행 모드</small>
          <strong>{mode === 'unknown' ? '미확인' : mode}</strong>
        </div>
        <div>
          <small>활성 구동 VM</small>
          <strong>{controlFresh ? activeLabel(p) : '미확인'}</strong>
        </div>
        <div>
          <small>Jetson AI · 인식</small>
          <strong>
            {controlFresh && p.state.drive.ai_status !== 'unknown' ? p.state.drive.ai_status : '미확인'}
          </strong>
        </div>
        <p>주·백업 전환과 자동·수동 전환을 별도로 표시합니다.</p>
      </section>
      {!p || !output ? (
        <section className={styles.empty}>상태 수신 대기</section>
      ) : output.view === 'overview' ? (
        <section className={styles.grid} aria-label="VM 3개 전체 상태">
          {[...p.state.vms]
            .sort((a, b) => Object.keys(ROLE).indexOf(a.role) - Object.keys(ROLE).indexOf(b.role))
            .map((v) => (
              <VmCard key={v.vm_id} vm={v} p={p} connected={connected} />
            ))}
        </section>
      ) : vm ? (
        <section className={styles.detail}>
          <VmCard key={vm.vm_id + vm.run_id} vm={vm} p={p} connected={connected} detailed />
          <aside className={styles.inspector}>
            <h2>수신 근거</h2>
            <p className={styles.subtle}>소스별 연결 상태를 따로 확인합니다.</p>
            <dl>
              {(['xen', 'app', 'agent', 'inventory'] as const).map((key) => (
                <div key={key}>
                  <dt>
                    {
                      {
                        xen: 'Xen 실행 정보',
                        app: '앱 heartbeat',
                        agent: 'VM Agent',
                        inventory: 'OS inventory',
                      }[key]
                    }
                  </dt>
                  <dd>
                    {!connected
                      ? '화면 연결 끊김'
                      : key === 'agent' && !vm.agent_supported
                        ? '미지원'
                        : SOURCE_LABEL[vm.sources[key].status]}
                    {vm.sources[key].age_ms !== null &&
                      ` · ${(vm.sources[key].age_ms / 1000).toFixed(1)}초 전`}
                  </dd>
                </div>
              ))}
            </dl>
            <h2>화면 선택</h2>
            <p>{output.selection === 'pinned' ? '선택 VM 유지' : '확인된 활성 구동 VM 따라가기'}</p>
            <p className={styles.subtle}>
              주행 모드와 활성 구동 VM은 확인된 제어 상태를 기준으로 표시합니다.
            </p>
          </aside>
        </section>
      ) : (
        <section className={styles.empty}>선택 VM 상태 수신 대기</section>
      )}
      <footer className={styles.footer}>
        <span>LOCAL DISPLAY · 읽기 전용</span>
        <span>{p ? `정책 ${p.revision} · 렌더 ${p.render_revision.slice(0, 8)}` : '초기 연결 중'}</span>
      </footer>
    </main>
  );
}

function activeLabel(p: Presentation): string {
  const vm = p.state.vms.find((v) => v.vm_id === p.state.drive.active_vm_id);
  return vm ? `${ROLE[vm.role].name} · ${ROLE[vm.role].title}` : '없음 / 미확인';
}

function VmCard({
  vm,
  p,
  connected,
  detailed = false,
}: {
  vm: DisplayVm;
  p: Presentation;
  connected: boolean;
  detailed?: boolean;
}) {
  const meta = ROLE[vm.role];
  const decision = p.decisions[vm.vm_id]!;
  const active =
    connected && p.state.drive.source.status === 'fresh' && p.state.drive.active_vm_id === vm.vm_id;
  return (
    <article
      className={styles.card}
      data-role={vm.role}
      data-kind={connected ? decision.kind : 'stale'}
      data-testid={`card-${meta.name}`}
    >
      <div className={styles.cardHead}>
        <span>
          {meta.name} / {meta.title}
        </span>
        {active && <strong className={styles.active}>활성 구동</strong>}
      </div>
      <div className={styles.face} data-os={vm.os.expected}>
        <span className={styles.osMark}>{vm.os.expected === 'zephyr' ? 'Z' : 'D'}</span>
        <div>
          <h2>{vm.os.expected === 'zephyr' ? 'Zephyr RTOS' : 'Debian GNU/Linux'}</h2>
          <p>{meta.note}</p>
        </div>
      </div>
      <div className={styles.status} data-tone={connected ? decision.tone : 'warning'}>
        {connected ? decision.label : '상태 연결 끊김 · 마지막 관측 값'}
      </div>
      <dl className={styles.facts}>
        <div>
          <dt>기대 OS</dt>
          <dd>{vm.os.expected}</dd>
        </div>
        <div>
          <dt>관측 OS</dt>
          <dd>
            {vm.os.observed ?? '근거 미수신'}
            {vm.os.observed && !vm.os.current_run_verified && ' · 현재 부팅 확인 전'}
          </dd>
        </div>
        <div>
          <dt>Xen 상태{!connected ? ' (과거)' : ''}</dt>
          <dd>{vm.xen_state ?? '미확인'}</dd>
        </div>
        <div>
          <dt>앱 상태{!connected ? ' (과거)' : ''}</dt>
          <dd>{vm.app_state === 'unknown' ? '미확인' : vm.app_state}</dd>
        </div>
      </dl>
      {detailed && (
        <section className={styles.appArea}>
          <h3>앱 · 상태 화면</h3>
          <p>
            {vm.screen_source.kind === 'graphics'
              ? '그래픽 소스 연결 대기'
              : '현재는 상태 화면을 표시합니다.'}
          </p>
          <small>앱 화면 소스가 아직 연결되지 않았습니다.</small>
        </section>
      )}
      <div className={styles.identity}>
        <span>VM UUID</span>
        <code>{vm.vm_id}</code>
        {detailed && (
          <>
            <span>관측 실행 ID</span>
            <code>{vm.run_id ?? '미확인'}</code>
          </>
        )}
      </div>
    </article>
  );
}
