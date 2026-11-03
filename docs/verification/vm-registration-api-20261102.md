# VM 등록 명세 조회·FastAPI 통지 연결

## 후보와 범위

- 작업 브랜치: `feat/platform/vm-registration-api`.
- 기준 HEAD: `5a9c8398f5796f3127a6cc4388283ee39e77cb27` (`origin/platform`).
- 별도 managed worktree에서 작업한다. 원 작업트리의 planner/지도 미커밋 변경은 옮기거나 수정하지 않는다.
- 선행 생성/factory 조사, CLI 정합화, VM 공급 검증을 재사용한다. 이번 직접 수정은 등록 조회·통지의 하위/FastAPI와 관련 시험·포장·문서다.
- Spring 확인 후보: `origin/backend`의 `800d584`. 등록 `5a99857`, 동기화 `0565b5d`와 bean/auth 후속 수정이 포함돼 있다. 제품 소스는 수정하지 않는다.
- 운영·실데이터·실제 토큰·전체 이미지/firmware·7번·commit/push는 실행하지 않는다.

부팅 통지의 현재 구현·설치 근거는 마지막 **FastAPI 준비 이후 부팅 통지** 절을 따른다.
앞선 15초 준비 polling과 pre-merge 포장 결과는 그 후보의 이력으로 보존한다.

## 계약 대조와 구현 순서

1. 기존 registry/lifecycle reader로 읽기 전용 `registrations` 단건/목록을 제공한다. 표시 이름이 같은 UUID들을 유지하고 저장 IPv4를 OS별로 보정하지 않는다.
2. 실제 FastAPI GET 배열·인증된 UUID notify POST를 연결한다. Spring 설정/토큰·전체 envelope 송신·응답 판정은 FastAPI가 소유한다.
3. 기존 자동 기회 기록은 보존하고 등록 완료 후 로컬 FastAPI만 최대 한 번 호출한다. factory의 준비 확인은 부팅과 독립된 유한 단계다.
4. 구형 하위 `registration-notify` CLI·직접 Spring sender·설정/설치물을 제거한다. 과거 검증·소비된 기회·원 결과는 보존한다.
5. R01~R16의 정상/오류/회복·실제 프로세스 HTTP·비용·부분 패키지와 담당자 현재 안내를 같은 후보에 연결한다.

Spring은 전체 `data.registration` envelope와 service token을 소비한다. 같은 UUID의 POST는 원 행을 덮지 않고, 관리자 목록 동기화는 지정한 등록 필드를 갱신하는 별도 기능이다. Spring의 이름 충돌은 원 오류로 전달하며 플랫폼의 이름 변경·숨김·dedup으로 우회하지 않는다.

## 실제 제공 호출과 책임

```sh
xenova-vm registrations --json
xenova-vm registrations 850e8400-e29b-41d4-a716-446655440001 --json
curl --fail-with-body -H "Authorization: Bearer $DOM0_SERVICE_TOKEN" \
  http://127.0.0.1:8081/api/v1/vm-registrations
curl --fail-with-body -X POST -H "Authorization: Bearer $DOM0_SERVICE_TOKEN" \
  http://127.0.0.1:8081/api/v1/vm-registrations/850e8400-e29b-41d4-a716-446655440001/notify
```

UUID/주소는 fixture 예시다. 실제 토큰은 승인된 별도 비밀 공급 경로를 사용한다.
공개 Python은 `from xenova_management import Manager` → `Manager().call('registrations', {})`다.
`data.registrations` 배열 또는 UUID spec의 `data.registration` 단건이다. execute·원 key·lifecycle 필터는 거절한다.
FastAPI GET은 Manager envelope를 제거한 여덟 필드 배열이고 POST는 UUID만 받아 현재 명세를 다시 읽는다.
기존 `/vms`의 domain 조회·full create 201/실행 중 동일 POST 409는 유지한다.

