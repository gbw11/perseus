# VM 관리 API 계약 v1

**최종 갱신** 2026-10-19
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼
**구성 계층** 프론트 ↔ Spring (공개 API) ↔ dom0-manager (FastAPI) ↔ Xen `xl`

이 문서는 계약 확정안과 플랫폼·Spring 구현 검토 결과를 합친 구현 기준 문서입니다. 실측이나 생명주기 정책이 바뀌면 이 문서를 함께 갱신합니다.

---

## 1. 계층 책임

### 1.1 영구 식별자와 플랫폼 registry

Spring이 생성 전에 발급한 UUID 형식의 `vm_id`가 시스템 전체의 유일한 영구 VM 식별자입니다. 보드는 별도 UUID나 `instance_id`를 만들지 않으며, 전달받은 `vm_id`를 registry와 Xen UUID에 그대로 사용합니다.

플랫폼 registry는 등록·보관·복구 생명주기의 원본입니다. dom0-manager는 플랫폼 관리 계층을 통해 registry와 Xen 실행 상태를 함께 조회합니다. `domid`는 재실행마다 바뀔 수 있으므로 영구 식별자로 사용하지 않습니다.

### 1.2 책임 분담

| 책임 | 담당 |
|---|---|
| VM 명세 보유 (`name`, `template_id`, `template_version`) | Spring |
| `vm_id` 발급 및 생성 멱등성 | Spring (`vm_id` 자체가 idempotency key) |
| 종료된 VM을 포함한 전체 목록 구성 | Spring |
| 표시 이름 중복 검사 | Spring |
| Xen 런타임 상태 조회·변경 | dom0-manager |
| 디스크 수명주기 집행 | dom0-manager |
| 자원 가용 판정 | dom0-manager |
| IP 자동 할당과 중복 방지 | 플랫폼 전역 잠금 내부 |

### 1.3 계층별 계약 분리

이 문서의 모든 응답 규약은 **어느 계층의 계약인지 명시**합니다. 두 계층의 상태 코드와 오류 명명이 다르므로 한 표에 섞지 않습니다.

---

## 2. 리소스 모델

### 2.1 VM 객체 — dom0-manager

Xen에서 직접 읽히는 필드만 포함합니다.

```json
{
  "vm_id": "550e8400-e29b-41d4-a716-446655440000",
  "state": "running",
  "vcpus": 1,
  "memory_mib": 256
}
```

### 2.2 VM 객체 — 프론트 공개

Spring이 자기 레코드와 dom0-manager 응답을 조합해 구성합니다. 목록·단건 조회 응답에는 에이전트 상태(`agent_status`)가 함께 들어갑니다.

```json
{
  "vm_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "web-01",
  "guest_kind": "linux",
  "state": "running",
  "agent_status": "ONLINE",
  "template_id": "debian",
  "template_version": "12",
  "vcpus": 1,
  "memory_mib": 256
}
```

| 필드 | 의미 |
|---|---|
| `vm_id` | VM 고유 ID. Spring이 생성 |
| `name` | 표시 이름. Spring 레코드 |
| `guest_kind` | VM 종류 `linux` / `zephyr`. 생성 때 템플릿에서 복사해 Spring 레코드에 저장. **Spring만 쓰는 값이며 dom0-manager 계약에는 없음.** `zephyr`는 에이전트·네트워크·정상 종료가 없는 콘솔 전용 펌웨어라 `agent_status`가 항상 `UNKNOWN` |
| `state` | Xen 기준 실행 상태 (3.3절). 도메인이 없으면 `stopped` |
| `agent_status` | heartbeat 기준 에이전트 상태 `UNKNOWN`/`ONLINE`/`STALE`/`OFFLINE`. **목록·단건 조회에만** 있고 생성·시작·정지 응답에는 없음 |
| `template_id`, `template_version` | 생성에 쓴 템플릿. Spring 레코드 |
| `vcpus`, `memory_mib` | 실행 중이면 dom0-manager가 관측한 값, 꺼져 있으면 Spring 레코드의 값 |

### 2.3 템플릿 객체

`GET /api/v1/templates`는 아래 객체의 배열을 반환합니다.

