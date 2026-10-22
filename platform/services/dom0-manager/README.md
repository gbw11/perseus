# dom0-manager

## MO-04 단계 3: 내부 WebSocket 프록시

`/api/v1/vms/{vm_id}/console`에서 `ConsoleSession`의 입출력을 WebSocket과 연결합니다.
인증·메시지·종료 코드·시험 방법은
[콘솔 WebSocket 계약](../../../docs/contracts/console-websocket-v1.md)에 정의합니다.

- 기본 비활성. Linux dom0에서 `XENOVA_CONSOLE_ENABLED=1`과 `XENOVA_SERVICE_TOKEN`을 설정합니다.
- `Authorization: Bearer <service-token>`으로 접속하는 내부 게이트웨이용입니다.
  브라우저 로그인/사용자별 VM 권한 및 xterm.js 연결은 다음 단계에서 구성합니다.
- UUID → 현재 domid 확인, VM당 한 접속, 양방향 전달, 단절/VM 소멸 시 정리를 구현했습니다.
- `XENOVA_CONSOLE_LOCK_DIR`은 여러 worker가 공유할 로컬 경로입니다(기본 `/run/xenova/console-locks`).
- `websockets` 런타임 의존성을 추가했습니다. 기존 HTTP 경로·응답 계약은 동일합니다.

## MO-04 단계 2: Python PV 콘솔 모듈

`dom0_manager/console.py`의 `ConsoleSession`은 Linux dom0에서 PTY를 열고
`xl console -t pv <guest>`를 실행합니다. 추가 Python 패키지는 필요하지 않습니다.
단계 3의 WebSocket 라우터에서 이 모듈을 사용합니다.

```python
from dom0_manager.console import ConsoleSession, ConsoleExited

async def stream_console(verified_domid):
    async with ConsoleSession(verified_domid) as console:
        # 실제 게스트 로그인은 사용자 입력으로 진행합니다.
        await console.write(b"\r")
        try:
            while True:
                data = await console.read(timeout=30)
                # 다음 단계에서 이 바이트를 WebSocket으로 전달합니다.
                print(repr(data))
        except TimeoutError:
            pass  # 출력이 없는 것만으로 VM 장애를 판정하지 않습니다.
        except ConsoleExited as exc:
            print("console ended:", exc.returncode)
```

- `open()`은 프로세스 생성만 확인합니다. 대상 VM 없음 등의 접속 오류는
  터미널 출력과 `read()`의 `ConsoleExited.returncode`로 확인합니다.
- `read()`는 최대 64KiB의 원시 바이트 조각, `write()`는 최대 64KiB의 입력을
  처리합니다. UTF-8/ANSI 경계가 조각 중간에 올 수 있으므로 조각별로 디코딩하지 않습니다.
- PTY 로컬 echo/라인 처리를 끄며, 입출력 준비를 이벤트 루프로 기다립니다.
  읽기 대기 취소와 쓰기 timeout을 지원합니다.
- `close()`는 Ctrl+]로 접속을 해제합니다. 응답하지 않는 콘솔 프로세스는
  제한 시간 후 TERM/KILL로 정리하며 VM 종료 명령은 실행하지 않습니다.
- 같은 세션은 다시 열 수 없습니다. 각 연결에 새 세션을 생성합니다.
- 인증, VM UUID → 현재 게스트 domid 확인, VM별 접속 충돌 정책은
  다음 API 연결 단계의 책임입니다. 임의의 클라이언트 문자열을 실행 명령으로 받지 않습니다.
  `xl_command`는 서버의 신뢰된 실행 파일 인자이며 공개 요청에 노출하면 안 됩니다.
- dom0 콘솔 접속은 거부합니다. 게스트 로그인 암호는 모듈에 저장하지 않습니다.
- Linux dom0용입니다. Windows에서는 WSL을 이용해 테스트합니다.

Linux 단위 검증 (저장소의 `platform/services/dom0-manager`에서 실행):

```sh
python3 -B -m unittest discover -s tests -p test_console.py -v
```

