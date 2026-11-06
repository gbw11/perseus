/**
 * MSW 핸들러 — 백엔드 없이 로그인 → VM 목록 → 상세 → 제어 흐름을 돌린다.
 * 상태 코드·오류 코드는 실제 API 를 따른다:
 *   인증: 노션 API 명세 "로그인"·"토큰 재발급"·"로그아웃" (refresh 는 HttpOnly 쿠키 xenova_refresh)
 *   VM:   docs/contracts/vm-api-contract-v1.md §5(엔드포인트) §8.2(오류) §9(입력값)
 * mock 쿠키는 HttpOnly 를 붙이지 않는다 (Service Worker 가 만든 응답의 HttpOnly 쿠키는 브라우저가 버린다).
 * 메모리에만 상태를 두므로 새로고침하면 처음으로 돌아간다 (로그인 세션은 쿠키로 유지).
 */
import { delay, http, HttpResponse, type JsonBodyType } from 'msw';
import {
  VM_CPU_PERCENT_METRIC,
  type CreateVmRequest,
  type LoginRequest,
  type SignupRequest,
  type TokenResponse,
  type UpdateMemoryRequest,
  type UpdateVcpuPinningRequest,
  type UpdateVcpusRequest,
  type VcpuPinning,
  type Vm,
  type VmResources,
} from '../api/types';
import { mockBoardDown, setMockBoardDown } from './board';
import { initialVms, MOCK_PASSWORD, MOCK_USER, TEMPLATES, VM_IDS } from './data';
import { mockKernelHeapSamples, mockMemoryPoolSamples, mockZephyrNetworkSamples } from './memoryMetrics';
import { getDisplays, RouteError, scenarioEvents, setRoute, startVm, stopVm, vmStateOf } from './scenario';

const REFRESH_COOKIE = 'xenova_refresh';
const TOKEN_TTL_S = 900;
/** 실제 네트워크처럼 보이게 */
const LATENCY_MS = 150;

let vms: Vm[] = initialVms();
let pinningByVm = initialPinning();
let tokenSeq = 0;
let accessToken: string | null = null;
/** Node(시험)에는 쿠키 저장소가 없어서 로그인 상태도 같이 기억한다. 브라우저는 새로고침 뒤 쿠키로 복원 */
let refreshGranted = false;
let mockUsername = MOCK_USER.username;
let mockNickname = MOCK_USER.nickname;
let mockPassword = MOCK_PASSWORD;

/** 시험용: 처음 상태로 */
export function resetMockState(): void {
  setMockBoardDown(false);
  vms = initialVms();
  pinningByVm = initialPinning();
  tokenSeq = 0;
  accessToken = null;
  refreshGranted = false;
  mockUsername = MOCK_USER.username;
  mockNickname = MOCK_USER.nickname;
  mockPassword = MOCK_PASSWORD;
}

function initialPinning(): Map<string, VcpuPinning[]> {
  return new Map([
    [VM_IDS.autonomous, [{ vcpu: 0, pcpus: [1] }]],
    [VM_IDS.manual, [{ vcpu: 0, pcpus: [2] }]],
    [VM_IDS.interaction, [{ vcpu: 0, pcpus: [0, 3] }]],
  ]);
}

const defaultPinning = (vm: Vm): VcpuPinning[] =>
  Array.from({ length: vm.vcpus }, (_, vcpu) => ({ vcpu, pcpus: [0, 3] }));

function resourcesOf(vm: Vm): VmResources {
  const vcpuPinning = pinningByVm.get(vm.vm_id) ?? defaultPinning(vm);
  pinningByVm.set(vm.vm_id, vcpuPinning);
  const template = TEMPLATES.find(
    (item) => item.template_id === vm.template_id && item.version === vm.template_version,
  );
  const fixed = vm.guest_kind === 'zephyr';
  return {
    vm_id: vm.vm_id,
    vcpus: vm.vcpus,
    memory_mib: vm.memory_mib,
    pcpu_count: 4,
    vcpu_pinning: vcpuPinning,
    exclusive_pcpus: vm.guest_kind === 'zephyr' ? [...new Set(vcpuPinning.flatMap((row) => row.pcpus))] : [],
    pcpus: [
      { pcpu: 0, load_percent: 24.8 },
      { pcpu: 1, load_percent: 36.2 },
      { pcpu: 2, load_percent: 18.5 },
      { pcpu: 3, load_percent: 41.7 },
    ],
    limits: {
      vcpus_min: fixed ? vm.vcpus : 1,
      vcpus_max: fixed ? vm.vcpus : 4,
      memory_mib_min: fixed ? vm.memory_mib : (template?.min_memory_mib ?? 16),
      memory_mib_max: fixed ? vm.memory_mib : 1024,
      memory_mib_step: fixed ? 1 : 64,
    },
    observed_at: new Date().toISOString(),
  };
}