| 필드 | 의미 |
|---|---|
| `template_id` | 템플릿 ID (예: `debian`) |
| `version` | 템플릿 버전. VM 생성 요청에서는 이 값을 `template_version`으로 보냄 |
| `display_name` | 드롭다운 표시 이름 (예: `Debian 12`) |
| `min_memory_mib` | 이 템플릿의 최소 메모리 |
| `guest_kind` | 이 템플릿으로 만든 VM의 종류 `linux` / `zephyr` |

```json
[
  { "template_id": "ubuntu", "version": "24.04-20260926", "display_name": "Ubuntu 24.04", "min_memory_mib": 512, "guest_kind": "linux" },
  { "template_id": "debian", "version": "13-20261015", "display_name": "Debian 13", "min_memory_mib": 256, "guest_kind": "linux" },
  { "template_id": "zephyr", "version": "starter-1.0.0", "display_name": "Zephyr Starter 1.0.0", "min_memory_mib": 128, "guest_kind": "zephyr" }
]
```

플랫폼 기본 Ubuntu·Debian·Zephyr 템플릿 카탈로그에 맞춘 값입니다. `template_id`는 OS 검증 리포트가 에이전트의 os-release ID와 비교하므로 OS ID로 두고, `version`은 플랫폼 카탈로그 버전을 사용합니다.

1차 데이터는 Spring 설정 파일(`application.yml`의 `perseus.template-catalog.templates`)에 고정하며, OS(Alpine·Debian·Ubuntu)마다 버전 하나를 고정합니다. 버전과 최소 메모리 값은 P2 실측 전 임시값입니다(13장).

---

## 3. 상태 모델

### 3.1 공개 state

네 가지이며 전부 `xl list`에서 그대로 읽히는 실제 Xen 상태입니다. API가 생성하는 상태는 `running`뿐이고 나머지 셋은 조회로만 관찰됩니다.

| state | 의미 |
|---|---|
| `running` | Xen runtime 실행 중 |
| `stopping` | 종료 진행 중 |
| `paused` | 일시정지 (dom0에서 `xl pause`를 직접 실행한 경우에만 발생) |
| `crashed` | 비정상 종료 |

"꺼짐"은 state가 아니라 **도메인의 부재**로 표현됩니다.

### 3.2 `xl list` 상태 문자 매핑

이 매핑은 계약의 일부입니다. **`b`(blocked)는 정상 동작 중인 VM의 일반적인 상태이며 장애가 아닙니다.** 유휴 상태로 I/O를 대기하는 도메인이 `b`로 표시됩니다.

| `xl list` 상태 문자 | 공개 state |
|---|---|
| `r` (running), `b` (blocked) | `running` |
| `------` (플래그 없음) | `running` |
| `s` (shutdown), `d` (dying) | `stopping` |
| `p` (paused) | `paused` |
| `c` (crashed) | `crashed` |
| 도메인 없음 | 404 / 목록에서 제외 |
| 그 밖의 문자 | `running`으로 넘기지 않고 `500 DriverError` |

`------`는 스케줄링 순간에 아무 플래그도 서지 않은 정상 도메인입니다. dom0-manager 골격이 이 규칙으로 구현돼 있어 계약에 추가했습니다.

`crashed`가 남아 있는 이유는 플랫폼 도메인 설정이 `on_crash = 'preserve'`이기 때문입니다(`xenova_vm.py`). 정리(stop 또는 DELETE)하기 전까지 이 상태로 남습니다.

### 3.3 Spring 공개 state

Spring은 3.1의 네 상태에 `stopped`를 더해 다섯 상태를 공개합니다.

| state | 의미 |
|---|---|
| `running` / `stopping` / `paused` / `crashed` | dom0-manager가 알려 준 Xen 상태 그대로 |
| `stopped` | Spring 레코드는 있지만 Xen에 도메인이 없음. **Spring만 만드는 값이며 dom0-manager는 반환하지 않음** |

---

## 4. dom0-manager 엔드포인트 (내부 계약 / OpenAPI 원본)

