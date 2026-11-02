# dom0-manager

## 등록 목록과 단건 통지

`platform` 드라이버의 `GET /api/v1/vm-registrations`는 실행 여부와 무관한 REGISTERED 목록을 여덟 필드 배열로 반환합니다.
`POST /api/v1/vm-registrations/{vm_id}/notify`는 UUID만 받아 현재 명세를 읽고 Spring 등록 POST를 한 번 보냅니다.
기존 서비스 Bearer 인증이 필수이며 입력 body·URL·token 중계는 받지 않습니다. runtime `/vms`는 기존 의미를 유지합니다.

```sh
curl --fail-with-body -H "Authorization: Bearer $DOM0_SERVICE_TOKEN" \
  http://127.0.0.1:8081/api/v1/vm-registrations
curl --fail-with-body -X POST -H "Authorization: Bearer $DOM0_SERVICE_TOKEN" \
  http://127.0.0.1:8081/api/v1/vm-registrations/850e8400-e29b-41d4-a716-446655440001/notify
```

Spring URL과 `VM_REGISTRATION_SERVICE_TOKEN`은 root 0600 `/etc/xenova/spring-registration.env`로 FastAPI에만 공급합니다.
미설정은 notify에만 영향을 줍니다. 성공은 `confirmed=true`와 원 HTTP 200/201을 검증한 경우이고,
upstream 거절(502·원 http_status/code), 응답 미확인(504), 설정/연결/포화(503)를 구별합니다.
자동 retry/redirect/후속 GET은 없습니다. 일반 생성/factory의 공통 hook이 자동 기회를 소유하며 API startup/create service가 추가 송신하지 않습니다.

실제 import·wire·한도·실패 처리와 설치 순서는 [공급 계약](../../../docs/contracts/vm-provisioning-v1.md),
정상/실패의 별도 프로세스 시험은 [검증 기록](../../../docs/verification/vm-registration-api-20261102.md)을 따릅니다.
구형 `xenova-vm registration-notify`와 하위 직접 Spring 설정은 제거했습니다. 하위 조회는 `xenova-vm registrations [UUID] --json`입니다.

factory 통지는 등록 완료 사건에서 일시 systemd job을 비동기 예약합니다. API unit은 `Type=notify`이며
제품 entrypoint가 ASGI startup·listen 완료 뒤 `READY=1`을 보냅니다. 통지 job은 그 뒤 기존 15초 예산으로
실행하므로 first-boot·autostart는 기다리지 않습니다. API restart의 dependent 재진입도 같은 boot의
배타 실행 표식으로 막습니다. API 기동 실패·응답 유실·다음 boot에서 자동 재예약하지 않습니다.
API entrypoint·unit·하위 hook을 함께 설치해야 합니다. `uvicorn dom0_manager.main:app` 직접 실행은
HTTP 개발 실행이며 systemd 준비 신호를 내는 제품 entrypoint를 대신하지 않습니다.

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
curl -s localhost:8081/api/v1/stats | python -m json.tool   # xentop 원시값 (mock 은 가짜 누적값)
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
| `XENOVA_RESPONSE_RESERVE` | `0.5` | 요청 예산(`X-Request-Budget-Ms`)이 있을 때 하위 호출에 넘기기 전에 응답 몫으로 남길 초. 실측 전 임시값, 0 이상 |
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
| `XENOVA_PLATFORM_READ_FORK` | `0` | `1`이면 일반 조회(get·list 등)를 직렬화하지 않고 호출마다 요청별 `Manager.fork()`로 부른다. 하위가 조회를 공통 실행 잠금 없이 처리하는 설치본을 확인한 뒤에만 켠다. Worker 도 같은 값을 읽는다 |

동작 대응:

| API | 플랫폼 호출 |
| --- | --- |
| `GET /vms`, `GET /vms/{id}` | `list` / `get`. `domid`가 있는 항목만 도메인으로 봄. 등록만 된 VM은 목록에 없고 단건은 404 |
| `POST /vms` | 등록이 없으면 `create`(`idempotency_key=create-<vm_id>`, Spring 발급 `vm_id`를 그대로 전달) → `start` |
| `POST /vms/{id}/stop` | `stop` (`force=false`, 플랫폼이 최대 90초 대기). 시한 초과는 504. `Idempotency-Key` 가 있으면 아래 "정지 요청 추적" |
| `GET /vms/{id}/control-operations?request_key=` | `control-operation` (`vm_id`, `idempotency_key`, `timeout_ms` 기본 5000). 읽기만 한다 |
| `DELETE /vms/{id}` | 실행 중 409 → 정지 후 `retire`(숨김 보관) |

플랫폼 오류 코드 → HTTP: `INVALID_SPEC`·템플릿 없음 400 / `VM_NOT_FOUND` 404 / 이름·IP·멱등성 충돌과 `VM_NOT_STOPPED` 등 409 / `INSUFFICIENT_STORAGE`·`IP_POOL_EXHAUSTED` 507 / `STORAGE_UNAVAILABLE`·`RECOVERY_REQUIRED`·`CLEANUP_INCOMPLETE`·`OPERATION_IN_PROGRESS`(잠금 보유, 재시도 가능) 503 / 시한 초과 504 / 그 외 500.

플랫폼 `xen_state` → 계약 상태: `running`·`blocked`·`unknown`(플래그 없음) → running, `paused` → paused, `shutdown`·`dying` → stopping, `crashed` → crashed, `stopped` → 도메인 없음.

`load_manager`는 `Manager`를 `SerializedManager`로 감싸 프로세스 안에서 `call`을 직렬화합니다. 플랫폼 `Manager`가 요청별 상태(`current_operation`·`replayed`)를 인스턴스 변수에 두어 FastAPI 스레드가 동시에 부르면 섞이기 때문입니다. 차례를 기다리는 시간은 `XENOVA_PLATFORM_BUSY_TIMEOUT`(기본 5초)으로 제한하고, 넘으면 플랫폼의 잠금 충돌과 같은 `OPERATION_IN_PROGRESS` 봉투를 돌려 503으로 냅니다. 정지가 90초 동안 잠금을 쥐어도 조회가 스레드풀에 쌓이지 않습니다. `/healthz`는 Manager를 거치지 않고 `xl info`와 registry 디렉터리 존재만 확인하므로 잠금이나 느린 `xl list`에 영향받지 않습니다.

