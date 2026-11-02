# VM 자격 공급·신호 프로필·등록 통지

2026-11-02 제공자 계약이다. 일반 생성과 factory 모두 영구 VM UUID를 사용한다.
아래 Python/CLI는 하위 제공물이다. 새 HTTP 관리 route를 제공한다는 뜻은 아니다.
Spring의 등록-only route는 **계약안·HTTP fixture 수락 상태**이며 담당자 합의와 제품 구현이 남아 있다.

## 자격의 원본과 전달

계정·암호·ACL의 원본은 기존 `xenova_broker`의 `/srv/xenova/mqtt/current` 세대다.
`xenova_provisioning.ensure(data, record)`가 검증된 `provisioning_profile.channels`에 필요한
기존 UUID 계정 또는 `telemetry-UUID` 계정만 확보한다. 새 암호 생성기는 만들지 않았다.
Linux Agent는 기존 UUID 계정으로 v1과 자기 VM의 v2를 발행한다. Zephyr v2 Publisher와
drive 연결은 기존 별도 계정을 쓴다. 같은 인증 자격을 쓰는 동시 MQTT 연결은 서로 다른 Client ID를 사용한다.

| 사건 | 실제 호출·저장·소비 |
|---|---|
| Debian/Ubuntu 일반 create | 검증 복사 1회 안의 `prepare_instance.make_instance` → `apply_linux` → private root `/etc/xenova/agent.env`. 실제 `AgentSettings.from_env`·Agent unit이 소비 |
| factory Linux | seed에는 비밀 없음. `FirstBoot.register`의 영구 UUID 계획 뒤 private root에 위 env와 `platform-identity.json` 공급. guest identity unit이 host UUID로 instance-id를 확정한 뒤 Agent 시작 |
| Zephyr 일반 create | 기존 대상 AssetLease/lifecycle slot 아래 private firmware 준비 → `ensure` → 짧은 등록 게시. 해시 동안 공통 관리 잠금을 반환 |
| factory Zephyr | `FirstBoot.register`의 같은 UUID 계획 뒤 `ensure`. A/B는 같은 firmware·서로 다른 UUID와 암호 |
| Zephyr start/autostart/reboot/recover | `Platform.start`의 실제 실행 binding 뒤 `bootstrap_execution` → 기존 Bootstrap codec·XenStore/receipt. 영구 암호 유지, 새 execution의 boot/publisher/app 신원만 원 API로 발급 |
| 명시 재공급 | `xenova-vm provision UUID --execute`. Linux는 정지 의도·현재 미사용·대상 lease guard 후 offline mount. Zephyr는 원 자격 재사용, running intent이면 현재 실행에 Store 적용. stopped intent이면 local 준비만 하며 VM을 시작하지 않음 |
| 읽기 | `xenova-vm provisioning UUID --json`. 자격 발급/reload/POST/guest 변경 없음 |

`provision UUID`는 계획이고 `--execute`가 실제 공급이다. `--json`은 출력만 바꾼다.
명시 timeout/budget은 적용하며 잘못된 옵션은 거절한다. 다음 호출은 기존 full create와 함께 쓸 수 있다.

```sh
xenova-vm provisioning 850e8400-e29b-41d4-a716-446655440001 --json
xenova-vm provision 850e8400-e29b-41d4-a716-446655440001
xenova-vm provision 850e8400-e29b-41d4-a716-446655440001 --execute --timeout-ms 10000 --json
```

Worker/Bootstrap의 root 로컬 호출자는 설치된 `/usr/lib/xenova`에서 다음을 사용한다.

```python
from xenova_provisioning import credential_file
drive_credentials = credential_file(data_root, vm_id, 'drive')  # Path, 발급·reload 없음
telemetry_credentials = credential_file(data_root, vm_id, 'telemetry')
# ProductionBootstrap의 config['credentials'][vm_id][channel]에 경로를 연결한다.
# 파일 본문을 로그/HTTP/원 journal에 복제하지 않는다.
```

