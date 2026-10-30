# Gateway Authority 증거 기반 복원 요청

수신: 기존 Gateway 소유자 공병우. **상태: D03-G01~G04 요청·현행 보호 대조 완료 /
담당자 제품 구현·운영 복원·실보드/장치 수락 전.** ID는 문서 내부 추적용이며 새 Jira 티켓이 아니다.
기존 Gateway 요청은 [2026-10-20 원문](2026-10-20-manual-app-gateway-ctrl.md)에 보존돼 있으나
현재 Authority 복원의 전용 요청서는 없어 이 문서에서 단일 기준으로 추적한다.

## 현재 할 일과 읽는 순서

1. 아래 G01~G04에서 초기화·기존 원본·백업·retained·권한 소유의 조건을 확인한다.
2. [MQTT 계약](../contracts/mqtt-contract-v1.md) 5·6·9장과
   [Gateway 운영 안내](../../platform/services/gateway/README.md)의 verified 프로필을 대조한다.
3. [건강 판정 계약](../contracts/health-evidence-v1.md)의 준비·출력 책임과
   [Worker D03 요청](2026-10-27-recovery-health-ctrl.md#d03-worker)의 별도 재합류 조건을 확인한다.
4. 근거는 [복구 감사 D03](../verification/recovery-architecture-audit-20261027.md)과
   [저장 감사 S06](../verification/persistence-transaction-audit-20261027.md),
   [이번 하위 검증](../verification/reconciliation-closure-20261027.md)을 참고한다.

## 현재 코드와 수습 공백

`xenova_gateway/authority.py`의 `AuthorityStore.initialize/load/save/verify/reconcile`은 작은
파일 원본을 사용한다. `initialized`를 먼저 내구 저장한 뒤 `control.json`을 만든다.
partial init·파일 유실/손상은 차단하고, `save`의 replace/parent fsync 미확정은
`AUTHORITY_STORAGE_UNCERTAIN` latch로 이후 발급을 막는다. `verify`는 RAM과 파일 교체도 감지한다.
`reconcile(payload)`는 retained와 원본을 비교하는 함수이며 백업 복원 도구가 아니다.

`GatewayLease.acquire`, `__main__.py:main`은 단일 lease를 얻은 뒤 Authority와 출력 자원을 연다.
`verified.py`는 재시작 시 재무장을 요구한다. 이 보호를 유지하면서 아래 지원 절차가 필요하다.

## D03-G01 — 진단·최초 설치·기존 손상 구분

- 대상: `AuthorityStore.initialize/load/verify`, `config.py`의 초기화 옵션, `__main__.py:main`.
- 입력: 검증된 마운트·설치 신원, 원 marker/control, 현재 host, 단일 소유와 출력 금지 상태,
  retained·통제된 백업의 출처와 최신성 근거. 비밀·MQTT 원문 전체를 별도 저장하지 않는다.
- 권고: 기존 파일을 바꾸지 않는 진단과 명시적 복원을 분리한다. marker 존재+control 부재,
  partial init, unreadable/corrupt를 정상 최초 설치로 취급하지 않는다. `--initialize-control-state`
  재실행이나 marker 삭제로 generation=1을 만드는 경로는 금지한다.
- 수락: 진단 전후 원본/marker·generation·소비 ID 불변, 출력/발행 0. 최초 설치만 초기화 가능하고
  손상·권한 오류·마운트 실패는 차단한다. 현재 초기화 보호 회귀를 유지한다.

## D03-G02 — 최신 세대 근거가 있는 제한적 복원

- 대상: `AuthorityStore.validate/save/reconcile`와 담당자가 추가할 최소 로컬 복원 경로.
- 권고: 단일 lease·출력 금지 아래 현재 원본/retained/백업의 세대·선택·소비 문맥을 대조한다.
  정상 파싱·checksum은 파일의 유효성일 뿐 최신성 증명이 아니다. retained 부재/지연을 generation
  부재로 취급하지 않는다. 같은 generation에서 선택 충돌이면 자동 우선순위를 만들지 않는다.
- 허용 판단: 신뢰할 수 있는 최신 원본과 기대 generation/선택을 확정하고 늦은 발급·출력을 배제한
  경우에만 같은 writer/원자 저장으로 복원한다. 진단 뒤 원본/소유가 바뀌면 재판정한다.
- 거절: 최신 세대 근거를 모두 잃거나 백업이 과거일 가능성을 배제할 수 없으면 차단을 유지한다.
  운영자에게 필요한 출처/소유/장치 확인을 반환한다. 임의 max+1 추측·rollback/wrap·새 원장·SQLite로
  우회하지 않는다. 추가 신뢰 장치가 필요하면 파일 방식의 최소안과 비교해 별도 합의한다.
- 수락: 최신성 입증 백업의 제한적 복원 성공과 과거 정상 파일/retained 고세대/동세대 충돌의
  거절을 모두 시험한다. 불충분한 사례를 초기화 성공으로 바꾸지 않는다.

## D03-G03 — 복원 후에도 disarmed·과거 요청 배제

- 대상: `verified.py:VerifiedDriveMachine`, `verified_runner.py`의 retained/발행 연결,
  `AuthorityStore.consume_request/issue`, `__main__.py`의 출력 소유.
- 권고: 원본 복원은 출력 허가 복원이 아니다. mode·선호 owner를 보존해도 disarmed와
  rearm 요구를 유지하고 현재 실행 바인딩·새 제어 적용·실제 준비·명시적 재개를 별도 확인한다.
- 금지: 과거 resume/소비 ID를 새 승인으로 재생, heartbeat/receipt applied/Worker COMPLETED만으로
  출발, 이전 Active 자동 원복, 최종 장치 확인 없는 시리얼 제출을 물리 적용으로 표시.
- 수락: 복원·Gateway 재시작·연결 회복 뒤 자동 출력 0. 검증된 새 execution/boot/instance는 정상
  수락하고 과거 메시지는 거절한다. 두 Gateway가 동시에 발급·장치 open하는 횟수 0.

## D03-G04 — 저장 미확정·재시작·공동 인수

- 대상: `AuthorityStore.save/verify`, `GatewayLease`, `tests/test_verified.py`와 복원용 로컬 시험.
- 권고: write/file fsync/replace/parent fsync 실패·응답 유실·중간 프로세스 종료에서 성공을
  반환하거나 기존 latch를 조용히 해제하지 않는다. 같은 복원 확인을 재시도하며 원본과 실제
  출력 소유를 재대조한다. 원본 삭제나 과거 RAM 사본 저장으로 높은 generation을 덮지 않는다.
- 재사용 회귀: `test_authority_loss_conflict_exhaustion_and_single_owner`,
  `test_authority_save_failure_does_not_issue_or_publish`,
  `test_uninitialized_and_interrupted_initialization_cannot_guess_generation`,
  `test_uncertain_parent_fsync_never_rolls_back_committed_generation`,
  `test_restart_preserves_selection_but_never_output_or_old_request`.
- 추가 수락: 정상 복원·근거 부족 거절·재시작·중복 소유·소비 ID 재생·장치 제출/적용을 독립적으로
  확인한다. 이번 하위 회귀는 기존 보호의 근거이며 실제 복원 도구 구현을 뜻하지 않는다.

현재 운영자는 Authority 파일을 편집/삭제해 차단을 해제하지 않고, 원본·백업 출처와 공개 진단
근거를 보존해 Gateway 담당자에게 전달한다. 지원 복원 도구가 연결되기 전 일반 자동 복원을
안내하지 않는다. Worker·Agent·하위 Manager가 Authority를 대신 쓰는 수습 경로도 만들지 않는다.
