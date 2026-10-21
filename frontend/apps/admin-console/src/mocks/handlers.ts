/**
 * MSW 핸들러 — 백엔드 없이 로그인 → VM 목록 → 상세 → 제어 흐름을 돌린다.
 * 상태 코드·오류 코드는 실제 API 를 따른다:
 *   인증: 노션 API 명세 "로그인"·"토큰 재발급"·"로그아웃" (refresh 는 HttpOnly 쿠키 xenova_refresh)
 *   VM:   docs/contracts/vm-api-contract-v1.md §5(엔드포인트) §8.2(오류) §9(입력값)
 * mock 쿠키는 HttpOnly 를 붙이지 않는다 (Service Worker 가 만든 응답의 HttpOnly 쿠키는 브라우저가 버린다).
 * 메모리에만 상태를 두므로 새로고침하면 처음으로 돌아간다 (로그인 세션은 쿠키로 유지).
 */
import { delay, http, HttpResponse, type JsonBodyType } from 'msw';
import type { CreateVmRequest, LoginRequest, TokenResponse, Vm } from '../api/types';
import { initialVms, MOCK_PASSWORD, MOCK_USER, TEMPLATES, VM_IDS } from './data';
import { scenarioEvents, vmStateOf } from './scenario';

const REFRESH_COOKIE = 'xenova_refresh';
const TOKEN_TTL_S = 900;
/** 실제 네트워크처럼 보이게 */
const LATENCY_MS = 150;

let vms: Vm[] = initialVms();
let tokenSeq = 0;
let accessToken: string | null = null;
/** Node(시험)에는 쿠키 저장소가 없어서 로그인 상태도 같이 기억한다. 브라우저는 새로고침 뒤 쿠키로 복원 */
let refreshGranted = false;

/** 시험용: 처음 상태로 */
export function resetMockState(): void {
  vms = initialVms();
  tokenSeq = 0;
  accessToken = null;
  refreshGranted = false;
}

const error = (status: number, code: string, message: string, errors?: { field: string; reason: string }[]) =>
  HttpResponse.json({ code, message, ...(errors ? { errors } : {}) } as JsonBodyType, { status });

function issue(): HttpResponse<JsonBodyType> {
  accessToken = `mock-access-${++tokenSeq}`;
  refreshGranted = true;
  const body: TokenResponse = {
    access_token: accessToken,
    token_type: 'Bearer',
    expires_in: TOKEN_TTL_S,
    expires_at: new Date(Date.now() + TOKEN_TTL_S * 1000).toISOString(),
    user: MOCK_USER,
  };
  return HttpResponse.json(body as unknown as JsonBodyType, {
    headers: {
      'Set-Cookie': `${REFRESH_COOKIE}=mock-refresh-${tokenSeq}; Path=/; SameSite=Strict`,
    },
  });
}

/** 보호된 API: Bearer 가 지금 토큰과 같아야 한다 */
function authorized(request: Request): boolean {
  return accessToken !== null && request.headers.get('Authorization') === `Bearer ${accessToken}`;
}

const unauthorized = () => error(401, 'INVALID_TOKEN', '인증이 필요합니다.');
/** 자율주행 VM 은 시연 시나리오(강제 종료 → 재시작)를 따른다 */
const live = (vm: Vm): Vm => ({ ...vm, state: vmStateOf(vm.vm_id) ?? vm.state });
const find = (id: string) => {
  const vm = vms.find((v) => v.vm_id === id);
  return vm && live(vm);
};
const vmNotFound = () => error(404, 'VM_NOT_FOUND', 'VM을 찾을 수 없습니다.');

function validate(body: Partial<CreateVmRequest>): { field: string; reason: string }[] {
  const errors: { field: string; reason: string }[] = [];
  const name = body.name ?? '';
  // eslint-disable-next-line no-control-regex
  if (name.length < 1 || name.length > 63 || name.trim() !== name || /[\u0000-\u001f]/.test(name)) {
    errors.push({ field: 'name', reason: '1~63자, 앞뒤 공백·제어문자 금지' });
  }
  if (!Number.isInteger(body.vcpus) || body.vcpus! < 1 || body.vcpus! > 4) {
    errors.push({ field: 'vcpus', reason: '1~4' });
  }
  const template = TEMPLATES.find(
    (t) => t.template_id === body.template_id && t.version === body.template_version,
  );
  if (
    template &&
    (!Number.isInteger(body.memory_mib) ||
      body.memory_mib! < template.min_memory_mib ||
      body.memory_mib! > 1024)
  ) {
    errors.push({ field: 'memory_mib', reason: `${template.min_memory_mib}~1024` });
  }
  return errors;
}

