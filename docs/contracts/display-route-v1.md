# 화면 배치 계약 v1

**최종 갱신** 2026-10-22 v1.1 초안 (view `sim` 추가 — 변경 이력)
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼, RC카 시나리오
**구성 계층** 관리 콘솔 ↔ Spring 백엔드 ↔ dom0 표시 서비스 ↔ 키오스크 브라우저 (모니터 2대)

모니터 1·2(HDMI 0·1)에 어떤 화면을 띄울지 정하고 바꾸는 경로의 기준 문서입니다. 콘솔이 백엔드에 "이 모니터에 이 화면" 을 요청하면, 백엔드가 dom0 표시 서비스에 알리고, 표시 서비스가 그 모니터의 키오스크 브라우저를 그 주소로 옮긴 뒤 결과를 백엔드에 돌려줍니다.

**모니터 브라우저는 스스로 배치를 따라가지 않습니다.** 모니터 화면(`frontend/apps/vm-screens`)은 받은 주소만 그립니다. 어느 주소를 열지는 표시 서비스가 정합니다 (2026-10-21 결정).

필드·프레임·오류 코드를 바꾸면 백엔드·표시 서비스·콘솔 담당이 함께 리뷰합니다 (CONTRIBUTING §6).

---

## 1. 구성

```text
 관리 콘솔 (사용자 JWT)
   │  REST-23 GET  /api/v1/displays
   │  REST-24 PUT  /api/v1/displays/{display_id}/route
   │  WS  /ws/v1/displays          ◀── display.snapshot (읽기 전용)
   ▼
 Spring 백엔드  ── 배치 상태의 원본 (desired_route · revision)
   │  WS-04  /ws/v1/display-service   (서비스 토큰, 연결 1개)
   │    ▶ route.set               ◀ service.hello · display.status · route.applied · route.failed
   ▼
 dom0 표시 서비스 (platform/services/display)
   │  키오스크 브라우저를 {기준 주소}{screen_path} 로 연다
   ▼
 모니터 1 (display-0, HDMI 0)  /  모니터 2 (display-1, HDMI 1)
```

| 용어 | 뜻 |
| --- | --- |
| 디스플레이 | 모니터 한 대. `display-0`(모니터 1), `display-1`(모니터 2) 두 개로 고정 |
| view | 화면 종류. `split`(3분할, 모니터 전체) / `os-face`(VM 1개의 상세 화면) / `sim`(시뮬레이터 화면 — [시뮬레이터 시연 시나리오](../scenario-simulator.md)) |
| desired_route | 콘솔이 마지막으로 요청한 배치 |
| observed_route | 표시 서비스가 `route.applied` 로 "다 띄웠다" 고 확인한 배치 |
| revision | 디스플레이마다 배치가 바뀔 때 1씩 오르는 번호. 시작값 1 |

- 배치 상태는 백엔드 메모리에만 있다. 백엔드가 재시작하면 두 모니터 모두 `split`, revision 1 로 돌아간다.

## 2. Display 객체

REST 응답과 WS `display.snapshot` 의 `items` 가 같은 모양을 쓴다. 필드 이름은 snake_case.

