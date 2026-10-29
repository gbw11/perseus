import { Button, Card, Modal } from '@xenova/ui';
import { useState, type CSSProperties } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useCreateVm, useTemplates } from '../api/queries';
import type { Template } from '../api/types';
import styles from './pages.module.css';

const STEPS = ['템플릿 선택', 'VM 정보 및 리소스', '생성 정보 확인'] as const;
const TEMPLATE_ACCENTS = [
  'var(--x-app-autonomous)',
  'var(--x-app-manual)',
  'var(--x-app-interaction)',
] as const;
const MAX_MEMORY = 1024;
const MEMORY_STEP = 64;
const ZEPHYR_PROFILE = { vcpus: 1, memory_mib: 16 };

const ERROR_HINT: Record<string, string> = {
  VM_NAME_CONFLICT: '같은 이름의 VM이 있습니다. 다른 이름을 입력해 주세요.',
  INSUFFICIENT_RESOURCES: '사용 가능한 CPU 또는 메모리가 부족합니다. 할당량을 조정해 주세요.',
  VALIDATION_ERROR: '입력한 VM 정보와 자원 값을 다시 확인해 주세요.',
  TEMPLATE_NOT_FOUND: '선택한 템플릿을 찾을 수 없습니다. 템플릿을 다시 선택해 주세요.',
};

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
  const resourcesOk =
    template !== null &&
    Number.isInteger(spec.vcpus) &&
    spec.vcpus >= 1 &&
    spec.vcpus <= 4 &&
    Number.isInteger(spec.memory_mib) &&
    spec.memory_mib >= template.min_memory_mib &&
    spec.memory_mib <= MAX_MEMORY;

  const pick = (selected: Template) => {
    setTemplate(selected);
    setMemory(Math.max(selected.min_memory_mib, Math.min(memory, MAX_MEMORY)));
    create.reset();
  };

  const submit = () => {
    if (!template) return;
    create.mutate({
      name,
      template_id: template.template_id,
      template_version: template.version,
      ...spec,
    });
  };

  const error = create.error instanceof ApiError ? create.error : null;
  const goToList = () => navigate('/control');

  return (
    <div className={styles.createPage}>
      <header className={styles.contentHeading}>
        <div>
          <span className={styles.contentEyebrow}>VM CONTROL</span>
          <h2>VM 생성</h2>
          <p className={styles.headingDescription}>템플릿과 자원을 선택해 새로운 가상 머신을 생성합니다.</p>
        </div>
        <Link className={styles.headingLink} to="/control">
          ← VM 목록
        </Link>
      </header>

      <nav className={styles.createSteps} aria-label="VM 생성 단계">
        {STEPS.map((label, index) => (
          <div
            key={label}
            className={`${styles.createStep} ${index === step ? styles.createStepCurrent : ''} ${
              index < step ? styles.createStepDone : ''
            }`}
            aria-current={index === step ? 'step' : undefined}
          >
            <span>{index < step ? '✓' : index + 1}</span>
            <strong>{label}</strong>
          </div>
        ))}
      </nav>

      {step === 0 && (
        <section className={styles.createStage} aria-labelledby="template-stage-title">
          <div className={styles.createStageHead}>
            <div>
              <span>STEP 1</span>
              <h3 id="template-stage-title">템플릿을 선택해 주세요</h3>
            </div>
            <p>VM에 설치할 운영체제와 기본 실행 환경을 선택합니다.</p>
          </div>

          {templates.isPending && <div className={styles.statePanel}>템플릿을 불러오는 중입니다…</div>}
          {templates.error && <div className={styles.statePanel}>템플릿을 불러오지 못했습니다.</div>}
          <div className={styles.templateGrid}>
            {(templates.data ?? []).map((item, index) => {
              const selected =
                template?.template_id === item.template_id && template.version === item.version;
              const accent = TEMPLATE_ACCENTS[index % TEMPLATE_ACCENTS.length];
              return (
                <button
                  key={`${item.template_id}@${item.version}`}
                  type="button"
                  className={`${styles.templateCard} ${selected ? styles.templateCardSelected : ''}`}
                  style={{ '--template-accent': accent } as CSSProperties}
                  aria-pressed={selected}
                  onClick={() => pick(item)}
                >
                  <span className={styles.templateKind}>
                    {item.guest_kind === 'zephyr' ? 'RTOS' : 'LINUX'}
                  </span>
                  <strong>{item.display_name}</strong>
                  <p>
                    {item.guest_kind === 'zephyr'
                      ? '고정 프로필 · 1 vCPU · 16 MiB'
                      : `사용자 설정 가능 · 최소 ${item.min_memory_mib} MiB`}
                  </p>
                  <small>
                    {item.template_id} · v{item.version}
                  </small>
                  <i>{selected ? '선택됨' : '선택'}</i>
                </button>
              );
            })}
          </div>
          <div className={styles.createStageActions}>
            <Button onClick={() => setStep(1)} disabled={!template}>
              다음
            </Button>
          </div>
        </section>
      )}

      {step === 1 && template && (
        <section className={styles.createStage} aria-labelledby="info-stage-title">
          <div className={styles.createStageHead}>
            <div>
              <span>STEP 2</span>
              <h3 id="info-stage-title">VM 정보 및 리소스</h3>
            </div>
            <p>{template.display_name} 템플릿에 적용할 이름과 초기 자원을 설정합니다.</p>
          </div>

          <div className={styles.createFormLayout}>
            <Card className={styles.createFormCard} title="기본 정보">
              <div className={styles.createForm}>
                <label htmlFor="vm-name">
                  <span>VM 이름</span>
                  <small>1~63자, 앞뒤 공백 없이 입력</small>
                </label>
                <input
                  id="vm-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="예: drive-vm-a"
                  aria-invalid={name.length > 0 && !nameOk}
                />

                {zephyr ? (
                  <div className={styles.fixedProfile}>
                    <span>Zephyr 고정 프로필</span>
                    <strong>1 vCPU · 16 MiB</strong>
                    <small>vchan을 통해 dom0와 통신하며 자원 값은 변경할 수 없습니다.</small>
                  </div>
                ) : (
                  <div className={styles.resourceInputs}>
                    <div>
                      <label htmlFor="vm-vcpus">
                        <span>vCPU</span>
                        <small>1~4개</small>
                      </label>
                      <input
                        id="vm-vcpus"
                        type="number"
                        min={1}
                        max={4}
                        value={vcpus}
                        onChange={(event) => setVcpus(Number(event.target.value))}
                      />
                    </div>
                    <div>
                      <label htmlFor="vm-memory">
                        <span>메모리</span>
                        <small>
                          {template.min_memory_mib}~{MAX_MEMORY} MiB
                        </small>
                      </label>
                      <div className={styles.inputWithUnit}>
                        <input
                          id="vm-memory"
                          type="number"
                          min={template.min_memory_mib}
                          max={MAX_MEMORY}
                          step={MEMORY_STEP}
                          value={memory}
                          onChange={(event) => setMemory(Number(event.target.value))}
                        />
                        <span>MiB</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </Card>

            <aside className={styles.templateSummary}>
              <span>SELECTED TEMPLATE</span>
              <h3>{template.display_name}</h3>
              <dl>
                <div>
                  <dt>유형</dt>
                  <dd>{zephyr ? 'RTOS · Zephyr' : 'Linux'}</dd>
                </div>
                <div>
                  <dt>버전</dt>
                  <dd>{template.version}</dd>
                </div>
                <div>
                  <dt>네트워크</dt>
                  <dd>{zephyr ? 'vchan · dom0 중계' : '가상 네트워크 · Agent'}</dd>
                </div>
              </dl>
            </aside>
          </div>

          <div className={styles.createStageActions}>
            <Button variant="secondary" onClick={() => setStep(0)}>
              이전
            </Button>
            <Button onClick={() => setStep(2)} disabled={!nameOk || !resourcesOk}>
              다음
            </Button>
          </div>
        </section>
      )}

      {step === 2 && template && (
        <section className={styles.createStage} aria-labelledby="review-stage-title">
          <div className={styles.createStageHead}>
            <div>
              <span>STEP 3</span>
              <h3 id="review-stage-title">생성 정보를 확인해 주세요</h3>
            </div>
            <p>VM 생성 후에는 리소스 설정을 별도 관리 화면에서 변경할 수 있습니다.</p>
          </div>

          <div className={styles.reviewCard}>
            <div className={styles.reviewIntro}>
              <span>NEW VIRTUAL MACHINE</span>
              <h3>{name}</h3>
              <p>{template.display_name}</p>
            </div>
            <dl className={styles.reviewGrid}>
              <div>
                <dt>템플릿</dt>
                <dd>{template.display_name}</dd>
                <small>{template.template_id}</small>
              </div>
              <div>
                <dt>버전</dt>
                <dd>{template.version}</dd>
              </div>
              <div>
                <dt>vCPU</dt>
                <dd>{spec.vcpus}개</dd>
              </div>
              <div>
                <dt>메모리</dt>
                <dd>{spec.memory_mib} MiB</dd>
              </div>
              <div>
                <dt>연결 방식</dt>
                <dd>{zephyr ? 'vchan · dom0 중계' : '가상 네트워크 · Agent'}</dd>
              </div>
            </dl>
          </div>

          {create.error && (
            <div className={styles.createError} role="alert">
              <strong>{error ? `${error.message} (${error.code})` : String(create.error)}</strong>
              {error && ERROR_HINT[error.code] && <span>{ERROR_HINT[error.code]}</span>}
            </div>
          )}

          <div className={styles.createStageActions}>
            <Button variant="secondary" onClick={() => setStep(1)} disabled={create.isPending}>
              이전
            </Button>
            <Button onClick={submit} disabled={create.isPending}>
              {create.isPending ? 'VM 생성 중…' : 'VM 생성'}
            </Button>
          </div>
        </section>
      )}

      <Modal
        open={create.data !== undefined}
        title="VM 생성 완료"
        onClose={goToList}
        footer={<Button onClick={goToList}>VM 목록으로</Button>}
      >
        {create.data && (
          <div className={styles.createSuccess}>
            <span aria-hidden="true">✓</span>
            <div>
              <strong>‘{create.data.name}’</strong>
              <p>새 VM이 성공적으로 생성되었습니다.</p>
            </div>
            <dl className={styles.createSuccessMeta}>
              <div>
                <dt>템플릿</dt>
                <dd>{template?.display_name}</dd>
              </div>
              <div>
                <dt>할당 자원</dt>
                <dd>
                  {spec.vcpus} vCPU · {spec.memory_mib} MiB
                </dd>
              </div>
            </dl>
          </div>
        )}
      </Modal>
    </div>
  );
}
