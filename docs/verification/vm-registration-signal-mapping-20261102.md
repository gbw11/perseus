# 생성·초기 설치·신호 선언 연결 조사

조사일: 2026-11-02. 대상: `feat/platform/gateway-history-driving-app`, HEAD `206361b1b74994dccf26a3dccb32998ee8164487`. 착수 작업트리는 깨끗했다. 첨부 `CODEX_VM_REGISTRATION_SIGNAL_MAPPING.md`의 **구현 전 조사**만 수행했다. 제품·시험·recipe·unit·정식 계약·담당자 요청서는 수정하지 않았다.

## 1. 핵심 결론

**공통 출발점은 `/srv/xenova/instances/*/instance.json` 등록부다. 일반 create journal을 등록 VM 목록으로 쓰면 factory VM을 놓친다.** `load_records()`가 Debian·Ubuntu·Zephyr를 함께 읽고, `Manager._state()`는 등록부와 tombstone으로 lifecycle을 조립한다. factory는 일반 create journal 대신 설치 manifest·identity plan·공개 instance를 남긴다. [등록부][registry] [lifecycle 조립][state] [factory 등록][fb-register]

**일반 생성과 factory는 신원·설정 전달 시점이 다르다.** 일반 생성은 호출자가 제공한 UUID와 원 확정 명세로 private 자산을 준비한 뒤 게시한다. factory 조립은 UUID가 미확정인 seed를 만들고, 보드 첫 설치에서 UUID/MAC plan을 저장한 다음 seed를 공개 등록한다. 일반 Linux는 rootfs 준비 때 VM UUID를 `instance-id`에 기록하지만, factory guest는 PID1이 확정한 machine-id에서 `instance-id`를 만든다. 후자는 host 등록 UUID와의 실제 일치까지 소스만으로 확인했다고 할 수 없다. [일반 준비][m-prepare] [seed 준비][linux-seed] [첫 설치 신원][fb-identities] [guest 신원][guest-id]

**메타데이터 reader는 있으나 공개 `get/list`를 경량 의도 조회로 재사용하면 관측·쓰기 비용이 따라온다.** `Execution.intent()`는 JSON을 읽고 검증한다. 반면 `get/list`는 등록부 전체 열람, lifecycle 대조, Xen/실행 신원 관측, observation JSON 게시를 수행한다. `boot_decision()`은 admission을 쓰므로 reader가 아니다. [intent][intent] [조회 회차][read-context] [공개 조회][query] [관측 게시][observation] [부팅 판단][boot-decision]

**계정 발급·보존·export와 실행별 Store provisioning은 이미 제공돼 있지만, create/FirstBoot가 VM별 MQTT 자격을 자동 확보·전달하는 연결은 확인되지 않았다.** Linux의 private `agent.env` 소비와 Zephyr의 실행별 receipt/binding 소비는 별도 어댑터다. broker 계정 저장 성공, broker reload, guest 전달, 실제 인증·수신을 하나의 완료로 합치면 안 된다. [계정 변경][broker-change] [export][broker-export] [Linux unit][agent-unit] [Bootstrap][production-bootstrap]

**새 주행 firmware 제공과 초기 A/B 선택은 연결되지 않았다.** 현재 lock·factory defaults·예제 config는 Network Starter를 선택한다. 새 주행앱은 별도 firmware/BUILD_RECEIPT를 생성한다. 또한 firmware 교체 후 `active_firmware`는 바뀌어도 Worker의 일반 신호 기대는 최초 template ID/version의 catalog를 읽는다. 명시 drive rejoin 설정은 별도 우회 근거를 제공하지만, 모든 VM의 현재 적용 신호 선언을 자동 공급하는 경로는 아니다. [lock][template-lock] [factory 기본값][defaults] [예제 선택][factory-config] [주행앱 포장][drive-package] [firmware 전환][firmware-transition] [Worker 기대][worker-observer]

**Spring의 기존 POST는 생성·기동 명령이며 등록 통지 수신 API가 아니다.** 이번에 확인한 VM Controller/Service 및 backend Controller mapping에는 platform-origin 등록만 수신하는 경로가 없다. 기존 POST로 통지하면 UUID/DB 생성과 start 역호출이 얽힌다. 일반 생성의 성공 저장 후와 factory 등록 완료 후가 의미상 후보지만, 둘 다 현재 잠금 안쪽이므로 그 위치에서 HTTP를 직접 기다리면 안 된다. [HTTP 생성][http-create] [Spring 생성][spring-service] [일반 완료][finish] [factory main 잠금][fb-main]

아래 상태는 구분해서 사용한다. **확인**은 현재 소스의 호출 연결, **미연결**은 조사한 제품 경로에 자동 호출이 없음, **의도된 차이**는 서로 다른 수명·어댑터, **미확인**은 설치/실행 근거가 없음, **제안**은 이번에 구현하지 않은 후속 범위다.

## 2. 생성·설치·등록 대응표

