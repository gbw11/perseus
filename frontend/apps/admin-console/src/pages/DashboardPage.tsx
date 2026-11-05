import {
  Button,
  CameraView,
  Card,
  circuitOf,
  DriverView,
  fmtLap,
  isCircuit,
  kmh,
  StatusBadge,
  TrackView,
  useToast,
} from '@xenova/ui';
import type { Health } from '@xenova/ui';
import type { CSSProperties } from 'react';
import { Link } from 'react-router';
import type { DriveState, SocketState } from '../api/drive';
import { useEvents, useMetrics, useTelemetryHeartbeat, useVms } from '../api/queries';
import { restartSimRace, selectSimTrack, useSimState, type SimView } from '../api/sim';
import type { Vm, VmEvent } from '../api/types';
import { cpuPercentOf, latest, memoryPercentOf } from '../domain/vmMetrics';
import { observedControl, vmAppObservations } from '../domain/driveObservations';
import { clock, describe } from '../domain/events';
import { publisherObservation } from '../domain/telemetryPublisher';
import { vmAuthorityOf } from '../domain/vmAuthority';
import {
  ago,
  appHealth,
  appHealthLabel,
  controlLabel,
  MODE_HEALTH,
  MODE_LABEL,
  osLabel,
  plain,
  ROLE_ORDER,
  ROLES,
  roleOf,
  SAFETY,
  signed,
  sortVms,
  UNASSIGNED_COLOR,
  vmOf,
  type AppId,
} from '../domain/roles';
import { AGENT_LABEL, STATE_HEALTH, STATE_LABEL } from '../format';
import { useDrive } from '../layout/DriveContext';
import { SimTrackSelect } from './SimTrackSelect';
import styles from './pages.module.css';

const MOCK = import.meta.env.VITE_MOCK === 'true';
/** 세 번째 VM 이 낸 AI 감지 영상 (MJPEG) — dom0 웹 프록시가 같은 origin 으로 넘긴다 (카메라 계약 camera-stream-v1) */
const CAMERA_URL: string = import.meta.env.VITE_CAMERA_URL || '/camera/stream.mjpg';

/**
 * W-02 대시보드 — 주행 통합 상태 (화면 정의서 W-02, UI-09). 데모 내내 띄워 두는 화면.
 * "지금 누가 차를 몰고 있고, 넘길 준비가 됐는지" 를 한 화면에. 참고 시안: stitch_untitled_project/auto.
 */
