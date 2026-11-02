import { Card } from '@xenova/ui';
import { useEffect, useState } from 'react';
import { appendLogs, fetchLogs, fetchLogSources, type RuntimeLog, type LogSource } from '../api/logs';
import styles from './RuntimeLogsPanel.module.css';

export function RuntimeLogsPanel({ vmId }: { vmId?: string }) {
  const [onlyVm, setOnlyVm] = useState(false);
  const [paused, setPaused] = useState(false);
  const [records, setRecords] = useState<RuntimeLog[]>([]);
  const [sources, setSources] = useState<LogSource[]>([]);
  const [state, setState] = useState('연결 대기');
  const [gap, setGap] = useState(false);
  const filter = onlyVm ? vmId : undefined;

  useEffect(() => {
    let active = true;
    let cursor: number | undefined;
    let epoch: string | undefined;
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    if (paused) {
      return () => abort.abort();
    }
    async function poll() {
      let delay = 1000;
      try {
        const page = await fetchLogs({ after: cursor, vmId: filter, signal: abort.signal });
        if (!active) return;
        if (epoch && epoch !== page.epoch) {
          // A replaced DB must not leave a cursor from the previous log history.
          epoch = page.epoch;
          cursor = undefined;
          setRecords([]);
          setGap(true);
          delay = 50;
        } else {
          epoch = page.epoch;
          cursor = page.next_cursor;
          setRecords((old) => appendLogs(old, page.items));
          if (page.history_truncated) setGap(true);
          setState('새 로그 확인 중 · 1초 간격');
          if (page.has_more) delay = 100;
          try {
            const known = await fetchLogSources(abort.signal);
            if (active) setSources(known.items);
          } catch {
            if (active) setSources([]);
          }
        }
      } catch (error) {
        if (active)
          setState(`연결 끊김 · 이전 기록: ${error instanceof Error ? error.message : '조회 실패'}`);
        delay = 3000;
      } finally {
        if (active)
          timer = setTimeout(() => {
            void poll();
          }, delay);
      }
    }
    void poll();
    return () => {
      active = false;
      abort.abort();
      clearTimeout(timer);
    };
  }, [filter, paused]);

  function resetView() {
    setRecords([]);
    setGap(false);
    setSources([]);
    setState('연결 중');
  }

  return (
    <Card>
      <section aria-label="실행 로그" className={styles.panel}>
        <div className={styles.controls}>
          <h2>실행 로그</h2>
          {vmId && (
            <label>
              <input
                type="checkbox"
                checked={onlyVm}
                onChange={(e) => {
                  resetView();
                  setOnlyVm(e.target.checked);
                }}
              />{' '}
              선택 VM만
            </label>
          )}
          <label>
            <input
              type="checkbox"
              checked={paused}
              onChange={(e) => {
                if (!e.target.checked) resetView();
                setPaused(e.target.checked);
              }}
            />{' '}
            조회 일시정지
          </label>
        </div>
        <p role="status">{paused ? '조회 일시정지' : state}</p>
        <p>
          등록된 수집 소스의 기록만 표시해요. 다른 SSH 터미널의 출력은 서비스 로그로 전달해야 모을 수 있어요.
        </p>
        {sources.length > 0 && (
          <p>
            {sources
              .map((s) => `${s.source_id}: ${s.state === 'connected' ? '수집 연결됨' : '수집 미확인'}`)
              .join(' · ')}
          </p>
        )}
        {sources.length === 0 && <p>수집 소스 미확인 · 빈 화면이 전체 시스템 정상이라는 뜻은 아니에요.</p>}
        {gap && <p role="alert">보존 한도 또는 기록 저장소 변경으로 이전 기록 일부를 표시할 수 없어요.</p>}
        <pre className={styles.output} aria-label="수집된 로그">
          {records.length
            ? records.map((r) => (
                <span
                  key={r.id}
                >{`[${r.observed_at}] ${r.level} ${r.source_id}/${r.component}${r.vm_id ? ` (${r.vm_id})` : ''}\n${r.message}\n`}</span>
              ))
            : '수집된 로그가 없습니다.'}
        </pre>
      </section>
    </Card>
  );
}