| 메서드 | 경로 | 성공 응답 | 오류 |
|---|---|---|---|
| `GET` | `/api/v1/vms` | `200` VM 배열 | — |
| `POST` | `/api/v1/vms` | `201` VM | `400`, `409`, `507`, `500` |
| `GET` | `/api/v1/vms/{vm_id}` | `200` VM | `404`, `500` |
| `POST` | `/api/v1/vms/{vm_id}/stop` | `204` | `500` |
| `DELETE` | `/api/v1/vms/{vm_id}` | `204` | `404`, `409`, `500` |
| `GET` | `/healthz` | `200` | `503` |

`500 DriverError`는 분류되지 않은 플랫폼 실행 실패이며 모든 엔드포인트에서 날 수 있습니다(8.1절). `501`은 계약에서 사용하지 않습니다.

### 4.1 `GET /api/v1/vms`

**Xen에 존재하는 도메인만** 반환합니다. 종료된 VM을 포함한 전체 목록은 Spring이 자기 레코드와 조합해 구성합니다.

### 4.2 `POST /api/v1/vms` — 생성 및 재시작

요청 본문: `vm_id`, `template_id`, `template_version`, `vcpus`, `memory_mib`

- 해당 `vm_id`의 디스크가 존재하면 **재사용**하고, 존재하지 않으면 `template_id`·`template_version`으로 새로 생성한다.
- 디스크 경로는 `vm_id`에서 파생한다.
- 동일 `vm_id`·동일 명세 재요청은 기존 생성 결과를 반환하고, 동일 `vm_id`·다른 명세는 `409 VMExists`로 거부한다.
- Linux IP는 Spring이 전달하지 않는다. 플랫폼이 전역 잠금 안에서 IP 풀과 registry를 확인해 빈 주소를 자동 할당한다.

**Spring 불변조건** — 동일한 `vm_id`에는 항상 동일한 생성 명세를 전송한다. 플랫폼은 registry의 기존 생성 명세와 비교해 위반을 거부한다.

`start` 엔드포인트는 두지 않습니다. 꺼진 VM의 재시작은 같은 `vm_id`로 이 엔드포인트를 재호출하는 방식이며, 디스크가 보존되어 있으므로 데이터는 유지됩니다.

### 4.3 `POST /api/v1/vms/{vm_id}/stop`

`xl shutdown`을 사용하는 graceful shutdown이며 도메인은 소멸합니다. 디스크는 **보존**됩니다.

- 성공 응답은 `204 No Content`. 종료 후에는 Xen에 도메인이 없으므로 반환할 VM 객체가 존재하지 않는다.
- **없는 VM에 stop을 호출하면 `204`.** 멱등성은 목표 상태 기준이므로 이미 실행 중이 아닌 VM의 stop은 목표가 달성된 상태다.
- 호출은 동기 방식이며 플랫폼은 기본 최대 90초까지 도메인 소멸을 기다린다. 일반 stop은 시간 초과 시 자동으로 강제 종료하지 않는다.
- 강제 종료는 명시적인 force 요청에서만 수행한다. Zephyr는 `board_shutdown_policy`가 `force-allowed`가 아니면 `409`로 거부한다.

### 4.4 `DELETE /api/v1/vms/{vm_id}`

실행 중인 VM은 `409`로 거부합니다. 정지된 VM은 디스크를 즉시 지우지 않고 플랫폼의 retire 생명주기로 숨김 보관하며 tombstone과 generation을 유지합니다. 동일 디스크를 restore할 때는 기존 `vm_id`를 유지하고, purge 또는 복제로 새 VM을 만들 때는 Spring이 새 `vm_id`를 발급합니다.

### 4.5 `GET /healthz`

dom0-manager는 정상이지만 Xen이 응답하지 않는 상황을 구분하기 위한 엔드포인트입니다.

| 응답 | 본문 | 의미 |
|---|---|---|
| `200` | `{"status": "ok"}` | dom0-manager와 Xen 모두 응답 |
| `503` | `{"status": "xen_unavailable"}` | dom0-manager는 살아 있지만 Xen(`xl`)이 응답하지 않음 |

Spring의 시스템 상태 점검(REST-28)에서 Xen 상태를 판단할 때 사용합니다. 자동 복구의 주체(보드 또는 Spring)는 아직 정하지 않았으며(13장), 정해지면 이 엔드포인트의 사용처를 갱신합니다.

---

## 5. Spring 공개 API

