# 건강 판정 근거와 계층별 행동

## 목적과 적용 상태

**CPU·메모리·스레드 상태를 정식 건강 판정의 입력으로 사용해야 합니다. 다만 그 결과는 모두
‘VM 재시작’으로 이어지는 것이 아니라, 경고·준비 제한·기능 축소·승계·앱 재시작·VM 복구 중
적절한 행동으로 연결돼야 합니다.**

이 문서는 관측의 의미와 행동 책임의 공통 원문이다. 새로운 MQTT 메시지·health 점수·복구
단계표를 정의하지 않는다. 기존 [v1 준비 보고](mqtt-contract-v1.md#62-앱-준비-보고-appreadiness),
[Telemetry 2.1](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md),
[Manager 신원·의도](../platform/management-boundaries.md)를 재사용한다.

| 영역 | 현재 구현·인계 상태 |
|---|---|
| Zephyr Diagnostics | 유효 CPU 구간·heap/pool 여유·thread 목록 완전성과 필수 작업 결과를 분리해 로컬 판정 |
| 공통 drive 펌웨어 | 실제 두 계산의 결과·기존 20ms release deadline을 준비 판단에 연결. 선택된 불충족 결과는 0 출력 후보로 제한 |
| Xen·dom0 독립 근거 | 기존 Manager probe/execution-context/cpu-layout 및 unit/journal. 새로운 host 건강 수집기나 재시작기 없음 |
| Linux·Agent | 기존 계측·heartbeat·systemd 감독은 있음. 아래 기능 진행·로컬 복구 연동은 [Linux·Agent 요청](../requests/2026-10-27-linux-health-agent.md) 대상 |
| Worker | 기존 D01 per-VM 전달 분류 반례와 자원/기능 판단 연결은 [Worker 요청](../requests/2026-10-27-recovery-health-ctrl.md) 대상. 제품 수정·종단 수락 전 |
| Gateway | 기존 모드·owner·재무장·출력 권한의 단일 소유자. 건강 모듈·Agent·Worker가 대신 변경하지 않음 |

기존 D01 반례는 **다른 VM의 수신으로 대상 VM의 전달 경로를 정상으로 판단한 문제**다.
계층별 자원·기능 건강 연결은 별도 확장 요구다. 둘을 같은 과거 결함으로 기록하지 않는다.

## 관측의 대상과 유효성

| 근거 | 원천·단위·범위 | 유효성·사용 제한 |
|---|---|---|
| Xen 실행 | Manager `probe`, `execution-context`: UUID, host/execution, domid·unique ID, 의도 revision | running/blocked·domain 존재는 앱 준비와 다름. 관측 오류는 absent가 아님. CPU policy 검증 실패는 개별 guest 사망의 근거가 아님 |
| Xen CPU 배치 | `cpu-layout`: 실제 온라인 pCPU·pool·scheduler·vCPU별 hard affinity | 배치 검증이며 앱 계산·pCPU 사용률이 아님. dom0 Linux CPU 번호로 물리 코어를 추정하지 않음 |
| Xen 사용 통계 | 기존 xentop 수집기의 CPU 누적 초·vCPU 수·메모리·장치 누계 | 독립 관측이지만 현재 서비스 소비 범위를 따라야 함. 평균 사용률은 같은 실행의 두 표본 구간 필요. 개별 VM의 CPU 구간을 host CPU 부족으로 단정하지 않음 |
| Zephyr CPU | `xdiag_resources_get/try_get`: guest UP non-idle/전체 계상, 100%=100000 milli-percent | 200ms collector 원 구간. 첫 표본·분모0·reset·긴 공백·1초 초과 cache는 무효. Xen 비실행/IRQ 귀속의 한계는 Telemetry 원문을 따름 |
| Zephyr memory | kernel system heap used/free, 고정 RX/TX data pool의 payload bytes | 서로 겹치는 영역을 전체 RAM처럼 합산하지 않음. free=0은 여유 경고이며 실제 allocation 실패와 다름. source rc·sample·cache 나이를 확인 |
| Zephyr threads | CPU registry lifetime·raw state·선택형 stack high-water, 실제 필수 작업 완료 | 이름·주소·CPU%로 수명/진행을 추측하지 않음. registry 64/반환 32·목록 잘림은 전부 정상이라는 증거가 아님. 정기 CPU 수집은 stack scan을 하지 않음 |
| 계산 진행 | 앱 어댑터의 실제 반환·progress, 공통 release 원점·deadline·완료 시각 | 순번은 송신 횟수가 아님. 완료·입력 유효성·준비·출력 허가는 각각 다름. skipped release를 몰아서 실행하지 않음 |
| v1 I/O 작업 | `xd_io_stats_get`: started/joined, enqueued/attempted/sent/failed·폐기, clock/close 오류 | command가 없는 대기는 장애가 아님. accepted와 sent, socket 제출과 수신/장치 적용을 구분. 해당 thread의 강제 abort/restart를 추가하지 않음 |
| netfront | `xnet_stats_try`: TX/RX/drop/pool_busy/protocol 오류, 기존 link provider | link는 DHCP/MQTT 준비가 아님. pool_busy/drop은 전달 자원 근거이며 계산 실패나 VM 사망의 단독 증거가 아님 |
| Linux OS/서비스 | 기존 Agent `/proc/stat`, `/proc/meminfo`, ProcessSampler·systemd | guest CPU는 할당 vCPU 전체 100%, process CPU는 한 코어 100%. process 목록은 top-N이며 PID/starttime·구간 의미의 소비 보완이 필요 |
| heartbeat/readiness | 검증된 현재 실행 봉투·수신시각·실제 progress | capability는 신호 지원 선언. Agent heartbeat는 앱 완료의 대체물이 아님. v2 Publisher와 v1 앱의 instance는 별개 |

work의 cycle microseconds, resource의 guest uptime milliseconds, dom0 수신 monotonic,
host boot-local deadline을 직접 빼지 않는다. 수신 lease는 원천 정보 나이의 상한이 아니다.
retained·같은 progress·역전·과거 실행·과거 crash를 새 표본처럼 갱신하지 않는다.
상세 v2의 미수신·수집 오류는 v1 계산의 새 필수 선행조건이 아니다.

## Zephyr의 적용된 분기

`xdh_assess`는 독립된 경고 bits와 필수 작업의 `failure/restrict_function`을 반환한다.
아래 수치는 새 운영 임계치가 아니라 원 계측의 유효 범위·기존 작업 기한이다.
정상 고부하는 정상 상태일 수 있다. 합의된 자원 여유·지속 조건 위반과 실제 필수 작업·할당·
기한 실패를 구분한다. 미합의 임계치를 새 정책값으로 넣지 않는다. 현재 `CPU_NO_IDLE`은
그 유효 표본 구간에 계상된 idle이 없다는 진단 경고이며, CPU 고장이나 VM 복구 허가가 아니다.

| 관측 → 판정 조건 | 기능 영향·허용 행동 | 회복·반복 억제 | 수락 assertion |
|---|---|---|---|
| 유효 CPU 구간이 100000: 계상 idle 없음 | `CPU_NO_IDLE` 경고. 실제 계산·기한 정상일 때 READY/명령을 유지 | 다음 유효 구간으로 판단. 90% 등 근거 없는 문턱 없음 | 95% 정상, 100% 경고에서 준비 제한·VM 호출 0 |
| 유효 heap/pool free=0 | `MEMORY_NO_FREE` 경고. 현재 stateless 계산이 할당을 요구하지 않으면 기능 실패로 확대하지 않음 | 다음 실제 free 관측으로 해제. 누적 과거 ENOMEM을 현재 실패로 사용하지 않음 | 자원 여유 0이어도 on-time MANUAL 유효 |
| CPU/heap 미지원·첫 표본·cache 오래됨·try-lock 경합·목록 잘림 | 보조 관측 경고. 불명 값을 0%/전체 정상으로 만들지 않음 | 새 유효 원 구간을 기다림. 표본 재전달로 age를 갱신하지 않음 | `cpu_valid=false`·local 출력 null, 정상 계산은 유지 |
| 해당 필수 어댑터가 실제 -ENOMEM/-ENOBUFS 반환 | `RESOURCE_FAILED`, 해당 모드 NOT_READY·결과 0. 기존 `CALCULATION_FAILED`와 local 원 errno 사용 | 다음 실제 계산·입력·상태 준비가 충족돼야 회복 | AUTO 할당 실패 중 정상 MANUAL READY 유지 |
| 실제 계산 오류·잘못된 출력 | 해당 모드 제한. `OUTPUT_INVALID`는 기존 사유 유지 | 새 실제 완료·출력 검증. heartbeat로 상쇄하지 않음 | 낮은 CPU·정상 통신이어도 오류 결과 선택 불가 |
| 실행한 작업이 기존 release deadline 초과·시각 근거 불명 | `DEADLINE` 또는 `UNVERIFIED`, 해당 결과 제한. end==deadline은 정상 | 다음 현재 release의 실제 완료. 기한 연장·재전달·catch-up 없음 | 늦은 계산은 NOT_READY, 다음 정상 계산은 회복 |
| 직렬 AUTO 지연 때문에 전체 step 기한 초과 | 두 모드에 공통 영향. whole-step miss 확인 후 두 결과 제한 | 원 serial 구조 유지. 별도 안전한 계산 분리 없이는 MANUAL만 독립 정상이라고 주장하지 않음 | AUTO→MANUAL 모두 지연되는 반례 보존 |
| 필수 작업 미시작·일 없음·종료 중 | 존재/낮은 CPU/대기를 진행 실패로 해석하지 않음 | 현재 lifecycle·작업 기대 조건으로 재평가 | IDLE/NOT_STARTED/STOPPING에 과거 deadline 적용 금지 |
| 진단 전용 작업의 allocation 실패 | 진단 실패를 남기되 drive 준비를 제한하지 않음 | 진단 작업의 재수집. 새 실행·원 intent 변경 없음 | 동일 ENOMEM도 required=false면 기능 제한 0 |

실제 알고리즘이 영원히 반환하지 않으면 같은 main이 자신의 NOT_READY를 새로 만들 수는 없다.
이때 과거 READY/heartbeat를 통신 worker가 갱신하지 않는 기존 구조와 Gateway의 신선도 제한이
작동한다. 이를 새 로컬 deadlock 검출·스레드 복구 완료로 표현하지 않는다.

## 준비·전달·행동의 책임

`CALCULATION_FAILED`는 현재 계산 어댑터가 유효한 결과를 제공하지 못한 경우에 사용한다.
이번 공통 펌웨어는 필수 자원 실패·기존 완료 기한 위반도 이 사유로 준비를 취소하며,
자세한 구분은 local health의 원 errno/failure에 남긴다. 새 MQTT type·필드·reason·QoS·ACL은
추가하지 않았다. 기존 소비자의 generic NOT_READY 거절은 유지되지만 상세 사유 표시는
담당자 인수 대상이다. legacy 분리 앱과 Network Starter에 이 준비 연결이 자동 생긴 것은 아니다.

Gateway는 현재 신원·제어 적용·실제 준비 유지·입력과 출력 경로를 확인해 선택/승계를 결정한다.
READY 회복이나 Worker COMPLETED 하나만으로 Active·출력 권한·AUTO 재개를 승인하지 않는다.
같은 실행의 AUTO↔MANUAL은 firmware·UUID·CPU 배치를 바꾸지 않는다. 재합류한 실행은 준비된
Standby이며, 현 Active를 자동으로 되돌리지 않는다.

| 근거·영향 | 현재 허용된 주체·행동 | 자동으로 허용하지 않는 것 |
|---|---|---|
| 단순 사용률/여유 경고 | local 진단, 운영자 확인 | CPU%만으로 재시작, 새 hard threshold |
| 모드별 유효 결과 없음 | 앱 어댑터 준비 제한·해당 출력 후보 0, Gateway의 기존 기능 축소/승계 규칙 | Agent/Worker가 vehicle.control 직접 발행 |
| 앱/Agent의 확정 종료 | Linux 담당자가 기존 systemd supervisor 범위의 로컬 서비스 복구를 확정할 수 있음. 현재 추가 앱 복구 연결은 미구현 | 편의상 원격 앱 재시작 API·새 daemon·공유 자원 미정리 thread abort |
| VM crashed/absent·정책상 허가된 장애 | 기존 Worker/Engine → 조건부 Manager `recover_once` | 직접 xl, user stop 무시, 새 attempt/기한 무제한 발급, 자동 force |
| 전달 불가·v2만 단절·입력 부재·warm-up | 원천별 보류/준비 제한/관측 불가, 기존 Gateway 출력 보호는 독립 | 무조건 VM 장애·모든 무신호를 정상으로 치환 |
| 여러 VM 지연·dom0 공통 서비스 오류 | 공통 환경 점검·개별 복구 억제 검토 | 상관관계만으로 host 원인 확정, 자동 host reboot·CPU 변경·전 VM restart |

Worker의 기존 `restart/none`, episode당 3회·backoff 5/15/30초·attempt 110초·상한120초·
verify150초·force 기본 off는 **소스 기본값**이다. 이번에 바꾸거나 원인 미확정에 새 1회 정책을
적용하지 않았다. 장애 판단 유예와 실행 deadline은 별개다. S01/S02와 사용자 stop·원
operation/attempt·의도 revision·미완료 소유·원 deadline·예산을 모든 행동의 선행 guard로 유지한다.

## local 조회와 인수 경계

공통 drive 셸에서 `xenova drive health`로 최신 두 모드의 local 평가를 읽는다. `failure`는
`0=NONE, 1=WORK_FAILED, 2=RESOURCE_FAILED, 3=DEADLINE, 4=UNVERIFIED`, warning은
`1=CPU_UNAVAILABLE, 2=CPU_NO_IDLE, 4=MEMORY_UNAVAILABLE, 8=MEMORY_NO_FREE,
16=THREADS_INCOMPLETE` bits다. 처음에는 `-ENODATA`다.

`completed/step_end_us`는 저장 당시 원 계산의 순번·guest cycle 시각이며 조회가 갱신하지 않는다.
`stopping`과 현재 실행 문맥도 대조한다. 이것은 마지막 평가이며 현재 lease·UTC·운행 허가가 아니다.
`cpu_valid=false`이면 `cpu_percent_milli=null`이다. 수집·해석 오류를 실제 0%로 바꾸지 않는다.
cache·local 평가와 시각은 RAM이며 guest 재실행에서 초기화된다. 영구 원문 저장은 추가하지 않는다.

현재 구현·고정 후보·시험·미수행은 [검증 보고](../verification/health-evidence-20261027.md)에 둔다.
Linux/Worker 요청 작성, 담당자 제품 구현, 소비 연결, 설치본·Xen·실보드 수락은 각각 별도 단계다.