| 흐름 | 실제 제품 위치 | 실행·저장 경계 |
|---|---|---|
| 현재 등록 조회 | `xenova_management.ReadContext/_read_states/_query`, `xenova_registration.registration` | 기존 registry/tombstone/원 create 완료와 현재 구성을 최대 두 회차 대조. intent/host/Xen/Store/자격/HTTP/cache 게시 없음 |
| CLI | `xenova_management_cli.OPTIONS/_validate/_spec/main` | 출력 형식과 실행 의미 분리. `registrations [UUID]` 읽기 전용, 옛 notify/별칭은 Manager 호출 전 거절 |
| FastAPI GET·POST | `dom0_manager.api.registrations.registrations/notify`, `PlatformDriver.registrations`, `_BUDGET_ACTIONS/_TIMEOUT_ACTIONS` | 요청별 fork·남은 budget. 선행 list/get·CLI subprocess·raw registry 접근·상위 장기 mutex 없음 |
| Spring 송신 | `RegistrationSender.send/send_once`, `Settings`, app lifespan | 앱 소유 HTTP 자식, worker 공통 4 flock slot, 단일 deadline·1 POST, redirect/retry/후속 GET 없음. 종료 kill/wait |
| 일반 자동 후속 | `Manager._finish/_request` → `xenova_registration_hook.automatic/supervise/call_once` | 원 `registration_notice=consumed` 저장 후 기존 common/대상 lease·FD 반환 뒤 시작. 원 응답은 HTTP를 기다리지 않음 |
| factory 자동 후속 | `FirstBoot.register/main` → 같은 local hook | 기존 `registration_notices`에 소비를 확정하고 register가 새 대상만 반환. main의 잠금/요청 출력 종료 후 helper. API 준비/POST는 부팅·autostart와 독립 |

등록 범위는 REGISTERED 일반/factory이며 stopped/no domid·등록 뒤 start 실패도 같은 원칙이다.
RETIRED/PURGED/미게시 private 자산은 제외한다. display_name(없으면 저장 name)·현재 저장 자원·당시 template/version·저장 IP를 사용한다.
같은 display name의 다른 UUID를 유지하고 Zephyr 예약 IPv4도 반환한다. 없을 때만 null이다.
구형 등록 버전은 null이며 현재 catalog로 보충하지 않는다. UUID/internal name/MAC/IP의 기존 정합성 검증을 낮추지 않는다.

## 무엇을 어떤 기준으로 바꿨는가

1. 기존 `intents` reader의 재대조 구조를 재사용하되 등록 조회에는 intent/host 의존성을 넣지 않았다.
   두 자원은 현재 configuration, template은 원 생성 출처라는 기준으로 projection을 분리했다.
2. 하위가 Spring에 직접 보내던 경로를 FastAPI로 옮겼다. 합의된 전체 envelope와 nullable key를 유지하고
   Spring 200/201·같은 UUID·`registration=confirmed`를 모두 대조한다. 전송/접수나 임의 2xx는 확정이 아니다.
3. 이미 소비한 자동 기회·원 journal/factory progress는 변환하지 않았다. 재부팅/replay/복구/API startup/패키지 교체로 기회를 되살리지 않는다.
   factory만 유한한 API 준비 확인을 거치며 notify POST를 시도한 뒤에는 조회·재송신하지 않는다.
4. 순수 조회와 wire 변환, local API 인증과 Spring 송신 인증을 분리했다. lower helper는 Spring 전용 파일을 읽지 않는다.
   unit의 API token 우선순위(manager.env override)를 따르며 임의 URL/body/token을 API 호출자로부터 받지 않는다.
   API create 안에서 시작하는 helper에도 Spring token/URL이 환경으로 상속되지 않게 실행 환경을 whitelist로 제한했다.
   실제 Popen을 실행하는 시험에서 두 변수의 부재를 확인했다. sender의 Spring 권한을 lower에 복제하지 않는다.
5. `registration-notify`의 parser/action/계획/실행/JSON·별칭, 하위 직접 sender와 옛 JSON 예시 설치를 제거했다.
   옛 원 통지 결과/기회는 보존한다. 과거 CLI anchor는 새 조회/curl 전환 안내로 남겼다.
6. 실제 등록 UUID 검증 오류에서 0.6초 동기 sink 지연을 재현했다(수정 전 0.6186초).
   400 사유/코드는 그대로 반환하고 부가 진단은 앱의 포화 계수·최초 512자 사유에 한정했다.
   이 예상 거절 경로에서 raw body·입력 경로·동기 경고 출력을 수집하지 않는다.
   내부 결함·다른 driver 오류·최초 원인·필수 operation 저장을 일괄 제거하지 않았다.