| 경로/지원 프로필 | 실제 진입점·호출 순서 | UUID 확정·등록 완료 지점 | 등록/lifecycle/의도 원본과 writer·reader | 재사용 함수·주의 경계 | 근거 |
|---|---|---|---|---|---|
| 일반 CLI·직접 Manager, 공통 | 설치 wrapper → `xenova_vm.main` → 공통 CLI `main` → `Manager.call('create')` → `_normalize` → `_mutate`. create는 `--json` 유무와 무관하게 공통 경로 | 호출자가 `vm_id` 제공. CLI `request_id` 자동 발급은 추적용이며 VM UUID 발급이 아님. 계획은 무저장·무예약 | 원 `operations/<op>.json`에 request/initial_spec/hash/할당·원 기한. 공개 instance와 tombstone은 별개 | 신규 생략 자원은 catalog 기본값. replay는 원 initial_spec 기준. 생략↔동일 명시 허용은 유지하며 표현 정리를 새 기능으로 구현하지 않음 | [CLI 선택][cli-route] [입력 조립][cli-create] [strict 입력][normalize] [수락][accept-create] [동등 비교][same-create] |
| Debian 일반 생성 | `debian-13-arm64/1.0.2` 선택 → `_prepare` → `prepare_instance.make_instance(private=True)` → 검증 복사/rootfs 신원 준비 → `_complete_private` → `PreparedInstance.publish` → `_create` → `_finish` | 외부 VM UUID를 유지. rename 게시만으로 원 성공이 아님. metadata 대조·REGISTERED tombstone·DHCP·stopped intent 초기화·원 SUCCEEDED/complete 저장까지 구별 | `instances/<internal>/instance.json`; `tombstones/<UUID>.json` generation 0; `control/<UUID>.json` desired stopped. 일반 create 원장은 같은 UUID와 연결 | 실제 Xen start 없음. 불변 template 읽기/검증 복사·자산 소유·원 기한은 기존 경로 사용. 통지/자격 때문에 root를 다시 전체 읽지 않음 | [lock][template-lock] [준비 분기][m-prepare] [복사/신원][linux-instance] [공개 채택][private-complete] [등록 정산][m-create] [최종 저장][finish] |
| Ubuntu 일반 생성 | `ubuntu-24.04-arm64/1.0.2` → Debian과 같은 Linux 준비 경로. `make_instance`는 source distribution을 ubuntu/debian으로 검사 | Debian과 같은 외부 UUID·게시·완료 경계. OS별 root/boot source는 선택 template에서 옴 | 위 Linux와 같은 등록·lifecycle·intent 모델. OS 차이를 원장/신원 모델로 나누지 않음 | 두 OS가 같은 함수를 쓴다는 소스 확인. 실제 각 guest 부팅·Agent 인증이 이번에 실행됐다는 뜻은 아님 | [source 선택 검사][linux-select] [기존 양 OS 기준선][baseline-create] [Linux 신원 기록][linux-root] |
| Zephyr 일반 생성, 비네트워크 Starter | `_prepare` → `zephyr.new_instance` → `bundle_info/copy_revision` → firmware revision 복사·공개 instance → 공통 `_create/_finish` | Manager 제공 UUID 유지. `creation_origin`과 `active_firmware` 분리. REGISTERED/원 완료 경계는 공통 | schema 2 instance, 고정 1-vCPU/16MiB 프로필·capabilities, tombstone/intent. Linux root.raw 없음 | 비네트워크 Starter에는 DHCP/SSH/협조 종료/앱 준비가 없음. MQTT 자동 공급 대상으로 동일 취급하면 안 됨 | [준비 분기][m-prepare] [프로필][zephyr-profile] [신규 자산][zephyr-create] [공통 정산][m-create] |
| Zephyr 일반 생성, Network Starter·호환 network firmware | network profile이면 MAC/IP/hostname 할당 → 같은 `new_instance`와 network profile 검증 → 공통 등록 | UUID·네트워크 예약 유지. network 지원은 앱 신호 제공의 증거가 아님 | network capabilities에는 협조 reboot/DHCP 등이 있으나 `app_heartbeat`의 현재 앱별 선언은 별도 문제 | catalog의 Starter는 `app_heartbeat:false`를 반환. 실제 drive bundle은 임의로 catalog 등록됐다고 볼 수 없음 | [할당][new-selection] [network 판정][zephyr-profile] [catalog 선언][catalog-caps] [생성][zephyr-create] |
| 일반 HTTP·Spring 생성 | Spring `VmService.create`가 DB UUID 저장 → 기존 dom0 HTTP POST → `VmService.create` → `PlatformProvisioner.ensure_instance` → 하위 create → `PlatformDriver.create`로 기동 | Spring UUID를 하위 registry/Xen에 전달. 하위 asset 등록과 HTTP create→start 완료는 별개. HTTP는 기존 201 및 이미 실행 중 거절 정책을 가짐 | Spring DB는 자기 UI/요청 기록. 하위 instance가 host 등록 원본. provisioning의 기존 full 자원 입력은 변경 불필요 | HTTP를 등록 통지 receiver로 재사용하지 않음. 하위 생략 지원이 이미 DTO까지 제공됐다고 확대하지 않음 | [Spring 저장][spring-service] [HTTP route][http-create] [순차 등록→기동][http-service] [실제 어댑터][provisioner] |
| factory 빌드·Linux seed | `assemble-xenova-first-install.py` → `assemble.plan/assemble` → 검증 template 복사 → defaults에 선택된 guest에 `prepare.make_instance(deployment_seed=True)` → 공통 seed 준비 | seed UUID는 pending placeholder. factory_id·디스크 bundle 작성은 보드 영구 VM 등록이 아님 | 조립 데이터의 `templates/`, `seeds/`, catalog와 `.xenova-install.json`. 현재 기본은 Debian seed; Ubuntu template은 보존하되 현재 기본 guest는 아님 | `prepare_deployment_seed`는 machine-id/SSH 신원을 공유하지 않도록 비움. 일반 rootfs prepare와 같은 시점에 VM private credential을 고정할 수 없음 | [조립][assemble] [factory manifest][factory-manifest] [seed UUID][linux-instance] [seed rootfs][linux-seed] [기본값][defaults] |
| factory 빌드·Zephyr A/B seed | `copy_rtos_templates` → `copy_rtos_seeds` → `zephyr.new_instance(registry='seeds', seed=True)` | 각 독립 firmware 자산은 생기나 영구 UUID는 pending. 현재 factory 입력의 A/B는 같은 Starter provenance 요구 | `seeds/zephyr-drive-a,b`의 instance+firmware, `.xenova-install.json.firmware_instances` | 현재 factory preset 요구와 일반 플랫폼의 임의 지원 VM 관리를 구분. drive firmware를 넣으려면 allowed template/선택 provenance/FirstBoot 검증을 함께 대조해야 함 | [seed adapter][rtos-seeds] [신원 placeholder][zephyr-create] [FirstBoot 입력 제한][fb-register] |
| 보드 첫 설치·등록, 현재/과거 factory | `xenova-first-boot.service` → `main(register)`의 operation lock → `FirstBoot.register` → identity plan 저장 → seed metadata에 UUID/MAC → rename → 전체 등록 검사 → intent 초기화 → 설치 complete | `/srv/xenova/.first-install-state.json` identities에서 VM UUID 확정·보존. VM별 공개 rename·registered[] 진척과 설치 전체 complete는 다른 지점. `.xenova-storage.json`, `/run/xenova/install-ready.json`도 역할이 다름 | 일반 create journal 없음. 공개 instance + 선택적 tombstone으로 Manager에 포함. factory 초기 intent running/source factory는 `/srv/.../control`; completed 재실행은 사용자 변경을 초기화하지 않음 | current는 Debian+A/B, legacy는 Ubuntu+Debian(+선택 Zephyr)를 수용. 기존 completed VM 부재를 seed로 재생성하지 않음. 첫 설치 중간 공개 자산도 설치 complete 전에는 `storage_check`를 통과하지 못함 | [unit][firstboot-unit] [UUID plan][fb-identities] [등록·완료][fb-publish] [mount/완료 gate][storage] [intent 초기화][initialize] |
| start/autostart, 모든 OS | 등록 후 별도 `start`; 부팅은 `xenova-vms.service` → `Platform.autostart` → `boot_decision` → 실제 start | 새 execution_id는 `prepare_launch`, domid/Xen unique는 실제 실행 결합. VM UUID 재발급 아님 | 영구 `autostart` 설정, 영구 desired intent, 이번 host의 admission, `/run/.../executions`는 각각 별도 | 일반 create는 초기 stopped. factory는 초기 running. autostart on이 현재 running을 뜻하지 않고, start 성공도 Agent/app/Gateway 준비 완료가 아님 | [autostart unit][vms-unit] [결정][boot-decision] [기동][autostart] [실행 신원][launch] |

### 의도·등록 reader의 비용과 정합성 경계

