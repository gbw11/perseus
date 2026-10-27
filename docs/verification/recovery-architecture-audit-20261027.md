# 장애 대응·복구 아키텍처 정합성 감사

후속 [복구 상태 영속성·트랜잭션 경계 감사](persistence-transaction-audit-20261027.md)는 현재 병합 후보의 제품 바이트 동일성을 확인하고 S01 이후의 저장 관련 발견을 별도로 기록한다. 아래 D01~D09의 과거 판정과 원자료는 유지한다.
저장 관련 S01·S02의 후속 구현은 [파일 저장 마감 결과](recovery-store-closure-20261027.md)에 분리한다. D02·D03·D08의 종단 연동·수습 판정은 이 수정의 완료 범위가 아니다.
후속 [D03 하위 수습·인수 결과](reconciliation-closure-20261027.md)는 receipt/recovery의 제한적 성공·차단과 담당자 소비/Authority 복원 대기를 구분한다. 아래 감사 당시의 반례·미완료 판정을 덮어쓰지 않는다.

## 판정과 기준 후보

2026-10-27의 **감사·로컬 반례 검증은 완료**했다. 제품 종단 통합은 완료하지 않았다.
하위 Manager의 의도·실행·원 작업 보존, verified Gateway의 권한·준비 분리,
실제 Recovery Engine 이후의 재합류 연결은 로컬 근거가 있다. HTTP pending 전달,
Spring의 입력·재개·상태 소비, 미확정 작업의 운영 수습, 정식 배포에는 아래의 공백이 남는다.
안전하게 차단되는 결과를 운영 수습 또는 실제 차량 안전 수락으로 확대하지 않는다.

