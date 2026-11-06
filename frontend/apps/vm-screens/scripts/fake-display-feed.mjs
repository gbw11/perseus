#!/usr/bin/env node
/**
 * 노트북 미리보기용 가짜 dom0 표시 수집기 (HDMI /display/* 화면 개발용, 보드 없이).
 * 실제 수집기(platform/services/display/router)와 같은 경로·규격으로 답한다:
 *   GET  /api/v1/display/presentation       현행 display.presentation (parsePresentation 통과)
 *   POST /api/v1/display/outputs/:id/ack    204
 * 시나리오:
 *   GET  /scenario                          현재 시나리오와 목록
 *   GET  /scenario/<name>                   고정 (cycle 이면 자동 순환)
 *   GET  /jump/<지도 m>                      경로 위 그 지점으로 건너뛰기 (예: /jump/3600 = 집 220 m 앞)
 *   GET  /restart                           처음부터 (회사에서 정차 화면 → 8초 뒤 출발)
 *   GET  /park · /go                        처음 정차 화면에 머무르기 · 거기서 출발
 *
 * 주행은 내비 경로(src/display/cityMap.ts, 회사 → 집)를 그대로 따른다: 출발 전 정차 → 사거리마다 신호(초록·노랑·빨강)에
 * 맞춰 정지·출발 → 회전 전 안내 차로로 이동 → 도착 후 잠시 쉬고 다시 출발.
 *
 * 제안 확장 필드 (현재 dom0 수집기는 아직 보내지 않는다 — 화면이 정해지면 수집기 요청 명세가 된다):
 *   state.vehicle             vehicle.status (MQTT 계약 5.4, MQTT-10) — 게이트웨이가 ESP32 실측을 변환
 *   state.drive_apps[vm_id]   Zephyr 주행 펌웨어의 앱별 보고
 *     .autonomous / .manual = { heartbeat (6.1), lifecycle (6.3 warm-drive-1), command (5.4 drive.command),
 *                               perception (perception.event, 자율만), source }
 *   state.drive_limits        주행 프로필 한도 (apps/drive/profiles/lattice-lab-1.json max_velocity 0.5 m/s)
 *   state.trip                트립 거리·주행 시간 (vehicle.status 속도 적분)
 *   state.road                지금 도로: 한 방향 차로 수, 내 차로(1 = 중앙선 옆 1차로, 소수 = 차로 변경 중), 다음 사거리
 *   state.world.objects       주변 물체 (인지 결과, 차 기준 x_m 왼쪽-·오른쪽+, y_m 앞)
 *                             — 차(heading same|oncoming|cross_left|cross_right), 신호등(state red|yellow|green, 사거리에만)
 *                             road.intersection.turn = 경로가 그 사거리에서 꺾는 방향(left|right, 직진 null)
 * 값은 모두 합성이다. 실제 보드 측정값으로 쓰지 않는다.
 */
import http from 'node:http';
import {
  laneGuide,
  lanesOf,
  nextIntersection,
  passedTurn,
  ROUTE,
  ROUTE_LENGTH,
  ROUTE_SCALE,
  routePosition,
  INTERSECTIONS,
  stopLineBefore,
  turnAngle,
} from '../src/display/cityMap.ts';

