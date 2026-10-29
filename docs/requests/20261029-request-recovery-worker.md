# Recovery Worker·Engine 후속 구현 요청

대상: Recovery Worker·Engine 담당자. **상태: 최소 소비 연결 구현·로컬 검증 완료 / 나머지 담당자 구현·종단 수락 전.**
기존 D01-W01~W05·D03-W01~W04를 유지하며 새 티켓으로 재발급하지 않습니다.

## 현재 할 일과 읽는 순서

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

## 제공물·금지 효과

root rejoin 설정의 선택형 `app_state_models`는 실제 고정 firmware/adapter의 stateless/stateful
선언과 일치시켜 바인딩에 전달한다. 누락/상태 있는 앱을 stateless 준비로 승인하지 않는다.
이 선언은 상태 적용 완료나 출력 허가가 아니다. Gateway의 PREPARED/권위 COMMITTED를
Manager recovery COMPLETED나 Standby 재합류로 치환하지 않는다. 원 요청/커밋 결과 조회도
새 recover_once 실행의 근거가 아니다. 이 메타데이터 연결은 Worker의 장애/시도 정책을 바꾸지 않는다.

플랫폼의 원 key·execution·의도 revision·원 deadline·pending/uncertain 조회·receipt 재확인은 기존 계약을 사용합니다. 새 준비 프로필만으로 Engine의 시도 수·backoff·force·저장 위치를 바꾸지 않습니다. 사용자 stop을 오래된 복구가 running으로 되돌려서는 안 됩니다.

Gateway가 모드·출력 보호·승계를 맡고 Worker는 기존 Engine/Manager를 통한 필요한 VM 복구를 맡습니다. 입력 부재·Jetson 단절·준비 미완료 자체를 반복 VM 재시작으로 수습하지 않습니다.

## 진행 기록

- 2026-10-29: 승인한 최소 소비 연결과 로컬 회귀를 완료했습니다. D01·D03 요청 전체나 실제 Xen/Store·설치본 수락을 완료한 것은 아닙니다. Worker가 앱 재초기화를 자동 선택·요청하는 정책은 구현하지 않았으며, 로컬 root 수동 경로와 별도로 D01-W03·W04에서 결정해야 합니다.
