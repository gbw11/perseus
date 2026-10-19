# dom0-manager

dom0에서 도는 Python 제어 서비스입니다. Spring 관리 서버가 호출하는 내부 HTTP API(기본 `127.0.0.1:8081`, 배포는 Tailscale 주소 + Bearer 토큰)를 열고, 드라이버를 통해 VM을 조회·제어합니다. 드라이버는 세 가지입니다: `mock`(인메모리), `xen`(xl 직접 조회, 읽기 전용), `platform`(플랫폼 파트의 관리 라이브러리로 registry + Xen 생명주기).

```text
프론트 → Spring :8080 → dom0-manager :8081 → Driver ─┬─ platform: xenova_management.Manager → registry(/srv/xenova) + xl
                                   └ Provisioner      ├─ xen: xl list / info (조회만)
                                     (platform create/retire)  └─ mock: 인메모리
```

- 계약 문서: [docs/contracts/vm-api-contract-v1.md](../../docs/contracts/vm-api-contract-v1.md)
- OpenAPI 원본: [contracts/dom0-manager-openapi.yaml](../../contracts/dom0-manager-openapi.yaml). `tests/test_contract.py`가 서비스가 내는 스키마와 비교합니다.

## 실행 (Mock 모드)

보드·Xen 없이 바로 띄울 수 있습니다. 백엔드·프론트엔드 연동용입니다.

```bash
cd services/dom0-manager
python -m pip install -e .[test]
XENOVA_DRIVER=mock XENOVA_MOCK_SAMPLES=1 python -m dom0_manager
# → http://127.0.0.1:8081/docs 에서 내부 API 문서 확인
```

```bash
curl -s localhost:8081/api/v1/vms | python -m json.tool
curl -s -X POST localhost:8081/api/v1/vms -H 'content-type: application/json' \
  -d '{"vm_id":"11111111-1111-4111-8111-111111111111","template_id":"alpine","template_version":"3.20","vcpus":1,"memory_mib":256}'
curl -i -X POST localhost:8081/api/v1/vms/11111111-1111-4111-8111-111111111111/stop   # 204
```

`XENOVA_MOCK_SAMPLES=1`이면 배포판 3종 샘플 VM이 미리 떠 있습니다. vm_id는 고정(uuid5)이라 그대로 써도 됩니다.

| 이름 | vm_id | vcpus / memory_mib |
| --- | --- | --- |
| alpine-drive | `fe629065-6ee9-5f0f-ab42-38eb60186d85` | 1 / 256 |
| debian-safety | `b926423f-e3e9-5e1e-95f8-6caf6d91a63e` | 1 / 512 |
| ubuntu-infotainment | `96d76964-4eaa-5700-a642-747aa91f0cc0` | 2 / 512 |

값은 `uuid5(NAMESPACE_URL, "xenova/mock/<이름>")`이며 `dom0_manager.drivers.mock.SAMPLE_VMS`에 있습니다.

## 설정 (환경변수)

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `XENOVA_DRIVER` | `mock` | `mock` / `xen` / `platform` |
| `XENOVA_HOST` / `XENOVA_PORT` | `127.0.0.1` / `8081` | 바인드 주소. dom0 밖으로 열지 않습니다 |
| `XENOVA_XL_QUERY_TIMEOUT` | `5` | 조회용 xl 호출 한도(초). Spring 조회 대기 6초 안 |
| `XENOVA_XL_COMMAND_TIMEOUT` | `95` | 명령용 xl 호출 한도(초) |
| `XENOVA_SHUTDOWN_GRACE` | `90` | 정상 종료 대기(초). 계약 v1.1: 시간이 지나도 자동 강제 종료하지 않고 실패로 보고 |
| `XENOVA_SERVICE_TOKEN` | (없음) | 설정하면 `/healthz`를 포함한 모든 요청에 `Authorization: Bearer <토큰>`을 요구(401). Tailscale 바인드 배포에서는 필수 |
| `XENOVA_HOST_CPUS` | `4` | vcpus 상한 기본값. `xl info`의 nr_cpus가 있으면 그 값 우선 |
| `XENOVA_INSTANCE_ROOT` | `/srv/xenova/instances` | 인스턴스 루트 (xen 모드의 등록 확인용. platform 모드는 Manager의 data 경로를 씀) |
| `XENOVA_MOCK_SAMPLES` | `0` | Mock 샘플 VM 3종 미리 생성 |
| `XENOVA_LOG_LEVEL` | `INFO` | |

