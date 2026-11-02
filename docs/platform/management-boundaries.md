# VM 관리 구조와 작업 수명주기

이 문서는 VM을 등록한 뒤 시작·정지·재부팅·복구할 때 플랫폼이 무엇을 검증하고
어느 시점의 결과를 반환하는지 설명한다. 명령 입력·필드·오류의 정식 정의는
[CLI·Manager 명세](cli-manager-spec.md), 서비스 순서는
[부팅·종료 구조](management-boot-boundaries.md)를 따른다.

## 목차

- [VM·실행·작업·의도](#vm실행작업의도)
- [요청 수락부터 완료까지](#요청-수락부터-완료까지)
- [검증 근거의 수명](#검증-근거의-수명)
- [일반 조회와 변경 작업의 진행](#일반-조회와-변경-작업의-진행)
- [완료·현재 조회·미확정](#완료현재-조회미확정)
- [복구 중 정지 요청](#복구-중-정지-요청)
- [시간 예산과 실제 반환](#시간-예산과-실제-반환)
- [제품 서비스 연동과 지원 경계](#제품-서비스-연동과-지원-경계)
- [진단과 검증 근거](#진단과-검증-근거)

## VM·실행·작업·의도

| 구분 | 설명 | 수명 |
|---|---|---|
| VM | 등록된 자산과 영구 UUID. 표시 이름·내부 이름·예약 MAC/IP·자원 설정을 가짐 | start/stop/reboot와 별개. retire/restore/purge로 자산 수명을 관리 |
| 현재 실행 | Xen에 존재하는 domain과 플랫폼 실행 기록의 결합 | 재생성 때 domid·Xen incarnation·execution_id가 달라질 수 있음 |
| 관리 작업 | 한 사용자 제어 또는 자산 변경의 원 요청과 journal | 원 키·operation ID로 진행·완료·실패를 조회하고 재전달 |
| 사용자 의도 | VM이 running/stopped여야 한다는 내구 상태와 revision | 물리 실행이 실패해도 수락한 의도는 남음. 후속 명령과 revision으로 순서를 구분 |

VM이 REGISTERED라고 실행 중인 것은 아니다. `xen_state=blocked`는 Xen의 대기 상태이며
장애라는 뜻이 아니다. `desired_state=running`이어도 실행 실패나 외부 변경으로 domain이
없을 수 있다. 실행 UUID와 실제 Xen 고유값을 대조한 결과를 함께 읽어야 한다.
정확한 필드는 [신원·VMView 사전](cli-manager-spec.md#contract-identity)에 있다.

dom0의 `xenova-vm`은 공통 Manager CLI와 기존 운영 CLI를 사용한다. Python 연동은
`Manager.call()`을 호출하며 FastAPI dom0-manager는 그 위의 별도 호출자다.
Manager는 HTTP 상태 코드나 웹 세션을 알지 못하고 같은 envelope를 반환한다.

## 요청 수락부터 완료까지

1. 입력과 등록 자산을 검증한다. 대상 UUID, 현재 host/lifecycle, 구성·정책을 확인한다.
2. 실행 작업은 공통 operation 잠금으로 직렬화한다. 충돌하면 무한 대기하지 않는다.
   stop은 이 긴 잠금보다 앞에서 짧은 admission 잠금으로 의도 수락을 시도할 수 있다.
   Linux 자산 생성은 아래처럼 긴 private 준비만 공통 잠금 밖에서 수행한다.
3. 원 요청 journal과 사용자 의도를 내구 저장한다. 의도 파일은 file fsync → rename →
   directory fsync를 거친다. 저장을 확인하지 못하면 수락 성공으로 반환하지 않는다.
4. 조치 직전 guard가 host·lifecycle·의도 revision·실행 신원·구성·CPU 정책·기한을
   다시 확인한다. 그 사이 저장된 새 stop은 아직 제출하지 않은 오래된 create/reboot를 막는다.
5. Xen 명령을 제출하고 실제 결과를 관측한다. domain 생성, guest OS 준비, 앱 준비는
   서로 다른 완료 단계다. start 성공은 domain 실행까지이며 앱 준비를 보장하지 않는다.
6. 필수 실행/CPU 검증과 완료 journal 저장을 끝내고 원 결과를 반환한다.

명령이 이미 제출됐거나 응답을 잃은 경우, CLI 종료·HTTP timeout을 취소 증거로 쓰지 않는다.
Xen이 명령을 받았을 수 있으므로 원 operation과 현재 실행을 대조한다.
현재 CPU 정책 오류가 있어도 신원을 확인한 대상의 stop/shutdown은 가능해야 한다.

### Linux 준비의 소유권과 게시

Debian·Ubuntu는 하나의 준비 슬롯을 공유한다. 기존 key의 결과·활성 실행을 먼저 확인하고,
신규 요청은 공통 잠금 밖에서 슬롯을 시도한다. 슬롯이 없으면 새 작업·예약을 수락하지 않는다.
슬롯을 얻어도 수락 직전에 원 key·현재 자원/주소·gate를 다시 확인한다.

원 operation과 예약을 저장한 뒤, 동일한 원본의 검증 결합 복사·guest 설정·필수 검증과
자원 정리를 private workspace에서 수행한다. Manager가 게시를 결정하기 전에는 등록된
VM 경로로 옮기지 않는다. 게시 직전 현재 원장·등록부·예약·manifest와 준비 결과의
신원/대상 경로를 대조한다. source guard와 실제 게시 사이에는 공통/admission 잠금을
유지한다. 복사 전의 등록부를 다시 덮어쓰지 않는다.

| 보호 대상 | 실제 소유·순서 | 해제·오류의 의미 |
|---|---|---|
| 공통 metadata/물리 변경 | `locks/platform.lock`. Linux 수락과 채택 때만 유지. 다른 VM의 물리 제어는 기존 직렬 경로 사용 | 긴 Linux 복사 자체가 목록·상세·다른 VM stop을 막지 않음. 모든 작업의 병렬 실행 보장은 아님 |
| Linux 준비 실행량 | `locks/linux-prepare.lock`. 공통 잠금 밖에서 무대기 획득한 뒤 공통 수락을 재검사 | 미수락 슬롯 경합과 이미 기록한 원 요청을 구별. 숨은 대기열/실행기는 없음 |
| 특정 원 실행 | `locks/linux-owner-<operation UUID>.lock`의 원 요청/host/프로세스 시작 신원과 잠긴 실제 FD 결합 | PID·파일 존재·다른 잠금 busy만으로 RUNNING을 확정하지 않음. 종료된 부모의 잔존 자식은 미확정으로 보호 |
| 게시·host 의도 | 공통 잠금 → `locks/admission.lock` 순서. 현재 gate/준비 admission 문맥을 확인한 채 게시 | quiesce는 admission을 해제한 뒤 공통 잠금 밖에서 drain. 역순으로 긴 작업 종료를 기다리지 않음 |
| private 자원 | `instances/.operation-<UUID>/.preparation-resources.json`에 mount/loop·미확정 할당·정리 결과 기록 | 슬롯이 없어도 자원 정리 기록이 미확정이면 저장소 해제 불가. 조회가 자원을 정리하거나 예약을 풀지 않음 |

원 준비 소유 FD는 준비의 외부 자식에도 전달한다. 부모 종료/응답 유실을 자식 종료로
해석하지 않는다. 같은 key/spec의 활성 재전달은 원 operation만 돌려주며, 다른 spec는
거절한다. 정상 완료가 내구 확정된 경우에는 실행 잠금의 뒤늦은 해제보다 원 성공을 우선한다.
정확한 `effective_status`·미수락·조회 오류는 [원장 계약](cli-manager-spec.md#operation-fields)을 따른다.

부분 실패의 원 기록·파일 보존과, 운영자의 명시 종결·정리·예약 해제는 다른 기능이다.
[operation-reconcile](cli-manager-spec.md#cmd-operation-reconcile)은 미게시 Linux private만
진단/폐기한다. 등록 VM retire/purge로 우회하거나 파일 삭제·원 ID 재발급·자동 force로
보호를 해제하지 않는다. 실제 운영 적용과 제품 HTTP 연결은 별도 승인/수락이다.

### 원 생성 실패와 예약 반환

원 생성의 host/기한은 최초 내구 수락에서 고정한다. 요청 진입·대기의 소비 시간을 제외하고
정책의 종료 여유를 남기며 원본 복사와 guest 설정·게시 허가에 같은 기한을 전달한다.
자기 실행의 자식/FD·mount/loop 정상 종료는 원 실행자의 책임이다. 실패 이미지 폐기는
보존한 원 operation을 참조하는 별도 명시 수습이며 조회가 자동 이행하지 않는다.

| 경계 | 기존 보호와 5번의 연결 |
|---|---|
| 실행량/소유 | 기존 단일 Linux 슬롯을 공통 잠금 밖에서 확보. create와 discard-private 역할을 구별하고 실제 상속 FD/소유 수명 유지 |
| 원 journal | 같은 operation의 schema 1.1, 버전형 creation/reconciliation. 고정 원 key/spec/hash/할당·최초 오류/원 결과 보존. 수습의 자기 진행은 새 요청으로 취급하지 않음 |
| 계획·개별 삭제 | 제한된 자기 private topology·inode 계획을 먼저 저장. 긴 사용 확인/열거/정리는 공통 잠금 밖. 삭제 직전 짧은 admission/gate·현재 inode 대조 |
| 수습 완료·할당 | 원 계획의 자산/사용 해소와 완료 기록을 확인. 그 기록이 읽힌다는 사실만으로 fsync 성공을 추정하지 않음. 실제 예약 소비 writer가 파일/부모를 재확정하며 실패하면 소비 금지 |
| 조회/다른 VM 제어 | 실패 대상의 원 기록을 조회하며 무관한 B의 정상 조회·실제 정지는 계속 진행. 공통 저장소 고장으로 근거 자체가 없으면 그 공통 범위는 명시 |

공통 예약 판정 `xenova_creation.holds`를 용량/IP·변경/제어·복구/CPU guard가 사용한다.
같은 원 UUID의 이력 충돌 보호는 보존한다. FAILED 하나·시간 경과·파일 부재만으로 반환하지
않고, 수습이 확정된 뒤 별도 신규 key/VM 신원의 현재 자원 검사를 허용한다. source 손상은
게시 허가와 소유 실패 자산 폐기에서 다르게 해석하며 폐기용 대용량 재해시는 하지 않는다.

기한 없는 구버전 미완료/다른 host의 생성은 새 기한으로 자동 재개하지 않는다. 현재 host의
명시 수습은 가능하다. 이미 게시·DHCP 적용이 확인된 원 결과는 제한된 정산으로 회수하고
최신 stopped 의도를 유지한다. 만료한 생성의 새 DHCP/게시/launch는 결과 회수가 아니다.

## 일반 조회와 변경 작업의 진행

일반 list/get과 asset/control 원장 조회는 공통 물리 실행 잠금을 기다리지 않는다.
읽기와 변경이 서로 무제한 병렬화되는 것은 아니다. 실제 제어·자산 수명 변경의 실행 잠금,
짧은 admission 및 Linux 준비 소유권은 그대로이며, 화면용 근거를 효과 제출 guard로 전달하지 않는다.

| 읽는 근거 | 읽기·채택 경계 | writer와의 관계 |
|---|---|---|
| 등록 구성·수명/대상 집합 | 기존 원자 JSON과 경로 검증. list는 외부 관측 뒤 집합/구성을 재확인, get은 대상 record/tomb만 확인 | 기존 원자 replace·private/public 게시·단조 수명/신원 규칙을 사용. 이동 중 불일치나 미완료 등록은 명시 오류/유한 재확인 |
| 현재 Xen 신원·상태 | 완전한 두 `xl list -v` 표본과 대상별 두 incarnation 관측. 같은 회차의 공통 목록을 재사용 | 외부 관측 동안 공통/admission 잠금 없음. 제어는 별도의 상세 자산/신원 guard 유지 |
| intent/run/host/admission | 요청별 입력 사본과 관측 게시 직전 현재 값 대조 | 기존 원자 파일이 원본. 바뀐 근거는 새 요청처럼 재포장하지 않고 최대 한 번 새 회차 또는 기존 오류 |
| 파생 관측 snapshot | VM별 observation 잠금에서 해당 대상의 입력 재확인·필수 저장 | 짧은 캐시 게시만 보호. Xen/helper나 전체 등록부 검색을 기다리지 않음. 실행 원장을 수정하지 않음 |
| asset/control 원장 | 완전 검색과 별도 회차 대조, 원 실행 소유권 확인 | 원장 조회가 공통/admission을 점유하지 않음. 정상 성공·현재 실행·미확인과 부재를 구별 |
| 읽기 실행량 | `observation-slot-0..3.lock`, 공통/admission 밖에서 무대기 획득 | list/get만 사용. 네 슬롯은 초기 검증 상한, 초과 시 명시 오류. 실제 제어·원장 조회·Linux 준비 슬롯과 별개 |

같은 Manager 객체의 동시 call은 무대기 거절하며 다른 요청은 fork 객체를 사용한다.
한 조회의 구성·실행 사본은 반환/예외 시 폐기한다. 새 TTL cache·daemon·DB는 만들지 않는다.
원본 JSON 원자성은 모든 파일/Xen을 하나의 트랜잭션으로 묶지 않는다. 외부 root/xl 변경까지
원자적으로 막는 보장도 아니다. 검증된 신원·수명 결합만 응답하며 불완전한 목록은 성공으로 숨기지 않는다.

조회 자식은 관측 슬롯 FD를 상속하고 timeout/I/O 오류 후 직접 소유 자식만 회수한다.
FD 개수만으로 Linux 준비 권한을 판단하지 않는다. 종료가 확인되지 않은 자식을 슬롯 부재나
성공으로 취급하지 않으며 guest backend/다른 프로세스를 조회 helper와 같이 정리하지 않는다.
관측 슬롯 파일은 기존 Linux 준비 소유 파일과 같이 현재 uid·단일 링크·0600 일반 파일을 요구한다.
부모 중단 뒤 상속 FD의 실제 종료까지 재사용을 막고, 정상 반환 때 기존 잠금 inode를 재사용한다.
지원하는 오류·회차·예산은 [정식 동시 조회 계약](cli-manager-spec.md#read-concurrency)을 따른다.

## 검증 근거의 수명

순수 검증은 입력·정책·설정을 읽어 사용 가능한 조합인지 판단한다. 실제 관측은 Xen의
domain 집합·UUID/name/domid·고유 실행값과 필요한 CPU mask를 읽는다. guard는 조치
직전에 현재 문맥이 그 근거와 여전히 같은지 확인하는 단계다.

같은 요청의 같은 단계에서는 완전한 관측 결과를 응답 조립에 재사용한다. 이때 대상,
host, lifecycle, intent revision, configuration fingerprint, CPU policy revision/hash,
deadline이 모두 일치해야 한다. 다른 요청에 넘기는 TTL 캐시로 사용하지 않는다.

domain 제거·새 create, 정책/구성/의도 변경, host 변경은 재검증 경계다. Linux의 기존
toolstack reboot도 새 실행을 bind할 때 신원과 CPU 배치를 다시 검증한다. 이미 실행 중인
start 역시 현재 신원·affinity를 확인하고 중복 생성하거나 몰래 재배치하지 않는다.
스냅샷의 오래된 관측을 조치 허가로 사용하지 않는다.

## 완료·현재 조회·미확정

| 반환 상황 | 해석과 다음 행동 |
|---|---|
| 사전 거절 | 수락하지 않은 입력/대상/정책 오류. 원인 수정 후 현재 문맥으로 요청 |
| 수락 후 pending | 의도는 저장됐지만 물리 완료 전. 원 key·operation을 보존하고 재확인 |
| 실제 완료 | 필수 검증과 완료 저장이 끝남. 같은 요청 재전달은 물리 동작을 반복하지 않음 |
| 완료 replay의 현재 조회 실패 | 원 완료 결과는 유지. `vm=null` 또는 `current_observation=null`과 warning을 현재 조회의 실패로 해석 |
| accepted + failed/uncertain | 수락한 의도는 남음. 원 journal·Xen 실행·scope를 대조하기 전 새 key로 반복하지 않음 |
| 후속 사용자 명령으로 superseded | 더 최신 revision이 유효. 과거 요청이 새 의도를 되돌리면 안 됨 |

`original_result`는 원 작업 당시의 결과이고 `data.vm`은 응답 시점의 현재 상태다.
이미 완료된 reboot 뒤 사용자가 stop했다면, reboot replay의 현재 VM은 정지 상태일 수 있다.
현재 조회 실패가 원 완료를 실패로 뒤집지는 않는다. 반대로 필수 신원·CPU·완료 저장의
실패를 부가 진단 실패라고 바꾸어 성공으로 만들지도 않는다.

[수락·재전달 계약](cli-manager-spec.md#acceptance-contract)에 응답 형태와 필드 정의가 있다.
완료된 원 journal만 과거 결과로 반환한다. failed/uncertain journal은 성공으로 승격하지 않는다.
event/trace의 부가 저장 실패와 원 intent/launch/완료 journal의 필수 저장 실패를 구분한다.

## 복구 중 정지 요청

복구가 긴 실행 잠금을 보유해도 별도의 하위 Manager stop은 짧은 admission 잠금으로
stopped 의도를 먼저 저장할 수 있다. 잠금 순서는 긴 실행 잠금 → 짧은 admission이며
빠른 stop은 admission을 해제한 뒤 긴 잠금을 시도한다. 잠금 순서를 역전하지 않는다.

내구 수락 뒤 긴 잠금이 바쁘면 `OPERATION_IN_PROGRESS`와 accepted/pending 문맥을
반환한다. 이 응답은 정지 완료도, 아무 효과 없는 미수락 busy도 아니다.
아래는 dom0의 독립된 한 사용자 요청이다. KEY를 기록하고 재전달 때 다시 만들지 않는다.

```sh
read -r -p '정지할 VM UUID: ' VM_UUID
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '원 작업 키: %s\n' "$KEY"
xenova-vm stop "$VM_UUID" --idempotency-key "$KEY" --json
# pending이면 원 operation을 조회하고, 잠금 해제 뒤 같은 요청을 재전달한다.
xenova-vm stop "$VM_UUID" --idempotency-key "$KEY" --json
xenova-vm get "$VM_UUID" --json
```

남은 물리 정지를 이행하는 주체는 원 요청을 보유한 CLI/상위 호출자다. 자동 stop
daemon은 없다. `control-operation`은 기록 조회이며 실행기가 아니다. 호출 프로세스가
내구 저장 직후 죽어도 새 Manager가 원 key/operation/revision/deadline을 대조해 재개한다.
만료한 원 요청에 새 실행 예산을 주지 않는다. 원 실행의 상태를 확인하고 필요하면
명시적으로 새 stop을 요청한다. force는 별도 승인·신원 조건을 그대로 따른다.

이미 제출한 create의 효과가 있을 수 있으면 취소된 것으로 가정하지 않는다. 과거 host,
lifecycle 변경, 후속 사용자 revision, 저장 불확실성, 모호한 실행 신원은 먼저 재확인한다.
실제 force/destroy를 수락 의도의 자동 후속 동작으로 추가하지 않는다.

## 시간 예산과 실제 반환

deadline은 한 관리 작업의 절대 단조 시각 기한이다. 일반 control은 Manager 진입부터,
복구는 원 요청에 저장된 절대 기한부터 계산한다. 잠금·registry·검증·실행은 잔여 시간을
공유하고 같은 요청의 재전달로 기한을 연장하지 않는다.

일반 제어 90초·관측 15초·autostart batch 140초는 서로 다른 작업의 기본값이다.
CPU 준비의 기존 timeout_ms도 factory 정책 검증까지 전달한다. 하위가 내부 단계에
새 15초를 더 주지 않는다. 부팅 전체에 개별 reboot 예산을 적용하지 않는다.

필수 저장과 커널 I/O는 이미 시작한 syscall을 강제로 선점하는 보장이 없다. 만료 뒤
원 실행의 결과만 확인하는 제한 예산이 실제 반환을 늦출 수 있지만, 그 예산으로 새
create/reboot나 강제 종료를 제출할 수는 없다. 관측 실패는 정상값이나 domain 부재가 아니다.
현재 적용값·설정 위치·계산식·연동 제약은 [시간 예산 계약](cli-manager-spec.md#time-budgets)에 모았다.

## 제품 서비스 연동과 지원 경계

하위 Manager의 stop 수락과 제품 FastAPI의 **key 있는 stop·원 key 조회**는 구현돼 있다.
이 경로는 요청별 Manager로 선행 get·VM mutex·공유 직렬화를 우회해 pending/완료를 구분한다.
key 없는 기존 stop은 VM mutex·SerializedManager 경로를 유지한다. Spring의 원 key 보존과
조건부 후속 이행, 3~5번의 일반 조회/생성·수습 소비까지 종단 연결됐다는 뜻은 아니다.
하위는 독립 요청 객체의 stop 수락, 원 key의 read-only 작업 조회와 호출별 잔여 예산을
제공한다. FastAPI·Spring·VM 관리 화면의 연결 위치와 수락 조건은
[관리 요청 연동 요청서](management-integration-request.md)에 정리돼 있다. 요청서는
담당자가 구현할 범위이며 현재 설치된 HTTP 기능을 뜻하지 않는다.

Recovery Worker는 현재 Watcher 판단을 기존 Recovery Engine에 연결해, 원 attempt를
저장한 뒤 Manager의 recover_once를 실행한다. 기본 attempt 실행 예산은 110초다.
응답 유실은 같은 journal/기한으로 확인하며 복구 완료는 새 Xen 실행 관측이라는 뜻이다.
Worker의 손상 이력 차단과 사건 기록 실패의 해석은
[상태 파일·사건 기록](../../platform/services/dom0-manager/README.md#worker-상태-파일과-사건-기록)을 따른다.
선택한 공통 주행 프로필에서는 그 뒤 실행별 Bootstrap·신뢰 바인딩·Standby 준비를
별도로 확인한다. [주행·재합류 구조](../../platform/apps/drive/README.md#실행-바인딩과-자동-복구)를 따른다.
감시 주기·장애 판정 유예는 복구 실행 timeout이 아니다. CPU 조회와 제품 HTTP의
복구 중 stop 수락은 별도 연결·수락 범위를 유지한다.

## 진단과 검증 근거

미확정 작업은 [recovery-reconcile](cli-manager-spec.md#cmd-recovery-reconcile)로 원 attempt의
실제 효과·호출 종료·신원·의도를 진단한다. 명시적 실행도 새 VM 제어 없이 입증한 사실만 같은
journal에 종결한다. 구형 stopping에 종료 근거가 없거나 미확인 launch/실행자가 남으면 차단한다.
Bootstrap의 uncertain은 [receipt 적용 확인](../../platform/guests/zephyr/telemetry/README.md#uncertain-receipt의-재주입-없는-적용-확인)으로
재주입 없이 대조한다. 기록 삭제·phase 편집·새 신원/기한·자동 force는 수습 경로가 아니다.
Worker의 원 결과 소비·Standby 재합류 종단 연결은 담당자 인수 대기다. Gateway의
[로컬 원본 진단·재확인](../../platform/services/gateway/README.md#권위-원본-진단과-재확인)은
기존 정상 원본과 같은 lease를 조건으로 제공한다. 손상 원본의 backup 최신성 입증·복원과
장치 적용 수락은 별도다.

증상을 조사할 때는 UUID·현재 host_boot_id·intent revision·operation/attempt·execution을
함께 기록한다. 현재 domain과 원 journal을 구분하고 비밀 설정 전체를 출력하지 않는다.
`intents`는 등록/lifecycle/저장 의도 metadata만 읽고 실행을 관측하지 않는다.
`get/probe/execution-context`는 같은 정합성 엔진의 현재 관측이며 snapshot을 게시한다.
`snapshot`은 유효기간이 있는 과거 관측, 작업 이력 명령은 원 기록 조회다.
status는 출력 형식과 무관하게 list이며 옛 자산/시각 종합 점검은 diagnose --details로 요청한다.

로컬 FakeXen/실제 systemd 격리, 실보드 개별 제어, 제품 HTTP·host 전원 부팅은 서로 다른
검증 범위다. 실제 host 종료의 모든 실패 경로와 상위 종단 수락은 남아 있다.
특정 후보 비교·측정 조건·원자료·과거 실패는
[검증 이력](../verification/platform-management-boundaries-history.md)에 보존한다.

## 현재 적용 상태

### Lifecycle의 대상 소유와 공통 잠금

`xenova_preparation.AssetLease`는 `locks/asset-<VM UUID>.lock`의 실제 flock을
수락 전 검사부터 원 lifecycle 정산까지 유지한다. 미수락 검사 lease는 원 operation/예약이 아니다.
획득 순서는 **대상 lease → lifecycle I/O 슬롯 → 공통 operation lock → 필요한 짧은 admission**이다.
대상과 슬롯은 모두 무대기다. `lifecycle-io.lock`은 기존의 무거운 lifecycle 동시 1개만
유지하며 조회/다른 VM stop은 이 슬롯을 사용하지 않는다. Linux create 준비 슬롯과도 별개다.
공통/admission을 잡고 대상 lease를 기다리지 않는다. 다른 writer는 이 파일을 무대기 확인해
같은 VM을 변경하지 않는다. 같은 thread의 중첩 공개 호출도 예외가 아니다.

retire/restore/purge의 긴 usage/inventory·restore fsck·purge 항목 unlink/fsync와
update-resources의 usage, firmware-transition의 bundle 검증·revision 복사/검증은
`Manager._lifecycle_io`에서 대상 보호를 유지한 채 공통 잠금 밖에서 수행한다.
자원/firmware도 수락 전 lease와 기존 I/O 슬롯을 사용하고, 수락 뒤 원 journal에 결합한다.
독립 firmware-replace CLI는 같은 대상 보호로 동일 프로필 준비를 분리하되 기존의
비-journal 출력/교체 의미를 유지한다. 직접 Python replace_firmware의 기존 호출자는
여전히 공통 잠금을 보유해야 한다. 일반 조회/다른 VM stop은 I/O 슬롯을 사용하지 않는다.
복귀할 때 해당 tombstone·의도·원 journal·해당 configuration을 대조하고, 주소/용량 판정에는
현재 등록 집합을 새로 얻는다. 삭제 항목마다 전체 registry/Xen을 다시 스캔하지 않는다.
이동·등록·필수 journal 결과 확정과 DHCP 예약 파일의 원자적 게시만 공통 구간이다.
이 소유 경로의 DHCP start/network/is-active와 reload는 admission·공통 잠금 밖이다.
서비스 확인 후 새 registry로 예약 파일을 구성하여 다른 VM의 새 예약을 덮지 않는다.
reload 실패는 원 결과를 성공으로 만들지 않으며 같은 원 작업으로 회수한다.
create/제어/복구의 기존 DHCP 호출을 전부 병렬화한 것은 아니다. 단일 rename/fsync 등 syscall의
최악 지연이나 모든 물리 제어의 병렬화를 보장하지 않는다.

원 journal을 기록한 뒤 lease에 operation/spec/세대를 결합한다. 활성 같은 key는 원 진행을
반환하고 재검사·이동하지 않는다. 잠금이 실제 반환된 뒤에만 기존 journal 재개 규칙을 사용한다.
helper에는 대상 FD를 상속한다. 부모 종료/함수 반환만으로 상속 자식의 FD를 회수하지 않는다.
FD 잔존은 새 실행과 storage drain을 막으며 대상 보호/슬롯을 각각 확인한다.
원장 삭제·자동 kill로 해소하지 않는다.

정상 불변 root의 fsck는 1회다. 수락 전 최초 fsck 실패는 여전히 원 operation 미생성이다.
소유·root revision·원 기록·host/gate가 달라지면 재검사/명시 거절하며 gate 닫기·재열기는
옛 검사를 다시 허가하지 않는다. 게시 직전 admission 안에서 gate·metadata·검사한 root를
대조한다. 실제 보호/비용과 양 OS barrier는 [5.5 후속 근거](../verification/recovery-scenario-closure-20261030.md#stage55-followup)를 따른다.
그 이후 firmware/usage/DHCP의 실제 독립 stop과 CPU 교차·추가 비용은
[좁은 교차 보완](../verification/recovery-scenario-closure-20261030.md#stage55-cross-boundaries)에 연결한다.

5번까지 반영한 `4a585d4`의 기능별 권위 원본·호출·소유/잠금·비용·실패 종결은
[기본 관리 기준선](../verification/platform-management-baseline.md)에서 대조한다.
Linux 원 기한·명시 private 수습·내구 예약 반환은 하위 로컬/부분 패키징 범위다.
운영 생성 시간 정책의 공급과 같은 후보 설치, HTTP·Spring·화면 및 실제 보드 수락은 별도다.