프론트에 노출되는 엔드포인트 6개를 유지합니다. `start`는 dom0-manager의 `POST /api/v1/vms` 재호출로 구현합니다.

| 메서드 | 경로 | 성공 응답 | 비고 |
|---|---|---|---|
| `GET` | `/api/v1/vms` | `200` `{items: VM[], observed_at}` | 자기 레코드와 조합한 전체 목록. 생성 순 정렬. Spring에 레코드가 없는 도메인은 제외 |
| `POST` | `/api/v1/vms` | 신규 `201` VM, 동일 명세 재시도 `200` VM | 같은 이름에 다른 명세면 `409 VM_NAME_CONFLICT` |
| `GET` | `/api/v1/vms/{vm_id}` | `200` VM | — |
| `POST` | `/api/v1/vms/{vm_id}/start` | `200` VM | dom0 단건 조회 후 도메인이 없을 때만 create 재호출. 이미 `running`이면 그대로 반환. `crashed`·`paused`·`stopping`이면 `409 INVALID_VM_STATE`로 거부(정지 후 다시 시작). 플랫폼 CLI와 같은 동작이며 자동 복구는 하지 않음 |
| `POST` | `/api/v1/vms/{vm_id}/stop` | `200` VM (`state: stopped`) | dom0 단건 조회로 정지 전 상태를 읽은 뒤 stop 호출. 이미 꺼져 있으면 dom0를 호출하지 않고 이벤트도 남기지 않음. dom0는 `204`지만 Spring은 레코드로 VM 객체를 만들어 반환 |
| `DELETE` | `/api/v1/vms/{vm_id}` | `204` | dom0 retire 성공 뒤 Spring 레코드를 `RETIRED`로 보존하고 v1 목록·단건 조회에서는 숨김 |
| `GET` | `/api/v1/templates` | `200` 템플릿 배열 | 설정 파일 고정 데이터 (2.3절) |

모든 공개 API는 `Authorization: Bearer {access_token}`이 필요합니다. 생성·시작·정지·삭제가 성공하면 이벤트(`vm.created`, `vm.state.changed`, `vm.deleted`)가 기록됩니다.

Xen은 VM 상태 변화를 누구에게도 알리지 않으므로, Spring이 dom0-manager `GET /api/v1/vms`를 주기적으로(기본 5초, `EVENT_VM_STATE_CHECK_INTERVAL_MS`) 조회해 사용자 조작 없이 바뀐 상태(크래시, 스스로 종료 등)를 `vm.state.changed`(`cause: detected`)로 기록합니다. 조작 중인 VM은 건너뛰고, dom0-manager에 닿지 않으면 그 회차는 아무것도 기록하지 않으며, 서버가 처음 보는 VM은 기준값만 저장합니다.

---

## 6. 디스크 수명주기

| 연산 | 종료 방식 | 디스크 | 재시작 |
|---|---|---|---|
| `stop` | 게스트에 정상 종료 요청 | 보존 | 가능 |
| `DELETE` | 정지 상태에서만 허용 | 숨김 보관(retire) | restore로 가능 |
| `purge` | 해당 없음 | 영구 삭제 | 불가 |

retire는 복구 가능성을 위해 디스크를 보존하고, 실제 공간 회수는 별도 purge 생명주기에서 수행합니다.

---

## 7. 상태 전이표

| 현재 상태 | `POST /vms` | `POST /stop` | `DELETE` |
|---|---|---|---|
| 도메인 없음 + 등록 디스크 있음 | 기존 결과 반환 또는 재시작 | `204` | `204` — retire |
| 도메인 없음 + 디스크 없음 | `201` — 템플릿에서 생성 | `204` | `404` |
| `running` | `409 VMExists` | `204` — 정상 종료, 디스크 보존 | `409` — 먼저 정지 필요 |
| `stopping` | `409 VMExists` | 소멸까지 동기 대기 | `409` |
| `paused` | `409 VMExists` | 플랫폼 정책에 따라 정상 종료 | `409` |
| `crashed` | `409 VMExists` | 명시적 force 정책 적용 | `409` |

`crashed` 도메인은 종료 요청에 응답할 수 없으므로 `stop`은 `destroy`로 목표 상태를 달성합니다. 이 경우 디스크는 보존되지만 unclean 상태일 수 있습니다.