## 구조

```text
dom0_manager/
  main.py          FastAPI 조립, 422 → 400 정규화된 OpenAPI
  config.py        환경변수 설정
  models.py        계약 스키마 (Vm, CreateVmRequest, Error, Health)
  errors.py        예외 → HTTP 코드 매핑
  service.py       Application 계층: vm_id 락, 전역 자원 락, 생성·종료·삭제 순서
  api/             라우터(vms, health)와 예외 핸들러
  drivers/
    base.py        Driver / Provisioner 인터페이스 (S15P31E110-31)
    mock.py        MockDriver, MockProvisioner, 장애 주입 (S15P31E110-35)
    xl.py          xl 실행·파싱 (subprocess 격리, 타임아웃)
    xen.py         XenDriver: 조회 전용 (S15P31E110-36)
    platform.py    PlatformDriver·PlatformProvisioner: xenova_management 연동 (S15P31E110-115)
tests/
  test_api_flow.py              계약 7장 상태 전이 (Mock)
  test_validation.py            400 정규화, 추가 필드·UUID·범위
  test_contract.py              OpenAPI yaml 과 비교
  test_auth.py                  Bearer 토큰
  test_xl.py                    xl 파싱, XenDriver 조회, FakeRunner
  test_platform_driver.py       PlatformDriver/Provisioner (FakeManager)
  test_platform_integration.py  저장소의 실제 xenova_management + FakeXen + 임시 registry·카탈로그
```

## 드라이버 인터페이스 (S15P31E110-31)

`drivers/base.py`의 `Driver`를 platform·xen·mock이 똑같이 구현합니다. 상위 계층은 드라이버 종류를 모릅니다.

| 메서드 | v1 계약 | 의미 |
| --- | --- | --- |
| `list_domains()` | O | Xen에 존재하는 도메인. Domain-0 제외 |
| `get_domain(vm_id)` / `has_domain(vm_id)` | O | 단건 조회. 없으면 `VMNotFound` |
| `create(spec)` | O | 등록된 인스턴스로 도메인을 띄움(platform은 `start`). 이미 떠 있으면 `VMExists` |
| `shutdown(vm_id, grace_s)` | O | 정상 종료 요청 후 grace(90초)까지 대기. 시간 초과 시 자동 강제 종료 없이 `DriverTimeout`(v1.1). 디스크 보존. 없는 VM은 성공(멱등) |
| `destroy(vm_id)` | O | 명시적 강제 종료. 디스크는 Provisioner가. 멱등 |
| `host_info()` | O | free_memory, total_memory, nr_cpus (`xl info`) |
| `health()` | O | `xl info` 응답과 registry 저장소(`instances/`) 존재 → `/healthz` 503 판정. Manager 를 거치지 않아 조회 한도 안에 끝남 |
| `reboot`, `set_vcpus`, `set_memory`, `pin_vcpu`, `set_sched_weight`, `get_stats` | v2 | 208·101·112에서. 기본 구현은 `NotImplementedError` |

`Provisioner`는 `ensure_instance(vm_id, template_id, template_version, vcpus=, memory_mib=) / instance_exists / delete_instance` 세 개입니다. platform 모드에서는 플랫폼 `create`(등록 + 디스크)와 `retire`(숨김 보관)로 구현되며, dom0-manager 안에 디스크 생성기는 없습니다. xen 모드는 조회 전용이라 `ensure_instance`가 501입니다.

`DELETE /api/v1/vms/{vm_id}`는 v1.1 의미입니다. 실행 중이면 409 `InvalidState`, 등록이 없으면 404, 정지된 VM은 숨김 보관(retire)으로 204. 강제 종료는 하지 않습니다.

`POST /api/v1/vms`로 같은 `vm_id`가 다시 오면 등록된 생성 명세(template_id·template_version·vcpus·memory_mib)와 비교합니다. 같으면 디스크를 재사용해 다시 띄우고(재시작), 다르면 409 `VMExists`입니다(계약 4.2 불변조건).

