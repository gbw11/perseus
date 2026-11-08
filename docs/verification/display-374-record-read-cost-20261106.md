# 374 — 실행 기록 경로 검사 중복 제거

2026-11-06. 실보드의 읽기 전용 진단 후 로컬에서 변경했다. 보드 적용·서비스 재시작은 하지 않았다.
기준은 platform ae6ac60의 런타임 코드이며 작업 브랜치 HEAD 86dae36은 그 위의 기록 문서다.

## 변경

`xenova_execution.read_record`는 원래 `catalog.plain(parent, directory=True)` 뒤에
`catalog.plain(file)`을 호출했다. 후자의 전체 절대 경로 resolve 검사 자체가 모든 부모 경로의
심볼릭 링크·비정규 경로를 거부하고, 최종 파일이 단일 링크 일반 파일인지 검사한다.
성공한 일반 파일 경로의 부모는 디렉터리여야 하므로 앞의 별도 부모 경로 순회를 제거했다.

전체 파일 경로 검사는 매번 유지한다. 경로·inode·JSON 캐시, 검증 유효기간, TTL 연장,
원본 관측 시각 변경, 신원·실행·의도·admission·shutdown barrier 검증 생략은 없다.
JSON 내용은 계속 파일에서 읽어 파싱한다. 부모나 파일의 원자 교체 후에도 새로 검사한다.
기존 pathname 검사 자체를 완전한 TOCTOU 방어로 주장하지 않는다. descriptor 기반 경로
보안 모델을 새로 구현한 변경도 아니다.

변경은 공통 실행 기록 reader에 있으므로 조회뿐 아니라 이 reader를 사용하는 제어·복구에도
영향 범위를 고려했다. Manager의 VM별 최종 검증 순서와 제어 동작은 바꾸지 않았다.

## 비용 비교

동일한 두 VM 로컬 fixture에서 원본·후보·후보·원본 순서를 세 번 반복했다.
기준 Git 소스에서 read_record 함수만 AST로 읽어 원본으로 사용했고, 나머지 코드는 동일하다.
파일·메타데이터·잠금은 실제 실행하며 Xen은 기존 시험 대역이다.

| 항목 | 원본 | 후보 |
|---|---:|---:|
| 표본 | 6 | 6 |
| 조회당 canonical 경로 검사 호출 | 48 | 34 |
| 목록 조회 중앙값 | 4.641ms | 3.885ms |
| 정상 반환 VM 수 | 2 | 2 |

12회 모두 성공했다. 이 작은 로컬 표본의 시간 차이는 약 16.3%지만, 실제 Xen·보드 부하·
동시 브라우저 처리 비용을 포함하지 않으므로 실보드 개선율이나 3초 freshness 수락으로 해석하지 않는다.
반복 경로 검사 14회 감소는 이 fixture에서 직접 계수했다. 큰 변경 없이 불필요한 순회를 줄인
첫 최적화이며, 앞선 모든 운영 지연을 해결했다고 주장하지 않는다.

벤치마크는 `platform/tests/benchmark_execution_record_reads.py`이며 실행 시 비교 commit의
원본 `xenova_execution.py`를 `--baseline`, 식별자를 `--baseline-commit`으로 지정한다.
기존 로컬 PYTHONPATH(테스트·first-install·platform files·의존성)를 설정한 Linux 환경에서 실행한다.
[표본 JSON](display-374-record-read-cost-20261106.json)에 순서·모든 표본을 기록했다.

## 검증과 완료 범위

- 새 경로 경계 테스트: 파일/부모/상위 symlink, 첫 성공 후 부모 교체, hard link, 비정규/상대 경로,
  원자 JSON 교체, 손상·잘못된 schema·디렉터리, 부재와 dangling symlink 구분.
- 조회·등록·실행 경계 회귀: 140 passed, 24 subtests passed, 1 deselected.
  기존에 기준 코드에서도 재현된 `test_S4_T08_owned_helper_timeout_after_pipe_eof_reaps_child`는
  프로세스 PID 파일 생성 전 100ms 제한이 만료될 수 있어 이번 선택에서 제외했다. 제한을 늘리지 않았다.

- 추가 복구·host 설정·재부팅 관측·수명 소유권 회귀: 45 passed, 11 subtests passed,
  root 전용 실제 private 파일 검증 4개는 비-root WSL이라 skipped.

**로컬 후보 구현·검증과 실보드 수락을 구분한다.** 기존 [읽기 전용 진단](display-374-readonly-timing-20261106.md)은
이 변경 이전 정상 서비스의 결과다. 수정 후보를 보드에 배포·비교하지 않았고 374는 계속 진행 중이다.
다음 단계는 보드 사용 시간을 확보해 현재 파일 해시·팀원 변경을 확인한 뒤 후보 적용 범위를 조율하고,
정상 VM 목록 및 실제 두 브라우저 120초 연속 freshness를 재측정하는 것이다.