| 원본/함수 | 실제 writer·reader/저장 | 읽을 때 따라오는 일 | 재사용 판단 |
|---|---|---|---|
| `load_records(data, validate_assets=False)` / `Platform.records` | 일반 게시·FirstBoot가 instance를 씀. Manager/Platform이 읽음 | 전 VM 경로·metadata/중복 신원, Linux root의 symlink·inode·template alias 검사. `Platform.records`는 mountinfo·storage/설치 marker도 읽음. `False`는 무검사/단일 파일 조회가 아니며 전체 boot hash만 생략 | 공통 등록 원본으로 재사용. 새 registry나 create journal 색인으로 대체할 이유 없음. [registry][registry] [Platform.records][platform-records] |
| `read_tombstones` / `Manager._state` | create/retire/restore/purge가 lifecycle 저장. `_state`는 active+non-REGISTERED tomb 불일치, REGISTERED인데 자산 부재를 오류로 처리 | tombstones 전체 열람; 옛 `.deleted-*` archive 처리 분기. tomb 없는 정상 공개 등록은 REGISTERED/generation 0 | factory 포함에 필요. 미완료 move/게시를 완성 등록으로 오인하지 않도록 현 경계 유지. [tomb reader][tombs] [state][state] [세대 writer][tomb-write] |
| `Execution.intent` | `_initialize_locked`와 `_accept_locked`가 `/srv/xenova/control/<UUID>.json` 저장. launch/recovery/Worker 응답 조립이 읽음 | UUID·desired enum·양의 revision·generation·원 operation ID 검사. 파일 없음은 `None`; 손상은 RECONCILE_REQUIRED. intent() 자체는 Xen/저장/초기화/잠금/해시 없음. `Execution` 생성은 host boot ID 읽기·형식 검사 | 읽기 원천 재사용. missing을 running/stopped로 만들지 않음. [reader][intent] [초기 writer][initialize] [사용자 writer][intent-writer] |
| `Execution.describe` | intent/lifecycle을 응답에 조립 | intent 외에 run/admission/host barrier/shutdown marker를 읽음. intent generation과 현재 REGISTERED generation을 대조해 applicable 반환. Xen/저장은 없지만 intent-only 함수는 아님 | 출력 의미 재사용 가능. 오래된 intent를 지우지 않고 `intent_applicable:false`로 구분. [describe][describe] |
| `boot_decision` | autostart가 호출 | admission을 쓰고, 정책상 필요한 경우 정식 boot-policy start intent/operation도 수락 | **조회에서 호출 금지**. autostart 설정을 사용자 의도로 대체하지 않음. [writer 경로][boot-decision] |
| 공개 `Manager.get/list` | `_query` → ReadContext → observe/observe_many → `_view` | global physical lock을 항상 잡는 옛 구조는 아님. 현재 observation slot/대상 observation lock, 외부 Xen/identity 관측, JSON fingerprint, `/run/observations` 저장이 있음. read 변화는 최대 두 회차 | 경량 의도 API의 구현체로 그대로 호출하면 목적과 비용이 다름. 단순 `records+intent` 루프로 줄이면 현 정합성 보호도 빠짐. [dispatch][query-route] [ReadContext][read-context] [query][query] [게시][observation] |
| `ReadContext.validate_one` | 관측 제출 직전 metadata 재대조 | 해당 instance/tomb/intent/run/admission 등을 대조. creation_operation_id가 있는 일반 등록은 원 create SUCCEEDED를 추가 확인; factory에는 없는 field를 강제하지 않음 | 후속 경량 조회의 정합성 설계에 가장 가까운 기존 경계. public move·intent 쓰기 전후를 섞어 반환하지 않도록 해당 사실의 재대조가 필요. 새 Xen 관측·전체 이미지 hash가 필요한 것은 아님. [검증][read-context] |
| `xenova_drive_binding.observe` / snapshot | rejoin은 private binding observer 사용. snapshot은 기존 observation 읽음 | binding observer는 짧은 admission 전후 + Xen 관측·snapshot 게시·metadata fingerprint가 있음. snapshot은 과거 관측 시각/오류·나이 조건을 가진 캐시이며 현재 intent 원본이 아님 | 둘 다 새 metadata-only intent reader로 오인하지 않음. [binding observer][binding-observe] [snapshot][snapshot] |

일반 create 완료에서 `initialize(stopped)`는 intent가 없을 때만 쓴다. factory `initialize(running)`도 기존 intent가 있으면 그대로 반환한다. `retire/restore/purge`의 generation 변경은 intent를 현재 세대의 새 의도로 자동 승격시키지 않는다. 따라서 “intent 파일은 있음”과 “현재 적용 가능”을 분리해야 한다. 부재·잘못된 JSON·과거 세대를 autostart 값으로 덮어 정상값처럼 만들면 안 된다. [초기화][initialize] [세대 변경][transition] [applicable][describe]

## 3. 자격·설정·신호 공급/소비 대응표

