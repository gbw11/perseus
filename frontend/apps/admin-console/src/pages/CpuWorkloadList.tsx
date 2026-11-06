import { useProcesses, useThreads } from '../api/queries';
import type { Vm } from '../api/types';
import {
  countLabel,
  processCollectionNotes,
  threadCollectionNotes,
  topProcesses,
  topThreads,
  type CollectionNote,
} from '../domain/workloads';
import { MetricErrorNotice } from './MetricErrorNotice';
import styles from './pages.module.css';
import { formatMiB } from './resourceFormat';

/** CPU 탭 카드 하단 목록. Linux 는 상위 프로세스, Zephyr 는 스레드 (CPU 높은 순 10개) */
export function CpuWorkloadList({ vm }: { vm: Vm }) {
  return vm.guest_kind === 'zephyr' ? <ThreadList vm={vm} /> : <ProcessList vm={vm} />;
}

function ProcessList({ vm }: { vm: Vm }) {
  const processes = useProcesses(vm.vm_id, true);
  const list = topProcesses(processes.data);
  const notes = processCollectionNotes(processes.data);

  return (
    <section className={styles.workloadBlock} aria-label={`${vm.name} 상위 프로세스`}>
      <WorkloadHead title="상위 프로세스" count={processes.data ? countLabel(list) : null} notes={notes} />
      {processes.error && <MetricErrorNotice />}
      <table className={styles.workloadTable}>
        <thead>
          <tr>
            <th>PID</th>
            <th>이름</th>
            <th className={styles.workloadNum}>CPU %</th>
            <th className={styles.workloadNum}>RSS</th>
          </tr>
        </thead>
        <tbody>
          {list.items.map((process) => (
            <tr key={process.pid}>
              <td className={styles.mono}>{process.pid}</td>
              <td className={styles.workloadName}>{process.name}</td>
              <td className={`${styles.mono} ${styles.workloadNum}`}>
                {process.cpu_percent?.toFixed(1) ?? '—'}
              </td>
              <td className={`${styles.mono} ${styles.workloadNum}`}>
                {process.rss_bytes == null ? '—' : formatMiB(process.rss_bytes)}
              </td>
            </tr>
          ))}
          {list.items.length === 0 && (
            <tr>
              <td colSpan={4} className={styles.workloadEmpty}>
                {processes.isPending ? '프로세스 보고 대기 중' : '프로세스 보고 없음'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function ThreadList({ vm }: { vm: Vm }) {
  const threads = useThreads(vm.vm_id, true);
  const list = topThreads(threads.data);
  const notes = threadCollectionNotes(threads.data);
  const windowMs = threads.data?.window_ms;

  return (
    <section className={styles.workloadBlock} aria-label={`${vm.name} 스레드`}>
      <WorkloadHead
        title="스레드"
        count={threads.data ? countLabel(list) : null}
        notes={notes}
        meta={windowMs != null ? `계측 구간 ${(windowMs / 1000).toFixed(1)}초` : null}
      />
      {threads.error && <MetricErrorNotice />}
      <table className={styles.workloadTable}>
        <thead>
          <tr>
            <th>스레드</th>
            <th className={styles.workloadNum}>CPU %</th>
          </tr>
        </thead>
        <tbody>
          {list.items.map((thread) => (
            <tr key={thread.thread_id}>
              <td className={styles.workloadName} title={thread.name_truncated ? '이름 잘림' : undefined}>
                {thread.thread_name || thread.thread_id}
                {thread.name_truncated ? '…' : ''}
              </td>
              <td className={`${styles.mono} ${styles.workloadNum}`}>
                {thread.cpu_percent?.toFixed(1) ?? thread.unavailable_reason ?? '—'}
              </td>
            </tr>
          ))}
          {list.items.length === 0 && (
            <tr>
              <td colSpan={2} className={styles.workloadEmpty}>
                {threads.isPending ? '스레드 표본 대기 중' : '스레드 표본 없음'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function WorkloadHead({
  title,
  count,
  notes,
  meta = null,
}: {
  title: string;
  count: string | null;
  notes: CollectionNote[];
  meta?: string | null;
}) {
  return (
    <header className={styles.workloadHead}>
      <div>
        <strong>{title}</strong>
        {count && <small>{count} · CPU 높은 순</small>}
        {meta && <small>{meta}</small>}
      </div>
      {notes.length > 0 && (
        <ul className={styles.workloadNotes}>
          {notes.map((note) => (
            <li key={note.text} data-tone={note.tone}>
              {note.text}
            </li>
          ))}
        </ul>
      )}
    </header>
  );
}
