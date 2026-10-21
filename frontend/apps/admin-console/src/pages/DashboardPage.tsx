import { CameraView, Card, StatusBadge } from '@xenova/ui';
import type { CSSProperties } from 'react';
import { Link } from 'react-router';
import type { DriveState } from '../api/drive';
import { useEvents, useVms } from '../api/queries';
import type { Vm } from '../api/types';
import { clock, describe } from '../domain/events';
import {
  ago,
  appHealth,
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
  vmOf,
  type AppId,
} from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

const MOCK = import.meta.env.VITE_MOCK === 'true';
/** 네비게이션 VM 이 내는 카메라 AI 감지 영상 (MJPEG). 주소·형식은 계약 미정 */
const CAMERA_URL: string = import.meta.env.VITE_CAMERA_URL ?? '';

/**
 * W-02 대시보드 — 주행 통합 상태 (화면 정의서 W-02, UI-09). 데모 내내 띄워 두는 화면.
 * "지금 누가 차를 몰고 있고, 넘길 준비가 됐는지" 를 한 화면에. 참고 시안: stitch_untitled_project/auto.
 */
export function DashboardPage() {
  const { drive } = useDrive();
  const vms = useVms();
  const events = useEvents({ limit: 5 });
  const control = drive?.control;
  const vehicle = drive?.vehicle;
  const flags = vehicle?.flags;
  const perception = drive?.perception;
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
          className={[styles.controlBar, control?.active_app ? styles.controlOn : ''].join(' ')}
          style={
            { '--accent': control?.active_app ? ROLES[control.active_app].color : undefined } as CSSProperties
          }
        >
          {control?.active_app ? `▶ ${ROLES[control.active_app].title} 차량 제어 중` : '차량 제어 앱 없음'}
        </div>
        <span className={styles.grow} />
        <div className={styles.chips}>
          {SAFETY.map((f) => (
            <span
              key={f.key}
              className={styles.chip}
              title={f.hint}
              style={flags?.[f.key] ? { borderColor: 'var(--x-fault)', color: 'var(--x-fault)' } : undefined}
            >
              {f.label}: {flags ? (flags[f.key] ? f.on : f.off) : '—'}
            </span>
          ))}
        </div>
      </section>

      {control?.mode === 'AUTO_READY' && (
        <div className={styles.draft}>
          자율주행 재개 가능 — <Link to="/demo">시연 제어</Link>에서 [자율주행 재개]
        </div>
      )}

      <div className={styles.cols3}>
        {ROLE_ORDER.map((app) => (
          <AppCard key={app} app={app} vm={vmOf(app, vms.data?.items, drive)} drive={drive} />
        ))}
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
                <span style={flags?.[f.key] ? { color: 'var(--x-fault)' } : undefined}>
                  {flags ? (flags[f.key] ? f.on : f.off) : '—'}
                </span>
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

function AppCard({ app, vm, drive }: { app: AppId; vm: Vm | undefined; drive: DriveState | null }) {
  const role = ROLES[app];
  const state = drive?.apps[app];
  const health = appHealth(role, vm, state);
  const control = controlLabel(role, drive);
  const active = drive?.control?.active_app === app;
  const command = state?.last_command;
  return (
    <Card
      className={styles.appCard}
      style={{ '--accent': role.color } as CSSProperties}
      title={<span style={{ color: role.color }}>{role.title}</span>}
      actions={<StatusBadge health={health} size="sm" />}
    >
      <div className={styles.rows}>
        <div>
          <span>OS · app_id</span>
          <span>
            {vm ? osLabel(vm) : '—'} · {app}
          </span>
        </div>
      </div>
      {control && (
        <div className={[styles.controlBar, active ? styles.controlOn : ''].join(' ')}>{control}</div>
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
            <span>{command ? `${plain(command.v_mps)} m/s · ${signed(command.steer_rad)} rad` : '—'}</span>
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
            streamUrl={CAMERA_URL || undefined}
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
