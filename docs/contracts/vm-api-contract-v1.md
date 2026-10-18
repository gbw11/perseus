# VM 관리 API 계약 v1

**최종 갱신** 2026-10-19
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼
**구성 계층** 프론트 ↔ Spring (공개 API) ↔ dom0-manager (FastAPI) ↔ Xen `xl`

이 문서는 계약 확정안 v1.0, 리뷰 v1.1, 백엔드 VM API 규약 v1, 리뷰 v1.2에서 확정된 내용을 하나로 합친 것입니다. 구현의 기준 문서이며, 실측으로 수치가 바뀌면 이 문서를 갱신합니다. v1.1에서 dom0-manager 골격(`services/dom0-manager`)과 Spring 클라이언트의 실제 동작에 맞춰 보완했습니다(14장).

---

## 1. 계층 책임

### 1.1 무상태 원칙

dom0-manager는 **무상태**입니다.

- 파일시스템이나 DB에 VM 레코드를 두지 않는다.
- 모든 조회 응답은 `xl` 질의 결과에서 파생한다.
- **Xen에 존재하지 않는 도메인은 dom0-manager가 알 수 없다.**

Xen의 `xl` 툴스택에는 libvirt나 Docker와 달리 "정의된 채 꺼져 있는" 도메인 개념이 없습니다. 도메인은 실행 중일 때만 존재하며 `xl shutdown`과 `xl destroy`는 모두 도메인을 완전히 소멸시킵니다.

### 1.2 책임 분담

| 책임 | 담당 |
|---|---|
| VM 명세 보유 (`name`, `template_id`, `template_version`) | Spring |
| 멱등성 판정 | Spring |
| 종료된 VM을 포함한 전체 목록 구성 | Spring |
| 표시 이름 중복 검사 | Spring |
| Xen 런타임 상태 조회·변경 | dom0-manager |
| 디스크 수명주기 집행 | dom0-manager |
| 자원 가용 판정 | dom0-manager |

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

```json
[
  { "template_id": "alpine", "version": "1.2.0", "display_name": "Alpine 1.2.0", "min_memory_mib": 128 },
  { "template_id": "debian", "version": "12", "display_name": "Debian 12", "min_memory_mib": 256 },
  { "template_id": "ubuntu", "version": "24.04", "display_name": "Ubuntu 24.04", "min_memory_mib": 512 }
]
```

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

`crashed`가 남아 있는 이유는 플랫폼 도메인 설정이 `on_crash = 'preserve'`이기 때문입니다(`perseus_vm.py`). 정리(stop 또는 DELETE)하기 전까지 이 상태로 남습니다.

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
| `DELETE` | `/api/v1/vms/{vm_id}` | `204` | `500` |
| `GET` | `/healthz` | `200` | `503` |

`500 DriverError`는 `xl` 실행 실패·타임아웃이며 모든 엔드포인트에서 날 수 있습니다(8.1절). 현재 골격의 Xen 모드는 생성·정지·삭제가 아직 구현되지 않아 `501`(code `DriverError`)을 반환합니다(S15P31E110-115).

### 4.1 `GET /api/v1/vms`

**Xen에 존재하는 도메인만** 반환합니다. 종료된 VM을 포함한 전체 목록은 Spring이 자기 레코드와 조합해 구성합니다.

### 4.2 `POST /api/v1/vms` — 생성 및 재시작

요청 본문: `vm_id`, `template_id`, `template_version`, `vcpus`, `memory_mib`

- 해당 `vm_id`의 디스크가 존재하면 **재사용**하고, 존재하지 않으면 `template_id`·`template_version`으로 새로 생성한다.
- 디스크 경로는 `vm_id`에서 파생한다.
- 성공은 `201`, `vm_id` 충돌은 `409 VMExists`만 반환한다. **"동일 재시도 200"은 Spring 계층의 동작**이며 이 계약에 포함되지 않는다.

**Spring 불변조건** — 동일한 `vm_id`에는 항상 동일한 `template_id`·`template_version`을 전송한다. 무상태 dom0-manager는 보존된 디스크가 어떤 템플릿으로 생성되었는지 알 수 없으며 위반을 탐지할 수 없다.

`start` 엔드포인트는 두지 않습니다. 꺼진 VM의 재시작은 같은 `vm_id`로 이 엔드포인트를 재호출하는 방식이며, 디스크가 보존되어 있으므로 데이터는 유지됩니다.

### 4.3 `POST /api/v1/vms/{vm_id}/stop`

`xl shutdown`을 사용하는 graceful shutdown이며 도메인은 소멸합니다. 디스크는 **보존**됩니다.

- 성공 응답은 `204 No Content`. 종료 후에는 Xen에 도메인이 없으므로 반환할 VM 객체가 존재하지 않는다.
- **없는 VM에 stop을 호출하면 `204`.** 멱등성은 목표 상태 기준이므로 이미 실행 중이 아닌 VM의 stop은 목표가 달성된 상태다.
- **강제 전환** — `xl shutdown` 요청 후 25초까지 도메인이 소멸하지 않으면 `xl destroy`로 전환한다. 강제 전환을 포함한 드라이버 전체 응답은 30초를 넘지 않는다. 이 경우 디스크는 unclean 상태로 남을 수 있다.

### 4.4 `DELETE /api/v1/vms/{vm_id}`