const PORT = Number(process.env.FAKE_FEED_PORT || 8082);
const IDS = {
  primary: '00000000-0000-4000-8000-000000000001',
  backup: '00000000-0000-4000-8000-000000000002',
  interaction: '00000000-0000-4000-8000-000000000003',
};
/** cycle: 0~44초 VM1 로 주행, 44~48 VM1 장애(안전 정지), 48~52 VM2 승계 준비, 52~70 VM2 로 주행 */
const CYCLE_S = 70;
const ROUTE_RC_M = ROUTE_LENGTH / ROUTE_SCALE;
const ARRIVE_PAUSE_S = 8;
const PARK_S = 8;
/** RC 트랙 차로 폭 (m) — 화면 장면과 같다 */
const LANE_M = 0.38;
const SIGNAL_PERIOD_S = 24;
const SCENARIOS = ['cycle', 'cruise', 'safe-stop', 'auto-ready', 'failover-done', 'manual', 'feed-down'];
let scenario = process.env.FAKE_FEED_SCENARIO || 'cycle';
let sequence = 0;
const started = Date.now();
const epoch = `PREVIEW-${started}`;
let odometer = {
  distance_m: 0,
  at: started,
  driving_s: 0,
  arrivedAt: null,
  parkedUntil: started + PARK_S * 1000,
};
let lane = 2;
/** 지금 달리는 경로 구간 (바뀌면 새 도로에 들어선 것) */
let roadSegment = 0;
/** 차로를 마지막으로 옮긴 시각 (차로 변경은 시간 기준) */
let laneAt = Date.now();
/** 주변 차 상대 위치 (id → 내 차 앞 범퍼 기준 RC m) */
const traffic = new Map();
/** 차 길이 (RC m, 화면 차 모델과 같다) */
const CAR_LEN_M = 0.46;
/** 추월 시연: 내 차로 앞에 가끔 나타나는 느린 차 { lane: 실제 차로, y: 내 차 앞 범퍼 기준 RC m, base·v: 속도 } */
let slow = null;
let slowCooldownUntil = started + (PARK_S + 15) * 1000;
/** 느린 차를 앞지르려고 옮겨 간 차로 (없으면 null) */
let overtakeLane = null;

const fresh = (age = 120) => ({ status: 'fresh', age_ms: age });
const stale = (age) => ({ status: 'stale', age_ms: age });
const unknown = () => ({ status: 'unknown', age_ms: null });
const round = (v, d = 3) => Math.round(v * 10 ** d) / 10 ** d;

function phaseAt(t) {
  if (scenario !== 'cycle') return { name: scenario, t: t % CYCLE_S };
  const s = t % CYCLE_S;
  if (s < 44) return { name: 'cruise', t: s };
  if (s < 48) return { name: 'safe-stop', t: s - 44 };
  if (s < 52) return { name: 'auto-ready', t: s - 48 };
  return { name: 'failover-done', t: s - 52 };
}