| 정보/기능 | 생산 원본·소유자 | 일반 생성의 전달 시점 | factory의 전달 시점 | 실제 어댑터·소비자 | 현재 연결/미연결 및 주의 | 근거 |
|---|---|---|---|---|---|---|
| Pi MQTT 계정·ACL | 플랫폼 `xenova_broker.change`/`publish`; 승인된 `/usr/share/xenova/mqtt/policy.json` | create의 `_prepare/_create/_finish`에서 계정 발급 호출 없음 | `assemble`/`FirstBoot.register`에서 VM UUID별 broker 변경 호출 없음 | `xenova-broker initialize --vm`, add/remove-account, explicit import-legacy. profiles를 ACL로 펼치고 새 세대를 current로 게시 | **도구 제공·생성 자동 연결 없음.** 계정명 VM UUID와 `telemetry-UUID` 분리. 기존 계정 secret 재사용, pending/손상·부재를 초기 발급으로 바꾸지 않음 | [기본 계정/ACL][broker-profiles] [변경][broker-change] [재사용][broker-publish] [일반 호출][m-prepare] [factory 호출][fb-publish] |
| 자격 저장·실제 broker 반영 | `/srv/xenova/mqtt/generations/<tx>` + current, transaction/published fence. credential 원문은 root-private | 등록과 별도 운영 단계 | 첫 설치 등록과 별도 | broker unit의 check --runtime → mosquitto config. CLI execute 후 reload_broker 시도, 실제 client 인증은 별도 | **의도된 수명 차이.** 영구 VM 계정은 reboot/recovery 때 재발급할 대상이 아님. current 손실·저장 오류를 신규 설치로 해석하지 않음. 이번에 어떤 실제 credential 파일도 읽지 않음 | [권한][broker-perms] [계정 저장][broker-publish] [unit][broker-unit] [CLI][broker-cli] |
| private export | `export(value,name,form,output,execute)` | 계정 확보·게시 뒤 adapter별 형식 export 가능 | 영구 UUID 확정 전에는 최종 VM용 export 확정 불가 | agent-env/credentials/drive/telemetry 등. root 소유 private parent·새 absolute file, write 0600. export는 guest config/receipt를 쓰지 않음 | **제공 기능.** export 성공만으로 rootfs 전달·Store 적용·인증 완료를 주장할 수 없음. Linux env와 drive/telemetry Bootstrap 형식을 혼용하지 않음 | [export 계약][broker-export] [권한 저장][broker-perms] |
| Linux Agent 코드/공용 MQTT 설치 | 플랫폼 template builder + Agent 제공 소스 | `prepare-xenova-guests.make_template`에서 recipe.guest_agent → `guest_agent_template.prepare` → installer → template에 포함. 신규 create는 기존 template 복사 | 동일 template를 seed에 복사. seed 전용 identity unit/drop-in 추가 | guest `/opt/xenova/{agent,mqtt-client}`, Agent unit. template receipt의 guest_agent, verify_guest_agent | **빌드 연결 확인·선택 archive 실내용 미확인.** ordinary create 때 apt/Agent 재설치하는 경로 아님. 코드 설치와 credential 공급은 다름. Debian/Ubuntu 양 recipe에 guest_agent 정의 있음 | [template builder][template-build] [template Agent][agent-template] [installer][agent-install] [D recipe][debian-recipe] [U recipe][ubuntu-recipe] |
| Linux 일반 instance 신원·설정 | `prepare_rootfs.prepare_instance`; UUID는 원 create | private rootfs 준비 중 `/etc/xenova/instance-id`에 원 VM UUID, machine-id/SSH key 별도 생성. Agent identity gate는 seed 경로가 섞이지 않도록 검사 | 해당 없음: factory는 아래 별도 경로 | unit `ConditionPathExists`, `EnvironmentFile=-/etc/xenova/agent.env`; `AgentSettings.from_env`는 env VM_ID 우선, 없으면 instance-id | **신원 연결 확인·broker env 자동 전달 없음.** private env 연결 후보는 VM별 rootfs 준비 단계지만 해당 파일을 쓰는 현재 호출은 확인되지 않음. common template에 VM 비밀을 넣으면 안 됨 | [일반 rootfs][linux-root] [Agent 설정][agent-config] [unit][agent-unit] |
| Linux factory guest 신원·설정 | host identity plan과 guest PID1/guest_identity는 서로 다른 생산 단계 | 해당 없음 | host는 seed instance.json만 UUID/MAC로 확정·rename. guest는 최초 부팅 시 machine-id → UUID형 instance-id, SSH key → identity-complete | `prepare_deployment_seed` → guest identity service → Agent seed drop-in → 같은 AgentSettings/unit | **의도된 준비 차이 + 일치 근거 미확인.** host plan UUID와 guest instance-id를 직접 전달·대조하는 호출은 조사 경로에 없음. PID1/Xen UUID 유래로 실제 일치하는지는 target 수락 필요. 불일치가 재현됐다고 단정하지 않음. credential을 이 신원 확인 전에 공급하면 다른 계정/VM 결합 위험 | [seed rootfs][linux-seed] [host 게시][fb-publish] [guest UUID][guest-id] [Agent gate][agent-identity] |
| Zephyr 실행별 Bootstrap | `provision_xenova_telemetry.credentials/current/provision`, private root 설정·source identity | 자산 create와 분리. 실제 실행을 관측한 뒤 명시 CLI 또는 설정된 RejoinCoordinator가 수행 | 등록 완료→별도 start/autostart→동일 실행별 경로. FirstBoot 자체가 Store를 주입하지 않음 | `ProductionBootstrap`이 telemetry→drive 순서로 기존 credential 읽기, XenStore.apply; receipt_dir/VM/execution의 telemetry.json·drive.json | **제공 연결, 운영 입력 필요.** execution_id/host/domid/Xen unique/current intent 확인. 같은 실행 retry는 원 receipt/boot/app ID를 유지. 새 실행은 새 결합, 영구 계정은 같은 자격 재사용. applied는 Store 적용 근거이며 앱 READY 아님 | [실제 호출자][production-bootstrap] [원 receipt][provision] [Store 적용][store-apply] [현재 실행][provision-current] |
| Zephyr Store 소비·v2 Publisher | telemetry bootstrap loader, drive `xd_owner_prepare`/context | 실행 후 dom0 소유 Store 노드; 해당 guest read 권한. v1 drive/v2 telemetry는 독립 Publisher/socket | 같은 실행별 경로 | telemetry Publisher와 drive runtime은 각 channel bootstrap을 읽고 자기 VM·host·execution·receipt를 결합. drive runtime은 MQTT client 구성/구독 | **제공.** v2 Publisher 생존은 앱 실제 계산 progress가 아님. Store write 성공과 guest 수신/발행도 구별. 비밀을 instance 공개 응답·로그에 포함하지 않음 | [Store 권한][store-apply] [공통 runtime][drive-bootstrap] [공통 계약][mqtt-contract] [receipt payload][bootstrap-payload] |
| 실행·앱 binding과 재합류 | Worker의 선택적 `XENOVA_RECOVERY_DRIVE_CONFIG` → production/rejoin, `/run` binding view | 설정 VM만 주기 pump가 observe→provision/refresh→현재성 확인→binding cache. recovery 소유 실행은 Engine 완료 확인도 필요 | factory VM도 UUID/config가 공급되면 같은 경로. 등록됐다는 이유로 자동 설정되지 않음 | `mqtt.subscribe_liveness` verified guard → `_apply_readiness`; Gateway도 해당 binding/evidence 계약 소비 | **조건부 연결.** credential file/config는 미리 있어야 함. 최초 실행과 recover 완료를 모두 다루되 새 execution에 옛 binding 재사용 안 함. binding 있음=출력권/재무장 아님 | [진입 설정][worker-main] [설정 검증][rejoin-production] [work][rejoin-work] [pump][rejoin-pump] [verified 수신][worker-mqtt] |
| Pi/Jetson 두 broker 자격 | Pi broker 도구는 Pi 계정 저장·export. Jetson broker 계정/ACL은 해당 제공 책임 | drive credential의 선택 `world` 객체를 받아 실행 Bootstrap으로 공급 가능. create가 두 broker를 알아서 구성하지 않음 | seed/FirstBoot 자동 공급 없음. UUID 확정 후 두 자격 경로를 설정해야 함 | drive credential parser → bootstrap `world_*` → 별도 world receiver. Pi의 작은 차량·MANUAL 경로와 분리 | **수신/공급 형식 제공·자동 확보 미연결.** Pi export는 `world` 계정을 만들지 않음. Jetson 실제 계정/ACL·mapper 전달·운영 인증은 이번 미확인. world 부재를 MANUAL 전체 불가로 확대하면 안 됨 | [credential parser][credential-parser] [payload 변환][bootstrap-payload] [두 broker 계약][world-contract] |
| Linux 생존·앱 신호 | Agent `agent.heartbeat`, 앱 별 heartbeat/lifecycle, metrics/Telemetry | Agent 코드/identity/unit 설치와 실제 credential 인증 후 발행. Linux catalog는 Agent/앱 지원 신호를 정식 선언하지 않음 | 같은 구분. Agent unit enable이나 instance-id 있음만으로 수신 보장 안 됨 | Worker Linux liveness는 AGENT. 프로세스 metrics/Publisher/앱 heartbeat를 임의 VM restart 근거로 합치지 않음 | **의도된 신호 차이 + 선언 공백.** Linux Agent 설치가 실제 앱 진행 제공이라는 연결은 없음. 별도 서비스 기능 건강은 기존 담당 범위 | [Agent 발행][agent-heartbeat] [catalog][catalog-caps] [Worker 분류][worker-liveness] [MQTT 역할][mqtt-contract] |
| Zephyr app_heartbeat 기대 | catalog.describe의 capabilities + Worker template cache | Starter source는 명시 false. 그 외 현재 CAPABILITIES/NET_CAPABILITIES에는 true 선언이 없음 | current factory는 Starter false로 선택 | Observer가 template ID/version으로 선언 읽음. true=기대, false=미지원 선언, 부재/null=현재 구현에서 None, 잘못된 non-null 타입=capability_error. 실제 수신 armed는 별도 근거 | **확인.** true 소비 분기는 있으나 현재 packager가 앱별 true를 공급하지 않음. null과 missing을 엄격히 따로 오류 처리하는 producer/consumer 계약은 현재 없음. legacy 실제 heartbeat 수신으로 armed될 수 있으나 첫 무수신 기대를 대신할 수 없음 | [생산 capabilities][zephyr-profile] [catalog Starter][catalog-caps] [Worker 해석][worker-declared] [watcher][worker-liveness] |
| warm Standby·활성 계산 | drive app `warm_report` → app.lifecycle, 실제 worker completion | 명시 warm-drive-1 bootstrap/profile에서 앱별 instance/control/current completion 결합 | 동일. factory A/B라는 이름만으로 이 profile이 선택되지 않음 | WAITING/QUALIFIED와 EXECUTING/READY·progress를 별도 검증. Worker verified MQTT + supervision_pair, Gateway 준비/권위 별도 | **기존 제공·재사용.** 정상 대기를 계산 정체로 보지 않음. check_seq/주기만으로 계산 progress를 만들지 않음. 실제 적용 profile은 receipt/config/binding에서 확인해야 함 | [앱 생산][warm-report] [codec][lifecycle-codec] [Worker 수신][worker-mqtt] [감독 소비][warm-watch] |
| 최초 template/다음 firmware/현재 실행 | creation_origin, active_firmware, execution.loaded_artifact | firmware-transition은 정지 guard 후 active revision/profile 변경. 초기 template ID/version·creation_origin은 그대로 | FirstBoot 재확인은 현재 자산의 creation_origin을 확인하며 초기 firmware로 되돌리지 않음 | start 직전 검증 loader 입력을 loaded_artifact로 저장. 실행의 manifest/artifact hash는 현재 configuration과 따로 결합 | **세 수명 제공·기대 자동 연결 공백.** Worker `_intent`는 초기 template을 사용. 드라이브 설정 VM의 expected=True/app_state_models는 명시 설정이며 active firmware에서 자동 유도하지 않음 | [원본/현재 revision][zephyr-validate] [전환 writer][firmware-transition] [loaded artifact][launch] [FirstBoot 재확인][fb-verify] [Worker][worker-observer] |
| 실제 주행 firmware의 최종 초기 설치 선택 | build-xenova-drive-app → package-xenova-firmware → firmware + BUILD_RECEIPT | 별도 산출물. regular catalog descriptor로 게시·선택해야 일반 create 대상이 됨 | 현재 lock/defaults/example은 Network Starter. `copy_rtos_seeds` allowlist 및 FirstBoot current template 검증도 Starter를 전제로 함 | assemble이 선택 rtos template와 독립 seed를 이미지 데이터에 포함. 새 앱 제공 보고서는 이 경로의 선택 변경을 대신하지 않음 | **미연결 확인.** 앱 profile/state model은 warm-lattice-lab BUILD_RECEIPT에 있으나 generic firmware capabilities에는 실리지 않음. 이번에 선택/manifest/recipe를 수정하지 않음 | [앱 포장][drive-package] [manifest][firmware-packager] [receipt][drive-receipt] [선택][factory-config] [seed 허용][rtos-seeds] [첫 설치 검증][fb-register] |
| Spring 등록 통지 | 현재 생산/수신 구현 없음. 하위 등록 결과와 Spring DB는 별도 | `_finish` 원 성공 저장 뒤 의미상 후보. 실행 response/잠금 밖으로 전달하는 새 연결 필요 | registered[]/install complete 전후의 신규 확정 구분을 바깥 호출자에 전달하는 후보 | 기존 Spring POST는 생성 명령이며 이 기능의 consumer가 아님. Spring list는 DB에 없는 도메인을 포함하지 않음 | **미연결.** 이번에는 위치만 제안. endpoint·auth·DTO·dedup 수신 정책은 Spring 담당 결정. POST/자동 retry/수동 재전송 기능 모두 미구현 | [일반 완료][finish] [factory 완료][fb-publish] [main 잠금][fb-main] [Spring 저장/목록][spring-service] [Controller][spring-controller] |

