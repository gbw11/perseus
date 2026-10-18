# dom0-manager

dom0에서 도는 Python 제어 서비스입니다. Spring 관리 서버가 호출하는 내부 HTTP API(`127.0.0.1:8081`)를 열고, 드라이버(Mock/Xen)를 통해 Xen 도메인을 조회·제어합니다.

```text
프론트 → Spring :8080 → dom0-manager :8081 → Driver → xl (Xen) | Mock
                                   └ Provisioner (템플릿 → 인스턴스 디스크, P2 연동 예정)
```

- 계약 문서: [docs/contracts/vm-api-contract-v1.md](../../docs/contracts/vm-api-contract-v1.md)
- OpenAPI 원본: [contracts/dom0-manager-openapi.yaml](../../contracts/dom0-manager-openapi.yaml). `tests/test_contract.py`가 서비스가 내는 스키마와 비교합니다.

## 실행 (Mock 모드)

보드·Xen 없이 바로 띄울 수 있습니다. 수환·송하 연동용입니다.

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
| `PERSEUS_DRIVER` | `mock` | `mock` 또는 `xen` |
| `PERSEUS_HOST` / `PERSEUS_PORT` | `127.0.0.1` / `8081` | 바인드 주소. dom0 밖으로 열지 않습니다 |
| `PERSEUS_XL_QUERY_TIMEOUT` | `5` | 조회용 xl 호출 한도(초). Spring 조회 대기 6초 안 |
| `PERSEUS_XL_COMMAND_TIMEOUT` | `30` | 명령용 xl 호출 한도(초) |
| `PERSEUS_SHUTDOWN_GRACE` | `25` | 정상 종료 대기 후 강제 전환까지(초) |
| `PERSEUS_HOST_CPUS` | `4` | vcpus 상한 기본값. `xl info`의 nr_cpus가 있으면 그 값 우선 |
| `PERSEUS_INSTANCE_ROOT` | `/srv/perseus/instances` | 인스턴스 루트 (플랫폼 도구와 동일) |
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
    xen.py         XenDriver: 조회 골격 (S15P31E110-36), 명령은 115에서
tests/
  test_api_flow.py     계약 7장 상태 전이 (Mock)
  test_validation.py   400 정규화, 추가 필드·UUID·범위
  test_contract.py     OpenAPI yaml 과 비교
  test_xl.py           xl 파싱, XenDriver 조회, FakeRunner
```

## 드라이버 인터페이스 (S15P31E110-31)

`drivers/base.py`의 `Driver`를 Xen과 Mock이 똑같이 구현합니다. 상위 계층은 드라이버 종류를 모릅니다.

| 메서드 | v1 계약 | 의미 |
| --- | --- | --- |
| `list_domains()` | O | Xen에 존재하는 도메인. Domain-0 제외 |
| `get_domain(vm_id)` / `has_domain(vm_id)` | O | 단건 조회. 없으면 `VMNotFound` |
| `create(spec)` | O | Provisioner가 준 경로로 도메인 생성. 이미 있으면 `VMExists` |
| `shutdown(vm_id, grace_s)` | O | 정상 종료 요청 → grace 초과 시 강제. 디스크 보존. 멱등 |
| `destroy(vm_id)` | O | 강제 종료. 디스크는 Provisioner가. 멱등 |
| `host_info()` | O | free_memory, total_memory, nr_cpus (`xl info`) |
| `health()` | O | 하이퍼바이저 응답 여부 → `/healthz` 503 판정 |
| `reboot`, `set_vcpus`, `set_memory`, `pin_vcpu`, `set_sched_weight`, `get_stats` | v2 | 208·101·112에서. 기본 구현은 `NotImplementedError` |

`Provisioner`는 `ensure_instance / instance_exists / delete_instance` 세 개입니다. 디스크 생성·삭제 코드는 여기 없습니다. P2(플랫폼)의 인스턴스 생성 기능과 연결할 때 구현하며, 그 전까지 Xen 모드의 `ensure_instance`는 501을 냅니다.

상태 매핑은 계약 3.2장을 따릅니다. `b`(blocked)는 I/O 대기 중인 정상 상태이고, 플래그가 없는 `------`도 running으로 봅니다. 그 외 모르는 문자는 조용히 running으로 넘기지 않고 `DriverError`를 냅니다.

## Xen 모드 현황

`PERSEUS_DRIVER=xen`에서 지금 되는 것은 `GET /healthz`, `GET /api/v1/vms`, `GET /api/v1/vms/{vm_id}`입니다. `xl list`로 상태를, `xl list -l`의 `config.c_info.uuid`로 vm_id를 읽습니다(플랫폼 도구와 같은 방식). 생성·정지·삭제는 501을 내며 S15P31E110-115에서 구현합니다.

## 테스트

```bash
cd services/dom0-manager
python -m pytest
```

## 아직 정하지 않은 것

- xl 실패·타임아웃의 응답 코드. 계약 문서에 없어 임시로 500 `DriverError`를 냅니다 (OpenAPI에는 없음).
- `/healthz` 본문은 `{"status": "ok" | "xen_unavailable"}`로 임시 정의.
- `xl list`의 `------`(플래그 없음)를 running으로 보는 규칙은 계약 3.2장에 없습니다. 문서에 추가 제안.
- 인스턴스 디스크·MAC·hostname 생성 주체(P2 합의). 정해지면 Provisioner 구현으로 연결합니다.
