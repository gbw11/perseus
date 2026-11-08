# 374 A/B 조회 최적화·오류 순서 보호 로컬 통합 — 2026-11-08

**로컬 통합 검증은 통과했다. 실보드의 374 연속 freshness 수락은 아직 남아 있다.**

## 작업 기준과 Git 범위

- 작업 브랜치: `feat/platform/S15P31E110-374-freshness-recheck`.
- 기준 HEAD: `d54f1ed7` — 374의 오류 순서 보호 `f1013ad3`가 이미 포함된 platform 기준.
- 가져온 변경: `1ab5ee7f` — `perf(platform): reuse A/B binding observations during rejoin`.
- 실행 명령: `git cherry-pick --no-commit 1ab5ee7f`.
- 커밋을 새로 만들거나 다른 로컬 브랜치의 내용을 수정하지 않았다. 기존 미커밋 실보드 점검 문서 두 개도 보존했다.

이번에는 A/B 관측 비용 절감에 필요한 커밋과 그 테스트를 가져왔다. stress 브랜치의 별도 장애 범위/Gateway 정책 변경과 vehicle 전체를 병합한 작업이 아니다. 원격 브랜치·보호 브랜치는 변경하지 않았다. 보드 접속·배포·설치·서비스 재시작은 하지 않았다.

## 함께 보존한 동작

A/B가 같은 회차에 due이면 RejoinCoordinator가 한 일괄 관측을 제출한다. Manager는 대상 실행 입력을 필요한 VM으로 제한하면서 전체 등록부 검증을 유지한다. Execution은 Xen 전체 목록 두 번과 native incarnation batch 두 번을 재사용한다. 게시 전 현재 대상·host·기한·실행 검증과 최종 admission guard는 유지한다.

기존 374 수정도 유지했다. 먼저 시작한 identity-only 조회가 늦게 실패해도, 그 뒤 검증·게시된 새 정상 관측을 과거 실패로 덮지 않는다. 원 실패 호출자는 원래 오류를 받는다. 최신 조회 자체의 실패는 기존처럼 snapshot을 미확인으로 만든다. 원 관측 시각과 3초 TTL, 내부 시간 한도는 늘리지 않았다.

기존 소스와 가져온 패치가 자동 병합됐으며 Execution.observe의 시작 시각 전달 보호가 남은 것을 직접 대조했다. 불필요한 추가 구현 수정은 하지 않았다.

## 통합 경합 테스트

`platform/tests/test_xenova_binding_pair_error_order.py`에 네 경우를 추가했다.

- 이전 단일 identity-only 조회 / 이전 paired 조회 두 경로.
- 각 경로에서 DEADLINE_EXCEEDED / TARGET_CHANGED 두 원 오류.
- 이전 조회를 실제 thread/barrier로 멈춘 사이 새로운 A/B 관측을 성공시킨다.
- 뒤늦은 실패 후 두 VM의 관측 파일이 새 성공 시점과 바이트 단위로 같고 last_error가 없는지 확인한다.
- 실패한 호출의 원 오류를 유지하고, 새 paired 성공 경로가 Xen 목록 2회·native batch 2회를 사용했는지 확인한다.

Xen/native helper 출력은 fixture 경계다. Manager/Execution/관측 파일/잠금/Coordinator와 thread 경합은 실제 구현을 사용한다. 실보드 Xen/CPU 부하 결과로 확대하지 않는다.

## 결과

| 검증 묶음 | 통과 | 하위 검증 통과 |
| --- | ---: | ---: |
| A/B 관측·순서 보호·기존 단일/배치 조회 | 64 | 5 |
| RejoinCoordinator·Worker·warm liveness | 107 | 0 |
| 공유 관측·follower·inventory·앱·표시 전환 | 79 | 0 |
| Management·복구 기반 호출 | 117 | 20 |
| 합계 | **367** | **25** |

별도 테스트 프로세스에서 Execution.observe 메서드만 기존 stress 버전으로 바꾼 비교 실험도 했다. 새 네 테스트 중 단일 identity 경합 두 경우가 실제로 실패했고 paired 두 경우는 통과했다. 비교 프로세스만의 변경이며 저장소 파일은 바꾸지 않았다. 이 기대된 2개 실패는 통합 구현의 실패가 아니라, 회귀 테스트가 374 보호 누락을 검출한다는 근거다.

WSL CPython 3.14.4와 `outputs/venvs/display-374-integration`의 격리 환경을 사용했다. 의존성은 이 로컬 환경에만 설치했고 보드/시스템 Python을 변경하지 않았다. 테스트의 Starlette/httpx 사용 중단 예정 경고는 기록했으나 실패는 없었다. `git diff --cached --check`와 `git diff --check`도 통과했다.

[구조화 증거](display-374-stress-local-integration-20261108.json)에 테스트별 결과·JUnit 해시·변경 파일 해시를 기록했다. 원시 로그/XML/비교 실험 도구는 `outputs/display-374-local-integration-20261108/`에 보관했다.

## 다음 실보드 수락

1. 이 통합안을 리뷰·병합하고 팀원의 현재 stress/Gateway/Worker 변경까지 보존한 배포 조합을 확정한다. 이번 worktree의 Coordinator를 보드의 더 넓은 stress 변경 위에 그대로 덮어쓰지 않는다.
2. 실제 파일 해시·의존 조합·실행 중 로드 상태를 확인한 뒤 필요한 재시작 범위를 먼저 알리고 시험한다. 이번 작업에서 재시작 허용을 새로 받았다고 해석하지 않는다.
3. 추가 진단 소비자 없이 두 실제 브라우저의 120초 연속 freshness를 재확인한다. 늦은 실패 순서 보호가 수락됐다는 것과 모든 부하의 3초 갱신 보장은 다르다.
4. 이전 stale 1건의 직접 원인이 확정된 것은 아니다. 실보드 만료가 남으면 374/253을 완료로 바꾸지 않는다.

이번 요청 범위에서는 커밋·푸시·MR 생성·Jira 상태 변경을 수행하지 않았다.
