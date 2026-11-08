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
| 공통 drive 펌웨어 | AUTO/MANUAL 계산 소유자를 분리하고 각각의 실제 결과·원 20ms release deadline을 준비 판단에 연결. 선택된 불충족 결과는 0 출력 후보로 제한 |
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
| 유효 kernel heap·net RX/TX data pool free=0 | `MEMORY_NO_FREE` 경고. 현재 stateless 계산이 할당을 요구하지 않으면 기능 실패로 확대하지 않음 | 다음 유효 원 표본에서 영역별 재평가. 누적 과거 ENOMEM을 현재 실패로 사용하지 않음 | 자원 여유 0이어도 on-time MANUAL 유효 |
| CPU/heap 미지원·첫 표본·cache 오래됨·try-lock 경합·목록 잘림 | 보조 관측 경고. 불명 값을 0%/전체 정상으로 만들지 않음 | 새 유효 원 구간을 기다림. 표본 재전달로 age를 갱신하지 않음 | `cpu_valid=false`·local 출력 null, 정상 계산은 유지 |
| 해당 필수 어댑터가 실제 -ENOMEM/-ENOBUFS 반환 | `RESOURCE_FAILED`, 해당 모드 NOT_READY·결과 0. 기존 `CALCULATION_FAILED`와 local 원 errno 사용 | 다음 실제 계산·입력·상태 준비가 충족돼야 회복 | AUTO 할당 실패 중 정상 MANUAL READY 유지 |
| 실제 계산 오류·잘못된 출력 | 해당 모드 제한. `OUTPUT_INVALID`는 기존 사유 유지 | 새 실제 완료·출력 검증. heartbeat로 상쇄하지 않음 | 낮은 CPU·정상 통신이어도 오류 결과 선택 불가 |
| 실행한 작업이 기존 release deadline 초과·시각 근거 불명 | `DEADLINE` 또는 `UNVERIFIED`, 해당 결과 제한. end==deadline은 정상 | 다음 현재 release의 실제 완료. 기한 연장·재전달·catch-up 없음 | 늦은 계산은 NOT_READY, 다음 정상 계산은 회복 |
| AUTO callback 지연 중 MANUAL은 원 기한 안에 실제 완료 | AUTO 결과·준비만 제한. 정상 MANUAL의 실제 진행·준비는 유지 | 오래된 AUTO 결과로 새 제어를 적용했다고 하지 않음. 기존 serial helper의 과거 반례는 회귀로 보존 | 실제 main에서 AUTO 지연 중 MANUAL 진행, 옛 문맥 거절·원 기한 join 확인 |
| 필수 작업 미시작·일 없음·종료 중 | 존재/낮은 CPU/대기를 진행 실패로 해석하지 않음 | 현재 lifecycle·작업 기대 조건으로 재평가 | IDLE/NOT_STARTED/STOPPING에 과거 deadline 적용 금지 |
| 진단 전용 작업의 allocation 실패 | 진단 실패를 남기되 drive 준비를 제한하지 않음 | 진단 작업의 재수집. 새 실행·원 intent 변경 없음 | 동일 ENOMEM도 required=false면 기능 제한 0 |

### 자원별 사전 여유 경고의 현재 범위

`xdh_assess`는 호출마다 새 평가를 만드는 단일 표본 분류기다. 사전 여유 경고의 진입 문턱,
지속시간, 별도 해제 문턱과 hysteresis는 현재 구현하지 않았다. 기존 200ms collector 주기와
1초 cache 유효성은 관측의 조건이며 새 경고 정책값이 아니다. 같은 `resource_sample`·원 구간의
반복 평가나 재전달을 새 지속 위반으로 세지 않으며, 미지원·수집 오류·만료는 정상 회복의 증거가 아니다.