export function LegacyDashboardPage() {
  const { drive } = useDrive();
  const vms = useVms();
  const events = useEvents({ limit: 5 });
  const sim = useSimState();
  const control = drive?.control;
  const vehicle = drive?.vehicle;
  const flags = vehicle?.flags;
  const perception = drive?.perception;
  const activeVm =
    control && control.received_ago_ms < 3000
      ? vms.data?.items.find((vm) => vm.vm_id === control.active_vm_id)
      : undefined;
  const vmName = (vmId: string | null) => {
    const vm = vms.data?.items.find((v) => v.vm_id === vmId);
    return vm ? (roleOf(vm, drive)?.title ?? vm.name) : (vmId ?? '시스템');
  };

  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>주행 모드</div>
          <div className={styles.huge}>
            {control?.mode ? `${control.mode} · gen ${control.generation}` : '—'}
          </div>
        </div>
        {control?.mode && (
          <StatusBadge health={MODE_HEALTH[control.mode]} label={MODE_LABEL[control.mode]} size="lg" />
        )}
        <div
          className={[styles.controlBar, activeVm ? styles.controlOn : ''].join(' ')}
          style={{ '--accent': activeVm ? roleOf(activeVm, drive)?.color : undefined } as CSSProperties}
        >
          {activeVm
            ? `▶ ${roleOf(activeVm, drive)?.title ?? activeVm.name} 활성 구동`
            : '활성 구동 VM 미확인'}
        </div>
        {/* 안전 플래그 셋은 위쪽에서 뺐다 (2026-10-22 요청) — 아래 "차량 상태" 카드에 그대로 있다 */}
      </section>

      {control?.mode === 'AUTO_READY' && (
        <div className={styles.draft}>
          {drive?.resume
            ? `자율주행 재개 — ${Math.max(1, Math.ceil(drive.resume.remaining_ms / 1000))}초 뒤 전환`
            : drive?.resume_cancel?.reason === 'operator_cancelled'
              ? '자율주행 재개 취소됨 — 수동주행 유지'
              : '자율주행 재개 가능'}{' '}
          — <Link to="/simulator">Simulator</Link>에서 {drive?.resume ? '[취소]' : '[자율주행 재개]'}
        </div>
      )}

      <div className={styles.cols3}>
        {ROLE_ORDER.map((app) =>
          // 시뮬레이터 구성이면 세 번째 칸이 시뮬레이터로 바뀐다 (위치가 1초 안에 오면, 화면 배치 계약 v1.1 §5.6)
          app === 'interaction' && sim.available ? (
            <SimCard key="simulator" sim={sim} drive={drive} board={vmOf(app, vms.data?.items, drive)} />
          ) : (
            <AppCard key={app} app={app} vm={vmOf(app, vms.data?.items, drive)} drive={drive} sim={sim} />
          ),
        )}
      </div>

      <div className={styles.cols2}>
        <Card title="차량 상태" actions={<span className={styles.dim}>{ago(vehicle?.received_ago_ms)}</span>}>
          <div className={styles.tiles}>
            <div className={styles.tile}>
              <small>속도</small>
              <span className={styles.big}>{plain(vehicle?.v_mps)}</span>
              <span className={styles.unit}>m/s</span>
            </div>
            <div className={styles.tile}>
              <small>조향</small>
              <span className={styles.big}>{signed(vehicle?.steer_rad)}</span>
              <span className={styles.unit}>rad</span>
            </div>
          </div>
          <div className={styles.rows}>
            <div>
              <span>적용된 세대</span>
              <span>{vehicle?.applied_generation ?? '—'}</span>
            </div>
            {SAFETY.map((f) => (
              <div key={f.key} title={f.hint}>
                <span>{f.label}</span>
                <StatusBadge
                  health={!flags ? 'stopped' : flags[f.key] ? 'fault' : 'ok'}
                  label={flags ? (flags[f.key] ? f.on : f.off) : '—'}
                  size="sm"
                />
              </div>
            ))}
          </div>
          <p className={styles.dim}>항목에 마우스를 올리면 뜻이 나온다. 셋 중 하나라도 켜지면 차는 선다.</p>
        </Card>
        <Card
          title="인지 (젯슨)"
          actions={
            <StatusBadge
              health={perception?.valid ? 'ok' : 'fault'}
              label={perception?.valid ? `유효 · ${perception.hz} Hz` : '인지 끊김'}
              size="sm"
            />
          }
        >
          <div className={styles.tiles}>
            <div className={styles.tile}>
              <small>차선 오프셋</small>
              <span className={styles.big}>{signed(perception?.lane_offset_m)}</span>
              <span className={styles.unit}>m</span>
            </div>
            <div className={styles.tile}>
              <small>방향 오차</small>
              <span className={styles.big}>{signed(perception?.heading_err_rad)}</span>
              <span className={styles.unit}>rad</span>
            </div>
          </div>
          <div className={styles.rows}>
            <div>
              <span>마지막 수신</span>
              <span>{ago(perception?.received_ago_ms)}</span>
            </div>
            <div>
              <span>장애물 / 정지 필요</span>
              <span>
                {perception?.obstacle_count ?? '—'} / {perception?.stop ? '예' : '아니오'}
              </span>
            </div>
          </div>
          {perception && !perception.valid && (
            <p className={styles.dim}>
              인지가 끊기면 자율주행이 정지 명령을 낸다. 모드는 그대로다 (시나리오 D5).
            </p>
          )}
        </Card>
      </div>

      <Card title="최근 이벤트" actions={<Link to="/events">이벤트 화면 →</Link>}>
        {(events.data?.items ?? []).slice(0, 5).map((event) => {
          const { title, detail } = describe(event, vmName);
          return (
            <div key={event.event_id} className={styles.rows}>
              <div>
                <span className={styles.mono}>
                  {clock(event.occurred_at)}{' '}
                  <span className={`${styles.sev} ${styles[`sev${event.severity}`]}`}>{event.severity}</span>
                </span>
                <span>
                  {title}
                  {detail && <span className={styles.dim}> · {detail}</span>}
                </span>
              </div>
            </div>
          );
        })}
        {events.data?.items.length === 0 && <p className={styles.dim}>이벤트 없음</p>}
        {events.error && <p className={styles.dim}>이벤트를 불러오지 못했다</p>}
      </Card>
    </div>
  );
}