### 현재 적용 신호 선언에서 특히 남은 두 연결

1. **일반 기대 원천의 시점 불일치:** `firmware-transition`은 active_firmware/profile을 바꾸지만 최초 template ID/version은 유지한다. `PlatformObserver._intent/_refresh_intents`는 그 최초 template로 catalog capability를 찾는다. 따라서 Starter→주행앱 전환 뒤 기대가 자동으로 현재 앱에 맞춰진다고 볼 수 없다. 반대 전환도 동일한 시점 문제가 있다. 명시 drive config의 VM 목록은 기대를 True로 지정하지만 설치한 bundle을 읽어 자동 증명하지는 않는다. 이것은 현재 소스의 연결 공백이며, 실제 보드에서 잘못된 복구가 발생했다는 재현 결과는 아니다. [전환][firmware-transition] [Observer][worker-observer] [명시 설정][worker-declared]
2. **manifest/프로필 전달 공백:** generic firmware manifest는 hardware/network capabilities를 담는다. `warm-lattice-lab`의 stateful AUTO/stateless MANUAL·schema·primitive·Bootstrap profile은 별도 BUILD_RECEIPT다. 그 사실을 현재 실행에 묶어 공개 기대 원천으로 제공하는 연결이 필요하다. receipt 내용을 읽지 않은 채 OS 이름/A·B 역할/모듈 존재로 기대를 추측하면 안 된다. [packager][firmware-packager] [receipt][drive-receipt] [현재 요청서][worker-request]

기존 `networked()`와 `validate_instance()`는 capabilities를 정해진 dict와 엄격히 대조한다. 따라서 후속 선언 연결은 instance에 `app_heartbeat:true` 하나를 임의로 추가하는 방식으로 끝낼 수 없다. 기존 hardware/network 판정과 앱 신호 선언의 생산·검증·소비 경계를 함께 맞춰야 한다. 이번에는 해당 schema나 validator를 바꾸지 않았다. [profile 판정][zephyr-profile] [strict 검증][zephyr-validate]

## 4. 후속 네 작업의 재사용·최소 연결·담당·시험

**아래는 제안이며 구현하지 않았다.** 새 전역 잠금·원장·cache·daemon·outbox·범용 schema를 정하는 단계가 아니다. 공통 원본은 유지하고 실제 비싼 작업이나 외부 효과를 필요한 adapter 시점에만 붙인다.