private adapter 파일은 `/srv/xenova/provisioning/UUID/`의 root 0700 아래 0600이다.
broker가 원본이며 adapter 사본은 실행 신원/건강의 원본이 아니다. 완성 create replay는 공급을 다시 실행하지 않는다.
이미 계정이 있었다면 암호·해시·세대를 재사용한다. 정상 반복 확인은 reload하지 않는다.
직전 reload 미확정은 명시 provision에서만 다시 요청할 수 있다. `reload_requested`는 인증 성공이 아니다.
계정의 명시 제거는 revoked fence를 남겨 자동 ensure가 부활시키지 못하게 한다. 복원은 기존 broker의 명시 add 절차다.
공급 API의 `CREDENTIAL_REVOKED`와 `CREDENTIAL_SOURCE_UNAVAILABLE`은 수습 방법이 다르다.
후자는 broker의 비밀 없는 원인 사유를 보존하고 손상/원문 소실을 재발급으로 처리하지 않는다.
평문 staging/해시 완료 뒤 중단은 원 암호로 재개한다. 손상·원문 소실·혼합 형식은 정상 부재나 새 암호로 보정하지 않는다.
Zephyr create의 완성 private firmware는 명시한 같은 create로 대상 lease·원 명세·작은 firmware를
다시 검증한 뒤 공급/게시만 재개할 수 있다. 새 firmware 복사를 만들지 않는다. 불완전/손상 자산은 삭제·재복사하지 않는다.
Linux 실패 private 디스크의 기존 명시 수습/폐기·예약 반환 계약은 이 동작과 별개로 유지한다.

공개 supply는 `credential_state`, `broker_application`, `guest_application`, `authentication`을 나눈다.
`stored`/`next_start`/`reload_requested`/`private-root-configured`/`store-published`는 각 단계의 사실이다.
Store receipt의 `applied`도 Store 게시이며 guest 소비·MQTT 인증·앱 READY 증명이 아니다.
기존 Worker `receipt_dir=/srv/xenova/drive-rejoin`의 `UUID/execution/{telemetry,drive}.json`과
같은 receipt/lock을 사용한다. `receipt_directory(data, vm_id, execution_id)`로 경로를 확인한다.
별도 디렉터리에서 원 boot/app 신원을 다시 발급하지 않는다. 예전 설치의 임의 경로는 교체 전에 대조해야 한다.
인증 관측이 없으면 `authentication=unobserved`다. broker offline과 필수 local 저장 실패를 합치지 않는다.
후자는 등록 전 원 준비를 실패로 남기며, 이미 등록된 VM의 공급 실패는 등록·원 성공을 지우지 않는다.
stop/retire/purge가 자동 계정 폐기·암호 회전을 의미하지 않는다.

## 별도 world/Jetson 인증 영역

Pi 계정으로 Jetson 인증을 가정하지 않는다. Jetson broker 담당자가 해당 UUID의 read-only world
접근을 승인하고 다음 private 파일을 전달한다. 비밀번호 예시는 fixture 설명이며 운영값을 Git에 넣지 않는다.

```json
{"vm_id":"850e8400-e29b-41d4-a716-446655440001","world":{"broker_ipv4":"192.0.2.2","port":1883,"username":"850e8400-e29b-41d4-a716-446655440001","password":"fixture-only","profile":"lattice-lab-1","trusted_internal_plaintext":true}}
```

root 0600 파일만 `xenova-world-install --vm-id UUID --file /private/approved-world.json`로 설치한다.
등록된 정지 Zephyr와 기존 작업/의도/미사용 guard가 필요하다. 같은 codec의 메모리 검증 후 기존 drive
설정의 world 부분만 원자 교체한다. Pi 암호·broker 계정·VM/디스크를 만들지 않는다.
새 실행 전에 설치하며 기존 receipt는 고치지 않는다. 현재 실행 변경이 필요하면 정식 stop/start 문맥을 사용한다.
world 미제공이면 AUTO 입력은 unavailable이고 MANUAL/Pi 관리는 별개다.
이 평문 프로필은 신뢰된 내부망에 한정하며 외부 TLS 요구를 무시하는 fallback이 아니다.