7. 새 hook을 recipe/설치 fixture뿐 아니라 `assemble.py`의 필수 모듈 검증에도 넣었다.
   실제 ARM 앱 import에서 `runtime_logs`의 무조건 `sqlite3` import와 recipe 의존성 누락을 확인해
   고정 `python3-sqlite3`를 명시했다. DB 기능/저장 정책이나 운영 DB를 바꾼 것이 아니다.

FastAPI 송신 설정 부재는 notify만 제한한다. GET·일반 관리의 전역 readiness gate를 만들지 않았다.
송신은 기본 5초/설정 최대 15초와 전체 호출 잔여 기한 중 짧은 값이다. 헤더 없는 notify 전체는 15초다.
이 기본 기한도 middleware에서 threadpool 대기 전에 시작하며 async dependency 이전의 budget 존재를 시험했다.
local helper도 최대 15초와 4 slot이다. 두 계층 모두 일반 큐·상주 daemon·새 전송 재시도 정책이 없다.
자동 claim 뒤 중단하면 0회 송신도 가능하며 exactly-once 전달을 약속하지 않는다.
선택 결과 파일 부재/저장 실패는 원 등록을 뒤집거나 자동 재전송을 허가하지 않는다.

## 수락 근거와 대역

최종 관련 회귀: `native-final5.xml`의 **946 passed / 2 skipped / failures 0 / errors 0**, 213.52초.
이후 추가한 API 부재→수동 회복 시험은 `api-unavailable.xml` **1 passed**다. 이전 실패·재실행 수를 합산하지 않는다.
최종 OpenAPI 오류 union·sqlite3 포장 변경의 영향만 `contract-package-final.xml` **20 passed**로 재확인했다.
마지막 자격 환경 분리·기본 기한 보완 뒤 실제 HTTP 11개와 sender·OpenAPI 계약은 `registration-final.xml` **29 passed**다.
이 29개는 앞선 회귀와 중복되는 영향 재검증이며 전체 수에 다시 더하지 않는다.
테스트 개수만으로 완료를 판단하지 않고 아래 실제 assertion을 대응시킨다.

| 묶음 | 실제 시험·assertion | 경계 |
|---|---|---|
| R01~R03 | `RegistrationTest`의 exact fields/factory/no journal/lifecycle/Zephyr profile, process `actual_get...` | Debian·Ubuntu·Zephyr 실제 profile metadata가 GET에 전달. 같은 display name 2개·Zephyr 예약 IP/null·현재 자원/구형 null version, 읽기 전후 파일 동일·Xen/intent/host 금지 |
| R04 | metadata corruption→복구, retire 중 유한 재대조, HTTP 현재 자원/부분 열람/retire→404 | 손상≠빈 목록. 정지 원 key/실제 shutdown·completed 확인은 slow sender 중에도 수행 |
| R05·R10~R12 | 별도 uvicorn의 GET/OpenAPI/curl, `manual_actual_http...`, `response_meanings...`, auth/settings/body proxy | 실제 lower→FastAPI→실제 HTTP receiver. 전체 envelope/200·201·같은 UUID 확인. 400/401/403/409/503·redirect/무효·다른 UUID·응답 유실 구별. 실제 Spring DB는 receiver 대역 |
| R06·R07·R09 | `api_create_to_real_lower_callback...`, `factory_consumed_opportunities_late_api_readiness...`, `api_cold_restart...` | 실제 API create→하위 claim→실제 후속 POST. 원 201·후속 1회·기존 409, factory main 반환 뒤 API 지연 기동/정상 전달, cold restart 후 원 journal bytes 유지·추가 자동 POST 0 |
| R08 | factory API unavailable 유한 종료→startup 자동 보충 0→명시 요청 confirmed | 준비 지연 0.2초와 부재 예산 0.3초는 명시 fixture 값이다. 운영 예산/부팅 완료의 실측 상한이 아님 |
| R13 | `slow_sender_capacity_lookup_stop_original_result...`, `invalid_registration_diagnostics...`, 실제 DNS LD_PRELOAD, `test_shutdown_kills_owned_children...` | 4개 slow sender 중 포화 거절/GET/실제 stop/원 completed, sink 0.6초에서도 400·조회 진행, DNS 자식 종료 뒤 정상 송신, 자식 PID/slot 반환 |
| R14 | `test_cli_read_only_and_old_notify_all_variants...` | 옛 명령·execute/JSON/별칭 거절 전에 Manager 호출 0. 새 조회 execute 거절. 다른 CLI 규칙은 기존 회귀 |
| R15 | create compatibility/failure/reconcile·management budget/boundaries·lifecycle ownership·CPU pinning/cross·resources·provisioning/signal profiles와 전체 dom0-manager tests | 원 신원/의도/기한/동등 replay·단일 생성/수습·자격 재사용·신호/현재 관측 구분·HTTP/Worker 기존 소비 보존. Xen/guest mount/Store는 기존 소유 fixture 경계 |
| R16 | 두 정규 recipe package_write_rpm/package_qa, `verify_registration_delivery.py` | source→RPM 설치 bytes/mode, 새 import/route/단건·목록 metadata, 옛 설치물 없음. 최종 ARM 행은 아래에 구분 |