/** 사거리 신호: 사거리마다 시작 시점을 달리한 24초 주기 (초록 12 · 노랑 2 · 빨강 10) */
function signalAt(index, t) {
  const p = (t + index * 9) % SIGNAL_PERIOD_S;
  return p < 12 ? 'green' : p < 14 ? 'yellow' : 'red';
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

function build(now) {
  const t = (now - started) / 1000;
  const { name, t: local } = phaseAt(t);
  const uptime = Math.floor(t) + 600;
  const progress = Math.floor(t * 50);
  const wave = Math.sin(t / 2);

  // 기본: VM1 이 AUTO 로 주행 중
  let mode = 'AUTO';
  let generation = 4;
  let selected = IDS.primary;
  let phase = 'APPLIED';
  let permitted = true;
  let active = IDS.primary;
  let reason = 'start';
  let cruise = 0.42 + 0.04 * wave;
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
    cruise = 0;
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
    cruise = 0;
    primaryAlive = false;
    backupState = { phase: 'ACTIVATING', readiness: 'QUALIFIED', progress: 0 };
  } else if (name === 'failover-done') {
    generation = 7;
    selected = IDS.backup;
    active = IDS.backup;
    reason = 'resume_requested';
    cruise = Math.min(cruise, local * 0.2);
    backupState = { phase: 'EXECUTING', readiness: 'READY', progress: Math.floor(local * 50) };
  } else if (name === 'manual') {
    mode = 'MANUAL';
    generation = 8;
    driver = 'manual';
    cruise = 0.3 + 0.03 * wave;
  }

  // ── 경로 위 위치·사거리·신호 ──
  const mapD = odometer.distance_m * ROUTE_SCALE;
  const pos = routePosition(mapD);
  const guide = laneGuide(pos);
  const lanes = lanesOf(ROUTE[pos.segment].road);
  const inter = nextIntersection(mapD);
  const interIndex = inter ? INTERSECTIONS.findIndex((o) => o.at === inter.at) : -1;
  const light = inter ? signalAt(interIndex, t) : null;
  // 정지선 (장면이 그리는 정지선과 같은 곳: 가로 도로 반폭 + 횡단보도 앞)
  const stopRc = inter ? (inter.inM - stopLineBefore(inter)) / ROUTE_SCALE : Infinity;
  const parked = now < odometer.parkedUntil;
  // 회전 교차로를 막 지났으면 지나온 도로가 뒤에 가로로 남는다 (회전하며 도는 장면이 끊기지 않게)
  const passed = passedTurn(mapD);
  // 회전으로 새 도로에 들어서면 원호가 닿는 차로에서 시작한다 (우회전 = 맨 바깥 차로, 좌회전 = 1차로).
  // 옛 도로의 주변 차·느린 차는 지운다 (새 도로에서 다시 나타난다)
  if (pos.segment !== roadSegment) {
    if (passed?.turn === 'right') lane = lanes;
    else if (passed?.turn === 'left') lane = 1;
    else lane = Math.max(1, Math.min(lane, lanes));
    if (passed) {
      traffic.clear();
      slow = null;
      overtakeLane = null;
    }
    roadSegment = pos.segment;
  }

  // 속도: 정차 중이면 0, 빨간·노란불이면 정지선 앞에서 멈춘다 (노란불에 이미 정지선 가까우면 통과)
  let speed = parked || odometer.arrivedAt ? 0 : cruise;
  const mustStop = light === 'red' || (light === 'yellow' && stopRc > 0.12);
  if (mustStop && stopRc > -0.02 && stopRc < 1.4) speed = Math.min(speed, Math.max(0, 0.6 * (stopRc - 0.02)));
  // 앞 느린 차: 같은 차로에 있으면 간격(0.35 m)을 두고 그 차 속도에 맞춘다 (추월 차로로 옮기기 전까지)
  if (slow && Math.abs(lane - slow.lane) < 0.6 && slow.y > -0.3)
    speed = Math.min(speed, Math.max(0, slow.v + (slow.y - 0.35)));
  if (speed < 0.01) speed = 0;

  // 추월: 느린 차가 1.2 m 안으로 들어오고 옆 차로(왼쪽 먼저)에 가까운 차가 없으면 그 차로로 옮긴다.
  // 다 지나가면(느린 차가 내 뒤 0.7 m) 원래 차로로 돌아온다. 회전이 가까우면(지도 250 m) 회전 차로가 먼저
  const turnSoon = guide !== null && guide.inM < 250;
  // 추월은 회전까지 지도 400 m 넘게 남았을 때만 시작한다 (끝나기 전에 회전 차로로 급히 돌아가지 않게)
  const roomToOvertake = guide === null || guide.inM > 400;
  if (slow && overtakeLane === null && roomToOvertake && speed > 0.05 && slow.y > 0 && slow.y < 1.2) {
    const free = [slow.lane - 1, slow.lane + 1].find(
      (a) => a >= 1 && a <= lanes && Math.abs(traffic.get(`same-lane${a}`) ?? 99) > 1.0,
    );
    if (free !== undefined) overtakeLane = free;
  }
  if (overtakeLane !== null && (!slow || slow.y < -0.7 || turnSoon || overtakeLane > lanes))
    overtakeLane = null;

  // 차로: 추월 중이면 추월 차로, 회전 안내가 있으면 그 차로, 아니면 2차로 (골목이면 바깥 차로). 1초에 반 차로씩 옮긴다.
  // 회전 원호를 도는 동안과 회전 직후(지도 20 m)는 차로를 바꾸지 않는다 (돌면서 옆으로 미끄러지지 않게)
  const guideLane = guide ? (guide.turn === 'left' ? 1 : lanes) : Math.min(2, lanes);
  const angle = turnAngle(mapD);
  const onArc = Math.abs(angle) > 0 && Math.abs(angle) < 90;
  const settling = passed !== null && passed.behindM < 20;
  const targetLane = onArc || settling ? Math.round(lane) : (overtakeLane ?? guideLane);
  // 시간 기준 (초당 반 차로): 화면이 여럿 붙어 자주 물어도 빨라지지 않는다
  const laneStep = 0.5 * Math.max(0, Math.min(2, (now - laneAt) / 1000));
  laneAt = now;
  lane = Math.max(1, Math.min(lanes, lane + Math.max(-laneStep, Math.min(laneStep, targetLane - lane))));
  // 조향: 사거리 회전 원호를 도는 동안 크게, 차로 변경 중에는 그쪽으로 조금
  const turning = onArc ? (angle < 0 ? 0.32 : -0.32) : 0;
  const steer = speed > 0 ? turning || Math.sign(lane - targetLane) * 0.08 || 0.02 * wave : 0;
  const perception = light
    ? {
        kind: 'traffic_light',
        detail: light === 'red' ? '빨간불' : light === 'yellow' ? '노란불' : '초록불',
        age_ms: 120,
      }
    : { kind: 'clear', detail: null, age_ms: 300 };

  const drivingVm = active ?? null;
  const command = (vm, which) =>
    drivingVm === vm && driver === which
      ? { generation, v_mps: round(speed > 0 ? cruise : 0), steer_rad: round(steer), age_ms: 20 }
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
    os: { expected, observed: expected, current_run_verified: true },
    sources: {
      xen: fresh(200),
      app: appState === 'ready' ? fresh(300) : stale(4000),
      agent: agent ? fresh(1000) : unknown(),
      inventory: agent ? fresh(1000) : unknown(),
    },
    screen_source: { kind: 'status', handle: null },
  });
  const primaryState = primaryAlive ? 'ready' : primaryXen === 'crashed' ? 'failed' : 'unknown';
  const vms = [
    vm(IDS.primary, 'primary_drive', 1, primaryXen, 'zephyr', primaryState, false),
    vm(IDS.backup, 'backup_drive', 2, 'running', 'zephyr', 'ready', false),
    vm(IDS.interaction, 'interaction', 3, 'running', 'debian', 'ready', true),
  ];
  const decision = (state) =>
    state === 'ready'
      ? { kind: 'app_ready', label: '앱 상태 수신 정상', tone: 'ok' }
      : state === 'failed'
        ? { kind: 'app_failed', label: '앱 오류', tone: 'danger' }
        : { kind: 'app_waiting', label: 'VM 실행 중 · 앱 상태 미확인', tone: 'warning' };

  // 트립: 속도 적분. 도착하면 잠시 쉬고 0 으로 돌린 뒤 출발 전 정차부터 다시
  const dt = Math.max(0, Math.min(2, (now - odometer.at) / 1000));
  odometer = {
    ...odometer,
    distance_m: Math.min(ROUTE_RC_M, odometer.distance_m + speed * dt),
    at: now,
    driving_s: odometer.driving_s + (speed > 0.02 ? dt : 0),
  };
  if (odometer.distance_m >= ROUTE_RC_M) {
    odometer.arrivedAt ??= now;
    if (now - odometer.arrivedAt > ARRIVE_PAUSE_S * 1000)
      odometer = { distance_m: 0, at: now, driving_s: 0, arrivedAt: null, parkedUntil: now + PARK_S * 1000 };
  }

  // ── 주변 물체 (인지 결과) ──
  // 다른 차는 각자 상대 위치를 기억하고 자기 속도로 움직인다. 빨간·노란불이면 같은 방향 차는 내 쪽 정지선 앞에,
  // 마주 오는 차는 사거리 건너편 정지선 앞에 선다 (같은 신호를 받는다)
  const objects = [];
  const othersStop = light === 'red' || light === 'yellow';
  const farStopRc = inter ? (inter.inM + stopLineBefore(inter)) / ROUTE_SCALE : Infinity;
  const here = Math.round(lane);
  for (const k of [-2, -1, 1, 2]) {
    const a = here + k;
    const x = round((a - lane) * LANE_M);
    // 느린 차가 있는 차로는 느린 차만 (겹치지 않게)
    if (slow && slow.lane === a) continue;
    if (a >= 1 && a <= lanes) {
      // 같은 방향 차: 나보다 조금 빠르게 가다가 빨간불이면 정지선(차 앞이 정지선)에 맞춰 선다
      // id 는 실제 차로 번호로 (내가 차로를 바꿔도 같은 차가 같은 차로에 남는다)
      const id = `same-lane${a}`;
      let y = traffic.get(id) ?? -1.2 + (((k + 3) * 0.83) % 3.4);
      let v = cruise * 1.08;
      const room = stopRc - CAR_LEN_M - 0.02 - y;
      if (othersStop && room > -0.05) v = Math.min(v, Math.max(0, 1.2 * room));
      y += (v - speed) * dt;
      if (y > 2.2) y = -1.2;
      else if (y < -1.25) y = 2.2;
      traffic.set(id, y);
      objects.push({ id, kind: 'car', heading: 'same', x_m: x, y_m: round(y) });
    } else if (a <= 0 && 1 - a <= lanes) {
      // 반대 차로 차: 마주 오다가 빨간불이면 건너편 정지선(차 앞이 정지선)에 선다
      const id = `oncoming-lane${1 - a}`;
      let y = traffic.get(id) ?? 3.2 - (((k + 3) * 1.1) % 4.4);
      let v = 0.32;
      const room = y - farStopRc;
      if (othersStop && room > -0.05) v = Math.min(v, Math.max(0, 1.2 * room));
      y -= (v + speed) * dt;
      if (y < -1.25) y = 3.2;
      traffic.set(id, y);
      objects.push({ id, kind: 'car', heading: 'oncoming', x_m: x, y_m: round(y) });
    }
  }
  // 추월 시연용 느린 차: 회전이 가깝지 않고 사거리가 멀면 가끔 내 차로 앞에 나타나 내 속도의 40% 로 간다.
  // 신호는 다른 차와 같이 지킨다. 뒤로 멀어지거나(추월 끝) 앞으로 멀어지면 사라지고 12초 뒤 다시 나타날 수 있다
  if (slow) {
    const room = stopRc - CAR_LEN_M - 0.02 - slow.y;
    slow.v = othersStop && room > -0.05 ? Math.min(slow.base, Math.max(0, 1.2 * room)) : slow.base;
    slow.y += (slow.v - speed) * dt;
    if (slow.y < -1.3 || slow.y > 2.4 || slow.lane > lanes) {
      slow = null;
      slowCooldownUntil = now + 12_000;
    }
  } else if (
    now > slowCooldownUntil &&
    speed > 0.1 &&
    lanes >= 2 &&
    roomToOvertake &&
    !passed &&
    (!inter || inter.inM > 120) &&
    Math.abs(lane - Math.round(lane)) < 0.01
  ) {
    slow = { lane: Math.round(lane), y: 1.8, base: cruise * 0.4, v: cruise * 0.4 };
  }
  if (slow) {
    objects.push({
      id: 'slow',
      kind: 'car',
      heading: 'same',
      x_m: round((slow.lane - lane) * LANE_M),
      y_m: round(slow.y),
    });
  }
  if (inter && inter.inM < 330) {
    const yi = inter.inM / ROUTE_SCALE;
    objects.push({
      id: `signal-${interIndex}`,
      kind: 'traffic_light',
      x_m: 0,
      y_m: round(yi + 0.14),
      state: light,
    });
    // 가로지르는 차: 내 신호가 빨간불이면 교차 도로가 초록불이라 지나간다, 아니면 들어오는 쪽 정지선에 서 있다.
    // 우측 통행: 오른쪽으로 가는 차는 가까운 쪽 차로, 왼쪽으로 가는 차는 먼 쪽 차로
    for (const [i, dir] of [
      [0, 1],
      [1, -1],
    ]) {
      const moving = light === 'red';
      const progress = moving ? ((t * 0.55 + i * 1.6) % 3.4) - 1.7 : -(1.05 + i * 0.25);
      objects.push({
        id: `cross${i}`,
        kind: 'car',
        heading: dir > 0 ? 'cross_right' : 'cross_left',
        x_m: round(dir * progress),
        y_m: round(yi + (dir > 0 ? -0.09 : 0.09)),
      });
    }
  }

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
    decisions: Object.fromEntries(vms.map((v) => [v.vm_id, decision(v.app_state)])),
    state: {
      source_epoch: epoch,
      sequence,
      generated_at: new Date(now).toISOString(),
      mqtt_connected: true,
      vms,
      drive: {
        active_vm_id: active,
        mode,
        ai_status: 'ok',
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
        // 도로·주변 물체를 만든 위치와 같은 시점 (화면이 이 거리로 회전 교차로를 고른다)
        distance_m: round(mapD / ROUTE_SCALE, 4),
        driving_s: Math.round(odometer.driving_s),
        source: fresh(100),
      },
      road: {
        name: ROUTE[pos.segment].road,
        lanes,
        lane: round(lane, 2),
        oncoming_lanes: lanes,
        // 주행 계획: 옮겨 갈 차로. 추월 중이면 느린 차를 지나 돌아올 차로와, 돌아오기 시작할 곳(내가 그만큼 더 달린 뒤:
        // 느린 차가 내 뒤 0.7 m 가 될 때까지 달릴 거리)
        plan: {
          lane: targetLane,
          then_lane: overtakeLane !== null && slow ? guideLane : null,
          then_after_m:
            overtakeLane !== null && slow
              ? round(Math.max(0, ((slow.y + 0.7) * Math.max(speed, 0.05)) / Math.max(0.05, speed - slow.v)))
              : null,
        },
        intersection: passed
          ? {
              y_m: round(-passed.behindM / ROUTE_SCALE),
              cross_road: passed.fromRoad,
              cross_lanes: lanesOf(passed.fromRoad),
              turn: passed.turn,
            }
          : inter && inter.inM < 330
            ? {
                y_m: round(inter.inM / ROUTE_SCALE),
                cross_road: inter.crossRoad,
                cross_lanes: inter.crossLanes,
                turn: inter.turn,
              }
            : null,
        source: fresh(100),
      },
      world: { objects, source: name === 'safe-stop' ? stale(1500) : fresh(70) },
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
    odometer = { ...odometer, parkedUntil: Date.now() };
    slowCooldownUntil = Date.now() + 15_000;
    console.log('[fake-feed] go → 출발');
    return send(200, { departed: true });
  }
  if (url.pathname === '/restart' || url.pathname === '/park') {
    // 처음부터: 회사에서 정차(인트로 화면) → PARK_S 초 뒤 출발 (/park 는 /go 할 때까지 정차 화면에 머문다).
    // 주변 차·추월도 처음 상태로
    const at = Date.now();
    const hold = url.pathname === '/park';
    odometer = {
      distance_m: 0,
      at,
      driving_s: 0,
      arrivedAt: null,
      parkedUntil: hold ? Number.POSITIVE_INFINITY : at + PARK_S * 1000,
    };
    lane = 2;
    roadSegment = 0;
    traffic.clear();
    slow = null;
    // 출발 장면이 깔끔하게: 출발하고 잠시는 느린 차가 나오지 않는다
    slowCooldownUntil = at + (PARK_S + 15) * 1000;
    overtakeLane = null;
    console.log(`[fake-feed] ${hold ? 'park' : 'restart'} → 회사에서 정차`);
    return send(200, { restarted: true, depart_in_s: hold ? null : PARK_S });
  }
  if (url.pathname.startsWith('/jump/')) {
    const to = Number(url.pathname.split('/')[2]);
    if (!Number.isFinite(to) || to < 0) return send(400, { error: 'jump/<지도 m>' });
    setTripForTest(to);
    console.log(`[fake-feed] jump → ${to} m`);
    return send(200, { jumped_to_map_m: to });
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
/** 시험용: 트립을 경로 위 지도 거리로 옮기고 정차를 푼다 */
export function setTripForTest(mapDistance, now = Date.now()) {
  laneAt = now;
  odometer = {
    distance_m: mapDistance / ROUTE_SCALE,
    at: now,
    driving_s: 0,
    arrivedAt: null,
    parkedUntil: 0,
  };
}

if (process.argv[1]?.endsWith('fake-display-feed.mjs')) {
  server.listen(PORT, '127.0.0.1', () => {
    console.log(`[fake-feed] http://127.0.0.1:${PORT}/api/v1/display/presentation (scenario: ${scenario})`);
    console.log(`[fake-feed] 시나리오 바꾸기: http://127.0.0.1:${PORT}/scenario/<${SCENARIOS.join('|')}>`);
  });
}