실제 Xen 검증은 `tests/probe_console_live.py`와 `dom0_manager/console.py`를
dom0의 같은 임시 디렉터리에 복사한 뒤 실행합니다. 이 검증은 Debian 개발용
root 콘솔과 `zephyr-dev` / `zephyr-backup` 구성을 전제로 합니다.
게스트를 재시작하거나 설정을 바꾸지 않습니다.

```sh
python3 -B probe_console_live.py --execute --domain debian-dev
```

2026-10-21 QEMU ARM64 Xen에서 Debian `/dev/hvc0` 명령 왕복과 정상 detach,
없는 게스트의 실패 감지, Zephyr 두 대의 attach/detach를 확인했습니다.
검증 전후 세 게스트의 domid와 UUID가 동일했습니다.
Zephyr Starter는 출력 후 대기하며 입력 처리기가 없어 양방향 입출력은 미검증입니다.
라즈베리파이 실보드 검증은 별도입니다.

## 서비스 개요

dom0에서 도는 Python 제어 서비스입니다. Spring 관리 서버가 호출하는 내부 HTTP API(기본 `127.0.0.1:8081`, 배포는 Tailscale 주소 + Bearer 토큰)를 열고, 드라이버를 통해 VM을 조회·제어합니다. 드라이버는 세 가지입니다: `mock`(인메모리), `xen`(xl 직접 조회, 읽기 전용), `platform`(플랫폼 파트의 관리 라이브러리로 registry + Xen 생명주기).

```text
프론트 → Spring :8080 → dom0-manager :8081 → Driver ─┬─ platform: xenova_management.Manager → registry(/srv/xenova) + xl
                                   └ Provisioner      ├─ xen: xl list / info (조회만)
                                     (platform create/retire)  └─ mock: 인메모리
```

- 계약 문서: [docs/contracts/vm-api-contract-v1.md](../../../docs/contracts/vm-api-contract-v1.md)
- OpenAPI 원본: [docs/contracts/dom0-manager-openapi.yaml](../../../docs/contracts/dom0-manager-openapi.yaml). `tests/test_contract.py`가 서비스가 내는 스키마와 비교합니다.

## 실행 (Mock 모드)

보드·Xen 없이 바로 띄울 수 있습니다. 백엔드·프론트엔드 연동용입니다.

```bash
cd platform/services/dom0-manager
python -m pip install -e .[test]
XENOVA_DRIVER=mock XENOVA_MOCK_SAMPLES=1 python -m dom0_manager
# → http://127.0.0.1:8081/docs 에서 내부 API 문서 확인
```

```bash
curl -s localhost:8081/api/v1/vms | python -m json.tool
curl -s -X POST localhost:8081/api/v1/vms -H 'content-type: application/json' \
  -d '{"vm_id":"11111111-1111-4111-8111-111111111111","name":"개발용 VM","template_id":"alpine","template_version":"3.20","vcpus":1,"memory_mib":256}'
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
| `XENOVA_XL_COMMAND_TIMEOUT` | `95` | 명령용 xl 서브프로세스 호출 한도(초). platform 모드의 Manager 호출에는 걸리지 않는다 |
| `XENOVA_XL_SSH` | (없음) | 설정하면 xl 을 로컬 대신 `ssh -o BatchMode=yes <값> -- xl …`로 부른다(예: `root@100.64.0.1`). 개발 PC에서 xen 조회 모드로 실보드 출력을 계약과 대조할 때만 쓴다. 키 인증 필수 |
| `XENOVA_SHUTDOWN_GRACE` | `90` | 정상 종료 대기(초). 계약 v1.1: 시간이 지나도 자동 강제 종료하지 않고 실패로 보고. platform 모드는 플랫폼 고정 대기 90 만 허용(다른 값은 시작 거부) |
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

`Provisioner`는 `ensure_instance(vm_id, template_id, template_version, name=, vcpus=, memory_mib=) / instance_exists / delete_instance` 세 개입니다. platform 모드에서는 플랫폼 `create`(등록 + 디스크)와 `retire`(숨김 보관)로 구현되며, dom0-manager 안에 디스크 생성기는 없습니다. xen 모드는 조회 전용이라 `ensure_instance`가 501입니다.

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

`XENOVA_DRIVER=xen`에서 지금 되는 것은 `GET /healthz`, `GET /api/v1/vms`, `GET /api/v1/vms/{vm_id}`입니다. `xl list`로 상태를, `xl list -l`의 `config.c_info.uuid`로 vm_id를 읽습니다(플랫폼 도구와 같은 방식). 생성·정지·삭제는 501이며 `XENOVA_DRIVER=platform`에서 지원합니다.

실보드 대조는 보드에 아무것도 설치하지 않고 개발 PC에서 합니다. 보드 dom0에 PC 공개키를 넣은 뒤
`XENOVA_DRIVER=xen XENOVA_XL_SSH=root@<dom0 주소> python -m dom0_manager`로 띄우면 세 조회 엔드포인트가 실제 `xl` 출력으로 동작합니다.
2026-10-20 RPi4(rev 1.4, Xen 4.21.1-pre, 게스트 3개)에서 `/healthz` 200, 목록·단건 200, 미등록 404, nil UUID 400, stop 501을 확인했고,
그때의 `xl` 출력은 `tests/fixtures/rpi4-xen-4.21/`에 있어 `tests/test_xl.py`가 그대로 대조합니다. xl 호출마다 ssh 세션을 새로 열어
목록 한 번에 약 0.8초가 걸립니다(조회 한도 5초 안).

## 테스트

```bash
cd platform/services/dom0-manager
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