조건부 skip 둘은 실제 ARM drive bundle을 요구하는 무관 firmware 선택 시험(새 firmware를 만들거나 가짜 대체하지 않음),
root 실행에서 권한 거절을 재현할 수 없는 기존 Worker append 시험이다. 해당 기능의 새 완료 증거로 사용하지 않는다.
이번에는 planner/Gateway/Agent/Worker/Spring 제품 코드를 수정하지 않았다. 기존 소비 회귀와 제품 구현 책임을 구분한다.

## 조회 비용과 보호

기존 `benchmark_query_cost.py registrations intents get`를 같은 작은 fixture/시작 상태에서 실행했다.
아래는 `registrations` 한 회차 표본이며 단위 시간은 WSL native 측정이다.

| 등록 metadata 수 | text read / JSON parse | registry read | 작은 원 journal read | common/observation lock · host/Xen · fsync · payload hash | 표본 시간 |
|---:|---:|---:|---:|---|---:|
| 1 | 7 / 7 | 3 | 1 | 전부 0 | 1.61 ms |
| 10 | 34 / 34 | 30 | 1 | 전부 0 | 4.37 ms |
| 100 | 304 / 304 | 300 | 1 | 전부 0 | 36.31 ms |

registry는 최초 cohort·집합 재대조·항목 게시 전 검증의 세 번이다. 새 TTL cache나 조회 원본을 만들지 않았다.
동일 fixture의 intents는 9/36/306 read·parse와 intent 2회이며 이 의존성을 새 조회에 가져오지 않았다.
live get은 다른 의미의 단건 관측이므로 전체 목록과 지연 배수를 직접 비교하지 않는다.
통지의 공통 sender는 등록 조회 뒤에만 실행되고 원 Manager lease/common 잠금을 보유하지 않는다.
진단 계수·slot은 유한하며 필수 journal/claim/소유/정리의 내구성은 best effort 로그로 바꾸지 않았다.
로컬 시간/기한 주입을 SD·실보드·커널 전체 장애의 hard deadline 보장으로 확대하지 않는다.

## 포장·설치·실패 기록

- 고정 WSL 작업 root: `/home/ssafy/xenova-registration-api-20261102`.
- host 제공물: 새 worktree `artifacts/registration-api/`의 canonical LF source tar/manifest·OpenAPI와 최종 delivery 묶음.
- `xenova-platform_1.0.bb`: 순수 projection/local hook/Manager/CLI/first-boot 포함, 옛 JSON 예시 제외.
- `xenova-dom0-manager_0.1.0.bb`: 새 route/sender/settings/driver/app와 전용 비활성 env 예시·선택 EnvironmentFile 포함.
  netclient/io/ctypes/sqlite3 등 실제 고정 stdlib 분할을 선언한다. FastAPI 0.135.3 등 기존 target provider를 사용하며 pip/업그레이드를 하지 않았다.