```json
{
  "display_id": "display-0",
  "label": "모니터 1",
  "connected": true,
  "status": "ACTIVE",
  "supported_views": ["split", "os-face", "sim"],
  "revision": 3,
  "desired_route": {
    "vm_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
    "view": "os-face",
    "request_id": "0d6c1f5e-6b0a-4a43-9a51-2f6e2a1c7b10",
    "applied_at": null,
    "verification_level": null
  },
  "observed_route": {
    "vm_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
    "view": "os-face",
    "request_id": "0d6c1f5e-6b0a-4a43-9a51-2f6e2a1c7b10",
    "applied_at": "2026-10-21T09:00:01.200Z",
    "verification_level": "kiosk_loaded"
  },
  "last_error": null
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `display_id` | string | `display-0` / `display-1` |
| `label` | string | 화면에 보일 이름 (`모니터 1`, `모니터 2`) |
| `connected` | bool | 표시 서비스가 붙어 있고, 그 디스플레이의 HDMI·키오스크 브라우저가 떠 있음 |
| `status` | string | `DISCONNECTED` / `IDLE` / `SWITCHING` / `ACTIVE` / `ERROR` (5.4) |
| `supported_views` | string[] | `["split", "os-face", "sim"]` |
| `revision` | int | 지금 desired_route 의 번호 |
| `desired_route` | Route | 콘솔이 마지막으로 요청한 배치. 처음에는 `{vm_id: null, view: "split", request_id: "initial"}` |
| `observed_route` | Route \| null | 표시 서비스가 확인한 배치. 확인 전·연결이 끊긴 뒤에는 null |
| `last_error` | string \| null | 마지막 `route.failed` 의 `reason`. `ERROR` 가 아니면 null |

**Route**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `vm_id` | string \| null | `os-face` 일 때 그 VM. `split`·`sim` 이면 null |
| `view` | string | `split` / `os-face` / `sim` |
| `request_id` | string | PUT 마다 백엔드가 만드는 UUID. `route.set`·`route.applied` 가 같은 값을 쓴다 |
| `applied_at` | string \| null | ISO-8601 UTC. observed_route 에만 들어간다 (백엔드가 `route.applied` 를 받은 시각) |
| `verification_level` | string \| null | observed_route 에만 들어간다. v1 은 `kiosk_loaded`(키오스크가 그 주소를 다 불러옴) 하나 |

## 3. 콘솔 REST

인증은 다른 콘솔 API 와 같은 사용자 JWT (`Authorization: Bearer ...`). 오류 본문은 [VM 관리 API 계약](vm-api-contract-v1.md) 8.2 와 같은 `{code, message}`.

| ID | 메서드 | 경로 | 성공 | 오류 |
| --- | --- | --- | --- | --- |
| REST-23 | GET | `/api/v1/displays` | 200 | 401 |
| REST-24 | PUT | `/api/v1/displays/{display_id}/route` | 202 | 401, 404, 409, 422 |

### 3.1 `GET /api/v1/displays`

```json
{
  "items": [ { "display_id": "display-0", "...": "2장 Display" }, { "display_id": "display-1", "...": "..." } ],
  "observed_at": "2026-10-21T09:00:02.000Z",
  "stale": false
}
```

| 필드 | 설명 |
| --- | --- |
| `items` | Display 2개. `display-0`, `display-1` 순서 |
| `observed_at` | 백엔드가 이 목록을 만든 시각 |
| `stale` | 예약. v1 은 항상 `false` |

### 3.2 `PUT /api/v1/displays/{display_id}/route`

요청

```json
{ "vm_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "view": "os-face", "expected_revision": 2 }
```

```json
{ "vm_id": null, "view": "split", "expected_revision": 3 }
```

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `view` | O | `split` / `os-face` / `sim` |
| `vm_id` | `os-face` 일 때 | `os-face` 이면 필수, `split`·`sim` 이면 null 이거나 생략 |
| `expected_revision` | O | 콘솔이 마지막으로 본 `revision`. 지금 값과 다르면 409 (다른 사람이 먼저 바꿈) |

응답 `202 Accepted` — 바뀐 Display (2장). `revision` 이 1 오르고 `desired_route` 가 새 값, `status` 는 `SWITCHING`. 모니터가 실제로 바뀐 것은 아직 아니다. 결과는 WS `display.snapshot` 이나 GET 으로 본다 (`ACTIVE` 또는 `ERROR`).

- 같은 배치를 다시 요청해도 revision 이 오르고 `route.set` 을 다시 보낸다 (화면 새로 고침으로 쓸 수 있다).
- 표시 서비스가 끊겨 있어도 202 를 준다. desired_route 만 바뀌고, 서비스가 다시 붙으면 그 값을 보낸다 (5.5).

### 3.3 오류

검사 순서대로 적는다. 앞에서 걸리면 뒤는 보지 않는다.

| HTTP | `code` | 언제 |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` / `INVALID_TOKEN` | JWT 없음 / 틀림·만료 (다른 콘솔 API 와 같음) |
| 404 | `DISPLAY_NOT_FOUND` | `display_id` 가 `display-0`·`display-1` 이 아님 |
| 422 | `VALIDATION_ERROR` | `view` 없음, `expected_revision` 없음·정수 아님, 필드 타입 오류, `split`·`sim` 인데 `vm_id` 있음, `os-face` 인데 `vm_id` 없음 |
| 422 | `UNSUPPORTED_VIEW` | `view` 가 `split`·`os-face`·`sim` 이 아님 |
| 404 | `VM_NOT_FOUND` | `vm_id` 가 등록된 VM 이 아님 |
| 422 | `VM_ROLE_UNKNOWN` | `os-face` 인데 그 VM 에 화면 역할(role)이 없음 — `screen_path` 를 만들 수 없다 (5.6) |
| 409 | `DISPLAY_REVISION_CONFLICT` | `expected_revision` 이 지금 `revision` 과 다름. `message` 에 지금 revision 을 적는다 |

## 4. 콘솔 실시간 `WS /ws/v1/displays`

콘솔이 배치 상태를 실시간으로 받는 **읽기 전용** 소켓이다. 쓰지 않아도 된다 (GET 을 다시 불러도 같다).

| 순서 | 방향 | 프레임 |
| --- | --- | --- |
| 1 | 콘솔 → 백엔드 | `{"type":"auth","access_token":"<사용자 JWT>"}` — 첫 프레임. 10초 안에 안 오거나 틀리면 4401 로 닫음 |
| 2 | 백엔드 → 콘솔 | `{"type":"authenticated"}` |
| 3 | 백엔드 → 콘솔 | `{"type":"display.snapshot","items":[ ...Display 2개... ]}` — 인증 직후 한 번, 그 뒤 배치·상태가 바뀔 때마다 |

- 클라이언트 명령은 없다. `{"type":"ping"}` 에만 `{"type":"pong"}` 으로 답하고, 그 밖의 프레임은 `{"type":"error","code":"VALIDATION_ERROR","message":"..."}` 로 답한다.
- snapshot 은 늘 지금 목록 전체다. 받은 것으로 통째로 바꾸면 된다 (순서가 늦게 와도 옛 상태로 돌아가지 않는다).

## 5. dom0 표시 서비스 ↔ 백엔드 `WS /ws/v1/display-service` (WS-04)

표시 서비스(dom0)가 백엔드에 붙는 소켓이다. 표시 서비스가 클라이언트, 백엔드가 서버.

### 5.1 인증과 연결

| 항목 | 규칙 |
| --- | --- |
| 첫 프레임 | `{"type":"auth","service_token":"<공유 비밀>"}` |
| 공유 비밀 | 백엔드 설정 `xenova.display.service-token` (환경변수 `XENOVA_DISPLAY_SERVICE_TOKEN`). 표시 서비스도 같은 값을 설정으로 받는다. 사용자 JWT 는 쓰지 않는다 |
| 성공 | `{"type":"authenticated"}` |
| 실패 | 토큰이 틀리거나 첫 프레임이 auth 가 아니거나 10초 안에 안 오면 `{"type":"error","code":"UNAUTHORIZED",...}` 후 **4401** 로 닫음 |
| 동시 연결 | 1개. 새 연결이 인증에 성공하면 이전 연결을 **4409** 로 닫는다 |
| 생존 확인 | 아무 쪽이나 `{"type":"ping"}` → `{"type":"pong"}` |

닫힘 코드

| 코드 | 뜻 | 표시 서비스가 할 일 |
| --- | --- | --- |
| 4401 | 인증 실패 | 토큰 설정을 확인. 재연결해도 같은 결과면 로그를 남기고 간격을 늘린다 |
| 4409 | 다른 표시 서비스 연결이 들어옴 | 같은 dom0 에서 서비스가 두 번 떴는지 확인. 자동 재연결하지 않는다 |
| 그 밖 (1001·1006·1011 등) | 백엔드 재시작·네트워크 끊김 | 5.5 재연결 |

### 5.2 프레임

**표시 서비스 → 백엔드**

| `type` | 언제 | 필드 |
| --- | --- | --- |
| `service.hello` | 인증 직후 한 번 (재연결마다) | `displays: [{display_id, connected}]` — 디스플레이 2개 모두 |
| `display.status` | 모니터가 꽂히거나 빠짐, 키오스크 브라우저가 뜨거나 죽음 | `display_id`, `connected` |
| `route.applied` | 키오스크가 `route.set` 의 주소를 다 띄움 | `display_id`, `revision`, `request_id` |
| `route.failed` | 띄우지 못함 | `display_id`, `revision`, `request_id`, `reason` |

```json
{"type":"service.hello","displays":[{"display_id":"display-0","connected":true},{"display_id":"display-1","connected":false}]}
{"type":"display.status","display_id":"display-1","connected":true}
{"type":"route.applied","display_id":"display-0","revision":3,"request_id":"0d6c1f5e-6b0a-4a43-9a51-2f6e2a1c7b10"}
{"type":"route.failed","display_id":"display-0","revision":3,"request_id":"0d6c1f5e-6b0a-4a43-9a51-2f6e2a1c7b10","reason":"load_timeout"}
```

- `connected` = 그 HDMI 에 모니터가 있고 키오스크 브라우저가 떠 있음.
- `reason` 은 사람이 읽을 짧은 문자열. 예: `load_timeout`(정해진 시간 안에 못 띄움), `kiosk_not_running`, `display_disconnected`, `http_502`. 콘솔은 그대로 보여준다.
- 표시 서비스는 `route.set` 을 받고 **10초** 안에 다 띄우지 못하면 `route.failed`(`load_timeout`)를 보낸다. 백엔드는 따로 시간 제한을 두지 않는다.

**백엔드 → 표시 서비스**

| `type` | 언제 | 필드 |
| --- | --- | --- |
| `route.set` | `service.hello` 직후 디스플레이마다 지금 desired_route 를 한 번씩, 그 뒤 PUT 이 성공할 때마다 | `display_id`, `revision`, `request_id`, `view`, `vm_id`, `app_id`, `screen_path` |
| `error` | 인증 실패, 형식 오류, 모르는 `display_id`·`type` | `code`, `message` |

```json
{"type":"route.set","display_id":"display-0","revision":3,"request_id":"0d6c1f5e-6b0a-4a43-9a51-2f6e2a1c7b10","view":"os-face","vm_id":"1b4e28ba-2fa1-11d2-883f-0016d3cca427","app_id":"autonomous","screen_path":"/detail/autonomous"}
{"type":"route.set","display_id":"display-1","revision":1,"request_id":"initial","view":"split","vm_id":null,"app_id":null,"screen_path":"/split"}
{"type":"error","code":"VALIDATION_ERROR","message":"지원하지 않는 type입니다: route.done"}
```

- 표시 서비스는 디스플레이마다 **가장 큰 revision 의 `route.set` 만** 따른다. 띄우는 도중 새 `route.set` 이 오면 옛 것은 버리고 새 것을 띄운다 (옛 것에는 답하지 않아도 된다).
- `error` 의 `code`: `UNAUTHORIZED`, `VALIDATION_ERROR`, `DISPLAY_NOT_FOUND`.

### 5.3 순서도 예시

모니터 1 을 자율주행 VM 상세 화면으로 바꾸는 경우.

```text
 콘솔                  백엔드                         표시 서비스               키오스크(모니터 1)
  │                      │◀── auth{service_token} ───────│                          │
  │                      │─── authenticated ────────────▶│                          │
  │                      │◀── service.hello ─────────────│  display-0 connected     │
  │                      │─── route.set rev1 /split ────▶│  (디스플레이마다 한 번)    │
  │                      │◀── route.applied rev1 ────────│◀── 로드 완료 ─────────────│   status ACTIVE
  │── PUT route ────────▶│  revision 1→2, SWITCHING      │                          │
  │◀─ 202 (SWITCHING) ───│  이벤트 display.route.changed  │                          │
  │◀─ display.snapshot ──│─── route.set rev2 ───────────▶│── /monitor/detail/autonomous 열기 ─▶│
  │                      │◀── route.applied rev2 ────────│◀── 로드 완료 ─────────────│
  │◀─ display.snapshot ──│  ACTIVE, observed_route=rev2  │                          │
```

실패하면 마지막 두 줄이 `route.failed rev2 (reason)` → `ERROR`, `last_error = reason` 이 된다.

### 5.4 status 판정

백엔드가 디스플레이마다 정한다. 위에서부터 먼저 맞는 것.

| 조건 | `status` | `connected` | 그 밖 |
| --- | --- | --- | --- |
| 표시 서비스 연결이 없음, 또는 그 디스플레이 `connected = false` | `DISCONNECTED` | false | `observed_route = null` |
| 서비스가 hello 했지만 그 디스플레이에 아직 `route.set` 을 보내기 전 | `IDLE` | true | |
| 지금 revision 에 `route.failed` 를 받음 | `ERROR` | true | `last_error = reason` |
| 지금 revision 에 `route.applied` 를 받음 | `ACTIVE` | true | `observed_route` = desired_route + `applied_at`, `verification_level: kiosk_loaded`. `last_error = null` |
| 그 밖 (지금 revision 을 아직 확인 못 함) | `SWITCHING` | true | |

- 지금 revision 보다 오래된 `route.applied`·`route.failed` 는 무시한다 (상태를 바꾸지 않고 오류도 보내지 않는다). 지금보다 큰 revision 이나 `request_id` 가 다른 것은 `VALIDATION_ERROR`.
- `ERROR` 에서 같은 배치를 다시 PUT 하면 새 revision 으로 다시 시도한다.

### 5.5 재연결

- 표시 서비스가 끊기면 백엔드는 두 디스플레이 모두 `DISCONNECTED`, `observed_route = null` 로 바꾸고 snapshot 을 보낸다. desired_route·revision 은 그대로 둔다.
- 표시 서비스는 끊기면(4409 제외) 다시 붙는다. 간격은 1초부터 두 배씩, 최대 30초.
- 다시 붙으면 auth → `service.hello` 부터 다시 한다. 백엔드는 hello 직후 디스플레이마다 지금 desired_route 로 `route.set` 을 다시 보낸다. 서비스는 이미 그 화면이 떠 있어도 `route.applied` 로 답한다.
- 백엔드가 재시작하면 배치가 `split` 으로 돌아가므로 (1장) 재연결 뒤 두 모니터 모두 3분할이 된다.

### 5.5.1 세부 규칙 (백엔드 -129 구현과 같다)

- `service.hello` 전에 온 `display.status`·`route.applied`·`route.failed` 는 `VALIDATION_ERROR` 로 답하고 무시한다.
- `service.hello` 목록에 없는 `display_id` 는 무시하고, 목록에서 빠진 디스플레이는 연결 안 됨(`DISCONNECTED`)으로 본다.
- `route.applied`·`route.failed` 에 `request_id` 가 없으면 `revision` 만으로 판단한다. 있는데 지금 것과 다르면 `VALIDATION_ERROR`.
- 같은 `revision` 에 결과가 두 번 오면 나중 것을 따른다 (`route.failed` 뒤 `route.applied` 면 `ACTIVE`, `last_error` 지움).
- 모니터가 빠졌다 다시 꽂히면(`display.status` connected false → true) 백엔드는 `route.set` 을 다시 보내지 않는다.
  표시 서비스가 마지막으로 받은 `route.set` 을 스스로 다시 띄우고 `route.applied` 를 보낸다 — 그 전까지 상태는 `SWITCHING`.

### 5.6 `screen_path` 만들기

백엔드가 `route.set` 에 넣는다. 표시 서비스는 자기 키오스크 기준 주소 앞에 붙여 그대로 연다 (예 기준 주소 `http://127.0.0.1:8080/monitor` + `/detail/autonomous`).

| `view` | VM role (`app_id`) | `app_id` | `screen_path` |
| --- | --- | --- | --- |
| `split` | — | null | `/split` |
| `os-face` | `autonomous` | `autonomous` | `/detail/autonomous` |
| `os-face` | `manual` | `manual` | `/detail/manual` |
| `os-face` | `message-board` | `message-board` | `/detail/interaction` |
| `os-face` | 없음 | — | PUT 단계에서 422 `VM_ROLE_UNKNOWN` (보내지 않음) |
| `sim` | — | null | `/sim` |

- 화면 경로 id 는 [MQTT 계약](mqtt-contract-v1.md) 4장의 `app_id` 와 같고, 메시지 보드만 `interaction` 이다.
- `/split` 의 세 번째 칸은 화면 앱이 스스로 정한다: 시뮬레이터 위치(MQTT-15 `sim.pose`, 백엔드 중계 WS-06)가 1초 안에 왔으면 **시뮬레이터**, 아니면 **인터랙션**. 배치 API 는 바꾸지 않는다 — 실차 구성(시뮬레이터 없음)에서는 저절로 인터랙션 칸이 된다 (2026-10-22 결정).
- 기준 주소·키오스크 실행 방식(브라우저 종류, 로드 완료 판단)은 표시 서비스 구현이 정한다.

## 6. 이벤트 `display.route.changed`

PUT 이 성공할 때마다 백엔드가 콘솔 이벤트 로그(REST-18, `/ws/v1/events`)에 남긴다.

| 항목 | 값 |
| --- | --- |
| type | `display.route.changed` |
| 심각도 | `INFO` |
| 대상 VM | `vm_id` (`split`·`sim` 이면 없음) |
| payload | `{display_id, view, vm_id, revision, request_id, requested_by}` |

```json
{"display_id":"display-0","view":"os-face","vm_id":"1b4e28ba-2fa1-11d2-883f-0016d3cca427","revision":2,"request_id":"0d6c1f5e-6b0a-4a43-9a51-2f6e2a1c7b10","requested_by":"admin"}
```

- `requested_by` 는 요청한 사용자의 로그인 이름.
- 적용 결과(`ACTIVE`·`ERROR`)는 v1 에서 이벤트로 남기지 않는다. snapshot 으로만 알린다.

## 7. 담당

| 부분 | 위치 | 담당 | Jira |
| --- | --- | --- | --- |
| 백엔드 REST·WS·배치 상태 | `backend/.../display/` | 공병우 | S15P31E110-129 |
| dom0 표시 서비스·키오스크 | `platform/services/display/` | 박신영 | |
| 콘솔 화면 배치 UI | `frontend/apps/admin-console` | | S15P31E110-130 |
| 모니터 화면 (주소만 받아 그림) | `frontend/apps/vm-screens` | | |

## 8. 미확정

- 키오스크 장치 인증 (모니터 화면이 백엔드 API 를 부를 때 쓰는 계정·토큰) — [화면 정의서](../design/screen-spec.md) Q6
- `route.failed` 의 `reason` 값 목록 고정 여부
- 배치 상태 영속화 (지금은 백엔드 재시작 시 `split` 으로 초기화)

## 관련 문서

- [화면 정의서](../design/screen-spec.md) — 모니터 화면·콘솔 화면 배치 UI
- [MQTT 메시지 계약 v1](mqtt-contract-v1.md) — `app_id`
- [VM 관리 API 계약 v1](vm-api-contract-v1.md) — `vm_id`, 오류 본문
- [카메라·AI 감지 계약 v1](camera-stream-v1.md) — 메시지 보드 상세 화면의 영상
- [아키텍처 설계서](../architecture.md)

## 변경 이력

| 버전 | 날짜 | 내용 |
| --- | --- | --- |
| v1 (초안) | 2026-10-21 | 최초. dom0 표시 서비스 방식 (2026-10-21 결정): 콘솔 REST-23·24, 읽기 전용 `WS /ws/v1/displays`, 표시 서비스 소켓 `WS /ws/v1/display-service`(WS-04, 서비스 토큰·연결 1개), status 판정, 재연결, `screen_path`, 이벤트 `display.route.changed`. 모니터 브라우저가 `/ws/v1/displays` 에 `display.hello`·`route.applied` 를 보내던 백엔드 초안 방식은 쓰지 않는다 (S15P31E110-129) |
| v1.1 (초안) | 2026-10-22 | view `sim`(시뮬레이터 화면, `screen_path` `/sim`, `vm_id` null) 추가. `/split` 세 번째 칸은 시뮬레이터 위치가 신선하면 시뮬레이터로 자동 전환 (시뮬레이터 구성, S15P31E110-262·-265) |
