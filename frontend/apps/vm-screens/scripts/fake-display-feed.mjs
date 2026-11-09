#!/usr/bin/env node
/**
 * 노트북 미리보기용 가짜 dom0 표시 수집기 (HDMI /display/* 화면 개발용, 보드 없이).
 * 실제 수집기(platform/services/display/router)와 같은 경로·규격으로 답한다:
 *   GET  /api/v1/display/presentation       현행 display.presentation (parsePresentation 통과)
 *   POST /api/v1/display/outputs/:id/ack    204
 * 시나리오:
 *   GET  /scenario                          현재 시나리오와 목록
 *   GET  /scenario/<name>                   고정 (cycle 이면 아래 시연 순서대로)
 *   GET  /jump/<경로 m>                      시연 경로 위 그 지점으로 건너뛰기 (예: /jump/8 = 주차 진입 직전)
 *   GET  /restart                           처음부터 (출발선에서 정차 화면 → 8초 뒤 출발)
 *   GET  /park · /go                        처음 정차 화면에 머무르기 · 거기서 출발
 *
 * 주행은 Jetson 기본 코스(src/display/course.ts, jetson-demo) 한 바퀴 시연 경로를 따른다:
 * 출발 전 정차 → ① 콘 A·B 회피(VM1 자율) → ② 왼쪽 직선에서 수동 전환, 위 직선에서 자동 복귀 →
 * ③ 오른쪽 위 코너에서 VM1 장애(안전 정지 4초 → VM2 승계 준비 3초 → VM2 자율) → ④ 마당을 지나 P1 주차 →
 * 10초 쉬고 처음부터.
 *
 * 제안 확장 필드 (현재 dom0 수집기는 아직 보내지 않는다 — 화면이 정해지면 수집기 요청 명세가 된다):
 *   state.vehicle             vehicle.status (MQTT 계약 5.4, MQTT-10) — 게이트웨이가 ESP32 실측을 변환
 *   state.drive_apps[vm_id]   Zephyr 주행 펌웨어의 앱별 보고
 *     .autonomous / .manual = { heartbeat (6.1), lifecycle (6.3 warm-drive-1), command (5.4 drive.command),
 *                               perception (perception.event, 자율만), source }
 *   state.drive_limits        주행 프로필 한도 (apps/drive/profiles/lattice-lab-1.json max_velocity 0.5 m/s)
 *   state.trip                트립 거리·주행 시간 (vehicle.status 속도 적분)
 *   state.course              코스 위 차 위치(후륜축 x·y·yaw, sim.pose 와 같은 좌표)·장애물(sim.world obstacles 꼴)·
 *                             주행 계획 경로(코스 좌표, 수동·정지 중이면 빈 배열)
 * 값은 모두 합성이다. 실제 보드 측정값으로 쓰지 않는다.
 */
import http from 'node:http';
import {
  OBSTACLE_SEEDS,
  PATH_LENGTH,
  pathAhead,
  pathPose,
  obstacleAhead,
  SCENARIO_MARKS,
  TRACK_ID,
  VEHICLE,
} from '../src/display/course.ts';

const PORT = Number(process.env.FAKE_FEED_PORT || 8082);
const IDS = {
  primary: '00000000-0000-4000-8000-000000000001',
  backup: '00000000-0000-4000-8000-000000000002',
  interaction: '00000000-0000-4000-8000-000000000003',
};
const ARRIVE_PAUSE_S = 10;
const PARK_S = 8;
/** VM1 장애 뒤: 안전 정지 4초 → VM2 승계 준비 3초 → VM2 자율 */
const SAFE_STOP_S = 4;
const AUTO_READY_S = 3;
const SCENARIOS = ['cycle', 'cruise', 'safe-stop', 'auto-ready', 'failover-done', 'manual', 'feed-down'];
let scenario = process.env.FAKE_FEED_SCENARIO || 'cycle';
let sequence = 0;
const started = Date.now();
const epoch = `PREVIEW-${started}`;
/** 시연 경로 위 진행: s = 경로 거리 (m), v = 속도, crashAt = VM1 장애 시각, resumeAt = VM2 출발 시각 */
let run = fresh_run(started, PARK_S * 1000);

