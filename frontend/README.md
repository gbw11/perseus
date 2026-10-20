# frontend

관리 콘솔과 모니터 화면(VM 앱 화면)이 같은 디자인 시스템을 쓰는 npm workspaces 입니다.

```text
frontend/
├── packages/ui/          # 디자인 시스템: 토큰(tokens.css) + Button·Card·StatusBadge·Modal·Toast·AppShell
├── apps/admin-console/   # 관리 콘솔 (노트북 브라우저, 밝은 테마)        :5173
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

| 경로                 | 내용                            | 비고                                |
| -------------------- | ------------------------------- | ----------------------------------- |
| `/login`             | 로그인 기본형                   | 화면 완성은 S15P31E110-53           |
| `/vms`, `/vms/:vmId` | VM 목록·상세 기본형 (시작·정지) | 카드 화면은 -55, 상세는 정수환 작업 |
| `/screens`           | 모니터 화면 바로가기            | 화면 라우터 제어는 -129·-130        |
| `/components`        | 공통 컴포넌트 예시              | 새 컴포넌트는 여기에 같이 올린다    |

API 타입(`src/api/types.ts`)은 팀 문서를 그대로 옮겼습니다. 문서가 바뀌면 타입도 같이 고칩니다.

- VM·템플릿·오류: [VM 관리 API 계약 v1](../docs/contracts/vm-api-contract-v1.md) §2.2·§2.3·§3.3·§5·§8.2·§9
- 인증: 노션 API 명세 "로그인"·"토큰 재발급" — Access Token 은 메모리에만, 401 이면 refresh 쿠키로 한 번 재발급 후 재시도

## 모니터 화면

| 경로                                            | 화면                              |
| ----------------------------------------------- | --------------------------------- |
| `/split`                                        | 모니터 1 — VM 3개 3분할           |
| `/detail/autonomous` · `manual` · `interaction` | 모니터 2 — 관리자가 고른 1개 상세 |

- 지금은 **가짜 시나리오**(`src/telemetry/scenario.ts`)로 움직입니다. 규칙은 [MQTT 계약](../docs/contracts/mqtt-contract-v1.md) §5 와 [RC카 시나리오](../docs/scenario-rc-car.md) §4 를 흉내 냅니다 — 즉시 인계(100 ms), 복구, AUTO_READY(3초), 재개, SAFE_STOP, 격리.
- 아래 조작 바로 장애를 넣습니다. 키오스크에서는 `?kiosk=1` 로 숨깁니다.
- 화면 이동은 조작 바의 링크로 합니다 (주소창으로 이동하면 가짜 상태가 처음부터 다시 시작).
- 실제 데이터 경로(브로커 WebSocket 직결 / 백엔드 중계)는 RC카 시나리오 §8 D2 결정 뒤 `TelemetryProvider` 와 같은 모양으로 붙입니다.

## 규칙

- 스타일: CSS 변수 토큰(`packages/ui/src/tokens.css`) + CSS Modules. 색은 토큰만 씁니다.
- 상태 표시는 `StatusBadge` 의 네 가지(정상·장애·복구 중·정지)로 통일합니다.
- 시험: `*.test.ts(x)` 를 소스 옆에 두면 `npm test` 가 찾습니다 (Vitest + Testing Library).
