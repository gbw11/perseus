# Recovery Worker·Engine 후속 구현 요청

대상: Recovery Worker·Engine 담당자. **상태: 최소 소비 연결 구현·로컬 검증 완료 / 나머지 담당자 구현·종단 수락 전.**
기존 D01-W01~W05·D03-W01~W04를 유지하며 새 티켓으로 재발급하지 않습니다.

## 현재 할 일과 읽는 순서

2026-11-01 소비 경계(D01/D03 ID 유지): create의 선택 resource 생략은 설정 요청 계약이고,
journal 1.2는 하위 내부 저장이다. Worker의 heartbeat·앱 readiness·관측 누락에 기본값을 주입하지 않는다.
원 create/수습 결과는 최초 선택과 확정 `initial_spec`을 구분한다. 공개 Manager 응답만 읽는
Worker에 raw journal 버전별 분기/파일 해석을 새로 요구하지 않는다. 하위가 지원 1.0/1.1/1.2
혼재를 처리한다. create를 재전달하는 실제 adapter가 있다면 같은 key에서 두 자원의 생략↔
원 확정값 명시를 허용하되 최신 기본값/현재 구성으로 다시 선택하지 않는다. 복구의 원 attempt/기한·
CPU 정책 2.0·새 incarnation 검증은 그대로다. 이번에 Worker 제품은 수정하지 않았다.
공동 수락: 생략 생성의 실패→같은 원 수습→내구 예약 반환→조건부 recovery 정상 진행;
원 표본 부재/관측 실패는 미확인, 기본값 변경은 과거 확정값/attempt/deadline을 바꾸지 않음.
오류/진단 비용 변경은 실제 반복 포장·stack·handler 대기가 확인된 부분만 담당 구현으로
진행하고 episode 필수 사건/pending 저장을 부가 로그로 낮추지 않는다.

1. 아래 최소 연결/확장 범위와 [기존 Worker 요청](2026-10-27-recovery-health-ctrl.md)의 현재 할 일을 읽습니다.
2. [건강 근거](../contracts/health-evidence-v1.md), [주행 설계](../platform/drive-pipeline-architecture.md) 9·13·16·19절을 읽습니다.
3. [복구 시나리오](../platform/recovery-scenarios.md)의 A1·E1·E4·D1~D4·F2·F4·G1·G2와 [관리 계약](../platform/management-boundaries.md)을 대조합니다.
4. 원 결과·수습의 상세 입력과 assertion은 기존 문서의 D03 절을 따릅니다.

## 최소 수정과 담당자 작업