function AppCard({
  app,
  vm,
  drive,
  sim,
}: {
  app: AppId;
  vm: Vm | undefined;
  drive: DriveState | null;
  sim: SimView;
}) {
  const role = ROLES[app];
  const state = drive?.apps[app];
  const health = appHealth(role, vm, state);
  const control = controlLabel(role, vm, drive);
  const active = !!vm && drive?.control?.active_vm_id === vm.vm_id && drive.control.received_ago_ms < 3000;
  const command = state?.last_command;
  // 시뮬레이터 구성(계약 v1.4 실제 서킷)이면 모니터와 같은 차 1인칭을 카드에도 (자율 = 인식 박스)
  const circuit =
    sim.available && sim.world && isCircuit(sim.world.track) ? circuitOf(sim.world.track) : null;
  const pov = circuit && sim.pose && sim.world && (app === 'autonomous' || app === 'manual');
  const speed = (v: number | null) =>
    circuit ? `${Math.abs(kmh(circuit, v ?? 0)).toFixed(0)} km/h` : `${plain(v)} m/s`;
  return (
    <Card
      className={styles.appCard}
      style={{ '--accent': role.color } as CSSProperties}
      title={<span style={{ color: role.color }}>{role.title}</span>}
      actions={<StatusBadge health={health} label={appHealthLabel(vm, state)} size="sm" />}
    >
      <div className={styles.rows}>
        <div>
          <span>OS</span>
          <span>{vm ? osLabel(vm) : '—'}</span>
        </div>
      </div>
      {control && (
        <div className={[styles.controlBar, active ? styles.controlOn : ''].join(' ')}>{control}</div>
      )}
      {pov && (
        <DriverView
          pose={sim.pose}
          world={sim.world}
          boxes={app === 'autonomous'}
          dimmed={!active}
          quality="low"
          label={`${role.title} — 시뮬레이터 1인칭`}
          overlay={
            <div className={styles.povTop}>
              <span className={styles.povTag} style={{ background: role.color }}>
                {active ? '▶ 운전 중' : '대기'}
              </span>
              <span className={styles.povHud}>{speed(sim.pose?.v_mps ?? 0)}</span>
            </div>
          }
        />
      )}
      {role.driving ? (
        <div className={styles.rows}>
          <div>
            <span>명령 주기</span>
            <span>
              {state?.command_fresh ? `${state.command_hz} Hz` : '명령 없음'}
              {app === 'manual' && !active && state?.command_fresh ? ' (v=0)' : ''}
            </span>
          </div>
          <div>
            <span>마지막 명령</span>
            <span>{command ? `${speed(command.v_mps)} · ${signed(command.steer_rad)} rad` : '—'}</span>
          </div>
          <div>
            <span>app.heartbeat</span>
            <span>
              {state?.heartbeat_state ? `${state.heartbeat_state} · ${ago(state.heartbeat_ago_ms)}` : '—'}
            </span>
          </div>
          <div>
            <span>도메인</span>
            <span>{vm?.state ?? '—'}</span>
          </div>
        </div>
      ) : (
        <>
          <CameraView
            compact
            mock={MOCK}
            streamUrl={MOCK ? undefined : CAMERA_URL}
            down={
              vm && vm.state !== 'running'
                ? 'VM 중지 — 영상 없음 (주행은 그대로)'
                : health === 'fault' || health === 'recovering'
                  ? '앱 응답 없음 — 영상 없음 (주행은 그대로)'
                  : null
            }
            motion={{
              v: drive?.vehicle?.v_mps ?? 0,
              steer: drive?.vehicle?.steer_rad ?? 0,
              laneOffset: drive?.perception?.valid ? (drive.perception.lane_offset_m ?? 0) : 0,
              obstacle: !!drive?.vehicle?.flags?.obstacle || (drive?.perception?.obstacle_count ?? 0) > 0,
            }}
            stats={[
              {
                label: '장애물',
                value: String(drive?.perception?.obstacle_count ?? '—'),
                warn: !!drive?.perception?.obstacle_count,
              },
              {
                label: '정지',
                value: drive?.perception?.stop ? '예' : '아니오',
                warn: !!drive?.perception?.stop,
              },
            ]}
          />
          <div className={styles.rows}>
            <div>
              <span>app.heartbeat</span>
              <span>{ago(state?.heartbeat_ago_ms)}</span>
            </div>
            <div>
              <span>자율주행 재개</span>
              <span>
                {drive?.resume
                  ? `${Math.max(1, Math.ceil(drive.resume.remaining_ms / 1000))}초 뒤 자율주행으로 전환`
                  : drive?.control?.mode === 'AUTO_READY'
                    ? '가능 — 시연 제어에서'
                    : drive?.control?.mode === 'AUTO'
                      ? '자율주행 중'
                      : '대기'}
              </span>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

const CAR_COLOR = {
  autonomous: 'var(--x-app-autonomous)',
  manual: 'var(--x-app-manual)',
} as const;

/**
 * 세 번째 칸 — 시뮬레이터 (시뮬레이터 시연 시나리오 §4). 실차 대신 화면 속 차가 트랙을 도는 모습.
 * 네비게이션 VM 은 그대로 돌고 있으므로 그 상태 한 줄을 남긴다 (그 VM 을 죽여도 차는 계속 달린다 — D8 격리).
 */
function SimCard({ sim, drive, board }: { sim: SimView; drive: DriveState | null; board: Vm | undefined }) {
  const active = drive?.control?.active_app ?? null;
  const boardState = drive?.apps.interaction;
  const boardHealth = appHealth(ROLES.interaction, board, boardState);
  const pose = sim.pose;
  const flags = drive?.vehicle?.flags;
  const toast = useToast();
  // v1.4 실제 서킷: 속도는 실제 단위(km/h), 트랙 바꾸기·다시 출발 (REST-36·37)
  const circuit = sim.world && isCircuit(sim.world.track) ? circuitOf(sim.world.track) : null;
  const race = pose?.race;
  const act = (job: Promise<void>) =>
    job.catch((e: unknown) => toast.show(e instanceof Error ? e.message : String(e), 'fault'));
  return (
    <Card
      className={styles.appCard}
      style={{ '--accent': 'var(--x-app-simulator)' } as CSSProperties}
      title={<span style={{ color: 'var(--x-app-simulator)' }}>시뮬레이터</span>}
      actions={
        <StatusBadge
          health={flags?.estop || flags?.obstacle ? 'fault' : (pose?.v_mps ?? 0) > 0.02 ? 'ok' : 'stopped'}
          label={
            flags?.estop
              ? 'E-stop'
              : flags?.obstacle
                ? '장애물 정지'
                : (pose?.v_mps ?? 0) > 0.02
                  ? '주행 중'
                  : '정지'
          }
          size="sm"
        />
      }
    >
      <TrackView
        compact
        pose={pose}
        world={sim.world}
        trail={sim.trail}
        carColor={active ? CAR_COLOR[active] : 'var(--x-fault)'}
        sensing={!!flags?.obstacle}
        label="시뮬레이터 트랙 — 차 위치와 장애물"
      />
      <div className={styles.rows}>
        {circuit && sim.world ? (
          <>
            <div>
              <span>트랙</span>
              <SimTrackSelect
                tracks={sim.world.tracks ?? []}
                current={circuit.track.id}
                onPick={(id) => void act(selectSimTrack(id))}
              />
            </div>
            <div>
              <span>속도 · 랩</span>
              <span className={styles.mono}>
                {pose ? `${Math.abs(kmh(circuit, pose.v_mps)).toFixed(0)} km/h` : '—'}
                {race
                  ? race.phase === 'lights'
                    ? ` · 출발 신호 ${race.lights_on}/5`
                    : ` · ${race.lap + 1}랩 ${fmtLap(race.lap_ms)} · 최고 ${fmtLap(race.best_lap_ms)}`
                  : ''}
              </span>
            </div>
            <div>
              <span>출발</span>
              <Button size="sm" variant="secondary" onClick={() => void act(restartSimRace())}>
                다시 출발 (출발 신호)
              </Button>
            </div>
          </>
        ) : (
          <>
            <div>
              <span>위치 x, y</span>
              <span className={styles.mono}>
                {pose ? `${pose.x.toFixed(2)}, ${pose.y.toFixed(2)} m` : '—'}
              </span>
            </div>
            <div>
              <span>속도 · 조향</span>
              <span className={styles.mono}>
                {pose ? `${plain(pose.v_mps)} m/s · ${signed(pose.steer_rad)} rad` : '—'}
              </span>
            </div>
          </>
        )}
        <div>
          <span>장애물 · 트랙 rev</span>
          <span className={styles.mono}>
            {sim.world ? `${sim.world.obstacles.length}개 · ${sim.world.revision}` : '—'}
          </span>
        </div>
        <div>
          <span>네비게이션 VM</span>
          <StatusBadge
            health={boardHealth}
            label={boardHealth === 'ok' ? '정상 — 알림·재개 요청' : '응답 없음 — 주행은 그대로'}
            size="sm"
          />
        </div>
      </div>
    </Card>
  );
}

/**
 * VM Dashboard / Overview.
 * API VM 목록을 그대로 카드로 그리고 VM 이름으로 부른다. 역할은 vm.role 이 있을 때만 표시한다.
 * 두 주행 VM은 제어권(Active/Standby)과 주행 모드(AUTO/MANUAL)를 분리한다.
 */
export function DashboardPage() {
  const { drive, socket } = useDrive();
  const vms = useVms();
  const events = useEvents({ limit: 30 });
  const items = sortVms(vms.data?.items ?? []);
  const control = observedControl(drive, socket);
  const activeVm = items.find((vm) => vm.vm_id === control?.active_vm_id);
  const authority =
    control && !control.active_vm_id && !control.active_app ? '없음' : (activeVm?.name ?? '미확인');

  return (
    <div className={styles.overviewPage}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>OVERVIEW</span>
          <h2>Virtual Machines</h2>
        </div>
      </header>

      <section className={styles.overviewSummary} aria-label="주행 및 연결 요약">
        <OverviewSummaryItem label="DRIVE MODE" value={control?.mode ? MODE_LABEL[control.mode] : '미확인'} />
        <OverviewSummaryItem label="CONTROL AUTHORITY" value={authority} />
        <OverviewSummaryItem
          label="상태 스트림"
          value={socket === 'open' ? 'Connected' : socket === 'connecting' ? 'Connecting' : 'Disconnected'}
          tone={socket === 'open' ? 'ok' : socket === 'connecting' ? 'recovering' : 'fault'}
        />
        <OverviewSummaryItem label="LAST OBSERVED" value={overviewObserved(vms.data?.observed_at)} />
      </section>

      {vms.isPending && <div className={styles.statePanel}>VM 상태를 불러오는 중입니다…</div>}
      {vms.error && (
        <div className={styles.statePanel} role="alert">
          VM 상태를 불러오지 못했습니다. 연결을 확인해 주세요.
        </div>
      )}

      {vms.isSuccess && items.length === 0 && <div className={styles.statePanel}>등록된 VM이 없습니다.</div>}

      <div className={styles.vmGrid}>
        {items.map((vm) => (
          <VmOverviewCard
            key={vm.vm_id}
            vm={vm}
            drive={drive}
            socket={socket}
            latestEvent={overviewLatestEvent(events.data?.items, vm.vm_id)}
          />
        ))}
      </div>
    </div>
  );
}

function OverviewSummaryItem({ label, value, tone }: { label: string; value: string; tone?: Health }) {
  return (
    <div className={styles.summaryItem} data-tone={tone}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function VmOverviewCard({
  vm,
  drive,
  socket,
  latestEvent,
}: {
  vm: Vm;
  drive: DriveState | null;
  socket: SocketState;
  latestEvent: VmEvent | undefined;
}) {
  const role = roleOf(vm, drive);
  const metrics = useMetrics(vm.vm_id, true);
  const publisher = publisherObservation(vm.vm_id, useTelemetryHeartbeat(vm.vm_id));
  const samples = metrics.data?.items ?? [];
  const cpu = latest(samples, cpuPercentOf);
  const memory = latest(samples, (sample) => memoryPercentOf(sample, vm));
  const apps = vmAppObservations(vm.vm_id, drive, socket);
  const authority = vmAuthorityOf(role, vm, observedControl(drive, socket)?.active_vm_id);
  const eventSummary = latestEvent
    ? `${clock(latestEvent.occurred_at)} · ${describe(latestEvent, () => vm.name).title}`
    : '최근 중요 이벤트 없음';
  const unavailable = overviewMetricUnavailable(metrics.isPending, samples.length);
  const driving = role ? role.driving : vm.guest_kind === 'zephyr';

  const style = { '--vm-accent': role?.color ?? UNASSIGNED_COLOR } as CSSProperties;
  return (
    <Link className={styles.vmCard} style={style} to={`/dashboard/vms/${vm.vm_id}`}>
      <div className={styles.vmCardTop}>
        <div>
          <span className={styles.vmKicker}>{role?.title ?? '역할 미지정'}</span>
          <h3>{vm.name}</h3>
          <p>
            {driving ? 'Drive' : 'Infotainment'} · {osLabel(vm)}
          </p>
        </div>
        <span className={styles.authority} data-authority={authority.toLowerCase()}>
          {authority}
        </span>
      </div>

      <div className={styles.vmStatusRows}>
        <OverviewStatusLine label="VM" value={STATE_LABEL[vm.state]} tone={STATE_HEALTH[vm.state]} />
        <OverviewStatusLine label="앱 상태" value={overviewAppSignal(apps, 'phaseLabel')} />
        <OverviewStatusLine label="앱 heartbeat" value={overviewAppSignal(apps, 'heartbeatLabel')} />
        <OverviewStatusLine
          label="수집기 (v2)"
          value={publisher.label}
          tone={publisher.tone}
          detail={publisher.report?.age_ms != null ? ago(publisher.report.age_ms) : undefined}
        />
        {vm.guest_kind === 'linux' && (
          <OverviewStatusLine label="Linux Agent" value={AGENT_LABEL[vm.agent_status ?? 'UNKNOWN']} />
        )}
      </div>

      <div className={styles.resourceBlock}>
        <OverviewMetricBar label="CPU" value={cpu} unavailable={unavailable} />
        <OverviewMetricBar
          label="Memory"
          value={memory}
          unavailable={unavailable}
          detail={vm.guest_kind === 'zephyr' ? 'Kernel heap' : `${vm.memory_mib} MiB allocated`}
        />
      </div>

      <div className={styles.vmCardFoot}>
        <div>
          <span>RECENT SIGNAL</span>
          <p>{eventSummary}</p>
        </div>
        <strong>View details ↗</strong>
      </div>
    </Link>
  );
}

function OverviewStatusLine({
  label,
  value,
  tone,
  detail,
}: {
  label: string;
  value: string;
  tone?: Health;
  detail?: string;
}) {
  return (
    <div className={styles.statusLine} data-tone={tone}>
      <span>{label}</span>
      <strong>
        <i aria-hidden="true" /> {value}
      </strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function OverviewMetricBar({
  label,
  value,
  unavailable,
  detail,
}: {
  label: string;
  value: number | null;
  unavailable: string | null;
  detail?: string;
}) {
  const safeValue = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className={styles.metricRow}>
      <div>
        <span>{label}</span>
        <strong>{value == null ? '—' : `${value.toFixed(0)}%`}</strong>
      </div>
      <div
        className={styles.metricTrack}
        aria-label={`${label} ${value == null ? '데이터 없음' : `${value.toFixed(0)}%`}`}
      >
        <i style={{ width: `${safeValue}%` }} />
      </div>
      <small>{unavailable ?? detail ?? '\u00a0'}</small>
    </div>
  );
}

function overviewAppSignal(
  reports: ReturnType<typeof vmAppObservations>,
  field: 'phaseLabel' | 'heartbeatLabel',
): string {
  const labels = new Set(reports.map((report) => report[field]));
  if (labels.size === 0) return '미확인';
  if (labels.size === 1) return reports[0]![field];
  return reports.map((report) => `${report.appId}: ${report[field]}`).join(' · ');
}

function overviewMetricUnavailable(pending: boolean, sampleCount: number): string | null {
  if (pending) return 'Loading metrics';
  if (sampleCount === 0) return 'No metric data';
  return null;
}

function overviewLatestEvent(events: VmEvent[] | undefined, vmId: string | undefined): VmEvent | undefined {
  return vmId ? events?.find((event) => event.vm_id === vmId) : undefined;
}

function overviewObserved(value: string | undefined): string {
  if (!value) return 'Waiting for data';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('ko-KR', { hour12: false });
}