| 후속 작업 | 재사용 함수/원천 → 필요한 최소 연결 | 담당·선행 결정 | 정상/실패 수락 제안 |
|---|---|---|---|
| 1. 경량 의도 조회·CLI 정합화 | `load_records(False)` + `_state/read_tombstones` + `Execution.intent/describe`의 의미 및 `ReadContext.validate_one`의 변경 대조를 재사용. 공개 get/list→Xen observe를 중첩 호출하지 않는 metadata-only 진입을 후속에 정한다. 기존 wrapper→공통 CLI routing과 이름→UUID 해석 재사용 | 플랫폼. 출력 필드/단일·목록 범위는 기존 정식 계약과 최소 변경으로 확정. Worker의 소비 전환은 Worker 담당 | journal 없는 factory 포함 D/U/Z, stopped 등록·retired/purged, intent 없음/과거 generation/손상 구분. 동시에 stop/retire/restore·자원 변경 시 섞인 metadata를 정상으로 반환하지 않음. Xen/Store/asset hash/intent 초기화/observation 저장 0을 hook으로 확인. create 진행 중 public만 보인 상태는 성공 등록으로 표시하지 않음. [reader 경계][read-context] [현 CLI][cli-create] |
| 2. 공통 MQTT 자격 확보·전달 | 계정 `change/load/publish/export` 재사용. VM UUID·지원 역할·기존 계정을 먼저 확인하고 기존 자격 보존. Linux 일반은 private rootfs 준비 시 env, factory는 UUID 확정 후 최초 guest unit이 소비할 안전한 전달 시점, Zephyr는 실제 execution 관측 후 기존 Store/receipt로 연결. 한 함수에 세 adapter의 물리 준비를 합치지 않음 | 플랫폼이 계정·Linux template/Gateway·Zephyr adapter 연결 소유. Agent는 env 소비/기능 smoke, broker/Jetson 담당은 계정·ACL 승인/자격 제공. factory host/guest UUID 일치 확인이 Linux credential 결합의 선행조건 | 재전달/reboot/recover에 영구 자격 회전 0, 새 execution의 새 receipt/binding. 저장 미확정/정책 불일치/ACL 미반영/guest 전달 실패 구분. root-private·비밀 노출 0, 공통 template 비밀 0. 미설정이 무관한 VM get/stop을 막지 않음. [계정][broker-change] [export][broker-export] [receipt][provision] |
| 3. Spring 일회 등록 통지·수동 재전송 | 일반 `_finish`의 create SUCCEEDED 저장 후 확정 사실과 factory 신규 registered/complete 사실을 **외부 잠금이 풀린 호출 경계**로 전달하는 최소 hook 후보. `Manager._request`의 `_call` 반환 후는 물리 잠금 밖이지만 request_output/owner 정리 완료 경계도 확인해야 함. factory는 `main`의 operation_lock 블록 밖 후보. 통신을 `_finish/register` 안에 직접 넣지 않음 | 플랫폼은 확정 사실 공급, Spring/API 담당은 수신 endpoint·인증·DTO·UUID 중복 수신/DB 충돌 처리 결정. 목적은 등록만 반영; 기존 create/start 역호출 금지. 아직 새 API 이름/숫자를 확정하지 않음 | 새 등록 한 건, 동일 create replay/부팅 재검증/recover의 신규 통지 0. Spring 선발급 UUID의 중복 DB 생성·추가 start 0. 통지 timeout/거절에도 원 생성 응답·autostart·다른 VM stop이 대기하지 않음. 수동 재전송은 별도 명시 동작 제안. 일회 시도는 수신 exactly-once 보장이 아니며 crash 사이 전달 보장은 이번 원칙으로 약속하지 않음. [원 완료][finish] [바깥 request][request-boundary] [factory 잠금][fb-main] |
| 4. 현재 적용 신호 선언·최종 firmware 연결 | 기존 firmware source/hardware profile + BUILD_RECEIPT + catalog/active_firmware + execution.loaded_artifact + Bootstrap/lease binding을 연결. 앱 신호 계약을 검증한 작은 immutable metadata로 전달하고 최초 출처와 현재 적용을 분리. 새 대형 해시를 매 주기 붙이지 않음. 실제 firmware 후보를 선택하려면 lock/catalog/defaults/assemble allowlist/FirstBoot provenance 검증을 함께 대조 | 플랫폼·Gateway·자기 주행앱 제공물은 신영님 소유. Worker는 현재 선언 소비/첫 무수신 정책; Linux Agent 앱 건강, Jetson mapper, ESP32 제품은 각 담당. 시험 profile을 운영 차량값으로 승격하지 않음 | Starter↔실제 앱의 정지 전환, 재기동 전/후 기대 구분, 새 incarnation/app instance·오래된 binding 거절. true/false/미선언/무효 선언, Linux Agent와 앱/v2 생존 분리. warm 대기는 정상, 활성 progress 정체는 별도 제한. 최종 이미지 안 firmware/manifest/설치 record/loaded artifact를 대응하되 실제 적용은 별도 7번 수락. [현재 선택][factory-config] [현재 실행][launch] [warm][warm-report] |

### 통지의 신규 등록 판별에 이미 쓸 수 있는 것

- 일반 경로: `action=create`, operation_id, 영구 vm_id, status/phase, `creation_operation_id`, request context의 `replayed`. 다만 **replayed=false만으로 신규 확정이 아니다**. 수락·private 실패·계획과 원 성공 저장을 함께 구분해야 한다. 복구/reboot는 다른 action/원 실행 수명이다. [create 분기][m-mutate] [등록 대조][m-create] [원 완료][finish]
- factory: factory_id + identities + registered[] + phase, completed 재확인 분기가 있다. 완료 재부팅 때 registered[] 전체를 새 등록으로 읽으면 안 된다. 중간 rename 후 재개도 같은 영구 등록이며 임의 새 key/UUID 발급 사유가 아니다. 현재 구현에는 통지 시도/수신 확정 기록이 없으므로 전역 1회 전달 완료를 추론할 수 없다. [설치 진척][fb-identities] [완료 재확인][fb-publish]
- Spring: `VmService.create`는 DB UUID를 먼저 저장하고 launch를 요청하며, 같은 이름/명세는 기존 record를 재사용한다. 플랫폼-origin 등록의 DB 반영은 이 생성 명령과 분리해야 한다. 현재 `list`는 Spring DB에 없는 domain을 제외하므로 factory/로컬 생성이 저절로 화면 목록에 나타나지 않는다. [실제 Service][spring-service]

## 5. 확인한 빈칸과 의도된 차이

| 분류 | 판단·영향 | 다음 책임 |
|---|---|---|
| 확인된 미연결 | 일반 create/FirstBoot에서 broker VM 계정 확보→export→guest 설정 자동 호출을 찾지 못함. 현재 unit/코드의 credential 소비 기능과 별개 | 플랫폼 adapter 연결; 운영 계정/ACL 승인 |
| 확인된 미연결 | 현재 신호 기대의 일반 template 기준과 firmware 교체 후 active revision이 자동 연결되지 않음. current drive config는 별도 명시 설정 | 플랫폼의 현재 적용 선언 + Worker 소비 |
| 확인된 미연결 | 새 주행앱 bundle은 factory의 실제 선택 경로에 자동 편입되지 않음. Starter 선택·검증 제약이 여러 위치에 있음 | 플랫폼·자기 앱 제공물/선택 profile |
| 확인된 미연결 | platform 등록 통지 전송/전용 Spring receiver 없음. 기존 POST는 생성+기동 | 플랫폼·Spring 공동 최소 계약 |
| 의도된 차이 | 일반 asset create는 초기 stopped, factory 최초 등록은 초기 running intent. 부팅 autostart 설정과 실제 실행은 각각 별개 | 기존 계약 보존 |
| 의도된 차이 | Linux rootfs private 설정과 Zephyr 실행별 Store는 같은 전달 시점이 아님. VM credential과 execution/app binding 수명도 다름 | 기존 adapter 보존 |
| 잠재 위험·미확인 | factory Linux host UUID plan과 guest machine-id 기반 instance-id의 일치. 소스에 명시 전달/교차 검증이 없지만 실제 target PID1 동작으로 불일치를 재현한 것은 아님 | 플랫폼·Linux 설치본 smoke. 불일치 시 secret 공급 전에 바로잡을 최소 경계 결정 |
| 미확인 | 현재 선택 template archive 내부 Agent 설치 receipt·private env, target runtime 설정·실제 broker 계정/인증·Worker/Gateway 운영 config | 운영·설치본 수락. 이번에는 실경로를 열지 않음 |
| 미확인 | 새 firmware 실제 초기 SD 포함·보드 로드·신호 발행·실제 차량 진행 | 승인된 다음 설치/7번. 이번 source/이전 로컬 보고를 확대하지 않음 |

