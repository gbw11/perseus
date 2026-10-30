# HTTP·Spring·VM 관리 화면 연동 요청

수신: dom0-manager FastAPI·Driver, Spring VM 관리, VM 관리 화면 담당자.
**상태: D02·D08 하위 Manager 구현·로컬 회귀·부분 패키징 마감 / HTTP·Spring·화면 연동 미구현·종단 수락 전.**
현재 요청은 제품 담당자가 구현해야 할 연동이다. 하위에서 제공하는 기능과 HTTP/화면의
제안 계약을 구분하며, 정상 VM 응답의 의미를 pending 접수 의미로 바꾸지 않는다.

## 현재 할 일

| 담당·읽을 절 | 구현할 일 | 완료를 확인할 근거 |
|---|---|---|
| FastAPI · 1절 | stop의 짧은 수락 경로, 독립 fork, 원 요청 key 조회·오류/details·잔여 예산 전달 | 같은 프로세스의 긴 제어 중 stop 수락, 불완전 조회·timeout 의미 보존 |
| Spring · 2절 | 제출 전 원 key/spec 보존, 작업 상태·후속 조회·조건부 동일 요청 이행 연결 | pending을 STOPPED로 기록하지 않고 응답 유실·재시작 뒤 같은 요청 회수 |
| 화면 · 3절 | 요청 key와 현재 관측·수락·진행·완료·미확정을 구분 | 같은 key 재확인, 오래된 응답이 새 의도/현재 VM을 덮지 않음 |
| 공동 · 4~5절 | 진입부터 대기·실행·반환까지 잔여 예산과 종단 회귀 연결 | 원 물리 deadline 불변, 만료 뒤 뒤늦은 제출·중복 물리 실행 0 |

## 함께 읽을 파일과 순서

