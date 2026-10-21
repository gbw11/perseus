import { Button, Card, useToast } from '@xenova/ui';
import { useState, type CSSProperties } from 'react';
import { ROLES } from '../domain/roles';
import styles from './pages.module.css';

/**
 * W-06 자원 제어 (화면 정의서 W-06, UI-06) — 초안. 자원 제어 API(S15P31E110-107, 제어 계층 -101)가 아직 없어
 * 화면 안에서만 바뀌는 미리보기다. 화면 완성은 정수환 담당(-113 핀닝 UI, -114 가중치·메모리 UI)이 이어받는다.
 * 코어 배치 표본은 RC카 시나리오 §2 (자율·수동 전용 1코어씩, 인터랙션은 dom0 와 공유 가능). 참고 시안: init/vcpu.
 */

type Domain = 'dom0' | 'autonomous' | 'manual' | 'message-board';
interface Config {
  pins: Record<Domain, number[]>;
  weight: Record<Domain, number>;
  cap: Record<Domain, number>;
  memory: Record<Domain, number>;
}

const DOMAINS: {
  id: Domain;
  title: string;
  sub: string;
  color: string;
  dedicated: boolean;
  fixedMemory: boolean;
}[] = [
  {
    id: 'dom0',
    title: 'dom0',
    sub: 'Yocto · 관리 도메인',
    color: 'var(--x-stopped)',
    dedicated: false,
    fixedMemory: true,
  },
  {
    id: 'autonomous',
    title: ROLES.autonomous.title,
    sub: 'Zephyr · autonomous',
    color: ROLES.autonomous.color,
    dedicated: true,
    fixedMemory: true,
  },
  {
    id: 'manual',
    title: ROLES.manual.title,
    sub: 'Zephyr · manual',
    color: ROLES.manual.color,
    dedicated: true,
    fixedMemory: true,
  },
  {
    id: 'message-board',
    title: ROLES['message-board'].title,
    sub: 'Debian 13 · message-board',
    color: ROLES['message-board'].color,
    dedicated: false,
    fixedMemory: false,
  },
];
const CORES = [0, 1, 2, 3];

const INITIAL: Config = {
  pins: { dom0: [0], autonomous: [1], manual: [2], 'message-board': [3] },
  weight: { dom0: 512, autonomous: 512, manual: 512, 'message-board': 256 },
  cap: { dom0: 0, autonomous: 0, manual: 0, 'message-board': 0 },
  memory: { dom0: 1024, autonomous: 16, manual: 16, 'message-board': 512 },
};

const copy = (c: Config): Config => JSON.parse(JSON.stringify(c)) as Config;

