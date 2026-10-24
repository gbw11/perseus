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
매 부팅 준비, service별 상한은 [부팅·종료 경계](management-boot-boundaries.md)에
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