## 선언과 현재 실행

manifest의 검증된 `provisioning_profile`이 공통 원천이다. 구형 자산은 실제 Agent 설치 receipt,
알려진 앱 source와 검증한 `.config`에 한해 해석한다. 미선언은 null이며 오류를 false로 바꾸지 않는다.
`channels`는 이번 공급 선택, `signals`는 제공 기대, `apps`는 stateful/stateless·Standby progress 계약이다.
broker ACL은 별도 원본이며 network 지원이나 앱 자기 보고만으로 출력 권한을 주지 않는다.

`provisioning` 응답의 `initial_template`은 최초 출처, `profile`은 다음 기동 선택,
`current_profile`은 현재 host 실행의 검증된 loader capture다. 후자는 `captured-launch`,
`observation=not-refreshed`이며 이번 조회가 Xen/Store/Agent를 관측했다는 뜻이 아니다.
구형 실행에 capture가 없으면 null이다. 순수 `intents`의 읽기 전용·Xen 없음 계약은 그대로다.
new incarnation은 새 capture/binding을 확인한다. firmware 교체가 과거 실행의 선언을 바꾸지 않는다.

Starter의 `app_heartbeat=false`, 미선언 null, 실제 drive의 true는 서로 다르다.
true로부터 신호 수신·정상 계산·READY·출력 허가를 추론하지 않는다. warm Standby의 progress idle은 정상이다.
현재 lattice AUTO는 stateful, MANUAL은 stateless이며 미선언을 stateless로 승인하지 않는다.

## 등록 metadata와 FastAPI 등록 통지

### 읽기 전용 하위 API

`from xenova_management import Manager` 뒤 `Manager().call('registrations', spec)`를 호출한다.
`spec={}`는 `data.registrations` 배열, `spec={'vm_id':UUID}`는 `data.registration` 단건이다.
두 호출 모두 선택 `timeout_ms=1..15000`과 별도 `budget_ms`를 받으며 execute·원 key는 받지 않는다.
CLI는 `xenova-vm registrations [UUID] --json`이다. `--execute`는 거절한다.

현재 REGISTERED인 일반/factory VM을 실행 여부·domid·autostart·의도와 무관하게 반환한다.
RETIRED/PURGED와 숨은 미게시 자산은 제외하고 읽기 실패·손상·중복 UUID를 빈 목록으로 바꾸지 않는다.
기존 metadata reader의 대상 집합·tombstone·원 create 완료·현재 구성 재대조를 최대 두 회차로 수행한다.
Xen/Store/Agent/MQTT/네트워크·token·intent·catalog·관측 cache·대용량 payload는 읽거나 쓰지 않는다.

반환은 항상 `vm_id, name, guest_kind, template_id, template_version, vcpus, memory_mib, assigned_ipv4`의 여덟 key다.
name은 저장 display_name 우선, 없으면 내부 name이다. 이름 중복을 보정/숨김/dedup하지 않는다.
자원은 현재 저장 구성, template은 생성 때 보존한 template_id(구형은 당시 template), 버전은 없으면 null이다.
IPv4는 OS와 무관하게 저장 예약값 또는 null이며 실제 접속·Agent 주소가 아니다.
`intents`, runtime `list/get/status`의 의미는 바꾸지 않는다.

### FastAPI 조회·수동 재신청

- `GET /api/v1/vm-registrations`: 200과 위 여덟 필드의 **배열 자체**. nullable key도 유지한다.
- `POST /api/v1/vm-registrations/{vm_id}/notify`: UUID만 받고 현재 등록을 조회해 Spring에 한 번 송신한다.
- 두 API 모두 기존 `XENOVA_SERVICE_TOKEN`의 Bearer 인증이 필요하다. 본문·query URL/token/등록자료는 받지 않는다.
- FastAPI `api/registrations.py`→`PlatformDriver.registrations`→요청별 `Manager.fork()`→공개 action 경로다.
  CLI subprocess·raw journal·선행 live list/get·상위 공용 장기 mutex를 사용하지 않는다.