## 6. 조사·근거의 수준과 변경 기록

### 대조한 기존 자료

- [기능별 기준선][baseline-create]의 생성·등록/실행 원본·조회 대응표를 출발점으로 삼고 위 실제 함수와 대조했다. 기준선의 실제 경로는 `docs/verification/platform-management-baseline.md`다.
- [CLI/Manager 계약][cli-spec], [boot boundaries][boot-doc], [배포 준비 원본][deployment]을 참조했다. 부팅·원 요청·설치/실제 적용을 나누는 기존 모델을 유지했다.
- [최신 완료 보고][latest-report]는 주행앱/Gateway의 앞선 로컬 수락 이력으로 읽었다. 이번 조사에서 그 suite/ARM/RPM을 재실행하지 않았고 초기 설치 선택 완료 근거로 사용하지 않았다.
- [Worker 현재 요청][worker-request], [Linux 현재 요청][linux-request], [HTTP/Spring 현재 요청][http-request], [주행 설계][drive-design], [MQTT 계약][mqtt-contract], [world 계약][world-contract]을 연결 원본으로 참조했다. 이번에 해당 문서를 수정하거나 담당 제품 완료 상태를 바꾸지 않았다.

### 이번에 실제 수행한 확인

1. Git status·branch·HEAD 읽기, 첨부 지시문 읽기, `rg --files`/`rg -n` 호출자·writer·reader·Controller mapping 검색.
2. source를 줄 번호와 함께 읽고 Python AST로 함수 범위를 확인했다. helper는 제품을 import/실행하지 않는다. **정적 확인**이며 제품 동작 시험 통과와 다르다.
3. 기존 시험에서 연결 assertion의 위치를 확인했다. `test_xenova_first_install.py` RegistrationTest의 UUID plan/rename 중단 재개·완료 VM 재생성 방지, GuestIdentityTest의 machine-id/SSH identity 보존; `test_xenova_broker_accounts.py`의 재사용·중단·private export; `test_drive_rejoin.py`의 실행→provision→준비; `test_warm_liveness.py`의 WAITING/앱별 누락·stalled work 구분이다. **이번 재실행은 아니다.** [첫 설치 시험][firstboot-tests] [guest 시험][guest-tests] [계정 시험][broker-tests] [rejoin 시험][rejoin-tests] [warm 시험][warm-tests]
4. 작은 fixture가 필요한지 확인하는 과정에서 `wsl --list --quiet`가 `Wsl/E_ACCESSDENIED`를 반환했다. WSL 실행·제품 함수 fixture는 수행하지 않았다. 이번 결론은 소스·호출자 대조로 한정하고 런타임 일치·성능 수치를 새로 주장하지 않는다. 계정 발급/암호 해시 시험은 요청 범위상 실행하지 않았다.
5. 보고서의 repository 링크 경로·줄 범위와 인용 파일의 읽기 시점/마감 시점 SHA-256을 대조한다. `READ_SET.json`은 작은 소스/문서의 근거 목록이며 credential/template payload hash 목록이 아니다. 검사 결과는 아래 마감 기록에 남긴다.

### 수정·미수행 범위

조사 산출물은 처음에 저장소 밖 별도 작업 폴더의 `REPORT.md` 한 곳에 모았다. 이후 사용자의 작업 단위 커밋 요청에 따라 이 문서와 [인용 파일 근거 목록](evidence/vm-registration-signal-mapping-20261102.json)을 저장소에 보존한다. 읽기·작성 보조 스크립트는 커밋 대상이 아니다. 저장소의 제품 코드·시험·recipe·unit·정식 계약·진행 중 문서는 수정하지 않았다. 큰 이미지 읽기/해시·복사·빌드, 실제 VM/계정 생성, 보드·EC2·DB·broker·서비스 접속/변경, 통지 POST, commit/push/배포는 하지 않았다.

후속 구현은 자동으로 진행하지 않는다. 바로 이어갈 수 있는 범위는 **등록부/lifecycle/intent 원천을 재사용하는 경량 조회·CLI 정합화**이며, 실제 구현 전 해당 응답 계약과 동시 변경 assertion을 정하면 된다. 자격 자동 공급·Spring 수신·현재 적용 신호 및 firmware 선택은 위 네 작업표의 별도 연결과 선행조건을 따른다.


### 조사 마감 검증

- 참조 113개: 실제 파일 존재·줄 범위 확인. 인용 소스/문서 59개.
- 줄 단위 읽기 당시 hash와 겹치는 파일 및 초안 마감 후 인용 파일 hash 변화 0. 전체 payload/credential을 hash한 결과가 아니다.
- 마감 HEAD `206361b1b74994dccf26a3dccb32998ee8164487`, `git status --short` 출력 없음. 저장소 변경 0.
- 첨부 지시문 SHA-256: `1739db17eaaa565e3d2956b3f35bf9ea109cd5555223bf1fd5e57dfb490ce603`.
- 조사 때의 PC 원자료는 `READ_SET.json`, `FINAL_CHECK.json`이다. 저장소에는 재대조 가능한 [인용 파일 근거 목록](evidence/vm-registration-signal-mapping-20261102.json)을 함께 보존한다. 링크/후보 검증은 기능 실행·설치본 수락을 대신하지 않는다.
- 후속 문서 반영: 인용한 59개 파일의 바이트를 다시 대조했고, 113개 근거 링크를 저장소 상대 경로로 바꿨다. 조사 당시 commit/push 미수행 기록은 이력으로 유지한다. 이번 사용자 요청의 커밋 범위는 이 조사 보고서와 근거 목록이며 제품 구현·후속 연결·push는 포함하지 않는다.

