# VM 관리 구조와 작업 수명주기

이 문서는 VM을 등록한 뒤 시작·정지·재부팅·복구할 때 플랫폼이 무엇을 검증하고
어느 시점의 결과를 반환하는지 설명한다. 명령 입력·필드·오류의 정식 정의는
[CLI·Manager 명세](cli-manager-spec.md), 서비스 순서는
[부팅·종료 구조](management-boot-boundaries.md)를 따른다.

## 목차

- [VM·실행·작업·의도](#vm실행작업의도)
- [요청 수락부터 완료까지](#요청-수락부터-완료까지)
- [검증 근거의 수명](#검증-근거의-수명)
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

부분 실패의 원 기록·파일 보존과, 운영자의 정식 종결·정리·예약 해제는 다른 기능이다.
후자는 아직 제공되지 않는다. 현재의 보호를 파일 삭제·원 ID 재발급·자동 force로 해제하지 않는다.

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

하위 Manager의 stop 수락은 구현돼 있다. 제품 HTTP는 VM별 mutex와 공유
SerializedManager를 먼저 통과하며 pending details와 원 key의 전달도 연결돼 있지 않다.
따라서 하위의 복구 중 stop 수락을 HTTP 종단 수락으로 해석하면 안 된다.
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
`get/probe`는 실제 관측, `snapshot`은 유효기간이 있는 캐시, 작업 이력 명령은 원 기록 조회다.

로컬 FakeXen/실제 systemd 격리, 실보드 개별 제어, 제품 HTTP·host 전원 부팅은 서로 다른
검증 범위다. 실제 host 종료의 모든 실패 경로와 상위 종단 수락은 남아 있다.
특정 후보 비교·측정 조건·원자료·과거 실패는
[검증 이력](../verification/platform-management-boundaries-history.md)에 보존한다.