```sh
# 토큰은 승인된 비밀 공급 경로로 받은 환경 값이다. 예제에 실제 값을 기록하지 않는다.
curl --fail-with-body -H "Authorization: Bearer $DOM0_SERVICE_TOKEN" \
  http://127.0.0.1:8081/api/v1/vm-registrations
curl --fail-with-body -X POST -H "Authorization: Bearer $DOM0_SERVICE_TOKEN" \
  -H 'X-Request-Budget-Ms: 10000' \
  http://127.0.0.1:8081/api/v1/vm-registrations/850e8400-e29b-41d4-a716-446655440001/notify
```

POST 응답은 `vm_id, request_id, state, confirmed`와 가능한 `http_status, upstream_code, cause_type`다.
FastAPI 200은 Spring의 200/201 + 같은 UUID의 `registration=confirmed`가 모두 맞는 경우다.
Spring 400/401/403/409/503은 FastAPI 502의 `state=rejected`, 원 `http_status`와 안전한 code로 구분한다.
FastAPI 자체 인증 실패는 401이고 Spring에는 송신하지 않는다. redirect/무효 본문/다른 UUID/접수는 502 미확정이다.
응답 유실·원 호출 기한 초과는 504 미확인, 연결/설정/포화는 503이며 자동 retry·후속 GET·동기화는 없다.
UUID 미등록·퇴역·삭제는 하위 오류의 기존 HTTP 매핑이며 복원/생성/시작하지 않는다.

### Spring wire와 확인 범위

사용자의 Spring 구현 완료 전달과 `origin/backend` `800d584`의 실제 DTO/controller/auth/service 및
목록 동기화 코드를 대조했다. Spring 코드는 수정하지 않는다. 제품 실행·DB·운영 수락은 별도다.
Spring 전용 `POST /api/v1/vms/registrations`에 아래 **전체 envelope**를 보낸다. 예시 UUID/값은 fixture다.

```json
{"schema_version":"1.0","ok":true,"request_id":"0c6e71a6-9f82-41f4-b282-749bbe521aa2","operation_id":null,"replayed":false,"data":{"plan":true,"execute_required":true,"registration":{"schema_version":"1.0","vm_id":"850e8400-e29b-41d4-a716-446655440001","name":"debian-fixture","guest_kind":"linux","template_id":"debian-arm64-fixture","template_version":"1.0.2","vcpus":2,"memory_mib":512,"assigned_ipv4":"192.168.77.30"}},"error":null}
```

plan/execute_required는 합의된 wire 요소이며 읽기 API의 실행 요구나 DB 확정이 아니다.
201(신규)/200(동일 UUID)의 `{"vm_id":"…","registration":"confirmed"}`를 검증한다.
단건은 기존 행을 덮지 않는다. 관리자 `POST /api/v1/vms/synchronizations`가 이 GET을 읽어
지정 필드를 갱신하는 별도 기능이다. create/start 역호출·실패 fallback으로 사용하지 않는다.
Spring의 이름 충돌·퇴역 UUID 거절은 원 오류로 돌려준다. 첨부 GET 문서의 이름 유일성/Zephyr null 문구보다
사용자의 최신 이름·IP 무보정 결정이 우선하며 Spring 제약을 피하려고 데이터를 바꾸지 않는다.

### 설정·한도·자동 기회

Spring URL `XENOVA_REGISTRATION_URL`과 `VM_REGISTRATION_SERVICE_TOKEN`은 FastAPI만 읽는다.
root 0600 `/etc/xenova/spring-registration.env`는 선택 EnvironmentFile이고 활성 파일/비밀은 패키지가 만들지 않는다.
비활성 예시는 `/usr/share/xenova/spring-registration.env.example`다. URL은 HTTPS이며 numeric loopback HTTP만 격리용으로 허용한다.
통지 미설정/손상은 이 기능만 제한한다. GET과 일반 VM 관리는 Spring 설정과 독립이다.
Spring token을 로컬 API token과 복사·공유하지 않는다.