---

## 8. 오류 코드

### 8.1 dom0-manager

| 코드 | 명명 | 조건 |
|---|---|---|
| `400` | `InvalidArgument` | 형식 또는 범위 오류 |
| `404` | `VMNotFound` | `GET` 대상 도메인 부재 |
| `409` | `VMExists` | `vm_id` 충돌 (단일 의미) |
| `507` | `InsufficientResources` | 가용 메모리 부족 |
| `500` | `DriverError` | `xl` 실행 실패·타임아웃, 모르는 상태 문자. **임시 정의** — OpenAPI yaml의 오류 코드 목록에는 아직 없음(13장) |
| — | `InvalidState` | **현재 구현에서는 반환하지 않음.** 예비로 유지하며, pause/unpause 엔드포인트가 추가되면 생산자가 생긴다 |

오류 본문:

```json
{
  "code": "InsufficientResources",
  "message": "insufficient memory_mib: requested 1024, available 512",
  "detail": null,
  "resource": { "name": "memory_mib", "requested": 1024, "available": 512 }
}
```

| 필드 | 의미 |
|---|---|
| `code` | 위 표의 명명 |
| `message` | 사람이 읽는 설명 |
| `detail` | 선택. Xen 원문 등 상세. **Spring 로그 전용**이며 프론트에 노출하지 않음 |
| `resource` | 선택. `InsufficientResources`일 때만 `{name, requested, available}` |

FastAPI는 검증 실패 시 기본적으로 `422 HTTPValidationError`를 반환하므로 `400 InvalidArgument`로 정규화합니다. 예외 핸들러 교체만으로는 부족하며, **생성 스키마에서 422 응답 정의와 `HTTPValidationError` 모델까지 제거**해야 OpenAPI 차이 검사가 통과합니다.

`VMExists`는 `vm_id` 충돌 단일 의미입니다. dom0-manager는 표시 이름을 모르므로 이름 중복은 Spring이 판정합니다.

### 8.2 Spring

공개 오류 본문은 `{code, message}`이며, 입력 필드 검증 실패일 때만 `errors: [{field, reason}]`가 붙습니다.

**Spring이 직접 판단하는 오류**

| 코드 | 명명 | 조건 |
|---|---|---|
| `401` | `UNAUTHORIZED` / `INVALID_TOKEN` / `TOKEN_REVOKED` | 토큰 없음 / 잘못됐거나 만료 / 로그아웃으로 폐기 |
| `422` | `VALIDATION_ERROR` | 입력값 규칙 위반 — dom0-manager를 호출하지 않고 거절 |
| `404` | `TEMPLATE_NOT_FOUND` | 템플릿 목록에 없는 `template_id`·`template_version` |
| `404` | `VM_NOT_FOUND` | Spring 레코드에 없는 `vm_id` |
| `409` | `VM_NAME_CONFLICT` | 같은 이름의 VM이 다른 명세로 이미 있음 |
| `409` | `INVALID_VM_STATE` | 시작 요청인데 도메인이 `crashed`·`paused`·`stopping` 상태로 남아 있음 |

**dom0-manager 오류 변환** (dom0 본문의 `code`로 판단하며 `detail`은 로그에만 남김)

| dom0 | Spring 공개 |
|---|---|
| `404 VMNotFound` | 단건 조회에서는 오류가 아니라 "도메인 없음"(`stopped`)으로 처리. 그 밖에는 `404 VM_NOT_FOUND` |
| `409 VMExists` | 생성·시작에서는 성공으로 처리(10.1절). 그 밖에는 `409 VM_ALREADY_EXISTS` |
| `409 InvalidState` | `409 INVALID_VM_STATE` |
| `400 InvalidArgument` | `400 INVALID_ARGUMENT` |
| `507 InsufficientResources` | **`409 INSUFFICIENT_RESOURCES`** (공개 API에서는 507 대신 409를 씀) |
| `500 DriverError` | `500 VM_DRIVER_ERROR` |
| 해석할 수 없는 본문, 모르는 `code`, 본문이 필요한데 없음 | `502 VM_CONTROL_BAD_RESPONSE` |

**전송 실패**