export const handlers = [
  // ---- 인증 ----
  http.post('*/api/v1/auth/login', async ({ request }) => {
    await delay(LATENCY_MS);
    const body = (await request.json()) as Partial<LoginRequest>;
    if (body.username !== MOCK_USER.username || body.password !== MOCK_PASSWORD) {
      return error(401, 'INVALID_CREDENTIALS', '아이디 또는 비밀번호가 올바르지 않습니다.');
    }
    return issue();
  }),

  http.post('*/api/v1/auth/refresh', async ({ cookies }) => {
    await delay(LATENCY_MS);
    if (!cookies[REFRESH_COOKIE] && !refreshGranted)
      return error(401, 'INVALID_REFRESH_TOKEN', '다시 로그인해 주세요.');
    return issue();
  }),

  http.post('*/api/v1/auth/logout', async () => {
    accessToken = null;
    refreshGranted = false;
    return new HttpResponse(null, {
      status: 204,
      headers: { 'Set-Cookie': `${REFRESH_COOKIE}=; Path=/; Max-Age=0` },
    });
  }),

  // ---- 템플릿·VM ----
  http.get('*/api/v1/templates', async ({ request }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    return HttpResponse.json(TEMPLATES as unknown as JsonBodyType);
  }),

  http.get('*/api/v1/vms', async ({ request }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    return HttpResponse.json({
      items: vms.map(live),
      observed_at: new Date().toISOString(),
    } as unknown as JsonBodyType);
  }),

  http.get('*/api/v1/vms/:vmId', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const vm = find(String(params.vmId));
    return vm ? HttpResponse.json(vm as unknown as JsonBodyType) : vmNotFound();
  }),

  http.post('*/api/v1/vms', async ({ request }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const body = (await request.json()) as Partial<CreateVmRequest>;
    const template = TEMPLATES.find(
      (t) => t.template_id === body.template_id && t.version === body.template_version,
    );
    if (!template) return error(404, 'TEMPLATE_NOT_FOUND', '템플릿을 찾을 수 없습니다.');
    const errors = validate(body);
    if (errors.length) return error(422, 'VALIDATION_ERROR', '입력값을 확인해 주세요.', errors);
    if (vms.some((vm) => vm.name === body.name))
      return error(409, 'VM_NAME_CONFLICT', '같은 이름의 VM이 있습니다.');
    const vm: Vm = {
      vm_id: crypto.randomUUID(),
      name: body.name!,
      guest_kind: template.guest_kind,
      state: 'running',
      agent_status: template.guest_kind === 'zephyr' ? 'UNKNOWN' : 'STALE',
      ipv4: null,
      template_id: template.template_id,
      template_version: template.version,
      vcpus: body.vcpus!,
      memory_mib: body.memory_mib!,
    };
    vms = [...vms, vm];
    return HttpResponse.json(vm as unknown as JsonBodyType, { status: 201 });
  }),

  http.post('*/api/v1/vms/:vmId/:action', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const vm = find(String(params.vmId));
    if (!vm) return vmNotFound();
    const action = String(params.action);
    if (action !== 'start' && action !== 'stop') return error(404, 'NOT_FOUND', '없는 동작입니다.');
    const wanted = action === 'start' ? 'stopped' : 'running';
    if (vm.state !== wanted)
      return error(409, 'INVALID_VM_STATE', `지금 상태(${vm.state})에서는 할 수 없습니다.`);
    const next: Vm = { ...vm, state: action === 'start' ? 'running' : 'stopped' };
    vms = vms.map((v) => (v.vm_id === vm.vm_id ? next : v));
    return HttpResponse.json(next as unknown as JsonBodyType);
  }),

  http.delete('*/api/v1/vms/:vmId', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    if (!find(String(params.vmId))) return vmNotFound();
    vms = vms.filter((vm) => vm.vm_id !== params.vmId);
    return new HttpResponse(null, { status: 204 });
  }),

  // ---- 이벤트·메트릭 (노션 API 명세 REST-18, 메트릭 조회) ----
  http.get('*/api/v1/events', async ({ request }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const url = new URL(request.url);
    const vmId = url.searchParams.get('vm_id');
    const type = url.searchParams.get('type');
    const severity = url.searchParams.get('severity');
    const items = scenarioEvents().filter(
      (e) =>
        (!vmId || e.vm_id === vmId) && (!type || e.type === type) && (!severity || e.severity === severity),
    );
    return HttpResponse.json({ items } as unknown as JsonBodyType);
  }),

  http.get('*/api/v1/vms/:vmId/metrics', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    if (params.vmId !== VM_IDS.interaction) return HttpResponse.json({ items: [] });
    const now = Date.now();
    const items = Array.from({ length: 30 }, (_, i) => {
      const t = now - (29 - i) * 10_000;
      const wave = Math.sin(t / 40_000);
      return {
        sample_id: `m-${t}`,
        vm_id: VM_IDS.interaction,
        source: 'agent',
        observed_at: new Date(t).toISOString(),
        values: [
          {
            name: 'cpu_percent',
            value: Math.round((8 + 4 * wave) * 10) / 10,
            unit: '%',
            labels: null,
            unavailable_reason: null,
          },
          {
            name: 'memory_used_bytes',
            value: (142 + 10 * wave) * 1024 * 1024,
            unit: 'bytes',
            labels: null,
            unavailable_reason: null,
          },
          {
            name: 'net_rx_bytes_total',
            value: 1_200_000 + i * 8_000,
            unit: 'bytes',
            labels: null,
            unavailable_reason: null,
          },
        ],
      };
    });
    return HttpResponse.json({ items } as unknown as JsonBodyType);
  }),

  http.get('*/api/v1/vms/:vmId/processes', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    if (params.vmId !== VM_IDS.interaction) {
      return HttpResponse.json({
        vm_id: params.vmId,
        observed_at: null,
        stale: true,
        items: [],
      } as unknown as JsonBodyType);
    }
    return HttpResponse.json({
      vm_id: VM_IDS.interaction,
      observed_at: new Date().toISOString(),
      stale: false,
      items: [
        { pid: 412, name: 'message-board', cpu_percent: 4.2, rss_bytes: 48_234_496 },
        { pid: 388, name: 'xenova-agent', cpu_percent: 1.1, rss_bytes: 21_495_808 },
        { pid: 1, name: 'systemd', cpu_percent: 0.2, rss_bytes: 11_010_048 },
      ],
    } as unknown as JsonBodyType);
  }),
];