| 영역·단위 | 원 표본·측정창·분모 | 현재 경고 | 지속·해제 상태 | 허용 행동 | 근거와 미검증 범위 |
|---|---|---|---|---|---|
| guest UP CPU, milli-percent | 실제 `start_ms/end_ms`의 non-idle/전체 계상. source rc·연속성·cache 나이 확인 | 유효 구간 100000이면 `CPU_NO_IDLE` | 다음 유효 구간으로 재평가. 별도 지속·해제 정책 없음 | 계산·기한이 정상이면 READY/명령 유지, 진단·운영자 확인 | 분류 회귀는 있음. 실제 부하의 사전 여유·지속·해제 조건과 pCPU 부족 판단은 미수락 |
| kernel system heap, bytes | collector의 원 sample·heap rc·나이. 동일 heap의 allocated/free 범위이며 전체 RAM이 아님 | free=0이면 `MEMORY_NO_FREE` | 다음 유효 free 표본에서 재평가. 별도 해제 문턱 없음 | 실제 필수 할당 실패와 분리해 진단 | 여유 0과 필수 실패의 분리 회귀는 있음. 필요한 할당 크기·부하별 사전 여유는 미확정 |
| `net_rx_data`·`net_tx_data`, payload bytes | 각 고정 pool의 가용 block과 해당 capacity. 서로 다른 영역·RAM과 합산 금지 | 유효한 해당 영역 free=0이면 `MEMORY_NO_FREE` | 영역별 다음 유효 표본으로 재평가. 지속시간 없음 | 전달 자원 점검. 단독 VM 장애·승계 근거로 쓰지 않음 | pool 범위 계측은 있음. 전달 부하별 사전 여유·지속·해제 조건은 미확정 |
| `drive_maps`·`drive_paths`·`drive_search_nodes`·`drive_odometry`·MANUAL 소유 영역, bytes/count | 영역별 원 sample·rc·나이. capacity는 payload 단위이고 reserved는 metadata/workspace 포함 | app pool은 현재 `health.c` 경고 분기에 미연결 | 정책 없음. 반환 뒤에도 peak·고갈 누계는 남는 이력 | 계측·소유 반환 점검. 실제 필수 작업 실패는 별도 분기로 판단 | 실제 고갈·반환·peak 유지 회귀는 있음. 계상 정확성을 운영 여유 정책 수락으로 확대하지 않음 |
| thread stack, bytes | 명시적 scan의 stack 크기와 unused high-water. 현재 SP·사용량이 아님 | stack 여유 문턱은 현재 health 경고에 미연결 | 진입·지속·해제 정책 없음 | 용도별 최대 사용량 확인과 stack 구성 검토 | ARM scan 회귀와 미지원 구분은 있음. 실제 전체 call chain·IRQ 중첩·장기 부하 여유는 미수락 |
| managed RAM, bytes/영역 비율 | 원 firmware/page 표본의 RAM 영역·free page와 freshness. heap·pool을 중복 합산하지 않음 | managed RAM 비율은 현재 health 경고에 미연결 | 진입·지속·해제 정책 없음 | 영역 범위의 계측·운영자 확인 | allocator 범위·반환 회귀는 있음. VM별 운영 여유 문턱이나 실행 중 VM 종료 근거로 확대하지 않음 |

사전 여유 정책값은 위 영역·분모·원 구간·freshness와 실제 필수 작업의 자원 요구·완료 기한을
함께 측정한 근거로 정한다. 근거가 부족하면 운영값을 확정하지 않는다. CPU 100%·free 0 또는
과거 peak·고갈 누계만으로 승계·재시작을 허가하지 않는다. 현재 분기는
[health.c](../../platform/guests/zephyr/diagnostics/src/health.c), 원 구간·영역은
[resources_core.c](../../platform/guests/zephyr/diagnostics/src/resources_core.c)와
[resources.c](../../platform/guests/zephyr/diagnostics/src/resources.c)를 따른다.
분류 회귀는 [test_health.c](../../platform/guests/zephyr/diagnostics/tests/test_health.c),
계상·고갈·반환과 stack 측정 범위는 [memory-native](../../platform/apps/drive/tests/memory-native/src/main.c)에서 확인한다.