function fresh_run(now, holdMs) {
  return {
    s: 0,
    v: 0,
    at: now,
    driving_s: 0,
    parkedUntil: now + holdMs,
    crashAt: null,
    arrivedAt: null,
  };
}

const fresh = (age = 120) => ({ status: 'fresh', age_ms: age });
const stale = (age) => ({ status: 'stale', age_ms: age });
const unknown = () => ({ status: 'unknown', age_ms: null });
const round = (v, d = 3) => Math.round(v * 10 ** d) / 10 ** d;

/** 지금 단계 이름 (cycle 이면 경로 위치·장애 시각으로, 아니면 고정 시나리오) */
function phaseAt(now) {
  if (scenario !== 'cycle') return { name: scenario, t: (now - started) / 1000 };
  if (run.crashAt !== null) {
    const since = (now - run.crashAt) / 1000;
    if (since < SAFE_STOP_S) return { name: 'safe-stop', t: since };
    if (since < SAFE_STOP_S + AUTO_READY_S) return { name: 'auto-ready', t: since - SAFE_STOP_S };
    return { name: 'failover-done', t: since - SAFE_STOP_S - AUTO_READY_S };
  }
  if (run.s >= SCENARIO_MARKS.manualOn && run.s < SCENARIO_MARKS.manualOff) return { name: 'manual', t: 0 };
  return { name: 'cruise', t: 0 };
}

function lifecycle(phase, readiness, generation, progress, reason = null) {
  return {
    profile: 'warm-drive-1',
    applied_generation: generation,
    check_seq: String(Math.floor((Date.now() - started) / 100) + 1),
    progress_seq: String(progress),
    phase,
    readiness,
    reason,
  };
}

function app({ heartbeat, lifecycleReport, command = null, perception = null, source = fresh() }) {
  return { heartbeat, lifecycle: lifecycleReport, command, perception, source };
}

/** 시연 경로를 따라 한 걸음: 단계별 목표 속도로 가감속, 끝(P1)에 닿으면 쉬었다가 처음부터 */
function advance(now) {
  const dt = Math.max(0, Math.min(2, (now - run.at) / 1000));
  run.at = now;
  if (now < run.parkedUntil) {
    run.v = 0;
    return;
  }
  if (run.arrivedAt !== null) {
    run.v = 0;
    if (now - run.arrivedAt > ARRIVE_PAUSE_S * 1000) run = fresh_run(now, PARK_S * 1000);
    return;
  }
  if (scenario === 'cycle' && run.crashAt === null && run.s >= SCENARIO_MARKS.crash) run.crashAt = now;
  const { name, t: local } = phaseAt(now);
  const wave = Math.sin((now - started) / 2000);
  let target =
    name === 'safe-stop' || name === 'auto-ready'
      ? 0
      : name === 'manual'
        ? 0.26 + 0.03 * wave
        : 0.32 + 0.03 * wave;
  // VM2 승계 뒤에는 천천히 다시 출발, 주차 구간은 0.2 m/s, 끝에서는 0.25 m/s² 로 멈춘다
  if (name === 'failover-done') target = Math.min(target, 0.08 + local * 0.15);
  if (run.s >= SCENARIO_MARKS.park) target = Math.min(target, 0.2);
  target = Math.min(target, Math.sqrt(2 * 0.25 * Math.max(0, PATH_LENGTH - run.s)));
  const accel = target > run.v ? 0.5 : name === 'safe-stop' ? 2.0 : 0.8;
  run.v += Math.max(-accel * dt, Math.min(accel * dt, target - run.v));
  if (run.v < 0.005 && target < 0.005) run.v = 0;
  run.s = Math.min(PATH_LENGTH, run.s + run.v * dt);
  if (run.v > 0.02) run.driving_s += dt;
  if (PATH_LENGTH - run.s < 0.003) {
    run.s = PATH_LENGTH;
    run.v = 0;
    run.arrivedAt ??= now;
  }
}