function validMappings(vm: Vm, mappings: VcpuPinning[]): boolean {
  if (mappings.length !== vm.vcpus) return false;
  const sortedVcpus = mappings.map((row) => row.vcpu).sort((a, b) => a - b);
  if (sortedVcpus.some((vcpu, index) => vcpu !== index)) return false;
  return mappings.every(
    ({ pcpus }) =>
      pcpus.length > 0 &&
      new Set(pcpus).size === pcpus.length &&
      pcpus.every((pcpu) => Number.isInteger(pcpu) && pcpu >= 0 && pcpu < 4),
  );
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
    user: { ...MOCK_USER, username: mockUsername, nickname: mockNickname },
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

/** mock 보드 끊김(board.ts): 보드에 묻는 API 는 실제 백엔드처럼 502 */
const boardUnreachable = () =>
  HttpResponse.json(
    { code: 'VM_CONTROL_BAD_RESPONSE', message: 'VM 제어 서비스와의 연결 설정이 올바르지 않습니다.' },
    { status: 502 },
  );

export const handlers = [
  // ---- 인증 ----
  http.post('*/api/v1/auth/signup', async ({ request }) => {
    await delay(LATENCY_MS);
    const body = (await request.json()) as Partial<SignupRequest>;
    if (!body.username || !body.nickname || !body.password) {
      return error(422, 'VALIDATION_ERROR', '입력값을 확인해 주세요.');
    }
    mockUsername = body.username;
    mockNickname = body.nickname;
    mockPassword = body.password;
    return HttpResponse.json(
      {
        user_id: MOCK_USER.user_id,
        username: body.username,
        nickname: body.nickname,
        created_at: new Date().toISOString(),
      } as JsonBodyType,
      { status: 201 },
    );
  }),

  http.post('*/api/v1/auth/login', async ({ request }) => {
    await delay(LATENCY_MS);
    const body = (await request.json()) as Partial<LoginRequest>;
    if (body.username !== mockUsername || body.password !== mockPassword) {
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
    if (mockBoardDown()) return boardUnreachable();
    return HttpResponse.json({
      items: vms.map(live),
      observed_at: new Date().toISOString(),
    } as unknown as JsonBodyType);
  }),

  http.get('*/api/v1/vms/:vmId', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    if (mockBoardDown()) return boardUnreachable();
    const vm = find(String(params.vmId));
    return vm ? HttpResponse.json(vm as unknown as JsonBodyType) : vmNotFound();
  }),

  http.get('*/api/v1/vms/:vmId/resources', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    if (mockBoardDown()) return boardUnreachable();
    const vm = find(String(params.vmId));
    return vm ? HttpResponse.json(resourcesOf(vm) as unknown as JsonBodyType) : vmNotFound();
  }),

  http.put('*/api/v1/vms/:vmId/resources/vcpu-pinning', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const vm = find(String(params.vmId));
    if (!vm) return vmNotFound();
    const body = (await request.json()) as Partial<UpdateVcpuPinningRequest>;
    if (!body.mappings || !validMappings(vm, body.mappings)) {
      return error(400, 'INVALID_ARGUMENT', '각 vCPU에는 하나 이상의 유효한 pCPU를 선택해야 합니다.');
    }
    if (vm.guest_kind === 'zephyr' && body.mappings.some((row) => row.pcpus.length !== 1)) {
      return error(400, 'RESOURCE_UNSUPPORTED', 'Zephyr 전용 VM은 하나의 전용 pCPU에만 배치할 수 있습니다.');
    }
    pinningByVm.set(
      vm.vm_id,
      body.mappings.map(({ vcpu, pcpus }) => ({ vcpu, pcpus: [...pcpus].sort((a, b) => a - b) })),
    );
    return HttpResponse.json(resourcesOf(vm) as unknown as JsonBodyType);
  }),

  http.put('*/api/v1/vms/:vmId/resources/vcpus', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const vm = find(String(params.vmId));
    if (!vm) return vmNotFound();
    if (vm.guest_kind === 'zephyr') {
      return error(400, 'RESOURCE_UNSUPPORTED', 'Zephyr VM은 고정 리소스 프로필을 사용합니다.');
    }
    if (vm.state !== 'stopped') {
      return error(409, 'INVALID_VM_STATE', 'vCPU 수는 정지된 VM에서만 변경할 수 있습니다.');
    }
    const body = (await request.json()) as Partial<UpdateVcpusRequest>;
    if (!Number.isInteger(body.vcpus) || body.vcpus! < 1 || body.vcpus! > 4) {
      return error(400, 'INVALID_ARGUMENT', 'vCPU 수는 1~4 정수여야 합니다.');
    }
    const currentPinning = pinningByVm.get(vm.vm_id) ?? defaultPinning(vm);
    const fallback = currentPinning[0]?.pcpus ?? [0, 3];
    pinningByVm.set(
      vm.vm_id,
      Array.from({ length: body.vcpus! }, (_, vcpu) => ({
        vcpu,
        pcpus: [...(currentPinning[vcpu]?.pcpus ?? fallback)],
      })),
    );
    vms = vms.map((item) => (item.vm_id === vm.vm_id ? { ...item, vcpus: body.vcpus! } : item));
    return HttpResponse.json(resourcesOf(find(vm.vm_id)!) as unknown as JsonBodyType);
  }),

  http.put('*/api/v1/vms/:vmId/resources/memory', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const vm = find(String(params.vmId));
    if (!vm) return vmNotFound();
    if (vm.guest_kind === 'zephyr') {
      return error(400, 'RESOURCE_UNSUPPORTED', 'Zephyr VM은 고정 리소스 프로필을 사용합니다.');
    }
    if (vm.state !== 'stopped') {
      return error(409, 'INVALID_VM_STATE', '메모리는 정지된 VM에서만 변경할 수 있습니다.');
    }
    const body = (await request.json()) as Partial<UpdateMemoryRequest>;
    const limits = resourcesOf(vm).limits;
    if (
      !Number.isInteger(body.memory_mib) ||
      body.memory_mib! < limits.memory_mib_min ||
      body.memory_mib! > limits.memory_mib_max ||
      body.memory_mib! % limits.memory_mib_step !== 0
    ) {
      return error(
        400,
        'INVALID_ARGUMENT',
        `메모리는 ${limits.memory_mib_min}~${limits.memory_mib_max} MiB, ${limits.memory_mib_step} MiB 단위여야 합니다.`,
      );
    }
    vms = vms.map((item) => (item.vm_id === vm.vm_id ? { ...item, memory_mib: body.memory_mib! } : item));
    return HttpResponse.json(resourcesOf(find(vm.vm_id)!) as unknown as JsonBodyType);
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
    // 계약 §5: start 는 running 이면 그대로 200, crashed·paused·stopping 이면 409 (정지 후 다시 시작).
    // stop 은 runtime 유무와 관계없이 동작하고 이미 꺼져 있으면 그대로 200.
    const state = live(vm).state;
    if (action === 'start' && state === 'running') return HttpResponse.json(vm as unknown as JsonBodyType);
    if (action === 'stop' && state === 'stopped') return HttpResponse.json(vm as unknown as JsonBodyType);
    if (action === 'start' && state !== 'stopped')
      return error(
        409,
        'INVALID_VM_STATE',
        `지금 상태(${state})에서는 시작할 수 없습니다. 정지 후 다시 시작하세요.`,
      );
    const next: Vm = { ...vm, state: action === 'start' ? 'running' : 'stopped' };
    vms = vms.map((v) => (v.vm_id === vm.vm_id ? next : v));
    // 주행 시나리오에도 알린다 — 자율주행 VM 을 멈추면 수동주행이 바로 이어받는다 (계약 §5.2)
    if (action === 'stop') stopVm(vm.vm_id);
    else startVm(vm.vm_id);
    return HttpResponse.json(next as unknown as JsonBodyType);
  }),

  http.delete('*/api/v1/vms/:vmId', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    if (!find(String(params.vmId))) return vmNotFound();
    vms = vms.filter((vm) => vm.vm_id !== params.vmId);
    pinningByVm.delete(String(params.vmId));
    return new HttpResponse(null, { status: 204 });
  }),

  // ---- 화면 배치 (REST-23·REST-24, 백엔드 -129) ----
  http.get('*/api/v1/displays', async ({ request }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    return HttpResponse.json({
      items: getDisplays(),
      observed_at: new Date().toISOString(),
      stale: false,
    } as unknown as JsonBodyType);
  }),

  http.put('*/api/v1/displays/:displayId/route', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const body = (await request.json()) as {
      vm_id?: string | null;
      view?: string;
      expected_revision?: number;
    };
    try {
      return HttpResponse.json(setRoute(String(params.displayId), body) as unknown as JsonBodyType, {
        status: 202,
      });
    } catch (e) {
      if (!(e instanceof RouteError)) throw e;
      const message =
        e.code === 'DISPLAY_REVISION_CONFLICT'
          ? '다른 사람이 먼저 배치를 바꿨습니다. 새로 읽고 다시 하세요.'
          : e.code;
      return error(e.status, e.code, message);
    }
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
    if (params.vmId === VM_IDS.autonomous || params.vmId === VM_IDS.manual) {
      const vmId = String(params.vmId);
      return HttpResponse.json({
        items: [
          ...mockKernelHeapSamples(vmId),
          ...mockMemoryPoolSamples(vmId),
          ...mockZephyrNetworkSamples(vmId),
        ],
      } as unknown as JsonBodyType);
    }
    if (params.vmId !== VM_IDS.interaction) return HttpResponse.json({ items: [] });
    const now = Date.now();
    const items = Array.from({ length: 30 }, (_, i) => {
      const t = now - (29 - i) * 10_000;
      const wave = Math.sin(t / 40_000);
      const usedBytes = (142 + 10 * wave) * 1024 * 1024;
      return {
        sample_id: `m-${t}`,
        vm_id: VM_IDS.interaction,
        boot_id: 'mock-boot-interaction',
        source: 'guest_agent',
        observed_at: new Date(t).toISOString(),
        values: [
          {
            name: VM_CPU_PERCENT_METRIC,
            value: Math.round((8 + 4 * wave) * 10) / 10,
            unit: '%',
            labels: null,
            unavailable_reason: null,
          },
          {
            name: 'memory_used_bytes',
            value: usedBytes,
            unit: 'bytes',
            labels: null,
            unavailable_reason: null,
          },
          {
            // MemAvailable 은 페이지 캐시 일부를 포함해 할당량 − 사용량보다 조금 작다
            name: 'memory_available_bytes',
            value: 512 * 1024 * 1024 - usedBytes - 24 * 1024 * 1024,
            unit: 'bytes',
            labels: null,
            unavailable_reason: null,
          },
          ...(['xvda', 'xvdb'] as const).flatMap((device, d) => [
            {
              name: 'disk_read_bytes_total',
              value: (d === 0 ? 180_000_000 : 4_000_000) + i * (d === 0 ? 96_000 : 4_096),
              unit: 'bytes',
              labels: { device },
              unavailable_reason: null,
            },
            {
              name: 'disk_write_bytes_total',
              value: (d === 0 ? 62_000_000 : 1_000_000) + i * (d === 0 ? 40_960 : 0),
              unit: 'bytes',
              labels: { device },
              unavailable_reason: null,
            },
          ]),
          {
            name: 'net_rx_bytes_total',
            value: 1_200_000 + i * 8_000 + Math.round(3_000 * (1 + wave)),
            unit: 'bytes',
            labels: { iface: 'eth0' },
            unavailable_reason: null,
          },
          {
            name: 'net_tx_bytes_total',
            value: 640_000 + i * 3_200,
            unit: 'bytes',
            labels: { iface: 'eth0' },
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
        { pid: 412, name: 'interaction', cpu_percent: 4.2, rss_bytes: 48_234_496 },
        { pid: 388, name: 'xenova-agent', cpu_percent: 1.1, rss_bytes: 21_495_808 },
        { pid: 1, name: 'systemd', cpu_percent: 0.2, rss_bytes: 11_010_048 },
      ],
    } as unknown as JsonBodyType);
  }),

  http.get('*/api/v1/vms/:vmId/threads', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const zephyr = params.vmId === VM_IDS.autonomous || params.vmId === VM_IDS.manual;
    return HttpResponse.json({
      vm_id: params.vmId,
      window_ms: zephyr ? 1000 : null,
      received_at: zephyr ? new Date().toISOString() : null,
      stale: !zephyr,
      threads_status: zephyr ? 'ok' : null,
      parts_received: zephyr ? 1 : 0,
      part_count: zephyr ? 1 : null,
      complete: zephyr,
      conflicting_pages: false,
      items: zephyr
        ? [
            {
              thread_id: '1',
              thread_name: 'telemetry',
              name_truncated: false,
              cpu_percent: 1.2,
              unavailable_reason: null,
            },
          ]
        : [],
    });
  }),

  http.get('*/api/v1/vms/:vmId/telemetry/heartbeat', async ({ request, params }) => {
    await delay(LATENCY_MS);
    if (!authorized(request)) return unauthorized();
    const known = Object.values(VM_IDS).some((id) => id === params.vmId);
    return HttpResponse.json({
      vm_id: params.vmId,
      status: known ? 'FRESH' : 'UNKNOWN',
      publisher_state: known ? 'running' : null,
      last_collection_success_uptime_ms: known ? '12000' : null,
      last_collection_success_age_ms: known ? 500 : null,
      collection_failures_total: known ? 2 : null,
      received_at: known ? new Date().toISOString() : null,
      age_ms: known ? 0 : null,
    });
  }),
];