**조회의 요청별 Manager (S15P31E110-333)**: `XENOVA_PLATFORM_READ_FORK=1`이면 하위 계약의 일반 조회(`list`·`get`·`intents`·`probe`·`execution-context`·`templates`·`operation`·`control-operation`·`recovery-operation`·`provisioning`, [cli-manager-spec "일반 조회의 동시 실행과 정합성"](../../../docs/platform/cli-manager-spec.md#read-concurrency))는 직렬화를 거치지 않고 호출마다 새 `fork()` 객체로 부릅니다. 긴 Linux create 가 직렬화를 쥐어도 다른 VM 의 상세·목록이 busy timeout 503 없이 하위로 들어갑니다. 변경 action(create·start·stop·retire·recover_once·provision 등)은 그대로 직렬화합니다. 같은 요청 객체를 두 호출이 공유하지 않고 공유 `Manager` 를 잠금 없이 부르지 않습니다. 하위의 관측 슬롯 경합(`OPERATION_IN_PROGRESS`)·`TARGET_CHANGED`·`OBSERVATION_UNAVAILABLE` 은 지금처럼 503·409 로 냅니다. 기본은 꺼져 있습니다: `fork()` 가 있어도 조회가 공통 잠금을 쓰는 옛·혼합 설치본에서는 503 대신 하위 잠금 대기로 바뀔 뿐이기 때문입니다. `fork` 가 없으면 켜도 직렬화 경로입니다.

**생성 입력**: `create`에는 Spring 발급 `vm_id`, 표시 이름(`<template_id 정규화>-<vm_id 앞 12자리>`), 템플릿 ID·버전, vcpus·memory_mib만 보냅니다. 내부 실행 이름(`vm-<hex>`)·MAC·Linux IP는 플랫폼이 전역 잠금 안에서 발급하고, IP가 바닥나면 플랫폼 `IP_POOL_EXHAUSTED`를 507 `InsufficientResources`(resource.name=ipv4)로 냅니다. 플랫폼 `INVALID_SPEC`은 400 그대로입니다. 인스턴스 디렉터리는 `configuration.id`(내부 이름)로 찾고, 통합 테스트는 생성이 201이며 registry·Xen UUID가 요청 UUID와 같은지 항상 검증합니다.

### 정지 요청 추적 (S15P31E110-305)

계약 4.3.1·4.8·4.9. 하위 플랫폼의 정지 의도 빠른 수락·멱등 키·원 작업 조회·호출 예산을 HTTP 로 엽니다.

- **키 있는 stop**: VM 락·`SerializedManager` 를 기다리지 않고 `Manager.fork()` 의 요청별 객체로 `stop` 을 부릅니다. 플랫폼은 짧은 admission 잠금으로 정지 의도를 내구 저장하고, 긴 start·복구가 실행 잠금을 쥐고 있으면 기다리지 않고 `OPERATION_IN_PROGRESS` + `details.accepted=true, completion=pending` 으로 돌아옵니다. 이것을 `202 pending` 으로, 완료(재전달 포함)를 `200 completed` 로 냅니다. 상태는 `accepted`·`completion`·`phase`·오류 코드로 정합니다(`completion=pending` 만 보고 202 를 정하지 않습니다. `phase=uncertain`·`RECONCILE_REQUIRED` 는 `uncertain` 503, `superseded` 는 409)
- **키 없는 stop**: 지금과 같습니다(VM 락 → 직렬화 → 동기 204). Spring 이 키를 보내기 전까지 응답 의미가 바뀌지 않습니다
- **원 작업 조회**도 요청별 객체로 부릅니다. 플랫폼은 admission 잠금과 원 기록만 읽습니다. `VM_NOT_FOUND` 는 `404 OperationNotFound`(검색 부재, 미수락 증거 아님)
- `fork` 가 없는 옛 Manager 는 직렬화된 경로를 그대로 씁니다(공유 Manager 를 잠금 없이 부르지 않습니다). `budget_ms` 를 모르는 옛 Manager 에는 예산을 넘기지 않습니다
- **요청 예산**(`budget.py`): `X-Request-Budget-Ms` 가 있으면 미들웨어가 도착 시각부터 기한을 contextvar 에 둡니다. VM 락·직렬화 대기가 이 안에서 끝나고, 하위에는 `budget_ms`(get/list/조회는 `timeout_ms` 도)로 남은 값에서 `XENOVA_RESPONSE_RESERVE` 를 뺀 만큼 줍니다. 차례가 와도 남은 값이 없으면 제출하지 않고 504 입니다
- 오류 본문에 플랫폼 원본 `retryable` 이 붙고, 키 있는 stop 의 오류에는 원 작업 문맥(`operation`)이 붙습니다. raw journal·경로·인증정보는 내지 않습니다
- mock·xen 드라이버는 작업 기록이 없어 키 있는 stop 을 동기 정지 뒤 `200 completed`(`operation_id: null`)로 답하고 조회는 501 입니다

테스트: `tests/test_platform_driver.py`는 봉투를 흉내 내는 FakeManager, `tests/test_control_operations.py`는 fork·budget_ms·control-operation 을 아는 Manager 흉내로 정지 요청 추적·요청 예산·원 작업 ID 불변조건을, `tests/test_platform_integration.py`는 저장소의 실제 `xenova_management`를 플랫폼 파트 픽스처(FakeXen·임시 registry)로 돌립니다. 통합 시험에는 실제 Manager 의 키 있는 stop 완료·같은 키 재전달(추가 shutdown 0)·조회·키 충돌, 실행 잠금을 쥔 동안의 202 수락과 잠금 해제 뒤 같은 키 이행, 예산 만료 시 제출·기록 0 이 들어 있습니다. 이 파일은 fcntl 이 필요해 Linux(CI)에서만 돕니다. 실보드 종단 수락은 별도입니다.

## Xen 모드 현황

`XENOVA_DRIVER=xen`에서 지금 되는 것은 `GET /healthz`, `GET /api/v1/vms`, `GET /api/v1/vms/{vm_id}`, `GET /api/v1/stats`입니다. `xl list`로 상태를, `xl list -l`의 `config.c_info.uuid`로 vm_id를 읽습니다(플랫폼 도구와 같은 방식). 생성·정지·삭제는 501이며 `XENOVA_DRIVER=platform`에서 지원합니다.

실보드 대조는 보드에 아무것도 설치하지 않고 개발 PC에서 합니다. 보드 dom0에 PC 공개키를 넣은 뒤
`XENOVA_DRIVER=xen XENOVA_XL_SSH=root@<dom0 주소> python -m dom0_manager`로 띄우면 세 조회 엔드포인트가 실제 `xl` 출력으로 동작합니다.
2026-10-20 RPi4(rev 1.4, Xen 4.21.1-pre, 게스트 3개)에서 `/healthz` 200, 목록·단건 200, 미등록 404, nil UUID 400, stop 501을 확인했고,
그때의 `xl` 출력은 `tests/fixtures/rpi4-xen-4.21/`에 있어 `tests/test_xl.py`가 그대로 대조합니다. xl 호출마다 ssh 세션을 새로 열어
목록 한 번에 약 0.8초가 걸립니다(조회 한도 5초 안).

## 통계 (xentop)

`GET /api/v1/stats`는 `xentop -b -i 1 -n -x -f` 한 번의 출력으로 실행 중 도메인 전부의 누적 카운터·현재값을 돌려줍니다(계약 2.4·4.6, S15P31E110-112). xen·platform 모드 모두 xl 과 같은 실행기(로컬 또는 `XENOVA_XL_SSH`)로 xentop 을 부르고, 이름을 `xl list -l`의 `c_info.uuid`와 대조해 `vm_id`를 붙입니다. 플랫폼 Manager 를 거치지 않아 잠금의 영향을 받지 않습니다.

- 사용률(CPU %, bytes/s)은 싣지 않습니다. Spring 이 연속 표본의 차이로 계산하고, `domid`가 바뀌거나 누적값이 줄면 재시작으로 봅니다.
- 측정하지 못한 필드는 0 이 아니라 `null`이고 `unavailable`에 사유가 있습니다. `NO_DEVICE`(NETS·VBDS 0, Zephyr), `DEVICE_ERROR`(xentop 이 `-`), `NOT_REPORTED`(`no limit`·`n/a`).
- 네트워크 바이트는 `-n` 장치 줄의 값(정확)을 쓰고, 장치 줄이 없으면 KiB 열 × 1024 로 대신합니다. 디스크는 `VBD_RSECT`·`VBD_WSECT` × 512 입니다.
- xentop 의 STATE 글자 순서는 `d s b c p r`로 `xl list`와 다릅니다. 파서가 따로 있고 xl 순서의 문자열은 거부합니다.
- 관리 대상 판별: platform 모드는 플랫폼 라이브러리의 읽기 전용 검증 `xenova_vm.load_records(validate_assets=False)`가 돌려준 등록 VM 만 냅니다(잠금·Xen 호출 없음). 플랫폼이 거부하는 상태(심볼릭 링크, id·디렉터리 불일치, 중복 신원, 레코드 누락)는 불완전한 200 대신 503 입니다. Xen 은 설정에 uuid 가 없어도 자동 발급하므로 xl 로 직접 띄운 도메인도 uuid 가 있고, 그런 도메인은 registry 에 없어 빠집니다. xen 모드는 진단용이라 uuid 가 있는 도메인을 전부 냅니다. Domain-0 은 어느 모드에서도 없습니다.
- 조회 한도 5초는 요청 전체입니다. registry 조회와 xentop 이 쓴 시간을 뺀 나머지만 다음 명령에 주고, 다 쓰면 Xen 명령 없이 504 입니다. xentop 을 실행할 수 없으면 503, 출력 해석 실패 500 입니다.

## Recovery Worker (heartbeat 감시기)

자동 복구 주체는 제어 계층입니다(VM API 계약 1.2절). `python -m dom0_manager.recovery`는 dom0-manager HTTP 와 **별도 프로세스·별도 systemd 유닛**(`deploy/xenova-recovery-worker.service`, `After=xenova-vms.service mosquitto.service`, `Before=xenova-host-admission.service`, PartOf 없음)으로 돌며 **VM 재시작이 필요한지**만 판정합니다. 재시작 실행·재시도·backoff 는 복구 정책 엔진(S15P31E110-125)이 판정 큐를 소비해 수행합니다. 차량 제어권 전환은 게이트웨이의 일이며 여기서 보지 않습니다.

입력 세 가지와 읽는 방법:

| 입력 | 어디서 | 얼마나 자주 |
| --- | --- | --- |
| 의도 (`desired_state`, `intent_applicable`, `intent_revision`, `lifecycle`, `guest_kind`) | 플랫폼 `Manager intents` (S15P31E110-336) | 매 주기 읽습니다. 저장된 의도·등록만 읽고 Xen·공통 잠금을 쓰지 않습니다. **판정 직전에는 그 VM 만 `get`으로 재확인**합니다. 읽기에 실패하면 마지막으로 읽은 등록은 건강 보고 대상으로만 남기고(보관·삭제로 해석해 retained 를 지우지 않음) 오래된 의도로는 아무것도 판정하지 않습니다: crashed·absent 를 포함한 판정을 보류하고 건강은 `OBSERVATION_UNAVAILABLE`, 열린 복구 episode 도 닫지 않습니다. 다시 읽히면 자동으로 재개합니다. `intents` 를 모르는 옛 Manager(`INVALID_SPEC`)면 경고 뒤 `list`(플랫폼 잠금 + VM 마다 Xen 관측)를 `XENOVA_RECOVERY_INTENT_REFRESH`(기본 60초)마다 읽습니다 |
| Xen 실행 상태 (도메인 유무·상태·domid) | `xl list` / `xl list -l` (잠금 없음) | 매 주기(5초) |
| 생존 신호 | dom0 Mosquitto `xenova/v1/vms/+/heartbeat`(agent.heartbeat, Linux)와 `xenova/v1/apps/+/events` 중 `app.heartbeat`(Zephyr 주행 VM, 시나리오 D8) | 구독. 수신 단조 시각만 쓰고 `sent_at` 은 쓰지 않음. retain 플래그가 켜진 수신(구독 직후 브로커가 주는 옛 값)은 무시. agent.heartbeat 는 type·빈 payload·토픽-봉투 vm_id 일치, app.heartbeat 는 type·`state` 허용값·`uptime_s`(0 이상 유한)·app_id 일치를 검사해 맞는 것만 기록 |
| Zephyr 앱 대상 여부 | 템플릿 카탈로그 capability `app_heartbeat`(Manager `templates`, 의도와 같이 캐시). `true` 앱 펌웨어, `false` starter(Xen 상태만). JSON boolean 만 믿고, 문자열 `"false"` 같은 값이면 그 템플릿의 VM 은 대상 여부 불명(OBSERVATION_UNAVAILABLE) | 선언이 없는 템플릿은 신호를 한 번 받은 뒤 armed 로 다루고, armed 는 `XENOVA_RECOVERY_STATE_DIR/armed.json`(`{"version":1,"armed":{vm_id: app_id},"history_lost":bool,"agent_delivered":[vm_id]}`) 에 남겨 Worker 재시작 뒤에도 유지. 플랫폼 카탈로그는 기존·네트워크 Starter에 `false`를 노출합니다. `true` 선언은 감시 대상이라는 뜻이고, 손실 판정은 아래 "VM 별 수신 이력"이 있어야 합니다 |
| VM 별 수신 이력 | Worker 가 그 VM 의 신호를 실제로 받은 적이 있는가. Linux 는 `agent_delivered`, Zephyr 는 `armed`. 같은 `armed.json` 에 영속 | 그 VM 의 신호를 처음 받을 때 기록, 등록에서 사라지면 삭제. `agent_delivered` 가 없는 옛 파일은 이력이 비어 있는 것으로 읽음(손상 아님) |

판정 규칙 (`recovery/watcher.py`, 전부 단조 시계). VM 마다 OK / UNKNOWN / FAILED 셋 중 하나이고 FAILED 일 때만 사유가 있습니다.

| 판정 | 조건 |
| --- | --- |
| 대상 | lifecycle `REGISTERED` 이고 적용 가능한 의도가 `running` 인 VM 만. 사용자가 끈 VM(`stopped`)·의도 `unknown`·보관본은 절대 대상이 아님(OK) |
| `DOMAIN_CRASHED` | xl 상태 `crashed` 면 즉시 (Zephyr 포함) |
| `DOMAIN_ABSENT` | 도메인이 없는 채로 `XENOVA_RECOVERY_ABSENT_GRACE`(기본 60초) 경과. 그 전은 UNKNOWN(플랫폼 start·복구 진행 중일 수 있음) |
| `HEARTBEAT_LOST` | Linux VM 이 `running` 인데 agent.heartbeat 가 `XENOVA_RECOVERY_HEARTBEAT_LOST`(기본 30초, 백엔드 OFFLINE 과 같음, 하한 15초) 동안 없음 |
| `APP_HEARTBEAT_LOST` | Zephyr VM 이 `running` 인데 app.heartbeat 가 `XENOVA_RECOVERY_APP_HEARTBEAT_LOST`(기본 15초, 하한 3초) 동안 없음. 대상은 템플릿 capability `app_heartbeat` 로 선언된 VM 또는 신호를 한 번이라도 받아 armed 된 VM(영속). 둘 다 아닌 Zephyr(앱 없는 starter 펌웨어)는 Xen 상태로만 봅니다 |
| 부팅 유예 | 도메인이 새로 보인 뒤(새 domid) 첫 신호를 받기 전까지 `XENOVA_RECOVERY_BOOT_GRACE`(Linux 90초)·`XENOVA_RECOVERY_ZEPHYR_BOOT_GRACE`(30초) 동안 UNKNOWN. 이전 부팅의 신호는 세지 않음. 첫 신호 뒤에는 유예 없이 창으로만 판정 |
| 관측 경로 | 필터별 **구독 승인(SUBACK)** 과 **실제 수신 증거** 둘 다 봅니다. 연결(CONNACK)만으로는 부족하고, 승인만으로도 부족합니다. Mosquitto 는 읽기 ACL 에 막힌 구독도 SUBACK 성공으로 답하고 전달만 거르기 때문입니다(실보드 10/23 확인: 읽기 규칙 없는 ACL 에서 정확한 토픽·와일드카드 모두 승인 0, 전달 없음). 그래서 승인 뒤 그 채널에서 메시지를 하나라도 받기 전에는, 그리고 승인된 지 한 창(window)이 안 됐으면 그 신호의 손실 판정은 UNKNOWN(건강 OBSERVATION_UNAVAILABLE)이고 승인 뒤 한 창 동안 아무것도 안 오면 경고를 남깁니다. agent 와 app 경로를 따로 봅니다. 두 주기 사이에 끊겼다 다시 붙으면 승인이 그대로여도 연결 횟수(`connections`)가 바뀌므로, 끊기기 전 수신은 버리고 재연결 뒤 첫 메시지와 한 창을 다시 기다립니다. **브로커·ACL 문제는 VM 장애가 아닙니다.** crashed·absent 는 Xen 관측이라 계속 판정하고, 신호가 신선하면 경로 상태와 무관하게 OK. 한계: VM 이 하나뿐이고 그 에이전트가 Worker 기동 뒤 한 번도 보내지 않으면 "에이전트 미기동"과 "브로커가 안 줌"을 구별할 수 없어 HEARTBEAT_LOST 대신 OBSERVATION_UNAVAILABLE 로 남습니다 |
| VM 별 전달 근거 | 채널의 실제 수신(위)은 어느 VM 의 메시지든 되므로 "브로커가 이 채널을 지금 전달한다"는 공통 근거일 뿐입니다. **대상 VM 의 손실 판정은 그 VM 의 수신 이력이 있어야 합니다.** 다른 VM 의 수신으로 대상 VM 을 판정하지 않습니다. 이력이 없는 VM 은 계정·ACL 오류, 배포 누락, 첫 부팅 정체를 구별할 수 없으므로 UNKNOWN(유예 중 STARTING, 그 뒤 OBSERVATION_UNAVAILABLE)입니다. 이력은 손실 판정 자격일 뿐 현재 신호가 아닙니다: 재부팅 뒤 이전 부팅의 신호는 세지 않고, 이력이 있는 VM 은 부팅 유예가 지나도 신호가 없으면 판정합니다(부팅 중 멈춘 VM 복구). 한계: 첫 부팅부터 멈춘 VM 과, host 재부팅(이력이 tmpfs 라 비워짐) 뒤 다시 신호를 보내기 전에 멈춘 VM 은 자동 복구하지 않습니다. 한 번 받은 뒤 그 VM 의 ACL 만 바뀌는 경우는 생존 신호만으로 앱 정지와 구별할 수 없습니다(episode 당 시도 상한으로 묶임). 기록 손상은 이력이 없는 것과 같이 다룹니다 |
| 공통 경로 (S15P31E110-335) | 채널 증거는 구독 승인 뒤 첫 수신으로 세우고 구독이 끊길 때까지 유지하므로, 나중에 브로커·브리지·ACL 이 막혀 모든 VM 이 함께 조용해지는 것은 알지 못합니다. 그래서 손실 판정 직전에 같은 채널의 **동료 발행자**(대상이 아닌 등록 VM 중 의도 running·Xen running·수신 이력 있음·이번 부팅에 신호를 보낸 VM)를 봅니다. 하나라도 **동료 신선도 창**(`XENOVA_RECOVERY_AGENT_PEER_FRESH` 기본 15초, `XENOVA_RECOVERY_APP_PEER_FRESH` 기본 5초) 안에 신호를 보냈으면 판정하고, 동료가 모두 조용하면 공통 경로를 의심해 보류합니다(`OBSERVATION_UNAVAILABLE`). 이 창은 손실 창보다 짧아야 합니다(손실 창 − 1주기 이하로 검증): 공통 경로가 끊기면 VM 마다 마지막 수신이 최대 한 주기 어긋나므로, 손실 창을 그대로 쓰면 늦게 받은 동료가 신선해 보여 먼저 받은 VM 을 개별 장애로 오판합니다. 동료는 이번 주기 관측의 domid·execution 과 같은 실행에서 받은 신호만 셉니다(재시작한 동료의 이전 실행 신호는 증거가 아님). Zephyr 동료는 현재 binding 의 앱 메시지 중 가장 최근 것을 봅니다(기능 건강은 두 앱 중 오래된 쪽). 동료가 없는 **단독 발행자**는 VM 장애와 공통 경로 장애를 구별할 수 없어 기본은 보류이고, 채널별 정책이 `trust` 일 때만 판정합니다(`XENOVA_RECOVERY_AGENT_SOLE_PUBLISHER`·`XENOVA_RECOVERY_APP_SOLE_PUBLISHER`, 둘 다 기본 `hold`). Linux VM 이 하나뿐인 보드에서 heartbeat 손실 복구를 보이려면 **agent 만** `trust` 로 엽니다. app 을 열면 주행 VM 이 하나만 남은 동안 공통 경로가 끊길 때 마지막 주행 VM 까지 force-restart 할 수 있습니다. crashed·absent 는 이 규칙과 무관합니다 |
| 대상 불명 | capability 값이 boolean 이 아닌 템플릿의 Zephyr VM 은 앱 펌웨어인지 starter 인지 알 수 없으므로 UNKNOWN(건강 OBSERVATION_UNAVAILABLE). 신호를 받아 armed 되면 보통대로 |
| armed 기록 손상 | 원본을 이동하지 않고 `armed.json.corrupt` 보존 사본을 확보한 뒤 `history_lost=true` 본 파일로 교체합니다. 저장 실패 때도 손상 원본 또는 격리 흔적으로 Worker 재시작 시 차단을 복원합니다. capability 선언도 armed 기록도 없는 Zephyr VM은 UNKNOWN(건강 OBSERVATION_UNAVAILABLE)이고, 실제 신호를 받으면 해당 VM은 armed되어 기존 규칙으로 판정합니다. 손상은 앱 사망이나 복구 허가가 아닙니다. 파일 삭제로 차단을 해제하지 않습니다 |
| 장애 아님 | `paused`·`stopping` 은 UNKNOWN(사용자 조작·종료 진행) |

건강 스냅샷의 `HEALTHY` 는 **Xen 실행 + 생존 신호가 신선하다**는 뜻입니다. CPU·메모리·앱의 실제 작업까지 정상이라는 뜻은 아닙니다(그 근거는 아직 판정에 쓰지 않습니다, [건강 판정 계약](../../../docs/contracts/health-evidence-v1.md)).

판정을 내기 직전에는 그 VM 만 플랫폼 `get` 으로 의도와 실행 상태를 다시 읽고 **그 관측으로 다시 판정**합니다. 관측 사이에 VM 이 시작·복구됐거나 사용자가 껐으면 판정하지 않고, 건강 스냅샷도 재확인한 관측으로 냅니다. 재확인이 잠금 충돌이면 그 주기는 보류합니다.

장애 한 건(episode)에 판정(`Judgment`)은 하나입니다(계약 2.5 의 `recovery_id`). 사유가 바뀌어도(HEARTBEAT_LOST → DOMAIN_ABSENT) 다시 내지 않고 건강 스냅샷에만 반영합니다. UNKNOWN 동안은 episode 가 유지되므로 복구 재시작(도메인 사라짐 → 새 domid → 첫 신호) 중에도 같은 episode 입니다. OK 가 되면(신호 재개, 의도 변경) 닫히고 다음 장애는 새 `recovery_id` 입니다. 복구 정책(125)은 episode 가 닫힐 때까지 같은 `recovery_id` 로 재시도하고 현재 상태는 `Watcher.health()`·`open_recovery()` 로 봅니다. 관측에 실패한 주기(플랫폼 잠금 충돌 503, xl 오류)는 판정하지 않고 건너뜁니다.

건강 스냅샷은 VM 마다 `xenova/v1/recovery/health/{vm_id}`(type `vm.health`, QoS 1, retained)에 `{vm_id, ok, state, desired_state, xen_state, heartbeat_age_s, reason, manager_seq}` 로 내용이 바뀔 때만 냅니다. `state` 는 `HEALTHY`(신호 신선, `ok: true` 는 이때만) / `STARTING`(실행 중이지만 이번 부팅의 신호 전, 유예 중이라도 건강하다고 하지 않음) / `UNHEALTHY`(사유 있음, paused·stopping, 신호 끊김) / `STOPPED`(도메인 없고 의도가 running 아님) / `OBSERVATION_UNAVAILABLE`(구독 미승인 또는 승인 뒤 수신 증거 없음, armed 기록 손상·capability 값 오류로 대상 여부 불명, Worker 종료) 입니다. 내용이 같아도 `XENOVA_RECOVERY_HEALTH_REFRESH`(기본 15초)마다 다시 내고 payload 의 `lease_s`(3주기, 기본 45초)를 함께 보냅니다. **받는 쪽은 마지막 수신 뒤 `lease_s` 가 지나면 관측 불가로 봅니다.** retained 만 믿으면 Worker 가 죽어도 며칠 전 HEALTHY 가 현재로 읽히기 때문입니다. 구독 직후 브로커가 주는 retained 값(retain 플래그, 공용 클라이언트 `Message.retained`)은 임시값이라 `ok` 를 믿지 않고, Worker 가 다시 낸 실시간 값을 받은 뒤 확정합니다. 브로커는 retained 가 저장된 지 얼마나 됐는지 알려주지 않기 때문입니다. 끊긴 동안은 발행하지 않고(큐에 쌓지 않음), paho 가 반환값으로 알리는 실패도 실패로 다룹니다. `manager_seq` 는 내용이 바뀔 때만 오르고 재발행에서는 `heartbeat_age_s` 만 갱신됩니다. VM 이 등록에서 사라지면 빈 payload 로 retained 를 지우고(PUBACK 확인, 실패하면 플랫폼 관측 실패와 무관하게 다음 주기 재시도), Worker 가 정상 종료할 때는 남은 VM 전부를 `OBSERVATION_UNAVAILABLE` 로 바꿔 두며 PUBACK 까지 확인합니다(전체 5초 한도, 못 받으면 경고). `vms/{vm_id}/` 아래 토픽은 봉투 `vm_id` 와 토픽이 같아야 해서(MQTT 계약 2장) VM 이 아닌 발행자는 쓸 수 없어 복구 이벤트(MQTT-12)와 같은 `recovery/` 가지를 씁니다(MQTT-13, 계약 11.1).

설정은 `XENOVA_RECOVERY_INTERVAL`(5), `XENOVA_RECOVERY_STATE_DIR`(/run/xenova/recovery), `XENOVA_RECOVERY_INTENT_REFRESH`(60), `XENOVA_RECOVERY_HEALTH_REFRESH`(15), `XENOVA_RECOVERY_HEARTBEAT_LOST`(30), `XENOVA_RECOVERY_APP_HEARTBEAT_LOST`(15), `XENOVA_RECOVERY_BOOT_GRACE`(90), `XENOVA_RECOVERY_ZEPHYR_BOOT_GRACE`(30), `XENOVA_RECOVERY_ABSENT_GRACE`(60), `XENOVA_RECOVERY_AGENT_SOLE_PUBLISHER`·`XENOVA_RECOVERY_APP_SOLE_PUBLISHER`(hold|trust, 기본 hold), `XENOVA_RECOVERY_AGENT_PEER_FRESH`(15)·`XENOVA_RECOVERY_APP_PEER_FRESH`(5), `XENOVA_RECOVERY_BROKER_HOST/PORT/USERNAME/PASSWORD`(127.0.0.1:1883, 계정 `dom0-server`), `XENOVA_RECOVERY_LOG_LEVEL` 이며, 플랫폼·xl 은 dom0-manager 와 같은 `XENOVA_*`(`XENOVA_DRIVER=platform` 필수)입니다. 단 HTTP 전용 값(`XENOVA_HOST`·`XENOVA_PORT`·`XENOVA_SERVICE_TOKEN`·`XENOVA_CONSOLE_ENABLED`·`XENOVA_CONSOLE_LOCK_DIR`)은 읽지 않으므로, 같은 env 파일에서 콘솔을 켜도 Worker 는 콘솔 검증(토큰 필수·로컬 dom0)에 걸리지 않습니다.

Yocto의 `xenova-dom0-manager` 패키지는 HTTP 서비스와 Recovery Worker unit을 함께 설치·enable합니다.
Manager·공용 `xenova_mqtt`·Paho는 Python site-packages에서 로드하며 `/opt` 사본을 사용하지 않습니다.
Worker는 `/etc/xenova/recovery-worker.env`(root 소유, 0600)에서 broker 설정을 읽습니다.
broker의 계정 목록·ACL에는 `dom0-server`가 포함되며, 계정 비밀번호를 발급한 뒤
`XENOVA_RECOVERY_BROKER_USERNAME=dom0-server`와 `XENOVA_RECOVERY_BROKER_PASSWORD`를 해당 파일에 설정합니다.
broker 연결·필터별 SUBACK가 없으면 heartbeat 손실을 장애로 판정하지 않습니다.

HTTP 서비스의 첫 시작 전 `python3 -m dom0_manager.service_token`이 보드별 256bit 토큰을
`/etc/xenova/dom0-manager-token.env`(0600)에 생성합니다. 재시작·업데이트는 기존 토큰을 재사용하며
명시적인 `dom0-manager.env`의 `XENOVA_SERVICE_TOKEN`이 우선합니다. 토큰 값은 출력하지 않습니다.
공통 이미지에는 이 파일을 넣지 않습니다. Spring의 `DOM0_MANAGER_SERVICE_TOKEN`에는
보드 토큰과 같은 값을 비밀 설정으로 전달합니다. 토큰·broker 비밀번호를 Git이나 명령행에 넣지 않습니다.

확인 명령은 `systemctl is-enabled xenova-dom0-manager xenova-recovery-worker`,
`systemctl status xenova-recovery-worker --no-pager`입니다. HTTP `/healthz`와 `/api/v1/stats`는
Bearer 인증을 사용하며, 토큰이 없거나 다르면 401을 반환합니다.

Xen을 관측하지 않는 의도 조회가 생기기 전까지 Worker는 60초 캐시를 사용합니다.
열린 episode·판정 큐·recovery_id는 프로세스 메모리에 있으며 재시작 실행·복원은 복구 정책 엔진(125)의 책임입니다.

## 복구 정책 엔진 (S15P31E110-125)

감시기의 판정(`Judgment`)을 받아 **그 VM 만** 플랫폼 `recover_once` 로 재시작합니다(`recovery/engine.py`). 정책은 `restart` / `none` 둘뿐입니다. failover 는 없습니다.

| 규칙 | 내용 |
| --- | --- |
| 하나의 복구 | VM 마다 열린 복구(`recovery_id`)는 하나. 같은 VM 의 새 판정은 열린 복구에 흡수. `recovery_id` 는 Worker 재시작 뒤에도 `XENOVA_RECOVERY_STATE_DIR/recoveries.json` 에서 이어 감 |
| 저장 뒤 호출 | 시도(`recovery_attempt_id`·spec)를 원자적으로 저장한 뒤에만 `recover_once(execute=true)` 를 부름. 응답 유실·일시 거절(OPERATION_IN_PROGRESS·HOST_QUIESCING·BOOT_ADMISSION_REQUIRED·OBSERVATION_UNAVAILABLE)·Worker 재시작 뒤에는 `recovery-operation` 으로 같은 attempt 의 journal 을 먼저 보고, 없으면 **같은 spec 을 다시** 보냄. 새 attempt id 는 실패가 확정되고 backoff 가 끝났을 때만 |
| mode | 도메인 없음 → `start-if-absent`(view 의 `observation_revision`). Linux 실행 중 → `graceful-restart`(정상 종료 실패 뒤 다음 시도를 `force-restart` 로 올리는 것은 `XENOVA_RECOVERY_ESCALATE_TO_FORCE=1` 일 때만. 기본은 끔: 실행 중인 VM 을 파괴하는 동작이라 운영자가 명시적으로 켠다). Zephyr·crashed → `force-restart`(플랫폼 per-VM 승인 없으면 RECOVERY_MODE_UNSUPPORTED 로 최종 실패) |
| 시도 전 재확인 | 실행 슬롯을 얻은 **그 시점에** 플랫폼 `get` 으로 `REGISTERED`·`desired_state running`·`intent_applicable` 를 다시 읽고 `expected_*`(host_boot_id·generation·intent_revision·execution_id)와 deadline 을 채움. 큐에서 기다린 시간이 deadline 을 갉아먹지 않음. 아니면 취소 |
| 성공 | **플랫폼이 이 시도로 새 Xen 실행을 관측하고 새 `execution_id` 와 함께 completed 를 돌려준 것**(계약 2.5). 생존 신호(heartbeat) 복귀는 감시기의 건강 스냅샷으로만 보고하고, 돌아오지 않는다고 다시 재시작하지 않음(브로커·에이전트 문제로 멀쩡한 VM 을 또 재시작하게 됨). 시도가 완료되기 전에 감시기가 `HEALTHY` 를 내면(사용자 조작·플랫폼 쪽 재시작·신호 복귀) 더 시도하지 않고 `RECOVERED` 로 닫고 `recovery.recovered` 를 냄(completed 아님). `STOPPED` 면 `recovery.cancelled`. 이미 재시도 실패를 낸 시도 뒤(backoff 중)라면 두 이벤트는 장애 단위라 `recovery_attempt_id`·`attempt_duration_ms` 가 null. completed 인데 새 `execution_id` 가 없으면 final failed(RECONCILE_REQUIRED) |
| 결과를 모르는 시도 | 실행 중·응답 유실·일시 거절 뒤 재전달 대기·Worker 재시작 뒤 복원은 모두 EXECUTING 으로 두고 감시기의 `HEALTHY`·`STOPPED` 로 닫지 않음. 같은 attempt id 로 `recovery-operation`(journal)을 먼저 조회해 완료·실패를 받고, journal 에 없을 때만 지금 건강 상태를 본 뒤(건강하면 재시작하지 않음) 같은 spec 을 다시 보냄. 그 사이 사용자가 끄면 `cancel_requested` 로 저장해 두었다가 플랫폼이 완료했으면 완료로, 아니면 취소로 닫음 |
| 실패·재시도 | XEN_EXECUTION_FAILED·DEADLINE_EXCEEDED 등은 시도 소비 → `XENOVA_RECOVERY_BACKOFF`(5,15,30초) 뒤 새 attempt id. `XENOVA_RECOVERY_MAX_ATTEMPTS`(3) 초과면 final failed. force 승격(켰을 때만)은 플랫폼이 "정상 종료 실패"를 명시한 코드(RECOVERY_MODE_UNSUPPORTED·DEADLINE_EXCEEDED·정지 대기 만료의 XEN_EXECUTION_FAILED)에서만. 끈 상태의 RECOVERY_MODE_UNSUPPORTED 는 final failed. TARGET_CHANGED·INTENT_MISMATCH 는 재시도하지 않고 감시기의 다음 판단(HEALTHY/STOPPED)을 `XENOVA_RECOVERY_VERIFY_TIMEOUT`(150초)까지 기다린 뒤에야 새 시도. RECONCILE_REQUIRED·IDEMPOTENCY_CONFLICT·정체 모를 예외는 운영자 몫이라 즉시 final failed(force 로 올리지 않음). VM_NOT_FOUND·사용자 stop 은 취소 |
| 단일 실행 | 시작할 때 `XENOVA_RECOVERY_STATE_DIR/worker.lock` 에 non-blocking flock. 다른 Worker 가 쥐고 있으면 종료 코드 3 으로 끝나고 유닛의 `RestartPreventExitStatus=3` 이 재시작을 멈춤 |
| 실행 스레드 | `recover_once` 는 플랫폼 잠금 안에서 deadline(`XENOVA_RECOVERY_ATTEMPT_DEADLINE` 110초, 상한 120)까지 막힐 수 있어 엔진의 실행 스레드(하나)에서 부름. 감시 루프·건강 발행은 막히지 않고, 관측이 실패한 주기에도 끝난 결과는 회수(`collect`) |
| fail-safe | `recoveries.json`은 상태별 불변조건(EXECUTING의 원 attempt·spec·mode·시작 시각 등)까지 검증합니다. 손상 원본의 `.corrupt` 사본을 먼저 확보하고 차단 기록을 저장하며, 그 저장이 실패해도 원본/잔존 증거를 정상 최초 부재로 해석하지 않습니다. **이력 상실 시 복구 실행을 차단**하고 판정·건강 관측은 유지합니다. `XENOVA_RECOVERY_POLICY=none`도 실행하지 않습니다. pending 사건이 없는 닫힌 복구는 최근 20건만 보존합니다 |
| 이벤트 | 계약 2.5 `recovery.started/completed/recovered/cancelled/failed` 는 상태 전이와 **함께 레코드에 적어 `recoveries.json` 에 저장(event_id 고정)** 한 뒤에만 outbox `XENOVA_RECOVERY_STATE_DIR/recovery-events.jsonl`(최근 2,000건)로 옮기고 MQTT-12 `xenova/v1/recovery/events` 로 발행. 상태 저장에 실패하면 이벤트를 내지 않고 다음 주기에 다시 저장. outbox 에 못 옮긴 이벤트는 레코드에 남아 Worker 재시작 뒤에도 같은 event_id 로 저장·발행되며(Spring 은 event_id 로 거름) 그동안 `recovery worker degraded` 를 ERROR 로 남김. `error`(failed 의 실패 사유, cancelled 의 취소 사유)의 `code` 는 Worker 결과 코드, `platform_code` 는 플랫폼 원본 코드만. Spring 은 단절 뒤 `GET /api/v1/recovery/events?after_event_id=&limit=`(4.7)로 채움: 읽을 때 계약 스키마로 검증하고, 커서가 보관 범위 밖이거나 커서 뒤에 손상된 줄이 있으면 `gap: true`(손상 자리는 compaction 뒤에도 tombstone 으로 남김), 빈 페이지면 `next_after_event_id: null`. HTTP 서비스는 같은 디렉터리(`XENOVA_RECOVERY_STATE_DIR`)를 읽음. `/run` 은 tmpfs 라 host 재부팅이면 비고 그때는 `gap: true` |

아직 없는 것: 실보드 검증(플랫폼 `force_restart_vm_ids` 승인과 Mosquitto 배포 뒤), `probe` 로 Xen 관측 불가를 가리는 것, Worker 자체 상태(degraded)를 MQTT·HTTP 로 내보내는 것(계약 추가 필요, 지금은 로그).

### Worker 상태 파일과 사건 기록

기본 저장 위치는 `/run/xenova/recovery`입니다. 같은 host의 Worker 재시작을 지원하며,
host 재부팅 뒤의 tmpfs 초기화 정책은 별개입니다. `recoveries.json`의 상태와
`pending_events`는 기존과 같이 한 JSON의 원자 교체 단위입니다.

| 본 파일과 흔적 | 해석·처리 |
|---|---|
| 유효 schema, `history_lost=false` | 본 파일의 원 ID·spec·deadline·예산·pending을 복원합니다. 오래된 `.corrupt`/미완료 `.tmp`를 정상 본 파일 대신 읽지 않습니다 |
| 유효 schema, `history_lost=true` | 이력 상실 차단을 복원합니다. 정상 빈 파일로 취급하지 않습니다 |
| 본 파일·`.corrupt`·`.tmp` 모두 실제 부재 | 최초 빈 상태를 지원합니다 |
| 본 파일 부재 + `.corrupt` 또는 `.tmp` 존재 | 이전 구현의 격리 중단 또는 미완료 저장 흔적입니다. 차단하고 증거를 보존하며 임시 파일을 승격하지 않습니다 |
| 본 파일 손상 | 같은 디렉터리에 hard link로 `.corrupt`를 확보한 뒤 차단 파일을 저장합니다. 공간 부족·격리 실패·다른 기존 `.corrupt`와의 충돌이면 원본을 유지하고 저장 실패를 반환합니다. 같은 inode 사본은 이전 실패의 재시도로 인정합니다 |
| 읽기·경로 조회 오류 | 정상 부재로 숨기지 않습니다. 읽지 못한 원본이나 격리 흔적을 빈 파일로 덮어쓰지 않습니다 |

두 저장소 모두 `history_lost`는 같은 객체의 반복 load로 해제되지 않습니다.
`RecoveryStore.save()`와 내부 `ArmingStore.save()`는 로컬 저장 확인 여부를 bool로 반환합니다.
Watcher는 armed 저장 실패를 받아 미선언·미관측 대상의 관측 불가 보호를 유지합니다.
ArmingStore도 file flush/fsync 뒤 교체하지만, 이를 영구 저장 모드나 SD 전원 상실 보장으로
해석하지 않습니다. 원본·격리 파일·journal 삭제나 자동 reset은 지원 수습 절차가 아닙니다.

`EventLog.append=False`는 **로컬 기록 성공 미확정**입니다. 미기록·부분 기록·완전한 줄 뒤
동기화 실패 모두 가능하므로 Engine은 pending의 **원 event_id와 내용**을 유지합니다.
재시도는 raw 중복 줄을 만들 수 있지만 조회는 첫 유효 사건만 반환합니다. 개행 없이 잘린
줄에는 경계 개행을 더해 다음 사건이 붙지 않게 하고, 잘린 UTF-8/JSON은 gap으로 처리합니다.
손상 위치의 tombstone·cursor·compaction 계약은 유지합니다.

실제 sink는 로컬 EventLog 기록 확인 뒤 QoS1·retain=false·wait_s=0으로 MQTT에 제출합니다.
로컬 HTTP 재보충 원천이 있으면 MQTT 제출 예외에도 True를 반환합니다. 이는 PUBACK이나
최종 소비자 반영 확인이 아니며, 영구 MQTT 재전송 큐를 제공하지 않습니다.

## 공통 주행 실행의 Standby 재합류

기존 Worker/Engine 완료 뒤 `XENOVA_RECOVERY_DRIVE_CONFIG`를 지정하면 실행별
Bootstrap 적용 receipt·현재 실행 바인딩·앱 준비를 별도로 연결한다. 예제는
`deploy/drive-rejoin.example.json`이다. root 전용 설정과 VM별 v1/v2 인증 파일이 필요하다.
플랫폼의 COMPLETED는 새 Xen 실행 관측이라는 뜻을 유지하며 READY나 Active를 뜻하지 않는다.
입력 부족/준비 미완료만으로 정상 VM을 반복 재시작하지 않는다. 실행 신뢰·수명·설치와
소비자 이행은 [공통 주행 안내](../../apps/drive/README.md#실행-바인딩과-자동-복구)를 따른다.

## 테스트 실행

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
| `OperationNotFound` | 404 | 요청 키로 원 제어 작업을 찾지 못함(조회). 검색 부재이며 미수락 증거가 아님 |
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
- desired_state의 원본은 하위 플랫폼이다. 복구 중 stop 의 수락·응답 전달은 키 있는 stop 으로 연결했다(S15P31E110-305). Spring 의 키 보존·작업 상태 처리와 실보드 종단 수락은 남아 있다.
- 템플릿 ID 명명이 Spring 설정과 플랫폼 카탈로그에서 다릅니다. 변환 규칙은 세 파트가 함께 정합니다.

create의 `name`은 Spring에서 받은 표시 이름(1~63자)을 그대로 전달합니다. 한글·공백을 허용하고 제어·줄 구분 문자와 앞뒤 공백은 거부합니다. `PlatformProvisioner`는 이를 플랫폼 `spec["name"]`에 넘기며 템플릿명·UUID로 대체하지 않습니다. 내부 실행 이름과 경로는 기존 UUID 기반 규칙을 사용합니다. 등록된 VM 재시작·재전달은 표시 이름을 갱신하지 않습니다.