| 구분·연결 ID | 대상 파일·함수 | 입력·출력 및 요구 | 수락 조건 |
|---|---|---|---|
| 승인된 최소 연결, 로컬 완료 | `recovery/rejoin.py`, `mqtt.py`, `heartbeats.py`, `watcher.py`, `observe.py`, `model.py` | warm-drive-1의 앱별 신원·실제 감독·대기 소비. 두 앱 각각의 근거 요구, 기능 제한과 VM 장애 분리. Engine/예산 정책은 유지 | `tests/test_warm_liveness.py`, `test_drive_rejoin.py`의 실제 Engine/Manager/receipt와 외부 Xen/Store 대역. [후속 검증](../verification/zephyr-drive-foundation-20261029.md#후속-앱별-수명과-warm-준비-연결) |
| D01-W01·W02, 기존 대기 | `watcher.py:Watcher`와 `_judge`, `model.py`·건강 입력 | 대상별 전달 자격·원 실행·자원/실제 진행/기능 상태를 판정에 연결. 다른 VM 수신·CPU% 하나로 장애 확정하지 않음 | 기존 D01 반례와 원 요청의 assertion 유지. 지원되지 않는 계측을 정상 0으로 치환하지 않음 |
| D01-W03·W04·E4, 기존 대기/확장 | `engine.py`, `policy.py`, 로컬 수습과의 연결부 | 경고·앱 수습·VM 복구를 분리. 안정 구간·episode 회복·반복 예산의 정책과 소진 후 수습을 명시 | 로컬 재초기화와 중복 물리 복구 0, 불충분한 잠깐 정상으로 예산 무제한 초기화 0. 숫자는 원천·실측·운용 정책으로 합의 |
| D03-W01~W04, 기존 대기 | `engine.py`의 원 attempt 재확인, `rejoin.py` | 원 operation·deadline·receipt의 뒤늦은 확정 결과를 받아 새 실행 provisioning→바인딩→Standby 준비로 연결 | 조회가 새 물리 실행/기한 발급 0. Manager completed를 앱 재합류·출력 허가로 바꾸지 않음 |
| D01-W05·A1/E1·D4·G2, 후속 | 기존 요청의 판정·저장·배포 지점 | 최초 무수신, 공통 기반 오류, 기록 보존·host 재시작을 별도 정책/배포 수락으로 정리 | 관측 불가와 실제 장애 구분. 손상 이력·저장 실패를 최초 빈 상태로 오인하지 않으며 S01/S02 보존 |

## 시나리오 종단 마감에서 추가로 확인할 연결

기존 ID와 정책 소유는 아래처럼 유지한다. 플랫폼의 새 로컬 진단을 Worker의 실행기로 호출하지 않는다.

| 기존 ID·시나리오 | 실제 위치·권고 | 구체적인 수락 |
|---|---|---|
| D01-W01·W02, A1·E1·D2 | `watcher.py:_judge/_signal_age`, `observe.py`, `heartbeats.py`에서 첫 무수신·전달 불가·기능 제한·보고자 장애를 구별. 현재 실행의 provisioning·경로 확인과 유한한 시작 실패 판정을 연결 | 처음부터 신호 없음이 영구 정상/무조건 VM 재시작으로 바뀌지 않음. 보고자만 장애/앱 정상, 대기 정상/계산 정체를 각각 주입 |
| D01-W03·W04, E4·D4·DO2 | `engine.py:_check_watcher/_attempt_failed/_close`, `policy.py`에서 안정 구간과 episode 간 예산·회복 조건을 명시. 현재 3회는 한 recovery의 상한이며 새 episode 반복의 상한이 아님 | 잠깐의 HEALTHY로 누적 제한이 초기화되지 않음. 공통 경로 장애 중 개별 VM 복구 폭주 0. 값은 정책 소유자가 근거와 함께 확정 |
| D03-W01~W04, F5·H1 | 원 attempt의 하위 reconcile 확정 결과를 Engine/Coordinator에 반영. 활성 VM 복구 전 출력 보호와 계획 정비의 인계 완료를 구분 | COMMITTED/PUBACK을 새 앱 출력·장치 정지로 치환하지 않음. 정상 owner의 준비 실패 시 계획 정비 보류. 복구 후 새 execution이 Standby로 재합류하고 기존 Active 유지 |
| D01-W05, G2·AO3 | 기본 `/run/xenova/recovery`의 host 재부팅 보존 정책을 별도 확정. S01/S02의 같은-host 재시작 보호와 구별 | 새 host에서 옛 단조 기한/실행/예산을 현재 것으로 재사용하지 않음. 손상/조회 실패를 최초 빈 상태로 숨기지 않음. 경로 변경·이관은 별도 승인 |

위 항목은 담당자 구현·통합 수락 전이다. 플랫폼이 Worker 정책을 대신 변경하지 않았으며,
하위의 원 key 순회 예산 보완은 원 attempt/deadline 재발급이나 후속 실행 주체 추가가 아니다.

Gateway의 `submit_transition`은 한 프로세스 안의 actor queue다. 별도 Worker 프로세스가
현재 이 메서드를 직접 호출할 수 있는 운영 전송 경로는 제공되지 않는다. 기존 MQTT 상태나
Authority 파일 읽기만으로 보호 완료를 대신 승인하지 않는다. 두 프로세스 사이의 최소 요청/
결과 연결은 Gateway 담당자와 입력·권한·원 요청 식별·기한·실패 의미를 확정한 뒤 구현하며,
그 전에는 계획 정비·복구 보호의 종단 완료로 표시하지 않는다.

## 제공물·금지 효과

root rejoin 설정의 선택형 `app_state_models`는 실제 고정 firmware/adapter의 stateless/stateful
선언과 일치시켜 바인딩에 전달한다. 누락/상태 있는 앱을 stateless 준비로 승인하지 않는다.
이 선언은 상태 적용 완료나 출력 허가가 아니다. Gateway의 PREPARED/권위 COMMITTED를
Manager recovery COMPLETED나 Standby 재합류로 치환하지 않는다. 원 요청/커밋 결과 조회도
새 recover_once 실행의 근거가 아니다. 이 메타데이터 연결은 Worker의 장애/시도 정책을 바꾸지 않는다.

플랫폼의 원 key·execution·의도 revision·원 deadline·pending/uncertain 조회·receipt 재확인은 기존 계약을 사용합니다. 새 준비 프로필만으로 Engine의 시도 수·backoff·force·저장 위치를 바꾸지 않습니다. 사용자 stop을 오래된 복구가 running으로 되돌려서는 안 됩니다.

Gateway가 모드·출력 보호·승계를 맡고 Worker는 기존 Engine/Manager를 통한 필요한 VM 복구를 맡습니다. 입력 부재·Jetson 단절·준비 미완료 자체를 반복 VM 재시작으로 수습하지 않습니다.

## 일반 조회 변경의 소비 연결

기존 D01-W01/W02·A2/F4/FO1 요청의 후속이며 새 장애/재시도 정책이 아니다.
하위 4번의 [조회 계약](../platform/cli-manager-spec.md#read-concurrency)과
[HTTP 공동 수락](20261029-request-http-spring-ui.md#반복-조회와-제어-진행성-연결)을 함께 읽는다.
Worker 제품은 이번 플랫폼 변경에서 수정하지 않았다.

`recovery/observe.py:PlatformObserver.__call__/_refresh_intents/_merge`는 list 실패와 Xen
부재를 구분해야 한다. 현재 `_refresh_intents` 실패 뒤 기존 intent cache를 남기는 경로와
별도 Xen 목록의 출처/신선도를 대조한다. TARGET_CHANGED·관측 슬롯 OPERATION_IN_PROGRESS·
열람/원장 오류를 빈 목록/VM 사망으로 변환하지 않으며, 남겨둔 cache를 현재 수명/실행의
검증 근거로 승격하지 않는다. 새 TTL/cache를 추가하거나 미확인 입력에 recover_once를
바로 제출하는 것으로 해결하지 않는다. 요청별 Manager fork 사용은 해당 어댑터의 실제
버전 확인 뒤 연결하고 같은 공유 inner를 여러 thread가 직접 호출하지 않는다.

`watcher.py:_judge`와 기존 Engine의 원 결과 소비는 관측 불가를 별도로 유지하고, 원
attempt·host/실행·의도·물리 deadline을 그대로 대조한다. asset RUNNING은 준비 실행 관측,
recovery completed는 기존 플랫폼 새 실행 관측이며 Standby 재합류 완료가 아니다.
조회 응답만으로 후속 복구 시도·새 key/기한·force를 만들지 않는다.

인수 assertion은 정상 관측→느린 list/identity·부분 열람 실패→회복, 그 사이 사용자 stop 및
원 attempt 조회를 주입하여 관측 오류에서 복구 폭주 0·옛 intent 재적용 0·정상 관측 재개를
확인하는 것이다. 하위 S4-T02/03의 보호/진행성은 제공물이며 실제 Worker/상위 연결 수락은
담당자 구현 후 별도로 남긴다. D01/D03의 기존 미완료를 이 변경으로 닫지 않는다.
S4-T12 후속의 관측 슬롯 소유 부모 중단/잔존 helper·비정상 잠금 파일도 관측 불가 입력이다.
슬롯 경합·권한/경로 오류가 있는 동안 새 recover_once 제출 0·옛 intent 재적용 0을 확인하고,
helper 종료/정상 경로 회복 후 정상 관측이 다시 소비되는 양쪽을 수락한다. Worker가 슬롯 파일을
지우거나 chmod하여 차단을 해제하지 않는다. 이 요청은 Worker 제품에서 아직 수락 전이다.

## 원 create 실패 종결의 소비

5번 하위의 [원 작업 수습](../platform/cli-manager-spec.md#cmd-operation-reconcile)을
일반 VM recovery나 app Standby 재합류와 합치지 않는다. 기존 D01/D03·시도/기한/의도
보호는 유지하고 Worker 제품 구현은 계속 대기 상태다.

`recovery/observe.py:PlatformObserver.__call__/_refresh_intents/_merge`와 Engine의
원 결과 소비에서 asset RECONCILING은 명시 자산 수습 실행, FAILED는 원 생성 실패와
기록된 수습 완료다. VM 사망/새 실행 관측/복구 완료/준비/출력 허가가 아니다. 원 journal
status의 non-SUCCEEDED 하나만 보고 자산 수습을 반복 VM 재시작으로 연결하지 않는다.
하위 제어/Recovery guard는 공통 예약 판정과 내구 재확인을 사용하지만 Worker가 원
attempt·사용자 stop·backoff/시도 예산을 새로 발급할 근거는 아니다.

Worker에 자동 operation-reconcile 실행/삭제 권한/수습 스케줄러를 추가하지 않는다.
수습 필요·관측 오류·슬롯 경합은 원 작업 참조를 유지해 운영 수습으로 넘기고 raw
workspace/credential/진단 원문을 공개 사건으로 전달하지 않는다. 원 실패 파일/기록을
지워 관측 대상 부재로 만드는 fallback은 금지한다.

수락은 실패 private/잔존 자식/수습 중/기록 완료/저장 미확정을 순서대로 입력하고,
recover_once 폭주 0·옛 intent/attempt 재적용 0·수습을 VM 복구/Standby 완료로 승격 0을
확인한다. 이후 사용자가 명시 생성·시작한 새 VM execution은 기존 신원·준비 계약으로
정상 수락한다. 플랫폼 로컬 수습·신규 생성 통과는 실제 Worker 연결 수락이 아니다.

## 진행 기록

- 2026-10-31 5.5 후속: 하위 recover_once/start의 CPU 문맥은 OS 공통 UUID 정적 배치를
  사용한다. Debian·Ubuntu·Zephyr의 shared/exclusive mask와 새 incarnation의 모든 vCPU를
  확인하며 live repin으로 불일치를 보정하지 않는다. lifecycle 검사 중 대상 lease도
  조건부 복구를 제한한다. 상충 대상만 같은 원 작업으로 재확인하고 새 attempt·deadline을
  지급하지 않는다. 정책 오류는 해당 VM의 신원 확인 후 stop을 막는 이유가 아니다.
  [후속 근거](../verification/recovery-scenario-closure-20261030.md#stage55-followup)와 기존
  D01/D03을 함께 사용한다. Worker의 건강·앱 재시작 정책/제품 연결을 구현한 것은 아니다.

- 2026-10-29: 승인한 최소 소비 연결과 로컬 회귀를 완료했습니다. D01·D03 요청 전체나 실제 Xen/Store·설치본 수락을 완료한 것은 아닙니다. Worker가 앱 재초기화를 자동 선택·요청하는 정책은 구현하지 않았으며, 로컬 root 수동 경로와 별도로 D01-W03·W04에서 결정해야 합니다.

- 2026-10-31 5.5 교차 보완: CPU 정책/receipt 손상·read EIO·준비 중 상태에서 근거 없는
  start/recover를 차단하면서 정확한 Manager stop과 회복 후 start를 로컬 확인했다.
  생성 실패 수습·내구 반환 뒤 실제 CPU writer와 정상 실행이 재개되고, journal 전 살아 있는
  lifecycle lease는 writer가 계속 존중한다. [최신 근거](../verification/recovery-scenario-closure-20261030.md#stage55-cross-boundaries)를 연결하며
  D01/D03 정책을 대신 구현하지 않는다. 하위 import 집합을 교체할 때 장기 Worker의 이전
  import와 구형 writer를 혼합하지 않는 조건은 [배포 인계](20261029-request-http-spring-ui.md#같은-후보의-설치-집합과-프로세스-교체-인계-67번-미실행)를 따른다.