function build(now) {
  advance(now);
  const t = (now - started) / 1000;
  const { name, t: local } = phaseAt(now);
  const uptime = Math.floor(t) + 600;
  const progress = Math.floor(t * 50);

  // 기본: VM1 이 AUTO 로 주행 중
  let mode = 'AUTO';
  let generation = 4;
  let selected = IDS.primary;
  let phase = 'APPLIED';
  let permitted = true;
  let active = IDS.primary;
  let reason = 'start';
  let flags = { estop: false, watchdog: false, obstacle: false };
  let primaryXen = 'running';
  let primaryAlive = true;
  let backupState = { phase: 'WAITING', readiness: 'QUALIFIED', progress: 0 };
  let driver = 'autonomous';

  if (name === 'safe-stop') {
    mode = 'SAFE_STOP';
    generation = 5;
    permitted = false;
    active = null;
    reason = 'command_timeout';
    flags = { ...flags, watchdog: true };
    primaryXen = 'crashed';
    primaryAlive = false;
  } else if (name === 'auto-ready') {
    mode = 'AUTO_READY';
    generation = 6;
    selected = IDS.backup;
    phase = 'PENDING';
    permitted = false;
    active = null;
    reason = 'no_fresh_standby';
    primaryAlive = false;
    backupState = { phase: 'ACTIVATING', readiness: 'QUALIFIED', progress: 0 };
  } else if (name === 'failover-done') {
    generation = 7;
    selected = IDS.backup;
    active = IDS.backup;
    reason = 'resume_requested';
    backupState = { phase: 'EXECUTING', readiness: 'READY', progress: Math.floor(local * 50) };
  } else if (name === 'manual') {
    mode = 'MANUAL';
    generation = 8;
    driver = 'manual';
  }

  // ── 코스 위 위치·속도·조향 ──
  const parked = now < run.parkedUntil;
  const speed = run.v;
  const pose = pathPose(run.s);
  // 조향: 경로 곡률 × 축거 (자전거 모델)
  const ahead = pathPose(Math.min(PATH_LENGTH, run.s + 0.03));
  const behind = pathPose(Math.max(0, run.s - 0.03));
  const dyaw = Math.atan2(Math.sin(ahead.yaw - behind.yaw), Math.cos(ahead.yaw - behind.yaw));
  const steer = speed > 0 ? Math.atan((VEHICLE.wheelbase * dyaw) / 0.06) : 0;
  const cone = obstacleAhead(pose, OBSTACLE_SEEDS);
  const perception = cone
    ? { kind: 'obstacle', detail: `콘 ${cone.obstacle.id}`, age_ms: 120 }
    : { kind: 'clear', detail: null, age_ms: 300 };
  // 계획 경로: 자율 앱이 운전 중일 때만 (수동·정지·정차 중에는 없음)
  const planning = !parked && run.arrivedAt === null && (name === 'cruise' || name === 'failover-done');

  const drivingVm = active ?? null;
  const command = (vm, which) =>
    drivingVm === vm && driver === which
      ? { generation, v_mps: round(speed), steer_rad: round(steer), age_ms: 20 }
      : null;

  const primaryApps = primaryAlive
    ? {
        autonomous: app({
          heartbeat: {
            state: drivingVm === IDS.primary && driver === 'autonomous' ? 'active' : 'standby',
            uptime_s: uptime,
          },
          lifecycleReport:
            drivingVm === IDS.primary && driver === 'autonomous'
              ? lifecycle('EXECUTING', 'READY', generation, progress)
              : lifecycle('WAITING', 'QUALIFIED', generation, 0),
          command: command(IDS.primary, 'autonomous'),
          perception: drivingVm === IDS.primary ? perception : null,
        }),
        manual: app({
          heartbeat: {
            state: drivingVm === IDS.primary && driver === 'manual' ? 'active' : 'standby',
            uptime_s: uptime,
          },
          lifecycleReport:
            drivingVm === IDS.primary && driver === 'manual'
              ? lifecycle('EXECUTING', 'READY', generation, progress)
              : lifecycle('WAITING', 'QUALIFIED', generation, 0),
          command: command(IDS.primary, 'manual'),
        }),
      }
    : {
        autonomous: app({
          heartbeat: null,
          lifecycleReport: null,
          source: stale(Math.floor(local * 1000) + 300),
        }),
        manual: app({
          heartbeat: null,
          lifecycleReport: null,
          source: stale(Math.floor(local * 1000) + 300),
        }),
      };
  const backupApps = {
    autonomous: app({
      heartbeat: { state: drivingVm === IDS.backup ? 'active' : 'standby', uptime_s: uptime - 120 },
      lifecycleReport: lifecycle(backupState.phase, backupState.readiness, generation, backupState.progress),
      command: command(IDS.backup, 'autonomous'),
      perception: drivingVm === IDS.backup ? perception : null,
    }),
    manual: app({
      heartbeat: { state: 'standby', uptime_s: uptime - 120 },
      lifecycleReport: lifecycle('WAITING', 'QUALIFIED', generation, 0),
    }),
  };

  const vm = (id, role, i, xen, expected, appState, agent) => ({
    vm_id: id,
    role,
    run_id: `preview-run-${i}`,
    xen_state: xen,
    app_state: appState,
    agent_supported: agent,
    // 실제 수집기: current_run_verified 는 늘 false, inventory(에이전트)가 없으면 observed=null (display-pipeline-v1 §os)
    os: { expected, observed: agent ? expected : null, current_run_verified: false },
    sources: {
      xen: fresh(200),
      app: appState === 'ready' ? fresh(300) : stale(4000),
      agent: agent ? fresh(1000) : unknown(),
      inventory: agent ? fresh(1000) : unknown(),
    },
    screen_source: { kind: 'status', handle: null },
  });
  // 실제 수집기는 Xen 이 running 이 아니면 앱 상태를 판단하지 않는다 (state.py: unknown)
  const primaryState = primaryAlive ? 'ready' : 'unknown';
  const vms = [
    vm(IDS.primary, 'primary_drive', 1, primaryXen, 'zephyr', primaryState, false),
    vm(IDS.backup, 'backup_drive', 2, 'running', 'zephyr', 'ready', false),
    vm(IDS.interaction, 'interaction', 3, 'running', 'debian', 'ready', true),
  ];
  // 수집기 policy.decide_vm 과 같은 kind·문구 (Xen 상태가 먼저, 그다음 앱 상태)
  const decision = (v) =>
    v.xen_state === 'crashed'
      ? { kind: 'crashed', label: 'VM 장애', tone: 'danger' }
      : v.app_state === 'ready'
        ? { kind: 'status', label: '앱 상태 수신 정상', tone: 'ok' }
        : v.app_state === 'failed'
          ? { kind: 'app_error', label: '앱 오류 · VM 실행 중', tone: 'danger' }
          : { kind: 'app_waiting', label: 'VM 실행 중 · 앱 상태 미확인', tone: 'warning' };

  sequence += 1;
  return {
    schema_version: 1,
    type: 'display.presentation',
    policy_epoch: epoch,
    revision: 1,
    render_revision: 'preview',
    outputs: [
      { output_id: 'monitor-1', view: 'overview', screen: 'instrument', selection: 'pinned', vm_id: null },
      {
        output_id: 'monitor-2',
        view: 'detail',
        screen: 'infotainment',
        selection: 'pinned',
        vm_id: IDS.interaction,
      },
    ],
    decisions: Object.fromEntries(vms.map((v) => [v.vm_id, decision(v)])),
    state: {
      source_epoch: epoch,
      sequence,
      generated_at: new Date(now).toISOString(),
      mqtt_connected: true,
      vms,
      drive: {
        active_vm_id: active,
        mode,
        ai_status: 'unknown',
        source: fresh(80),
        authority: {
          selected_vm_id: selected,
          selected_mode: mode,
          generation,
          phase,
          output_permitted: permitted,
          storage_uncertain: false,
        },
      },
      // ── 제안 확장 (현재 수집기 미지원) ──
      scenario: parked ? 'parked' : name,
      vehicle: {
        applied_generation: generation,
        v_mps: round(speed),
        steer_rad: round(steer),
        flags,
        batt_v: round(Math.max(7.3, 8.05 - (t / 3600) * 0.25), 2),
        source: fresh(50),
      },
      drive_apps: { [IDS.primary]: primaryApps, [IDS.backup]: backupApps },
      drive_limits: { profile: 'lattice-lab-1', max_v_mps: 0.5 },
      trip: {
        distance_m: round(run.s, 4),
        driving_s: Math.round(run.driving_s),
        source: fresh(100),
      },
      course: {
        track_id: TRACK_ID,
        pose: { x: round(pose.x, 4), y: round(pose.y, 4), yaw: round(pose.yaw, 4) },
        obstacles: OBSTACLE_SEEDS,
        plan: planning ? pathAhead(run.s) : [],
        source: fresh(60),
      },
    },
  };
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://local');
  const send = (status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(body === undefined ? '' : JSON.stringify(body));
  };
  if (url.pathname === '/go') {
    // 정차 화면에서 바로 출발
    run.parkedUntil = Date.now();
    console.log('[fake-feed] go → 출발');
    return send(200, { departed: true });
  }
  if (url.pathname === '/restart' || url.pathname === '/park') {
    // 처음부터: 출발선에서 정차(인트로 화면) → PARK_S 초 뒤 출발 (/park 는 /go 할 때까지 정차 화면에 머문다)
    const hold = url.pathname === '/park';
    run = fresh_run(Date.now(), hold ? Number.POSITIVE_INFINITY : PARK_S * 1000);
    console.log(`[fake-feed] ${hold ? 'park' : 'restart'} → 출발선에서 정차`);
    return send(200, { restarted: true, depart_in_s: hold ? null : PARK_S });
  }
  if (url.pathname.startsWith('/jump/')) {
    const to = Number(url.pathname.split('/')[2]);
    if (!Number.isFinite(to) || to < 0) return send(400, { error: 'jump/<경로 m>' });
    setTripForTest(to);
    console.log(`[fake-feed] jump → ${to} m`);
    return send(200, { jumped_to_path_m: to, path_m: Math.round(PATH_LENGTH * 100) / 100 });
  }
  if (url.pathname.startsWith('/scenario')) {
    const next = url.pathname.split('/')[2];
    if (next) {
      if (!SCENARIOS.includes(next)) return send(400, { error: `unknown scenario`, scenarios: SCENARIOS });
      scenario = next;
      console.log(`[fake-feed] scenario → ${scenario}`);
    }
    return send(200, { scenario, scenarios: SCENARIOS });
  }
  if (req.method === 'GET' && url.pathname === '/api/v1/display/presentation') {
    if (scenario === 'feed-down')
      return send(503, { code: 'FEED_UNAVAILABLE', message: '미리보기: 수집기 끊김' });
    return send(200, build(Date.now()));
  }
  if (req.method === 'POST' && /^\/api\/v1\/display\/outputs\/monitor-[12]\/ack$/.test(url.pathname)) {
    req.resume();
    return send(204);
  }
  send(404, { code: 'NOT_FOUND' });
});

export { build, SCENARIOS, IDS };
export function setScenario(name) {
  scenario = name;
}
/**
 * 시험용: 시연 경로 위 s (m) 로 옮기고 정차를 푼다. VM1 장애 지점을 지났으면 장애는 오래전 일로 둔다 (VM2 운전 중)
 */
export function setTripForTest(pathM, now = Date.now()) {
  run = {
    ...fresh_run(now, 0),
    s: Math.min(PATH_LENGTH, pathM),
    v: 0.3,
    crashAt: pathM >= SCENARIO_MARKS.crash ? now - 60_000 : null,
  };
}

if (process.argv[1]?.endsWith('fake-display-feed.mjs')) {
  server.listen(PORT, '127.0.0.1', () => {
    console.log(`[fake-feed] http://127.0.0.1:${PORT}/api/v1/display/presentation (scenario: ${scenario})`);
    console.log(`[fake-feed] 시나리오 바꾸기: http://127.0.0.1:${PORT}/scenario/<${SCENARIOS.join('|')}>`);
  });
}