- 실제 API service의 KillMode/backend scope와 기존 host drain은 완화하지 않았다. 정규 unit 연결/교체 절차는
  [배포 원본](../platform/deployment-readiness.md#registration-api), [부팅 원본](../platform/management-boot-boundaries.md)에 있다.
- 최종 `package-final6.log`·`qa-final6.log`의 정규 RPM 생성·package QA가 통과했다.
  고정 WSL의 process I/O statistics 미지원 경고와 native TestClient deprecation 경고는 남기며 의존성 업그레이드로 숨기지 않았다.
- `delivery-final8.json`: 실제 Python runtime 의존성을 포함한 **80 RPM**, 제공 파일 **59개 bytes/mode 일치**.
  ARM/QEMU의 Python **3.14.7**, FastAPI **0.135.3**, Pydantic **2.12.5**에서 설치 import·공개 등록 조회·두 OpenAPI route가 통과했다.
  app import/조회가 만든 송신 자식은 0이다. root의 mount/Xen만 외부 fixture이며 개발 source PYTHONPATH를 넣지 않았다.
  ARM HTTP 자식의 실제 실행·board/systemd 서비스/전체 이미지 수락을 대신하지 않는다. HTTP 실제 왕복은 native 별도 프로세스 시험이다.
- 최종 정규 패키지 SHA-256:
  - `xenova-platform-1.0-r0.cortexa72.rpm`: `01978ed9fc29c984abb087cf366d5de90e4a655c234a8f991606388c32842e34`
  - `xenova-dom0-manager-0.1.0-r0.noarch.rpm`: `299be7fbc85ff8c95c6ab44c16cb5b6602a5f1df835c36adac95d0e513cfb3af`
  - 공용 `xenova-mqtt-client-0.1.0-r0.noarch.rpm`: `1855159136a045e91cbfc7726e0e97262cfb9f3a97c27f2c608f19e99c7f1020`
- 같은 후보의 공개 evidence는 [시험·비용·RPM/설치 manifest](evidence/vm-registration-api-20261102.json)다.
  제공 폴더 `artifacts/registration-api/delivery`에 위 세 RPM, source changes/diff·삭제 목록, OpenAPI,
  통과 로그·XML, 전체 target 설치/RPM hash, 실행 스크립트와 `manifest.json`을 모았다.
  원 credential/token/Authority/임시 VM root/원 실패 payload는 넣지 않았다. 기존 운영 파일은 정리하지 않았다.

재현 명령(고정 로컬 경로는 이 작업 PC 기준):

```sh
# 제공 폴더의 run-native.sh: 기존 도구/PYTHONPATH·broker·systemd259 경로를 포함한다.
bash artifacts/registration-api/run-native.sh
# 기존 고정 oe-init-build-env로 package 디렉터리를 선택한 뒤:
bitbake xenova-platform xenova-dom0-manager -c package_write_rpm
bitbake xenova-platform xenova-dom0-manager -c package_qa
python3 platform/tests/verify_registration_delivery.py \
  --task-root /home/ssafy/xenova-registration-api-20261102 \
  --base-root /home/ssafy/xenova-provisioning-20261102/target-release3 \
  --qemu /home/ssafy/perseus-stop-ssh-review-20261018-223833/sshd-binary-root-v2/qemu-aarch64-static \
  --tag NEW_OWNED_TAG
```

새 tag는 새 소유 설치 root를 만들며 기존 증거를 삭제/덮어쓰지 않는다. root/chroot 검증은 이 소유 fixture에서만 수행한다.

보존한 실패와 수정 이유:

1. 새 Zephyr fixture의 immutable kind/id와 Debian retire용 작은 ext4 header가 빠져 제품 검증에 거절됐다.
   fixture를 정식 출처/자산 형식으로 고쳤고 검증을 우회하지 않았다.
2. 확대 회귀의 child import·Agent/broker/backend source 경로와 systemd 259 root가 누락돼 실패했다.
   기존 고정 도구·소스 경로를 연결했고 외부 제품이나 의존성 버전을 바꾸지 않았다.
3. 기존 lock 시험의 문자열 일치와 CLI PlatformError 기대는 최신 구현/계약과 달랐다.
   AST의 실제 with-context lock과 정식 exit 4·사유·명시 force 뒤 정상 종료 assertion으로 고쳤다.
4. snapshot mtime을 0으로 고정해 BitBake의 파일 checksum cache가 최신 helper 변경을 놓쳤다.
   source→RPM 바이트 대조로 발견했고 후보의 실제 mtime/실행 모드·새 manifest로 다시 고정해 정규 task를 재실행했다.
5. native RPM 도구가 gzip tar를 반환하는데 cpio로 해석한 추출 실패, 기존 절대 systemd 링크의 안전 필터 거절을 기록했다.
   실제 archive 형식과 소유 target 경로 검증을 사용하고 마지막 링크 자체만 교체한다. 운영 경로를 삭제하지 않았다.
6. 최소 ARM root에는 anyio의 asyncio/concurrent 등 선언된 stdlib 패키지가 빠져 있었다.
   실제 pkgdata의 고정 runtime closure를 설치했다. 별도로 확인된 API sqlite3 누락은 recipe를 수정했다.

## Spring 확인과 남은 공동 수락

사용자 완료 전달, `800d584` 소스 확인, 계약 receiver 실행, 실제 Spring/DB·운영 설치를 각각 구분한다.
확인한 실제 클래스는 `VmRegistrationRequest/Controller/Service`, `VmRegistrationAuthenticationFilter`,
`Dom0ManagerClient.listRegistrations`, `Dom0VmRegistrationInventoryClient`, `VmSynchronizationService`다.
DTO의 snake_case 전체 envelope, 전용 token/권한, 201/200 confirmed, 이름/퇴역 409, GET 배열 소비를 대조했다.
현재 사용할 수 있는 Java21/Maven/Docker 실행 환경을 확인하지 못해 Spring 제품·격리 MySQL 종단을 실행하지 않았다.
source 검토/receiver를 실제 DB 저장이나 관리자 동기화 성공으로 확대하지 않는다.

남은 일은 승인 URL·별도 송신 token 공급, 동일 후보 설치/프로세스 로드 확인, 실제 Spring/DB와
단건 201/200·충돌/퇴역·응답 유실·관리자 동기화, 보드 first-boot/autostart/서비스 교체의 공동 수락이다.
이번에는 운영 설정·서비스·실데이터·보드/EC2/VM/broker/DB/SD·전체 이미지·실차·7번·commit/push를 수행하지 않았다.
다음 담당자가 새 FastAPI 수신부나 하위 sender를 다시 구현할 요청은 남기지 않았다.

## MR 준비: 최신 platform 통합과 재검증

위 구현·포장 단계 뒤 사용자가 작업 단위 커밋, origin push와 `platform` 대상 MR 생성을 승인했다.
구현은 `0a65791`(하위 조회·로컬 후속), `004abef`(FastAPI·Spring 송신),
`9cf161a`(교차·설치 검증), `d814bc2`(계약·인계)로 나눴다.
`origin/platform`의 `1659e3f0a3b78f46bbe3e89b5041fc1addeb0ed1`을 merge하며
`dom0_manager/drivers/platform.py` 한 파일의 충돌을 해결했다.

- 최신 target의 일반 조회 `read_fork`, action별 예산·timeout 전달과 Worker 설정 경계를 유지했다.
  삭제한 `registration-notify`를 allowlist에 되살리지 않고 `registrations`를 연결했다.
- HTTP 전용 Spring 설정은 `HTTP_ONLY_ENV`에도 추가했다. Worker가 송신 자격을 읽지 않는 assertion과
  새 조회의 예산 전달 assertion을 추가하고, 무관한 Worker 정책은 수정하지 않았다.
- 통합 후 `test_xenova_registration.py`, `test_registration_api_process.py`,
  `platform/services/dom0-manager/tests` 전체를 실행했다. 최종 결과는 **664 passed / 1 skipped**,
  83.14초다. skip은 root가 파일 권한을 무시하는 기존 저장 시험이다.

첫 실행(`/mnt/c` 작업트리)은 662 passed / 2 failed / 1 skipped였고, sender 단독 재검사에서도
두 실패가 재현됐다. 0.4초 fixture 예산 안에 자식 import가 끝나지 않았으며, 같은 경로에서
직접 실행한 정상 자식은 0.683초 뒤 confirmed를 반환했다. 제품 timeout이나 assertion을 늘리지 않고,
기존 native 검증과 같은 WSL 내부 소유 snapshot에서 전체 묶음을 재실행했다.
API source·시험 100개는 바이트 대조했고, 하위·first-install·common·플랫폼 시험 Python 146개는
LF 기준 대조해 차이가 없었다. 두 실패 기록은 삭제하지 않는다.

원자료는 `/home/ssafy/xenova-registration-api-20261102/`의 `mr-integration.xml`,
`mr-sender-recheck.xml`, `mr-integration-native.xml`, `mr-integration-source-sha256.json`이며,
해시는 같은 보고서의 evidence JSON에 기록했다.
재실행 명령은 위 세 시험 경로를 `python3 -m pytest -o addopts= -q`로 지정한 것이다.

기존 RPM·ARM 설치 근거는 `d814bc2`까지의 포장 후보 근거다. 이번 target merge 뒤 확인한 것은
native 소스 통합 회귀이며, 새 RPM·ARM 설치 검증을 수행했다고 확대하지 않는다.
fetch에서 `origin/backend`가 `ca9a2a3`으로 진행한 사실은 확인했지만, 기존 Spring 소스 대조 후보
`800d584`를 새 후보 검증으로 바꾸지 않는다. 운영 설치·Spring/DB 공동 수락은 계속 별도다.

## FastAPI 준비 이후 부팅 통지

후속 기준은 MR !335의 `bfa5bb4`다. 실제 API unit은 autostart/host admission 뒤인데
first-boot helper는 그보다 먼저 15초를 소모했다. 짧은 지연 fixture는 통과했지만,
API 활성화 전에 통지 기회가 끝날 수 있어 사용자 승인에 따라 연결 시점을 수정했다.
구현·시험은 `50e1aba`, 설치 검증 도구는 `a5c734a`에 나눴다. 이번 후속은 commit/push와 기존 MR 갱신까지 승인받았다.

### 무엇을 어떤 기준으로 바꿨는가

1. `dom0_manager.__main__.ReadyServer.startup`은 고정 Uvicorn의 `Server.startup`을 먼저 실행한다.
   ASGI startup·bind/listen 성공과 `started`를 확인한 뒤 작은 비차단 AF_UNIX datagram으로 `READY=1`을 보낸다.
   `xenova-dom0-manager.service`는 `Type=notify`, `NotifyAccess=main`으로 바꿨다.
   앱 import/프로세스 존재나 Xen health를 API listen 준비로 대신하지 않는다. bind 실패에는 READY가 없다.
2. `xenova_registration_hook.automatic(wait_ready=True)`는 기존 소비된 factory 사건의 UUID만
   `defer_factory`에 넘긴다. `systemd-run --no-block --collect`의 일시 job은 API를 Requires/After로 참조한다.
   first-boot의 공통/대상 FD는 이미 반환했고 API를 기다리지 않는다. 기존 autostart→host admission→API 순서는 유지한다.
   실제 `after_api` 실행 때만 기존 15초 송신 예산을 시작한다. API 기동 시간으로 송신 예산을 소모하거나 timeout 수치를 늘리지 않는다.
3. 첫 실제 systemd 시험에서 **API restart가 아직 남은 dependent를 다시 실행해 3번째 POST를 만드는 결함**을 확인했다.
   `/run/xenova/registration-notices/factory-started-UUID`를 `O_CREAT|O_EXCL`로 먼저 생성해 같은 boot의 재진입을 막았다.
   이 일시 표식은 큐/등록 원본/성공 증거가 아니다. 생성 뒤 중단되면 송신 0회일 수 있다.
   reboot에는 job/표식이 사라지고 원 progress의 내구 소비가 재예약을 막는다. API startup에서 목록을 훑거나 표식을 지우지 않는다.
4. API 실패·취소 뒤 자동 재예약, POST retry·후속 GET, 부팅 시 미송신 보충은 없다.
   job의 의존 실패 때문에 결과 파일이 없을 수 있으며 confirmed로 간주하지 않는다. 필요하면 기존 인증된 수동 API를 사용한다.
   Spring envelope·토큰 분리·200/201/대상 confirmed 검증과 일반 create의 후속 경계는 그대로다.

### 실제 정상·실패·회복

- **집중 14 passed**, **확대 667 passed / 1 skipped**. 두 묶음은 중복 합산하지 않는다.
  확대 묶음은 하위 등록/새 부팅 시험/별도 프로세스 HTTP/dom0-manager 전체이며,
  skip은 root에서 성립하지 않는 기존 파일 권한 시험이다.
- `test_factory_ready_after_old_budget_then_once_and_api_restart_does_not_requeue`:
  실제 WSL systemd 255.4의 시험 전용 transient API unit을 activating 상태로 유지한다.
  first-boot main은 5초 이내 반환하고 16초 뒤에도 통지 job은 대기·Spring POST는 0이다.
  barrier를 해제한 실제 FastAPI가 READY를 보내면 factory 두 UUID 각각 confirmed로 끝난다.
  API unit restart 뒤 추가 POST는 0이며 완료 factory 재등록의 새 기회도 없다.
- API 기동 실패의 종속 job 종결→자동 보충 0→동일 UUID의 수동 confirmed를 확인했다.
  실제 `automatic`→별도 defer 프로세스는 별도 시험에서 실행했고, scheduler command 경계만 기록 대역으로 대체해
  `--no-block`/Requires/After/Restart=no와 Spring 비밀 환경의 부재를 확인했다.
- 기존 실제 API create→하위→자동 callback, 원 201/409·조회·실제 stop·느린 송신/종료 시험을 함께 통과했다.
  systemd 실행 시험에서 대역은 guest/mount/Xen의 기존 Board와 Spring 계약 receiver다.
  production API unit명만 시험 전용 이름으로 치환했고, 수신부·READY·job 실행·local HTTP·sender는 제품 코드다.
- 집중 1차의 13 passed / 1 failed 기록은 `boot-ready-focused.xml`에 보존했다.
  수정 후 `boot-ready-focused2.xml`, 최종 `boot-ready-regression.xml`을 사용한다.
  시험 종료 뒤 이 시험이 만든 systemd job/unit 잔존은 각각 0이었다.

재현은 기존 고정 native PYTHONPATH에서 다음을 실행한다. systemd opt-in은 소유 격리 환경에서만 사용한다.

```sh
XENOVA_TEST_SYSTEMD_BOOT=1 python3 -m pytest -o addopts= -q \
  platform/tests/test_xenova_registration.py platform/tests/test_xenova_registration_boot.py \
  platform/tests/test_registration_api_process.py platform/services/dom0-manager/tests
```

### 포장·비용·남은 수락

두 정규 recipe의 `package_write_rpm`/`package_qa`를 새 고정 source로 다시 통과했다.
경고는 빌드 host의 `CONFIG_TASK_IO_ACCOUNTING` 부재 1건이며 제품 package QA 실패가 아니다.
80 RPM 의존성 설치, 제공 파일 59개의 byte/mode 일치, ARM/QEMU Python 3.14.7·FastAPI 0.135.3·
Pydantic 2.12.5·Uvicorn 0.44.0에서 import/등록 조회/OpenAPI와 실제 listen 후 READY datagram을 확인했다.
native Uvicorn은 0.54.0이며 두 고정 버전의 startup 경계와 실제 실행을 각각 확인했다.

RPM만 추출한 최소 root의 unit verify는 `sysinit.target` 부재로 실패했다.
이 기록을 보존하고 기존 전체 dom0 unit fixture에 실제 새 RPM의 API unit과 플랫폼 recipe를 연결했다.
고정 target systemd 259의 정적 전체 graph verify는 exit 0이었다. 없는 선택 mount/unit의 debug 진단은 보존한다.
명령 존재 확인용 stub을 실행하지 않았으며 실제 target PID1/보드 기동 시험으로 확대하지 않는다.

추가 비용은 API 기동당 READY datagram 1개, 새 factory UUID당 일시 systemd job과 `/run` 배타 생성 1회다.
관리 요청마다 새 스캔/해시/원장 fsync를 추가하지 않았고, factory 준비의 반복 OpenAPI GET은 실제 송신 경로에서 제거했다.
일시 표식에 내구 fsync는 하지 않는다. 재부팅 뒤 at-most-once 근거는 기존 factory progress의 필수 내구 저장이다.
활성 송신 slot은 기존 4개이며 job은 등록 사건의 새 UUID 집합에만 한정된다. 별도 daemon/영속 큐는 없다.

새 source·RPM·설치 manifest와 원 로그는 `artifacts/registration-api/delivery-boot-ready/`에 둔다.
API entrypoint·notify unit·하위 hook을 함께 교체해야 한다. 정규 제품 단일 서버 entrypoint 기준의 수락이며,
외부 `uvicorn` launcher를 notify unit의 준비 신호 생산자로 간주하지 않는다.
실제 보드의 전체 기동·autostart·서비스 교체, Spring/DB 종단·운영 설정 공급은 미수행이다.
운영 서비스/보드/VM/실데이터 변경이나 squash/Merge는 하지 않는다.
마감 중 CI 담당자의 요청을 전달받아 **push와 MR !335 갱신은 보류**했다. 이번 후속은 로컬 커밋만 남기며,
원격 MR의 기존 후보를 새 설치/실행 검증이 반영된 후보라고 표시하지 않는다.

기준 의미는 고정 [systemd v259 service](https://github.com/systemd/systemd/blob/v259/man/systemd.service.xml)와
[systemd-run](https://github.com/systemd/systemd/blob/v259/man/systemd-run.xml)의 notify/After 및 비동기 transient 실행이다.
실제 제공 여부는 위 소스·실행·설치 시험으로 판단한다.