상태 매핑은 계약 3.2장을 따릅니다. `b`(blocked)는 I/O 대기 중인 정상 상태이고, 플래그가 없는 `------`도 running으로 봅니다. 그 외 모르는 문자는 조용히 running으로 넘기지 않고 `DriverError`를 냅니다.

## platform 모드 (보드 실제 연동, S15P31E110-115)

`XENOVA_DRIVER=platform`은 플랫폼 파트의 관리 라이브러리 `xenova_management.Manager`를 직접 import해서 registry(`/srv/xenova/instances`)와 Xen을 함께 다룹니다. xl 명령을 직접 부르지 않습니다(`xl info`만 자원 조회용).

```bash
# 보드(dom0)에서
XENOVA_DRIVER=platform XENOVA_PLATFORM_LIB=/usr/lib/xenova XENOVA_SERVICE_TOKEN=<토큰> python -m dom0_manager
```

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `XENOVA_PLATFORM_LIB` | `/usr/lib/xenova` | `xenova_management` 등 플랫폼 모듈 경로. 여러 개면 OS 경로 구분자로 |
| `XENOVA_PLATFORM_BUSY_TIMEOUT` | `5` | 다른 요청이 플랫폼 `Manager`를 쓰는 동안 기다릴 최대 초. 넘으면 503 `PlatformUnavailable`(재시도 가능). 정지가 90초 잠금을 쥐는 동안 조회가 스레드풀에 쌓이지 않게 한다 |

동작 대응:

| API | 플랫폼 호출 |
| --- | --- |
| `GET /vms`, `GET /vms/{id}` | `list` / `get`. `domid`가 있는 항목만 도메인으로 봄. 등록만 된 VM은 목록에 없고 단건은 404 |
| `POST /vms` | 등록이 없으면 `create`(`idempotency_key=create-<vm_id>`, Spring 발급 `vm_id`를 그대로 전달) → `start` |
| `POST /vms/{id}/stop` | `stop` (`force=false`, 플랫폼이 최대 90초 대기). 시한 초과는 504 |
| `DELETE /vms/{id}` | 실행 중 409 → 정지 후 `retire`(숨김 보관) |

플랫폼 오류 코드 → HTTP: `INVALID_SPEC`·템플릿 없음 400 / `VM_NOT_FOUND` 404 / 이름·IP·멱등성 충돌과 `VM_NOT_STOPPED` 등 409 / `INSUFFICIENT_STORAGE`·`IP_POOL_EXHAUSTED` 507 / `STORAGE_UNAVAILABLE`·`RECOVERY_REQUIRED`·`CLEANUP_INCOMPLETE`·`OPERATION_IN_PROGRESS`(잠금 보유, 재시도 가능) 503 / 시한 초과 504 / 그 외 500.

플랫폼 `xen_state` → 계약 상태: `running`·`blocked`·`unknown`(플래그 없음) → running, `paused` → paused, `shutdown`·`dying` → stopping, `crashed` → crashed, `stopped` → 도메인 없음.

`load_manager`는 `Manager`를 `SerializedManager`로 감싸 프로세스 안에서 `call`을 직렬화합니다. 플랫폼 `Manager`가 요청별 상태(`current_operation`·`replayed`)를 인스턴스 변수에 두어 FastAPI 스레드가 동시에 부르면 섞이기 때문입니다. 차례를 기다리는 시간은 `XENOVA_PLATFORM_BUSY_TIMEOUT`(기본 5초)으로 제한하고, 넘으면 플랫폼의 잠금 충돌과 같은 `OPERATION_IN_PROGRESS` 봉투를 돌려 503으로 냅니다. 정지가 90초 동안 잠금을 쥐어도 조회가 스레드풀에 쌓이지 않습니다. `/healthz`는 Manager를 거치지 않고 `xl info`와 registry 디렉터리 존재만 확인하므로 잠금이나 느린 `xl list`에 영향받지 않습니다.