| 상황 | Spring 공개 |
|---|---|
| 연결 실패 (재시도 후에도) | `503 VM_CONTROL_UNAVAILABLE` |
| 요청을 보낸 뒤 연결이 끊김 | `503 VM_CONTROL_UNAVAILABLE` (명령은 재시도하지 않음) |
| 응답 타임아웃 | `504 VM_CONTROL_TIMEOUT` |

---

## 9. 입력값 검증

| 항목 | 규칙 | 검증 계층 |
|---|---|---|
| `name` | 1~63자, 소문자·숫자·하이픈(`-`) | Spring 단독 |
| `vcpus` | 1~4 | Spring, dom0-manager |
| `memory_mib` | 템플릿 `min_memory_mib` 이상 ~ 1024 이하 (공통 하한 128) | 템플릿별 하한은 Spring, 공통 범위는 dom0-manager |
| 배수 제약 | 없음. 화면 입력만 64 단위 | — |
| `template_id` | 템플릿 목록에 존재해야 함 | Spring |

`name`은 dom0-manager로 전달되지 않으므로 제어 계층의 관심사가 아닙니다.

공통 하한이 128MiB인 것은 Xen domU에서 100MiB로는 커널 부팅이 불안정할 수 있고, 가장 가벼운 Alpine 기준으로도 128MiB가 현실적인 하한이기 때문입니다.

---

## 10. 타임아웃 · 재시도

명령은 동기 처리합니다. 특히 graceful stop은 플랫폼에서 최대 90초까지 기다릴 수 있으므로 외부 계층의 제한 시간이 더 길어야 합니다.

| 요청 | 연결 실패 (2초) | 응답 타임아웃 |
|---|---|---|
| 조회 (GET) | 1회 재시도 → 또 실패하면 `503` | 1회 재시도 → 또 실패하면 `504` |
| 명령 (생성·시작·정지·삭제) | 1회 재시도 → 또 실패하면 `503` | **재시도 안 함** → `504` |

| 항목 | 값 |
|---|---|
| 연결 타임아웃 | 2초 |
| 재시도 간격 | 0.5초 |
| 응답 대기 — 조회 | 6초 |
| 응답 대기 — 명령 | 100초 |
| 플랫폼 stop 응답 한도 | 90초 |
| 전체 예산 — 조회 | 약 15초 |
| 전체 예산 — 명령 | 약 103초 |

명령의 응답 타임아웃에서 재시도하지 않는 이유는 두 가지입니다. 연결이 성립했으므로 dom0-manager가 요청을 이미 받아 처리 중일 수 있고, `xl` 호출은 블로킹 subprocess이며 uvicorn 워커가 1개이므로 재전송한 요청은 처리 중인 요청 뒤에서 대기할 뿐입니다.

### 10.1 `504` 이후 처리

명령이 `504`로 종료되면 프론트가 `GET`으로 상태를 확인합니다. 그 뒤 동일 `vm_id`에 동일 명세의 요청이 재도달하고 dom0-manager가 `409 VMExists`를 반환하면, **Spring은 이를 성공으로 변환해 응답합니다.** 명세가 다르면 `409`를 그대로 전달합니다.

---

## 11. 동시성 · 실행 모델

- 제어 계층은 **`vm_id` 단위 락**으로 동일 VM에 대한 변경 연산을 직렬화한다. 같은 `vm_id`에 create가 동시에 도달하면 `xl create`가 중복 실행된다.
- `xl` 호출은 블로킹 subprocess이므로 **비동기 핸들러에서 직접 호출하지 않고 별도 스레드로 분리**한다. 워커 1개 구성에서 직접 호출하면 한 VM의 create 30초 동안 목록 조회를 포함한 모든 요청이 대기한다.

---

## 12. 자원 산정

`InsufficientResources`의 판정은 `xl info`의 `free_memory` 기준으로 하며, dom0 메모리는 `dom0_mem` 부트 파라미터로 고정합니다.

**Pi 4 8GB 기준**

| 항목 | 할당 |
|---|---|
| 실사용 가능 RAM | 약 7.8GB |
| Xen 하이퍼바이저 | 약 128MB |
| dom0 (고정 할당) | 1GB |
| domU 가용 | **약 6.5GB** |

