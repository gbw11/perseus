# 관리 경계 구현·검증 이력

이 보고서는 운영 문서 재구성 전의 구현·시험 기록을 보존한다. 작업 식별자,
당시 승인·미수락 범위, 실패, 후보별 비교, PC 원자료 경로와 해시는 당시 근거다.
아래의 사용법·제안값을 현재 계약으로 사용하지 않는다. 현재 구조와 명령은
[플랫폼 안내](../platform/README.md)와 [CLI·Manager 명세](../platform/cli-manager-spec.md)를 따른다.

## 이전 관리 경계 문서

# 관리 작업의 검증·deadline·완료 경계

이 문서는 하위 `Manager`/`Platform`/`Execution`/`CPU`/`Recovery`의 구현과
회귀·상위 소비자 인계를 설명한다. 기준 소스는 `develop`에 포함된
`86667126aecc67c54c7277807db7742a2d7b162a`이며 작업 브랜치는
`feat/platform/runtime-lifecycle-diagnostics`다. 실제 설치본·최종 이미지·상위
서비스·최종 이미지 수락으로 확대하지 않는다. CPU 활성 실보드의 개별 reboot
수락과 autostart unit의 남은 경계는 아래 G5 결과로 구분한다.

## 목차

1. [변경 범위와 보존 계약](#변경-범위와-보존-계약)
2. [검증 책임과 근거 수명](#검증-책임과-근거-수명)
3. [시간 예산과 재전달](#시간-예산과-재전달)
4. [완료 결과와 현재 관측](#완료-결과와-현재-관측)
5. [계측·비교 결과의 해석](#계측비교-결과의-해석)
6. [T01–T20 수락 대응](#t01t20-수락-대응)
7. [Worker·dom0-manager 인계](#workerdom0-manager-인계)
8. [실보드 승인·원복 준비](#실보드-승인원복-준비)
9. [G5 실보드 결과와 별도 수락 경계](#g5-실보드-결과와-별도-수락-경계)
10. [A/B/C 후속 마감과 증거 연결](#abc-후속-마감과-증거-연결)

## 변경 범위와 보존 계약

대표 경로는 CPU 정책이 활성화된 Zephyr 협조형 reboot다. 같은 경계의
start·stop·Linux reboot·recover_once·autostart와 명시적 관측 조회를 회귀한다.
기존 등록·intent·operation/attempt·execution journal이 원본이다.

- 기존 CLI action/spec, Manager의 동기 호출과 일곱 envelope 필드를 유지한다.
- UUID·표시명·MAC/IP·펌웨어·템플릿·CPU 정책·per-VM force 승인을 변경하지 않는다.
- 신규 HTTP API·daemon·대체 상태 기계·공용 TTL 캐시를 만들지 않는다.
- Worker·dom0-manager·Spring·프론트·Gateway 코드는 변경하지 않는다.
- U-Boot·Xen·dom0 커널·DT·하드웨어·네트워크 부팅 설정을 재설계하지 않는다.
- 필수 저장의 file fsync → rename → directory fsync를 유지한다.

실제 Xen→dom0→domU 준비·종료 순서, `After`와 `Requires`의 차이, 최초 등록과
매 부팅 준비, service별 상한은 [부팅·종료 경계](../platform/management-boot-boundaries.md)에
정리했다. systemd unit 자체의 의존성은 이번 변경에서 재배치하지 않았다.

## 검증 책임과 근거 수명

| 책임 | 구현 경계 | 확인 내용 |
|---|---|---|
| 순수 입력·정책 검증 | Manager 입력 검사, CPU context, record/config 검증 | action/spec·영구 UUID·정책 revision·vCPU/자원·구성. 실제 적용 성공을 대신하지 않음 |
| 실제 실행 관측 | `Execution.observe/observe_many`, `CPU.require/layout/observe` | UUID/name/domid/incarnation, 실행 상태, 전체 domain 집합과 모든 hard affinity. 조회 실패는 부재가 아님 |
| 조치 직전 guard | `Execution.guard_launch`, 기존 종료 대상 재조회·admission 검사 | 현재 host/lifecycle/intent/record/config/정책 및 소유 launch가 그대로인지 확인하고 기한을 검사 |
| 실행 결합 | `bind_launch` | 새 실행 incarnation 대조, 필수 CPU 검증 완료, observation·execution 저장 |
| 작업 완료 | 원 control/recovery/Zephyr journal | 필수 실행 사실과 내구성 있는 완료 저장. 앱 준비·차량 제어권을 주장하지 않음 |
| 응답 구성 | `Manager._view(observation=...)` | 이미 확보한 관측과 metadata를 조립. 포맷 조립 자체는 Xen I/O를 하지 않음 |

### 재사용하는 근거

`CPU.require(..., capture=...)`가 만든 내부 `_ValidationEvidence`만 같은 launch
준비 단계에서 한 번 소비한다. 같은 platform 객체·host·record hash·lifecycle·
intent hash·deadline·CPU context가 모두 일치해야 한다. 외부 spec에
`prevalidated`나 `skip_validation`을 추가하지 않는다. 일반 직접 호출은
자체의 완전한 검증을 수행한다.

| 시점 | 재사용 여부와 남긴 fresh 검사 |
|---|---|
| 공통 start의 CPU 검증 → 중첩 prepare_launch | 같은 단계의 일회성 근거를 전달. 중복 전체 layout만 줄임 |
| 위 전체 CPU 검증 | 기존 두 표본 대조 유지. dom0·등록/미등록·paused/blocked domain을 제외하지 않음 |
| 소유·의도·구성·정책·host 변경 | 기존 근거로 새 조치를 허가하지 않음. `TARGET_CHANGED` 등 원 오류 유지 |
| 실제 create/reboot 직전 | `guard_launch`가 현재 저장 구성·의도·admission·소유 launch·기한 재확인 |
| 옛 실행 제거/새 실행 생성 이후 | 이전 근거를 새 incarnation의 검증으로 사용하지 않음. bind에서 신원·CPU를 fresh 검사 |
| binding 도중 affinity/incarnation/intent 변경 | 필수 검증 실패. 생성 결과를 지우거나 자동 repin/destroy하지 않음 |
| 완료 응답의 data.vm 조립 | 방금 필수 검증·저장한 observation을 재사용. 응답 포맷만을 위한 추가 Xen 조회 제거 |
| 완료 후 같은 원 요청 재전달 | 원 결과 보존. 선택적인 현재 관측은 별도로 반환하며 물리 명령을 재제출하지 않음 |
| 독립 get/list/probe | 명시 관측이므로 새 조회. 과거 제어 결과나 snapshot으로 대체하지 않음 |

이 검증은 관측 시점의 사실이다. 플랫폼 잠금 밖의 root/xl 변경까지 원자적으로
통제한다고 주장하지 않는다. 외부 도메인·affinity 변경은 필요한 fresh 관측
경계에서 탐지하며, 변경된 대상을 임의로 정리하지 않는다.

## 시간 예산과 재전달

전체 기한은 하위 호출 진입의 단조 시계에서 한 번 정한다. 하위 도구 호출은
남은 전체 시간과 개별 호출 상한 중 작은 값을 사용한다.

소비자에게는 **기한의 시작 시점 변경**이다. Manager 경로는 `_call` 진입의
`entered`부터 입력 검사·잠금·registry·준비·실행·필수 검증을 포함한다.
종전 준비 이후부터의 대기 상한과 같은 의미가 아니므로, 느린 준비 환경에서는
물리 조치 전 또는 더 이른 단계에 기한이 끝날 수 있다. HTTP 큐/상위 직렬화
대기는 포함하지 않는다. 소비자는 이 변경을 timeout 확대나 새 key 재발급으로
상쇄하지 말고 원 operation과 수락 여부를 확인해야 한다.

| 호출/단계 | 최초 예산·경계 | 재전달·만료 처리 |
|---|---|---|
| Manager start/stop/destroy/Linux reboot | 하위 진입부터 90초 공통 control 예산 | 수락 journal의 `deadline_monotonic_ms`를 보존. 같은 key로 연장하지 않음 |
| Zephyr 협조형 reboot | 기존 90초를 유지하고 상위 기한이 더 짧으면 그 기한 사용 | 원 journal 기한과 상위 기한의 최소값. 옛 실행 제거 뒤 만료해도 create를 강행하지 않음 |
| recover_once | 기존 `deadline_monotonic_ms`와 expected_host_boot_id | spec을 완화하거나 새 attempt로 자동 전환하지 않음 |
| get/list/execution-context | Manager 진입부터 15초 | registry 비용 포함. list의 전체 VM이 하나의 총 예산·공유 목록 관측 사용 |
| probe | 기존 필수 `timeout_ms` 입력 | 입력 범위를 유지. 실패가 snapshot의 정상 부재로 바뀌지 않음 |
| cpu-layout/cpu-host-prepare | 기존 timeout_ms, 기본 15초·상한 15초 | 정책·관측 실패를 정상 빈 배치로 대체하지 않음 |
| 내부 직접 start | 호출자가 전달한 기한, 없으면 이번에 명시한 90초 공통 예산 | CLI/Manager/autostart가 각각 정한 기한을 중첩 함수가 새로 시작하지 않음 |
| autostart batch | CLI 진입의 140초, unit 외부 상한 150초 | 모든 VM에 같은 기한. 완료 VM 보존, 미시작/실패 구분. 별도 서비스 재시도와 원 control 재전달은 다름 |
| shutdown-all | 기존 90초 guest 정리, unit 상한 110초 | admission 차단 후 기존 협조/force 정책. 실패를 자동 force로 승격하지 않음 |
| 독립 check-xen | 25초, unit 상한 30초 | 모든 xl 준비 조회가 같은 예산 공유 |
| 만료 뒤 기존 실행 재판정 | 기존 경로의 제한된 15초 관측 예산 | 기존 소유 실행·기록의 관측/종결만 가능. 새 create/reboot/repin 권한이 아님 |

autostart는 잠금 충돌 시 기존 즉시 거절 의미를 유지한다. 종료 drain의 기존
제한 대기는 별개다. 상위 FastAPI 직렬화 잠금·HTTP 대기 시간은 하위 진입 이전
비용이며 이 계측이 포함한다고 주장하지 않는다.

기존 start는 개별 도구 timeout만 있었고, Linux reboot는 초기 준비 뒤에
전체 대기를 시작했다. 이번에는 제어 진입의 90초에 준비·수락·실행·필수 관측을
포함한다. 큰 목록 조회도 VM마다 15초를 받던 방식에서 전체 15초로 바뀐다.
따라서 느린 환경에서는 더 이르게 기한 오류가 날 수 있다. 예산을 늘려 성공으로
바꾸지 않으며, 비제어 자산 작업 전체에 90초를 새로 적용하지 않는다.
기존 CLI `status`도 목록 관측과 시간 조회가 하나의 15초 예산을 공유한다.

fsync·프로세스 생성·커널 대기를 강제 선점할 수는 없다. 물리 명령 제출 뒤
timeout은 실행 효과 없음이 아니다. 원 소유 기록과 `uncertain`을 보존하고
새 실행을 반복하지 않는다. 필수 저장 전 실패와 이미 완료된 작업의 후조회
실패를 구분한다. 기한 없는 legacy 미완료 기록에 새 물리 실행 예산을 부여하지 않는다.

## 완료 결과와 현재 관측

기존 envelope는 아래 일곱 필드를 유지한다.

```text
schema_version, ok, request_id, operation_id, replayed, data, error
```

| 결과 | 반환·소비 의미 |
|---|---|
| 최초 정상 제어 완료 | 기존 `data.vm`의 live view 유지. `original_result`와 `current_observation`은 필수 완료 근거를 제공 |
| 완료 재전달·현재 조회 정상 | `original_result`는 원 작업의 역사, `current_observation`/`vm`은 이번 조회 |
| 완료 재전달·현재 조회 실패 | `ok=true`와 원 결과 유지. `current_observation=null`, `vm=null`, `diagnostic_warnings`로 미확인 표시 |
| 원 성공 뒤 사용자 stop | 원 성공 유지. 현재 observation/view는 부재·현재 intent. 옛 실행을 다시 생성하지 않음 |
| 필수 신원·CPU·제거·완료 저장 실패 | 기존 오류/uncertain 유지. observed라는 phase만으로 완전 성공 처리하지 않음 |
| 일반 get/list/probe 관측 실패 | `ok=false`, `data=null`, typed error. 성공한 빈 목록/`domid=null` view로 위장하지 않음 |
| control-operation/recovery-operation | 기록 조회만 수행. 조회 action이 reconcile·물리 동작·완료 종결을 하지 않음 |
| snapshot | 기존 lock-free/no-Xen 소비. stale/unknown/current null 의미 유지 |

전체 envelope 예제는 실제 Manager/FakeXen 호출로 만든
[응답 fixture](../../platform/tests/fixtures/management-boundary-responses.json)에 있다.
가짜 UUID·임시 경로·단조 시계 fixture이며 실보드 결과가 아니다.

| fixture 이름 | 확인할 핵심 필드 |
|---|---|
| `normal_start` | `ok=true`, `replayed=false`, `data.vm.domid`와 기존 configuration/vcpus/memory_mib |
| `completed_start_replay` | 같은 operation_id·original_result, `replayed=true` |
| `completed_replay_current_query_failed` | 원 성공 유지, 현재 vm/observation만 null, warning |
| `completed_replay_after_user_stop` | 원 결과의 domid 존재, 현재 vm.domid 부재·desired_state stopped |
| `create_submitted_result_uncertain` | `ok=false`, `RECONCILE_REQUIRED`, 원 소유/작업 ID |
| `current_domain_identity_changed` | `ok=false`, `TARGET_CHANGED`; 현재 대상을 임의 채택하지 않음 |

`_description`은 같은 완료 근거를 포맷에 전달하기 위한 내부 값이다.
Manager 공개 응답에서는 제거한다. 과거 observation을 현재라고 이름만 바꿔
내보내지 않으며, 응답 전송 실패로 이미 저장한 completed를 failed로 되돌리지 않는다.

물리 명령 제출 뒤 효과가 미확정인 일반 제어 오류는 `RECONCILE_REQUIRED`로
반환하고, 종전 `XEN_EXECUTION_FAILED` 또는 기한 오류를 `details.cause`에 보존한다.
원 journal은 `uncertain`이며 CLI 실패 종료 코드 4를 유지한다. 기존 소비자는
이 오류를 효과 없음으로 해석해 새 key로 재실행하면 안 된다.

## 계측·비교 결과의 해석

`xenova_trace.py`의 요청 로컬 trace는 단계별 inclusive/exclusive 시간,
명령 종류별 수·시간, 잠금 wait/hold, 필수 저장의 write/fsync/rename/dir-fsync,
근거 재사용과 남은 기한을 기록한다. 중첩 inclusive 시간을 단순 합산하지 않는다.
bounded buffer/summary의 누락은 drop 수로 표시하며 argv·payload·인증정보를
수집하지 않는다. stdout JSON에는 trace를 출력하지 않는다.

`Manager(..., trace_sink=...)`의 선택적 sink와 `last_trace`는 로컬 진단용이다.
sink/계측 실패는 warning이고 필수 journal 실패는 여전히 오류다. 상시 새 daemon,
무제한 파일, 모든 span에 대한 핵심 journal fsync는 추가하지 않는다.

비교 도구는 [benchmark_management_boundaries.py](../../platform/tests/benchmark_management_boundaries.py)다.
같은 실제 Manager/Platform fixture에 외부 명령 0.01초·fsync 0.001초의 **합성 비용**을
주고, 실제 작은 파일 저장/fsync도 실행한다. 최종 비교는 아래 항목을 별도로 기록한다.

| 측정 항목 | 기준선 8666712 | 최종 후보 | 해석 |
|---|---:|---:|---|
| 전체 외부 명령 | 177 | 123 | 각 조건 세 번 모두 같은 호출 수 |
| CPU require / 전체 두 표본 layout | 7 | 4 | 조치 전·start·bind의 fresh 경계 유지 |
| CPU sample | 14 | 8 | 각 전체 검증의 두 표본 유지 |
| Platform domains | 28 | 22 | 전체 domain 범위를 빼지 않고 중첩 호출 감소 |
| Execution observe | 4 | 2 | 완료 근거로 응답 조립 |
| 신원 helper 호출 | 33 | 25 | incarnation fresh 검사 유지 |
| file fsync / rename / directory fsync | 각각 22 | 각각 21 | 필수 저장 순서는 유지 |
| 물리 reboot / create | 각각 1 | 각각 1 | 필요한 물리 동작은 동일 |
| 합성 전체 시간·lock hold | 1.814초 | 1.272초 | 실보드 시간이 아닌 비용 모델 |
| 합성 lock wait | 0초 | 0초 | 경쟁 없는 fixture. 경합 성능 수치가 아님 |
| 실제 로컬 wall time 범위 | 0.386–0.397초 | 0.422–0.454초 | 최종 후보 측정 때 전체 회귀·부분 빌드 동시 실행. 실제 속도 개선 근거로 비교하지 않음 |

중첩을 제외한 대표 단계의 합성 exclusive 시간은 `platform.domains`
0.560→0.440초, `cpu.sample` 0.900→0.540초, `execution.observe`
0.148→0.064초다. `prepare_launch` inclusive는 중첩 CPU 검증 재사용으로
0.162→0.002초가 되지만 자체 exclusive는 모두 0.002초다. 반면 fresh guard를
보완한 `bind_launch` exclusive는 0.014→0.036초로 늘었다. 서로 포함되는
단계 시간을 전체 시간에 더하지 않는다. 실제 FS의 fsync 비용도 원본 보고서의
`storage_costs`로 별도 보관한다.

최종 원자료는 `BASELINE_CPU_ZEPHYR_REBOOT_COSTS.json`,
`AFTER_CPU_ZEPHYR_REBOOT_FINAL.json`, `BENCHMARK_COMPARISON_FINAL.json`이다.
진행 중 파일은 최종 비교에 사용하지 않는다. 비교 보고서의 source hash·조건·
세 표본·최대값·lock/storage 비용이 기준이며, 최종 전체 회귀와 패키징 검사는
이 비용 모델 결과와 별도로 실행 결과를 확정한다.
위 표는 합성 비교다. 아래 G5의 실보드 측정과 합산하지 않는다.
실보드에서도 기존 90초 만료 사례가 재현되지 않아 **그 원인이 해결됐다는 결론은 미검증**이다.

## T01–T20 수락 대응

아래는 설계서 시험 ID와 실제 regression 진입점의 대응이다. `로컬 근거`는
FakeXen/fake clock·실제 작은 파일 journal 또는 실제 systemd loader다.
전체 통합 실행의 최종 통과 수·로그는 실행 보고서에서 확정한다. 과거 로그의
중간 실패나 개별 시험 통과를 전체 완료로 바꾸지 않는다. 실보드 T01의 정상
협조 reboot·replay는 아래 G5에서 확인했으며 전원 부팅·unit 부분 성공 수락과 분리한다.

| ID | 시험 진입점/확인할 근거 | 범위·잔여 수락 |
|---|---|---|
| T01 | `test_xenova_execution_boundaries.ManagementBoundaryTest.test_cpu_reboot_keeps_fresh_boundaries_and_reduces_repeated_layouts` | 로컬 CPU-on 협조 reboot, 원 90000ms·new_cpu_verification complete. G5 후보 실보드 3회·원 replay 3회 통과. 기존 90초 만료 원인 해결은 미검증 |
| T02 | `test_xenova_cpu.CPUTest.test_unconfigured_and_absent_are_not_compliance_success`, `test_active_policy_corruption_read_error_and_zero_uuid_fail_closed`; boot readiness tests | 미설정/손상/현재 host 준비 구별. G5 초기 정책 활성화·현재 host 실제 배치 통과, 새 부팅·손상 주입은 별도 |
| T03 | `test_same_phase_cpu_evidence_is_one_use_and_keeps_full_two_sample_verification` | 같은 단계 일회성 재사용, sample 두 번 유지 |
| T04 | `test_evidence_rejects_host_intent_record_deadline_and_policy_changes`, `test_changed_affinity_after_preparation_is_incomplete_and_never_repeated`, CPU domain-set/incarnation tests | 변경 거절·CPU 실패. unconfigured binding의 incarnation/intent 변경도 boundary_review에서 검사 |
| T05 | `test_deadline_expiry_after_acceptance_cannot_gain_execution_on_replay`, boot lock contention, 기존 recovery busy tests | 원 기한·미수락 lock 경계, 물리 명령 0회·의도 보호 |
| T06 | recovery review의 `test_R3_removed_cut_expiration_closes_old_attempt_and_allows_new`, network Zephyr의 제거 후 만료 시험 | 새 create 강행 금지, 원 attempt/단계 유지 |
| T07 | `test_xenova_boundary_review.BoundaryReviewTest.test_create_effect_then_transport_timeout_keeps_uncertain_owner_and_deadline` | 실제 fake 생성 뒤 transport timeout, 원 소유·기한 보존, 중복 create 없음 |
| T08 | `test_required_completion_write_failure_remains_uncertain_then_same_owner_closes`, recovery review 필수 완료 저장 실패 | 완료 위장 금지, 같은 owner/실행으로만 종결 |
| T09 | Manager의 `test_completed_start_replay_preserves_original_after_postquery_failure`, `test_failing_optional_trace_sink_does_not_reclassify_completed_start`, trace fsync 오류 시험 | 부가 조회/계측 실패와 필수 저장 실패 구별. G5의 독립 시험 프로세스에서 현재 관측 오류를 주입해 원 성공·null/warning·물리 명령 0회 확인. 실제 망 장애 주입은 아님 |
| T10 | Manager의 `test_replay_after_user_stop_keeps_original_success_and_current_absence_separate`, execution `test_reboot_observed_journal_survives_later_user_stop_without_cpu_revalidation` | G5에서 대표 A 협조 stop 뒤 같은 reboot replay 통과. 원 결과·기한 유지, 현재 absent/stopped, 물리 명령 0회. A 복원 완료 |
| T11 | `test_changed_affinity_after_preparation_is_incomplete_and_never_repeated`, CPU 불완전 적용 시험 | observed 실행의 cpu_verification incomplete를 성공으로 바꾸지 않음 |
| T12 | 근거 변경 시험, `test_legacy_unfinished_execution_never_receives_new_physical_budget`, 기존 lifecycle/key 충돌 시험 | host/lifecycle/intent/config·key 변경과 legacy journal의 실행 권한 제한 |
| T13 | recovery foundation barrier/concurrent tests, recovery review R1 사용자 stop 중단 지점, management-boundaries shutdown gate tests | stop·quiesce 우선, 소유권 보존. 실제 전역 gate 시험 미수행 |
| T14 | `test_xenova_reboot_observation.RebootObservationTest`, CPU `test_linux_toolstack_reboot_binds_new_identity_with_cpu_context` | Linux toolstack 재생성 방식 유지, 다른 UUID/name/incarnation 자동 채택 금지 |
| T15 | boot-boundaries batch/retry/CPU readiness, 기존 CPU autostart/recovery/Zephyr reboot, lifecycle shutdown tests | 함수의 부분 성공·미시작 구분은 로컬 통과. G5 격리 unit 실패/timeout에서는 같은 cgroup 자식이 종료됨. unit 전체의 부분 성공 보존은 수락하지 않음. 실제 전원 부팅/종료도 별도 |
| T16 | Manager `test_get_resolves_only_requested_view_and_list_observes_all_once`, 공유 15초·query error tests, execution batch tests | get 대상만, list 두 공유 목록, 전체 예산, 실패≠빈 성공. 큰 실제 VM 수 미측정 |
| T17 | recovery foundation P18/P19 snapshot, operation 조회 tests, boundary_review batch 저장 실패 | snapshot lock-free/no-Xen, 기록 조회는 reconcile 안 함 |
| T18 | legacy unfinished journal test, Manager actual dom0 mapper AST 소비, 전체 lifecycle/first-install regression | 정상 data.vm 소비 확인. 전체 제품 HTTP/Worker 종단 시험은 아님 |
| T19 | `test_xenova_trace.RuntimeTraceTest`, recovery review R2, Manager single JSON stdout | file/dir fsync 실패는 오류, trace 손실은 warning·drop 표시 |
| T20 | `test_cpu_evidence_cannot_skip_creation_of_complete_direct_call_evidence`, 기존 Manager 입력 오류 tests | 외부 입력 dict로 근거 위조 불가, 내부 직접 경로의 필수 검증 유지 |

로컬 전용 Manager/부팅 묶음은 `test_xenova_manager_boundaries.py`와
`test_xenova_boot_boundaries.py`다. 최초 등록 재개·동일 host 명시 stop·미승인
legacy boot choice·새 host CPU ready 거절·외부 dom0 mask 변경도 포함한다.
최종 전체 회귀·recipe/static packaging 결과와 실보드 미수행을 함께 보고해야 한다.

### 최종 로컬 검증

2026-10-24 정식 WSL 소스에서 `bash platform/scripts/check-xenova-platform.sh`를
실행했다. 804개 중 803개 통과, 실제 하위 UID 접근 시험 1개는 일반 사용자
실행 조건 때문에 skip했다. 해당 단일 시험은 별도 격리 root 실행에서 통과했다.
부트/init/network 회귀 20건, 실제 systemd loader의 정상 의존성·주입한 순환
탐지, 셸 구문·`git diff --check`도 통과했다.

고정 레이어·기존 캐시를 사용하는 새 build 경로에서
`bitbake xenova-platform -c package_qa`가 성공했다. 실제 packages-split의
Python 모듈 16개와 service unit 8개를 소스 hash와 대조하고 패키지 경로만으로
import했다. 이는 하위 플랫폼의 부분 패키지 검증이며 WIC·최종 이미지 수락이 아니다.

실행 증거는 PC의 `Documents/Codex/2026-10-24/xenova-management-boundaries`에
있다. `final-source-inputs.json`, `full-regression-r4.log`,
`isolated-root-consumer.log`, `partial-package-verification.json`,
`BENCHMARK_COMPARISON_FINAL.json`을 함께 사용한다.

## Worker·dom0-manager 인계

기존 호출을 유지한다. 아래 예제의 UUID와 key는 소비자가 관리하는 원 값이다.

```python
# 최초 요청과 전송 장애 재전달은 같은 action/spec/key를 사용한다.
manager.call("reboot", {"vm_id": vm_id}, execute=True,
             idempotency_key=original_key, request_id=request_id)

# 기록 조회는 부작용 없이 원 진행/결과만 본다.
manager.call("control-operation", {"operation_id": operation_id})
manager.call("recovery-operation", {"recovery_attempt_id": attempt_id})

# 현재 관측은 별도 요청이다. probe의 timeout_ms는 기존 필수 입력이다.
manager.call("get", {"vm_id": vm_id})
manager.call("probe", {"vm_id": vm_id, "timeout_ms": 1000})
```

| 필드/오류 | 소비자 해석·재시도 원칙 |
|---|---|
| `operation_id`, `replayed`, `original_result` | 원 작업과 연결. request_id를 멱등 key로 바꾸지 않음 |
| `current_observation` | 이번 현재 관측. null은 미확인이고 부재가 아님 |
| 정상 `data.vm` | 기존 dom0 mapper가 configuration.vcpus/memory_mib, vm_id, domid, xen_state를 계속 소비 |
| 완료 replay의 `vm=null` + warning | 정상 최초 start와 다른 예외 의미. 기존 mapper의 부재(404)로 단순 변환하지 말고 원 결과와 현재 미확인을 구별하도록 담당자 연결 필요 |
| `DEADLINE_EXCEEDED` | 원 실행 기한 만료. 같은 요청에 기한을 늘리거나 자동 물리 재시도하지 않음 |
| `RECONCILE_REQUIRED` | 원 소유/기록 확인 필요. 원 요청 재전달이 허용하는 종결만 수행, 새 key를 만들어 우회하지 않음 |
| `TARGET_CHANGED`, `INTENT_MISMATCH` | 현재 host/신원/의도/구성이 바뀜. 오래된 요청 그대로 강행 금지 |
| `CPU_NOT_READY`, `CPU_APPLY_INCOMPLETE`, `CPU_POLICY_INVALID` | 시작 준비/배치 실패. 자동 repin·policy 수정·force stop으로 확대하지 않음 |
| `OBSERVATION_UNAVAILABLE` | 조회 자체 실패. 현재 domain 부재나 OS 장애로 단정하지 않음 |
| `OPERATION_IN_PROGRESS` | 잠금/소유 충돌. 원 작업 상태를 확인한 뒤 동일 요청의 허용된 재시도. 새 물리 실행을 병행하지 않음 |
| `DIAGNOSTIC_UNAVAILABLE` warning | 부가 관측/trace 손실. 필수 completed의 성공을 뒤집지 않음 |

상위 HTTP 코드 분류는 기존 mapping을 우선한다. 현재 조회 미확인과 역사적 성공,
미완료 작업의 안전한 재판정이 필요한 경우의 사용자 표시를 담당자가 연결해야 한다.
이 문서가 Worker·FastAPI의 직렬화 대기·HTTP timeout·오류 mapping을 구현하거나
검증한 것으로 해석하면 안 된다.

`data.vm` 정상 응답의 호환은 예외 응답 전체의 호환이 아니다. 특히 완료 replay는
`ok=true`, `original_result` 유지와 함께 `vm=null`, `current_observation=null`,
`diagnostic_warnings`가 올 수 있다. 이를 VM 부재·원 작업 실패로 바꾸거나 새
reboot를 발행하면 안 된다. 현재 정보를 표시하려면 별도 get/probe의 성공을
확인한다. 기존 dom0-manager의 nullable 처리·HTTP 매핑·화면 연결은 담당자가
추가해야 하며 이번 하위 작업에서는 변경하지 않았다.

## 실보드 승인·원복 준비

2026-10-24 기존 인증·호스트키로 조회한 설치본은 CPU 기능 배포 이전 상태였다.
host_boot_id는 `2a879e42-5a8d-4196-b77c-a30b229486c9`, 커널은 `6.18.33-v8`이다.
`/usr/lib/xenova/xenova_cpu.py`가 없고 `cpu-layout`은 `INVALID_SPEC`을 반환했다.
CPU 정책·활성화 표식·정책 journal 경로도 없었다. Xen은 pCPU 4개·Pool-0·Credit2이며
dom0의 4 vCPU와 실행 중인 Debian/A/B의 hard affinity는 모두 `all`이었다.
따라서 이 설치본에서 곧바로 CPU 활성 대표 경로를 시험했다고 보고할 수 없다.
기존 인증을 사용한 조회만 수행했으며 VM 제어·정책·affinity·설치 파일은 변경하지 않았다.
다음 승인 검토는 CPU 기능의 설치 정합성·초기 정책 활성화와 기본 VM 영향을 포함해야 한다.

최초 로컬 구현은 보드 변경 권한을 포함하지 않았다. 이후 2026-10-25 사용자가
CPU 기능 적용·정책 활성화·필요 기본 VM 일시 정지를 승인했고, 아래 G5를 수행했다.
다음 목록은 최초 검토 기준이며 추가 실패 주입·전원 시험 권한으로 확대하지 않는다.
실제 UUID·경로·hash·현재 IP는 보드 읽기 기준선을
다시 수집해 채운다. 과거 DHCP 주소·domid·host_boot_id를 그대로 사용하지 않는다.

1. **후보와 대상**: 별도 소유 network Zephyr 시험 VM 한 대, 필요하면 소유 Linux
   회귀 VM 한 대. 현재 firmware·자원·broker 조건·등록 UUID·CPU 정책을 고정하고
   기존 설치본과 별도 후보 코드 사본을 비교한다. 파일 배포·서비스 교체 범위를 명시한다.
2. **보호 목록**: 기본 Debian·Zephyr A/B·다른 VM·불변 템플릿·저장소·관리 Wi-Fi/SSH·
   UART·boot 파일·host admission을 보호한다. 현재 CPU 정책이 없거나 dedicated
   core가 충돌하면 임의로 전체 VM을 정지하거나 정책을 변경하지 않고 별도 승인받는다.
3. **사전 증거/백업**: PC 시각·host_boot_id·단조 시각, 설치 파일과 후보 hash,
   VM/asset UUID·hash, 영구 intent/revision·operation/attempt·execution 소유,
   실제 전체 domain 집합/affinity·CPU ready 문맥, unit 상태·자원 사용을 PC에 보관한다.
   비밀번호·토큰·환경 전체를 수집하지 않는다.
4. **고정 비교**: 같은 CPU 정책 on/off의 필요한 대표 조건에서 소수 반복으로
   협조 reboot·기록 조회·원 요청 replay를 비교한다. 최종 execution/CPU/완료 저장,
   trace 단계별 시간·명령 수·lock/storage 비용, replay 물리 명령 0회를 확인한다.
   M3 네 조건 전체나 실제 주행 성능 시험으로 범위를 확대하지 않는다.
5. **영향/중단**: SSH 단절은 원격 취소가 아니다. 재접속 후 같은 operation/journal/
   프로세스/domain을 대조한다. 예상 밖 기본 VM·Wi-Fi 영향이면 추가 mutation을 멈춘다.
   미확정 owner를 지우거나 broad kill·새 key 반복·force로 해결하지 않는다.
6. **원복**: 실행 중 작업의 종결/불확실 여부를 먼저 확인한 뒤 소유 후보만 원본
   hash와 대조해 복원한다. 정책 변경이 승인됐다면 원 정책·현재 host 준비·실제
   affinity를 별도 검증한다. 성공 VM 임의 rollback·현재 Wi-Fi 조치 제거는 하지 않는다.
7. **실제 전원 부팅**: Xen→dom0→domU 전원 부팅·종료 수락은 로컬 unit 검사의
   대체물이 아니다. 필요한 경우 다른 비재부팅 시험 뒤 사용자 작업으로 묶는다.
   Wi-Fi 영구 복원 조건·UART 복구·전후 host_boot_id·등록/intent·CPU ready·
   autostart 부분 결과를 확인한다. 정상 reboot 한 번으로 poweroff·종료 거절·경합을
   모두 통과 처리하지 않는다.

full image 재빌드·조립·SD 기록·commit/push는 별도 사용자 지시 전 수행하지 않는다.

## G5 실보드 결과와 별도 수락 경계

2026-10-25 동일 host_boot_id `2a879e42-5a8d-4196-b77c-a30b229486c9`에서
검증한 부분 패키지 29개 파일을 백업 후 적용했다. Wi-Fi 파일·서비스·SSH·boot
설정·VM 등록/자산은 보존했다. 초기 정책 등록을 위해 기본 A/B와 Debian을 협조
종료하고 policy revision 1과 현재 host의 dom0 준비를 확정한 뒤 세 VM을 복원했다.
A는 UUID `64b1ab38-89cc-4454-8d94-b8201f4cdd33`→pCPU 1,
B는 `a77fa3fc-64e5-4d66-8225-2a49920bda2a`→pCPU 2다.
dom0의 네 vCPU와 Debian은 각각 hard affinity `{0,3}`를 사용하며 Pool-0·Credit2를 유지한다.

### 비교 입력과 실제 진입점

시간 기준선은 CPU 기능을 이미 포함한 고정 Git `8666712` 사본이다. CPU 기능이
없던 최초 보드 설치본은 배포 전 상태 확인에만 사용했고 최적화 비교에서 제외했다.
후보는 최종 로컬 회귀·부분 패키징을 통과한 모듈 집합이다. 같은 revision 1 정책,
동일 firmware·VM 구성/UUID·디스크 inode/크기·dom0+세 guest·저장소를 사용했다.
대상 A의 재부팅에 따른 incarnation/intent revision 변경은 정상적인 시험 결과다.
불변 firmware·커널·initrd·등록 파일은 해시를 대조했다. root.raw는 guest가 쓰는
파일이므로 내용 해시 불변을 주장하지 않는다.

관리 진입점은 각각 선택한 Python 라이브러리의
`xenova_management.Manager.call("reboot", {"vm_id": A}, execute=True, idempotency_key=key)`다.
HTTP 지연 측정이 아니다. 두 조건 모두 API와 Worker를 잠시 정지해 같은 조건을
유지했고 종료 후 원래 active 상태로 복원했다. 상위 서비스 소스는 변경하지 않았다.
측정 loader는 핵심 모듈의 실제 경로를 선택한 source root와 대조했다.
추가 cold-load CPU 조회·완료 reboot replay에서 실제 로딩된 플랫폼 모듈
10개/11개(후보의 `xenova_trace` 추가)의 경로·SHA-256, Python 3.14.7,
공통 identity helper·설치 CLI·시험 진입점 해시를 봉인한 입력 manifest와 대조했다.
이 추가 import audit를 과거 각 측정의 전 모듈 import trace라고 표시하지 않는다.

### 개별 협조 reboot 수락

| 항목 | 고정 기준선 3회 | 최종 후보 3회 |
|---|---:|---:|
| 실제 Manager 호출 시간 | 48.472 / 48.151 / 48.408초 | 34.375 / 34.752 / 34.374초 |
| 평균 | 48.344초 | 34.500초 |
| 외부 명령 | 매번 209회 | 매번 143회 |
| CPU require / sample | 7 / 14회 | 4 / 8회 |
| Execution observe | 4회 | 2회 |
| file fsync / rename / directory fsync | 각각 22회 | 각각 21회 |
| 물리 reboot / create | 각각 1회 | 각각 1회 |

관측된 평균 차이는 약 28.64%다. 독립 성능 보장이나 원래 90초 지연 원인의
해결을 뜻하지 않는다. 기준선에서도 90초 문제가 재현되지 않았다. 후보의
binding guard exclusive 비용은 오히려 늘었으며 필수 fresh 검증을 생략하지 않았다.
file fsync 누적은 기준선 46–50ms, 후보 51–562ms였다. 후보 한 회의 저장 지연도
그대로 보존했으며, 저장 1회 감소를 전체 약 13.84초 차이의 주원인으로 해석하지
않는다. Python lock/fsync 계측은 libxl 자식 내부 I/O를 제외한다.

세 후보 요청은 새 실행·CPU 검증 완료·completed 내구 저장을 확인했다.
각 원 요청 replay는 1.88–1.93초, 추가 물리 명령 0회였다. 이후 A만 협조 stop한 뒤
같은 완료 reboot를 재전달해 원 결과/operation/deadline 유지와 현재 absent/stopped를
확인했다. 독립 시험 프로세스에서 현재 관측 오류를 주입한 replay도 원 성공을
유지하고 `vm=null`/warning을 반환했다. 실제 Wi-Fi 장애 주입은 하지 않았다.
두 추가 replay도 물리 명령 0회였으며 A를 정상 start로 복원했다. B·Debian의 실행은
이 추가 시험 중 동일했고 다른 등록 VM·자산과 관리 Wi-Fi/SSH 프로세스를 보존했다.

### 저장 1회 감소의 내구성 근거

제거된 것은 완료 후 `Manager._view(observe=True)`의 응답 조립용 재관측이
다시 쓰던 `/run/xenova/observations/<UUID>.json` 게시 1회다. 전체 저장의
file fsync→rename→directory fsync 순서를 지운 것이 아니다.
동일 fixture의 `Execution.write` 목적지·단계를 추가 계측한 결과
observation 게시만 4→3회이며, control-requests 8회·execution 4회·intent 1회·
boot admission 1회·event 2회는 동일했다.

후보는 `bind_launch`에서 fresh 신원·CPU 검증 후 `cpu_verification=complete`인
execution을 내구 저장하고, 검증한 observation을 게시해 소유 reboot에 전달한다.
그 뒤 원 control의 `new-observed`와 `completed`를 각각 내구 저장한다.
응답은 이 완료 근거를 사용한다. 따라서 필수 실행/완료 저장을 부가 현재 조회로
대체하지 않았고, 필수 완료 저장 실패는 기존 uncertain/오류 시험으로 검증한다.
`/run` execution/observation은 현재 host 실행 문맥이며 전원 재부팅 뒤 보존되는
저장소가 아니다. 영구 `/srv/xenova/control-requests`의 완료 기록과 구별한다.

### autostart unit의 부분 성공 보존은 별도 미수락

실제 `xenova-vms.service`는 oneshot/RemainAfterExit,
TimeoutStartSec=150s·TimeoutStopSec=110s·KillMode=control-group·SendSIGKILL=yes다.
제품 unit의 KillMode나 의존성을 변경하지 않았다. 같은 기본 KillMode와 상속 cgroup을
사용하는 WSL 격리 unit에서 monitor/backend 모형 자식을 먼저 준비한 뒤 launcher
실패와 start timeout을 각각 발생시켰다. 두 경우 모두 자식이 SIGTERM으로 종료됐다.
짧은 3초 timeout은 격리 fixture에만 적용했다.

따라서 Python batch가 먼저 시작한 VM을 rollback하지 않는다는 함수 시험만으로
unit 실패 후 monitor/qdisk 보존을 수락할 수 없다. 이번 실보드의 기본 VM은 수동
Manager 경로로 복원돼 monitor/guest qdisk가 sshd cgroup에 있었고 공용 qdisk는
별도 backend unit에 있었다. 이는 실제 autostart 실패 후 생존 증명이 아니다.
실제 VM의 autostart 실패 주입·서비스 중단은 수행하지 않았다. 소유 관계와 실패
처리를 별도 설계·승인·검증하기 전까지 unit 전체의 부분 성공 보존은 미수락으로
남긴다. 개별 reboot 수락, 전원 부팅/종료 수락과 각각 분리한다.

원자료는 PC `Documents/Codex/2026-10-25/xenova-management-live`의
`COMPARISON.json`, `SUPPLEMENT_VERIFIED.json`, `WRITE_BASELINE.json`,
`WRITE_CURRENT.json`, `UNIT_CGROUP_RESULT.json`과 해시 검증한 두 board archive에 있다.
소스·부분 패키지·보드 임시 적용의 성공을 최종 이미지 수락으로 확대하지 않는다.

## 후속 A/B/C 수락 경계 (2026-10-25)

아래 세 항목은 앞의 G5 개별 reboot 결과와 별도다. 특히 G5 시간 비교에는 이번
process scope/stop 수락 보완이 포함되지 않았으므로 그 수치를 새 후보의 성능으로
재사용하지 않는다. FastAPI·Worker·Spring 제품 소스와 설정은 변경하지 않는다.

### A. 호출 경로와 시간 예산

시간은 순서 대기(Q), 직렬화(S), 플랫폼 잠금(L), 하위 실행(E), 필수 저장/반환(P),
만료 후 제한된 결과 재확인(R)으로 구분한다. 한 Manager 작업의 절대 기한은 E만이
아니라 Manager 진입 이후 L과 검증을 포함한다. S는 그 진입 **이전**이다.
외부 HTTP 요청의 Q/S 대기가 하위 기한 안에 들어갔다고 가정하면 안 된다.

| 진입점 / 실제 경로 | 순서·잠금 | 하위 실행·저장·재확인 | 바깥 계약과 판정 |
|---|---|---|---|
| HTTP list/get → `VmService.list/get` → `PlatformDriver._views/_view` | FastAPI 동기 route의 threadpool Q; `SerializedManager` S 최대 5초; L 충돌은 즉시 오류 | Manager 진입부터 목록 전체/단건 전체 15초; 개별 xl 호출은 잔여 기한 이하. 관측 게시 fsync와 반환 비용도 있음 | OpenAPI의 조회 5초·Spring 기본 read 6초와 불일치. `5+15`만으로도 6초를 넘을 수 있음 |
| HTTP health/stats → driver의 직접 관측 | 공통 Manager S/L을 통과하지 않음. threadpool Q는 별도 | health는 xl info subprocess 5초 및 registry 존재 검사; stats는 registry scan을 포함한 요청 전체 5초를 xentop과 xl 관측이 공유 | get/list의 15초와 다른 경로. 저장소 syscall을 강제로 선점하는 보장은 아님 |
| HTTP stop → `VmService.stop` → `PlatformDriver._stop` → Manager stop | VM별 mutex Q는 현재 무기한; 그 뒤 S 5초. 이번 하위 stop은 L 이전에 짧은 admission 수락(최대 2초의 잠금 대기)을 수행 | 최초 Manager 진입부터 90초; 의도·완료 journal의 필수 저장. 제출된 동작의 결과가 미확정이면 원 작업 관측 R 최대 15초를 별도로 쓸 수 있음 | Spring command 기본 100초는 Q가 없다고 해도 `5+90+15+반환 여유`를 포괄하지 못함. Q가 무기한이면 유한한 종단 상한 자체가 없음 |
| 직접 CLI/Manager start·Linux reboot·Zephyr 협조 reboot·명시 destroy | HTTP Q/S 없음; 공통 L. stop 이외 요청은 L 전에 의도를 쓰지 않음 | 진입부터 90초를 공유. 신원/CPU/의도 guard·물리 호출·완료 저장을 구별. Linux toolstack reboot 방식 유지 | CLI 종료나 HTTP 단절은 이미 제출한 Xen 동작 취소 증거가 아님 |
| HTTP create → `VmService.create` | VM별 Q + 자원 mutex Q; has-domain get → xl host-info → registration get → 필요 시 등록 create → start 각각의 S/L | get 각각 전체 15초, xl info는 별도 subprocess 한도; 자산 준비 create와 start는 다른 작업. 등록 create에 control의 90초를 적용하지 않음 | OpenAPI create 30초·Spring command 100초는 이 전체 경로의 합산 보장이 아님. 전체 생성 요청 기한과 단계별 잔여 전달이 먼저 필요 |
| HTTP delete → `VmService.delete` | VM별 Q; 실행 get → 등록 get → retire 각각 S/L | retire는 자산 수명주기 journal/저장 경로. stop이나 강제 삭제가 아님 | OpenAPI의 명령 시간만으로 전체 Q와 여러 호출 비용을 포괄할 수 없음 |
| Worker 관측 → `PlatformObserver` | Worker 프로세스의 별도 SerializedManager; FastAPI와 Python mutex는 다르지만 플랫폼 L은 공유 | refresh list 전체 15초 + templates 조회; 주기 Xen 관측은 별도 xl 5초; confirm get 전체 15초 | 감시 주기 5초가 실행 timeout이 아님. 바쁜 주기는 건너뛰며 과거 근거로 새 복구를 허가하지 않아야 함 |
| 하위 `recover_once` 직접 호출 | 요청의 원 host/intent/execution/attempt와 L | `deadline_monotonic_ms` 원 절대 기한. 최초 수락 시 미래 120초 이내. 재전달로 갱신하지 않음. R 최대 15초는 새 create/reboot 권한이 아님 | 현재 설치 Worker는 판단 큐까지만 연결돼 있어 실제 attempt 실행 110초 설정/전달 경로가 없음 |
| autostart CLI / unit | CLI 진입에서 한 batch 기한, L 충돌 즉시 실패 | 140초 하나를 등록 조회·준비·모든 VM이 공유. 필수 저장/반환은 별도 비용 | unit 시작 150초. VM마다 새 140초를 주지 않음. 부분 성공과 systemd cleanup은 B에서 별도 검사 |

승인된 독립 보드 시험에서 Debian 1.0.2의 8GiB 템플릿 등록은 1,353.370초였다.
선택·준비·게시 전 원본 해시 대조와 디스크 복사를 포함한 등록 시간이며 reboot
시간이 아니다. `prepare_instance.digest()`의 개별 sha256sum 상한은 900초이고,
이 값도 등록 요청 전체 상한은 아니다. 생성 HTTP의 종단 기한·단계별 잔여 전달과
중복 무결성 검사의 정리는 별도 검토 대상이며 이번 reboot 최적화 성과로 계산하지 않는다.

90/15/140초는 내부의 협조적 전체 예산이다. 이미 시작한 파일 fsync, 커널 I/O,
프로세스 생성까지 선점하는 엄밀한 wall-clock 상한이 아니다. 필수 완료 저장이
실행 기한 직후 끝나거나 R이 사용되면 실제 반환은 더 늦을 수 있다. R로 같은
물리 명령을 재제출하지 않는다. 특히 `xl_command_timeout_s=95`는 `xl` 드라이버의
subprocess 설정이며, platform Manager 전체 호출의 timeout 설정이 아니다.
API unit의 `TimeoutStopSec=100` 역시 HTTP 요청 timeout이 아니라 서비스 종료 시
정리 상한이다. 현재 uvicorn 진입점에는 별도 요청 실행 deadline 전달이 없다.

확인한 입력은 Git `b37fd19`와 이번 후보, 보드 boot
`2a879e42-5a8d-4196-b77c-a30b229486c9`의 설치 모듈 경로/해시 및 허용한 숫자
설정이다. API의 실제 driver=platform, xl query=5초, xl command=95초,
shutdown grace=90초, serialization busy=5초를 확인했다. Spring의 실행 프로세스/
환경은 이 보드에서 확인하지 않았으며 2/6/100초 connect/query/command 및 500ms
backoff는 `backend/src/main/resources/application.yml`의 **소스 기본값**이다.
전체 환경변수나 토큰·암호를 수집하지 않았다.

Worker의 실제 숫자 설정은 interval=5초, intent refresh=60초, agent heartbeat
lost=30초, app heartbeat lost=15초, boot grace=90초, Zephyr boot grace=30초,
absent grace=60초, health refresh=15초다. 모두 장애 판정/관측 유예·주기이며 물리
복구 실행 시간과 다르다. 현재 `recovery.worker`는 `judgments.put()`까지 수행하고
`recovery.__main__`에는 소비자/executor 조립이 없다. 따라서 '110초 attempt가 실제
동작한다'고 보고하지 않는다. 120초는 하위 수락 상한이지 기본 110초의 증거가 아니다.

| 변경 담당/위치 | 권장 계약·설정 또는 전달 방식 | 근거/전제 |
|---|---|---|
| FastAPI `SerializedManager`, `VmService` | 요청의 원 deadline을 Q 이전에 정하고 단계별 잔여 예산으로 잠금/호출을 제한. 수락된 stop을 위한 짧은 경로와 긴 실행 직렬화를 구분 | 지금 S가 하위 기한 밖이고 VM/resource Q는 무기한. 숫자만 늘려 해결할 수 없음 |
| Spring/OpenAPI 조회 | 현행 하위 15초를 유지한다면 S 5초 + E/P 15초 + 명시적 반환 여유. Q를 먼저 제한한 뒤 예를 들어 read 25초와 계약 5초의 수정 여부를 합의 | 25초는 적용값이 아닌, Q=0이고 반환 여유 5초라는 조건의 권고. 외부 프록시/호출자 설정도 함께 확인 필요 |
| Spring 일반 control | Q를 제한한 뒤 `5+90+최대15+반환 여유` 이상. 예: Q=0·반환 5초일 때 최소 115초이며 120초 후보를 검토 | 무조건 연장이나 실패 숨김이 아님. 실행 기한은 계속 원 90초. 현재 100초만으로는 부족한 경로가 존재 |
| Worker executor 담당 | attempt 최초 생성 시 `host_boot_id`에 결합한 `D=monotonic_now+110초` 기본, 최대 120초를 검증하고 **동일 D/attempt**를 하위에 전달. 재큐잉·재전달 시 `now+110` 재발급 금지 | 현재 연결이 없으므로 제안/인계이며 구현 완료가 아님. dispatch Q도 D에서 차감. 반환 대기는 잔여 D + R 15초 + 저장/반환 여유를 별도로 고려 |
| FastAPI 오류 매핑/호출자 | `accepted`, `completion`, operation/attempt ID, 원 deadline을 구조적으로 보존하고 원 요청을 재확인 | 현재 `raise_for_envelope`는 details를 사람용 오류로 축약. HTTP 503만 보면 미수락과 수락 후 실행 대기를 구별할 수 없음 |
| Spring 재시도 담당 | 현재 query는 transport 실패 1회 재시도, command는 connect 실패만 1회 재시도한다는 구분 유지. 전송 후 timeout으로 새 키/새 attempt 실행 금지 | `Dom0ManagerClient.executeQuery/executeCommand` 실제 구현. 응답 유실 시 원 operation/attempt 조회·대조가 우선 |

기존 ARM WIC의 FastAPI/uvicorn과 현행 상위 소스를 사용한 loopback 격리 시험은
`platform/tests/run_manager_http_boundary.py`에 있다. 하위 Manager만 대역이므로
HTTP 생명주기/상위 잠금 증거와 하위의 실제 journal 경쟁 시험을 구별한다.

### B. autostart 지속 프로세스 소유와 실패 처리

보드 systemd는 259.5, 격리 WSL PID1은 255다. 설치 unit/drop-in의 적용값은
oneshot/RemainAfterExit=yes, KillMode=control-group, KillSignal=SIGTERM,
FinalKillSignal=SIGKILL, SendSIGKILL=yes, TimeoutStart=150초, TimeoutStop=110초,
Restart=no, ExecStop=shutdown-all, ExecStopPost 없음이었다. `Before/After`,
Requires/Wants 및 PartOf/BindsTo도 수집했다. 후보는 이 값들을 바꾸지 않는다.

원인 경로는 `autostart → xl create → fork된 libxl monitor/guest qdisk가 batch의
cgroup을 상속 → oneshot 시작 실패/timeout → 기본 control-group cleanup`이다.
이로 인해 필요한 Linux 프로세스가 종료될 수 있다. **그 사실은 Xen domain 제거와
동일하지 않다.** 관측한 G5 보드에서는 수동으로 복원한 monitor/guest qdisk가
sshd cgroup에 있고 공용 qdisk는 별도 backend unit에 있어, 이 보드의 현재 상태를
autostart 실패 보존 증거로 삼지 않았다.

최소 수정은 각 새 실행의 `xl create`를
`xenova-vm-<VM UUID>-<execution UUID>.scope`에서 수행하는 것이다.
`xenova_guest_process.py`가 공통 시작 경로와 Linux reboot의 제출 직전 guard를
담당한다. 별도 daemon이나 대체 VM 상태 기계는 만들지 않는다.

- 지속 libxl/guest qdisk는 해당 실행 scope에 남고 batch와 cgroup 소유가 다르다.
- 일시 xl 명령에는 Linux PDEATHSIG=SIGKILL을 걸어 호출자 사망 때 정리한다.
  fork 자식에게는 이 설정이 상속되지 않는다. 부모 PID도 설정 직후/exec 직전에 대조한다.
- scope에는 `Before=xenova-vm-shutdown.service`를 둔다. host 종료의 역순에서는
  기존 shutdown guard가 guest를 정리한 뒤 scope가 정리된다. 실패한 oneshot에는
  ExecStop이 실행되지 않으므로, 독립된 기존 shutdown guard를 계속 사용한다.
- 신뢰한 Xen 부재 확인 뒤만 해당 VM/실행 소유 scope를 정리하고 최종 비활성 상태를
  확인한다. 원 실행 기한 안에서 TERM으로 backend의 flush/정상 종료 기회를 주며
  자동 KILL로 성공을 만들지 않는다. R 예산에서는 신호 없이 종료 상태만 관측한다.
  다른 VM·공용 backend나 Xen destroy를 scope cleanup에서 호출하지 않는다.
- Linux 일반 reboot는 기존 toolstack 재생성 방식과 monitor scope를 유지한다.
  구형 실행에 scope가 없었다면 새 scope에 들어갔다고 기록하지 않는다. 기존 실행을
  자동 이동시키지 않고 다음 명시적 stop/start의 create부터 적용한다. explicit create에는
  새 scope를 쓴다. CPU 실패를 이유로 신원 검증이나 stop을 막지 않는다.

`platform/tests/run_autostart_systemd_isolation.py`는 실제 systemd에 독립 unit과
소유 dummy monitor/backend/일시 프로세스 및 fake Xen marker만 만든다.
명시적 두 번째 VM 실패, 내부 batch deadline, 외부 unit timeout/최종 SIGKILL,
부분 실패 뒤 같은 unit 재시도, 명시적 종료와 shutdown guard 순서를 검사한다.
첫 성공 marker/create 횟수와 PID/PPID/cgroup, unit Result·journal 및 최종 생존/사망을
함께 남긴다. 제품 KillMode/Timeout/Restart는 변경하지 않으며 짧은 timeout은
격리 fixture에서만 쓴다. 실제 Xen guest의 batch 실패 보존 수락은 별도로 남는다.

동작 근거는 [systemd scope](https://www.freedesktop.org/software/systemd/man/latest/systemd.scope.html),
[service 종료 처리](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[Linux PDEATHSIG](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html)다.
실제 255/259 검증과 최신 매뉴얼의 버전을 동일하다고 가정하지 않는다.

### C. 복구 잠금 중 stop 의도 수락

| 수락 항목 | 현재 하위 구현 | 상위 종단 상태 |
|---|---|---|
| 이미 수락한 stop의 의도를 물리 종료 전에 저장 | 기존 authoritative intent/revision·control journal 유지 | 정상 완료와 물리 미확정 오류를 구별 |
| 복구가 실행 잠금을 잡은 중에도 별도 stop 의도 수락 | 이번 `Manager._accept_stop`이 실행 잠금 전에 짧은 admission 잠금으로 검사·저장 | FastAPI 직렬화 및 VM mutex 때문에 HTTP 종단 수락은 아직 성립하지 않음 |

공개 stop 입력/action과 정상 완료 `data.vm`은 유지한다. 내부 이름 CLI도 공통
Manager 수락 경로를 사용한다. durable intent 저장을 확인한 시점이 수락 시점이며,
저장 오류에는 `STORAGE_UNAVAILABLE`, acceptance=unconfirmed를 반환한다.
prepared journal만 있거나 rename은 보였지만 directory fsync가 실패한 상태를
접수 성공으로 꾸미지 않는다. 재시작 후 원 요청의 journal과 intent를 대조해 재개한다.
admission 잠금의 2초 대기만 소진했다면 원 실행 기한 만료로 표시하지 않는다.
이때는 OPERATION_IN_PROGRESS/retryable과 acceptance=not-checked를 반환한다.

잠금 순서는 긴 실행 잠금 → 짧은 admission만 허용한다. 빠른 stop은 admission을
해제한 뒤 실행 잠금을 시도한다. 복구는 이를 기다리며 긴 잠금을 역으로 요청하지
않는다. 초기 intent 생성·사용자 수락과 lifecycle tombstone 게시도 같은 admission
경계를 사용한다. 현재 host/lifecycle/base revision을 잠금 안에서 읽고 원자적으로
갱신하므로 오래된 prepared 요청이 새 사용자 revision을 덮지 못한다.

실행 잠금이 바쁘면 정상 완료 봉투를 반환하지 않는다. 예를 들어 입력이
`Manager.call("stop", {"vm_id": UUID, "force": False}, idempotency_key="stop-001")`
이면 반환은 다음 형태다. UUID/ID/시각은 설명용이며 실제 값은 원 기록에서 읽는다.

```json
{
  "schema_version": "1.0", "ok": false, "request_id": "request-id",
  "operation_id": "original-operation-uuid", "replayed": false, "data": null,
  "error": {
    "code": "OPERATION_IN_PROGRESS", "message": "another platform operation is in progress",
    "retryable": true,
    "details": {
      "accepted": true, "completion": "pending",
      "operation_id": "original-operation-uuid", "intent_revision": 12,
      "deadline_monotonic_ms": 123456, "execution_deadline_expired": false,
      "next_action": "replay_original_stop_after_execution_lock_is_available"
    }
  }
}
```

`ok=false`는 실제 정지 완료가 아니라는 뜻이다. accepted=true는 이미 내구 저장한
의도가 있다는 뜻이며, 아무 효과 없는 단순 busy 거절과 다르다. `completion`은
pending/failed/completed/superseded를 구별하며 journal phase를 성공으로 고치지 않는다.
`control-operation`으로 원 operation을 확인하고 같은 키로 재전달한다. 다른 사용자
명령이 나중에 수락되면 revision 순서를 따르고 과거 stop을 다시 실행하지 않는다.
원 기한이 끝났으면 재전달로 새 물리 권한을 만들지 않는다. stopped 의도는 보존하되
원 실행을 대조한 후 필요하면 **명시적인 새 stop**을 요청한다. 자동 force는 없다.
구형 미확정 journal에 원 deadline이 없으면 새 기한을 발급하지 않는다.
오류 details의 deadline/만료 여부는 null로 남기고 관측·원 기록 대조만 허용한다.

최종 create/Linux reboot 제출은 admission guard 아래 현재 host/기한/원 execution/
intent/configuration/CPU 문맥을 확인하고 exec에서 잠금 FD를 닫는 순서다. 긴 xl 응답
대기 동안 admission을 잡지 않는다. 이 경계보다 먼저 저장된 stop은 오래된 제출을
막는다. 경계 뒤 제출은 취소됐다고 가정하지 않고 원 journal/execution을 재확인한다.
여기서 제출은 xl child의 exec 수락 경계이며 Xen의 효과 완료와 다르다.
외부 root/xl 또는 이미 커널에 전달한 물리 효과까지 원자적으로 통제한다는 뜻은 아니다.

수락된 stop의 물리 이행 책임은 **같은 요청을 보유한 CLI/호출자**다. 긴 복구가
끝난 후 원 키로 재전달해 이행한다. background stop daemon은 추가하지 않았다.
키를 생략한 호출도 현재 pending stop journal에 재결합하지만, 응답 유실을 안전하게
관리하려면 호출자가 원 키와 operation을 보존해야 한다. 원 intent를 지우거나
새 key를 자동 생성해 물리 동작을 반복하는 방식으로 복구하지 않는다.

`test_xenova_stop_acceptance.py`는 실제 실행 flock을 보유한 별도 복구 요청과 공개
stop을 경쟁시킨다. 옛 실행 종료 대기/제거 직후/create 직전/create 효과 후 binding
미확정, 의도 저장 실패, 저장 직후 실제 자식 프로세스 종료·재전달, 새 사용자 start,
원 기한 만료, 바깥 대기 만료를 검사한다. 원 attempt/operation/intent revision/
execution·명령 횟수와 최종 정지를 함께 대조한다. 실제 Xen은 FakeXen 경계다.

상위 담당자의 최소 연결 사항은 기존 stop HTTP 경로에서 긴 VM mutex/공유
SerializedManager에 가로막히지 않도록 짧은 수락 경계를 호출하고 원 요청 키를
전달하는 것, 수락 대기와 정지 완료를 다른 응답으로 표현하고 operation/details를
보존하는 것이다. 기존 204 완료 응답의 뜻을 접수 완료로 바꾸면 안 된다. 새 HTTP
API나 daemon은 이번에 만들지 않았으며, 상위 연결 전 C를 종단 완료로 수락하지 않는다.

### 로컬 검증 결과와 남은 수락

| 실행한 검증 | 결과 | 수락 범위 |
|---|---|---|
| `python3 -m unittest discover -s platform/tests -p 'test_*.py' -v` | 837개, 836 PASS + 권한 분리 1개 | Manager/Platform/Execution, CPU, 복구·부팅·신원·자산·CLI 공통 회귀. dom0/guest의 기존 고정 systemd loader 사용 |
| 분리한 Wi-Fi consumer UID 시험 | root 격리에서 PASS | 소유 fixture의 실제 낮은 UID 접근만 확인. 실제 Wi-Fi 파일 변경 없음 |
| `test_xenova_stop_acceptance.py` | 20개 PASS | 별도 요청/실행 flock, 실제 자식 종료·재전달, 저장 실패·기한·후속 의도·정지 이행, 완료 replay의 잠금 충돌 |
| `test_xenova_guest_process.py` | 13개 PASS | exec 때 잠금 해제, 원 문맥·45초 create 상한, scope 소유·관측 전용 cleanup·빈 관측 거절, Zephyr/Linux 분기·미제출 종결 |
| `run_autostart_systemd_isolation.py run --output <결과.json>` | 실제 WSL PID1 systemd 255에서 PASS | 명시 실패·내부/외부 timeout, 최종 kill, 재시도·명시 종료·guard 역순. Xen은 대역 |
| `run_manager_http_boundary.py --output <결과.json>` | 기존 ARM FastAPI/uvicorn 런타임에서 4개 PASS | 실제 상위 HTTP/잠금/오류 매핑 코드, 하위 Manager만 대역 |
| `bitbake xenova-platform -c package_qa` | PASS, 실제 설치 runtime SHA-256 대조 | 별도 TMPDIR의 부분 타깃. 전체 이미지/WIC/SD 수락은 아님 |

### 승인된 후속 실보드 수락

2026-10-25 보드 systemd 259.5에서 독립 후보 runtime과 소유 Zephyr
`3bd58154-9f33-42e9-bab7-2d9dd4149c78`, Debian
`c7e8e0df-89eb-4ba7-81b8-28efb3505d45`만 제어했다. 설치 runtime·기본 VM·CPU
정책·기존 service/env는 교체하지 않았다. 단계마다 기본 VM의 구성·의도·실행
incarnation·CPU 배치, DHCP PID/기존 예약, Wi-Fi COMPLETED를 대조했다.

| 항목 | 직접 확인한 결과 | 수락 경계 |
|---|---|---|
| 정상 제어·재전달 | Zephyr 협조 reboot, Linux toolstack reboot, 같은 요청 replay와 후속 stop 뒤 replay 통과. 추가 물리 호출 0회 | Xen 실행·ICMP 확인. 앱 준비 수락은 아님 |
| B 실제 실패·재시도 | 소유 transient unit에서 Zephyr 성공 뒤 Debian 시작 경계 실패. 실패 후 Zephyr의 domain·scope 지속 프로세스·ICMP 보존, 임시 프로세스 제거. 재시도는 Debian create 1회, Zephyr execution 유지. unit stop 뒤 두 VM 유지 | 실행 scope 후보의 실제 Xen/systemd 시험. 외부 timeout의 최종 SIGKILL·host shutdown 역순은 WSL 실제 systemd 격리 증거이며 보드 전원 수락과 구분 |
| C 제거 직후·create 직전 | 복구가 실제 실행 flock을 보유한 동안 별도 Manager stop 수락. 복구 create 각각 0회, 원 stop operation/기한으로 이행·replay | 수락 시점은 내구 intent 저장. 실제 정지 완료와 구분 |
| C create 효과 후 | stop 수락 전 create 1회 발생. 취소됐다고 가정하지 않고 현재 신원 확인 후 같은 stop으로 협조 종료·replay | 원 recovery의 uncertain journal은 그대로 보존. 자동 force·중복 create 없음 |
| 완료 결과와 현재 조회 | 현재 조회 실패 주입 및 실제 실행 잠금 충돌에서 검증된 완료 결과 유지. `vm=null`/warning, 물리 호출 0회 | 현재 실행 상태를 과거 완료 결과로 대체하지 않음 |

후속 시험에서 확인한 최소 수정은 세 가지다.

1. scope를 쓰는 `Platform.control()`의 Linux reboot helper 분기를 Linux에만 적용했다.
   Zephyr는 기존 실행에 협조 요청을 보낸 뒤 공통 start에서 새 launch를 만든다.
2. 부모 또는 helper가 exec 전에 거절됐고 같은 launch·도메인 부재가 확인된 경우만
   기존 execution을 `stopped`로 종결하고 `launch_aborted_before_submit` 근거를 남긴다.
   뒤의 명시적 사용자 start가 가능해졌다. 이미 제출된 실행·존재 도메인·저장 실패는
   이 근거로 지우지 않으며 원 실패/uncertain operation을 성공으로 바꾸지 않는다.
3. 실행 잠금 충돌에서도 같은 요청의 내구 완료 journal을 검증할 수 있으면 control/
   recovery 원 결과를 반환한다. 현재 관측은 생략하고 warning을 붙인다. 만료 뒤
   짧은 기록 조회는 새 실행 예산이 아니다. 미완료·실패·다른 요청에는 적용하지 않는다.

CPU 활성 Git `b37fd19` 기준선과 최종 후보를 같은 정책 revision 1, firmware·VM
구성·전체 guest 집합·저장소에서 1쌍 비교했다. 실제 로딩 모듈/진입점의 경로·해시와
각 trace를 남겼다. 기준선 49.606초/외부 호출 159회, 후보 51.672초/162회였다.
후속 scope/수락 보완의 관측값이며 성능 개선으로 수락하지 않는다. 앞선 G5의
48.344→34.500초 결과나 원 90초 지연의 원인 해결로 확대하지 않는다.

상위의 시간 예산·직렬화/VM mutex·pending 응답 연결은 A/C의 인계 사항으로 남는다.
보드 전원 부팅/종료, 전역 autostart unit 실패 주입, 최종 이미지 수락은 수행하지
않았다. B는 scope 후보에서 직접 확인했고 이후 미제출/완료 replay 보완은 최종
후보의 전체 회귀·정상 reboot·실보드 C 경로로 확인했다.
`systemd-run`의 제공 패키지 `systemd-extra-utils`는 사용자 승인 후
`xenova-platform`의 명시적 RDEPENDS에 추가했다. 현재 보드에도 설치된 도구이며,
추천 의존성에 기대지 않고 scope 실행에 필요한 패키지의 포함을 보장한다.
부분 package QA와 의존성 검사는 최종 이미지 부팅 수락과 구분한다.

## A/B/C 후속 마감과 증거 연결

### 패키지 원본과 최종 후보

고정 빌드 `build-rpi4-management-closure-20261025`의
`tmp/pkgdata/raspberrypi4-64/runtime/`에서 다음을 직접 대조했다.

| 패키지 메타데이터 | 확인 필드 | 결과 |
|---|---|---|
| `systemd-extra-utils` | `FILES_INFO:systemd-extra-utils` | `/usr/bin/systemd-run` 포함 |
| `xenova-platform` | `RDEPENDS:xenova-platform` | `systemd-extra-utils` 포함. RRECOMMENDS만으로 포함하는 상태가 아님 |

승인한 recipe SHA-256은
`f672d58e17517869e93f79b25246f90182f7d98a04a9435dae67ed44aef27da1`이다.
추가 후 수행한 package QA는 2,298개 작업 중 3개 재실행으로 통과했다.
`package-qa.json`의 설치 runtime 5개는 최종 소스와 해시가 같고, 위 의존성은
생성된 pkgdata에서도 확인했다. 부분 패키징 결과이며 새 WIC/SD 수락이 아니다.
최종 runtime 후보 zip의 SHA-256은
`8a90038d9de97e4d0ad2ef2162d726b843b8ddef93ff3cd8c59b7396951b493d`로 유지된다.
recipe·pkgdata와 runtime zip은 서로 다른 산출물이다. 의존성 선언만으로 runtime
파일이나 기존 이미지가 바뀌었다고 보고하지 않는다.

실제 실행기의 `process_isolation=True` 경로는 scope 명령 실패를 상위로 전달한다.
직접 `xl create` 경로는 주입한 FakeXen 실행기에 사용하는 별도 분기이며, 실패 시
우회 경로가 아니다. 새 회귀의 도구 부재·scope 생성 거절·생성 효과 후 응답 실패
세 사례에서 scope 제출은 각 1회, 직접 실행 fallback과 원 요청의 재제출은 0회였다.
마지막 사례의 존재 domain은 보존했다. 실행 결과를 받지 못한 요청은 원
operation/execution의 `uncertain`과 `RECONCILE_REQUIRED`를 유지하며, 효과가 없었다고
단정하거나 자동 destroy하지 않는다.

### scope 수명주기와 B 증거의 범위

| 경계 | 실행 주체와 의도한 동작 | 근거·남은 범위 |
|---|---|---|
| 시작 batch 실패·내부/외부 기한 초과 | batch cgroup의 일시 프로세스만 정리. 성공한 실행의 scope·monitor/backend와 Xen domain은 별도로 관측 | 기존 WSL 실제 systemd 255 격리의 세 실패 사례·최종 SIGKILL, 보드 P01의 명시 실패로 확인 |
| 보드 시험 unit의 명시 stop | `xenova-management-closure-batch-20261025.service`는 batch 도구만 소유. stop 직전 `active/exited`, `Type=oneshot`, `RemainAfterExit=yes`, `ExecStop/ExecStopPost` 없음. 따라서 VM 종료 명령이 실행되지 않고 두 VM 보존이 계약 | `batch-retry-unit.json`, `board-batch-controller.py`, `batch-unit-stop-{zephyr,linux}.json`. 제품 서비스 종료 시험으로 확대하지 않음 |
| 개별 VM stop | 신원 확인·기존 graceful/force 정책으로 domain 종료 후 해당 execution의 소유 scope에 TERM, 비활성 확인. 다른 scope나 공용 backend는 대상이 아님 | 최종 후보의 실보드 stop 및 `test_cleanup_requires_absence_and_owned_shutdown_order_and_observes_final_state`. R 재확인에서는 신호 없음 |
| 제품 `xenova-vms.service`의 정상 stop/restart | 성공해 `active/exited`인 제품 unit은 `ExecStop=... shutdown-all`을 실행. 위 시험 unit과 달리 등록 VM 전체의 기존 종료 정책을 적용 | 기존 WSL 격리의 성공 재시도 뒤 명시 stop은 가짜 guest 정리 실행을 확인. 제품 unit을 실제 중단한 수락은 없음 |
| host 종료 | admission 차단 → 정상 vms ExecStop(실패한 시작이면 생략) → 독립 vm-shutdown guard → 실행 scope/backend/toolstack·저장소 정리 | 실제 unit 그래프와 기존 WSL shutdown guard 역순 사례. 실제 host 전원 종료 수락은 없음 |

실보드 P01 scope의 `Before`에는 `shutdown.target xenova-vm-shutdown.service`가
실제로 있었다(`batch-failure-persistent.json`). 기존 guest 종료 guard는 backend/
toolstack/mount의 `After`, vms의 `Before`에 놓인다. 동시에 stop될 때 순서는 역전되어
guard의 guest 정리 동안 실행 scope의 지속 프로세스가 먼저 정리되지 않게 한다.
WSL 격리는 guard 진입 때 monitor/backend 생존과 이후 최종 정리를 직접 확인했다.
다만 그 fixture는 가짜 guest marker와 KILL로 정리를 모형화하므로 실제 guest flush나
제품 host 종료의 증거는 아니다. 실제 개별 scope 정리의 TERM/기한 검사는 별도 회귀다.

시작에 실패한 oneshot의 ExecStop은 실행되지 않아도, 먼저 활성화된 독립
`xenova-vm-shutdown.service`는 host 종료의 정리 경계로 남는다. guest 종료 거절·기한
초과·scope 관측/정리 실패는 원 실패·미확정 기록으로 남기고 자동 force하지 않는다.
host 종료 guard 자체가 실패하거나 110초 unit 상한을 넘으면 systemd의 후속 종료가
계속되어 남은 프로세스가 정리될 수 있다. 이 경우까지 guest 보존을 보장하지 않으며
실제 보드 수락도 남아 있다. KillMode·DefaultDependencies·기존 종료 정책은 바꾸지 않았다.

P01→최종 후보는 보존한 원본과 AST 함수 단위로 대조했다. scope 이름·명령·소유 검사,
`cleanup/arm_parent_death/exec_owned`, `Execution.prepare_launch/bind_launch`,
`Platform.autostart`는 동일하다. 달라진 경계와 적용한 증거는 다음과 같다.

| 변경 | 영향 범위와 확인 근거 |
|---|---|
| `Platform.control`의 Linux reboot helper 분기 제한 | Zephyr 협조 요청을 Linux 재생성 helper로 보내지 않음. 최종 후보의 정상 Zephyr/Linux reboot 실보드 및 분기 회귀로 확인 |
| `Platform.start`, helper `main`, 신규 `Execution.abort_unsubmitted_launch` | 제출 전 guard 거절에서만 domain 부재·동일 실행을 확인하고 stopped로 종결. scope 생성/정리 함수의 정상 동작은 동일. 기존 guard/저장 실패 회귀·최종 보드 C 경로와 이번 실패 전달 3개로 확인 |
| Manager의 완료 replay 보완 | 현재 조회/잠금 실패와 내구 완료 결과를 분리. scope 소유/종료 순서는 바꾸지 않음. 기존 완료 replay·후속 stop 뒤 replay의 물리 호출 0회 증거 적용 |

P01의 B 전체를 최종 후보에서 다시 실보드 수행했다고 표시하지 않는다. 동일한 scope
핵심과 기존 실제 systemd 증거를 연결하고, 변경된 제출 전/완료 replay 경계의 증거를
별도로 적용했다. 이번 마감에서는 보드 시험 환경을 재구성하지 않았다.

### C 수락 직후 프로세스 종료와 이행 책임

기존 stop 20개 중 실제 프로세스 종료 시험이 이미 있었다. 이번에는 해당 시험의
원 execution·host/lifecycle·intent revision·deadline과 완료 후 replay 검증을 보강했다.

| 시험 ID (`test_xenova_stop_acceptance.py`) | 확인한 사실 |
|---|---|
| `test_real_process_exit_after_durable_intent_is_resumed_by_original_stop` | `intent-published`에서 실제 자식 `os._exit(73)`. intent는 내구 저장됐지만 journal은 prepared, 물리 stop 미제출. 새 Manager가 원 키로 재개해 같은 operation/revision/request/deadline으로 종료. 재전달 후 추가 shutdown 0회·destroy 0회 |
| `test_restart_after_durable_intent_reuses_operation_and_original_deadline` | 예외 중단 후 Manager 재생성과 동일 요청 재개. 실제 프로세스 종료 시험과 구분 |
| `test_pending_stop_expiry_preserves_intent_without_renewing_execution_permission` | 만료 후 원 stop 재전달은 물리 호출 없이 거절하고 stopped 의도·원 operation 보존. 새 실행은 명시적인 새 stop에서만 허용 |
| `test_outer_wait_timeout_does_not_cancel_or_duplicate_original_operation` | 바깥 대기 만료 뒤에도 원 복구는 계속됨. 원 요청 재전달의 create 추가 0회 |

이 시험은 실제 로컬 flock/파일·fork를 사용하고 Xen은 FakeXen이다. 복구 프로세스의
SIGKILL을 모든 제출 단계에 주입한 시험이나 제품 Worker 종단 수락으로 확대하지 않는다.
중단된 호출자 대신 남은 정지를 자동 실행하는 daemon은 없다. 원 요청을 보유한
CLI/상위 호출자가 같은 key로 다시 호출해야 하며, `control-operation` 조회는 실행하지
않는다. 이미 제출된 동작이 미확정이면 원 journal/execution/Xen 신원·scope를 먼저
대조한다. 만료·과거 host/lifecycle·후속 사용자 revision·저장 실패·모호한 신원은
명시적 재확인이 필요하다. stopped 의도 보존은 새 물리 실행 권한이나 자동 force가 아니다.

### 상위 담당자의 정확한 연결 지점과 수락 조건

아래는 기존 A/C 인계의 구현 위치를 확정한 것으로, 상위 제품 코드를 수정하지 않았다.
경로는 저장소 루트 기준이며 값은 A 표의 실제 적용값/소스 기본값 구분을 따른다.

| 위치·함수 | 필요한 최소 연결과 수락 조건 |
|---|---|
| `platform/services/dom0-manager/dom0_manager/service.py`의 `VmService._vm_lock/create/stop`, `drivers/platform.py`의 `SerializedManager.call` | VM/resource mutex의 무기한 Q와 Manager 앞 5초 S를 원 요청 예산에 포함. 긴 실행과 짧은 stop 수락을 구분하되, 요청별 Manager 상태를 공유 객체에서 무잠금 호출하지 않음. 실제 복구가 잠금을 가진 HTTP 경쟁에서 durable pending 수락과 기존 직렬화 안전성을 모두 확인해야 함 |
| 같은 `drivers/platform.py`의 `PlatformDriver._stop`, `raise_for_envelope`; `errors.py`의 `Dom0ManagerError.body`; `api/errors.py`, `api/vms.py:stop_vm` | 현재 stop은 key를 전달하지 않고 오류 details도 보존하지 않음. 원 key/operation 및 accepted/completion/revision/deadline을 구조적으로 전달하는 계약 필요. 미수락 busy·내구 수락 pending·실제 완료·superseded·만료를 구분하고 204 완료를 접수로 재해석하지 않는 HTTP 시험 필요 |
| `backend/src/main/resources/application.yml`, `backend/src/main/java/com/xenova/backend/vm/infrastructure/Dom0ManagerProperties.java`, `Dom0ManagerClient.createClient/executeQuery/executeCommand/handleError` | connect/query/command 소스 기본 2/6/100초와 하위 15/90초·S 5초·R 최대 15초를 대조. A의 조건부 25/120초 권고는 미적용이며 Q 제한·반환 여유 합의가 먼저다. 전송 후 timeout에 새 key를 만들지 않고 원 요청으로 재확인하는 시험 필요 |
| `platform/services/dom0-manager/dom0_manager/recovery/worker.py:RecoveryWorker.run_once`, `recovery/__main__.py:main` | 실제 설치본/현행 코드는 `judgments.put()` 판단 큐까지다. 110초 executor 소비·조립·하위 호출은 미연결. 담당자가 최초 attempt의 host/절대 D(기본 110초, 상한 120초)를 생성·내구 보존하고 원 attempt/D를 전달해야 함. 큐 대기 차감·재시작/중복 전달·실행 중 stop·완료 재확인을 종단 시험한 뒤에만 실행 연결 수락 |

하위 직접 호출 C 수락과 제품 HTTP/Worker 종단 수락은 계속 별도다. 새 HTTP API,
executor나 상위 잠금 구조를 이번에 대신 구현하지 않았다.

8GiB 등록 1,353.370초는 `VmService.create → ensure_instance → Manager create →
_create_assets → make_instance → prepare_instance`의 별도 후속 과제다. 원 보드
실행·trace에는 등록 전체 시간과 복수 해시 대조/복사 호출이 남아 있으나 모든 I/O
비용을 독립적으로 계측한 것은 아니다. 원자료 이상의 비용 배분이나 원인 확정을
하지 않으며, timeout 확대·무결성 검사 축소·90초 reboot 성과로 대체하지 않는다.

PC 증거 디렉터리는 기존 `xenova-management-closure`를 유지한다.
`CLOSURE_FOLLOWUP_AUDIT.json`에 pkgdata·recipe 해시, P01/최종 함수 대조와 unit 상태를
기록했다. 기존 `systemd-isolation-final.json`, 보드 회수본과 archive 해시를 연결하며,
이번 추가 실행은 `closure-followup-guest.log`(16개), `closure-followup-stop.log`(20개)다.
기존 전체 837개 회귀와 이번 집중 회귀를 구분하며 전체 회귀·보드 시험을 반복하지 않았다.


## 이전 부팅 경계 문서

# 관리 작업과 부팅·종료 경계

이 문서는 `feat/platform/runtime-lifecycle-diagnostics`의 하위 플랫폼 변경과
실제 systemd unit을 기준으로 dom0 준비, domU 자동 시작, 종료의 경계를 설명한다.
U-Boot·Xen·dom0 커널·DT·하드웨어 설정은 변경하지 않는다.

## 준비 의존성과 완료 의미

| 단계/실제 진입점 | 선행 조건과 의존성 | 완료가 의미하는 것 |
|---|---|---|
| `xenova-data-prepare.service` → `xenova-first-boot grow` | `systemd-remount-fs.service`를 Requires+After, udev trigger 뒤 | 기존 부팅 매체의 마지막 데이터 파티션 확인·확장. 새 디스크 생성/포맷이 아님 |
| `srv-xenova.mount` | data-prepare Requires+After | 검증된 ext4를 `/srv/xenova`에 마운트 |
| `xenova-first-boot.service` → `register` | 데이터 mount Requires+After | 최초 등록 재개 또는 기존 설치 확인. 이미 확정된 UUID·자산·사용자 autostart 선택을 덮지 않음 |
| `xenova-xen-ready.service` → `check-xen` | xenstored·xenconsoled·xen-init-dom0 Requires+After | dom0 제어 인터페이스·xl·Domain-0 확인. guest/app 준비가 아님 |
| `xenova-cpu-prepare.service` → `cpu-host-prepare --execute --json` | mount와 first-boot·xen-ready 뒤. 후자 둘은 Wants | 현재 host/policy의 dom0 실제 affinity 확인·적용. CPU 정책 미설정 설치는 기존 호환 경로 |
| `xenova-vm-shutdown.service`의 `/bin/true` | first-boot·mount·xen-ready Requires+After, toolstack/backend 뒤 | 종료 때 guest를 먼저 정리할 서비스 경계 설치. VM 시작은 하지 않음 |
| `xenova-vms.service` → `autostart` | first-boot·mount·xen-ready·vm-shutdown Requires+After, cpu-prepare Wants+After | 등록·부팅 정책에 따라 선택한 VM의 Xen 실행 및 필수 신원/CPU 검증. guest OS·앱 준비는 별도 |
| networked guest의 `prepare_dhcp` | `xenova-dhcp.service` start/검증, DHCP는 bridge Requires+After | 내부 bridge/예약/DHCP 확인. Wi-Fi·외부 DNS·NTP에 부팅을 묶지 않음 |
| `xenova-host-admission.service` → `host-init` | vms Wants+After | 현재 host의 admission 초기화. 기존 quiescing barrier를 무조건 다시 열지 않음 |

`After`는 순서만 뜻한다. `Requires`와 함께일 때 선행 start 실패가 서비스 시작을
막는다. CPU 준비는 `Wants+After`이므로 legacy 설치의 호환 동작을 유지하고,
정책 활성화 시에는 **각 공통 start/reboot/recovery 경로가 실제 현재 준비 상태를
검사**한다. service의 active 문자열이나 전 부팅의 ready 파일만으로 허가하지 않는다.
CPU 준비 서비스에는 `ExecStop`, guest에 대한 `PartOf/BindsTo/Requires` 전파를
추가하지 않았다. CPU 준비 재시작이 실행 중 VM을 종료하거나 affinity를 초기화하지 않는다.

내부 bridge/DHCP는 networked guest가 사용할 때도 검사한다. 네트워크 장애가
network 없는 guest 또는 dom0 진단 경로를 불필요하게 직렬화하지 않도록 기존
조건부 진입을 유지한다. 기존 factory 등록과 매 부팅 실행 준비는 별개다.

## 시간 예산

`TimeoutStartSec`는 해당 service의 실행 상한이며 모든 선행 unit을 포함한
보드 전체 부팅 상한이 아니다. reboot의 90초를 부팅 전체에 적용하지 않는다.

| 실행 | unit의 바깥 상한 | 내부 경계 |
|---|---|---|
| data-prepare / 최초 등록 | 각각 300초 | 기존 설치 단계·저장 검증. 이번 변경으로 90초를 적용하지 않음 |
| 데이터 mount | mount 15초, JobTimeoutSec 330초 | 선행 data-prepare와 실제 mount를 구별 |
| check-xen | 30초 | CLI 진입에서 **25초** 한 번; 모든 xl 조회가 남은 예산 공유 |
| CPU host prepare | 30초 | 기존 CPU preparation의 15초 관측/적용 예산. 서비스 실패는 완료 증거가 아님 |
| DHCP | 15초 | 서비스 내부 ExecStartPre/ExecStartPost 및 기존 준비 확인. 호출자는 남은 관리 예산으로도 제한 |
| autostart | 150초 | **140초 batch 하나**. 잠금 시도 전 생성하고 registry·Xen 준비·DHCP·모든 VM start가 공유 |
| vms/vm-shutdown의 shutdown-all | 각각 110초 | 기존 90초 전체 guest 정리 기한. admission gate 종료 뒤 순서 유지 |
| host-admission quiesce | 10초 | 새 시작/복구 차단. guest 정리를 대신하지 않음 |

autostart의 10초 차이는 결과 저장·프로세스 종료를 위한 여유다. fsync·프로세스
생성·커널 대기를 엄밀하게 선점한다는 보장은 아니다. 개별 VM마다 140초를 새로
주지 않는다. 기존 공통 작업 잠금의 충돌 즉시 거절 의미도 유지한다.

## autostart 부분 성공과 재시도

`/run/xenova/autostart-results.json`의 기존 VM 내부 이름별 map을 유지한다.
각 항목에 이번 `host_boot_id`, batch의 `deadline_monotonic_ms`를 붙인다.
기한은 해당 부팅의 단조 시계 값이며 UTC가 아니다.

- 정상 완료 항목의 `result`·`xen_state`는 실제 관측 상태를 유지한다.
- 시간이 끝나기 전에 처리된 skip은 기존 boot admission/정책 근거를 유지한다.
- 아직 start를 호출하지 못한 VM은 `result="not-started"`, `start_invoked=false`로 기록한다.
- start 호출 뒤 실패는 `result="failed"`, `start_invoked=true`로 구별한다.
  실패가 도메인 생성 효과 없음이나 안전한 자동 재시도라는 뜻은 아니다.
- 이미 시작한 VM을 다른 VM의 실패/기한 만료 때문에 임의로 종료하지 않는다.
  호출은 실패로 끝나도 각 VM의 원 결과·자산·실행 기록은 남는다.
- 서비스 재시도는 새로운 부팅 batch다. 기존 실행에 대해 신원·CPU 정책을
  확인하는 공통 start를 사용하므로 중복 생성하지 않는다. 원 VM 제어/recovery의
  멱등 키·실행 기한을 재발급하는 동작이 아니다.
- 작업 중 강제 중단이면 마지막 `starting` 또는 `pending`이 남을 수 있다.
  이를 성공/부재로 해석하지 않고 기존 실행·journal과 대조한다.

새 host에서는 이전 execution/관측/CPU ready 근거를 현재 것으로 사용하지 않는다.
영구 desired_state와 autostart의 충돌은 기존 승인된 boot policy가 판단한다.
동일 host의 명시적 사용자 stop을 서비스 재시작으로 뒤집지 않는다.

## 종료 순서

동시에 종료되는 unit의 `After`는 반대 순서로 적용된다.

1. host-admission의 `ExecStop=host-quiesce`가 새 시작·복구를 차단한다.
2. vms의 `ExecStop=shutdown-all`이 기존 VM별 종료 정책으로 guest를 정리한다.
3. 별도 vm-shutdown 경계가 guest 부재를 확인하며 backend/toolstack·저장소보다 먼저 종료된다.
4. 그 뒤 toolstack/backend·mount 등의 기존 systemd 정리가 진행된다.

협조 종료 실패를 자동 force로 바꾸지 않는다. CPU 정책 오류도 신원으로 확인된
guest의 stop/shutdown을 새로 막아서는 안 된다. xen domain 생성, guest OS 준비,
앱 준비는 서로 다른 완료 단계이며 앱 readiness를 이 경로에 추가하지 않는다.

## 검증과 실보드 인계

로컬 `test_xenova_boot_boundaries.py`는 실제 Platform/Execution/CPU와 작은
자산·FakeXen·가짜 시계를 사용한다. 실제 systemd loader로 정상 그래프와 주입한
의존성 순환을 검사하고, 등록 중단 재개·기존 등록 보존·동일 실행 재사용·현재 host
CPU 준비·실제 affinity 변경·공유 예산 소진·부분 성공을 확인한다.
기존 lifecycle/recovery/first-install 회귀도 함께 실행한다.

이 결과는 전원 부팅 수락이 아니다. 보드 후보 설치·서비스 교체·CPU 정책 변경·
guest 제어는 이후 별도 승인으로 G5의 개별 협조 reboot까지 검증했다.
host 재부팅은 수행하지 않았다. 후속 전원 시험 시
전후 host_boot_id, 등록 UUID·자산 hash, 영구 intent, autostart-results,
CPU policy/ready 문맥, 실제 전체 domain/affinity, unit journal을 함께 회수한다.
정상 VM을 임의 rollback하지 않고 pending/failed 항목을 구분해 인계한다.

### unit 실패와 cgroup 소유 관계의 후속 수락

아래는 G5 당시 확인한 실패 근거다. 후속 후보는 실행별 scope/PDEATHSIG와 기존
shutdown guard의 정리 순서를 보완했다. 실제 systemd 격리 시험과 후속 Xen 실보드
수락은 [관리 경계 B 절](#b-autostart-지속-프로세스-소유와-실패-처리)에
구분했다. unit의 KillMode·Type·Timeout·Restart는 그대로다.

함수의 부분 성공 보존과 systemd의 프로세스 정리는 서로 다른 경계다.
2026-10-25 실제 보드의 `xenova-vms.service`는 `KillMode=control-group`,
`SendSIGKILL=yes`였다. WSL의 별도 oneshot unit에서 같은 cgroup의 비-VM
monitor/backend 모형을 먼저 준비한 후 launcher 실패/timeout을 주입했다.
두 경우 모두 자식이 SIGTERM으로 종료됐다. 실제 Xen guest 실패 주입은 하지 않았다.

따라서 autostart batch의 함수 결과·journal이 부분 성공을 보존해도, 실패한
unit이 소유한 libxl monitor/qdisk까지 보존한다고 결론내릴 수 없다.
`KillMode`를 바꾸거나 전체 서비스 순서를 재배치하지 않았으며, 이 unit 전체
수락은 별도로 남긴다. 이번 보드의 수동 복원 VM들은 sshd cgroup에서 실행됐으므로
그 생존도 autostart unit의 실패 수락을 대신하지 않는다.
격리 증거·실제 cgroup·보드 reboot 결과의 범위는
[G5 수락 경계](#g5-실보드-결과와-별도-수락-경계)에 있다.

후속 승인 범위에서는 별도 transient unit과 소유 Zephyr/Debian만 사용해 실제
Xen 부분 실패·재시도를 확인했다. 실패한 batch의 일시 프로세스는 정리됐고,
먼저 시작한 Zephyr의 실행 scope·domain·ICMP는 유지됐다. 재시도는 Zephyr를
중복 생성하지 않고 Debian만 시작했다. 시험 unit stop도 두 VM에 전파되지 않았다.
그 시험 unit은 stop 직전 active/exited였고 ExecStop이 없었다. 제품
`xenova-vms.service`의 정상 stop은 shutdown-all을 실행하므로 별도 계약이다.
이는 위 G5 당시 미수락 항목 중 소유 격리 시험의 후속 결과다. 전역 unit의 실패
주입이나 실제 host 종료·전원 부팅을 수락한 것은 아니다. 소스 버전·후속 guard/
replay 수정과 남은 경계는 [후속 수락](#승인된-후속-실보드-수락)에 구분했다.
실패 batch·개별 stop·제품 서비스 종료·host 종료의 수명주기와 guard 기한 초과 시의
한계는 [마감 증거 연결](#scope-수명주기와-b-증거의-범위)을 따른다.


## CPU 문서의 검증·실보드 기록



소스 회귀:

```sh
python3 -B -m unittest discover -s platform/tests -p 'test_xenova_cpu.py' -v
bash platform/scripts/check-xenova-platform.sh
bash platform/scripts/check-xenova-platform.sh --metadata
```

전체 회귀의 실제 dom0 unit 그래프 검사는 프로젝트의 systemd 259 loader 환경을 사용한다.
--metadata는 격리된 설정에서 BitBake parse/-e만 수행한다. 이미지나 타깃 바이너리를 빌드하지 않는다.
조립 검증기는 새 CPU 모듈·프리셋·unit을 소스와 바이트 단위로 비교한다. G1 인계 묶음도 새 import를 포함한다.

실보드 affinity 변경·부하 주입·guest/host reboot는 이번 구현 요청으로 실행하지 않았다.
별도 승인 후 다음 증거를 수집한다. 실보드 배치 수락과 성능 수락을 별도로 판정한다.

1. 현재 이미지 영수증, Xen 버전, host_boot_id, 원본 cpu-layout, xl info,
   xl cpupool-list / -c, xl list -c, xl vcpu-list, 정책·runtime 영수증 해시를 PC에 보관한다.
2. UART 확보·관리 Wi-Fi 보존·기본 VM/UUID/자산 목록을 확인한 뒤 승인된 정지 창에서 정책 등록·host 준비를 수행한다.
3. A/B/기본 Debian/추가 Linux를 기존 start로 기동하고 cpu-layout의 모든 실제 마스크와 예약 충돌 부재를 확인한다.
   Linux vCPU 수를 임의로 줄이지 않았는지 함께 확인한다.
4. A/B 협조형 reboot, Linux 일반 reboot, recover_once, 이미 실행 중 start 후 새 execution_id와 동일 policy revision·마스크를 수집한다.
5. 승인된 host reboot 전후 host_boot_id 교체, 새 준비 영수증, 자동 시작 배치를 확인한다.
   CPU prepare stop/restart만으로 기존 VM 정지·마스크 초기화가 전파되지 않는지도 별도 승인 창에서 확인한다.
6. 동일 펌웨어·주행 입력·broker/Gateway 설정으로 pinning 전/후 × Debian 부하 없음/있음의 네 조건을 비교한다.
   부하는 기존 도구를 사용해 공유 코어 집합에서만 실행한다. 조건마다 vCPU 마스크·빈도·실행 시간·샘플 수를 기록한다.
7. guest 계산 주기/명령 간격과 Gateway 수신→출력 지연을 구분한다.
   두 계측 지점을 연결하려면 동기 시각 또는 correlation ID의 기준을 명시한다.
   p95/p99·관측 최대·deadline miss·오류·손실을 기록하고 원시 표본을 보관한다.
   실제 주행 앱이 없다면 타이밍 harness 결과만 보고하며 실제 주행 성능 PASS로 표시하지 않는다.

보드 적용과 새 이미지 배포는 별도 단계다. FakeXen 회귀를 실제 affinity 또는 성능 검증으로 대체해 보고하지 않는다.

### 별도 승인 후 사용할 실보드 명령

다음은 **인계 명령**이며 이번 구현 중 실행하지 않았다. 실제 guest/host reboot와 부하 주입은 별도 승인한다.

dom0에서 관측 증거를 수집한다. 동일 명령을 시작·재부팅·복구의 각 전후에 실행하고 출력 파일명만 단계별로 바꾼다.

```sh
umask 077
evidence=/root/cpu-layout-acceptance-01
mkdir -m 700 "$evidence"
cat /proc/sys/kernel/random/boot_id > "$evidence/host-boot-id"
cat /proc/uptime > "$evidence/dom0-monotonic"
xenova-vm cpu-layout --json > "$evidence/cpu-layout.json" 2> "$evidence/cpu-layout.stderr"
xl info > "$evidence/xl-info.txt"
xl cpupool-list > "$evidence/pools.txt"
xl cpupool-list -c > "$evidence/pool-cpus.txt"
xl list -v > "$evidence/domains.txt"
xl list -c > "$evidence/domain-pools.txt"
xl vcpu-list > "$evidence/vcpu-affinity.txt"
sha256sum /srv/xenova/cpu-policy.json /run/xenova/cpu-host.json > "$evidence/files.sha256"
```

복구는 새 실행 문맥으로 요청을 만든다. `TARGET_UUID`에는 현재 실행 중인 시험 대상 UUID를 직접 지정한다.
기존 stop 의도가 있는 VM을 이 명령으로 running으로 바꾸지 않는다.

```sh
TARGET_UUID='<현재 running 의도를 가진 시험 VM UUID>'
export TARGET_UUID
PYTHONPATH=/usr/lib/xenova python3 - <<'PY'
import json, os, time
from pathlib import Path
from first_install_contract import uuid4
from xenova_management import Manager
target = os.environ['TARGET_UUID']
response = Manager().call('probe', {'vm_id':target, 'timeout_ms':15000})
assert response['ok'], response['error']
o = response['data']['observation']
assert o['intent_applicable'] and o['desired_state'] == 'running'
present = o['presence'] == 'present'
if present:
    assert o['execution_status'] == 'managed' and o['execution_id']
request = {
    'vm_id':target, 'recovery_attempt_id':uuid4(),
    'expected_host_boot_id':o['host_boot_id'],
    'expected_lifecycle_generation':o['lifecycle_generation'],
    'expected_intent_revision':o['intent_revision'],
    'expected_execution':({'execution_id':o['execution_id']} if present else
                          {'absent_observation_revision':o['observation_revision']}),
    'mode':'graceful-restart' if present else 'start-if-absent',
    'deadline_monotonic_ms':int((time.monotonic()+90)*1000),
}
path = Path('/root/cpu-recovery-request.json')
assert not path.exists(), 'use a fresh owned request path'
path.write_text(json.dumps(request)+'\n')
PY
xenova-vm recover-once --spec-file /root/cpu-recovery-request.json --json
xenova-vm recover-once --spec-file /root/cpu-recovery-request.json --execute --json
xenova-vm cpu-layout "$TARGET_UUID" --json
```

동일 시도의 재전달에는 파일 내용·attempt ID를 유지한다. 다음 장애에는 새 probe와 새 요청 파일을 만든다.
호스트 재부팅 후에는 이전 요청 파일의 host_boot_id/deadline을 재사용하지 않는다.

부하 시험은 **시험 Debian guest 안**에서 실행한다. 새 패키지 없이 기존 Python 표준 라이브러리만 사용하며 60초로 제한한다.
guest의 모든 vCPU는 이미 Xen 공유 집합으로 제한돼 있어야 한다. 전용 주행 VM이나 dom0에서 아래 부하를 실행하지 않는다.

```sh
python3 - <<'PY'
import json, multiprocessing as mp, os, time
def burn(end):
    value = 1
    while time.monotonic() < end:
        for _ in range(10000):
            value = (value * 1664525 + 1013904223) & 0xffffffff
if __name__ == '__main__':
    started = time.monotonic()
    workers = max(1, os.cpu_count() or 1)
    children = [mp.Process(target=burn, args=(started+60,)) for _ in range(workers)]
    for child in children: child.start()
    for child in children: child.join(70)
    for child in children:
        if child.is_alive():
            child.terminate()
            child.join()
    print(json.dumps({'guest_workers':workers, 'elapsed_s':time.monotonic()-started,
                      'exit_codes':[child.exitcode for child in children]}))
PY
```

pinning 전 기준선은 활성화 전에 확보하거나, 동일 정지 조건에서 `exclusive=[]`,
`shared_pcpus=[0,1,2,3]`인 명시적 비교 정책을 cpu-policy-set으로 등록·준비해 수집한다.
그다음 새 revision으로 A/B 전용 정책을 적용한다. 정책/anchor/journal을 삭제해 legacy 모드를 만드는 방식으로 비교하지 않는다.
각 정책에서 Debian 부하 없음/있음을 각각 측정한다. 펌웨어·vCPU 수·메모리·broker·입력은 유지한다.
주행 명령 표본은 실제 앱/Gateway의 기존 계측 도구를 사용한다. 아직 없는 이벤트명이나 타임스탬프 필드를 이 구현이 가정하지 않는다.

host reboot 수락 시에는 위 증거를 PC에 회수하고 관리 Wi-Fi/UART 복원 조건을 확인한 뒤,
승인된 창에서 dom0의 `xenova-vm reboot-board`를 사용한다. 재접속 후 새 host_boot_id,
`xenova-cpu-prepare.service`의 완료 로그와 cpu-layout, 기존 VM UUID·자산 해시를 비교한다.