[accept-create]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1520
[agent-config]: ../../platform/services/vm-agent/agent/xenova_agent/config.py#L72
[agent-heartbeat]: ../../platform/services/vm-agent/agent/xenova_agent/heartbeat.py#L63
[agent-identity]: ../../platform/guests/common/prepare_rootfs.py#L365
[agent-install]: ../../platform/services/vm-agent/agent/deploy/install_into_rootfs.py#L98
[agent-template]: ../../platform/guests/common/guest_agent_template.py#L111
[agent-unit]: ../../platform/services/vm-agent/agent/deploy/xenova-agent.service#L1
[assemble]: ../../platform/first-install/assemble.py#L599
[autostart]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L779
[baseline-create]: ../../docs/verification/platform-management-baseline.md#L123
[binding-observe]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_drive_binding.py#L9
[boot-decision]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L241
[boot-doc]: ../../docs/platform/management-boot-boundaries.md#L218
[bootstrap-payload]: ../../platform/scripts/provision-xenova-telemetry.py#L328
[broker-change]: ../../platform/deploy/mosquitto/scripts/xenova_broker.py#L447
[broker-cli]: ../../platform/deploy/mosquitto/scripts/xenova_broker.py#L589
[broker-export]: ../../platform/deploy/mosquitto/scripts/xenova_broker.py#L548
[broker-perms]: ../../platform/deploy/mosquitto/scripts/xenova_broker.py#L63
[broker-profiles]: ../../platform/deploy/mosquitto/scripts/xenova_broker.py#L177
[broker-publish]: ../../platform/deploy/mosquitto/scripts/xenova_broker.py#L346
[broker-tests]: ../../platform/tests/test_xenova_broker_accounts.py#L92
[broker-unit]: ../../platform/yocto/meta-xenova/recipes-connectivity/xenova-mqtt-broker/files/20-xenova-broker.conf#L1
[catalog-caps]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_catalog.py#L108
[cli-create]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management_cli.py#L94
[cli-route]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L1257
[cli-spec]: ../../docs/platform/cli-manager-spec.md#L33
[credential-parser]: ../../platform/scripts/provision-xenova-telemetry.py#L42
[debian-recipe]: ../../platform/guests/linux/debian-13-20261015/recipe.json#L1
[defaults]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/defaults.json#L1
[deployment]: ../../docs/platform/deployment-readiness.md#L19
[describe]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L668
[drive-bootstrap]: ../../platform/apps/common/xenova-drive-runtime/src/runtime.c#L257
[drive-design]: ../../docs/platform/drive-pipeline-architecture.md#L21
[drive-package]: ../../platform/scripts/build-xenova-drive-app.py#L185
[drive-receipt]: ../../platform/scripts/build-xenova-drive-app.py#L201
[factory-config]: ../../platform/first-install/example.config.json#L13
[factory-manifest]: ../../platform/first-install/assemble.py#L672
[fb-identities]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_first_boot.py#L259
[fb-main]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_first_boot.py#L378
[fb-publish]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_first_boot.py#L298
[fb-register]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_first_boot.py#L206
[fb-verify]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_first_boot.py#L190
[finish]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1046
[firmware-packager]: ../../platform/scripts/package-xenova-firmware.py#L35
[firmware-transition]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr_transition.py#L35
[firstboot-tests]: ../../platform/tests/test_xenova_first_install.py#L380
[firstboot-unit]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova-first-boot.service#L1
[guest-id]: ../../platform/guests/common/guest_identity.py#L30
[guest-tests]: ../../platform/tests/test_xenova_first_install.py#L481
[http-create]: ../../platform/services/dom0-manager/dom0_manager/api/vms.py#L41
[http-request]: ../../docs/requests/20261029-request-http-spring-ui.md#L1
[http-service]: ../../platform/services/dom0-manager/dom0_manager/service.py#L89
[initialize]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L138
[intent]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L127
[intent-writer]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L746
[latest-report]: ../../docs/verification/recovery-scenario-closure-20261030.md#L2545
[launch]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L319
[lifecycle-codec]: ../../platform/services/vm-agent/mqtt-client/xenova_mqtt/lifecycle.py#L29
[linux-instance]: ../../platform/guests/common/prepare_instance.py#L400
[linux-request]: ../../docs/requests/20261029-request-linux-agent.md#L1
[linux-root]: ../../platform/guests/common/prepare_rootfs.py#L291
[linux-seed]: ../../platform/guests/common/prepare_rootfs.py#L313
[linux-select]: ../../platform/guests/common/prepare_instance.py#L369
[m-create]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1146
[m-mutate]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1386
[m-prepare]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L877
[mqtt-contract]: ../../docs/contracts/mqtt-contract-v1.md#L286
[new-selection]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1466
[normalize]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L599
[observation]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L571
[platform-records]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L263
[private-complete]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1100
[production-bootstrap]: ../../platform/services/dom0-manager/dom0_manager/recovery/rejoin.py#L17
[provision]: ../../platform/scripts/provision-xenova-telemetry.py#L473
[provision-current]: ../../platform/scripts/provision-xenova-telemetry.py#L88
[provisioner]: ../../platform/services/dom0-manager/dom0_manager/drivers/platform.py#L707
[query]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1720
[query-route]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1821
[read-context]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L94
[registry]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L163
[rejoin-production]: ../../platform/services/dom0-manager/dom0_manager/recovery/rejoin.py#L244
[rejoin-pump]: ../../platform/services/dom0-manager/dom0_manager/recovery/rejoin.py#L153
[rejoin-tests]: ../../platform/services/dom0-manager/tests/test_drive_rejoin.py#L41
[rejoin-work]: ../../platform/services/dom0-manager/dom0_manager/recovery/rejoin.py#L120
[request-boundary]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1572
[rtos-seeds]: ../../platform/first-install/assemble.py#L227
[same-create]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L718
[snapshot]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L645
[spring-controller]: ../../backend/src/main/java/com/xenova/backend/vm/api/VmController.java#L25
[spring-service]: ../../backend/src/main/java/com/xenova/backend/vm/application/VmService.java#L61
[state]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L451
[storage]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L40
[store-apply]: ../../platform/scripts/provision-xenova-telemetry.py#L199
[template-build]: ../../platform/scripts/prepare-xenova-guests.py#L159
[template-lock]: ../../platform/guests/default-templates.lock.json#L1
[tomb-write]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1056
[tombs]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L1193
[transition]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1241
[ubuntu-recipe]: ../../platform/guests/linux/ubuntu-24.04-20260926/recipe.json#L1
[vms-unit]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova-vms.service#L1
[warm-report]: ../../platform/apps/drive/zephyr/src/main.c#L69
[warm-tests]: ../../platform/services/dom0-manager/tests/test_warm_liveness.py#L19
[warm-watch]: ../../platform/services/dom0-manager/dom0_manager/recovery/watcher.py#L320
[worker-declared]: ../../platform/services/dom0-manager/dom0_manager/recovery/observe.py#L89
[worker-liveness]: ../../platform/services/dom0-manager/dom0_manager/recovery/watcher.py#L253
[worker-main]: ../../platform/services/dom0-manager/dom0_manager/recovery/__main__.py#L54
[worker-mqtt]: ../../platform/services/dom0-manager/dom0_manager/recovery/mqtt.py#L123
[worker-observer]: ../../platform/services/dom0-manager/dom0_manager/recovery/observe.py#L32
[worker-request]: ../../docs/requests/20261029-request-recovery-worker.md#L78
[world-contract]: ../../docs/contracts/drive-world/README.md#L10
[zephyr-create]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr.py#L234
[zephyr-profile]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr.py#L15
[zephyr-validate]: ../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr.py#L190