**생성 입력**: `create`에는 Spring 발급 `vm_id`, 표시 이름(`<template_id 정규화>-<vm_id 앞 12자리>`), 템플릿 ID·버전, vcpus·memory_mib만 보냅니다. 내부 실행 이름(`vm-<hex>`)·MAC·Linux IP는 플랫폼이 전역 잠금 안에서 발급하고, IP가 바닥나면 플랫폼 `IP_POOL_EXHAUSTED`를 507 `InsufficientResources`(resource.name=ipv4)로 냅니다. 플랫폼 `INVALID_SPEC`은 400 그대로입니다. 인스턴스 디렉터리는 `configuration.id`(내부 이름)로 찾고, 통합 테스트는 생성이 201이며 registry·Xen UUID가 요청 UUID와 같은지 항상 검증합니다.

테스트: `tests/test_platform_driver.py`는 봉투를 흉내 내는 FakeManager, `tests/test_platform_integration.py`는 저장소의 실제 `xenova_management`를 플랫폼 파트 픽스처(FakeXen·임시 registry)로 돌립니다.

## Xen 모드 현황

`XENOVA_DRIVER=xen`에서 지금 되는 것은 `GET /healthz`, `GET /api/v1/vms`, `GET /api/v1/vms/{vm_id}`입니다. `xl list`로 상태를, `xl list -l`의 `config.c_info.uuid`로 vm_id를 읽습니다(플랫폼 도구와 같은 방식). 생성·정지·삭제는 501을 내며 S15P31E110-115에서 구현합니다.

## 테스트

```bash
cd services/dom0-manager
python -m pytest
```

## 오류 코드와 HTTP 상태

드라이버·플랫폼 실패는 code와 HTTP 상태가 1:1이고, 모든 엔드포인트의 OpenAPI 응답 목록에 500·503·504(명령은 501도)가 들어 있습니다. Spring은 code를 보고 같은 상태로 전달해야 합니다. 백엔드가 `NotSupported`·`PlatformUnavailable`·`DriverTimeout` 변환을 반영하기 전까지는 모르는 code로 취급돼 502 `VM_CONTROL_BAD_RESPONSE`가 나갑니다.

| code | HTTP | 언제 |
| --- | --- | --- |
| `Unauthorized` | 401 | Bearer 서비스 토큰 없음·불일치. 미들웨어가 내며 `/healthz` 포함 |
| `InvalidArgument` | 400 | 입력 검증, nil UUID(Domain-0) 대상 호출, 플랫폼 `INVALID_SPEC`·템플릿 없음 |
| `VMNotFound` | 404 | 도메인 없음(단건), 등록 없음(삭제) |
| `VMExists` | 409 | 같은 vm_id가 떠 있음, 다른 명세로 재생성, 이름·멱등성 충돌 |
| `InvalidState` | 409 | 실행 중 삭제, `VM_NOT_STOPPED`·IP 충돌 등 |
| `InsufficientResources` | 507 | vcpus·memory_mib 부족, 플랫폼 Linux IP 풀 고갈(`ipv4`), 보드 저장소 부족 |
| `NotSupported` | 501 | 드라이버·플랫폼이 제공하지 않는 동작. xen 모드의 생성·정지·삭제 명령 |
| `PlatformUnavailable` | 503 | registry·저장소·Xen 일시 장애 (`STORAGE_UNAVAILABLE`·`RECOVERY_REQUIRED`·`CLEANUP_INCOMPLETE`), 다른 플랫폼 작업이 잠금 보유(`OPERATION_IN_PROGRESS`, 대기 한도 초과 포함, 재시도 가능) |
| `DriverTimeout` | 504 | xl·플랫폼 호출 시한 초과(정지 90초 포함) |
| `DriverError` | 500 | 그 외 실행 오류 |

## 아직 정하지 않은 것

- `/healthz` 본문은 `{"status": "ok" | "xen_unavailable"}`로 임시 정의.
- `xl list`의 `------`(플래그 없음)는 libxl 소스상 "실행 가능·미스케줄"이라 running으로 봅니다. 실보드에서 나타나는 조건을 확인한 뒤 계약 3.2장에 반영합니다(S15P31E110-36).
- desired_state 저장은 플랫폼 파트에 요청 중입니다. (IP 자동 배정은 들어와서 dom0-manager의 IP 풀 선택을 제거했습니다.)
- 템플릿 ID 명명이 Spring 설정과 플랫폼 카탈로그에서 다릅니다. 변환 규칙은 세 파트가 함께 정합니다.