export function ResourcesPage() {
  const toast = useToast();
  const [staged, setStaged] = useState<Config>(() => copy(INITIAL));

  /** 전용 코어(자율·수동)는 다른 도메인과 나누지 않는다 */
  const owner = (core: number) => DOMAINS.find((d) => d.dedicated && staged.pins[d.id].includes(core));

  const toggle = (domain: Domain, core: number) => {
    const pins = staged.pins[domain];
    const on = pins.includes(core);
    const holder = owner(core);
    if (!on && holder && holder.id !== domain) {
      toast.show(`코어 ${core} 는 ${holder.title} 전용이라 나눌 수 없다`, 'fault', 4000);
      return;
    }
    if (on && pins.length === 1) {
      toast.show('도메인마다 코어가 하나는 있어야 한다', 'fault', 3000);
      return;
    }
    const next = copy(staged);
    next.pins[domain] = on ? pins.filter((c) => c !== core) : [...pins, core].sort();
    setStaged(next);
  };

  const set = (key: 'weight' | 'cap' | 'memory', domain: Domain, value: number) => {
    const next = copy(staged);
    next[key][domain] = value;
    setStaged(next);
  };

  const diff = DOMAINS.flatMap((d) => {
    const rows: { domain: string; item: string; before: string; after: string }[] = [];
    const pinText = (c: Config) => `코어 ${c.pins[d.id].join(', ')}`;
    if (pinText(INITIAL) !== pinText(staged))
      rows.push({ domain: d.title, item: 'vCPU 핀닝', before: pinText(INITIAL), after: pinText(staged) });
    for (const key of ['weight', 'cap', 'memory'] as const) {
      if (INITIAL[key][d.id] !== staged[key][d.id]) {
        const unit = key === 'memory' ? ' MiB' : key === 'cap' ? '%' : '';
        rows.push({
          domain: d.title,
          item: key,
          before: `${INITIAL[key][d.id]}${unit}`,
          after: `${staged[key][d.id]}${unit}`,
        });
      }
    }
    return rows;
  });

  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>자원 제어 · Cortex-A72 4코어 · Xen credit2</div>
          <h1>vCPU·스케줄러·메모리</h1>
        </div>
        <span className={styles.draft}>자원 제어 API(-107) 미구현 — 화면 안 미리보기</span>
        <span className={styles.grow} />
        <Button variant="secondary" onClick={() => setStaged(copy(INITIAL))}>
          초기화
        </Button>
        <Button
          disabled={diff.length === 0}
          onClick={() => toast.show('자원 제어 API(-107)가 아직 없어 적용하지 않았다', 'fault', 4000)}
        >
          설정 적용
        </Button>
      </section>

      <Card
        title="vCPU 핀닝 매트릭스"
        actions={<span className={styles.dim}>칸을 눌러 고정·해제 (xl vcpu-pin)</span>}
      >
        <table className={styles.matrix}>
          <thead>
            <tr>
              <th>도메인</th>
              {CORES.map((c) => (
                <th key={c}>CORE {c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {DOMAINS.map((d) => (
              <tr key={d.id}>
                <td>
                  <b style={{ color: d.color }}>{d.title}</b>
                  <div className={styles.eyebrow}>{d.sub}</div>
                </td>
                {CORES.map((core) => {
                  const on = staged.pins[d.id].includes(core);
                  return (
                    <td key={core}>
                      <button
                        type="button"
                        className={[styles.cell, on ? styles.cellPinned : ''].join(' ')}
                        style={{ '--accent': d.color } as CSSProperties}
                        onClick={() => toggle(d.id, core)}
                        aria-pressed={on}
                      >
                        {on ? (d.dedicated ? '전용 고정' : '고정') : '—'}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className={styles.cols2}>
        <Card title="스케줄러 (credit2)">
          {DOMAINS.filter((d) => d.id !== 'dom0').map((d) => (
            <div
              key={d.id}
              style={{ '--accent': d.color, marginBottom: 'var(--x-space-4)' } as CSSProperties}
            >
              <div className={styles.row} style={{ justifyContent: 'space-between', marginBottom: 4 }}>
                <b style={{ color: d.color }}>{d.title}</b>
                <span className={styles.mono}>
                  weight {staged.weight[d.id]} · cap{' '}
                  {staged.cap[d.id] === 0 ? '제한 없음' : `${staged.cap[d.id]}%`}
                </span>
              </div>
              <input
                className={styles.slider}
                type="range"
                min={1}
                max={1024}
                value={staged.weight[d.id]}
                onChange={(e) => set('weight', d.id, Number(e.target.value))}
                aria-label={`${d.title} weight`}
              />
              <input
                className={styles.slider}
                type="range"
                min={0}
                max={100}
                step={5}
                value={staged.cap[d.id]}
                onChange={(e) => set('cap', d.id, Number(e.target.value))}
                aria-label={`${d.title} cap`}
              />
            </div>
          ))}
        </Card>
        <Card title="메모리">
          <div className={styles.rows}>
            {DOMAINS.filter((d) => d.id !== 'dom0').map((d) => (
              <div key={d.id}>
                <span style={{ color: d.color }}>{d.title}</span>
                <span>
                  {d.fixedMemory ? (
                    `${staged.memory[d.id]} MiB 고정 (Zephyr 프로필)`
                  ) : (
                    <input
                      className={styles.select}
                      type="number"
                      min={256}
                      max={1024}
                      step={64}
                      value={staged.memory[d.id]}
                      onChange={(e) => set('memory', d.id, Number(e.target.value))}
                      style={{ width: 110 }}
                    />
                  )}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="변경 전 → 변경 후 → 실제 적용">
        <table className={styles.table}>
          <thead>
            <tr>
              <th>도메인</th>
              <th>항목</th>
              <th>변경 전</th>
              <th>변경 후</th>
              <th>실제 적용</th>
            </tr>
          </thead>
          <tbody>
            {diff.map((r) => (
              <tr key={`${r.domain}-${r.item}`}>
                <td>{r.domain}</td>
                <td className={styles.mono}>{r.item}</td>
                <td className={styles.mono}>{r.before}</td>
                <td className={styles.mono}>{r.after}</td>
                <td className={styles.dim}>미적용 (API 없음)</td>
              </tr>
            ))}
            {diff.length === 0 && (
              <tr>
                <td colSpan={5} className={styles.dim}>
                  바뀐 값이 없다
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