기본 HTTP 송신 상한 5초(설정 최대 15초)는 전송 자원 예산이다. 요청 헤더가 있으면
인증/조회/송신의 남은 예산 안에서 더 짧게 사용한다. 헤더 없는 notify 전체는 15초이며 단계마다 기한을 재발급하지 않는다.
worker 공통 flock slot 4개, 전용 HTTP 자식과 앱 shutdown의 kill/wait를 사용한다.
DNS/응답이 느려도 event loop·Manager lease에서 기다리지 않는다. 포화는 미송신 거절이며 큐·daemon·자동 retry는 없다.
예상 UUID 형식 거절은 400 사유를 유지하고 `app.state.registration_validation`의 포화 계수/최초 512자 진단만 남긴다.
async 경계에서 느린 경고 sink·원 본문 수집을 기다리지 않는다. 내부 결함·다른 오류 종류/최초 원인은 축소하지 않는다.
커널/저장장치 전체 장애에서의 hard deadline 보장은 아니다.

자동 기회는 기존 create 완료 journal `registration_notice=consumed`, factory complete의 `registration_notices`가 소유한다.
실제 송신 전 소비를 내구 확정하며, 이미 소비된 기회·과거 결과는 그대로 읽는다. 중단 직후 송신 0회도 가능하다.
공통/대상 FD와 lease 반환 뒤 `xenova_registration_hook.automatic`이 로컬 FastAPI에만 1회 요청한다.
factory는 first-boot main의 잠금 반환 후 별도 유한 helper를 띄운다. 최대 15초 안에서 `/openapi.json` 준비만 확인한 뒤 POST 1회다.
등록/원 응답/autostart는 이를 기다리지 않는다. POST 뒤 조회·retry·다음 부팅 보충은 없다.
helper는 `/etc/xenova/dom0-manager.env`의 bind 주소/port와 기존 API token 공급만 사용한다.
token의 unit 우선순위(manager.env가 token.env를 override)를 따르며 Spring 전용 파일은 읽지 않는다.
API 프로세스 안의 create에서도 helper에 Spring token/URL이나 다른 서비스 자격을 환경으로 넘기지 않는다.
helper도 4 slot으로 제한하며 결과는 기존 root-private `registration-notices/UUID.json`에 부가 기록한다.
launch/기록 실패·start 실패·API 부재가 로컬 등록을 rollback하지 않는다.

구형 `registration-notify` 명령은 계획/실행/JSON/별칭까지 제거했다. 하위 직접 Spring sender와
`registration-notification.json`·token JSON·설치 예시도 제거했다. 수동 요청은 위 인증된 curl이다.
과거 결과/검증 이력은 보존한다. 설치 시 옛 프로세스 drain·기존 파일의 운영 정리는 [배포 절차](../platform/deployment-readiness.md#registration-api)를 따른다.

## 재현과 제공 수준

실제 제품 시험은 `platform/tests/test_xenova_provisioning.py`, `test_xenova_registration.py`,
`test_xenova_signal_profiles.py`다. native hash·격리 broker·HTTP socket/process·실제 Agent parser를 사용한다.
mount/guest OS/Store 및 Spring DB는 명시 fixture다. 실제 ARM 번들을 지정하는 factory 시험은
`XENOVA_ACTUAL_DRIVE_TEMPLATE=/absolute/drive-template`로 실행한다. 대체 firmware를 만들지 않는다.
명령/후보·package·원자료는 [검증 보고](../verification/recovery-scenario-closure-20261030.md#vm-provisioning-integration)에 연결한다.
실제 제품 상위 연결·새 Linux rootfs 생성·운영 설치·실보드 인증/앱/차량·7번은 별도다.
