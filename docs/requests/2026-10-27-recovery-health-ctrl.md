# Recovery Worker 건강 분류·복구·재합류 연결 요청

수신: Recovery Worker·Engine 담당(ctrl). Gateway의 모드·owner·출력 허가·승계는
박신영의 플랫폼·Gateway 구현 영역으로 유지한다. 2026-10-20의 분담 표기는 과거 이력이다.
**상태: D01-W01~W05 요청 작성·플랫폼 근거 제공 완료 / 담당자 구현·로컬·연동·실보드 수락 전.**
아래 ID는 기존 요청 ID를 유지한 문서 내 추적 ID이며 새 Jira 티켓이 아니다.
별도 [D03 원 결과 재확인·재합류 요청](#d03-worker)은 하위 수습 도구를 소비하는 담당자 작업이며,
D01의 대기 상태를 변경하지 않는다.

## 현재 할 일

| 순서·요청 ID | 구현할 일 | 완료를 확인할 근거 |
|---|---|---|
| 1 · [D01-W01](#d01-w01) | 전역 연결과 VM·현재 실행·신호별 전달 근거를 분리 | 다른 VM의 수신으로 대상의 전달 정상/장애를 확정하지 않음 |
| 2 · [D01-W02](#d01-w02) | 유효한 자원·기능·진행·관측 근거를 판정에 연결 | CPU%·heartbeat 하나로 준비나 복구 허가를 만들지 않음 |
| 3 · [D01-W03](#d01-w03) | 판정별 행동 주체와 Linux 로컬 감독의 경합을 연결 | 출력/승계는 Gateway, 서비스 복구는 감독, VM 복구는 기존 Engine 경로 |
| 4 · [D01-W04](#d01-w04) | 원 작업·기한·예산과 기능 회복·Standby 재합류를 구분 | S01/S02·사용자 stop 보호, 중복 실행·무조건 예산 초기화 0 |
| 5 · [D01-W05](#d01-w05) | 근거·행동·보류 이유와 배포/공동 수락을 연결 | 담당자 구현·제품 연결·실보드/장치 수락을 별도 기록 |
| D03 · [W01~W04](#d03-worker) | 운영자가 수습한 원 attempt/receipt의 결과를 다시 읽어 기존 복구·바인딩 흐름에 반영 | 새 attempt·신원·기한 발급 없이 원 결과 반영. 플랫폼 완료·Standby·출력 허가 구분 |

## 함께 읽을 파일과 순서

| 순서 | 파일·읽을 부분 | 필요한 이유 |
|---|---|---|
| 1 | 이 문서의 현재 할 일과 D01-W01~W05 | 현재 Worker 수정 위치와 필수 수락 assertion |
| 2 | [공통 건강 판정 계약](../contracts/health-evidence-v1.md) | 관측·기능 영향·허용 행동·회복 조건의 공통 기준 |
| 3 | [MQTT 계약](../contracts/mqtt-contract-v1.md) 2·5·6·9·11장 | 현재 실행 봉투·제어/준비·출처·건강/복구 사건의 의미 |
| 4 | [CLI·Manager 명세](../platform/cli-manager-spec.md) 3~5장·공통 계약 A~C·Python Manager 호출 | 실행 신원·원 요청 조회·recover_once·pending/uncertain·기한과 재전달 |
| 5 | [Linux·Agent 요청](2026-10-27-linux-health-agent.md)의 L01~L04 | 아직 제공되지 않은 서비스/진행/로컬 감독 근거와 선행조건 |
| 참고 | [건강 판정 검증 보고](../verification/health-evidence-20261027.md), [복구 저장 마감 보고](../verification/recovery-store-closure-20261027.md) | D01 재현과 S01/S02의 실제 로컬 회귀 범위 |

기존 HTTP/details 연결은 [HTTP·Spring·화면 요청서](../platform/management-integration-request.md)를
따른다. 관련 기능을 변경할 때 참조하며, 이 문서가 HTTP 담당자의 구현을 대신하지 않는다.

## 공통 원칙과 현재 확인

**CPU·메모리·스레드 상태를 정식 건강 판정의 입력으로 사용해야 합니다. 다만 그 결과는 모두
‘VM 재시작’으로 이어지는 것이 아니라, 경고·준비 제한·기능 축소·승계·앱 재시작·VM 복구 중
적절한 행동으로 연결돼야 합니다.**

- 기존 D01 반례: `LastSeenTable._last_received`와 Watcher `_link_proven`은 채널 전역이다.
  다른 VM만 수신해도 Linux/legacy 대상은 loss 판정이 가능했다. verified 현재 binding의
  최초 무수신 보호는 이미 있으며 재구현 대상이 아니다. 감사의 근거와 이번 재확인은
  [검증 보고](../verification/health-evidence-20261027.md)에 연결한다.
- 이후의 CPU/메모리/thread·서비스 기능 건강 연결은 **이번 확장 요구**다. 기존 감사가 이 전체를
  결함으로 입증한 것은 아니다. 현재 Watcher의 HEALTHY는 신호·Xen 근거이며 자원·앱 기능 전체의
  건강 보증으로 확대하면 안 된다.
- 상태: D01-W01~W05 **요청 작성·제공물 준비 / 담당자 구현·로컬·연동·실보드 수락 전**.
  이번 플랫폼 작업은 Worker·Agent·FastAPI·Spring·Gateway 제품 소스를 변경하지 않았다.

<a id="d01-w01"></a>
### D01-W01 — VM·현재 실행·신호별 전달 근거

| 항목 | 요청 |
|---|---|
| 대상 | `recovery/heartbeats.py:LastSeenTable.put/last/forget/last_received_monotonic`, `watcher.py:_track_domain/_track_link/_check_delivery/_link_ready/_signal_age/_judge`, `mqtt.py`의 실제 신호 수신 경로 |
| 입력·유효성 | VM UUID + 현재 실행 binding/boot·instance + agent/app 신호 + 해당 구독 승인 세대·실제 수신시각. link 전역은 공통 환경 근거로만 사용 |
| 권고 | 전역 broker/subscription 상태와 대상별 확인 상태를 나눈다. 다른 VM 수신·과거 arming·이전 실행은 대상의 전달 증거를 만들지 않는다. root trust binding 없는 legacy/Linux의 이전 boot 비교 한계는 명시 |
| 판정·행동 | 최초 미수신·개별 ACL/인증 문제·실제 앱 정체를 구별할 자료가 없으면 원인 미확인으로 보류. crashed/absent의 독립 Xen 근거는 계속 평가. 모든 무신호를 UNKNOWN/FAILED로 일괄 변경하지 않음 |
| 미결정·권고 영향 | 대상 신호를 한 번 받았어도 이후 ACL 거절과 앱 사망은 heartbeat만으로 구별 불가. 독립 서비스/플랫폼 근거가 없는 경우에는 기존 loss 정책을 확정 장애처럼 설명하지 말고 suspicion과 VM 복구 허가를 분리하는 안을 권고. 자동 재시도 수·새 유예값은 합의 전 미적용 |
| 보존 | verified 최초 미수신, S01 Arming history_lost, 신호별 기존 boot grace·window, 의도 stopped 제외 |
| 수락 | 대상 신호 0·타 VM 수신만 있는 Linux/legacy에서 전달 정상 확정 0. broker 전체/개별 ACL/실제 앱 정체/최초 무수신을 독립 주입. 이전 실행·reconnect 이전 수신이 새 전달 성공을 만드는 횟수 0 |

<a id="d01-w02"></a>
### D01-W02 — 원천 유효성과 계층별 건강 입력

| 항목 | 요청 |
|---|---|
| 대상 | `observe.py:PlatformObserver`, `model.py:VmObservation/VmHealth`, `worker.py:RecoveryWorker.run_once`, Watcher `_evaluate/_health`, `rejoin.py:RejoinCoordinator._apply_readiness` |
| 입력 | 기존 Manager 실행·의도, Linux의 실제 구간/서비스 수명, Zephyr v1 readiness·progress와 선택적 v2 CPU/memory. 원천 지원·오류·구간·잘림·현재 실행 확인을 함께 사용 |
| 권고 | 단일 health 점수나 전체 AND 대신 기능/시간/자원/전달/관측 불가의 근거·영향을 분리. 상세 v2 전체 수신을 주행 필수조건으로 만들지 않음. 필요한 최소 필드 확장은 생산자·schema·소비자와 합의 후 수행 |
| 행동 | high CPU+정상 완료는 정상 부하일 수 있음. 합의된 자원 여유·지속 조건 위반을 경고/제한 근거로 사용하며 임의 임계치는 넣지 않음. 유효 작업 정체+확정 기한 위반은 해당 기능 실패. 실제 필수 자원 실패는 준비 제한을 존중. 낮은 CPU/정상 heartbeat가 fatal·기능 실패를 상쇄하지 않음 |
| 신선도 | retained·중복/역전·과거 crash·다른 thread lifetime·stale/partial CPU 페이지는 현재 근거를 갱신하지 않음. 같은 MQTT 경로의 지표 여러 개를 독립 전달 증거처럼 세지 않음 |
| 우리 제공물 | `xdh_assess`, 실제 어댑터의 -ENOMEM/기한/회복 시험, `xenova drive health` 최신 local 근거. local query는 Worker에 이미 MQTT 연결된 API가 아님 |
| 수락 | CPU 100%에도 on-time READY·VM 실행 추가 0; fresh task 실패는 CPU 0/heartbeat 정상으로 덮지 않음; resource cache 무효/v2만 단절은 정상 v1 기능 실패로 변환 0; 잘린 thread 목록을 전체 정상으로 확정 0 |

<a id="d01-w03"></a>
### D01-W03 — 허용 행동·로컬 감독과의 경합

| 항목 | 요청 |
|---|---|
| 대상 | `watcher.py`의 Judgment 생성, `engine.py:on_judgment/_attempt_failed/_check_watcher`, `policy.py:RecoveryPolicy`, Linux supervisor 소비 연결 |
| 판정→행동 | 단순 위험은 경고, 앱/모드의 실제 준비 부족은 기능 제한, 출력 보호·승계는 Gateway, Linux의 합의한 서비스 복구는 기존 supervisor, VM 복구는 Engine→Manager |
| 권고 | local 복구의 시작/진행/소진/회복을 현재 서비스 수명과 결합해 중복 restart를 억제한다. 실제 unit 시작 기한·backoff에서 유예를 산정하며 매 heartbeat로 갱신하지 않음. 감독이 없는 앱을 복구 가능하다고 전제하지 않음 |
| 금지 | broker/input/warm-up/v2 장애만으로 반복 VM restart, 공통 기반 의심만으로 전 VM 복구·host reboot·pin 변경, Worker가 vehicle.control 발행·재무장 |
| 현재 설정 | policy restart/none, max_attempts 3, backoff 5/15/30s, attempt 110s(상한120), verify150s, transient retry5s, force 기본 off는 소스 기본값. 배포 적용값·원인 미확정의 새 정책은 별도 확인/합의 |
| 수락 | Agent 단독/앱 단독/OOM/전달 장애의 행동 주체가 구별되고 supervisor와 VM 복구의 동시 물리 제어 0. user stop·원 작업 미확정 중 새 경보로 신규 attempt 폭증 0 |

<a id="d01-w04"></a>
### D01-W04 — 원 작업·예산·회복 종결

| 항목 | 요청 |
|---|---|
| 대상 | `engine.py:_task_attempt/_commit/_flush_pending/_watch_in_flight`와 실제 호출 함수, `state.py`, `events.py`, `rejoin.py:completed/pump`, `worker.py`·`__main__.py` 조립 |
| 보존 | S01 손상 차단·Arming 불명, S02 동일 event ID·append 미확정·pending 공동 저장. 원 intent revision·host/execution·attempt/spec/deadline·force 승인·미완료 작업 소유는 Manager guard를 사용 |
| 권고 | 새 실행 생성/메시지 한 번으로 episode·시도 예산을 무조건 초기화하지 않음. 플랫폼 COMPLETED(새 Xen 실행 관측), 기능·자원 회복, 준비된 Standby 재합류를 별도 확인 |
| 회복 | 현재 실행의 새 기능 완료·필수 자원/입력·준비 유지. READY/heartbeat 복귀로 이전 Active 자동 원복·AUTO 재무장 없음 |
| 미확정 | 원 journal/attempt의 제한된 조회와 지원 이행. 하위 receipt/recovery 수습의 제공 범위는 아래 D03 절을 따르며 Worker 소비와 Gateway Authority 수습은 대기. 기록 조회를 실행기로 바꾸거나 만료 뒤 새 key/D/force로 우회하지 않음 |
| 수락 | 복구 중 stop·호출자 종료·응답 유실·동일 attempt replay에서 원 ID/D/예산 유지, 추가 물리 create/reboot 0. 준비 미완료만으로 정상 새 VM을 반복 복구 0. 신호 한 번으로 예산 초기화 0 |

<a id="d01-w05"></a>
### D01-W05 — 설명·배포·공동 수락

| 항목 | 요청 |
|---|---|
| 대상 | `model.py`의 결과·건강 모델, `mqtt.py:HealthPublisher`·`__main__.py`의 사건 sink, 기존 event/HTTP 조회, 관련 recovery 시험·unit 패키지 |
| 출력 | 확인 사실·추정 원인·근거 참조·영향 범위·허용 행동/보류·회복 조건을 제한된 상태와 전이 사건으로 남긴다. 아직 없는 wire 필드는 제안으로 분리하고 schema/consumers를 함께 검토 |
| 저장·권한 | raw MQTT·주기 표본·crash 본문을 Worker DB/큐로 보존하지 않음. 기존 작은 state/pending/events 사용. FastAPI의 허용 details와 인증·VM/요청 참조 정책은 기존 D02 요청을 따름 |
| 시험 | 기존 `test_recovery_watcher/engine/events/store/store_process/lock/drive_rejoin.py` 유지. 공통 계약의 11개 조건과 W01~W04 assertion을 실제 제품 코드·외부 FS/Xen/MQTT 경계 대역으로 실행 |
| 인수 | 플랫폼 local/ARM 근거는 제공물이다. 담당자 제품 구현→실제 Agent/Worker/Gateway 연결→설치본/실보드/장치 수락을 별도 표시. mock/fixture로 종단 완료를 대신하지 않음 |

아직 적용하지 않은 제안으로 정책 상한을 늘리거나 기존 강제종료 승인을 확대하지 않는다.
구현 담당자는 각 요청 ID에 후보·시험 assertion·배포·수락 상태를 누적 갱신한다.

<a id="d03-worker"></a>
## D03 — 수습한 원 결과 재확인·Standby 재합류 연결

**상태: 하위 receipt/recovery 수습 도구 제공 / Worker·Coordinator 소비 구현·제품 종단 수락 대기.**
D03-W01~W04는 이 문서의 추적 ID다. 새 Jira 티켓이나 담당 재배정이 아니며 D01-W01~W05의
미완료 상태를 유지한다. 실제 하위 계약은 [recovery-reconcile](../platform/cli-manager-spec.md#cmd-recovery-reconcile)과
[receipt 적용 확인](../../platform/guests/zephyr/telemetry/README.md#uncertain-receipt의-재주입-없는-적용-확인)에 둔다.

### D03-W01 — 원 attempt 종결을 상위 결과에 반영

| 항목 | 담당자 요청·수락 조건 |
|---|---|
| 실제 위치 | `recovery/engine.py:tick/collect/_task_replay/_on_result/_on_error/_close/completed_executions`, `state.py:RecoveryRecord`, `worker.py:run_once` |
| 현재 공백 | `_task_replay`는 원 attempt의 completed/failed를 읽지만 `tick`은 열린 record만 다룬다. 이미 final FAILED로 닫힌 상위 record는 운영자가 하위를 수습해도 자동 재확인되지 않는다 |
| 권고 연결 | 기존 운영 흐름에 명시적인 원 recovery/attempt 결과 재확인을 연결한다. `platform_recovery.py:PlatformRecovery.attempt`와 기존 `_task_replay`의 read-only 분기를 재사용하고, 지원 재확인에서 미완료이면 `recover_once`로 넘어가는 현재 실행 분기를 호출하지 않는다. 별도 daemon·웹 수습 API를 선행조건으로 만들지 않는다 |
| 입력·판정 | 원 recovery/attempt/operation·spec/hash·host·intent revision·deadline·현재 실행을 대조. lower completed+검증된 새 execution만 기존 COMPLETED 해석으로 반영. failed/not_submitted/stopped_without_replacement는 실패 종결이며 복구 성공이 아님 |
| 저장·사건 | 원 실패 사실을 삭제하지 않고 후속 확인 근거를 함께 기록한다. 같은 확인의 재전달·응답 유실·Worker 재시작에서도 같은 원 ID/시도 수·deadline·pending 공동 저장을 유지하고 논리 사건을 중복 발급하지 않는다. 새 wire 유형이 필요하면 별도 계약 검토 대상이다 |
| 수락 | 상위 final FAILED + 하위 수습 completed → 명시적 재확인으로 새 execution 연결. 같은 요청 반복 시 사건 논리 중복 0·추가 물리 호출 0. 하위 failed/null result를 COMPLETED로 승격 0. 검색 실패·다른 host·불완전 관측에서 성공 0 |

### D03-W02 — 같은 applied receipt에서 기존 provisioning/binding 흐름 재개

| 항목 | 담당자 요청·수락 조건 |
|---|---|
| 실제 위치 | `rejoin.py:ProductionBootstrap.__call__`, `RejoinCoordinator._work/_record/pump/completed`, `xenova_drive_binding.observe`의 기존 신뢰 관측 소비 |
| 현재 공백 | provisioner는 채널별 `provision`을 호출한다. uncertain은 차단하고, applied여도 원 `apply` 경로를 다시 실행한다. 하위 수습 결과의 read-only 재사용 분기가 아직 없다 |
| 권고 연결 | 운영자가 수습한 동일 receipt를 private 경계 안에서 다시 읽어 두 채널의 context/boot/phase·현재 관측·credential 원천을 검증하고 기존 ID를 재사용한다. 이 재개 분기에서는 Store 재주입·새 receipt/boot/instance 발급 0. 값·권한 재확인은 하위 `reconcile(..., execute=False)` 또는 같은 정식 조회를 사용하고, 자동 `--execute`로 차단을 풀지 않는다 |
| 실행·슬롯 | 진행 중인 원 A future/receipt lock이 있으면 원 작업 종료부터 확인한다. 새 Coordinator로 바뀌었다는 이유로 A에 두 번째 쓰기를 제출하지 않는다. B의 독립 조회·lease는 계속 갱신할 수 있어야 한다 |
| 새 실행 | 새 Manager execution이 정당하게 검증되면 기존 실행별 디렉터리·일반 공급을 사용한다. 새 boot/instance 발급은 그 새 실행의 정상 수명주기다. 같은 미확정 실행에 새 ID를 발급하는 우회와 구분한다 |
| 수락 | 같은 실행 uncertain→운영 수습 applied→명시적 재개에서 boot/app instance 불변·Store 추가 쓰기 0·현재 binding lease 재획득. 정체 A/정상 B, Worker/Coordinator 재시작, 이전 실행 receipt와 지연 READY 교차를 함께 시험. 과거 READY로 새 lease/준비/출력 승인 0 |

### D03-W03 — 회복·예산·의도와 준비의 별도 종결

| 항목 | 담당자 요청·수락 조건 |
|---|---|
| 실제 위치 | `engine.py:_watch_in_flight/_attempt_failed/_commit/_flush_pending`, `rejoin.py:_apply_readiness`, `RecoveryWorker.run_once/shutdown` |
| 불변조건 | 수습 확인으로 새 episode/attempt 예산을 초기화하거나 원 physical deadline을 재발급하지 않는다. lower COMPLETED는 새 Xen 실행 관측이며, 새 앱 진행·제어 적용·fresh 입력·준비 유지 뒤 Standby를 별도로 확인한다 |
| 사용자 의도 | 수습 전에 user stop/new revision이면 그 의도를 보존하고 과거 복구의 새 create 0. graceful/force 승인·S01 차단·S02 같은 사건 ID와 pending 공동 저장을 유지한다 |
| 출력 경계 | Coordinator의 준비 회복이나 Engine의 결과 재확인으로 기존 Active를 원복하거나 AUTO 재무장하지 않는다. Gateway의 현재 실행·제어·출력 guard가 계속 소유한다 |
| 수락 | 원 호출이 남은 경우 재개 0, 저장 실패는 상위 반영 성공 0. 정상 새 execution 수락과 옛 메시지 배제를 양방향 시험. 같은 VM의 Agent/앱/Publisher만 바뀐 경우를 VM 교체로 오인하지 않음. 새 instance 승인 경로가 없으면 미지원으로 남김 |

### D03-W04 — 실제 호출·반환·인수 시험

다음은 설치된 하위에서 호출할 예다. 원 attempt를 선택하며 새 key/spec를 만들지 않는다.

```python
import sys
sys.path.insert(0, '/usr/lib/xenova')
from xenova_management import Manager

manager = Manager()
# attempt_id는 기존 RecoveryRecord의 recovery_attempt_id다.
original = manager.call('recovery-operation', {'recovery_attempt_id': attempt_id})
diagnostic = manager.call('recovery-reconcile',
    {'recovery_attempt_id': attempt_id, 'timeout_ms': 3000}, budget_ms=3000)
# 운영자가 근거를 검토한 명시적 수습 단계에서만 execute=True를 사용한다.
# 이후 Worker는 원 결과만 다시 조회하며 이 진단을 물리 실행 허가로 쓰지 않는다.
```

진단은 기존 envelope와 원 operation ID·`replayed=true`를 유지한다.
`data.reconciliation.status=blocked`이면 조회 성공이어도 종결 근거가 없다.
명시 실행의 blocked는 `RECONCILE_REQUIRED`, 잠금은 `OPERATION_IN_PROGRESS`, host/intent 변경은
`TARGET_CHANGED/INTENT_MISMATCH`, 관측·조회 기한 오류는 미확인이다. `recovery-operation`은
기록 조회만 하며 새 물리 실행·Store 공급·준비/출력을 진행하지 않는다.

하위 T01~T12와 [D03 검증 보고](../verification/reconciliation-closure-20261027.md)의 후보·fixture를
제공물로 사용한다. 담당자 인수는 기존 `test_recovery_engine/store/store_process/events/lock.py`와
`test_drive_rejoin.py`에 원 final 상태 재확인·applied 재사용·정체 슬롯·재시작·새/옛 실행 교차를 추가한다.
T13의 실제 Worker/Coordinator 소비 종단은 **대기**다. 하위 fixture 통과나 요청서 작성으로 닫지 않는다.

## 요청 이력과 소유 경계

| 이력 | 보존한 의미·상태 |
|---|---|
| S15P31E110-155·237·124, D7·D8 | [2026-10-20 원 요청](2026-10-20-manual-app-gateway-ctrl.md)의 작성자·담당 이력·당시 미병합/미구현 표기를 보존한다. 역사적 표기를 현재 배포 상태로 해석하지 않는다 |
| 2026-10-20 Gateway 담당 분리 | Gateway는 공병우, Worker/Engine은 ctrl 담당 경계를 유지한다. Worker가 제어권·출력 허가를 발급하지 않는다 |
| 2026-10-27 D01-W01~W05 | 원 요청서 5절에 누적한 내용을 이 문서로 옮겼다. ID·수정 위치·입출력·조건·수락 assertion과 미완료 상태를 유지한다 |
| D03-W01~W04 | 하위 원 결과/receipt 수습의 소비 요청을 별도로 누적했다. D01·D02/D08 대기 및 Gateway 출력 소유를 유지한다 |
| 플랫폼 제공 요청 S15P31E110-279 | [2026-10-22 요청](2026-10-22-recovery-worker-platform.md)은 반대 방향의 플랫폼 제공 요청이다. capability/unit 제공과 아직 없는 별도 intents 조회를 구분하며 전체 완료로 닫지 않는다 |

문서 이동이나 플랫폼 로컬/ARM 통과는 Worker 제품 구현·소비 연결·설치본/실보드 수락이 아니다.
각 요청의 후보·시험·배포·수락 상태는 기존 D01-W01~W05에 이어 기록한다.