플랫폼 오류에는 본문에 `platform_code`(Manager 원본 코드)가 같이 나갑니다. 복구 기반 코드는 `HOST_QUIESCING`·`BOOT_ADMISSION_REQUIRED`·`INTENT_MISMATCH` → 409 `InvalidState`, `RECOVERY_MODE_UNSUPPORTED` → 501, `DEADLINE_EXCEEDED` → 504, `OBSERVATION_UNAVAILABLE`·`RECONCILE_REQUIRED`·`INTENT_RECONCILIATION_REQUIRED` → 503, `XEN_EXECUTION_FAILED` → 500 이고, 모르는 코드는 500 이되 `platform_code`는 보존합니다(계약 8.1 표). 코드로 분기할 때는 `detail`의 `"CODE: message"` 문자열이 아니라 `platform_code`를 씁니다. 정지 대기 만료는 플랫폼에 전용 코드가 아직 없어 `XEN_EXECUTION_FAILED`·`ARTIFACT_INVALID`의 메시지(`deadline`, `timed out`)로만 504 를 가립니다.

플랫폼 stop 은 대기 시간을 입력받지 않고 90초 고정이라 `shutdown_grace_s`는 전달되지 않습니다. platform 모드는 `XENOVA_SHUTDOWN_GRACE`를 90으로만 허용하며 다른 값은 시작 시 거부합니다. `XENOVA_XL_COMMAND_TIMEOUT`은 xl 서브프로세스에만 걸리고 Manager 호출(정지는 약 90초)을 제한하지 않습니다.

## 아직 정하지 않은 것

- `/healthz` 본문은 `{"status": "ok" | "xen_unavailable"}`로 임시 정의.
- desired_state 저장은 플랫폼 파트에 요청 중입니다. (IP 자동 배정은 들어와서 dom0-manager의 IP 풀 선택을 제거했습니다.)
- 템플릿 ID 명명이 Spring 설정과 플랫폼 카탈로그에서 다릅니다. 변환 규칙은 세 파트가 함께 정합니다.

create의 `name`은 Spring에서 받은 표시 이름(1~63자)을 그대로 전달합니다. 한글·공백을 허용하고 제어·줄 구분 문자와 앞뒤 공백은 거부합니다. `PlatformProvisioner`는 이를 플랫폼 `spec["name"]`에 넘기며 템플릿명·UUID로 대체하지 않습니다. 내부 실행 이름과 경로는 기존 UUID 기반 규칙을 사용합니다. 등록된 VM 재시작·재전달은 표시 이름을 갱신하지 않습니다.