| 순서 | 파일·읽을 부분 | 필요한 이유 |
|---|---|---|
| 1 | 이 문서의 현재 할 일과 담당 절·공동 4~5절 | 수정 위치·제안 응답·담당별 후속 이행 책임 |
| 2 | [CLI·Manager 명세](cli-manager-spec.md)의 `control-operation`·공통 계약 A·Python Manager 호출·시간 예산 | 실제 하위 action/envelope·입력 범위·오류·pending/replay·예산의 정식 정의 |
| 3 | [관리 구조·수명주기](management-boundaries.md) | VM·현재 실행·원 operation·사용자 의도, 수락과 완료의 차이 |
| 4 | [기존 VM HTTP 계약](../contracts/vm-api-contract-v1.md) | 현재 제품 HTTP 계약과 이번 변경 제안을 대조 |
| 참고 | [관리 요청 하위 검증](../verification/management-budget-adapter-20261027.md), [fork·key 조회 보완 검증](../verification/health-evidence-20261027.md#d02-d08) | 하위 제공물·이미 수행한 시험과 아직 없는 제품 연동을 구분 |

## 목적과 책임

사용자가 제출 전에 정한 요청 키로 수락·pending·원 작업·완료·미확정을 추적하고, 실제 VM
관측과 구분해야 한다. 하위 journal이 수락·실행·결과의 원본이다. Spring의 추적 정보는
원 key·VM·action·원 spec 및 하위 결과 참조이며 새 실행 원장이 아니다.

아래 HTTP endpoint·header·DTO는 담당자 검토·구현·회귀가 필요한 제안이다. 하위의
`Manager.call/fork` 지원을 제품 HTTP에 이미 연결된 기능으로 표시하지 않는다.
D03 수습·차량 입력/resume·전체 Telemetry 화면과 SQLite 도입은 포함하지 않는다.

## 1. FastAPI 담당 요청

| 현재 위치·동작 | 요청할 최소 변경 |
|---|---|
| `dom0_manager/drivers/platform.py:SerializedManager.call/load_manager` | 긴 작업의 기존 직렬화를 유지한다. stop 수락과 `control-operation` 조회만 `inner.fork()`의 독립 요청 객체로 호출하는 경로를 연결한다. 공유 inner를 동시 호출하거나 모든 action의 mutex를 없애지 않는다 |
| `service.py:VmService._vm_lock/create/stop` | 같은 VM의 긴 start/reboot 때문에 stop이 수락 전에 막히지 않게 한다. platform stop의 admission 경로 앞에 VM mutex·선행 get을 두지 않는다. mock/xen 및 긴 실행의 기존 보호는 유지한다 |
| `drivers/platform.py:raise_for_envelope`, `errors.py`, `models.py`, `api/errors.py` | `operation_id/replayed`, 원 error.code/retryable, 허용된 details를 구조적으로 보존한다. 문자열 detail 파싱은 하지 않는다. private 경로·raw journal·credential은 반환하지 않는다 |
| `api/vms.py`, 요청 진입/서비스 예산 | 인증된 호출의 남은 기간을 받되 로컬 monotonic으로 요청 deadline을 만든다. threadpool·VM/resource 대기·직렬화·하위 제출·반환을 같은 예산으로 처리한다. 획득/실행 직후 만료 재검사, 만료 대기자의 뒤늦은 제출 금지 |
| 현재 원 작업 조회 HTTP 없음 | 제안: `GET /api/v1/vms/{vm_id}/control-operations?request_key=...`. 원 operation ID를 받기 전 응답 유실도 key로 찾는다. 서버에서 VM 접근권한 확인 후 아래의 VM-bound Manager 조회 사용. 조회가 이행/수락을 하지 않게 한다 |
| 제어 요청 key 전달 없음 | 기존 제어 요청에 `Idempotency-Key`를 받아 하위 `idempotency_key`로 그대로 전달하는 방식 제안. 최초 전송 전 caller가 정한다. 같은 key/다른 VM·action·force/spec는 충돌이며 timeout 값은 spec/hash에 섞지 않는다 |

`Manager.fork()`는 Python 요청 객체 분리이며 새 프로세스·실행기·daemon이 아니다.
fork만으로 상위 VM mutex가 없어지지는 않는다. CPU·신원·의도·물리 실행 guard와
execution flock은 하위에 남긴다. stop은 이미 다른 작업이 flock을 보유했다면 내구 수락 후
pending을 반환하고, 기록 조회는 admission 잠금만 사용한다.
현재 `ManagerLike` protocol에는 fork 선언이 없으므로 platform 전용 어댑터의 지원 확인도
연결한다. 미지원 설치본에서 공유 inner를 잠금 없이 호출하는 fallback으로 우회하지 않는다.

### 하위에서 실제 제공하는 호출

```python
# KEY/VM_ID는 최초 전송 전에 정한 값을 추적·보존한다.
manager.call("stop", {"vm_id": VM_ID}, idempotency_key=KEY,
             request_id=CORRELATION_ID, budget_ms=REMAINING_MS)

def read_original(manager, vm_id, original_key, remaining_ms):
    # 별도 요청 객체이며 같은 권위 저장소·admission/실행 잠금·guard를 사용한다.
    return manager.fork().call(
        "control-operation",
        {"vm_id": vm_id, "idempotency_key": original_key,
         "timeout_ms": min(remaining_ms, 15000)},
        request_id="lookup-original", budget_ms=remaining_ms)

def read_current(manager, vm_id, remaining_ms):
    # get/list는 현행 긴 호출 직렬화 연결을 유지해야 하며 stop 전 선행 호출이 아니다.
    one = manager.call("get", {"vm_id": vm_id, "timeout_ms": min(remaining_ms, 15000)},
                       budget_ms=remaining_ms)
    return one

def read_all(manager, remaining_ms):
    return manager.call("list", {"lifecycle": "REGISTERED",
                                "timeout_ms": min(remaining_ms, 15000)},
                        budget_ms=remaining_ms)
```

인자는 앞선 대기·호출 비용과 반환 여유를 뺀 **양수 정수 잔여 ms**다. 같은 예산을 두 함수를
연속 호출할 때마다 다시 주는 예가 아니다. get/list와 원 기록 조회는 최대 15000ms, call의
budget_ms는 1..105000ms지만 원 control의 물리 deadline은 기존 90초와 최초 잔여 예산으로
고정한다. 후속 조회의 새 예산은 원 deadline을 재발급하지 않는다. 최초 사용자 control의 key/spec를
Spring 관리 흐름이 제출 전 보존하고, 허용되는 동일 요청 이행 때 같은 값으로 전달해야 한다.

조회는 `data.operation`에 원 record를 반환한다. `data.deadline_observation`은 현재 host의
단조 시각과 원 host 일치/원 실행 기한 만료 여부를 반환한다. host가 다르거나 기한 근거가
없으면 만료는 null이다. 이것은 관측 시점의 정보이며 이후 물리 재전달의 승인 토큰이 아니다.
최종 실행은 같은 key/spec와 하위 guard로 다시 검사한다. `VM_NOT_FOUND` 조회 결과는
그 시점의 검색 부재이며, 앞선 제출이 결코 수락되지 않았다는 증거가 아니다.

### 작업 상태와 HTTP 응답 제안

| 확인한 근거 | 제안 상태·HTTP | 소비자가 할 일 |
|---|---|---|
| 호출 미제출 또는 명시적 미수락 거절 | rejected·기존 4xx/503/504 | accepted를 만들지 않는다. 미제출과 수락 여부 미확정을 구분 |
| 수락 확인 + 원 phase accepted/executing + 미완료·수습 오류 없음 | pending·202 | 원 작업 참조와 실제 현재 VM 관측을 따로 표시. stopped 완료 처리 금지 |
| 원 journal completed·result 확정 | completed·기존 동기 완료 의미 | vm=null/warning이어도 원 완료 보존. 현재 상태는 별도 조회 결과 |
| failed·원 오류 확정 | failed·기존 오류 매핑 | 사용자 desired_state를 rollback하지 않는다 |
| 응답 유실·아직 검색 부재 | unknown·확인 경로 포함 | 취소/미수락/효과 없음을 추측하지 않는다 |
| phase uncertain 또는 RECONCILE_REQUIRED | uncertain·명시적 수습 필요 | 202 무한 재시도·새 key/force 우회 금지 |
| 옛 요청이 새 의도로 대체됨 | superseded·원 결과 보존 | 옛 결과를 현재 의도/화면으로 덮어쓰지 않는다 |

하위 stop의 `completion="pending"` 하나만으로 202를 결정하면 안 된다. `phase`, 원 오류,
실제 수락 근거도 확인한다. 모든 503·OPERATION_IN_PROGRESS·timeout이 accepted는 아니다.

작업 DTO 제안은 request_key·vm_id/action·operation_id·accepted(불명 null)·status·phase·replayed·
intent_revision·host_boot_id/lifecycle_generation·원 deadline·원 결과의 허용 필드·nullable vm·
진단 warning·원 오류 code/retryable/허용 details다. 이는 현재 네 필드 Vm DTO의 대체물이 아니다.
원 journal 전체나 영구 credential·파일 경로를 그대로 직렬화하지 않는다. OpenAPI·Java·프론트
타입 및 기존 정상 완료의 호환 시험을 함께 갱신해야 한다.

### 원 key 검색 결과의 구분

| 응답·근거 | 담당자의 처리 |
|---|---|
| `VM_NOT_FOUND` | 완전한 해당 시점 검색의 부재. 앞선 제출이 결코 수락되지 않았다는 증거가 아님 |
| `DEADLINE_EXCEEDED` | 조회 예산 소진. 없음/유일함을 확정하지 않음 |
| `OBSERVATION_UNAVAILABLE`·읽기/형식 오류 | 불완전 directory 열람·사라진 항목·읽기 실패를 보존. key 존재 여부 미확인 |
| 같은 key의 복수 기록 | `RECONCILE_REQUIRED`. 첫 항목을 임의로 골라 유일한 원 작업으로 확정하지 않음 |
| host 변경 | 원 host와 다르면 `execution_deadline_expired=null`. 서로 다른 monotonic을 비교하거나 실행 허가를 복원하지 않음 |

FastAPI는 서비스 인증·대상 참조 검증 후 하위 VM-bound
조회만 호출하고, 허용 details(`accepted/completion/phase/operation_id/intent_revision/host_boot_id/
lifecycle_generation/deadline_monotonic_ms/execution_deadline_expired/next_action` 및 안전한 warning)를
구조적으로 전달한다. raw journal·경로·credential 전체를 반환하지 않는다. Spring은 현재 요청
주체 정책을 대조해야 하며 기존 모델에 사용자 소유 필드가 없다는 한계를 숨기지 않는다.

## 2. Spring 담당 요청

| 현재 위치·동작 | 요청할 최소 변경 |
|---|---|
| `vm/infrastructure/Dom0ManagerClient.stop`이 body 없는 2xx를 성공 처리 | 202/작업 DTO와 실제 완료를 구분한다. `Dom0ErrorResponse`의 원 platform_code·작업 참조/details 보존도 연결한다 |
| `vm/application/VmService.stop`이 선행 find 후 stop 반환 즉시 STOPPED 기록 | 수락 앞의 긴 find 의존을 제거/분리한다. pending·unknown은 stateTracker에 STOPPED 완료 사건을 만들지 않는다 |
| `Dom0ManagerClient.executeQuery/executeCommand` | 2회 재시도·backoff·connect/read를 같은 호출 잔여 예산으로 제한한다. read timeout은 하위 취소가 아니다. 새 key 자동 재전달 금지 |
| `VmControlPort`, controller·DTO | 요청 key, 하위 작업 참조, 조회/명시적 동일 요청 이행을 운반한다. `void stop`만으로 완료/접수 의미를 바꾸지 않는다 |
| 기존 업무 저장·VM 관리 흐름 | 최초 제출 전 key·VM/action·원 spec·요청 주체와 필요한 참조를 보존한다. 같은 key/spec 재전달과 신규 명령을 구별한다. 재시작 뒤 원 key 회수·read-only 조회·지원되는 이행 절차를 연결한다 |

현재 VmStateWatcher의 주기 상태 조회는 실제 VM 관측이며 원 작업 이행기가 아니다. 이것만으로
원 stop이 자동 완료된다고 주장하지 않는다. 기존 관리 흐름에서 제한된 후속 처리를 연결하되
새 daemon·detached future·범용 영구 큐로 책임을 넘기지 않는다. 실제 재시작 후 자동 이행이
지원되지 않는 경우 원 key 회수와 사용자의 명시적 동일 요청 재전달 절차를 제공해야 한다.

후속 확인은 예를 들어 한 사용자 동작당 최대 3회, 각 읽기는 남은 기간 안에서 제한하는 안을
검토한다. 횟수·간격은 **아직 적용값이 아니다**. `deadline_observation`이 만료/다른 host를
알리거나 phase uncertain/failed/completed/superseded이면 물리 재전달하지 않는다. 단순 검색
부재를 신규 실행 허가로 사용하지 않는다. 조회와 제어 재전달을 별도 함수·화면 동작으로 둔다.

작업 조회에도 VM 권한·요청 주체 정책을 적용한다. 현재 Spring VM 모델에 사용자별 소유 필드는
없으므로 새 요청 조회가 사용자 간 격리를 이미 제공한다고 가정할 수 없다. 기존 인증을 유지하고
operation ID/key만 알아서 다른 대상/주체의 요청을 조회·이행하는 경로를 만들지 않는다.

Spring의 기존 관리 요청 추적이 재시작 뒤 원 key를 회수하고 조회·지원 이행을 이어가는 주체다.
구현 전에는 자동 후속 이행을 지원한다고 표시하지 않는다. 브라우저의 재확인은 같은 key를
회수하며 조회가 실행기로 바뀌지 않는다.

## 3. VM 관리 화면 담당 요청

대상은 `api/client.ts`, `api/queries.ts`, `api/types.ts`, `VmListPage.tsx`, `VmDetailPage.tsx`다.

- 최초 전송 전에 key를 만들고 네트워크 오류·401 재발급 재전송·재확인에 같은 값/본문을 쓴다.
  필요한 최소 key·VM·action을 회수 가능하게 보존하되 토큰이나 신선한 VM 관측을 저장하지 않는다.
- “정지 요청 수락/진행 중”, “결과 확인 중”, “작업 완료”, “실패/수습 필요”를 근거로 구분한다.
  현재 실제 VM state·desired_state·작업 결과를 다른 정보로 다룬다.
- 조회와 동일 요청의 명시적 이행 버튼을 구분한다. 새 사용자 명령만 새 key를 만든다.
- 새 start 이후 도착한 옛 stop 응답은 원 결과 이력만 갱신한다. 원 key/operation과 host/lifecycle/
  revision을 대조하고 현재 VM 카드의 상태를 옛 결과로 덮어쓰지 않는다.
- 완료 replay의 vm=null/warning을 실패나 정지 상태로 채우지 않는다. “원 작업 완료·현재 관측 불가”로
  표시하며 불필요한 재실행·무제한 polling을 하지 않는다.

## 4. 시간 예산 연결 요청

현재 소스 기본값은 Spring connect/query/command **2/6/100초**, FastAPI 직렬화 **5초**,
하위 get/list **15초**, 일반 control **90초**다. 변경하거나 배포값으로 단정하지 않았다.

1. 각 서비스의 진입 시점에 로컬 전체 기한을 정한다. 받은 값이 남은 기간이면 인증·범위를 검증하고
   로컬 시계로 변환한다. 다른 host의 monotonic 절대값을 빼지 않는다.
2. Q/S 대기를 잔여 예산으로 제한하고 획득 직후/실행 직전 다시 확인한다. stop의 짧은 경로에는
   긴 VM mutex·SerializedManager 대기를 앞세우지 않는다.
3. 하위에는 `budget_ms`와 get/list의 `timeout_ms`를 실제 잔여값으로 전달한다. 원 key/spec/hash에
   시간값을 추가하지 않는다. 새 조회 예산과 원 physical deadline을 혼동하지 않는다.
4. 하위 `budget_ms` 상한 105,000ms는 90초 물리 실행과 기존 최대 15초 재확인을 나타내는 입력
   유효 범위다. **HTTP를 105초 기다리라는 설정이 아니다.** 예를 들어 100초 caller에 반환 여유를
   빼고 95초가 남았다면 물리는 최대 90초, 재확인은 그 95초 안의 잔여분이다. 대기가 7초를 썼다면
   다시 95초를 주지 않는다. 반환 여유 수치는 제품 실측 후 확정하며 일괄 25/120초 변경을 하지 않는다.
5. syscall/fsync·기제출 Xen/Store 작업을 취소했다고 가정하지 않는다. 바깥 응답이 끝나도 실제
   실행 소유권을 조기 해제하지 않고 원 journal로 확인한다.

## 5. 담당자 수락 시험

T01~T16은 기존 담당자 인수 시험 ID다. 다음 실제 경로와 판정으로 확인한다.
하위 로컬 통과는 HTTP·Java·화면 수락을 대신하지 않는다.

| 묶음 | 필수 실제 경로·판정 |
|---|---|
| T01~T03 | 실제 FastAPI/Driver/Manager와 execution flock, 같은 FastAPI의 긴 control. stop 내구 수락과 같은 원 작업·기한, 허용 이행 뒤 필요한 shutdown만 수행 |
| T04~T05 | VM/직렬화 대기 만료 뒤 뒤늦은 제출 0. get/list는 모든 VM에 하나의 잔여 예산. 실패를 빈 성공으로 바꾸지 않음 |
| T06~T08 | 실제 HTTP 지연/응답 유실. opid를 받기 전 key 조회, 같은 key/spec 유지·상충 거절·원 기한 불변·만료 뒤 새 물리 권한 없음 |
| T09~T12 | completed replay vm=null/warning, uncertain·저장 실패, 호출자 재시작 key 회수, 새 의도 뒤 옛 응답 역전 |
| T13~T15 | 실제 Java client/service/HTTP와 최소 화면 시험. 202를 완료로 처리하지 않음. 다른 VM/주체·비밀 포함 details 거절/제외 |
| T16 | S01/S02·stop acceptance·recovery/rejoin 회귀. guest를 바꾸지 않으면 무관한 ARM 재빌드 없음 |

FastAPI 연동·Spring 요청 추적·UI 표시를 각각 담당자가 구현한 뒤 같은 후보로 종단 수락한다.
현재 요청서만으로 이 기능들이 설치·연결됐다고 표시하지 않는다.

위 T01~T16에 더해, fork 중 다른 예산/trace 혼입 0, 부분 열람 뒤 false absence/unique 0,
완료 replay 추가 물리 명령 0을 담당자 인수 assertion으로 유지한다.

## 수습 후 원 결과 재조회 연동

로컬 운영자의 하위 [recovery-reconcile](cli-manager-spec.md#cmd-recovery-reconcile)과
receipt 확인은 VM 복구 실행·Standby·출력 허가와 다르다. 상위는 기존 원 key/operation/attempt의
결과를 다시 조회하고, lower failed/null result를 복구 완료로 표시하지 않는다. completed라도
현재 `vm=null/warning`과 원 완료를 분리한다. 같은 원 요청 조회가 물리 실행기로 바뀌면 안 된다.

D02·D08의 구현·상위 연동 대기와 기존 권한/허용 details 목록을 유지한다. 수습 관련 최소
phase/result/error와 재조회 사유만 기존 DTO 연결 요청에 포함하고 raw journal·credential·private
경로를 그대로 반환하지 않는다. 새 웹 수습 버튼·HTTP 복원 API는 이 요청의 선행조건이 아니다.
원 attempt의 운영 수습과 Worker final 결과 반영은 [Worker D03 요청](../requests/2026-10-27-recovery-health-ctrl.md#d03-worker)을 따른다.

## 요청 이력과 담당별 문서

| 기존 위치·요청 | 현재 위치·상태 |
|---|---|
| D02·D08의 1~5절 | 이 문서의 동일 담당 절에서 유지한다. 하위 마감과 상위 미구현 상태는 바꾸지 않았다 |
| 종전 7절 하위 인수 보완 | 호출·검색 오류·허용 details는 1절, 후속 책임은 2절, 예산과 assertion은 관련 본문에 합쳤다 |
| D01-L01~L05 | [Linux·Agent 요청서](../requests/2026-10-27-linux-health-agent.md)로 이동. 담당자 구현·연동·실보드 수락 전 |
| D01-W01~W05 | [Worker 요청서](../requests/2026-10-27-recovery-health-ctrl.md)에서 별도 추적. 담당자 구현·수락 전 |

<a id="linux-health"></a>
### 이전 Linux 요청 링크

기존 `#linux-health` 링크는 호환을 위해 남긴다. Linux·Agent 담당자는
[Linux·Agent 요청서의 현재 할 일과 읽는 순서](../requests/2026-10-27-linux-health-agent.md)에서 시작한다.
