# 기본 VM 관리의 기능별 기준선

<a id="candidate"></a>
## 1. 후보와 읽는 방법

2026-10-31, `feat/platform/recovery-scenario-closure`의 **4a585d49a25d12459000306ffa74c42d0d473dcb**를
기준으로 한다. 착수 작업트리는 tracked diff/untracked 모두 없었다. 이 문서는 현재 구조를
설명하는 1번 마감이며 제품 최적화·운영 정책·설치본·제품 종단의 완료 선언이 아니다.

읽는 순서는 [개념 문서](../platform/conceptual-architecture.md)의 0~2·7·12장 → 이 문서의
필요한 기능 카드 → 정식 명세/소스 → [후보별 검증 보고](recovery-scenario-closure-20261030.md#원-create-기한실패-종결과-예약-반환)다.
개념·생명주기의 설명 원문은 [관리 구조](../platform/management-boundaries.md), 입력·응답은
[CLI·Manager 계약](../platform/cli-manager-spec.md), 부팅/종료는 [부팅 구조](../platform/management-boot-boundaries.md)에 둔다.
이 기준선은 세 문서를 대체하거나 새로운 schema·정책을 정의하지 않는다.

사용자가 본문으로 제공한 `XENOVA_CONCEPTUAL_ARCHITECTURE.md`는 위 개념 문서 경로에
수록했다. 0~11장 원문은 보존하고 12장만 현재 5번 적용 상태와 근거로 갱신했다.
P1의 USER_CONCEPT_ORIGINAL.md와 12장 전 본문 동일성을 확인하며 개념을 새 구현 계획으로 바꾸지 않는다.

5번의 `69387c1`은 당시 기반 HEAD다. 실제 검증 입력은 그 HEAD+제품/시험 diff·새 파일이었다.
현재 네 커밋 `1aa64ac`(제품) → `4334967`(시험) → `53c5077`(계약) → `4a585d4`(보고)와
P5 `COMMIT_MAPPING.json/FINAL_CANDIDATE.json/CANDIDATE-FINAL_SOURCE.json`을 대조했다.
문서 착수 전 봉인 25파일과 시험 snapshot의 platform 915파일이 모두 일치했다. 그 시점의
snapshot 뒤 차이는 최종 검증 보고서 한 파일이며 봉인과는 일치했다. 이번 문서 diff는
이후의 별도 후보로 P1에 기록하고 제품/시험 915파일의 불변을 다시 검사한다.

자료 위치는 다음 약칭을 쓴다. 외부 경로는 PC 원자료 위치이며 배포 경로가 아니다.

| 약칭 | 실제 위치·검사 범위 |
|---|---|
| P5 | `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage5/`: 최종 source/commit map·시험 JSON/log·S4/S5 assertion·부분 tar/manifest를 열어 대조 |
| P4 | `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage4-20261030/`: PAIRED_COST-FINAL과 3쌍 원 facts를 열어 이전 후보의 비용 조건 확인 |
| P1 | `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage1-closure/`: 착수 INPUT_COMPARISON, 문서/기호/불변성 검사, Network 기존 두 시험 보충 결과. 대규모 제품 회귀/성능을 새로 측정한 자료가 아님 |

<a id="sources"></a>
### 소스 기호

아래 링크는 모두 위 후보의 실제 소스다. 카드의 `M._create`처럼 표기한 기호는 이 표의
파일/클래스를 뜻한다. 문서 검증에서 파일·symbol·링크 줄을 대조한다.

| 약칭 | 저장소 경로·기준 기호 |
|---|---|
| M | [xenova_management.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py#L1377): Manager.call/fork, ReadContext, _mutate/_query/_create |
| P | [xenova_vm.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py#L257): Platform.request_control/records/start/stop_many/reboot/autostart |
| E | [xenova_execution.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py#L800): Execution.control/accept/observe_many/guard_launch/snapshot |
| C | [xenova_creation.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_creation.py#L259): policy/accept/work/validate/reconcile/released/holds |
| O | [xenova_preparation.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_preparation.py#L254): LinuxSlot/owner_state/ensure_quiet/drain, request_output/run |
| R | [xenova_runtime.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_runtime.py#L73): operation_lock/observation_slot/atomic_write |
| L | [prepare_instance.py](../../platform/guests/common/prepare_instance.py#L322): make_instance/_copy_verified/PreparedInstance.publish/Workspace.close |
| K | [xenova_catalog.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_catalog.py#L50): describe/entries |
| Z | [xenova_zephyr.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr.py#L234): new_instance/bundle_info/copy_revision |
| RC | [xenova_recovery.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_recovery.py#L81): Recovery.conditions/once/reconcile |
| CP·RS | [xenova_cpu.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_cpu.py) CPU.guard_stopped/require, [xenova_resources.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_resources.py) guards/mutate |
| GP | [xenova_guest_process.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_guest_process.py#L58): cleanup/scope_command/exec_owned |
| F·S | [Driver](../../platform/services/dom0-manager/dom0_manager/drivers/platform.py) SerializedManager/PlatformDriver/PlatformProvisioner, [Service](../../platform/services/dom0-manager/dom0_manager/service.py) VmService. 타 담당자 제품은 읽기만 함 |

<a id="concepts"></a>
## 2. 개념·권위 원본·담당 범위

다음은 기존 개념의 관리 경로 대응이다. 앱/OS 준비와 운행 권한을 VM.status에 합치지 않는다.

| 대상·식별/교체 범위 | 권위 원본·writer / 소비자 | 수명 끝·정식 계약 |
|---|---|---|
| 템플릿·산출물: template_id/version, source ID, manifest/payload hash | 조립/게시 도구가 catalog와 immutable template를 공급. K/M/L/Z가 검증 | 새 게시 버전은 새 산출물. 실행 신원과 별개. [Template](../platform/cli-manager-spec.md#template-fields) |
| VM 자산·현재 구성: 영구 UUID, 내부 이름, configuration hash, lifecycle generation | public instance.json+tombstone, M의 create/transition·RS/firmware writer. P와 상위가 읽음 | stop은 유지, retire/restore/purge는 세대 변경. 자원/firmware 구성 변경은 lifecycle 세대 유지. [신원](../platform/cli-manager-spec.md#contract-identity) |
| host 문맥·admission: boot_id/barrier_id와 현재 intent/lifecycle 결합 | E의 host-init/quiesce/resume와 VM admission. 시작·복구·게시 guard가 소비 | host 교체/새 gate에서 옛 단조 기한·허가 재사용 금지. [부팅](../platform/management-boot-boundaries.md) |
| 실행: execution_id와 domid/Xen unique ID·scope | E prepare_launch/bind_launch의 `/run` 기록+실제 Xen. P/RC/Bootstrap이 대조 | domain 교체 시 새 실행. domid 재사용·관측만으로 원 실행을 만들지 않음 |
| 사용자 의도: VM UUID/intent_revision/수락 operation | E.accept의 `/srv` control JSON. 실행·Watcher·상위가 소비 | 물리 실패에도 남고 후속 revision이 대체. stop이 옛 복구/launch를 차단 |
| 원 작업: asset operation / control operation / recovery attempt | 각각 M / E / RC의 서로 다른 journal. caller·Worker가 원 key/ID로 회수 | 완료·실패·미확정 이력 보존. 조회는 실행자가 아님. [원 요청](../platform/cli-manager-spec.md#acceptance-contract) |
| 관측/snapshot: host·실행·수명·원 관측시각/revision | E가 검증된 관측의 파생본 게시. Reader/화면은 만료·오류 구분 | 새 관측·만료·실행 변경. 화면 snapshot은 효과 제출 guard가 아님 |
| 실행 소유/일시 자원/논리 예약 | O의 실제 flock/상속 FD, workspace 자원 receipt, 원 operation 할당은 각각 다른 근거 | 실행 종료·mount/loop 정리·내구 예약 반환을 별도 확인. 원 기록 존재는 살아 있는 실행이 아님 |
| 명시 수습: 원 op/spec/context·안정 reconciliation ID | C가 원 asset operation의 제한 subtree만 기록/폐기. 권한 있는 caller가 명시 제출 | 원 실패는 보존, private 정리·내구 완료 뒤 활성 예약 제외. CPU 정책 예약 전체 반환은 아님 |
| 앱/Bootstrap·episode·주행 Authority | 실행별 receipt/binding, Worker episode/attempt, Gateway authority가 각각 소유 | Manager domain 성공≠OS/앱 준비≠Standby≠출력 허가. [건강](../contracts/health-evidence-v1.md)·[담당 요청 색인](../requests/README.md) |

신영의 직접 관리·Gateway 범위는 현재 요청 색인의 담당 표와 기존 소유 계약에 근거한다.
FastAPI/Worker/Agent/Spring/화면은 소비 경계이며 이번 수정 대상이 아니다. 자세한 공동
소유가 확정되지 않은 부트/네트워크 기반은 아래 색인에서 공유 영역으로 표시한다.

<a id="functions"></a>
## 3. 기능별 기준선

| 기능 | 하위의 정상 완료 | 혼동하지 않을 것 |
|---|---|---|
| 생성 | 선택 자산의 VM 등록·원 결과 확정 | Xen launch·OS/Agent 준비는 후속 |
| 목록/상세 | 지정 수명의 등록 정보+검증된 현재 실행 관측 | stopped 등록 VM은 자산 부재가 아님 |
| 시작/정지/재부팅 | 수락 의도와 물리 결과·원 journal 확정 | accepted/pending·현재 화면 상태·장치 적용은 별개 |
| 삭제/보관/복원/영구 제거 | retire/restore/purge의 해당 자산 수명 확정 | retire는 디스크 공간 반환 아님 |
| 원 작업 회수 | 해당 종류 원장의 원 결과/현재 진행 근거 확인 | 원 조회가 새 실행/새 기한을 만들지 않음 |
| 실패 수습 | 승인한 private 정리·내구 확인·해제 가능한 예약 반환 | 수습 성공은 원 생성 성공 아님 |

<a id="create"></a>
### 3.1 템플릿 확인·신규 생성

- **대상/책임:** CLI `xenova-vm create` 또는 M.call의 요청자가 UUID와 원 spec를 정한다.
  M이 `create:UUID`로 수락/할당/원장을 소유하고 현재 동기 호출이 준비·등록을 끝낸다.
  F.ensure_instance는 등록, F.create(Driver)는 start를 요청한다. 상위 HTTP 생성은 S.create에서
  둘을 순차 호출하므로 하위 create 완료와 HTTP launch 완료는 같은 사건이 아니다.
- **흐름:** M._mutate의 저비용 입력·K.describe metadata·용량/IP 검사 → O.LinuxSlot을 공통
  잠금 밖에서 획득 → 다시 key/자원/gate 확인·원 host/기한/할당 내구 수락 → M._prepare에서
  L.make_instance의 private 복사/guest 설정/검증/정리 → M._complete_private에서 현재 원장/
  등록부/manifest/예약/의도/gate와 결과를 대조 → PreparedInstance.publish → M._create의
  tombstone·DHCP → _finish의 stopped 초기화/원 성공. Xen start는 여기서 제출하지 않는다.
- **OS 차이:** [고정 template lock](../../platform/guests/default-templates.lock.json)의 Debian
  `debian-13-arm64/1.0.2`(source debian-13-20261015), Ubuntu `ubuntu-24.04-arm64/1.0.2`
  (source ubuntu-24.04-20260926)는 실제 선택 뒤 같은 L 경로를 쓴다. 배포 버전/source는
  분리되고 테스트는 해당 lock·sources에서 읽는다. 두 OS 모두 root/Image/initrd/boot.json/
  kernel.config의 실제 복사 stream 검증이며 prehash 0·root 검증 복사 1이다.
  Zephyr `zephyr-xenvm-starter-arm64/1.1.0`은 M._prepare→Z.new_instance/bundle_info/copy_revision의
  펌웨어/DTS/config 검증·revision 복사다. Linux 단일 복사 횟수/새 시간 정책을 일반화하지 않는다.
- **보호/저장/종결:** [공통 보호](#shared)의 슬롯·원 소유 FD와 private/public 경계를 유지한다.
  원 key/spec·UUID/MAC/IP·기한을 재발급하지 않는다. 신규 슬롯 경합만 명시 미수락;
  수락 저장의 실패/응답 유실은 미수락으로 단정하지 않는다. workspace 전의 지원되는 같은
  요청 재개에는 원 host/기한이 필요하고, 부분 private는 자동 재복사/게시하지 않는다.
  실패 이미지는 [3.6](#settlement), public 이후는 원 결과 회수로 분류한다.
- **근거/비용:** `test_xenova_linux_template_paths.py:DefaultLinuxTemplatePathTest.verified_path/failed_path`의
  양 OS 선택·계수·source 불변/독립 inode assertion, S3-T01/02/05/08/11, S5-T01/02/05/08.
  최종 P5 관련 suite에서 재실행됐다. payload는 축소 fixture이고 Xen/guest/mount 대역이다.
  Zephyr legacy 신규 등록은 ManagementTest의 실제 bundle 사례다. Network 신규 등록과
  협조 stop/reboot는 이번에 기존 NetworkPlatformTest 두 건만 보충 실행했다. 이 시험의
  bundle도 축소 fixture이며 고정 1.1.0 실물 전체 payload를 시험한 결과는 아니다.
  실제 고정 대용량 생성·OS 부팅 시간은 현재 후보 미측정이다. [생성 계약](../platform/cli-manager-spec.md#creation-contract).

<a id="query"></a>
### 3.2 목록·상세·기본 정보

- **대상/책임:** M.call의 list/get은 등록 자산의 수명과 현재 실행 관측을 결합한다. M._read_states가
  records/tombstone을 읽고 ReadContext가 요청별 문맥을 소유한다. E.observe_many가 현재 Xen
  목록 전후·대상 unique 두 확인을 수행하고 해당 VM의 파생 snapshot을 게시한다. M._view는
  검증 결과 조립이며 Xen을 재호출하지 않는다. Reader/화면은 실행 허가자가 아니다.
- **대기/변경 경계:** 공통 platform.lock 없이 네 관측 슬롯 중 하나로 외부 관측에 진입한다.
  같은 회차 목록·메타데이터를 재사용하되 게시 직전에 host/lifecycle/구성/원 create 성공/
  intent/run/admission을 대조한다. 변경이면 최대 2회차, 계속 변하면 오류다. 부분 열람·
  identity 오류·예산 소진을 빈 목록/부재로 바꾸지 않는다. 관측 시각도 지연된 옛 결과로 갱신하지 않는다.
- **소비 차이:** F.view_to_domain은 domid 없는 항목을 제외한다. 따라서 FastAPI list는 현재
  domain 목록이고 M.call의 list가 반환한 stopped 등록 VM을 그대로 보여주는 목록이 아니다. Spring의
  영구 VM record와 current runtime 조합은 또 다른 출처다. IP/Agent/실제 자원은 [정보 출처](#information)를 따른다.
- **근거/비용:** `test_xenova_read_progress.py` S4-T01(관측 호출·시각), T02(느린 조회 미해제 중
  B stop/원 조회), T03(양 OS 준비+1/2/4 폴러), T04~06(교체/부분 열람), T09~10(현재 guard/정보
  의미), T12(실행량·FD 수명). P5 최종 assertion을 열어 확인했다. [정식 조회 계약](../platform/cli-manager-spec.md#read-concurrency).

<a id="control"></a>
### 3.3 시작·정지·재부팅

- **대상/책임/경로:** M._dispatch_locked→P.request_control→E.control→P.start/stop_many/reboot.
  E.accept가 사용자 의도와 원 control request를 저장한다. P가 실제 Xen 효과와 소유 scope를
  수행하고 E가 신원/CPU·원 결과를 확정한다. start는 DHCP/실행 준비 후 xl create,
  stop은 현재 실행 확인→graceful shutdown/부재·자기 scope 정리다. Linux reboot는 기존
  toolstack 경로, 협조 가능한 Zephyr reboot는 별도 `xenova_zephyr_control.reboot` 경로다.
- **보호/원본:** 원 key/request hash·host/lifecycle·intent revision·execution/unique·CPU 정책·
  deadline을 결합한다. E.guard_launch는 제출 직전 registry/현재 의도/구성/실행/정책/gate를
  다시 확인한다. 표시 조회 결과로 이 guard를 대체하지 않는다. 같은 실행의 already-running
  start도 신원/CPU 검증 후 중복 create 없이 끝낸다. [CPU](../platform/cpu-layout.md)·[실행 계약](../platform/cli-manager-spec.md#acceptance-contract).
- **대기/종결:** 물리 제어는 공통 실행 잠금을 유지한다. M._accept_stop은 먼저 짧은 admission으로
  stopped 의도를 수락하고 그 잠금을 해제한 뒤 물리 잠금을 시도한다. pending은 정지 완료가
  아니다. 남은 stop은 원 caller/상위가 원 key·기한으로 조건부 이행한다. 일반 조회/Worker가
  자동 이행하지 않는다. 원 실행 기한 뒤는 제한적 효과 확인이며 새 물리 제출 예산이 아니다.
  executing/uncertain의 같은 요청은 실제 원 실행 효과를 대조하고 미확정 명령을 반복하지 않는다.
- **기록/근거:** `/srv` control-requests/control과 `/run` executions·observations·scope를 구별한다.
  완료 replay의 현재 조회가 실패해도 원 결과는 유지하고 vm/current_observation=null+warning을
  줄 수 있다. management_budget·reconciliation·boot_boundaries·S3-T06·S4-T02/03/T09가
  최종 회귀에 있다. 실제 Xen·프로세스 귀속/HTTP·화면은 별도 수락이다. [부팅/소유](../platform/management-boot-boundaries.md#vm-프로세스의-소유).

<a id="lifecycle"></a>
### 3.4 삭제·보관·복원·영구 제거

- **의미/수행:** CLI delete는 retire 별칭이다. M._transition의 retire는 stopped·현재 소유/
  미사용 확인 뒤 public 자산을 `.retired-<name>-<UUID>-gN`으로 이동해 숨김 보관한다.
  restore는 같은 보관 자산을 현재 등록/신원/MAC/IP·용량과 재검증하고 Linux는 e2fsck -fn도
  수행한다. M._purge는 RETIRED 소유 inventory·현재 미사용·inode를 확인한 영구 제거다.
  원 요청자와 M이 원 key/operation 및 tombstone 세대를 책임진다. 미게시 private는 대상이 아니다.
- **보호/저장:** 공통 잠금 안에서 source/target, 원 snapshot/expected generation과 실제 정지를
  대조한다. moving→publish→moved→tombstone generation+1→DHCP→complete 순서다.
  purge는 inventory·PURGING·remaining_assets를 내구 기록하고 dir_fd/O_NOFOLLOW·inode/
  단일 링크 확인 후 unlink/fsync한다. 실제 여유 delta와 논리 bytes·할당 blocks를 구별한다.
- **종결/회수:** 같은 key의 이동 후 재개는 새 자산/신원을 만들지 않고 정산한다. purge 중
  실패는 같은 inventory로 잔여를 확인한다. public/보관 충돌·다른 inode·사용 중이면 차단하고
  원 기록을 조사한다. retire 성공으로 디스크 여유 반환을 표시하지 않는다. 응답 유실 후 상위
  record 상태 회수와 restore 뒤 고정 retire key 재사용은 담당자 수락 전이다.
- **근거/비용:** `test_xenova_management.py`의 이동 중단·purge 오류/partial 재개·링크/사용 검사,
  `test_xenova_spring_followup.py:FactoryReservationTest`의 실제 factory MAC/IP·복원 검증을
  읽고 P5 최종 실행 목록/통과를 대조했다. 이번에 삭제/이동/새 시험은 실행하지 않았다.
  e2fsck·현재 사용 열람·unlink/fsync는 긴 공통 잠금 비용으로 남는다. [삭제 계약](../platform/cli-manager-spec.md#cmd-retire).

<a id="operations"></a>
### 3.5 원 작업 조회·재전달

- **서로 다른 원본:** asset `operations/<operation_id>.json`은 M, `control-requests/<operation_id>.json`은 E,
  `recovery-attempts/<attempt_id>.json`은 RC가 쓴다. 같은 이름의 operation ID만으로 원장을
  합치지 않는다. 원 key는 최초 제출 전에 caller가 보존한다. 조회 접근권한은 HTTP 담당자의 책임이다.
- **조회/대기:** operation/control-operation은 M._query의 완전 검색·재검색으로 유일성을 확인하고
  공통 실행/관측 슬롯을 잡지 않는다. 최대 2회차·각 회차 최대 2번 완전 검색이다.
  예산 소진=DEADLINE_EXCEEDED, 열람/소유 오류=해당 관측 오류, 손상/중복=수습 오류다.
  첫 일치를 유일한 원 작업으로 확정하지 않으며 조회가 원 기한·자산·예약을 고치지 않는다.
  recovery-operation은 현재 별도 dispatch로 공통 잠금을 사용한다.
- **현재 실행/종결:** asset 원 status는 SUCCEEDED 또는 RUNNING/RECOVERY_REQUIRED로 남을 수
  있다. 정확한 Linux 소유 FD를 확인한 원 준비는 effective_status=RUNNING, 원 cleanup은
  RECONCILING, 수습 기록 complete는 FAILED로 관측한다. 이는 VM 실행 상태가 아니다.
  완료된 원 결과는 현재 get 오류로 뒤집지 않는다. 같은 create는 정규 key이므로 호출자가
  임의 key만 바꿔 원 UUID 이력을 우회할 수 없다.
- **근거:** S3-T02/06/07/14, S4-T06/T08, S5-T03/08/10·strict CLI. P5 raw JSON에서 원 operation,
  수습 ID와 원 실패 replay/새 생성이 서로 연결되는지 대조했다. HTTP asset 조회·수습의
  제품 노출·권한/최소 DTO는 [기존 요청](../requests/20261029-request-http-spring-ui.md#원-create-기한과-실패-자산-수습-연결) 상태다.

<a id="settlement"></a>
### 3.6 생성 실패의 명시 수습·예약 반환

- **요청/권한:** `operation-reconcile`은 기본 읽기 진단, execute만 discard-private 실행이다.
  C.original/validate_request가 원 op ID 또는 key·VM·spec hash를 확인한다. 진단 context hash와
  안정 reconciliation_id를 명시 적용에 결합한다. ID/hash를 안다는 사실은 사용자 삭제 권한이
  아니다. root 하위 계약을 HTTP에 자동 공개하지 않았다. [정식 수습](../platform/cli-manager-spec.md#cmd-operation-reconcile).
- **실행/보호:** 기존 Linux 슬롯을 공통 잠금 밖에서 확보→원 실행/자산 사용 재확인→원 journal에
  계획 저장→잠금 밖의 제한된 inventory/usage→삭제 marker 저장→짧은 admission에서 현재
  gate/inode를 확인하고 해당 항목만 unlink/부모 fsync→진행 저장→잔여 사용/public/등록 충돌
  확인→완료/반환 내구 확정이다. 원 source가 손상돼도 소유 실패 사본 폐기용 재해시는 없다.
- **기록/종결:** 원 asset journal schema 1.1의 creation.version=1은 원 host/기한/정책,
  reconciliation.version=1은 원 수습·계획·planned/cleaning/complete·remaining/deleting/
  root_pending/error/release다. 원 spec·할당·최초 실패를 덮지 않는다. 실제 사용 종료, 이미지
  폐기, 완료 저장, 논리 반환은 각각 확인한다. raw complete의 가시성은 과거 fsync 성공 증거가
  아니다. C.holds의 예약 소비 writer가 파일/부모 sync·같은 기록을 재확정해야 활성 예약에서 제외한다.
- **지원되는 다음 행동:** [차단 표](#blocked)를 따른다. 같은 create key는 원 실패+수습 결과로
  끝나며 새 복사 0이다. 완료 뒤 사용자가 새 UUID로 별도 생성하면 현재 IP/용량 검사를
  거쳐 진행한다. 원 UUID 이력과 CPU 정책 전용 예약은 지우지 않는다.
- **근거:** S5-T07 SIGKILL 두 지점·새 프로세스 회수, T08 양 OS 실패→수습→원 실패 replay→
  새 생성/반환 IP 재사용, T09 정리 barrier 미해제 중 B graceful stop·정상 polling,
  T15 보이는 complete+fsync EIO의 예약 차단/회복, T16 CPU guard 회복. P5의 해당 assertion과
  실제 테스트 본문을 대조했다. 실제 운영 자산 삭제/전원 상실 시험은 아니다.

<a id="related"></a>
### 3.7 공유 guard를 사용하는 연관 관리

| 경로·원 책임 | 공유 경계·정상/실패 종결 | 근거·남은 조건 |
|---|---|---|
| update-resources / RS.mutate | 정지한 Linux의 원 configuration hash/lifecycle·stopped intent·자산/복구/실행 충돌을 검사. instance.json 게시와 원 asset 결과, UUID/자산 세대 유지 | resource suite와 CP 정책 문맥 검증. 자원 수량≠실제 사용량, 실행 중 hotplug 아님 |
| firmware-transition / xenova_zephyr_transition.mutate | 정지 Zephyr의 원 bundle/revision·IP/manifest/정책을 검사해 별도 firmware revision과 active metadata 게시. 원 UUID/lifecycle 보존 | Zephyr/lifecycle/recovery suite. 새 펌웨어의 앱/출력 준비는 별도 |
| CPU policy / CP.guard_stopped·set_policy·prepare | 정책 revision/hash·공장 identity·실제 pool/affinity와 host 준비를 확인. 미완료 자산 guard는 C.holds 소비, VM 전용 pCPU 예약은 별도 정책 | cpu suite/S5-T16. 정책 단독 오류를 VM 사망으로 판정하지 않음. 실제 배치·IRQ/I/O 격리는 별도 |
| host-init/autostart/quiesce/shutdown-all | E gate·새 boot/admission, P.autostart의 batch, O.drain/ensure_quiet와 guest별 종료. scope·backend/저장소를 보존할 단계 구별 | boot_boundaries/부모 중단·자식 FD·quiesce 회귀. 설치 unit·host 종료 실패/전원은 7번 |
| recover_once / RC.conditions·once | 현재 running intent/host/lifecycle·원 expected execution·CPU·attempt/기한·자산 소유를 확인한 한 번의 물리 복구. Worker가 판단·정책/후속 attempt를 소유 | recovery_foundation/review/reconciliation·S3-T04. 새 실행 관측 완료≠Bootstrap/Standby. D01 정책은 대기 유지 |
| recovery-reconcile / RC.reconcile | 원 attempt의 실제 효과·호출 종료·자기 scope·현재 실행을 확인한 범위만 종결. 진단/명시 execute 모두 새 물리 명령 0, 기한 재발급 0 | D03 정상/차단 사례 회귀. 증거 없는 stopping·다른 host는 private 폐기로 해결하지 않음 |

<a id="information"></a>
## 4. 기본 정보의 출처

| 항목 | 현재 원천·소비 | 현재성/부재 표현·주의 |
|---|---|---|
| OS·템플릿 | K.describe source.distribution·공개 ID/version·record.template/source ID. 실제 guest OS 이름은 Agent inventory의 osId/osVersionId/prettyName | guest_kind=linux는 Debian/Ubuntu 판별 자체가 아님. source는 산출물 출처이고 실제 현재 guest OS 탐지와 별개 |
| vCPU·할당 메모리 | M._view와 F.view_to_domain은 record/configuration의 vcpus/memory_mib. Spring VmResponse는 자신의 VM record 값 | 화면 수량을 현재 Xen 실제 배치/사용률로 부르지 않음. 실제 Xen 자원/CPU mask는 CP.layout·xentop 등 별도 관측·구간 |
| 플랫폼 IP | M._view record.ipv4, 원 operation의 assigned·공장 예약/lease와 별도 검사 | 예약/구성 IP이며 guest 응답·연결 성공 증거가 아님. Network Zephyr도 이 값이 있을 수 있음 |
| guest 보고 IP/OS | Spring AgentInventory.primaryIpv4와 observedAt/receivedAt. eth0 우선·loopback 제외 | Spring VmResponse.ipv4는 마지막 Agent 보고 주소, 정지 뒤에도 남을 수 있음. Zephyr는 현재 이 응답에서 null. 플랫폼 IP를 현재 보고 주소로 대체하지 않음 |
| Agent 상태 | AgentDataService.statusOf가 heartbeat receivedAt의 age를 설정 staleAfter/offlineAfter와 비교. 없으면 UNKNOWN | ONLINE/STALE/OFFLINE는 Agent 전달 상태. boot_id/agent_instance_id/seq와 Platform.execution_id의 검증 연결은 별도 책임·수락. 부재를 앱 사망으로 추정하지 않음 |
| Xen/실행 | E.observe_many의 전후 목록·unique·host/lifecycle·observed_monotonic_ms 및 /run execution | stopped/absent·외부 실행/신원 불명·조회 오류를 분리. 다른 host의 단조 시각을 직접 비교하지 않음 |
| 앱 준비·출력 | 하위 M._view는 app_readiness=unknown. 앱 어댑터/readiness·receipt/binding·Gateway authority가 별도 생산/판정 | running/blocked·Agent ONLINE·복구 완료에서 Standby/출력 허가를 추론하지 않음 |

상위 원천은 `backend/.../vm/api/VmResponse.java`, `agent/domain/AgentInventory.java`,
`AgentHeartbeat.java`, `agent/application/AgentDataService.java`를 실제 읽었다. 설치된 값·
화면 렌더링·전체 Agent 신원 검증을 시험한 것은 아니다. Linux·Agent D01-L01~L05는
[기존 요청](../requests/20261029-request-linux-agent.md)에 남긴다.

<a id="shared"></a>
## 5. 공통 잠금·저장·시간과 예약

### 보호의 실제 범위

| 보호·위치 | 획득/보유·수명과 정상 반환 | 중단/동시 요청에 대한 의미 |
|---|---|---|
| 요청 객체 `_request_lock` | 동일 M 객체의 동시 call은 무대기 거절. 별도 fork는 독립 가변 budget/trace, 동일 저장소/guard | thread/GIL만으로 보호하지 않음. fork는 새 프로세스 아님 |
| R.operation_lock `/run/xenova/locks/platform.lock` | 프로세스 공통 flock. Linux 수락/게시·원장 갱신, 다른 물리 제어/자산 변경은 실행 구간 유지. fd close로 반납 | 일반 list/get/operation/control-operation은 획득하지 않음. templates/probe/recovery-operation·retire/restore/purge 등은 기존 공통 경로. 무한 병렬 관리 아님 |
| E.short_lock admission.lock | 보통 공통 잠금→admission. 빠른 stop은 admission 해제 뒤 공통 잠금 시도. guest exec guard·gate·개별 private unlink와 직렬화 | 긴 자식/준비 종료를 admission 안에서 기다리는 역순을 만들지 않음. 호출별 deadline 없는 지점은 busy 즉시 거절, 있는 지점은 잔여 예산 대기 |
| O.LinuxSlot linux-prepare.lock | Linux create+discard-private 한 개. 공통 잠금 밖 무대기 획득, 수락 시 원 owner 연결 | 새 경합은 미수락·숨은 queue 없음. 같은 key 확인은 슬롯 경합보다 우선. 자식이 상속 FD를 유지하면 부모 종료만으로 반납 아님 |
| linux-owner-<op>.lock | 원 operation/spec/workspace/manifest·host/PID start_ticks·실제 FD inode를 결합. role=create/discard-private | active/residual/reconciling/absent와 관측 오류 구별. lock 파일 존재/임의 busy/PID만으로 소유 확정 금지 |
| observation-slot-0..3.lock | 일반 list/get 외부 관측 4개, 무대기. 소유 helper에 FD 상속. 정상 종료 뒤 같은 안정 inode 재사용 | 원장 조회/제어/Linux 슬롯과 별개. 모든 조회를 거절해 제어 시험을 통과한 것이 아님 |
| observation-<UUID>.lock | E.publish_observation/probe_error의 해당 VM 파생본 채택·필수 저장. registry/Xen/helper 대기는 밖 | 늦은 성공/실패가 최신 관측을 덮지 않음. 필수 저장 I/O 자체의 짧은 잠금 지연은 남음 |
| 논리 예약 | 원 operation 할당/미복사 약정량·공장/등록/보관/lease·CPU policy에서 각자 도출 | 물리 flock과 별개. deadline/FAILED/슬롯 반납만으로 반환 아님. C.holds는 해당 create 활성 예약만 판단 |
| host quiesce | E.quiesce가 gate를 닫고 O.drain은 공통 잠금 밖에서 같은 종료 예산으로 준비를 확인 | 슬롯 부재여도 workspace 자원 receipt 미확정이면 unmount 금지. 이력 파일만으로 영구 실행 잠금 보유는 아님 |

짧은 파일/개별 syscall도 저장 장치에 따라 오래 걸릴 수 있다. 이 표의 ‘짧음’은 보호할
논리 구간이며 실보드 최대 지연 수치가 아니다. 외부 root/xl과 모든 파일/Xen 효과를 하나의
원자 트랜잭션으로 직렬화하는 보장도 없다.

### 원본과 파생 저장

| 위치(기본 data=/srv/xenova, runtime=/run/xenova) | writer·저장 의미 |
|---|---|
| catalog·templates/rtos-templates | 조립/게시 산출물. manifest·source guard와 실제 사용 bytes를 결합 |
| instances/<name>/instance.json·tombstones/<UUID>.json | 현재 public 구성과 자산 수명. private/보관 경로와 구별 |
| operations/<op>.json | 원 asset 요청·할당·결과, Linux creation/reconciliation을 같은 JSON에 결합 |
| control/<UUID>.json·control-requests/<op>.json·recovery-attempts/<attempt>.json | 사용자 의도·원 제어·원 복구. 세 파일 전체가 하나의 원자 단위인 것은 아님 |
| instances/.operation-<op>/.preparation-resources.json | 일시 mount/loop/pending/clean 근거. 이미지·원 오류와 분리, 기본 자동 폐기 없음 |
| /run executions·observations·admission·host-admission·*.cfg | 이번 boot의 실행/관측/허가·렌더링 cfg. snapshot은 파생본 |
| cpu-policy.json / /run의 CPU 준비 | 정책 권위와 이번 host 적용 근거. 개별 stop/수습이 전용 정책 예약을 삭제하지 않음 |

R.atomic_write는 같은 부모 temp 쓰기→file fsync→replace→부모 fsync다. rename 가시성과
내구 성공은 다르다. E.accept의 여러 journal/intent도 같은 파일 하나의 트랜잭션처럼
설명하지 않는다. 오류 후 원 기록과 실제 효과를 회수하는 분기가 필요하다.

### 세 시간과 호환

`/etc/xenova/creation-policy.json`의 공급은 플랫폼 배포/운영 책임이다. C.policy는 정확한
schema/key·유한 양수·call>cleanup·파일 안전성을 검사한다. recipe의 null .example은
`/usr/share/xenova`에만 들어간다. **활성 정책 부재/무효에서는 새 Linux create를 수락하지 않는다.**
현재 부분 묶음을 새 생성 사용 준비 완료로 표시하지 않는다.

호출 예산은 진입/대기/실행/반환, 원 create 기한은 최초 내구 수락에서
`min(t_accept+execution_ms,D_call-cleanup_ms)`로 host와 함께 고정한다. 한 번 시작한
정리 여유는 같은 원 실행의 여러 단계에서 공유한다. 명시 수습은 현재 호출의 별도 기한이다.
call/실행 상한 86460000ms·정리 상한 60000ms는 입력 한계이고 보드 운영값이 아니다.
기존 control 90초·조회 15초·autostart 140초를 생성의 운영값으로 복사하지 않는다.

원 기한 없는 legacy 미완료/다른 boot는 새 기한으로 생성 재개하지 않는다. 완료 1.0 replay는
유지하고 현재 host의 명시 private 수습은 별도 사용/소유 확인으로 가능하다. 새 schema 1.1을
모르는 writer와 혼합 배포는 지원하지 않는다. 기한 뒤 새 복사/게시/DHCP 제출은 금지하되
이미 dhcp-applied인 public 원 결과의 등록/기록 정산은 현재 호출 예산으로 가능하다.
최신 stopped 의도를 초기 running으로 바꾸거나 launch하지 않는다.

### 예약 소비자

C.holds를 실제 호출하는 곳은 M._reserve_space/_ip_inventory/_accept_stop/_mutate,
E.control, RC.conditions, RS.guards, CP.guard_stopped, xenova_zephyr_control.reboot다.
M._control_view는 원 결과/현재 조회 응답 조립이며 holds 소비자가 아니다.
C.released(durable=True)는 workspace 부재·원 file/부모 fsync·동일 JSON 재확인을 수행한다.
용량은 현재 statvfs free와 미실현 약정량·sparse 할당 블록, 기존 등록 VM 증가분·2GiB 여유를
계산한다. IP는 현재 등록/보관/공장 예약/다른 원 작업/lease를 다시 확인한다. 원 UUID/MAC 이력
충돌과 CPU 전용 정책 예약까지 반환한 것으로 설명하지 않는다.

<a id="blocked"></a>
### 차단에서 다음 행동까지

| 근거/영향 | 추가로 필요한 사실·현재 다음 행동 |
|---|---|
| 새 슬롯 경합·accepted=false | 해당 신규 요청만 미수락. 원 key로 기존 작업부터 확인, 슬롯 반환 후 명시 재제출. 자동 queue 없음 |
| active/residual/소유 관측 오류 | 현재 원 실행/FD·사용 자원 종료 근거 필요. 원 작업/환경 조사. 종료를 입증할 지원 절차가 없으면 계속 차단하며 자동 kill/unlock 없음 |
| 부분 private·원 실패 | 진단/권한/현재 미사용 확인 뒤 동일 명시 수습. workspace 없는 원 실패도 삭제 0회 종결 가능 |
| public/등록 충돌·확인 안 된 DHCP | private 수습으로 해제하지 않음. 원 게시/외부 효과/의도에 맞는 결과 정산을 조사. 입증 불가 상태의 자동 종결 경로 없음 |
| 원장 손상·원인 불명 자산 교체/부재 | 신뢰할 원 소유/진행 근거가 필요. 기록 삭제/reset로 최초 상태를 만들지 않음. 현재 명시 수습이 모든 손상을 복구하지 않음 |
| 계획/완료 저장 ENOSPC/EIO | 공통 저장소 범위일 수 있음. 계획 전 삭제 0·반환 확정 0, 호출은 유한 종료. 저장소 회복 뒤 동일 수습 재확인. 다른 자산 자동 삭제 없음 |
| 만료·과거 host/legacy 미완료 | 새 실행 예산을 만들지 않음. 원 효과 조회, 조건이 맞는 private 명시 폐기 또는 지원되는 원 결과 정산. control/recovery와 private 수습은 별개 |
| 수습 complete·writer 내구 확인 | 원 실패를 계속 회수하고 활성 예약 제외. 사용자가 별도 신규 생성 요청 가능. 원 기록과 안정 lock 유지 |

<a id="cost"></a>
## 6. 비용과 반복 범위

아래 상한은 소스 조건/로컬 계수다. 현재 보드 성능을 새로 측정하지 않았고, 최적화는 5.5번이다.

| 단계·실제 작업 | 발생 단위·재사용/재검증 이유 | 확인한 범위·증가 요인/미측정 |
|---|---|---|
| K.describe/entries | descriptor마다 manifest hash+JSON·payload stat/boot 출처. 선택/준비/채택 경계에서 현재 manifest를 다시 대조 | Linux full=False: payload prehash 0, metadata hash는 있음. template 수/작업 수에 따라 스캔 증가. 조립의 full 검증·Zephyr bundle은 별개 |
| L._metadata/_copy_verified/_ranges | 한 준비의 입력 FD·identity를 결합하고 payload당 한 stream. 1MiB 단위 read/hash/부분 write·hole/다음 파일·반환 뒤 기한 확인 | 양 OS root 검증 복사 1·5 payload 각각 1, 사전 root digest/별도 cp 0. hole도 논리 0 전체 hash에 포함. logical bytes/CPU hash 비용≠물리 읽기/쓰기≠전체 생성시간 |
| guest 설정/Workspace | private 준비당 mount/loop·chroot·SSH/Agent 검사·정리. 명령 상한과 잔여 원 기한을 사용 | 요청별 stdout/stderr 64KiB 수집, data 잘림 오류. 실제 OS 설정/장치 비용 미측정. cleanup는 후속 동작 허가가 아님 |
| registry/tombstone JSON | ReadContext 초기 집합, list의 전체 집합 재검증, 게시할 VM별 최종 현재 파일 대조 | get도 초기 registry 전체 검증이 남는다. 등록/보관 수 증가에 비례. per-VM 마지막 대조는 전체 registry/helper 재호출 아님 |
| E._observe_many | 비어 있지 않은 회차당 xl list -v 전후 2회, 존재하는 대상 unique 전후 2회. 목록/unique 사이 실행 교체를 검출 | 한 요청 최대 2회차. list의 대상 N에 unique 최대 4N, 목록 최대 4회(회차 변경 조건). 빈 목록/시간/관측 실패로 더 일찍 종료. 고정 시간 비용 보장 아님 |
| M._query 원 key 검색 | 회차당 원장 전체 2회 검색, 뷰/소유 일관성 재검사. 늦은 중복 key·완료/소유 해제 교차를 검출 | 최대 2회차/4스캔, 원장 개수·JSON 크기에 비례. 첫 일치 중단 없음. recovery-operation은 별도 잠금 경로 |
| 현재 효과 guard/CPU·helper | launch/제거·bind·정산 경계의 신원·의도/정책/실행 재확인. 화면 문맥은 재사용하지 않음 | 중간 외부 효과/의도가 바뀔 수 있어 회차 내 응답 조립 재사용과 다름. 실제 호출 수는 action/분기/활성 CPU 정책에 의존 |
| R.atomic_write/필수 저장 | 각 journal/intent/자원 receipt·게시/완료 저장마다 file/parent fsync. 복사 chunk마다 journal fsync는 없음 | syscall은 엄밀히 선점 못 함. snapshot의 VM별 짧은 잠금도 저장 지연 가능. SD 내구·최악 지연 미측정 |
| C.holds/released | 미완료 자산 소비 guard가 호출. complete이면 file/parent fsync+원 JSON 재읽기, SUCCEEDED는 short-circuit | 소비자/원장 수에 따라 반복. 새로운 전역 cache로 생략하지 않음. 5.5에서 동일 근거 재확정 비용과 필요한 내구 경계를 따로 계측 |
| C.inventory/usage/reconcile | 진단/수습 진입·각 삭제 전·최종 사용 확인. 현재 Xen disk·mountinfo·loop·/proc FD, 남은 계획/inode, 항목별 marker/진행 저장 | 계획 최대128항목/직렬화128KiB·고정 topology. 프로세스/FD 수·파일 수·저장 횟수 증가. 각 시점의 사용/교체가 바뀔 수 있음. 전역 proc 보드 비용 미측정 |
| M._transition/_purge | stopped/usage·restore e2fsck·이동/삭제·DHCP·필수 저장을 공통 잠금 안에서 수행 | 긴 Linux private 분리와 별개로 남은 장기 소유 구간. 크기/파일/VM 수·장치 지연의 실제 비용은 5.5/7번 |

E.unique는 `xenova-domain-identity --domid`를 호출한다. 현재 recipe의 wrapper는 실행 가능한
`/usr/libexec/xenova-domain-identity-native`가 있으면 그 파일로 exec하고, 없을 때만 기존
Python 경로를 쓴다. native 실행 오류 뒤 Python으로 재시도하지 않는다. 이것은 source/recipe의
선택 규칙이며 현재 보드에서 어느 파일이 설치/로드되는지를 이번에 확인한 결과는 아니다.

P4 `PAIRED_COST-FINAL.json`과 여섯 raw facts는 동일 두 VM fixture의 기준선/당시 후보 3쌍이다.
현재와 동일 시간 측정은 아니므로 아래는 **4번 당시 관측**으로만 재사용한다.

| action | 외부 명령 전→후 | text read/JSON parse 전→후 | 중앙값 ms 전→후(n=3) |
|---|---|---|---|
| list 2 VM | verbose2·상세1·short1·identity4 → verbose2·identity4 | 12/17→28/32 | 31.122→33.805 |
| get 1 VM | verbose2·상세1·short1·identity2 → verbose2·identity2 | 10/13→14/16 | 19.636→17.278 |
| asset operation | 양쪽 Xen/helper0 | 4/4→4/4 | 2.162→1.579 |
| control-operation | 양쪽 Xen/helper0 | 2/2→4/4 | 1.590→1.730 |

목록 상세 호출은 줄었지만 metadata 읽기는 증가했고 모든 응답이 빨라지지 않았다.
위 text bytes는 임시 JSON 입력이며 물리 I/O량이 아니다. 새 5번의 holds/수습 비용을 이
과거 값에 합산하지 않는다. `477.203초`는 이전 **2회 처리 후보**의 한 Debian 보드 관측이다.
과거 설치본의 6분/12분 추정, Python/native 소수 비교도 현재 단일 복사 후보 전체 시간으로
쓰지 않는다. 현재 후보의 OS별 대용량 검증·복사·설정·Xen·화면 단계 시간은 미측정이다.

<a id="scope"></a>
## 7. 전체 소유·공유 플랫폼의 후속 범위 색인

이 절은 5.5번의 누락 방지 색인이다. 아래 영역을 이번에 새로 성능 감사/변경한 것은 아니다.
소유는 현재 문서의 책임과 이 작업의 승인 범위에 근거하며 폴더/과거 작성자만으로 확정하지 않는다.
표의 `recipes-*`는 `platform/yocto/meta-xenova/` 아래, `guests/apps/services`는
`platform/` 아래 경로다. 소스 기호 표의 Python 모듈도 같은 실제 파일을 참조한다.

| 영역·실제 경로 | 기존 설명/근거·기본 관리와 만나는 경계 | 책임/후속 범위 |
|---|---|---|
| `platform/guests/common`, `platform/first-install`·lock | [빌드](../platform/build-guide.md), [2~5번](recovery-scenario-closure-20261030.md): source/seed/UUID·게시 | 플랫폼 직접. 준비/조립/독립 seed 비용·중복과 정책 적용 범위 5.5 |
| `recipes-core/xenova-platform/files`·wrapper/unit | [관리](../platform/management-boundaries.md)·[boot](../platform/management-boot-boundaries.md)·[CPU](../platform/cpu-layout.md): 저장/실행/host | 플랫폼 직접. 본문 전 경로와 legacy 진입·필수 저장·scope/identity 5.5 |
| `platform/boot`, `platform/network`, `recipes-core/xenova-bringup` | [boot](../platform/management-boot-boundaries.md): Xen/bridge/DHCP와 guest 시작·종료 | 공유 플랫폼 기반. 세부 운영 소유는 개별 unit/변경 승인 확인; 하위 호출 경계만 5.5 |
| `guests/zephyr/xen-runtime`, `xen-store`, netfront·DTS/config | [runtime](../../platform/guests/zephyr/xen-runtime/README.md), [대기 검증](zephyr-wait-separation-20261026.md): 실행/Bootstrap·부분 초기화/종료 | 플랫폼 직접. Store 대기·통신·반납·메모리 상한을 기존 자료와 점검 |
| `apps/drive`, `apps/common/xenova-drive-runtime`, `xenova-mqtt-c` | [공통 펌웨어](../../platform/apps/drive/README.md)·[주행 설계](../platform/drive-pipeline-architecture.md)·[기반 검증](zephyr-drive-foundation-20261029.md) | 플랫폼 승인 공통 경계. 계산/준비/통신/종료 부하; 실제 planner/차량은 별도 |
| `guests/zephyr/diagnostics`, `telemetry` | [Telemetry](../../platform/guests/zephyr/telemetry/README.md)·[건강](../contracts/health-evidence-v1.md): 실행별 계측/원자료 | 플랫폼 직접. 수집/페이지/발행/보존·공간/stack 비용 5.5, 실계상 수락7 |
| `xenova_drive_binding.py`, `provision-xenova-telemetry.py`, receipt 도구 | [receipt](../../platform/guests/zephyr/telemetry/README.md#uncertain-receipt의-재주입-없는-적용-확인): 새 실행/인증·바인딩 | 플랫폼 직접 제공, Worker 소비 별도. 재확인/반복 Store·권한 수명 점검 |
| `platform/services/gateway` | [Gateway](../../platform/services/gateway/README.md)·[담당 표](../requests/README.md): Authority/lease·모드·출력/장치 제출 | 신영 직접. actor queue·준비/전환/시리얼·영구 저장 비용 5.5, 장치 수락 별도 |
| `platform/deploy/mosquitto`, `recipes-connectivity/xenova-mqtt-broker`, `mosquitto`, `tailscale`, `openssh`·xenova_wifi.py | [MQTT](../../platform/deploy/mosquitto/README.md)·[관리 사용법](../platform/cli-manager-spec.md): 인증/경로/부팅/영구 상태 | 플랫폼 패키지·공통 정책 연계, backend 도구 공동 원본. 실제 broker/Tailnet 정책 변경은 별도 승인 |
| `recipes-extended/xen/files/0002-console-filter-xenova-evidence.patch`·`xenova_telemetry_evidence.py`·`xenova_trace.py`·console collector | [실행 로그](../contracts/runtime-logs-v1.md)·[원자료/운영 프로필](zephyr-drive-foundation-20261029.md): 실행 귀속/보존·관측 간섭 | 플랫폼 소유 추가 경로. 기존 PTY/VM 간 비차단·회전/trace 비용 5.5 |
| `platform/yocto/meta-xenova`, `platform/scripts`·이미지 조립 | [빌드 가이드](../platform/build-guide.md)·[recipe](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/xenova-platform_1.0.bb) | 플랫폼 제공·공유 빌드 작업. source→패키지→설치/설정 단위; 진행 중 이미지 작업은 이번 비대상 |
| `services/dom0-manager`·Worker·Agent, backend/frontend·Jetson/ESP32/display | [요청 색인](../requests/README.md)·[VM HTTP](../contracts/vm-api-contract-v1.md)·[화면](../contracts/display-source-bindings-v1.md) | 타 담당자/공동 연결. Gateway 외 제품 소유를 추정해 대리 수정하지 않음. 6·7번 연결/수락이며 5.5 직접 수정 범위 아님 |

<a id="evidence"></a>
## 8. 재사용한 보장과 근거 수준

| 근거 | 실제로 대조한 assertion/조건 | 재사용/한계 |
|---|---|---|
| 양 OS 공통 생성 | DefaultLinuxTemplatePathTest, source lock 선택·prehash0/복사1·원본 불변/독립 inode, 실패 후 guest 미제출 | P5 최종 29 suite의 argv/log와 현재 시험 바이트 일치. 작은 파일·외부 OS 대역 |
| 긴 준비→무관한 B 진행 | S3-T01/02/04/05/06/09/11/12 및 S4-T03 raw assertion | A root copy가 막힌 상태의 B 실제 하위 stop/원 completed, 같은 key/주소·게시·FD 보호. 모든 스케줄 무기아 아님 |
| 느린 조회/지속 polling | S4-T02 raw의 slow_alive/slow_released=false·B stop·원 조회, T03 두 OS/1·2·4 폴러, T04~T12 코드 | P5 최종 raw를 열어 조건 대조. Xen/Store 대역, HTTP threadpool·실제 QPS와 다름 |
| 실패 수습→정상 복귀 | S5-T07/08/09/15 raw·test 본문: same rec ID/cold process/new create, 실제 SIGKILL, 정리 미해제·polling 성공/stop completed | 전역 proc는 시험 자기/부모 실제 FD로 제한. mount/loop/Xen은 대역. 전원 상실/운영 삭제 아님 |
| 회귀 결과 | P5 `regression-candidate-final`: 696 passed, 별도 `manager-candidate-final`: 576 passed·기존 warning1 | 66 집중은 696에 포함, 합산 안 함. suite 수만으로 특정 행동을 입증하지 않음. 이번에 재실행하지 않음 |
| 이번의 좁은 보충 | `test_xenova_network_zephyr.py:NetworkPlatformTest.test_new_catalog_create_allocates_network_identity_without_start`와 `test_cooperative_stop_and_reboot_change_execution_not_assets`: 2 passed | P1 NETWORK_CHECK-fixed-path.json/log. 실제 Manager/자산·원 신원 보존·재전달 create 증가0/강제 destroy0, Xen/Store 대역. 고정 도구 경로 누락의 최초 환경 실패도 보존, 의존성 설치/제품 변경 없음 |
| 부분 포함 | P5 SUBSET_PACKAGE: Python33+비활성 example1, 11 import·mode/source/tar hash 일치 | tar 34항목을 이번에 다시 읽어 대조. BitBake/RPM/이미지/보드 설치·활성 정책 아님 |
| 과거 보드 | 보고서의 당시 helper/공간 부족/2회 처리 후보 시간 | 이번에 보드/EC2 원 세션 미조회. 과거 현장 원자료는 해당 보고서 기재이며 이번 현재 보드 확인으로 쓰지 않음 |

P5 초기 실패·수정 전 audit·inode 오탐·fixture 정책/경계 실패는 그대로 보존한다.
과거 실패를 성공으로 덮지 않았고 다른 후보의 최솟값을 묶어 개선율을 만들지 않는다.

<a id="followups"></a>
## 9. 불일치와 후속 인계

아래 한 표를 후속 기준으로 쓴다. ‘비용 의심’은 이번에 새 결함을 재현한 판정이 아니다.

| 구분·사실/근거 수준 | 영향·필요한 확인/수정 | 책임·단계·완료 근거 |
|---|---|---|
| 구식 표기: 관리 구조가 HTTP stop 전체 미연결로 설명 | 실제 F.request_stop/S.request_stop·api stop_vm은 key 경로 제공. 이 설명만 최소 정정, 기존 역사 요청은 보존 | 문서 정정1번. Spring 원 key/후속 이행 D02/D08은 6·7 대기 |
| 구식 기호: 5번 보고가 _control_view를 holds 소비자로 나열 | 실제 소비자는 _accept_stop. source call site 대조로 기호만 정정, 새 보호 구현 아님 | 보고 정정1번; P1 symbol 검사 |
| 구식 범위: DEADLINE_EXCEEDED 오류 표가 Linux create/수습을 누락 | 현재 C/명세의 기존 허용 의미를 표에도 반영. schema/코드 변경 없음 | 계약 설명 정정1번 |
| 구현/소비 불일치: F의 create 예산 allowlist와 주석·직렬화는 새 하위 지원 전 상태 | 하위 지원과 실제 HTTP 사용을 분리. create/operation-reconcile·원 key·RUNNING/RECONCILING/FAILED·vm=null 소비 필요 | 기존 HTTP 요청의 D02/D08·3~5번 절, 담당자6·실제 권한/timeout 인수7 |
| 구현/소비 범위 차이: F domain 목록 vs 하위 등록 목록, Spring의 마지막 Agent IP | 정상 계약 차이를 구분해야 함. 관측 부재를 자산 미등록/화면 삭제로 치환하지 않음 | 기존 기본 VM 관리 요청·A2/F4, 6·화면/설치7 |
| 설계/운영 미정: 유한 생성 정책 공급·보드 시간값 | 예시만 설치됨. 현재 새 Linux 생성 활성 전제 명시. 임의 숫자로 활성화하지 않음 | 배포/운영 정책 소유자6, 같은 후보 보드 수락7 |
| 비용 의심: holds의 소비자별 fsync/read, 반복 원장/registry·manifest/lease 스캔 | 내구 재확정/현재성 목적과 동일 근거 중복을 분리해 호출·parse/bytes/fsync/lock 계측. 지금 생략/최적화하지 않음 | 소유 플랫폼5.5. 동일 조건 정상 진행+오류 보호 비용 회귀 |
| 비용 의심: 수습의 항목별 inventory·전역 proc/Xen/loop 사용 열람 | 현재 사용/대상 교체 보호는 유지. 프로세스/FD·항목 규모 증가와 필수 저장 비용 미측정 | 소유 플랫폼5.5, 실제 root 관측/SD 비용7 |
| 비용 의심: templates/probe/recovery-operation·retire/restore/purge/CPU의 공통 잠금 | Linux private 분리 밖 장기 구간은 남음. 읽기·효과·작업 소유를 혼동하지 말고 전체 색인에서 점검 | 소유 플랫폼5.5, 정상 조합과 원 key·저장/중단 보장 회귀 필요 |
| 증거 부족: 현재 대용량 생성/제어/수습의 실제 시간·부하·공정성 | 이번 자료는 로컬 대역·제어 시계·과거 비용. 현재 후보의 단계별 보드/OS·Xen·SD 계측 필요 | 7, 사전 비용 질문/실험 설계는5.5. 무제한 병렬/최악 지연 주장 금지 |
| 증거 부족: API cgroup/backend 귀속·host 종료 | 하위 GP.scope_command와 설치본 동작을 구별. API 재시작에서 guest 보존은 이번 통과 아님 | 기존 요청 ‘설치본 API 재시작과 VM backend 소유’,6·7 |
| 기존 미완료: Linux 기능 건강·Worker episode/후속 이행·Standby·장치 적용 | D01-L01~L05/W01~W05·D03-W01~W04·G2/AO3·H1/H3 원 책임 유지 | 담당 요청/정책6·제품/장치7. Manager 생성/복구 수락으로 닫지 않음 |
| 근거 범위: 사용자 제공 개념 모델과 실제 적용 | 0~11장 설명 틀은 보존하고 12장에 현재 후보·5번 제공·담당자/설치 미완료를 반영 | 1번 문서 대응. 개념 전체의 신규 구현/성능/장치 수락으로 확대하지 않음 |

<a id="closure"></a>
## 10. 1번 마감 판정

**기능별 현행 기준선 범위의 1번은 마감한다.** 기본 다섯 기능과 원 작업 조회·실패 수습을
세 OS의 진입/분기부터 정상/실패/미확정 종결까지 대응했다. 권위 writer·실행자·호출자
후속 책임·식별자 수명·잠금/소유/예약과 남는 비용을 설명할 수 있다. 직접 관리 경로의
원본/수명 자체가 설명되지 않는 구조적 빈칸은 남지 않았다.

사용자 제공 개념 본문은 보존하고 마지막 상태 장까지 현재 기준에 연결했다.
5.5/6/7의 비용·연동·설치 수락과 기존 담당자 미완료는 위 표대로 남긴다.
이번 변경은 문서와 P1의 읽기 검증 도구/결과뿐이다. 제품/정책/schema/API/시간값/잠금/
운영 상태/배포/보드/commit/push를 바꾸지 않았고 5.5 이후 작업은 실행하지 않았다.
