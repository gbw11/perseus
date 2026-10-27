# 복구 서비스의 Zephyr VM 생존 판정 — 제다빈(ctrl) 요청

> **현재 인수 기준(2026-10-27):** 0~4절은 원 요청과 당시 상태를 보존한다. 공통 drive/netfront
> 펌웨어·verified 실행 바인딩·readiness는 이후 구현됐다. 현재 Worker의 미완료 건강 분류와
> 담당자 수락은 [D01 누적 요청](#d01-worker)을 따른다. 원 S15P31E110-155·237·124와
> Gateway 공병우 소유 경계는 유지한다. 요청서 갱신은 담당자 제품 구현 완료가 아니다.

> **2026-10-20 갱신: dom0 게이트웨이(-98)는 공병우가 맡는다.** 제다빈 님께 남는 요청은 복구 서비스의 Zephyr VM 생존 판정(시나리오 D8 결정: Xen 도메인 상태 + `app.heartbeat`)과 계약 §6.1 리뷰, 아키텍처 설계서 갱신(D7)이다. 아래 §2 의 게이트웨이 항목은 공병우가 구현할 때의 기준으로 남긴다.

> 작성: 공병우, 2026-10-20 · 관련: S15P31E110-155·237, MQTT 계약 v1 §5·§6.1·§7 (브랜치 `feat/app/S15P31E110-155-manual-drive-app`)

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| 제다빈 (ctrl, dom0 게이트웨이·복구) | 계약 §6.1 `app.heartbeat` 추가 리뷰, 게이트웨이 구현 시 아래 신호 구분대로 판정 | 🟢 여유 있음 — 게이트웨이 구현 전. 계약 리뷰만 이번 MR에서 |


## 1. 확정된 사실

| 신호 | 의미 | 주기 | 판정 주체 | 상태 |
| --- | --- | --- | --- | --- |
| `drive.command` (`apps/{autonomous,manual}/events`) | 주행 명령이 실제로 나오고 있다. **대기 앱도 계속 발행** (수동 앱은 키 없으면 `v_mps 0`) | 50 Hz | **게이트웨이** — 100 ms 끊기면 전환 (§5.1·5.2) | ✅ 수동 앱 구현·검증 |
| `app.heartbeat` (`apps/{app_id}/events`) | 앱 **프로세스** 생존과 상태 `active/standby/idle/error`, `uptime_s` | 1초 | 백엔드·콘솔·메시지 보드 표시용, 3초 끊김 | ✅ 수동 앱 구현 (이번에 추가, §6.1) |
| `agent.heartbeat` (`vms/{vm_id}/heartbeat`) | VM 생존 | 5초 | 복구 서비스 — VM 재시작 (§3) | ✅ 기존 |

## 2. 게이트웨이에 부탁하는 것

1. **제어권 전환에 `app.heartbeat`를 쓰지 않기.** 1초 주기라 3번 놓치면 3초, 1 m/s면 약 3 m를 제어 없이 갑니다. 전환은 지금 계약대로 `drive.command` 100 ms.
2. **수동 앱 `state`는 게이트웨이가 낸 `vehicle/control`의 `active_app`을 그대로 반영합니다.** 앱이 스스로 활성을 선언하지 않습니다 (예전 명세의 "자기 보고값을 승격에 쓰지 않는다" 원칙과 같음).
3. **수동 앱은 `vehicle/control`을 못 받으면 `generation 0`으로 발행합니다.** 게이트웨이는 시작 시 `vehicle/control`을 retained로 먼저 내 주세요 (§5.3). ESP32는 세대가 다르면 버리므로 안전합니다.
4. 예전 `docs/design/vehicle-control-spec.md`의 승격 조건(최근 500 ms 유효 명령 25개 이상, 간격 100 ms 이하)과 `controller_session_id`는 지금 계약에 없습니다. 게이트웨이 구현에 필요하면 계약 §5에 옮기는 안을 내 주세요. 재시작 구분은 공통 봉투의 `agent_instance_id`(프로세스마다 새로 생성)로 대신할 수 있습니다.
5. **자율·수동주행 VM이 Zephyr로 정해져 에이전트가 없습니다** (시나리오 §2, D8). 복구 서비스의 VM 생존 판정과 §5.2 `AUTO_READY`의 "에이전트 `ONLINE`" 조건을 무엇으로 바꿀지 정해 주세요. 앱 쪽은 `app.heartbeat`(1초)를 Zephyr 판에서도 보냅니다 — 도메인 상태 + `drive.command` 신선도에 보조로 쓸 수 있습니다.

## 3. 내가 이미 해 둔 것

| 내용 | 위치 | 병합됨? |
| --- | --- | --- |
| 계약 §6.1 `app.heartbeat`, §7 `drive.input` 키·반복·끊김 | `docs/contracts/mqtt-contract-v1.md` | ❌ 위 브랜치 MR 미병합 |
| 노션 MQTT-06·11, 변경 이력 | 노션 인터페이스 계약 | ✅ 2026-10-20 |
| 게이트웨이 목 (vehicle.control retained 발행) | `platform/apps/manual/tools/mock_gateway.py`, 브라우저 테스트 화면 | ❌ 미병합 |
| 게이트웨이 구현 | 하지 않음 (ctrl 영역) | — |

## 4. 처리되면

게이트웨이가 생기면 수동 앱과 붙여 "자율 명령 끊김 → 100 ms 안에 MANUAL 전환 → 수동 앱 `state: active`"를 같이 확인하겠습니다 (S15P31E110-94 E2E 선행).

<a id="d01-worker"></a>
## 5. D01 — Worker 건강 분류·복구 근거 누적 요청

**CPU·메모리·스레드 상태를 정식 건강 판정의 입력으로 사용해야 합니다. 다만 그 결과는 모두
‘VM 재시작’으로 이어지는 것이 아니라, 경고·준비 제한·기능 축소·승계·앱 재시작·VM 복구 중
적절한 행동으로 연결돼야 합니다.**

수신은 기존 Recovery Worker/Engine 담당(ctrl)이며 Gateway의 모드·owner·출력 허가·승계는
공병우 소유로 남긴다. 아래 ID는 문서 내 추적용이다. 공통 정의·조건별 수락은
[건강 근거 원문](../contracts/health-evidence-v1.md), Linux 선행 제공물은
[누적 Linux 요청](../platform/management-integration-request.md#linux-health)을 따른다.
D02·D08의 하위 마감과 상위 미구현은 같은 문서의 기존 요청으로 유지한다.

### 현재 확인과 상태

- 기존 D01 반례: `LastSeenTable._last_received`와 Watcher `_link_proven`은 채널 전역이다.
  다른 VM만 수신해도 Linux/legacy 대상은 loss 판정이 가능했다. verified 현재 binding의
  최초 무수신 보호는 이미 있으며 재구현 대상이 아니다. 감사의 근거와 이번 재확인은
  [검증 보고](../verification/health-evidence-20261027.md)에 연결한다.
- 이후의 CPU/메모리/thread·서비스 기능 건강 연결은 **이번 확장 요구**다. 기존 감사가 이 전체를
  결함으로 입증한 것은 아니다. 현재 Watcher의 HEALTHY는 신호·Xen 근거이며 자원·앱 기능 전체의
  건강 보증으로 확대하면 안 된다.
- 상태: D01-W01~W05 **요청 작성·제공물 준비 / 담당자 구현·로컬·연동·실보드 수락 전**.
  이번 플랫폼 작업은 Worker·Agent·FastAPI·Spring·Gateway 제품 소스를 변경하지 않았다.

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

### D01-W03 — 허용 행동·로컬 감독과의 경합

| 항목 | 요청 |
|---|---|
| 대상 | `watcher.py`의 Judgment 생성, `engine.py:on_judgment/_attempt_failed/_check_watcher`, `policy.py:RecoveryPolicy`, Linux supervisor 소비 연결 |
| 판정→행동 | 단순 위험은 경고, 앱/모드의 실제 준비 부족은 기능 제한, 출력 보호·승계는 Gateway, Linux의 합의한 서비스 복구는 기존 supervisor, VM 복구는 Engine→Manager |
| 권고 | local 복구의 시작/진행/소진/회복을 현재 서비스 수명과 결합해 중복 restart를 억제한다. 실제 unit 시작 기한·backoff에서 유예를 산정하며 매 heartbeat로 갱신하지 않음. 감독이 없는 앱을 복구 가능하다고 전제하지 않음 |
| 금지 | broker/input/warm-up/v2 장애만으로 반복 VM restart, 공통 기반 의심만으로 전 VM 복구·host reboot·pin 변경, Worker가 vehicle.control 발행·재무장 |
| 현재 설정 | policy restart/none, max_attempts 3, backoff 5/15/30s, attempt 110s(상한120), verify150s, transient retry5s, force 기본 off는 소스 기본값. 배포 적용값·원인 미확정의 새 정책은 별도 확인/합의 |
| 수락 | Agent 단독/앱 단독/OOM/전달 장애의 행동 주체가 구별되고 supervisor와 VM 복구의 동시 물리 제어 0. user stop·원 작업 미확정 중 새 경보로 신규 attempt 폭증 0 |

### D01-W04 — 원 작업·예산·회복 종결

| 항목 | 요청 |
|---|---|
| 대상 | `engine.py:_task_attempt/_commit/_flush_pending/_watch_in_flight`와 실제 호출 함수, `state.py`, `events.py`, `rejoin.py:completed/pump`, `worker.py`·`__main__.py` 조립 |
| 보존 | S01 손상 차단·Arming 불명, S02 동일 event ID·append 미확정·pending 공동 저장. 원 intent revision·host/execution·attempt/spec/deadline·force 승인·미완료 작업 소유는 Manager guard를 사용 |
| 권고 | 새 실행 생성/메시지 한 번으로 episode·시도 예산을 무조건 초기화하지 않음. 플랫폼 COMPLETED(새 Xen 실행 관측), 기능·자원 회복, 준비된 Standby 재합류를 별도 확인 |
| 회복 | 현재 실행의 새 기능 완료·필수 자원/입력·준비 유지. READY/heartbeat 복귀로 이전 Active 자동 원복·AUTO 재무장 없음 |
| 미확정 | 원 journal/attempt의 제한된 조회와 지원 이행. D03의 receipt/Authority 수습은 미구현 범위 유지. 기록 조회를 실행기로 바꾸거나 만료 뒤 새 key/D/force로 우회하지 않음 |
| 수락 | 복구 중 stop·호출자 종료·응답 유실·동일 attempt replay에서 원 ID/D/예산 유지, 추가 물리 create/reboot 0. 준비 미완료만으로 정상 새 VM을 반복 복구 0. 신호 한 번으로 예산 초기화 0 |

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