알고리즘이 반환하지 않으면 해당 계산 소유자는 새 완료·READY를 만들 수 없다. main과
다른 계산은 계속 동작할 수 있지만, 통신 작업자가 과거 READY/heartbeat를 갱신하지 않는다.
Gateway의 기존 신선도 제한이 적용된다. 종료 시 원 기한 내 join이 안 되면 잔존으로 남기며,
이를 새 deadlock 검출·앱 재시작·메모리 손상 격리 완료로 표현하지 않는다.

### 대기와 준비의 구분

내부 `WAITING`은 다음 작업이 없다는 뜻이다. 실제 계산 완료나 `app.readiness=READY`가
아니며, 대기에 과거 계산 deadline을 적용하지 않는다. 현재 `verified-drive-1`은 기존과 같이
실제 계산·입력·제어 적용 근거로 준비를 보고한다. 새 설계의 warm Standby 대기 자격,
활성화 준비 확인, 활성 실행 진행은 서로 다른 근거이며 이 프로필의 READY 하나로 대체하지 않는다.
명시적인 warm 기반 프로필의 대기·활성화·진행 계약은
[app.lifecycle](mqtt-contract-v1.md#63-대기활성화-보고-applifecycle)을 따른다. 현재 입력으로
준비 가능한 어댑터와 소비자의 로컬 연결을 지원하며, 지도·maneuver·상태 인계와 실제
차량의 승계 상한을 수락한 것은 아니다. 정식 목표·미정 사항은 주행 설계 9.6·20절을 유지한다.

공통 `loop_seq`는 선택된 계산의 실제 완료를 관측한다(verified의 SAFE_STOP은 실제 MANUAL
계산, warm의 SAFE_STOP은 무업무 대기이므로 완료 증가 없음).
앱별 progress와 공통 관측 순번, 통신 제출 순번을 합치지 않는다. 직렬 두 계산 합계였던
후보와 동일한 비용·지터 측정으로 비교하지 않는다. 미완료·시각 오류·입력/문맥 변경·
보고 만료를 0이나 새 관측 시각으로 덮어쓰지 않는다.

## 준비·전달·행동의 책임

`CALCULATION_FAILED`는 현재 계산 어댑터가 유효한 결과를 제공하지 못한 경우에 사용한다.
이번 공통 펌웨어는 필수 자원 실패·기존 완료 기한 위반도 이 사유로 준비를 취소하며,
자세한 구분은 local health의 원 errno/failure에 남긴다. 준비 프로필별 공개 필드는 MQTT
계약 한곳에서 정의한다. CPU·메모리 상세를 새 주행 메시지에 복제하지 않는다. 기존 소비자의
generic NOT_READY 거절은 유지되지만 상세 사유 표시는
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

### 시연 부하와 장애 범위

부하 명령의 접수·실행 결과와 주행 기능의 건강 판정은 별개다. `auto_delay`는 실제 AUTO 계산 뒤에 busy wait를 더하므로
그 작업의 원 deadline을 넘길 수 있다. 계산이 없는 WAITING·SAFE_STOP에서 명령 시간이 끝난
것만으로 계산 지연이나 승계 성공을 주장하지 않는다.

| 실제 관측 | 허용하는 판단·행동 |
|---|---|
| AUTO 지연 중 CPU 사용률 증가 | 원 구간의 계측을 보존. 사용률만으로 VM 복구를 시작하지 않음 |
| 주행 어댑터의 실제 필수 할당·pool 확보 실패 | 해당 기능의 원 실패와 NOT_READY·출력 제한. 기존 유효 자료로 계속 수행할 수 있는지 먼저 구분 |
| AUTO 작업의 실제 원 deadline 위반 | 해당 결과를 즉시 제한하고 기존 앱별 lifecycle로 전달. 준비된 다른 AUTO·정상 MANUAL·SAFE_STOP 선택은 Gateway가 담당 |
| 현재 실행의 fatal·손상·정책상 확정된 생존 실패 | 현재 실행·의도·원 기한과 Gateway 보호 아래 기존 Worker/Engine 복구 경로 사용 |

현재 lattice의 map/search/path/odom은 고정 예약 풀이다. 풀 소진 시험은 실제 예약·lease를 유한하게 보유하고 해제하며,
실패 뒤 새 정상 입력의 실제 계산·반환과 정상 MANUAL 진행을 함께 확인한다.

현재 소스는 `io.c`에서 AUTO 지연 자체를 VM 불신으로 승격하지 않는다.
`workers.c`의 원 health와 `main.c:warm_report`의 `app.lifecycle`을 사용한다. `AUTO_DEADLINE_MISS`는 AUTO 기능 실패로 소비한다.
같은 원 표본을 다시 읽어 지속 위반을 늘리지 않는다.
원 사유·명령 식별자·실행 문맥은 진단에 남기고 이 보고만으로 Worker의 VM 불신 latch나 새 시도를 만들지 않는다.

과거 Authority의 `distrusted` 기록은 기존 형식으로 읽는다. 새 보고의 분류가 바뀌었다고 기록을
삭제하거나 기존 보호를 자동 해제하지 않는다. 정당한 새 실행·준비·보호 종결을 확인한 뒤 Standby로
재합류한다. 입력 회복의 명시적 AUTO 재개와 사용자 `manual_hold`는 유지한다.

명령의 주입 기간과 관측창도 분리한다. 관측창이 끝났다고 명령을 취소·미실행으로 확정하지 않으며,
실행이 교체되면 원 결과를 보존하고 새 boot에 부하를 자동 재주입하지 않는다.
기능 실패·모드 전환·VM 복구의 순서는 [시나리오 ⑥](../platform/recovery-scenarios.md#6-제어권-전환승계)를 따른다.

## local 조회와 인수 경계

공통 drive 셸에서 `xenova drive health`로 최신 두 모드의 local 평가를 읽는다. `failure`는
`0=NONE, 1=WORK_FAILED, 2=RESOURCE_FAILED, 3=DEADLINE, 4=UNVERIFIED`, warning은
`1=CPU_UNAVAILABLE, 2=CPU_NO_IDLE, 4=MEMORY_UNAVAILABLE, 8=MEMORY_NO_FREE,
16=THREADS_INCOMPLETE` bits다. 처음에는 `-ENODATA`다.

공통 drive의 두 모드 평가를 처음 게시하려면 같은 control/connection 문맥에서 각 계산이
실제로 완료되어야 한다. 미완료 모드의 초기 0을 정상 평가로 게시하지 않는다.
`completed/step_end_us`는 평가 쌍을 저장한 시점의 선택 계산 관측 순번·guest cycle 시각이다.
두 모드가 동시에 끝났다는 뜻이나 각 모드의 별도 최신성 표식은 아니며 조회가 갱신하지 않는다.
`stopping`과 현재 실행 문맥도 대조한다. 이것은 마지막 평가이며 현재 lease·UTC·운행 허가가 아니다.
`cpu_valid=false`이면 `cpu_percent_milli=null`이다. 수집·해석 오류를 실제 0%로 바꾸지 않는다.
cache·local 평가와 시각은 RAM이며 guest 재실행에서 초기화된다. 영구 원문 저장은 추가하지 않는다.

기존 후보는 [건강 검증 보고](../verification/health-evidence-20261027.md), 독립 실행 기반의
현 후보·시험·미수행은 [기반 검증 보고](../verification/zephyr-drive-foundation-20261029.md)에 둔다.
Linux/Worker 요청 작성, 담당자 제품 구현, 소비 연결, 설치본·Xen·실보드 수락은 각각 별도 단계다.
