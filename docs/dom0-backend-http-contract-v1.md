# 제어 계층과 백엔드 HTTP 계약 v1.0

> 현재 배포용 dom0-manager 구현은 없다. 이 문서는 HTTP 어댑터의 설계 경계이며,
> 실제 공통 Manager 계약은 [관리 가이드](build-guides/xenova-vm-management.md)를 따른다.
> UUID·자동 IP·템플릿의 간이 검사만 tests/contracts의 시험 연결부로 수행한다.

## 확정 범위

Spring Boot 백엔드는 Python 객체를 직접 호출하지 않는다. Python `dom0-manager`가 FastAPI로 내부 HTTP API를 제공하고, FastAPI 내부에서 설정에 따라 `MockDriver` 또는 `XenDriver`를 선택한다.

```text
Frontend -> Spring Boot :8080 -> FastAPI :8081 -> VMDriver -> Mock/Xen
```

1차에서는 Spring Boot와 FastAPI를 같은 dom0에서 실행한다. FastAPI는 `127.0.0.1:8081`에만 바인딩하며 외부 네트워크에 공개하지 않는다. 사용자 JWT는 Spring 공개 API에서만 사용하고, localhost 내부 호출에는 전달하지 않는다.

머신 분리가 필요해지는 시점에 mTLS 또는 서비스 토큰을 별도 계약으로 추가한다.

## 확정 결정

| 항목 | 결정 |
| --- | --- |
| 통신 방식 | FastAPI HTTP/JSON |
| 실행 위치 | Spring Boot와 FastAPI 모두 dom0 |
| 내부 주소 | `http://127.0.0.1:8081` |
| JSON 표기 | `snake_case` |
| 경로 버전 | `/api/v1` |
| `vm_id` | Spring이 UUID를 한 번 생성하고 재시도에도 같은 값을 사용 |
| start/stop | Xen runtime의 목표 상태가 확인될 때까지 동기 대기 |
| 게스트 준비 상태 | 2차 heartbeat로 별도 판단 |
| 드라이버 타임아웃 | 조회 5초, 변경 명령 30초 |
| Spring 타임아웃 | 조회 10초, 변경 명령 45초 |
| 멱등성 | 목표 상태 기준 |
| uvicorn | 워커 1개 |
| 계약 원본 | `contracts/dom0-manager-openapi.yaml` |

## 역할 경계

### Spring Boot

- 사용자 인증과 권한 확인
- `vm_id` 생성
- 사용자 요청과 작업 이력 저장
- FastAPI 호출 및 오류 변환
- 연결 실패를 `503 VM_CONTROL_UNAVAILABLE`로 변환
- 클라이언트 타임아웃을 `504 VM_CONTROL_TIMEOUT`으로 변환
- FastAPI 오류의 `detail`은 로그에만 기록
- 향후 heartbeat 감시와 복구 정책 수행

### FastAPI dom0-manager

- HTTP/JSON을 Python 호출로 변환
- Pydantic 검증 오류를 `400 InvalidArgument`로 정규화
- 설정에 따라 Mock/Xen 드라이버 선택
- 동시 자원 승인과 VM 상태 전이 직렬화
- 모든 Xen 오류를 공개 오류 6종 중 하나로 변환
- OpenAPI 스키마 제공

### VMDriver

- `list`, `get`, `create`, `start`, `stop`, `destroy` 구현
- Xen 용어와 원문을 Spring에 직접 노출하지 않음
- 정책, 사용자 권한, 복구 횟수를 판단하지 않음

## 엔드포인트

| 메서드 | 경로 | 성공 응답 |
| --- | --- | --- |
| GET | `/api/v1/vms` | `200 VM[]` |
| POST | `/api/v1/vms` | 신규 `201 VM`, 동일 재시도 `200 VM` |
| GET | `/api/v1/vms/{vm_id}` | `200 VM` |
| POST | `/api/v1/vms/{vm_id}/start` | `200 VM(state=running)` |
| POST | `/api/v1/vms/{vm_id}/stop` | `200 VM(state=shutdown)` |
| DELETE | `/api/v1/vms/{vm_id}` | `204`, 디스크 보존 |

## VM 객체

```json
{
  "vm_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "개발용 Ubuntu",
  "state": "running",
  "template_id": "ubuntu-24.04-arm64",
  "template_version": "1.0.0",
  "vcpus": 1,
  "memory_mib": 512,
  "guest_kind": "linux",
  "ipv4": "192.168.77.2"
}
```

`vm_id`는 불변 식별자이고 `name`은 표시 이름이다. `state`는 `running`, `paused`, `stopping`, `shutdown`, `crashed`만 사용한다. `starting`은 공개 runtime 상태에서 제거한다. `running`은 Xen runtime이 실행 중이라는 뜻이며 게스트 OS나 애플리케이션이 준비됐다는 뜻이 아니다.

## 생성 요청

```json
{
  "vm_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "개발용 Ubuntu",
  "template_id": "ubuntu-24.04-arm64",
  "template_version": "1.0.0",
  "vcpus": 1,
  "memory_mib": 256
}
```

Spring이 `vm_id`를 생성하므로 응답 유실 후에도 같은 요청을 안전하게 다시 보낼 수 있다. 같은 `vm_id`와 동일한 요청은 기존 VM을 `200`으로 반환한다. 같은 vm_id에 다른 최초 명세를 보내면 409 VMExists로 매핑한다.
표시 이름 중복은 Spring 정책이며 플랫폼 신원 충돌이 아니다. name은 경로/Xen 이름이 아니다.
IP와 guest_kind는 요청하지 않으며 카탈로그와 플랫폼 예약 결과에서 확정한다.
생성은 정지 인스턴스 등록이며 start와 분리한다. 보관/삭제 UUID는 create로 되살리지 않는다.