| 대상 | 고정한 근거 |
|---|---|
| 소스 | `cd86fd0652ac5b307b6c2735c25dc8363e75c801`. 착수 시 Windows/WSL 작업트리 모두 clean |
| 작업 브랜치 | `refactor/platform/recovery-contract-closure`. 이름만 변경했으며 HEAD는 유지 |
| 정식 계약 | [Manager/CLI](../platform/cli-manager-spec.md), [관리 경계](../platform/management-boundaries.md), [부팅·종료](../platform/management-boot-boundaries.md), [CPU 배치](../platform/cpu-layout.md), [v1 verified-drive-1](../contracts/mqtt-contract-v1.md), [Telemetry 2.1](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md) |
| Telemetry 완료 기준 | [최종 로컬 마감 결과](telemetry-measurements-20261026.md#로컬-마감-보완--2026-10-27)의 source manifest 81개를 현재 바이트와 대조. 모두 일치 |
| 공통 주행 기준 | [공통 주행·복구 결과](common-drive-integration-20261026.md)의 후속 계약 보완 및 최종 Telemetry 후보의 독립 회귀. 초기 분리 앱/vchan 기록은 현재 netfront 구현의 근거로 사용하지 않음 |
| 원격 대조 | fetch한 `origin/platform=e44636da7ebd15e68ff9d67432e3fc5a9eda9534`. 과거 공통 주행 MR !222는 병합됨. 최신 target을 작업트리에 병합하지 않음 |
| Telemetry MR | [!223](https://lab.ssafy.com/s15-final/S15P31E110/-/merge_requests/223), `feat(platform): Zephyr Telemetry 2.1 계측·출처·보존 검증`. 감사 결과는 이 MR에 push하지 않음 |

MR !223의 target 대조에서 `run_native_pair.py`, `test_drive_rejoin.py`의 add/add와
`xenova-platform_1.0.bb`의 내용 충돌을 확인했다. 검토 전 자동 선택·덮어쓰기는 하지 않았다.
이 감사의 고정 후보 통과를 충돌 해결 후 병합 후보의 통과로 해석하면 안 된다.

이번 저장소 변경은 이 보고서와 [감사 전용 반례 도구](../../platform/tests/audit_recovery_contract.py)뿐이다.
제품 소스·계약·설정·기존 시험은 변경하지 않았다. 보드 접속, 설치 상태 조회, 실제 서비스/VM/broker 제어,
전체 이미지 조립, commit/push도 하지 않았다. 비밀·실제 credential·private receipt 원문은 증거에 넣지 않았다.

## 증거와 시험 범위

공개 감사 증거는 PC의 `C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-recovery-audit/`,
WSL의 `/home/ssafy/xenova-recovery-audit-20261027/`에 보관한다.
`BASELINE.json`은 모든 기존 추적 파일의 해시, `AUDIT_PROBES.json`은 반례의 관측과 판정,
`DEPLOYMENT.json`은 패키지 대조, `NATIVE_V2_UNAVAILABLE.json`은 추가 native 시험을 기록한다.
도구의 종료 코드 0은 **감사 실행 성공**이며 모든 보장 충족을 뜻하지 않는다.

### 이번에 실제 실행한 검증

| 명령·결과 | 실제 제품 코드와 대체한 외부 경계 |
|---|---|
| `python3 -B platform/tests/audit_recovery_contract.py --output <저장소 밖>/AUDIT_PROBES.json`: 8개 probe 실행 | 실제 Watcher/Gateway/FastAPI/Driver/Manager/Execution/Recovery/Provisioning/Coordinator. Xen·Store·시계·입력 완료만 명시적으로 대체. 아래 발견도 기대한 감사 결과로 기록 |
| dom0-manager 디렉터리에서 `python3 -B -m pytest`: **380 passed**, 기존 Starlette 경고 1개 | 실제 Engine→Manager→provision 함수/receipt→binding→준비 시험 포함. 실제 Xen/Store 또는 Spring 서버는 아님 |
| gateway에서 같은 pytest: **84 passed** | 상태기계·권한 저장·바인딩·bounded drain·비동기 게시·serial 실패. 장치는 MockSink |
| vm-agent/agent에서 같은 pytest: **57 passed** | Agent 소스 회귀. 게시 템플릿/rootfs 설치 수락과 별개 |
| display/router에서 같은 pytest: **101 passed**, 기존 Starlette 경고 1개 | 표시 수신·freshness·정책의 기존 로컬 회귀. verified 제어 발행자 연결·실제 화면 수락 아님 |
| `python3 -B -m unittest discover -s platform/tests -p test_xenova_stop_acceptance.py -v`: **20 passed** | 실제 실행 잠금·프로세스 종료·동일 stop 재전달. Xen은 FakeXen |
| 같은 명령의 `test_xenova_recovery_review.py`: **21 passed** | 관측/신원/기한/host admission 회귀 |
| v2만 미접속인 native pair: **13단계 passed**, 28.688초 | 최종 common-drive native ELF, 실제 Zephyr thread·TCP·Mosquitto·Gateway. v1은 18884, v2 bootstrap만 비사용 18885. 원래 주행·명령→serial 인과 assertion은 유지. v2 수신 0 확인 |

native 추가 시험은 원본 `run_native_pair.py`를 변경하지 않고 network/mount namespace 안의
감사 전용 사본에서 실행했다. 정상 시험의 v2 수신 조건을 삭제하고 통과시킨 것이 아니라,
**v2 단절 주입 시험**으로 별도 식별했다. 원본 SHA-256은
`d593def9db196d3086adecfd5c98757599c48dfebda67a3b8ceb41ddf03e8d70`이다.
시험 사본·실행 인자·결과를 증거에 보관했다. 실제 Engine 재시작은 이 pair가 수행하지 않는다.

### 이미 실행한 최종 후보의 근거 재사용

동일 source manifest 및 최종 산출물 해시를 확인했으므로 다음 시험은 불필요하게 반복하지 않았다.
명령·실패 이력·고정 도구는 앞의 Telemetry 최종 보고서와 `ACCEPTANCE.json`에 있다.

| 근거 | 수락 범위와 한계 |
|---|---|
| 전체 platform 912개, 실패 0·기존 non-root 검사 skip 1 | 관리·신원·CPU·부팅/종료·패키징 회귀. 아래 별도 suite를 대신하지 않음 |
| MQTT Python 126 passed·외부 broker 4 skip, C MQTT ctest 1/1, controller 2/2 | v1 계약·계산/준비 어댑터. skip 항목은 제품 broker 수락 아님 |
| 실제 Zephyr v1 owner 27조건, lifecycle 9조건 | F01 창 확정·heartbeat 공정성·TCP_NODELAY·실제 진행·worker join·부분 초기화 실패·공통 종료 기한·잔존 표시 |
| 최종 native pair A 시작/B 시작 각각 13단계 | 같은 ELF `b0d31584776400abc5cc4b809431a97aaa5ace68dc3a5f115eb3040813d6118d`. AUTO↔MANUAL·대칭 승계·새 실행 Standby·재승계. 입력/Bootstrap/재시작은 harness가 통제하며 장치는 mock serial |
| native Diagnostics 8개 및 CPU 페이지 시험 | 2,147,983ms 가상 공백 후 stale→기준 재확보→2개 정상 window. 전체 thread 76/반환 32/-ENOSPC. 페이지 sample/window·누락·만료·충돌 확인 |
| 실제 Publisher→독립 subscriber 310개, source 73·CPU 44 | 2.1 validator, 25 CPU group 완전 수신. 합성 Golden만의 결과가 아님. 실제 transport 부분 누락은 이 run에 없고 별도 consumer/C 시험으로 확인 |
| 실제 patched Xen console 경로·openpty 2개 | 기록 I/O/잠금/포화/작업자 실패·종료·시작 불가에도 기존 PTY 바이트 유지. immutable BIND 귀속, unbound 기록 추측 금지. 물리 PV/IRQ/전원 상실은 미수락 |
| 부분 패키징 `bitbake -c package_qa xenova-platform xen-tools` | 3,071 tasks/3,055 cached, 통과·기존 경고 2개. 전체 이미지·설치본 수락 아님 |

Telemetry source archive SHA-256은 `c43e961d9055dafff1c909f81e67aaa5f9c15a23c8c25329c1f2180108d67cb2`,
증거 archive는 `ced99e28687cf7f891e4b1323957ed2a61fe6506aef17ebe27181c31de802236`이다.
증거 파일이 존재한다는 사실과 해당 후보에 적용된 시험이라는 사실을 해시 대조로 연결했다.

## 실제 책임과 상태 원본

| 사실·원본 | 생산자 / 소비자 | 유효성·복원 경계 |
|---|---|---|
| 영구 VM UUID·자산·configuration | 최초 등록·Manager / 실행·복구·공급 도구 | `/srv/xenova`; factory 등록 재개가 기존 UUID/의도를 덮어쓰지 않음 |
| desired_state·intent_revision | 사용자 관리 요청의 내구 수락 / 실행 직전 guard·Worker 재확인 | 짧은 admission 잠금과 긴 execution 잠금 분리. 현재 host/lifecycle/revision 대조 |
| control/recovery journal | Manager/Recovery / 동일 요청 재전달·조회·Engine | 원 operation/attempt·원 deadline. 조회는 실행기가 아니며 완료 저장 실패를 성공으로 바꾸지 않음 |
| 현재 실행 | Manager Execution / CPU·복구·Bootstrap·Gateway binding | UUID/host_boot_id/execution_id/domid/xen_unique_id. 새 host의 과거 근거 재사용 금지 |
| v1 boot/app instance·v2 Publisher session | 실행별 Bootstrap/applied receipt / 계정·토픽 검사 및 binding | 같은 guest boot namespace와 별개 app/Publisher instance. 게스트 본문 ID만으로 승인하지 않음 |
| 제어 generation·mode·선호 owner | Gateway Authority / retained control·상태기계 | `/srv/xenova/drive-control` 원본+초기화 표지. 단일 발급/원자 저장. retained는 사본 |
| 제어 전달·적용·기능 준비 | 비동기 MQTT 결과 / 앱 적용 보고 / 실제 앱 완료 어댑터 | PUBACK≠APPLIED≠READY. 이전 세대 생존 메시지는 적용 기한을 갱신하지 않음 |
| 출력 허가 | verified 상태기계 / 현재 frame 제출 guard | 프로세스 재시작 시 disarmed. READY·선호 owner 복원만으로 허가되지 않음 |
| 실행 binding 사본 | Coordinator의 신뢰된 실행 관측·receipt / Gateway·Watcher | 약 1초 관측, 3초 lease. 명시적 stopped/absent/unavailable/provisioning은 즉시 무효화 |
| 재합류 상태 | Engine 완료→Coordinator / 진단·준비 관측 | 플랫폼 COMPLETED는 새 Xen 실행. PROVISIONING→PREPARING→READY_OBSERVED는 별도이며 만료 시 PREPARING |
| v2 자원·UTC·crash | Diagnostics·Publisher·시각 공급·archive / 독립 진단 소비자 | UTC 품질 만료는 원 deadline과 무관. 과거 crash나 Publisher 생존은 현재 앱 복구 허가가 아님 |
| 최종 장치 적용 | 현재 Gateway에서는 미관측 | serial 전체 write 수락까지만 증거. `device_applied=null`을 실제 적용으로 바꾸지 않음 |

### 세 가지 연결 경로

1. **관리 경로:** HTTP→`VmService`의 VM mutex→`PlatformDriver`→프로세스 내
   `SerializedManager`→공통 Manager→짧은 의도 수락 잠금→실행 잠금/guard→Xen→필수 journal→응답.
   CLI는 공통 Manager로 직접 진입한다. Worker의 Manager wrapper는 별도 프로세스이므로
   FastAPI의 mutex를 함께 보유한다고 가정하지 않았다. HTTP stop은 선행 get 없이 shutdown으로 진입한다.
2. **제어와 복구는 병렬 책임:** Gateway는 현재 근거 소실 시 차단·준비된 반대 VM 선택을 수행한다.
   Worker Watcher→Engine은 별도로 필요 복구를 요청한다. `recovery/__main__.py`는 실제 Engine을 만들며,
   `XENOVA_RECOVERY_DRIVE_CONFIG`를 지정하면 Coordinator까지 연결한다. 과거의 “판단 큐만 있고
   110초 executor 미연결” 상태는 현재 결함이 아니다. 실제 Engine→Manager→실제 provisioning 함수→
   applied receipt→binding→준비가 `test_drive_rejoin.py`에서 연결된다. Xen/Store는 외부 대역이다.
3. **배포 경로:** recipe 설치→부분 package→image→설치본→환경·credential·authority 초기화→
   서비스 실행→제품 입력/화면/장치 수락이 각각 필요하다. 소스·package의 통과로 뒤 단계를 추정하지 않는다.

관련 구현은 [하위 실행/복구](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_recovery.py),
[Engine](../../platform/services/dom0-manager/dom0_manager/recovery/engine.py),
[Coordinator](../../platform/services/dom0-manager/dom0_manager/recovery/rejoin.py),
[verified 상태기계](../../platform/services/gateway/xenova_gateway/verified.py),
[Runner](../../platform/services/gateway/xenova_gateway/verified_runner.py),
[실행 binding](../../platform/services/vm-agent/mqtt-client/xenova_mqtt/drive_binding.py)에 있다.

## 보장별 대응표

`로컬 충족`은 해당 대체 경계 안의 판정이다. `부분`은 동일 행에 미연결·미수락 조건이 남는다는 뜻이다.
`격차`는 아래 발견 번호로 연결한다. 시험 이름은 생략된 경우 같은 행의 파일에서 확인할 수 있다.

| ID·개별 보장 | 계약·실제 위치 | 시험·원자료·판정 |
|---|---|---|
| A01 Linux 실행/의도적 stop/예상 밖 소실 | 관리 경계; `watcher.py`, `observe.py`, Manager Execution | 현재 Manager suite의 Watcher/Engine/intent 시험, stop 20개. 실행·의도 분리 **로컬 충족**. per-VM 전달 분류는 D01 |
| A02 Zephyr capability·최초 신호 무수신 | v1 capability; `watcher._judge`, catalog/Observer | `test_recovery_watcher.py`의 미선언/미지원·verified binding 시험, probe `delivery`. verified 최초 무수신은 UNKNOWN **충족**. legacy 범위 D01 |
| A03 broker/개별 인증·ACL/앱 실제 손실 | v1 관측; `heartbeats.LastSeenTable`, `watcher.py`, `recovery/mqtt.py` | broker/link 기존 시험, probe `delivery`: 타 VM 수신만 있는 Linux/legacy는 loss 판정. **격차 D01**; 실제 ACL 장애 제품 시험 미수행 |
| A04 v1 진행과 v2 정상의 독립성 | Telemetry 계약; v1 owner/Watcher 필터 | 기존 owner·Watcher 시험 + 이번 `pair-v2-unavailable`: v2 수신 0·주행 13단계 통과. **로컬 충족**. 실제 v1 계산 정체/물리 출력은 별도 |
| A05 복구 중 stop 수락·후속 이행 | CLI stop/pending; Manager `_accept_stop`, 실행 guard | stop suite의 pre-stop/removed/pre-create/post-create·실제 flock·프로세스 중단 시험 통과. HTTP probe에서는 intent 저장 뒤 정보 유실. **하위 충족/종단 격차 D02** |
| A06 응답 유실/재시작/중복 사건 | recover_once 원 attempt·기한; Engine/RecoveryStore | `test_response_loss_queries_journal_and_finishes_without_resending`, `test_worker_restart_restores_open_recovery_and_consults_journal_before_resending`. **로컬 충족**, 실제 systemd 재시작 미수행 |
| A07 미완료 stopping의 보호와 수습 | 관리 미확정 계약; `xenova_recovery.execute` | probe `stopping-owner`: 존재/부재 모두 원 deadline 만료, stopping 보존·추가 create 0. **보호 충족/운영 종결 격차 D03**. 관측 실패는 기존 review suite |
| A08 필수 완료 저장/부가 조회 실패 | 관리 완료·replay; Manager/Execution | stop suite의 completed busy replay/failed·uncertain 비승격, 전체 platform의 완료 저장 실패. **로컬 충족**. HTTP 경로의 vm=null/warning 보존은 D02 |
| A09 오래된 의도/실행·force 허가 | 실행·복구·CPU 계약; guard/Engine `_build_spec` | Engine stop/target-changed·회귀 21개. force escalation 기본 off, 하위 per-UUID 승인 필요. **충족**. 기본 Zephyr force-restart는 승인 없으면 거절됨을 운영 설정에 명시해야 함 |
| A10 이전 세대 생존과 새 적용 기한 | verified 전환; `verified._applied/step`, Runner delivery | `test_old_generation_liveness_does_not_extend_application_deadline`의 미전달/미적용, generation 고정. **로컬 충족** |
| A11 비선택 앱 NOT_READY와 적용 | v1 readiness; `verified._applied`, 앱 어댑터 | `test_auto_ready_waits_for_both_app_applications_without_generation_bounce` + probe `mode-readiness`. AUTO INPUT_STALE·MANUAL READY에서 MANUAL 운행, 적용 APPLIED·AUTO 준비 false. **충족** |
| A12 SAFE_STOP 반대 VM 준비·재무장 | verified 정지 복귀; `verified.py` | `test_safe_stop_can_select_other_vm_without_output_to_get_ready`·대칭 pair. **상태기계 충족**, 제품 입력 공급 D04 |
| A13 READY 중복/만료/이전 문맥/재생 | readiness 신선도·진행; Runner/BindingCache | `test_progress_duplicates_state_sync_and_expiry_are_not_ready`, retained 거절, owner 27조건. **로컬 충족** |
| A14 stateful adapter·입력 부재 | 앱 adapter 상태 준비; drive controller/adapter | controller ctest 2개와 readiness 시험. 계산 완료만으로 상태 동기화 미완료 승인 안 함. 현재 직렬 AUTO 지연은 MANUAL도 지연시킨다는 한계 명시. **로컬 충족/미래 앱 별도 수락** |
| A15 권한 단일 실행·재시작·원본 손상 | Authority 초기화/lease; `authority.py`, Gateway 진입 | `test_authority_loss_conflict_exhaustion_and_single_owner`, uninitialized/restart 시험. **보호 충족**. 원본 손상 운영 수습 D03 |
| A16 replace/fsync 미확정·롤백 방지 | Authority 내구성; `save`, `_persist` | `test_uncertain_parent_fsync_never_rolls_back_committed_generation`, 저장 실패 비발행. **로컬 충족**; 전원 상실 파일시스템 수락 아님 |
| A17 토픽/본문/계정/현재 실행 | v1 VM별 토픽·ACL·binding; C/Python parser·Runner·Watcher | MQTT suite, `test_current_execution_replaces_more_than_64_sessions`, native pair 실제 계정 ACL. **로컬 충족**, 배포 ACL D07 |
| A18 실제 Engine 후 재합류 | 플랫폼 COMPLETED≠Standby; Engine/ProductionBootstrap/Coordinator | `test_engine_completion_provisions_current_execution_then_standby_readiness`의 present/absent 두 경우. 실제 Manager/Execution·provision 함수, Xen/Store 대역. **로컬 연결 충족**, 보드/설치 환경 D07 |
| A19 Store 정체·receipt 미확정·lease 무효 | 실행별 provisioning; Coordinator/BindingCache | probe `rejoin-slots`, `uncertain-receipt`, `binding-invalidation`. B 갱신 유지·A 미종료 표시·즉시 무효화 **충족**. 실제 ctypes Store 장기 정체·프로세스 종료/재시작·수습 **부분 D03/D09** |
| A20 실제 수동 입력·중립·연결 단절 | v1 §7 legacy와 verified 준비; Spring `ManualInputRelay` | 실제 Java 조건 정적 대조. native 합성 입력은 제품 공급의 대체 증거가 아님. **격차 D04** |
| A21 resume 상관관계·재전달 | verified resume; Spring Requester/Runner | stale causation/retained 기존 시험 + probe `spring-resume-shape`: 현재 Java 형태 거절, 현재 문맥 결합 시 수락. **Gateway 충족/상위 연결 격차 D05** |
| A22 화면/API pending·권한·장치 의미 | Manager envelope/v1 제어; FastAPI errors·Spring DriveState/Tracker·frontend API | 실제 HTTP probe, 구독/모델 정적 대조. **격차 D02/D06**. 브라우저 제품 화면 실행 미수행 |
| A23 flood·비동기 publish·serial 실패 | bounded drain/출력 제출; `verified_runner.py` | `test_runner_bounded_drain_source_identity_and_serial_failure`, PUBACK 분리·scheduler pause 시험. **로컬 충족**, 장치 적용은 A28 |
| A24 초기화·복구·전환·계측 중 종료 | lifecycle/host admission/scope; 관련 unit·공통 quiesce | owner 27/lifecycle 9·stop 20·전체 회귀, 기존 실제 systemd 격리/P01. **로컬·기존 승인 대상 근거 있음**, 현재 후보 host 전원 종료 미수락 |
| A25 최초 등록·준비 실패·부분 시작 | 부팅 경계; first-boot/vms/cpu-prepare/vm-shutdown/host-admission | 기존 first-boot 재실행·autostart deadline/부분 성공·실제 systemd 격리. unit 순서 현재 소스 대조. **부분**, 새 이미지·실제 전원 부팅 D07/D09 |
| A26 Telemetry 2.1·UTC·진단 실패 | Telemetry 2.1/UTC 정식 정의; Publisher/console/archive | 최종 closure suite·독립 subscriber·이번 v2 단절 시험. **로컬 충족**, 제품 2.1 소비자 미연결 D06 |
| A27 동일 A/B·Agent·서비스·broker 배포 | build guide/recipe/defaults/assembler | `DEPLOYMENT.json`: host module 바이트 일치, ARM 번들 확인. Gateway recipe 부재·rejoin 선택 설정·broker 비활성·현재 최종 WIC 부재. **미연결/미수락 D07** |
| A28 실제 장치·상실·watchdog/E-stop | serial 제출/vehicle status의 구분 | Gateway는 실제 full write와 오류만 구분, `device_applied=null`. **미검증 D09**, 장치 승인 없이 시험하지 않음 |

## 확인된 발견과 최소 후속 작업

영향 P1은 제품 연결·복구 운영 수락을 막는 항목, P2는 진단·시간 계약의 보완이다.
확실성은 `재현`, `소스 확인`, `미수행`으로 별도 표기한다. 새 안전 정책을 이 감사에서 결정하지 않는다.

### D01 — 개별 VM 전달 불가와 heartbeat 장애의 구별

**P1 · 계약/장애 모델 격차 · 재현.** `LastSeenTable`의 채널 수신 근거는 전역이다.
probe에서 해당 VM의 신호를 한 번도 받지 않고 다른 VM만 수신한 뒤 Linux는
HEARTBEAT_LOST, legacy Zephyr는 APP_HEARTBEAT_LOST를 냈다. 반면 현재 binding이 있는
verified Zephyr는 최초 신호 전 OBSERVATION_UNAVAILABLE을 유지했다. 이 보완을 누락 결함으로 재등록하지 않는다.
한 번 관측한 VM도 이후 개별 ACL/인증 단절과 앱 진행 중단을 heartbeat만으로 구분할 수는 없다.

후속: Watcher·MQTT 담당자가 지원 프로필별 per-VM 전달 근거와 UNKNOWN/장애 판정 조건을
계약에 확정하고 최소 변경한다. `watcher.py`, `heartbeats.py`, `recovery/mqtt.py`와 관련 계약이 대상이다.
모든 무신호를 UNKNOWN 또는 장애로 일괄 치환하지 않는다. 기존 Linux/legacy 시험이 타 VM 수신을
정상 채널 근거로 삼는 목적도 함께 바꿔야 한다. 수락은 broker 전체 단절/특정 계정 ACL 거절/
실제 앱 정체/최초 무수신을 독립 주입하고 출력 보호와 복구 허가를 각각 확인하는 것이다.

### D02 — 저장된 stop 의도가 HTTP에서 단순 503으로 축약됨

**P1 · 소비자 미연결 · 실제 경로 재현.** `PlatformDriver.raise_for_envelope()`가
error.details를 버리고 `Dom0ManagerError`/HTTP 오류가 code/message/detail/platform_code만 전한다.
실제 실행 flock 중 별도 HTTP stop 두 번의 결과는 503, 물리 shutdown 0회였지만
의도는 stopped로 저장됐다. revision은 **2→3→3**, pending operation은 동일했다.
따라서 “매번 새 요청이 생김”은 재현된 사실이 아니다. 문제는 수락·pending·원 operation·
deadline·후속 이행 정보가 사용자에게 사라진다는 점이다. 다른 HTTP 명령이 잡은 VM mutex나
동일 프로세스의 직렬화는 수락 이전 대기일 수 있으며 별도 제한도 없다.

후속: Driver/errors/API DTO/VM service 및 Spring 호출 담당자가 기존 완료와
`accepted=true, pending=true, completed=false`를 구분하는 최소 계약을 합의한다.
조회·원 요청 재전달/이행 책임과 완료 replay의 `vm=null`/warning도 보존해야 한다.
새 daemon이나 완료 응답 의미 변경으로 대체하지 않는다. 회귀는 이 HTTP probe,
stop 20개, 외부 HTTP timeout 후 원 journal 확인·중복 물리 호출 0회, 상위 표시까지 필요하다.
하위 직접 CLI 수락을 HTTP 종단 완료로 보고할 수 없다.

### D03 — 미확정 상태의 차단은 유효하지만 지원 수습 경로가 부족함

**P1 · 의도된 보호 + 운영 종결 공백 · 재현/소스 확인.**

| 대상 | 확인한 보호 | 현재 지원 수습의 한계 |
|---|---|---|
| 원 recovery `stopping` | 중단 뒤 원 deadline이 지난 동일 요청은 도메인 존재/부재 모두 DEADLINE_EXCEEDED, journal 보존·추가 create 0 | 현재 공개 `recovery-operation`은 읽기 전용. 이번 조건을 종결하는 제한된 공개 재확인 절차를 확인하지 못함 |
| Bootstrap receipt `uncertain` | Store commit 응답 유실 후 receipt를 uncertain으로 보존. 재시도는 `uncertain receipt requires inspection`, Store 호출 총 1회·신원 불변 | 관측·원 credential·실행을 비교한 뒤 동일 receipt를 안전하게 확정하는 지원 CLI가 없음 |
| Authority 손상/유실/충돌 | 초기화 표지·원본·retained 충돌·세대 소진 시 출력 금지, 추측/rollback 없음 | 정상 최초 초기화 도구는 손상 원본 복원 도구가 아님. 유효한 백업/최고 세대를 확정할 근거가 없을 때 운영 절차 미완료 |

후속: 하위 Manager/Bootstrap/Gateway 담당자가 각 원본별로 **읽기 전용 진단→신뢰 근거 확인→
제한된 종결 또는 명시적 폐기 불가 판정** 절차와 필요한 최소 action을 먼저 검토한다.
원 기록 삭제, phase 직접 수정, 새 receipt/attempt 발급, 기한 자동 재발급 또는 force로 우회하지 않는다.
수락은 존재/부재/관측 불가, 응답 유실·프로세스 중단·동일 요청 재전달에서 원 IDs/revision/deadline과
물리 호출 수를 대조해야 한다. 현재 상태에서 종단 복구 지원 완료로 판정할 수 없다.

### D04 — 실제 수동 입력 공급이 정지·대기 준비와 순환할 수 있음

**P1 · 프로필 연결/정책 격차 · 소스 확인.** Spring `ManualInputRelay.manual()`은
vehicle/control이 MANUAL일 때만 입력을 허용하며, repeater는 눌린 키가 없으면 반환한다.
빈 키는 release 때 한 번 보낸다. verified 앱은 입력 수신 시각을 보존하고 200ms 뒤 stale로 본다.
따라서 SAFE_STOP의 MANUAL 준비, AUTO 중 MANUAL 승계 준비, 신선한 중립 입력 유지가
현재 제품 생산자만으로 충족된다는 근거가 없다. native 시험은 의도적으로 독립 입력을 계속 공급했다.
legacy §7의 기존 MANUAL 전용 규칙을 verified 준비 규칙과 혼용한 연결 문제다.

후속: Spring 입력/웹 세션·공통 앱 담당자가 연결된 실제 입력 세션의 신선한 중립 의미와
출력 금지 중 준비용 입력 공급을 합의한다. legacy 동작을 몰래 바꾸거나 빈 입력을 합성해
연결 단절을 정상으로 만들면 안 된다. 실제 WebSocket 연결/키 release/재연결/단절과 A/B
Standby·SAFE_STOP·AUTO→MANUAL 전환을 제품 경로에서 시험해야 한다. TTL 확대는 해결이 아니다.

### D05 — Spring resume이 verified의 현재 문맥을 전달하지 않음

**P1 · 소비자 미연결 · 소스 확인 + 실제 Gateway 반례.** `DriveResumeRequester.request()`는
payload의 requested_by만 생성하고 expected_generation/causation_id를 넣지 않으며 AUTO_READY만 허용한다.
현재 verified Runner는 현재 control generation과 게시 ID에 결합한 요청만 수락하고 disarmed
MANUAL/AUTO 재무장에도 이 계약을 쓴다. Java 형태를 복원한 probe는 거절됐고 현재 문맥을
결합한 요청은 수락됐다. **Java 서버 자체는 실행하지 않았다.**

후속: Spring `DriveResumeRequester`, `DriveStateTracker`, HTTP 요청/응답 및 frontend의
`api/drive.ts`가 현재 문맥과 원 요청 ID를 보존하도록 연결한다. 실제 control 게시 ID·generation의
출처와 응답 유실 재확인부터 합의해야 한다. stale/중복/retained/재연결 및 disarmed 재무장과
AUTO_READY 카운트다운을 제품 경로에서 수락한다. 현재 `send_resume.py --verified`의
제출 가능성을 웹 재개 기능 완료로 확대하지 않는다.

### D06 — 상태·진단 소비자의 새 계약 연결이 미완료

**P1/P2 · 소비자 미연결 · 소스 확인.** Spring `MqttDriveClient`/`DriveStateTracker`는
legacy `apps/+/events`를 구독하며 앱별 단일 상태를 저장한다. VM별 verified app.readiness
구독·A/B 실행별 준비, output_permitted/적용 단계가 현재 DriveState/웹 모델에 없다.
모드 AUTO만으로 실제 출력 허가를 설명할 수 없다. FastAPI pending 정보 유실은 D02다.
`AgentMessageHandler`의 토픽은 v1 heartbeat/inventory/metrics이고 Telemetry 2.1 제품 소비자는
연결되지 않았다. 2.1의 독립 subscriber 통과는 Spring/DB/화면 통과가 아니다.

로컬 표시 서비스 `xenova_display/mqtt.py`도 legacy 앱 토픽과 별도
`xenova/v1/display/drive-state`를 구독한다. `state.py`의 app_state=ready는 신선한 legacy
heartbeat에 대한 표시 상태이며 verified app.readiness와 다르다.
[표시용 주행 계약](../contracts/display-drive-state-v1.md)은 발행자/ACL 미연결을 명시하고,
현재 Gateway에는 이 별도 표시 토픽의 실제 발행 연결이 없다. 표시 서비스가 새 Gateway의
output_permitted를 보고한다고 가정하면 안 된다. 현재 표시 서비스는 VM/주행 제어를 실행하지 않는다.

후속: 소비자 담당자가 VM/실행/앱별 준비·선호 owner·출력 허가·제출/적용 미관측을 구분해
연결한다. telemetry 버전/단위/정수 범위를 v1 Agent 필드에 축약하지 않는다.
UI/API 실행은 미수행이므로 현재 화면의 구체적 오표시를 재현했다고 주장하지 않는다.
수락은 실제 Spring MQTT 구독→DTO→HTTP/WebSocket→화면, pending/uncertain/AUTO_READY/
AUTO-disarmed/Standby/장치 미확인의 각 사례다.

### D07 — 최신 소스와 설치·운영 구성의 연결이 끝나지 않음

**P1 · 배포 미연결/검증 공백 · 소스·부분 패키지 확인.** `DEPLOYMENT.json`의 결과는 다음과 같다.

| 대상 | 현재 확인 범위 | 미확인·미연결 |
|---|---|---|
| dom0-manager | 기존 고정 build packages-split의 Python 40개가 현재 소스와 동일. Worker/Engine/rejoin 모듈 설치. package RDEPENDS에 공용 MQTT·하위 플랫폼 포함 | 설치본의 환경 override·실행 상태는 조회하지 않음 |
| 공용 MQTT | package Python 7개 동일, RDEPENDS에 python3-paho-mqtt | 설치본의 실제 모듈 경로·broker 연결은 이번 감사 미수행 |
| rejoin | `drive-rejoin.example.json`과 Worker 진입 연결 존재 | 기본 env에 XENOVA_RECOVERY_DRIVE_CONFIG 지정 없음. 실제 UUID/credential/receipt/binding/authority 초기화 필요 |
| Gateway | verified 소스·단일 실행 잠금·초기화 도구 존재 | meta-xenova에 Gateway recipe/제품 unit 없음. 정규 이미지 자동 실행·장치 접근 배포 미완료 |
| Mosquitto | image recipe에 포함, 소스 bbappend의 auto-enable은 현재도 disable. VM별 v1 ACL 예시 존재 | 이번 build에 mosquitto pkgdata 없음. 실제 listener/계정/ACL/enable 상태는 미조회. “미설치”로 단정하지 않음 |
| Linux Agent | 현재 source 0.2.0. Paho/installer가 새 템플릿 준비에 연결됨 | 기존 게시 template/rootfs와 현재 Agent의 바이트 일치·enable은 이번 감사 미확인 |
| 기본 A/B | source defaults는 둘 다 network-starter 1.1.0. 공통 drive ARM 번들은 별도 생성됨 | 같은 최종 common-drive 번들을 A/B 자산에 편입한 최종 이미지·실행 증거 없음 |
| image/설치본 | 고정 build `build-rpi4-latest-image-20261026-121448`의 현재 deploy에 WIC 없음 | 다른 보존 이미지 전체가 없다는 뜻은 아님. 최신 후보 최종 조립·설치·전원 부팅 미수락 |

후속: 플랫폼 배포 담당자가 이 고정 후보의 **설정·이미지 포함 계획**을 먼저 확정한다.
외부 계정/비밀을 이미지에 넣지 않고 mount guard·authority 최초 초기화·credential 공급·
VM별 ACL·독립 A/B UUID/MAC/CPU·서비스 실행 책임을 연결한다. assembler는 호환 bundle ID를
선택할 수 있으므로 “공통 펌웨어 편입 자체가 불가능”하다고 판정하지 않았다.
Zephyr Engine의 force-restart 선택과 하위 force_restart_vm_ids 승인은 별도 운영 결정이다.
수락은 package→rootfs/image→각 A/B receipt→설치본 실제 module/unit/설정→입력/출력까지 단계별로 한다.

### D08 — 호출 예산은 일부 경로에서 상위 대기와 맞지 않음

**P2 · 시간 계약 격차 · 소스 확인.** 아래 값은 **소스 기본값/확인한 package** 기준이다.
현재 보드·배포 Spring 환경 override를 조회하지 않았으며 실제 적용값으로 단정하지 않는다.

| 경로 | 시작·대기·하위 예산 | 만료 뒤 의미·최소 협의 |
|---|---|---|
| Spring 조회→FastAPI→Manager | Spring connect 2s/read 6s. FastAPI 직렬화 최대 5s, Manager get/list 전체 기본 15s. `_view/_views`는 timeout_ms를 전달하지 않음 | 5s xl subprocess timeout은 Manager 전체 한도가 아님. 기존 Manager timeout_ms에 실제 잔여 예산을 전달하는 최소 상위 연결과 반환 여유 필요. 일괄 15→5 축소 금지 |
| 같은 VM HTTP control | VmService mutex의 획득 대기는 제한 없음. 그 뒤 SerializedManager 5s·Manager 기본 90s | 외부 100s를 mutex 전부터 사용하므로 5+90만으로 적합하다고 보장할 수 없음. 진입 시각/잔여 예산·pending 계약 D02를 함께 연결 |
| 일반 control | Manager 진입에서 원 deadline, 잠금/필수 검증/물리 실행/저장 포함. 원 요청 재전달은 재발급 안 함 | 기제출 결과 재확인의 제한된 15s가 물리 실행 허가를 늘리지는 않지만 반환 시간은 늘릴 수 있음. fsync는 강제 취소 가능한 시간 상한이 아님 |
| Spring command | 소스 read 100s. read timeout 후 command 자동 재전달 안 함. connect 이전 실패만 최대 2회 재시도 | 바깥 timeout≠하위 취소라는 설계는 유지됨. 원 작업 ID/조회 연결은 D02. 실제 소요 최대를 보장 상한으로 쓰지 않음 |
| Engine recover_once | 실제 executor 연결. attempt 시작 때 110s 절대 기한, 상한 120s. 큐 대기는 자기 attempt 시작과 구분. 같은 attempt 재전달 기한 불변 | verify 150s·transient retry 5s는 새로운 create/reboot 예산 아님. Worker 재시작도 원 attempt/journal 우선 |
| Watcher | tick 5s, intent refresh 60s, Linux loss/grace 30/90s, Zephyr 15/30s, absence 60s, health refresh/lease 15/45s | 장애 판단/부팅 유예는 실행 timeout과 별개. intent 캐시 뒤 판단 직전 get 및 하위 guard가 재검사. 경량 의도 조회 미사용만으로 안전 결함 판정 안 함 |
| Gateway | command 100ms, manual/perception 입력 200/300ms, readiness 300ms, ready hold/resume 3s, control apply 1s, binding 약 1s 관측/3s lease | 로컬 단조 시계·원 관측에 결합. 반복 메시지로 기한 연장 금지. tick 5ms는 최악 처리 지연 보장 아님 |
| service/batch/종료 | vms start 150s/내부 batch 140s, vms·독립 shutdown stop 110s, Worker stop 15s | VM별 새 전체 예산으로 batch 상한을 늘리지 않음. Store 미종료 thread와 systemd 최종 kill 경계는 D09 |

관련 파일은 `backend/.../vm/infrastructure/Dom0ManagerClient.java`, `application.yml`,
`dom0_manager/service.py`, `drivers/platform.py`, `recovery/policy.py`, 하위 Manager와 각 unit이다.
25s/120s를 합의된 HTTP 대기로 쓰지 않았다. 필요한 최소 변경은 호출 시작점·남은 예산 전달과
원 작업 반환 계약이며, 필수 검증·내구 저장을 줄이거나 실행 기한을 갱신하는 것이 아니다.

### D09 — 실제 Store·전원·장치·설치본 수락은 남아 있음

**P1 수락 조건 · 검증 공백 · 미수행.** Coordinator는 VM별 작업 슬롯을 나누며 probe에서
A의 Store gate가 막혀도 B binding은 갱신됐다. `stop()`은 미종료를 false로 표시했다.
그러나 실제 ctypes XenStore 호출 장기 정체, executor thread가 남은 Worker 종료와
systemd 15s 최종 kill→재시작→receipt 복원은 시험하지 않았다. 로컬 slot 통과로 이 경계를 대체할 수 없다.

부팅/종료 unit은 현재 소스로 대조했다. vms의 성공 VM monitor/backend는 execution scope에,
batch의 임시 프로세스는 batch cgroup에 속한다. vms start 실패 때 ExecStop이 생략되는 경우는
독립 `xenova-vm-shutdown.service`가 host 종료 정리를 맡는다. host admission의 quiesce→guest 정리→
scope/backend/toolstack·저장소 종료 순서이며 scope는 guest 종료 guard보다 먼저 정리되지 않도록 설정된다.
Wants/After CPU prepare와 실제 start의 host_boot_id/policy_revision·affinity 검사는 역할이 다르다.
active 표시만으로 CPU 준비를 승인하지 않는다. 최초 등록과 매 부팅 준비도 별개다.

근거는 [관리 경계 이력의 scope 수명주기](platform-management-boundaries-history.md#scope-수명주기와-b-증거의-범위)의
실제 systemd 255 격리·P01 및 최종 함수 대조다. 시험 unit stop은 제품 서비스 정상 종료나
현재 후보의 host 전원 종료 시험이 아니다. 외부 관리망·UTC·브로커 인증을 VM 부팅의 필수 online 조건으로
추가하지 않았고 KillMode/DefaultDependencies도 변경하지 않았다.

수락은 승인된 소유 VM/서비스와 별도 Store 실패 주입·정상/timeout/최종 kill·재시작,
최종 이미지 전원 부팅/종료, 실제 Xen CPU 계상·시각 공급·PV 전달·전원 상실,
실제 장치 ACK/명령 상실/watchdog/E-stop을 각각 수행해야 한다. serial 제출과 실제 적용은 계속 별도다.

## 운영 수습 절차의 현재 유효성

다음은 현재 제공되는 읽기 전용 진단과 조건부 원 요청 재전달의 범위다.
자동 운영 명령을 실행한 기록이 아니며 실제 환경 적용에는 별도 검토가 필요하다.

```sh
xenova-vm get <VM-UUID> --json
xenova-vm execution-context <VM-UUID> --json
xenova-vm probe <VM-UUID> --json
xenova-vm control-operation --operation-id <원-operation-UUID> --json
xenova-vm recovery-operation --recovery-attempt-id <원-attempt-UUID> --json
```

| 상황 | 가능한 확인·행동 | 완료로 간주하면 안 되는 것 |
|---|---|---|
| completed journal 뒤 현재 조회 실패 | 원 operation/attempt 결과와 현재 VM 조회를 각각 확인. 원 완료 replay는 추가 물리 작업 없이 결과를 보존 | 현재 조회 실패를 원 실패로 바꾸거나 새 key로 reboot |
| stop accepted/pending | 원 operation·intent revision·desired_state·host/execution·원 deadline을 확인. 유효 예산 안의 동일 key stop 또는 기존 recovery 후속 정리가 정지를 이행 | HTTP 503만 보고 효과 없었다고 판단, 의도 저장을 실제 정지 완료로 표시 |
| 수락 직후 호출자 종료 | 내구 intent/journal은 남음. 원 stop 재전달은 동일 operation/deadline을 사용. 조회만으로 물리 정지를 새로 실행하지 않음 | 호출자 소멸을 수락 취소로 판단, 자동 force |
| 만료된 stopping/uncertain | 원 요청·Xen 신원·관측 가능성·저장 상태를 보존하고 D03의 지원 종결 경로 필요를 명시 | journal 삭제/phase 수정/새 attempt로 owner 우회 |
| receipt uncertain/Store 미종료 | 해당 실행과 원 receipt를 보존, binding이 신뢰될 때까지 출력 차단. 정상 반대 VM은 자기 lease/준비로 별도 판단 | 새 boot/instance/receipt를 만들어 재합류 승인 |
| 정상 authority·현재 binding/준비의 disarmed 상태 | 기존 `platform/services/gateway/tools/send_resume.py --console --verified --host <broker>`는 현재 control 문맥에 묶어 요청 제출. credential은 안전한 환경에서 공급. accepted/적용/허가를 따로 확인 | 도구의 “submitted”를 운행 시작 또는 장치 적용으로 해석 |
| authority 원본 손상/retained 충돌 | 출력 금지 유지·원본/표지/백업/현재 세대 확인. 지원 복원 경로 D03 미완료 | 최초 설치 도구 재실행·낮은 generation 추정 |
| 앱 입력 NOT_READY | 실제 입력 경로·시각·세션·상태 준비를 확인. 플랫폼 실행 정상과 분리 | 입력 부재만으로 정상 VM 반복 재시작 |

`recover-once --spec-file <원-spec> --execute`는 원 attempt·host·deadline을 보존한 **같은 요청**에
한해 기존 계약에 따라 사용한다. 만료된 요청에 새 실행 권한을 주거나 force 승인을 대신하지 않는다.
특히 D03 상태에서 이 명령을 반복하면 자동 수습된다는 절차는 검증되지 않았다.

## 후속 마감 순서와 승인 조건

1. **D02/D04/D05/D06 계약 연결 검토:** pending·현재 제어 문맥·실제 입력 세션·출력 권한을
   상위 담당자와 확정한다. 기존 MQTT 전체/복구 체계를 교체하지 않는다. 최소 DTO·adapter·구독 연결과
   각 반례의 수정 전/후 시험, 기존 380/84/57 및 platform/owner/lifecycle 회귀가 필요하다.
2. **D01/D03 정책·운영 수습 확정:** 분류 불가능한 전달 장애와 미확정 소유권의 처리를 명시한다.
   새로운 판단 휴리스틱이나 state 편집을 먼저 넣지 않는다. 종결 근거와 원 기한/중복 방지 시험을 먼저 설계한다.
3. **D07 배포 후보 준비:** target 충돌 해결 뒤 source·package·firmware를 다시 봉인하고 동일 공통 번들의
   A/B 편입, 독립 신원/계정/CPU, Gateway/Worker/broker/authority 초기화·서비스 책임을 확인한다.
   현재 후보의 package 근거를 다른 merge 후보에 그대로 적용하지 않는다.
4. **D08/D09 종단 수락:** 실제 적용 시간 설정과 요청 예산을 연결하고 설치본/소유 VM/Store/systemd/
   전원/물리 장치를 승인된 범위에서 시험한다. native·부분 package·이미지·설치·차량 수락을 별도로 기록한다.

이번 감사는 위 항목을 구현하거나 제품 정책을 변경할 승인이 아니다. 상위 수정·배포·CPU/서비스·VM·
broker/장치·host/SD·force는 구체적인 영향·보호 대상·원복·수락 계획을 검토한 뒤 진행해야 한다.
감사 도구·보고서도 현재는 미커밋이며, Telemetry MR 제출과 구분한다.
