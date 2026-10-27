# Recovery Worker 건강 분류·복구·재합류 연결 요청

수신: Recovery Worker·Engine 담당(ctrl). Gateway의 모드·owner·출력 허가·승계는
공병우 소유로 유지한다.
**상태: D01-W01~W05 요청 작성·플랫폼 근거 제공 완료 / 담당자 구현·로컬·연동·실보드 수락 전.**
아래 ID는 기존 요청 ID를 유지한 문서 내 추적 ID이며 새 Jira 티켓이 아니다.

## 현재 할 일

| 순서·요청 ID | 구현할 일 | 완료를 확인할 근거 |
|---|---|---|
| 1 · [D01-W01](#d01-w01) | 전역 연결과 VM·현재 실행·신호별 전달 근거를 분리 | 다른 VM의 수신으로 대상의 전달 정상/장애를 확정하지 않음 |
| 2 · [D01-W02](#d01-w02) | 유효한 자원·기능·진행·관측 근거를 판정에 연결 | CPU%·heartbeat 하나로 준비나 복구 허가를 만들지 않음 |
| 3 · [D01-W03](#d01-w03) | 판정별 행동 주체와 Linux 로컬 감독의 경합을 연결 | 출력/승계는 Gateway, 서비스 복구는 감독, VM 복구는 기존 Engine 경로 |
| 4 · [D01-W04](#d01-w04) | 원 작업·기한·예산과 기능 회복·Standby 재합류를 구분 | S01/S02·사용자 stop 보호, 중복 실행·무조건 예산 초기화 0 |
| 5 · [D01-W05](#d01-w05) | 근거·행동·보류 이유와 배포/공동 수락을 연결 | 담당자 구현·제품 연결·실보드/장치 수락을 별도 기록 |

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
| 행동 | high CPU+정상 완료는 경고/정상 부하, 유효 작업 정체+확정 기한 위반은 해당 기능 실패. 실제 필수 자원 실패는 준비 제한을 존중. 낮은 CPU/정상 heartbeat가 fatal·기능 실패를 상쇄하지 않음 |
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
| 미확정 | 원 journal/attempt의 제한된 조회와 지원 이행. D03의 receipt/Authority 수습은 미구현 범위 유지. 기록 조회를 실행기로 바꾸거나 만료 뒤 새 key/D/force로 우회하지 않음 |
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

## 요청 이력과 소유 경계

| 이력 | 보존한 의미·상태 |
|---|---|
| S15P31E110-155·237·124, D7·D8 | [2026-10-20 원 요청](2026-10-20-manual-app-gateway-ctrl.md)의 작성자·담당 이력·당시 미병합/미구현 표기를 보존한다. 역사적 표기를 현재 배포 상태로 해석하지 않는다 |
| 2026-10-20 Gateway 담당 분리 | Gateway는 공병우, Worker/Engine은 ctrl 담당 경계를 유지한다. Worker가 제어권·출력 허가를 발급하지 않는다 |
| 2026-10-27 D01-W01~W05 | 원 요청서 5절에 누적한 내용을 이 문서로 옮겼다. ID·수정 위치·입출력·조건·수락 assertion과 미완료 상태를 유지한다 |
| 플랫폼 제공 요청 S15P31E110-279 | [2026-10-22 요청](2026-10-22-recovery-worker-platform.md)은 반대 방향의 플랫폼 제공 요청이다. capability/unit 제공과 아직 없는 별도 intents 조회를 구분하며 전체 완료로 닫지 않는다 |

문서 이동이나 플랫폼 로컬/ARM 통과는 Worker 제품 구현·소비 연결·설치본/실보드 수락이 아니다.
각 요청의 후보·시험·배포·수락 상태는 기존 D01-W01~W05에 이어 기록한다.
