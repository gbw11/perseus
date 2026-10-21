# frontend

관리 콘솔과 모니터 화면(VM 앱 화면)이 같은 디자인 시스템을 쓰는 npm workspaces 입니다.

```text
frontend/
├── packages/ui/          # 디자인 시스템: 토큰(tokens.css) + Button·Card·StatusBadge·Modal·Toast·AppShell
├── apps/admin-console/   # 관리 콘솔 (노트북 브라우저, 어두운 테마)      :5173
└── apps/vm-screens/      # 모니터 화면 — 3분할·상세 (dom0 키오스크, 어두운 테마) :5174
```

콘솔과 모니터 화면을 따로 빌드해서, 콘솔에 문제가 생겨도 데모 화면은 영향을 받지 않습니다.

## 실행

Node 20 이상.

```bash
cd frontend
npm ci
npm run dev:console      # http://localhost:5173  (/api·/ws 를 http://localhost:8080 백엔드로 프록시)
npm run dev:screens      # http://localhost:5174/split
npm run lint && npm test && npm run build
```

- 콘솔 환경변수는 `apps/admin-console/.env.example` 을 `.env.local` 로 복사해 바꿉니다 (`VITE_BACKEND_ORIGIN`, `VITE_API_BASE_URL`, `VITE_MOCK`).
- 디자인 확인만 할 때는 로그인 없이 `http://localhost:5173/components-public`.

## 관리 콘솔

화면은 [화면 정의서](../docs/design/screen-spec.md) W-01~W-08 의 초안입니다 (Stitch 시안 색·배치). 위쪽 메뉴 아래 **주행 모드 띠**가 모든 화면에 고정됩니다.

| 경로                      | 화면                                | 데이터                                   | 이어받을 작업            |
| ------------------------- | ----------------------------------- | ---------------------------------------- | ------------------------ |
| `/login`                  | W-01 로그인                         | auth                                     | -53                      |
| `/`                       | W-02 대시보드 (주행 통합)           | `/ws/v1/drive/state`, vms, events        | 공병우 (정의서 Q2)       |
| `/vms`, `/vms/new`        | W-03 목록 · W-05 생성               | vms, templates                           | -55 · -159               |
| `/vms/:vmId`              | W-04 상세 (개요·차트·프로세스·콘솔) | vms, metrics, processes. 콘솔 WS-03 없음 | 정수환                   |
| `/resources`              | W-06 자원 제어                      | 없음 (-107) — 화면 안 미리보기           | 정수환 -113·-114         |
| `/demo`                   | W-07 시연 제어                      | drive state·input. 배치·격리·재개 없음   | -129·-135, 재개 API 계약 |
| `/events`                 | W-08 이벤트 타임라인                | events (3초 재조회)                      | -160                     |
| `/screens`, `/components` | 모니터 바로가기, 컴포넌트           | —                                        |                          |

VM 은 "VM 1·2·3" 이 아니라 역할 이름(자율주행·수동주행·인터랙션)으로 부릅니다 (`src/domain/roles.ts`). 이벤트 분류·문구는 `src/domain/events.ts`.

API 타입(`src/api/types.ts`)은 팀 문서를 그대로 옮겼습니다. 문서가 바뀌면 타입도 같이 고칩니다.

- VM·템플릿·오류: [VM 관리 API 계약 v1](../docs/contracts/vm-api-contract-v1.md) §2.2·§2.3·§3.3·§5·§8.2·§9
- 인증: 노션 API 명세 "로그인"·"토큰 재발급" — Access Token 은 메모리에만, 401 이면 refresh 쿠키로 한 번 재발급 후 재시도

### 백엔드 없이 돌리기 (MSW mock)

```bash
VITE_MOCK=true npm run dev:console    # 또는 .env.local 에 VITE_MOCK=true
```

- 로그인: 아이디 `admin`, 비밀번호 `mock-password` (mock 전용 값, `src/mocks/data.ts`)
- VM 3대(autonomous·manual 은 Zephyr 16 MiB, interaction 은 Debian 13)와 템플릿 3개(Ubuntu 24.04·Debian 13·Zephyr)가 들어 있습니다. 시작·정지·생성·삭제가 메모리에서 동작하고, 새로고침하면 처음 상태로 돌아갑니다.
- 주행 상태·키 입력은 WS 대신 **표본 시연 시나리오**(`src/mocks/scenario.ts`)로 돕니다: 시연 제어에서 [강제 종료] → MANUAL 인계 → 복구 → AUTO_READY → [자율주행 재개] → AUTO. [인지 끊김 (D5)] 은 모드를 바꾸지 않고 정지 명령만 냅니다. 이벤트·메트릭·프로세스도 mock 이 답합니다.
- 상태 코드·오류 코드는 실제 API 와 같습니다 — 401 `INVALID_CREDENTIALS`·`INVALID_TOKEN`, 404 `VM_NOT_FOUND`, 409 `INVALID_VM_STATE`·`VM_NAME_CONFLICT`, 422 `VALIDATION_ERROR`(§9 입력값).
- mock 에 없는 요청(`/ws/...` 등)은 그대로 백엔드로 갑니다.
- 핸들러는 `src/mocks/handlers.ts`. API 가 바뀌면 `api/types.ts` 와 같이 고치고, `handlers.test.ts` 가 실제 클라이언트(`api/client.ts`)로 로그인 → 목록 → 상세 흐름을 확인합니다.
- `public/mockServiceWorker.js` 는 `npx msw init public` 이 만든 파일이라 손대지 않습니다 (msw 를 올리면 다시 생성).

## 모니터 화면

| 경로                                            | 화면                              |
| ----------------------------------------------- | --------------------------------- |
| `/split`                                        | 모니터 1 — VM 3개 3분할           |
| `/detail/autonomous` · `manual` · `interaction` | 모니터 2 — 관리자가 고른 1개 상세 |

- 지금은 **가짜 시나리오**(`src/telemetry/scenario.ts`)로 움직입니다. 규칙은 [MQTT 계약](../docs/contracts/mqtt-contract-v1.md) §5 와 [RC카 시나리오](../docs/scenario-rc-car.md) §4 를 흉내 냅니다 — 즉시 인계(100 ms), 복구, AUTO_READY(3초), 재개, SAFE_STOP, 격리.
- 아래 조작 바로 장애를 넣습니다. 키오스크에서는 `?kiosk=1` 로 숨깁니다.
- 화면 이동은 조작 바의 링크로 합니다 (주소창으로 이동하면 가짜 상태가 처음부터 다시 시작).
- 실제 데이터는 백엔드 중계(`/ws/v1/drive/state`, `/ws/v1/events`, 시나리오 D2)로 받습니다 — `VITE_TELEMETRY=live` (`.env.example`).
- 모니터는 보여 주기만 합니다 (화면 정의서 §1): 화면 안에 버튼이 없고, 수동주행 키·자율주행 재개는 관리 콘솔 시연 제어에서 합니다. 수동주행 상세의 키 표시는 웹에서 들어온 입력을 보여 줄 뿐입니다.
- 색·배치는 Stitch 시안(a_1·b_vm_1·interaction 계열)을 따릅니다. 1920×1080 기준.

## 규칙

- 스타일: CSS 변수 토큰(`packages/ui/src/tokens.css`) + CSS Modules. 색은 토큰만 씁니다.
- 상태 표시는 `StatusBadge` 의 네 가지(정상·장애·복구 중·정지)로 통일합니다.
- 시험: `*.test.ts(x)` 를 소스 옆에 두면 `npm test` 가 찾습니다 (Vitest + Testing Library).
