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

## Docker 이미지

하나의 Dockerfile에서 관리 콘솔과 모니터 화면을 서로 독립된 이미지로 빌드합니다.

```bash
docker build --target admin-console -t xenova-admin-console ./frontend
docker build --target vm-screens -t xenova-vm-screens ./frontend

docker run --rm -p 8080:8080 xenova-admin-console
docker run --rm -p 8081:8080 xenova-vm-screens
```

두 이미지는 Nginx에서 SPA 경로 새로고침을 지원하고 `/healthz`로 상태를 확인합니다. 관리 콘솔의
모니터 링크 주소는 이미지 빌드 시 `--build-arg VITE_SCREENS_ORIGIN=...`으로 지정할 수 있습니다.
API·WebSocket 프록시와 운영 도메인은 배포 Nginx 설정에서 연결합니다.

- 콘솔 환경변수는 `apps/admin-console/.env.example` 을 `.env.local` 로 복사해 바꿉니다 (`VITE_BACKEND_ORIGIN`, `VITE_API_BASE_URL`, `VITE_MOCK`).
- 디자인 확인만 할 때는 로그인 없이 `http://localhost:5173/components-public`.

## 관리 콘솔

화면은 [화면 정의서](../docs/design/screen-spec.md) W-01~W-08 의 초안입니다 (Stitch 시안 색·배치). 위쪽 메뉴 아래 **주행 모드 띠**가 모든 화면에 고정됩니다.

| 경로                      | 화면                                | 데이터                                 | 이어받을 작업            |
| ------------------------- | ----------------------------------- | -------------------------------------- | ------------------------ |
| `/login`                  | W-01 로그인                         | auth                                   | -53                      |
| `/`                       | W-02 대시보드 (주행 통합)           | `/ws/v1/drive/state`, vms, events      | 공병우 (정의서 Q2)       |
| `/vms`, `/vms/new`        | W-03 목록 · W-05 생성               | vms, templates                         | -55 · -159               |
| `/vms/:vmId`              | W-04 상세 (개요·차트·프로세스·콘솔) | vms, metrics, processes, 콘솔 WS-03    | 정수환                   |
| `/resources`              | W-06 자원 제어                      | 없음 (-107) — 화면 안 미리보기         | 정수환 -113·-114         |
| `/demo`                   | W-07 시연 제어                      | drive state·input. 배치·격리·재개 없음 | -129·-135, 재개 API 계약 |
| `/console`                | 웹 콘솔 (UI-05·MO-04)               | WS `/ws/v1/vms/{id}/console`           | platform -248            |
| `/events`                 | W-08 이벤트 타임라인                | events (3초 재조회)                    | -160                     |
| `/screens`, `/components` | 모니터 바로가기, 컴포넌트           | —                                      |                          |

VM 은 "VM 1·2·3" 이 아니라 역할 이름(자율주행·수동주행·네비게이션)으로 부릅니다 (`src/domain/roles.ts`). 이벤트 분류·문구는 `src/domain/events.ts`.

### 웹 콘솔 (UI-05 / MO-04)

VM 상세 화면의 **웹 콘솔 → 연결**에서 실제 게스트 터미널에 접속합니다.
기존 웹 로그인 뒤 게스트 로그인은 해당 OS 계정으로 별도 진행합니다.
연결 해제·페이지 이동은 VM을 종료하지 않습니다. Zephyr Starter는 셸이 없어 현재 로그 확인 범위입니다.
중복 접속·VM 종료·인증 만료를 안내하고 자동으로 명령을 재전송하거나 재접속하지 않습니다.

`@xterm/xterm`과 `@xterm/addon-fit`을 사용하며, Spring이 내부 dom0 서비스 토큰을 대신 처리합니다.
프론트 설정에 서비스 토큰을 넣지 않습니다. 연결 경로·메시지는
[웹 터미널 계약](../docs/contracts/browser-console-v1.md)에 정의합니다.
상세 화면 카드 아래와 위쪽 메뉴 `/console` 두 곳에서 열 수 있습니다.

API 타입(`src/api/types.ts`)은 팀 문서를 그대로 옮겼습니다. 문서가 바뀌면 타입도 같이 고칩니다.

- VM·템플릿·오류: [VM 관리 API 계약 v1](../docs/contracts/vm-api-contract-v1.md) §2.2·§2.3·§3.3·§5·§8.2·§9
- 인증: 노션 API 명세 "로그인"·"토큰 재발급" — Access Token 은 메모리에만, 401 이면 refresh 쿠키로 한 번 재발급 후 재시도

