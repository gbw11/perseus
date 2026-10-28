import { StatusBadge } from '@xenova/ui';
import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../api/client';
import { useVms } from '../api/queries';
import { osLabel, roleOf } from '../domain/roles';
import { vmAuthorityOf } from '../domain/vmAuthority';
import { capitalizeFirst, STATE_HEALTH, STATE_LABEL } from '../format';
import { useDrive } from '../layout/DriveContext';
import styles from './pages.module.css';

/** xterm 이 무거워서 이 화면을 열 때만 불러온다 (platform -248 구현) */
const ConsolePanel = lazy(() =>
  import('../console/ConsolePanel').then((module) => ({ default: module.ConsolePanel })),
);

/**
 * 웹 콘솔 — VM 을 골라 바로 게스트 터미널에 입력한다 (UI-05·MO-04). VM 상세의 [웹 콘솔] 탭과 같은 패널.
 * WS /ws/v1/vms/{id}/console (웹 터미널 계약 browser-console-v1). 고른 VM 은 주소(?vm=)에 남는다.
 * 웹 터미널 계약 browser-console-v1 그대로: [연결]로 접속, 자동 재접속 없음. VM 을 바꾸면 이전 연결은 끊긴다 — VM 은 종료하지 않는다.
 */
export function ConsolePage() {
  const vms = useVms();
  const { drive } = useDrive();
  const [params, setParams] = useSearchParams();
  const items = vms.data?.items ?? [];
  const selected = items.find((vm) => vm.vm_id === params.get('vm')) ?? items[0];
  const selectedRole = selected ? roleOf(selected, drive) : null;
  const selectedAuthority = selected
    ? vmAuthorityOf(selectedRole, selected, drive?.control?.active_vm_id)
    : undefined;

  return (
    <div className={styles.page}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>VM ACCESS</span>
          <h1>VM Console</h1>
        </div>
      </header>

      {vms.error && (
        <p className={styles.error} role="alert">
          {vms.error instanceof ApiError ? `${vms.error.message} (${vms.error.code})` : String(vms.error)}
        </p>
      )}
      {vms.isPending && <p className={styles.dim}>불러오는 중…</p>}

      <div className={styles.picks} role="tablist" aria-label="콘솔을 열 VM">
        {items.map((vm) => {
          const role = roleOf(vm, drive);
          const authority = vmAuthorityOf(role, vm, drive?.control?.active_vm_id);
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
                <b style={{ color: role?.color }}>{role?.vmLabel ?? capitalizeFirst(vm.name)}</b>
                <span className={styles.grow} />
                <span className={styles.consoleStates}>
                  <span className={styles.consoleAuthority} data-authority={authority.toLowerCase()}>
                    {authority}
                  </span>
                  <span className={styles.consoleRuntime}>
                    <StatusBadge health={STATE_HEALTH[vm.state]} label={STATE_LABEL[vm.state]} size="sm" />
                  </span>
                </span>
              </span>
              <span className={styles.eyebrow}>
                {role ? (role.driving ? 'Drive' : 'Infotainment') : capitalizeFirst(vm.name)} · {osLabel(vm)}
                {vm.guest_kind === 'zephyr' ? ' · 로그 전용' : ''}
              </span>
            </button>
          );
        })}
      </div>

      {selected && (
        <Suspense fallback={<p className={styles.dim}>콘솔 준비 중…</p>}>
          <ConsolePanel
            key={selected.vm_id}
            vm={selected}
            displayName={selectedRole?.vmLabel ?? capitalizeFirst(selected.name)}
            authority={selectedAuthority}
          />
        </Suspense>
      )}
    </div>
  );
}
