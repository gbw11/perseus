import { Card } from '@xenova/ui';
import { useMemo, useState } from 'react';
import { useEvents, useVms } from '../api/queries';
import type { EventSeverity } from '../api/types';
import { CATEGORY_LABEL, categoryOf, clock, describe, type EventCategory } from '../domain/events';
import { plain, ROLE_ORDER, ROLES, roleOf, vmOf, type AppId } from '../domain/roles';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

const CATEGORIES = Object.keys(CATEGORY_LABEL) as EventCategory[];
const SEVERITIES: EventSeverity[] = ['INFO', 'WARNING', 'ERROR'];

/**
 * W-08 이벤트 타임라인 (화면 정의서 W-08, UI-07) — 초안. 화면 완성은 S15P31E110-160 (정송하) 가 이어받는다.
 * 실시간 추가는 WS /ws/v1/events 대신 3초 재조회 (초안). 참고 시안: stitch_untitled_project_init/xenova_2.
 */
export function EventsPage() {
  const { drive } = useDrive();
  const vms = useVms();
  const [category, setCategory] = useState<EventCategory | null>(null);
  const [target, setTarget] = useState<AppId | null>(null);
  const [severity, setSeverity] = useState<EventSeverity | null>(null);
  const targetVm = target ? vmOf(target, vms.data?.items, drive) : undefined;
  const events = useEvents({ vm_id: targetVm?.vm_id, severity: severity ?? undefined, limit: 100 });

  const items = useMemo(
    () => (events.data?.items ?? []).filter((e) => !category || categoryOf(e) === category),
    [events.data, category],
  );
  const vmName = (vmId: string | null) => {
    const vm = vms.data?.items.find((v) => v.vm_id === vmId);
    return vm ? (roleOf(vm, drive)?.title ?? vm.name) : (vmId ?? '시스템');
  };
  const lastHandoff = (events.data?.items ?? []).find(
    (e) => e.type === 'drive.mode.changed' && e.payload.handoff_ms != null,
  );

  const chip = <T,>(
    value: T,
    current: T | null,
    set: (v: T | null) => void,
    label: string,
    color?: string,
  ) => (
    <button
      key={String(value)}
      type="button"
      className={[styles.chip, current === value ? styles.chipOn : ''].join(' ')}
      onClick={() => set(current === value ? null : value)}
      style={color && current !== value ? { color } : undefined}
    >
      {label}
    </button>
  );

  return (
    <div className={styles.page}>
      <section className={styles.pageHead} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
        <div className={styles.row} style={{ marginBottom: 0 }}>
          <h1>이벤트 타임라인</h1>
          <span className={styles.grow} />
          <span className={styles.dim}>{events.isFetching ? '갱신 중…' : '3초마다 갱신'}</span>
        </div>
        <div className={styles.chips}>
          <span className={styles.eyebrow}>종류</span>
          {chip(null, category, () => setCategory(null), '전체')}
          {CATEGORIES.map((c) => chip(c, category, setCategory, CATEGORY_LABEL[c]))}
        </div>
        <div className={styles.chips}>
          <span className={styles.eyebrow}>대상</span>
          {chip(null, target, () => setTarget(null), '전체 VM')}
          {ROLE_ORDER.map((app) => chip(app, target, setTarget, ROLES[app].title, ROLES[app].color))}
        </div>
        <div className={styles.chips}>
          <span className={styles.eyebrow}>심각도</span>
          {chip(null, severity, () => setSeverity(null), '전체')}
          {SEVERITIES.map((s) => chip(s, severity, setSeverity, s))}
        </div>
      </section>

      <div className={styles.tiles}>
        <div className={styles.tile}>
          <small>현재 세대</small>
          <span className={styles.big}>
            {drive?.control ? `gen ${drive.control.generation} (${drive.control.mode})` : '—'}
          </span>
        </div>
        <div className={styles.tile}>
          <small>최근 인계</small>
          <span className={styles.big}>
            {lastHandoff ? `${String(lastHandoff.payload.handoff_ms)} ms` : '—'}
          </span>
          <span className={styles.unit}>목표 ≤150 ms</span>
        </div>
        <div className={styles.tile}>
          <small>차량</small>
          <span className={styles.big}>{plain(drive?.vehicle?.v_mps)}</span>
          <span className={styles.unit}>m/s</span>
        </div>
      </div>

      <Card title={`시간순 이벤트 (${items.length}건, 최신 위)`}>
        {items.map((event) => {
          const { title, detail } = describe(event, vmName);
          const vm = vms.data?.items.find((v) => v.vm_id === event.vm_id);
          const role = vm ? roleOf(vm, drive) : null;
          return (
            <div key={event.event_id} className={styles.eventRow}>
              <span className={styles.mono}>
                {clock(event.occurred_at)}
                <div className={styles.dim}>{new Date(event.occurred_at).toLocaleDateString('ko-KR')}</div>
              </span>
              <span>
                <span className={`${styles.sev} ${styles[`sev${event.severity}`]}`}>{event.severity}</span>
              </span>
              <span className={styles.chip}>{CATEGORY_LABEL[categoryOf(event)]}</span>
              <span style={{ color: role?.color }}>
                {role?.short ?? (event.vm_id ? vmName(event.vm_id) : '게이트웨이')}
              </span>
              <span>
                <b>{title}</b>
                {detail && <div className={styles.dim}>{detail}</div>}
              </span>
            </div>
          );
        })}
        {events.data && items.length === 0 && <p className={styles.dim}>조건에 맞는 이벤트가 없다</p>}
        {events.error && <p className={styles.error}>이벤트를 불러오지 못했다</p>}
      </Card>
    </div>
  );
}