계획된 4~5대 규모에서 메모리는 제약 조건이 아닙니다. VM 5대를 512MiB씩 할당해도 2.5GB로 약 4GB의 여유가 있습니다.

---

## 13. 미확정 항목

| 항목 | 담당 | 내용 |
|---|---|---|
| 조회 응답 대기 6초의 기산점 | 백엔드 | 연결 시간 포함 여부. 미포함이면 최악 소요가 16.5초로 예산 15초를 초과하므로 예산 18초 또는 대기 5초로 조정 필요 |
| 오류 코드 명명 규칙 | 백엔드 | `VALIDATION_ERROR` / `InvalidArgument` 중 한쪽으로 통일 권고 |
| OS별 실제 버전 값 | 제어 계층 | 이미지 빌드 시 실측 |
| OS별 `min_memory_mib` | 제어 계층 | 이미지 빌드 시 실측 |
| `DriverError`의 세부 분류 | 제어 계층 | 알려진 입력·없음·충돌·자원·시간 초과·일시 장애는 각각 400·404·409·507·504·503으로 분리하고, 분류되지 않은 실패만 500 사용 |
| 서비스 토큰 배포·회전 | 공통 | Spring과 dom0-manager에 같은 Bearer 서비스 토큰을 안전하게 배포하고 회전하는 운영 절차 |
| 템플릿 이름 변환 | 제어 계층 | Spring `template_id`+`version`(예: `ubuntu` + `24.04-20260926`)을 플랫폼 템플릿 이름(`ubuntu-24.04-20260926`, `xenova-zephyr-starter-1.0.0`)으로 바꾸는 규칙 |
| 자동 복구 주체 | 공통 | 보드(Xen `on_crash`·dom0 감시) 또는 Spring. 결정 전까지 Spring은 복구를 수행하지 않음 |

dom0-manager는 보드의 Tailscale 주소에 바인딩하고 `/healthz`를 포함한 내부 API에서 Bearer 서비스 토큰을 검사합니다. Spring은 `DOM0_MANAGER_BASE_URL`에 Tailscale 주소를 설정하고 `DOM0_MANAGER_SERVICE_TOKEN`을 `Authorization: Bearer`로 전송합니다. 연결 실패는 `503 VM_CONTROL_UNAVAILABLE`로 변환합니다.

`vm_id`는 도메인 설정의 `uuid`에 기록하고 `xl list -l`의 `config.c_info.uuid`로 역조회합니다. 동일 VM의 stop/start 및 retire/restore는 같은 UUID를 유지하며, 템플릿 생성·복제·purge 이후 신규 생성은 새 UUID를 사용합니다.

---

## 14. 변경 이력

| 버전 | 일자 | 내용 |
|---|---|---|
| v1 | 2026-10-16 | 계약 확정안 v1.0, 리뷰 v1.1, 백엔드 VM API 규약 v1, 리뷰 v1.2의 확정 사항 통합 |
| v1.1 | 2026-10-19 | 구현에 맞춰 보완: 공개 VM 객체에 `agent_status`, Spring 공개 state `stopped`(3.3), 템플릿 응답 필드 `version`, `xl` 플래그 없음(`------`) 매핑, `DriverError` 500/501, `/healthz` 본문, Spring 오류 변환표(8.2), 공개 API 응답 형식(5장), `vm_id` 저장 1안 확정, 배포 분리·복구 주체 미확정 항목 추가. Spring 시작은 실행 중이 아닌 도메인을 `409 INVALID_VM_STATE`로 거부, 정지는 이전 상태를 조회해 이벤트 `from`에 기록. Spring이 VM 상태를 주기 조회해 `cause: detected` 이벤트 기록. 템플릿을 플랫폼 기본값(Ubuntu·Debian·Zephyr)으로 교체하고 템플릿·공개 VM 객체에 `guest_kind` 추가 |
| v1.2 | 2026-10-19 | Spring 선발급 `vm_id`를 유일한 영구 식별자·생성 멱등성 키로 확정. 플랫폼 자동 IP 할당, 동기 stop 90초와 Spring 100초 제한, retire/restore/purge 생명주기, Spring `RETIRED` 명세 보존, Tailscale+Bearer 내부 연결을 반영 |
