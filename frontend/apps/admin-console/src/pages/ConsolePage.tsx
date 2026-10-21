import { StatusBadge } from '@xenova/ui';
import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../api/client';
import { useVms } from '../api/queries';
import { osLabel, roleOf } from '../domain/roles';
import { STATE_HEALTH, STATE_LABEL } from '../format';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

/** xterm 이 무거워서 이 화면을 열 때만 불러온다 (platform -248 구현) */
const ConsolePanel = lazy(() =>
  import('../console/ConsolePanel').then((module) => ({ default: module.ConsolePanel })),
);

/**
 * 웹 콘솔 — VM 을 골라 바로 게스트 터미널에 입력한다 (UI-05·MO-04). VM 상세의 [웹 콘솔] 탭과 같은 패널.
 * WS /ws/v1/vms/{id}/console (웹 터미널 계약 browser-console-v1). 고른 VM 은 주소(?vm=)에 남는다.
 * VM 을 바꾸면 이전 연결은 끊긴다 — VM 은 종료하지 않는다.
 */
export function ConsolePage() {
  const vms = useVms();
  const { drive } = useDrive();
  const [params, setParams] = useSearchParams();
  const items = vms.data?.items ?? [];
  const selected = items.find((vm) => vm.vm_id === params.get('vm')) ?? items[0];

  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>웹 콘솔 · 게스트 터미널</div>
          <h1>VM 을 골라 바로 입력</h1>
        </div>
        <span className={styles.grow} />
        <span className={styles.dim}>VM 당 쓰기 세션 1개 · 연결 해제는 VM 을 끄지 않는다</span>
      </section>

      {vms.error && (
        <p className={styles.error} role="alert">
          {vms.error instanceof ApiError ? `${vms.error.message} (${vms.error.code})` : String(vms.error)}
        </p>
      )}
      {vms.isPending && <p className={styles.dim}>불러오는 중…</p>}

      <div className={styles.picks} role="tablist" aria-label="콘솔을 열 VM">
        {items.map((vm) => {
          const role = roleOf(vm, drive);
          const on = vm.vm_id === selected?.vm_id;
          return (
            <button
              key={vm.vm_id}
              type="button"
              role="tab"
              aria-selected={on}
              className={[styles.pick, on ? styles.pickOn : ''].join(' ')}
              style={{ borderTopColor: role?.color }}
              onClick={() => setParams({ vm: vm.vm_id }, { replace: true })}
            >
              <span className={styles.row}>
                <b style={{ color: role?.color }}>{role?.title ?? vm.name}</b>
                <span className={styles.grow} />
                <StatusBadge health={STATE_HEALTH[vm.state]} label={STATE_LABEL[vm.state]} size="sm" />
              </span>
              <span className={styles.eyebrow}>
                {vm.name} · {osLabel(vm)}
                {vm.guest_kind === 'zephyr' ? ' · 셸 없음(로그만)' : ''}
              </span>
            </button>
          );
        })}
      </div>

      {selected && (
        <Suspense fallback={<p className={styles.dim}>콘솔 준비 중…</p>}>
          <ConsolePanel key={selected.vm_id} vm={selected} />
        </Suspense>
      )}
    </div>
  );
}
