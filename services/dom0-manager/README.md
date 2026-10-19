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
PERSEUS_DRIVER=mock PERSEUS_MOCK_SAMPLES=1 python -m dom0_manager
# → http://127.0.0.1:8081/docs 에서 내부 API 문서 확인
```

```bash
curl -s localhost:8081/api/v1/vms | python -m json.tool
curl -s -X POST localhost:8081/api/v1/vms -H 'content-type: application/json' \
  -d '{"vm_id":"11111111-1111-4111-8111-111111111111","template_id":"alpine","template_version":"3.20","vcpus":1,"memory_mib":256}'
curl -i -X POST localhost:8081/api/v1/vms/11111111-1111-4111-8111-111111111111/stop   # 204
```

`PERSEUS_MOCK_SAMPLES=1`이면 배포판 3종 샘플 VM이 미리 떠 있습니다. vm_id는 고정(uuid5)이라 그대로 써도 됩니다.

| 이름 | vm_id | vcpus / memory_mib |
| --- | --- | --- |
| alpine-drive | `59b3e0d6-499d-517e-ab0a-27e3a4d56e89` | 1 / 256 |
| debian-safety | `c552c812-a816-534f-932b-fd4023d352b7` | 1 / 512 |
| ubuntu-infotainment | `d97823ca-68a2-5c6a-8e4e-f353071064bd` | 2 / 512 |

값은 `uuid5(NAMESPACE_URL, "perseus/mock/<이름>")`이며 `dom0_manager.drivers.mock.SAMPLE_VMS`에 있습니다.

## 설정 (환경변수)

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `PERSEUS_DRIVER` | `mock` | `mock` / `xen` / `platform` |
| `PERSEUS_HOST` / `PERSEUS_PORT` | `127.0.0.1` / `8081` | 바인드 주소. dom0 밖으로 열지 않습니다 |
| `PERSEUS_XL_QUERY_TIMEOUT` | `5` | 조회용 xl 호출 한도(초). Spring 조회 대기 6초 안 |
| `PERSEUS_XL_COMMAND_TIMEOUT` | `95` | 명령용 xl 호출 한도(초) |
| `PERSEUS_SHUTDOWN_GRACE` | `90` | 정상 종료 대기(초). 계약 v1.1: 시간이 지나도 자동 강제 종료하지 않고 실패로 보고 |
| `PERSEUS_SERVICE_TOKEN` | (없음) | 설정하면 `/healthz`를 포함한 모든 요청에 `Authorization: Bearer <토큰>`을 요구(401). Tailscale 바인드 배포에서는 필수 |
| `PERSEUS_HOST_CPUS` | `4` | vcpus 상한 기본값. `xl info`의 nr_cpus가 있으면 그 값 우선 |
| `PERSEUS_INSTANCE_ROOT` | `/srv/xenova/instances` | 인스턴스 루트 (xen 모드의 등록 확인용. platform 모드는 Manager의 data 경로를 씀) |
| `PERSEUS_MOCK_SAMPLES` | `0` | Mock 샘플 VM 3종 미리 생성 |
| `PERSEUS_LOG_LEVEL` | `INFO` | |

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
| `health()` | O | 하이퍼바이저·registry 응답 여부 → `/healthz` 503 판정 |
| `reboot`, `set_vcpus`, `set_memory`, `pin_vcpu`, `set_sched_weight`, `get_stats` | v2 | 208·101·112에서. 기본 구현은 `NotImplementedError` |

`Provisioner`는 `ensure_instance(vm_id, template_id, template_version, vcpus=, memory_mib=) / instance_exists / delete_instance` 세 개입니다. platform 모드에서는 플랫폼 `create`(등록 + 디스크)와 `retire`(숨김 보관)로 구현되며, dom0-manager 안에 디스크 생성기는 없습니다. xen 모드는 조회 전용이라 `ensure_instance`가 501입니다.

`DELETE /api/v1/vms/{vm_id}`는 v1.1 의미입니다. 실행 중이면 409 `InvalidState`, 등록이 없으면 404, 정지된 VM은 숨김 보관(retire)으로 204. 강제 종료는 하지 않습니다.

`POST /api/v1/vms`로 같은 `vm_id`가 다시 오면 등록된 생성 명세(template_id·template_version·vcpus·memory_mib)와 비교합니다. 같으면 디스크를 재사용해 다시 띄우고(재시작), 다르면 409 `VMExists`입니다(계약 4.2 불변조건).

상태 매핑은 계약 3.2장을 따릅니다. `b`(blocked)는 I/O 대기 중인 정상 상태이고, 플래그가 없는 `------`도 running으로 봅니다. 그 외 모르는 문자는 조용히 running으로 넘기지 않고 `DriverError`를 냅니다.

## platform 모드 (보드 실제 연동, S15P31E110-115)

`PERSEUS_DRIVER=platform`은 플랫폼 파트의 관리 라이브러리 `xenova_management.Manager`를 직접 import해서 registry(`/srv/xenova/instances`)와 Xen을 함께 다룹니다. xl 명령을 직접 부르지 않습니다(`xl info`만 자원 조회용).

```bash
# 보드(dom0)에서
PERSEUS_DRIVER=platform PERSEUS_PLATFORM_LIB=/usr/lib/xenova PERSEUS_SERVICE_TOKEN=<토큰> python -m dom0_manager
```

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `PERSEUS_PLATFORM_LIB` | `/usr/lib/xenova` | `xenova_management` 등 플랫폼 모듈 경로. 여러 개면 OS 경로 구분자로 |
| `PERSEUS_IP_POOL_START` / `END` | `2` / `99` | Linux VM에 줄 `192.168.77.x` 풀. 플랫폼이 자동 배정하기 전까지 dom0-manager가 빈 주소를 고르고 `IP_CONFLICT`면 다음 주소로 재시도 |

동작 대응:

| API | 플랫폼 호출 |
| --- | --- |
| `GET /vms`, `GET /vms/{id}` | `list` / `get`. `domid`가 있는 항목만 도메인으로 봄. 등록만 된 VM은 목록에 없고 단건은 404 |
| `POST /vms` | 등록이 없으면 `create`(`idempotency_key=create-<vm_id>`, Spring 발급 `vm_id`를 그대로 전달) → `start` |
| `POST /vms/{id}/stop` | `stop` (`force=false`, 플랫폼이 최대 90초 대기). 시한 초과는 504 |
| `DELETE /vms/{id}` | 실행 중 409 → 정지 후 `retire`(숨김 보관) |

플랫폼 오류 코드 → HTTP: `INVALID_SPEC`·템플릿 없음 400 / `VM_NOT_FOUND` 404 / 이름·IP·멱등성 충돌과 `VM_NOT_STOPPED`·`OPERATION_IN_PROGRESS` 등 409 / `INSUFFICIENT_STORAGE` 507 / `STORAGE_UNAVAILABLE`·`RECOVERY_REQUIRED`·`CLEANUP_INCOMPLETE` 503 / 시한 초과 504 / 그 외 500.

플랫폼 `xen_state` → 계약 상태: `running`·`blocked`·`unknown`(플래그 없음) → running, `paused` → paused, `shutdown`·`dying` → stopping, `crashed` → crashed, `stopped` → 도메인 없음.

**막힌 것**: 플랫폼 `create`가 아직 `spec['vm_id']`를 받지 않고 보드에서 UUID를 발급합니다. 팀 결정(Spring 발급)과 달라서, 새 VM 생성은 플랫폼이 `vm_id`를 받을 때까지 501을 냅니다. 조회·시작·정지·보관은 영향 없습니다.

테스트: `tests/test_platform_driver.py`는 봉투를 흉내 내는 FakeManager, `tests/test_platform_integration.py`는 저장소의 실제 `xenova_management`를 플랫폼 파트 픽스처(FakeXen·임시 registry)로 돌립니다.

## Xen 모드 현황

`PERSEUS_DRIVER=xen`에서 지금 되는 것은 `GET /healthz`, `GET /api/v1/vms`, `GET /api/v1/vms/{vm_id}`입니다. `xl list`로 상태를, `xl list -l`의 `config.c_info.uuid`로 vm_id를 읽습니다(플랫폼 도구와 같은 방식). 생성·정지·삭제는 501을 내며 S15P31E110-115에서 구현합니다.

## 테스트

```bash
cd services/dom0-manager
python -m pytest
```

## 오류 코드와 HTTP 상태

드라이버·플랫폼 실패는 code와 HTTP 상태가 1:1입니다. Spring은 code를 보고 같은 상태로 전달합니다.

| code | HTTP | 언제 |
| --- | --- | --- |
| `InvalidArgument` | 400 | 입력 검증, 플랫폼 `INVALID_SPEC`·템플릿 없음 |
| `VMNotFound` | 404 | 도메인 없음(단건), 등록 없음(삭제) |
| `VMExists` | 409 | 같은 vm_id가 떠 있음, 다른 명세로 재생성, 이름·멱등성 충돌 |
| `InvalidState` | 409 | 실행 중 삭제, `VM_NOT_STOPPED`·`OPERATION_IN_PROGRESS`·IP 충돌 등 |
| `InsufficientResources` | 507 | vcpus·memory_mib·ipv4 풀 부족, 보드 저장소 부족 |
| `NotSupported` | 501 | 플랫폼이 아직 제공하지 않는 동작(예: create의 호출자 지정 vm_id), xen 모드의 명령 |
| `PlatformUnavailable` | 503 | registry·저장소·Xen 일시 장애 (`STORAGE_UNAVAILABLE`·`RECOVERY_REQUIRED`·`CLEANUP_INCOMPLETE`) |
| `DriverTimeout` | 504 | xl·플랫폼 호출 시한 초과(정지 90초 포함) |
| `DriverError` | 500 | 그 외 실행 오류 |

## 아직 정하지 않은 것

- `/healthz` 본문은 `{"status": "ok" | "xen_unavailable"}`로 임시 정의.
- `xl list`의 `------`(플래그 없음)는 libxl 소스상 "실행 가능·미스케줄"이라 running으로 봅니다. 실보드에서 나타나는 조건을 확인한 뒤 계약 3.2장에 반영합니다(S15P31E110-36).
- Linux IP 자동 배정과 desired_state 저장은 플랫폼 파트에 요청 중입니다. 들어오면 dom0-manager의 IP 풀 할당은 제거합니다.
- 템플릿 ID 명명이 Spring 설정과 플랫폼 카탈로그에서 다릅니다. 변환 규칙은 세 파트가 함께 정합니다.