강제 종료 후 **디스크를 삭제**합니다. 성공 응답은 `204`이며, 없는 VM에 대한 호출도 `204`입니다.

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
| `DELETE` | `/api/v1/vms/{vm_id}` | `204` | dom0 삭제 성공 뒤 Spring 레코드 삭제. 없는 VM도 `204` |
| `GET` | `/api/v1/templates` | `200` 템플릿 배열 | 설정 파일 고정 데이터 (2.3절) |

모든 공개 API는 `Authorization: Bearer {access_token}`이 필요합니다. 생성·시작·정지·삭제가 성공하면 이벤트(`vm.created`, `vm.state.changed`, `vm.deleted`)가 기록됩니다.

---

## 6. 디스크 수명주기

| 연산 | 종료 방식 | 디스크 | 재시작 |
|---|---|---|---|
| `stop` | 게스트에 정상 종료 요청 | 보존 | 가능 |
| `DELETE` | 강제 종료 | 삭제 | 불가 |

두 연산이 모두 디스크를 보존하면 자원 회수 경로가 사라지고, 개발 중 생성·삭제를 반복할 때 SD카드에 디스크 이미지가 누적됩니다.

---

## 7. 상태 전이표

| 현재 상태 | `POST /vms` | `POST /stop` | `DELETE` |
|---|---|---|---|
| 도메인 없음 + 디스크 있음 | `201` — 기존 디스크 재사용 | `204` | `204` — 디스크 삭제 |
| 도메인 없음 + 디스크 없음 | `201` — 템플릿에서 생성 | `204` | `204` |
| `running` | `409 VMExists` | `204` — 정상 종료, 디스크 보존 | `204` — 강제 종료, 디스크 삭제 |
| `stopping` | `409 VMExists` | `204` — 소멸까지 대기 | `204` |
| `paused` | `409 VMExists` | `204` — unpause 후 정상 종료 | `204` |
| `crashed` | `409 VMExists` | `204` — destroy로 처리 | `204` |

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
| `501` | `DriverError` | 아직 구현하지 않은 드라이버 기능 (현재 Xen 모드의 생성·정지·삭제) |
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
| `500`/`501 DriverError` | `500 VM_DRIVER_ERROR` |
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

**45초는 공개 요청 하나의 전체 예산**이며 시도당 시간이 아닙니다.

| 요청 | 연결 실패 (2초) | 응답 타임아웃 |
|---|---|---|
| 조회 (GET) | 1회 재시도 → 또 실패하면 `503` | 1회 재시도 → 또 실패하면 `504` |
| 명령 (생성·시작·정지·삭제) | 1회 재시도 → 또 실패하면 `503` | **재시도 안 함** → `504` |

| 항목 | 값 |
|---|---|
| 연결 타임아웃 | 2초 |
| 재시도 간격 | 0.5초 |
| 응답 대기 — 조회 | 6초 |
| 응답 대기 — 명령 | 35초 (드라이버 30초 + 여유 5초) |
| 드라이버 응답 한도 — 명령 | 30초 (`shutdown` 대기 25초 + 강제 전환) |
| 전체 예산 — 조회 | 약 15초 |
| 전체 예산 — 명령 | 약 40초 |

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
| `DriverError`의 HTTP 코드 | 제어 계층 | 현재 `500`(구현 안 된 기능은 `501`)으로 임시 운영. OpenAPI yaml의 `ErrorCode`에 추가할지 결정 |
| 배포 분리 시 접속 방식 | 공통 | Spring은 EC2, dom0-manager는 보드에서 실행(Tailscale 연결 예정). dom0-manager는 지금 `127.0.0.1`에만 바인딩하고 인증이 없음. 바인드 주소와 서비스 인증(Spring은 `DOM0_MANAGER_SERVICE_TOKEN`이 설정되면 `Authorization: Bearer`를 보냄)을 정해야 함 |
| 자동 복구 주체 | 공통 | 보드(Xen `on_crash`·dom0 감시) 또는 Spring. 결정 전까지 Spring은 복구를 수행하지 않음 |

`vm_id` 저장 방식은 **1안으로 확정**했습니다. 도메인 설정의 `uuid`에 `vm_id`를 넣고 `xl list -l`의 `config.c_info.uuid`로 역조회합니다. 플랫폼 도구(`perseus_vm.py`)와 dom0-manager XenDriver가 같은 방식을 씁니다.

---

## 14. 변경 이력

| 버전 | 일자 | 내용 |
|---|---|---|
| v1 | 2026-10-16 | 계약 확정안 v1.0, 리뷰 v1.1, 백엔드 VM API 규약 v1, 리뷰 v1.2의 확정 사항 통합 |
| v1.1 | 2026-10-19 | 구현에 맞춰 보완: 공개 VM 객체에 `agent_status`, Spring 공개 state `stopped`(3.3), 템플릿 응답 필드 `version`, `xl` 플래그 없음(`------`) 매핑, `DriverError` 500/501, `/healthz` 본문, Spring 오류 변환표(8.2), 공개 API 응답 형식(5장), `vm_id` 저장 1안 확정, 배포 분리·복구 주체 미확정 항목 추가. Spring 시작은 실행 중이 아닌 도메인을 `409 INVALID_VM_STATE`로 거부, 정지는 이전 상태를 조회해 이벤트 `from`에 기록 |
