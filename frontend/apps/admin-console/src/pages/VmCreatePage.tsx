import { Button, Card } from '@xenova/ui';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useCreateVm, useTemplates } from '../api/queries';
import type { Template } from '../api/types';
import styles from './pages.module.css';

const STEPS = ['템플릿', '이름·자원', '확인', '결과'] as const;
/** VM 관리 API 계약 §9 */
const MAX_MEMORY = 1024;
const MEMORY_STEP = 64;
/** 화면 정의서 W-05: Zephyr 는 고정 프로필만 */
const ZEPHYR_PROFILE = { vcpus: 1, memory_mib: 16 };

const ERROR_HINT: Record<string, string> = {
  VM_NAME_CONFLICT: '같은 이름의 VM 이 있다. 다른 이름을 쓴다.',
  INSUFFICIENT_RESOURCES: '보드에 남은 CPU·메모리가 부족하다. 다른 VM 을 정지하거나 자원을 줄인다.',
  VALIDATION_ERROR: '입력값을 확인한다.',
  TEMPLATE_NOT_FOUND: '템플릿을 다시 고른다.',
};

/**
 * W-05 VM 생성 위저드 (화면 정의서 W-05, UI-03) — 초안. 화면 완성은 S15P31E110-159 (정송하) 가 이어받는다.
 * 참고 시안: stitch_untitled_project/ui_03_vm.
 */
export function VmCreatePage() {
  const templates = useTemplates();
  const create = useCreateVm();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [template, setTemplate] = useState<Template | null>(null);
  const [name, setName] = useState('');
  const [vcpus, setVcpus] = useState(1);
  const [memory, setMemory] = useState(512);
  const zephyr = template?.guest_kind === 'zephyr';
  const spec = zephyr ? ZEPHYR_PROFILE : { vcpus, memory_mib: memory };
  const nameOk = name.length >= 1 && name.length <= 63 && name.trim() === name;

  const pick = (t: Template) => {
    setTemplate(t);
    setMemory(Math.max(t.min_memory_mib, Math.min(memory, MAX_MEMORY)));
    setStep(1);
  };

  const submit = () =>
    template &&
    create.mutate(
      { name, template_id: template.template_id, template_version: template.version, ...spec },
      { onSettled: () => setStep(3) },
    );

  const error = create.error instanceof ApiError ? create.error : null;

  return (
    <div className={styles.page}>
      <section className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>
            <Link to="/vms">VM 목록</Link> / VM 만들기
          </div>
          <h1>VM 만들기</h1>
        </div>
      </section>
      <div className={styles.steps}>
        {STEPS.map((label, i) => (
          <div key={label} className={[styles.step, i <= step ? styles.stepOn : ''].join(' ')}>
            {i + 1}. {label}
          </div>
        ))}
      </div>

      {step === 0 && (
        <div className={styles.cols3}>
          {(templates.data ?? []).map((t) => (
            <button
              key={`${t.template_id}@${t.version}`}
              type="button"
              className={[styles.pick, template === t ? styles.pickOn : ''].join(' ')}
              onClick={() => pick(t)}
            >
              <div className={styles.big}>{t.display_name}</div>
              <div className={styles.dim}>
                {t.guest_kind === 'zephyr'
                  ? 'RTOS · 고정 프로필 1 vCPU / 16 MiB · vchan 중계'
                  : `Linux · 최소 ${t.min_memory_mib} MiB`}
              </div>
              <div className={styles.eyebrow}>
                {t.template_id} {t.version}
              </div>
            </button>
          ))}
          {templates.isPending && <p className={styles.dim}>템플릿 불러오는 중…</p>}
        </div>
      )}

      {step === 1 && template && (
        <div className={styles.cols32}>
          <Card title={`${template.display_name} — 이름·자원`}>
            <div className={styles.form}>
              <label>
                이름 (1~63자)
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: autonomous" />
              </label>
              {zephyr ? (
                <div className={styles.rows}>
                  <div>
                    <span>자원</span>
                    <span>고정 프로필 1 vCPU / 16 MiB (바꿀 수 없음)</span>
                  </div>
                  <div>
                    <span>네트워크</span>
                    <span>없음 — vchan 중계로 연결</span>
                  </div>
                </div>
              ) : (
                <>
                  <label>
                    vCPU (1~4)
                    <input
                      type="number"
                      min={1}
                      max={4}
                      value={vcpus}
                      onChange={(e) => setVcpus(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    메모리 ({template.min_memory_mib}~{MAX_MEMORY} MiB, {MEMORY_STEP} 단위)
                    <input
                      type="number"
                      min={template.min_memory_mib}
                      max={MAX_MEMORY}
                      step={MEMORY_STEP}
                      value={memory}
                      onChange={(e) => setMemory(Number(e.target.value))}
                    />
                  </label>
                </>
              )}
            </div>
            <div className={styles.rowActions} style={{ marginTop: 'var(--x-space-4)' }}>
              <Button variant="secondary" onClick={() => setStep(0)}>
                이전
              </Button>
              <Button onClick={() => setStep(2)} disabled={!nameOk}>
                다음
              </Button>
            </div>
          </Card>
          <Card title="OS 별 적용 항목">
            <p className={styles.dim}>
              Linux(Ubuntu·Debian)는 vCPU·메모리를 고르고 에이전트가 설치된다. Zephyr 는 고정 프로필이고
              에이전트가 없어 차트·프로세스 대신 콘솔로 확인한다.
            </p>
          </Card>
        </div>
      )}

      {step === 2 && template && (
        <Card title="확인">
          <div className={styles.rows}>
            <div>
              <span>이름</span>
              <span>{name}</span>
            </div>
            <div>
              <span>템플릿</span>
              <span>
                {template.display_name} ({template.template_id} {template.version})
              </span>
            </div>
            <div>
              <span>자원</span>
              <span>
                {spec.vcpus} vCPU / {spec.memory_mib} MiB
              </span>
            </div>
          </div>
          <div className={styles.rowActions}>
            <Button variant="secondary" onClick={() => setStep(1)}>
              이전
            </Button>
            <Button onClick={submit} disabled={create.isPending}>
              {create.isPending ? '만드는 중…' : '만들기'}
            </Button>
          </div>
        </Card>
      )}

      {step === 3 && (
        <Card title="결과">
          {create.data ? (
            <>
              <p>
                <b>{create.data.name}</b> 을 만들었다.
              </p>
              <Button onClick={() => navigate(`/vms/${create.data.vm_id}`)}>상세로</Button>
            </>
          ) : (
            <>
              <p className={styles.error} role="alert">
                {error ? `${error.message} (${error.code})` : String(create.error)}
              </p>
              {error && ERROR_HINT[error.code] && <p className={styles.dim}>{ERROR_HINT[error.code]}</p>}
              <Button variant="secondary" onClick={() => setStep(error?.code === 'VM_NAME_CONFLICT' ? 1 : 2)}>
                다시
              </Button>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