### 백엔드 없이 돌리기 (MSW mock)

```bash
VITE_MOCK=true npm run dev:console    # 또는 .env.local 에 VITE_MOCK=true
```

- 로그인: 아이디 `admin`, 비밀번호 `1234` (mock 전용 값, `src/mocks/data.ts`)
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

- **모니터는 보여 주기만 합니다** (화면 정의서 §1). 버튼·키 입력이 없고, 강제 종료·부하·재개·수동 키는 모두 관리 콘솔 **시연 제어**에서 합니다. 아래 띠는 데이터 출처·연결 상태와 화면 링크뿐이고 키오스크(`?kiosk=1`)에서는 숨깁니다.
- 데이터 출처 (`VITE_TELEMETRY`): 비움 = 관리 콘솔 mock 시나리오를 따라감 · `live` = 백엔드 중계(`/ws/v1/drive/state`, `/ws/v1/events`, 시나리오 D2) · `scenario` = 이 앱 혼자 도는 가짜 시나리오.
- 복구 서비스의 `recovery.started` 를 받으면 그 앱 칸은 명령이 다시 신선해질 때까지 '복구 중'(노랑)으로 표시합니다.
- 색·배치는 Stitch 시안(a_1·b_vm_1·interaction 계열)을 따릅니다. 1920×1080 기준.

### 모니터를 콘솔과 같이 보기 (mock)

콘솔 mock 시나리오는 브라우저 채널(BroadcastChannel)로 상태를 내보내므로 두 화면이 **같은 origin** 이어야 합니다. 콘솔 개발 서버가 `/monitor/` 를 모니터 개발 서버로 넘깁니다.

```bash
cd apps/vm-screens    && VITE_BASE=/monitor/ npx vite --port 5174       # Git Bash 는 앞에 MSYS_NO_PATHCONV=1
cd apps/admin-console && VITE_MOCK=true npx vite --port 5173            # VITE_MONITOR_ORIGIN 기본 http://localhost:5174
```

- 콘솔: `http://localhost:5173/demo` (admin / 1234) — 여기서 [강제 종료] → 인계 → 복구 → AUTO_READY → [자율주행 재개]
- 콘솔: `http://localhost:5173/demo` 의 세 VM 카드마다 [강제 종료]. 재개는 바로 바뀌지 않고 3·2·1 을 센 뒤 AUTO (MQTT 계약 v1.2 §6)
- 카메라: 세 번째 VM 이 젯슨 카메라 영상을 받아 AI 로 장애물을 감지하고, 감지 박스를 그린 영상을 낸다 — 대시보드 세 번째 VM 카드와 모니터 그 칸·상세에 보인다. 실제는 `/camera/stream.mjpg`(dom0 웹 프록시 → 세 번째 VM, 카메라 계약 `docs/contracts/camera-stream-v1.md`, `VITE_CAMERA_URL` 로 바꿀 수 있음), mock 은 차량 움직임을 따라가는 시험 영상. 감지 결과는 화면용이고 주행 판단에 쓰지 않는다 — 이 VM 을 강제 종료하면 영상만 멈추고 주행은 그대로다
- 웹 콘솔: 위쪽 메뉴 [웹 콘솔] 에서 VM 을 골라 [연결]. 웹 터미널 계약 `browser-console-v1` 그대로 — 자동 접속·재접속 없음, [연결 해제] 로 점유를 푼다(platform -248). mock 에서는 가짜 셸(Debian: `help`·`uname -a`·`ps` 등, Zephyr: 부팅 로그만)
- 모니터: `http://localhost:5173/monitor/split?kiosk=1`, 상세는 `/monitor/detail/<앱>`. 실제로는 dom0 표시 서비스가 콘솔 [화면 배치] 에 따라 키오스크 주소를 바꾼다(화면 배치 계약 `docs/contracts/display-route-v1.md`). mock 에서는 `?display=display-0` 으로 열면 모니터가 표시 서비스 대신 콘솔 mock 배치를 따라간다. 콘솔 탭이 열려 있는 동안 따라갑니다.

## 규칙

- 스타일: CSS 변수 토큰(`packages/ui/src/tokens.css`) + CSS Modules. 색은 토큰만 씁니다.
- 상태 표시는 `StatusBadge` 의 네 가지(정상·장애·복구 중·정지)로 통일합니다.
- 시험: `*.test.ts(x)` 를 소스 옆에 두면 `npm test` 가 찾습니다 (Vitest + Testing Library).