## 오류 계약

```json
{
  "code": "InvalidState",
  "message": "VM cannot transition from paused to shutdown",
  "detail": "xl: original driver message",
  "resource": null
}
```

`detail`과 `resource`는 선택 필드다. `detail`은 Spring 로그 전용이고 프론트에 노출하지 않는다. `resource`는 `InsufficientResources`일 때만 사용한다.

| code | HTTP | 의미 |
| --- | ---: | --- |
| `VMNotFound` | 404 | 조회하거나 명령할 `vm_id`가 없음 |
| `VMExists` | 409 | `vm_id` 의 최초 명세 또는 내부 신원이 충돌함 |
| `InvalidState` | 409 | 존재하는 VM이 목표 상태로 전이할 수 없음 |
| `InvalidArgument` | 400 | 형식이나 범위가 잘못됨 |
| `InsufficientResources` | 507 | IP 풀 고갈 또는 저장공간 부족을 포함한 자원 부족 |
| `DriverError` | 500 | Xen 실패, 드라이버 타임아웃, 분류되지 않은 실패 |

Spring이 FastAPI에 연결하지 못한 경우는 FastAPI 오류 본문이 없으므로 별도로 처리한다.

| 상황 | Spring 공개 응답 |
| --- | --- |
| Connection refused, DNS 또는 소켓 연결 실패 | `503 VM_CONTROL_UNAVAILABLE` |
| Spring HTTP 클라이언트 타임아웃 | `504 VM_CONTROL_TIMEOUT` |
| FastAPI의 `DriverError` | `500 VM_DRIVER_ERROR` |

## 타임아웃과 재시도

| 호출 | 드라이버 | Spring 클라이언트 | 자동 재시도 |
| --- | ---: | ---: | --- |
| GET 목록·단건 | 5초 | 10초 | 최대 1회 허용 |
| POST 생성 | 30초 | 45초 | 같은 `vm_id`로 1회 허용 |
| POST start | 30초 | 45초 | 목표 상태 기준 1회 허용 |
| POST stop | 30초 | 45초 | 목표 상태 기준 1회 허용 |
| DELETE | 30초 | 45초 | 1회 허용, 이미 없으면 204 |

재시도 사이에는 짧은 백오프를 둔다. Spring 클라이언트 타임아웃은 드라이버 타임아웃보다 길어야 한다.

## 멱등성 규칙

- 이미 `running`인 VM에 start를 호출하면 현재 VM을 `200`으로 반환한다.
- 이미 `shutdown`인 VM에 stop을 호출하면 현재 VM을 `200`으로 반환한다.
- 이미 사라진 VM에 DELETE를 호출하면 `204`를 반환한다.
- 없는 VM을 GET, start 또는 stop하면 `404 VMNotFound`를 반환한다.
- 동일 `vm_id`와 동일 명세로 create를 재호출하면 기존 VM을 `200`으로 반환한다.
- 동일 `vm_id`에 다른 명세를 보내면 `409 VMExists`를 반환한다.

## 구현 완료 조건

- FastAPI가 `DRIVER=mock`으로 기동된다.
- 여섯 엔드포인트가 OpenAPI 계약을 만족한다.
- FastAPI 검증 오류가 `400 InvalidArgument` 형식으로 반환된다.
- OpenAPI 파일이 CI에서 FastAPI 생성 스키마와 비교된다.
- Spring이 Mock FastAPI의 목록과 단건 조회에 성공한다.
- Spring이 생성, 시작, 종료, 삭제 흐름을 통합 테스트한다.
- 없는 `vm_id`가 `404 VMNotFound`로 전달된다.
- FastAPI 중단 시 Spring이 `503 VM_CONTROL_UNAVAILABLE`을 반환한다.
- 지연 응답 시 Spring이 `504 VM_CONTROL_TIMEOUT`을 반환한다.
- FastAPI는 uvicorn 워커 1개로 실행된다.


## 현재 코드와 후속 연결 시 확인할 차이

Spring 연결 시 요청·응답과 설정 카탈로그를 아래 플랫폼 계약에 맞춰야 한다.
tests/contracts의 Python 연결부는 기존 Dom0CreateVmRequest 필드와 공통 Manager를 대조하는
시험 코드이며 Java 직렬화·역직렬화 검사나 배포용 HTTP 어댑터를 대신하지 않는다.
플랫폼 공개 카탈로그는 ubuntu-24.04-arm64 / debian-13-arm64 /
zephyr-xenvm-starter-arm64 @ 1.0.0이다. 원본 OS/SDK 날짜와 해시는 플랫폼 catalog의 source다.
Linux 최소 128MiB, Zephyr는 1 vCPU/16MiB로 카탈로그 검사를 따른다.
create/list/get VM은 guest_kind와 ipv4(nullable)를 함께 반환한다.
Manager envelope와 이 문서의 HTTP 본문은 별개이며 시험 연결부가 변환한다.
Manager는 running/blocked/paused/shutdown/crashed/dying/stopped와 앱 준비 unknown을 구분한다.
HTTP VM state의 축약 매핑은 blocked→running, dying→stopping, 도메인 부재→shutdown이다.
이는 앱 준비 완료나 자동 복구를 의미하지 않는다.
실제 Spring command-timeout은 35초, 플랫폼 정상 stop 마감은 최대 90초다.
위 이전 설계 타임아웃 표는 현재 실행 결과가 아니며 후속 어댑터에서 합의해야 한다.
retire/restore/purge는 호출자 재시도 키·operation_id·lifecycle generation 전달이 필요하다.
현재 VmControlPort의 destroy만으로 이 계약이 모두 구현됐다고 해석하지 않는다.
